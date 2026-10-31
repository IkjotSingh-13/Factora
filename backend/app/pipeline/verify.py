"""Full pipeline: article → claims → evidence → stance → score → report.

This is the file that turns four separately-working stages into one product. Before
it existed the repo could extract (``/api/extract``), retrieve (``/api/retrieve``) and
score hand-written JSON (``/api/score``), but nothing joined them, so no caller could
get a report from pasted text.

    POST /api/verify  {"article_text": "...", "mode": "offline"}
      -> S1 extract   (backend.s1_claims)
      -> grounding    (article_quotes: verbatim quote + time_sensitive)
      -> S2 retrieve  (s2_service.aretrieve_for_claims)
      -> S3 analyze   (s3_analysis: stance + exact-quote guard)
      -> merge        (s2_service.apply_analysis: re-checks every quote)
      -> S4/S5 score  (s4_scoring + s5_report)

Failure policy (plan §5.4): every external call fails soft. A dead search source, a
refused fetch or an unavailable LLM degrades the report and is *named* in ``warnings``;
it never fabricates a result. If a quote cannot be verified it is dropped and counted.

Modes
-----
``live``     real source adapters, page text fetched for the top results.
``offline``  local mock corpus only — no network, no keys.
``snapshot`` replay ``data/demo_snapshot.json``. Labelled as a demo backup everywhere.
``auto``     ``VERIFY_*`` env decides; defaults to live.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field, model_validator

from app.models import (
    ClaimEvidenceBundle,
    ClaimInput,
    InputMetadata,
    ScoreRequest,
    VerificationReport,
)
from app.pipeline.article_quotes import (
    ground_claims,
    to_display_claims,
    to_pipeline_claims,
)
from app.pipeline.s5_report import build_report
from contract_adapter import to_score_request
from s2_retrieval import RetrievalConfig, RetrievalEngine
from s2_service import apply_analysis, aretrieve_for_claims, retrieval_summary
from s3_analysis import S3Config, analyze_bundles

log = logging.getLogger("verifyai.verify")

Mode = Literal["auto", "live", "offline", "snapshot"]
DEFAULT_SNAPSHOT = "data/demo_snapshot.json"
_SNAPSHOT_ON = ("1", "true", "yes", "on")


class VerifyRequest(BaseModel):
    """Run the whole pipeline over one article. Exactly one source is required."""

    article_text: str | None = Field(default=None, min_length=1, max_length=200_000)
    url: str | None = Field(default=None, min_length=1, max_length=2_048)
    mode: Mode = "auto"
    input: InputMetadata = Field(default_factory=InputMetadata)
    as_of: date | None = None
    #: keep snippet text per source in the response (bigger payload, useful in QA)
    include_evidence_text: bool = True
    max_claims: int | None = Field(default=None, ge=1, le=6)

    @model_validator(mode="after")
    def _one_source(self) -> "VerifyRequest":
        has_text = self.article_text is not None and bool(self.article_text.strip())
        has_url = self.url is not None and bool(self.url.strip())
        if has_text == has_url:
            raise ValueError("provide exactly one of article_text or url")
        if has_text:
            self.article_text = self.article_text.strip()
        if has_url:
            self.url = self.url.strip()
        return self


class StageResult(BaseModel):
    """Timing + outcome of one pipeline stage, for the audit footer."""

    name: str
    status: Literal["ok", "degraded", "failed", "skipped"] = "ok"
    ms: int = 0
    detail: str = ""


class VerifyResponse(BaseModel):
    report: VerificationReport
    mode: Mode
    source_type: Literal["article_text", "url"]
    stages: list[StageResult] = Field(default_factory=list)
    s1: dict[str, Any] = Field(default_factory=dict)
    grounding: dict[str, Any] = Field(default_factory=dict)
    s3_audit: dict[str, Any] = Field(default_factory=dict)
    retrieval_summary: dict[str, Any] = Field(default_factory=dict)
    per_claim_retrieval: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    demo_backup: bool = False
    note: str = ""


class ExtractionFailure(Exception):
    """S1 could not run at all. The route maps this to a 4xx, never a 500."""

    def __init__(self, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


class VerifyInputError(Exception):
    """The request itself was unusable (empty article, blocked URL). Maps to 400."""


# ---------------------------------------------------------------------------
# Stage helpers
# ---------------------------------------------------------------------------
#: (RetrievalConfig field, environment variable, lowest sane value, highest)
_S2_INT_KNOBS: tuple[tuple[str, str, int, int], ...] = (
    ("top_k", "S2_TOP_K", 1, 20),
    ("per_source_limit", "S2_PER_SOURCE_LIMIT", 1, 20),
    ("max_per_domain", "S2_MAX_PER_DOMAIN", 1, 20),
    ("cache_ttl_s", "S2_CACHE_TTL_S", 0, 86400),
)


def _s2_overrides() -> tuple[dict[str, Any], list[str]]:
    """Retrieval knobs read from the environment, plus a note for anything ignored.

    ``.env.example`` advertises these, so they have to do something: a setting that is
    silently inert is worse than no setting, because the reader believes they changed
    the run. A value that does not parse or is out of range is dropped and *named* in
    the response warnings — a typo in a config file must not take the pipeline down,
    and must not be invisible either.
    """
    overrides: dict[str, Any] = {}
    notes: list[str] = []
    for field, var, low, high in _S2_INT_KNOBS:
        raw = os.getenv(var, "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            notes.append(f"S2: ignoring {var}={raw!r}, which is not a whole number.")
            continue
        if not low <= value <= high:
            notes.append(f"S2: ignoring {var}={value}; the usable range is {low}-{high}.")
            continue
        overrides[field] = value
    cache_dir = os.getenv("S2_DISK_CACHE_DIR", "").strip()
    if cache_dir:
        overrides["disk_cache_dir"] = cache_dir
    return overrides, notes


def _engine_for(mode: Mode, notes: list[str] | None = None) -> RetrievalEngine:
    """The retrieval engine one run should use, given its mode."""
    overrides, found = _s2_overrides()
    if notes is not None:
        notes.extend(found)
    fetch_raw = os.getenv("S2_FETCH_FULL_TEXT", "3").strip()
    try:
        fetch_full_text = max(0, int(fetch_raw))
    except ValueError:
        fetch_full_text = 3
        if notes is not None:
            notes.append(
                f"S2: ignoring S2_FETCH_FULL_TEXT={fetch_raw!r}, which is not a whole "
                "number; 3 pages per claim were fetched instead."
            )
    if mode == "offline":
        return RetrievalEngine(
            RetrievalConfig(
                offline=True, mock=True, fetch_full_text=0, **overrides
            )
        )
    if mode == "snapshot":
        path = os.getenv("VERIFY_SNAPSHOT_PATH", DEFAULT_SNAPSHOT)
        # a replay reads one captured file: no live request to cache and none to make
        overrides.pop("disk_cache_dir", None)
        return RetrievalEngine(
            RetrievalConfig(
                replay_snapshot=path, fetch_full_text=0, use_cache=False, **overrides
            )
        )
    return RetrievalEngine(
        RetrievalConfig(fetch_full_text=fetch_full_text, **overrides)
    )


def _resolve_mode(requested: Mode) -> Mode:
    """Honour an explicit mode; ``auto`` reads the ``VERIFY_*`` environment.

    A pinned ``VERIFY_MODE`` exists so a demo can be forced offline by environment
    alone, without anyone touching the UI. It deliberately does *not* accept
    ``auto``: that value means "decide per request", and treating it as a pin would
    silently override an explicit ``offline`` or ``snapshot`` request — which is
    exactly what a copied ``.env.example`` used to do.
    """
    forced = os.getenv("VERIFY_MODE", "").strip().lower()
    if forced in ("live", "offline", "snapshot"):
        return forced  # type: ignore[return-value]
    if requested != "auto":
        return requested
    if os.getenv("VERIFY_OFFLINE", "").strip().lower() in _SNAPSHOT_ON:
        return "offline"
    if os.getenv("VERIFY_SNAPSHOT_ONLY", "").strip().lower() in _SNAPSHOT_ON:
        return "snapshot"
    return "live"


def _extract(request: VerifyRequest) -> tuple[list[Any], str, dict[str, Any], list[str]]:
    """Run S1. Returns ``(claims, article_text, provenance, warnings)``.

    ``article_text`` is echoed back so the next stage can pin claims to verbatim
    sentences from exactly what the user submitted.
    """
    from backend import (  # noqa: PLC0415 - keeps S1's import cost off module load
        ArticleExtractionError,
        ClaimExtractionError,
        EmptyArticleError,
        InvalidURLError,
        LLMError,
        extract_article_from_url,
        extract_claims_detailed,
    )
    from backend.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    warnings: list[str] = []

    try:
        if request.url:
            article_text = extract_article_from_url(request.url, settings=settings)
            source_type = "url"
        else:
            article_text = request.article_text or ""
            source_type = "article_text"
        result = extract_claims_detailed(article_text, settings=settings)
    except (EmptyArticleError, InvalidURLError) as exc:
        raise VerifyInputError(str(exc)) from exc
    except (ArticleExtractionError, LLMError) as exc:
        raise ExtractionFailure(str(exc), status_code=502) from exc
    except ClaimExtractionError as exc:
        raise ExtractionFailure(str(exc), status_code=422) from exc

    claims = list(result.claims)
    if request.max_claims:
        claims = claims[: request.max_claims]

    provenance = {
        "provider": result.provider,
        "configured_provider": settings.llm_provider,
        "used_fallback": result.used_fallback,
        "claims_returned": len(claims),
        "claims_from_provider": result.raw_claim_count,
        "rejected": len(result.rejected),
        "deduplicated": result.deduplicated,
        "article_sentences": result.article_sentence_count,
        "notes": [str(note) for note in (result.notes or [])][:6],
    }
    if result.used_fallback:
        warnings.append(
            "S1: the configured LLM provider did not return usable claims, so the "
            "deterministic heuristic extractor was used instead."
        )
    return claims, article_text, provenance, warnings


def _ground(
    claims: Sequence[Any], article_text: str
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Pin claims to verbatim article sentences; drop what the article does not say."""
    if not article_text:
        # No article to check against: keep the claims but say plainly that they are
        # unquoted, instead of inventing a "quote" that equals the claim text.
        return (
            [
                {
                    "id": getattr(claim, "id", f"claim_{index:03d}"),
                    "text": getattr(claim, "text", ""),
                    "claim_type": getattr(claim, "claim_type", "other"),
                    "entities": list(getattr(claim, "entities", []) or []),
                    "article_quote": None,
                    "time_sensitive": False,
                }
                for index, claim in enumerate(claims, 1)
            ],
            {
                "stage": "S1_GROUNDING",
                "skipped": True,
                "claims_in": len(list(claims)),
                "claims": [
                    {
                        "id": getattr(claim, "id", f"claim_{index:03d}"),
                        "original_text": getattr(claim, "text", ""),
                        "assertion": getattr(claim, "text", ""),
                        "reporting_frame_removed": None,
                        "article_quote": None,
                        "quote_source": "none",
                        "align": 0.0,
                        "type": getattr(claim, "claim_type", "other"),
                        "entities": list(getattr(claim, "entities", []) or []),
                        "time_sensitive": False,
                        "time_sensitive_reason": None,
                    }
                    for index, claim in enumerate(claims, 1)
                ],
            },
            [
                "S1: no article text was retained, so claims carry no verbatim article "
                "quote and cannot be checked against the source."
            ],
        )

    grounded, audit = ground_claims(claims, article_text)
    audit = {**audit, "claims": to_display_claims(grounded)}
    warnings = [f"S1: {note}" for note in audit.get("notes", []) if note]
    dropped = int(audit.get("claims_dropped_ungrounded") or 0)
    if dropped:
        warnings.append(
            f"S1: {dropped} claim(s) were dropped because no verbatim article quote "
            "could be located for them."
        )
    return to_pipeline_claims(grounded), audit, warnings


_RETRIEVAL_KEYS = (
    "sources_queried",
    "sources_ok",
    "sources_failed",
    "candidates_total",
    "dropped_low_coverage",
    "dropped_duplicates",
    "dropped_over_domain_cap",
    "returned",
    "distinct_domains",
    "cache_hits",
    "duration_ms",
    "full_text_requested",
    "full_text_fetched",
    "full_text_failed",
    "mean_coverage",
)


def _claim_details(bundles: Sequence[dict[str, Any]], *, include_text: bool) -> list[dict[str, Any]]:
    """Per-claim source + retrieval audit for the UI's evidence cards and footer."""
    out: list[dict[str, Any]] = []
    for bundle in bundles:
        claim = bundle.get("claim") or {}
        evidence = list(bundle.get("evidence") or [])
        sources: list[dict[str, Any]] = []
        for item in evidence:
            source = {
                "id": item.get("id"),
                "url": item.get("url"),
                "domain": item.get("domain"),
                "title": item.get("title"),
                "source_kind": item.get("source_kind"),
                "published_at": item.get("published_at"),
                "relevance": item.get("relevance"),
                "credibility": item.get("credibility"),
                "stance": item.get("stance"),
                "quote": item.get("quote") or "",
                "quote_verified": bool(item.get("quote_verified")),
                "rationale": item.get("rationale") or "",
                "text_available": bool(str(item.get("full_text") or "").strip()),
            }
            if include_text:
                source["snippet"] = str(item.get("snippet") or "")[:600]
            sources.append(source)

        entry: dict[str, Any] = {
            "claim_id": claim.get("id"),
            "claim_text": claim.get("text"),
            "evidence_count": len(evidence),
            "domains": sorted({str(s["domain"]) for s in sources if s.get("domain")}),
            "sources": sources,
        }
        audit = (bundle.get("extras") or {}).get("retrieval_audit") or {}
        if audit:
            retrieval = {key: audit.get(key) for key in _RETRIEVAL_KEYS if key in audit}
            retrieval["failed_source_names"] = [
                source.get("name")
                for source in (audit.get("sources") or [])
                if source.get("status") in ("error", "timeout")
            ]
            retrieval["sources"] = [
                {
                    "name": source.get("name"),
                    "status": source.get("status"),
                    "results": source.get("results"),
                    "cached": source.get("cached"),
                    "latency_ms": source.get("latency_ms"),
                    "error": source.get("error"),
                }
                for source in (audit.get("sources") or [])
            ]
            retrieval["notes"] = [str(n) for n in (audit.get("notes") or [])][:4]
            retrieval["queries"] = [str(q) for q in (audit.get("queries") or [])][:6]
            entry["retrieval"] = retrieval
        if (bundle.get("extras") or {}).get("retrieval_error"):
            entry["retrieval_error"] = str(bundle["extras"]["retrieval_error"])[:200]
        out.append(entry)
    return out


def _empty_report(request: VerifyRequest, claim_text: str) -> VerificationReport:
    """A report with one claim and no evidence — an honest INSUFFICIENT_EVIDENCE."""
    return build_report(
        ScoreRequest(
            input=request.input,
            claims=[
                ClaimEvidenceBundle(
                    claim=ClaimInput(
                        id="claim_001",
                        text=(claim_text[:160] + "…") if len(claim_text) > 160 else claim_text,
                        type="other",
                    ),
                    evidence=[],
                )
            ],
            as_of=request.as_of or date.today(),
        )
    )


def merge_and_score(
    bundles: Sequence[dict[str, Any]],
    *,
    input_meta: dict[str, Any] | None = None,
    as_of: date | None = None,
) -> VerificationReport:
    """The single join implementation: merged bundles → validated request → report.

    Both ``/api/verify`` and ``orchestrator.run_verified_claim_pipeline`` call this, so
    the two entry points cannot drift apart on the question of what reaches a score.
    Validation against ``ScoreRequest`` is the last gate: a payload that does not
    satisfy the frozen contract fails here rather than producing a quietly wrong report.
    """
    payload = to_score_request(
        list(bundles), input_meta=input_meta, as_of=as_of or date.today()
    )
    return build_report(ScoreRequest.model_validate(payload))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
async def run_verify(request: VerifyRequest) -> VerifyResponse:
    """Run every stage. Raises only for bad input; a dead service degrades output."""
    stages: list[StageResult] = []
    warnings: list[str] = []
    mode = _resolve_mode(request.mode)
    source_type: Literal["article_text", "url"] = "url" if request.url else "article_text"
    started = time.perf_counter()

    # ---- S1: extract --------------------------------------------------------
    claims, article_text, s1_summary, extraction_warnings = _extract(request)
    warnings.extend(extraction_warnings)
    stages.append(
        StageResult(
            name="S1 claim extraction",
            status="ok" if claims else "degraded",
            ms=int((time.perf_counter() - started) * 1000),
            detail=f"{len(claims)} checkable claims",
        )
    )
    # The plan scopes verification to factual claims: an opinion or a prediction has
    # no evidence that can support or refute it. Types come from S1, and if S1
    # labelled nothing factual the labels are clearly unreliable, so nothing is
    # filtered rather than silently verifying a different set from what was extracted.
    factual = [claim for claim in claims if getattr(claim, "claim_type", "factual") == "factual"]
    if claims and factual and len(factual) < len(claims):
        warnings.append(
            f"S1: {len(claims) - len(factual)} of {len(claims)} extracted claims were "
            "not factual (opinion, prediction or non-verifiable) and were not verified."
        )
        claims = factual

    if not claims:
        return VerifyResponse(
            report=_empty_report(request, article_text or request.url or "unavailable"),
            mode=mode,
            source_type=source_type,
            stages=stages,
            s1=s1_summary,
            warnings=warnings + ["No checkable claim could be extracted from this input."],
            note="Extraction produced nothing to verify, so no evidence was retrieved.",
        )

    # ---- S1: ground claims to verbatim article quotes -----------------------
    started = time.perf_counter()
    pipeline_claims, grounding_audit, grounding_warnings = _ground(claims, article_text)
    warnings.extend(grounding_warnings)
    stages.append(
        StageResult(
            name="S1 article-quote guard",
            status="ok" if grounding_audit.get("claims_kept") else "degraded",
            ms=int((time.perf_counter() - started) * 1000),
            detail=(
                f"{grounding_audit.get('claims_kept', 0)} of "
                f"{grounding_audit.get('claims_in', 0)} claims carry a verified quote"
            ),
        )
    )
    if not pipeline_claims:
        return VerifyResponse(
            report=_empty_report(request, article_text),
            mode=mode,
            source_type=source_type,
            stages=stages,
            s1=s1_summary,
            grounding=grounding_audit,
            warnings=warnings,
            note="Every claim failed the verbatim-quote guard, so nothing was verified.",
        )

    # ---- S2: retrieve -------------------------------------------------------
    started = time.perf_counter()
    retrieval_notes: list[str] = []
    try:
        bundles = await aretrieve_for_claims(
            pipeline_claims,
            engine=_engine_for(mode, retrieval_notes),
            include_extras=True,
        )
    except Exception as exc:  # the engine is fail-soft internally; this is the last net
        log.warning("retrieval stage failed: %s", exc)
        bundles = [
            {"claim": claim, "evidence": [], "extras": {"retrieval_error": str(exc)[:200]}}
            for claim in pipeline_claims
        ]
        warnings.append(
            f"S2: retrieval failed entirely ({type(exc).__name__}), so every claim is "
            "reported as insufficient evidence."
        )
    warnings.extend(retrieval_notes)
    evidence_total = sum(len(bundle.get("evidence") or []) for bundle in bundles)
    if mode == "snapshot":
        warnings.append(
            "S2: evidence came from the captured demo snapshot — a DEMO BACKUP, not a "
            "live retrieval."
        )
    if evidence_total == 0:
        warnings.append(
            "S2: no source returned usable evidence for any claim. "
            + (
                "That can mean the sources were unreachable from this network, not that "
                "the claims are wrong."
                if mode == "live"
                else "The local corpus holds no entry matching these claims, so this says "
                     "nothing about the claims themselves."
            )
        )
    kinds = {
        str(item.get("source_kind") or "web")
        for bundle in bundles
        for item in (bundle.get("evidence") or [])
    }
    if kinds and kinds <= {"mock"}:
        warnings.append(
            "S2: every source in this run came from the local demo corpus. Those "
            "entries are tagged source_kind=mock and score 0.10 credibility by design, "
            "so this run shows how the mechanism works, not what real evidence does."
        )
    stages.append(
        StageResult(
            name=f"S2 retrieval ({mode})",
            status="ok" if evidence_total else "degraded",
            ms=int((time.perf_counter() - started) * 1000),
            detail=f"{evidence_total} evidence items across "
            f"{len({item.get('domain') for b in bundles for item in (b.get('evidence') or []) if item.get('domain')})} domains",
        )
    )

    # ---- S3: stance + quote verification ------------------------------------
    started = time.perf_counter()
    s3_mode = os.getenv("S3_MODE", "lexicon" if mode in ("offline", "snapshot") else "auto")
    analyses: list[dict[str, Any]] = []
    try:
        result = analyze_bundles(
            pipeline_claims, bundles, config=S3Config.resolve(mode=s3_mode)
        )
        analyses = result.analyses
        s3_audit = dict(result.audit)
        s3_status: Literal["ok", "degraded", "failed"] = "ok" if analyses else "degraded"
    except Exception as exc:
        log.warning("S3 analysis failed: %s", exc)
        s3_audit = {"stage": "S3_ANALYSIS", "error": str(exc)[:200], "evidence_analyzed": 0}
        s3_status = "failed"
        warnings.append(
            f"S3: stance analysis failed ({type(exc).__name__}), so no quote was "
            "accepted and no verdict is claimed."
        )
    # apply_analysis is the merge-side guard: it re-checks every quote against the
    # retrieved text, so an analysis that slipped through here cannot reach the score.
    bundles = apply_analysis(bundles, analyses)
    dropped_quotes = int(s3_audit.get("quote_dropped_count") or 0)
    if dropped_quotes:
        warnings.append(
            f"S3: {dropped_quotes} quote(s) could not be found in the retrieved text "
            "and were dropped rather than displayed."
        )
    if s3_audit.get("analyzer") == "lexical":
        warnings.append(
            "S3: stances came from the deterministic lexical analyser, not a trained "
            f"NLI model ({s3_audit.get('provider_note', 'no LLM configured')})."
        )
    stages.append(
        StageResult(
            name="S3 stance + quote guard",
            status=s3_status,
            ms=int((time.perf_counter() - started) * 1000),
            detail=(
                f"{s3_audit.get('decisive', 0)}/{s3_audit.get('evidence_analyzed', 0)} "
                f"items given a stance; {dropped_quotes} quote(s) dropped"
            ),
        )
    )

    # ---- S4/S5: score and report -------------------------------------------
    started = time.perf_counter()
    input_meta = request.input.model_dump(mode="json")
    if request.url and not input_meta.get("url"):
        input_meta["url"] = request.url
    report = merge_and_score(
        bundles, input_meta=input_meta, as_of=request.as_of or date.today()
    )
    verified_items = sum(
        1
        for bundle in bundles
        for item in (bundle.get("evidence") or [])
        if item.get("quote_verified")
    )
    stages.append(
        StageResult(
            name="S4/S5 score + report",
            status="ok",
            ms=int((time.perf_counter() - started) * 1000),
            detail=f"{len(report.claims)} claim report(s) from {verified_items} verified quotes",
        )
    )
    if verified_items == 0 and evidence_total:
        warnings.append(
            "S3: evidence was retrieved but no quote survived verification, so every "
            "claim is scored on zero verified evidence and reads INSUFFICIENT EVIDENCE."
        )
    snippet_only = int(retrieval_summary(bundles).get("snippet_only_items") or 0)
    if snippet_only:
        warnings.append(
            f"S2/S3: {snippet_only} item(s) had no page text, so their quotes are "
            "verified against the search snippet only — not against the source."
        )

    return VerifyResponse(
        report=report,
        mode=mode,
        source_type=source_type,
        stages=stages,
        s1=s1_summary,
        grounding=grounding_audit,
        s3_audit=s3_audit,
        retrieval_summary=retrieval_summary(bundles),
        per_claim_retrieval=_claim_details(bundles, include_text=request.include_evidence_text),
        warnings=warnings,
        demo_backup=mode == "snapshot",
        note=(
            "Evidence-weighted assessment. Every displayed quote was verified against "
            "retrieved text; verdicts are computed only from verified quotes."
        ),
    )
