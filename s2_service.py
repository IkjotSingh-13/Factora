"""S2 service: the single entry point the orchestrator calls (Mansimran).

The lead's `verify-orchestrator` branch needs one function, not five. This module
wraps retrieval + the contract adapter + the S3 merge, so the API layer never has
to know about sources, ranking or pydantic shapes.

    from s2_service import retrieve_for_claims, apply_analysis, build_score_request

    # 1. claims in (lead's ClaimInput, Ikjot's Claim, or a dict) -> bundles out
    bundles = retrieve_for_claims(claims)

    # 2. S3 fills stance / quote / quote_verified / rationale
    bundles = apply_analysis(bundles, analyses_from_s3)

    # 3. feed the lead's scorer directly
    from app.pipeline.s4_scoring import score_claim
    from app.models import ClaimInput, EvidenceItem

    reports = [
        score_claim(ClaimInput(**b["claim"]),
                    [EvidenceItem(**e) for e in b["evidence"]])
        for b in bundles
    ]

**Important seam fact** (found 2026-10-31): `score_claim` only counts evidence
where ``quote_verified is True`` and ``quote`` is non-empty. Retrieval deliberately
leaves those untouched, so S2 output alone scores ``INSUFFICIENT_EVIDENCE`` — which
is the honest result. ``apply_analysis()`` is how S3's verdicts reach the scorer,
and it drops any analysis whose quote does not appear in the evidence text.
"""

from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Optional, Sequence

from contract_adapter import (
    SCOPE_FULL_TEXT,
    SCOPE_SNIPPET,
    extract_entities,
    quote_check_region,
    quote_scope,
    to_claim_bundle,
    to_claim_input,
    to_score_request,
)
from s2_retrieval import RetrievalConfig, RetrievalEngine

log = logging.getLogger("verifyai.s2service")

VALID_STANCES = {"SUPPORTS", "REFUTES", "NEUTRAL"}
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="s2-retrieval")

_WS_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# Retrieval entry points
# ---------------------------------------------------------------------------
def default_engine(**overrides: Any) -> RetrievalEngine:
    """Engine with the module's production defaults; overrides are config kwargs."""
    config = RetrievalConfig(**overrides) if overrides else RetrievalConfig()
    return RetrievalEngine(config=config)


def retrieve_for_claims(
    claims: Sequence[Any],
    *,
    engine: Optional[RetrievalEngine] = None,
    top_k: Optional[int] = None,
    include_extras: bool = False,
    fail_soft: bool = True,
    parallel_claims: int = 4,
) -> list[dict[str, Any]]:
    """Claims -> ``ClaimEvidenceBundle`` dicts, in the same order as the input.

    Accepts any claim shape (Ikjot's ``Claim``, the lead's ``ClaimInput``, a dict).
    With ``fail_soft=True`` a claim that raises still gets a bundle with empty
    evidence, so one bad claim cannot 500 the whole request.
    """
    engine = engine or default_engine()
    if top_k is not None:
        engine.config.top_k = top_k

    # Claims are independent, so run them concurrently: measured 4.5 s per claim
    # sequentially, which is 27 s for a 6-claim article - too slow for the demo.
    # Order of the returned list is preserved.
    if parallel_claims > 1 and len(claims) > 1:
        with ThreadPoolExecutor(
            max_workers=min(parallel_claims, len(claims)), thread_name_prefix="s2-claim"
        ) as pool:
            return list(
                pool.map(
                    lambda c: _retrieve_one(
                        c,
                        engine=engine,
                        include_extras=include_extras,
                        fail_soft=fail_soft,
                        index=None,
                    ),
                    claims,
                )
            )

    bundles: list[dict[str, Any]] = []
    for claim in claims:
        try:
            claim_payload = to_claim_input(claim)
            result = engine.search_claim(
                claim_payload["text"],
                claim_id=claim_payload["id"],
                entities=extract_entities(claim),
            )
            bundles.append(
                to_claim_bundle(
                    claim,
                    evidence=result.evidence,
                    audit=result.audit,
                    include_extras=include_extras,
                )
            )
        except Exception as exc:  # fail soft, but never silently
            log.warning("retrieval failed for a claim: %s", exc)
            if not fail_soft:
                raise
            fallback_id = str(_safe_get(claim, "id", "claim_id") or f"claim_{len(bundles) + 1}")
            fallback_text = str(_safe_get(claim, "text", "claim_text") or "")
            bundles.append(
                {
                    "claim": to_claim_input(claim_id=fallback_id, claim_text=fallback_text or "unavailable"),
                    "evidence": [],
                    "extras": {"entities": extract_entities(claim), "retrieval_error": str(exc)[:200]},
                }
            )
    return bundles


async def aretrieve_for_claims(
    claims: Sequence[Any],
    *,
    engine: Optional[RetrievalEngine] = None,
    top_k: Optional[int] = None,
    include_extras: bool = False,
) -> list[dict[str, Any]]:
    """Async wrapper for FastAPI: retrieval is blocking HTTP, so run it off-loop.

    Keeps ``/api/verify`` from blocking the event loop for ~1-2 s per claim.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: retrieve_for_claims(
            claims, engine=engine, top_k=top_k, include_extras=include_extras
        ),
    )


def _retrieve_one(
    claim: Any,
    *,
    engine: RetrievalEngine,
    include_extras: bool,
    fail_soft: bool,
    index: Optional[int],
) -> dict[str, Any]:
    """Retrieve for a single claim; never raises when fail_soft is on."""
    try:
        claim_payload = to_claim_input(claim)
        result = engine.search_claim(
            claim_payload["text"],
            claim_id=claim_payload["id"],
            entities=extract_entities(claim),
        )
        return to_claim_bundle(
            claim,
            evidence=result.evidence,
            audit=result.audit,
            include_extras=include_extras,
        )
    except Exception as exc:
        log.warning("retrieval failed for a claim: %s", exc)
        if not fail_soft:
            raise
        fallback_id = str(
            _safe_get(claim, "id", "claim_id") or f"claim_{index if index is not None else 1}"
        )
        fallback_text = str(_safe_get(claim, "text", "claim_text") or "")
        return {
            "claim": to_claim_input(
                claim_id=fallback_id, claim_text=fallback_text or "unavailable"
            ),
            "evidence": [],
            "extras": {"entities": extract_entities(claim), "retrieval_error": str(exc)[:200]},
        }


def build_score_request(
    claims: Sequence[Any],
    *,
    engine: Optional[RetrievalEngine] = None,
    top_k: Optional[int] = None,
    analyses: Optional[Sequence[Any]] = None,
    input_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One call from claims to a payload the lead's ``ScoreRequest`` accepts."""
    bundles = retrieve_for_claims(claims, engine=engine, top_k=top_k)
    if analyses:
        bundles = apply_analysis(bundles, analyses)
    return to_score_request(bundles, input_meta=input_meta)


# ---------------------------------------------------------------------------
# S3 merge (Ikjot's stance output -> evidence items)
# ---------------------------------------------------------------------------
def normalize_quote(text: str) -> str:
    return _WS_RE.sub(" ", (text or "").lower()).strip()


def quote_present(quote: str, source_text: str) -> bool:
    """Exact substring check on normalised text - the same guard S3 must apply."""
    q = normalize_quote(quote)
    if not q:
        return False
    return q in normalize_quote(source_text)


def apply_analysis(
    bundles: Sequence[dict[str, Any]],
    analyses: Iterable[Any],
    *,
    require_quote_in_text: bool = True,
) -> list[dict[str, Any]]:
    """Merge S3 analyses into evidence items, in place-safe fashion.

    Each analysis must identify its evidence by ``evidence_id`` **or** ``url``
    and may carry ``stance``, ``quote``, ``quote_verified``, ``rationale``,
    ``context_score``, ``context_gap``.

    Safety rules:
      * unknown stance strings fall back to NEUTRAL (never guessed)
      * ``quote_verified`` is only accepted as True when the quote genuinely
        appears in the evidence text (``require_quote_in_text``)
      * analyses that match no evidence are ignored, not invented into new items
    """
    by_id: dict[str, dict[str, Any]] = {}
    by_url: dict[str, dict[str, Any]] = {}
    for bundle in bundles:
        for item in bundle.get("evidence", []):
            by_id[item["id"]] = item
            by_url[str(item.get("url", "")).rstrip("/")] = item

    applied = 0
    for analysis in analyses:
        target = None
        evidence_id = _safe_get(analysis, "evidence_id", "id")
        url = _safe_get(analysis, "url")
        if evidence_id and str(evidence_id) in by_id:
            target = by_id[str(evidence_id)]
        elif url:
            target = by_url.get(str(url).rstrip("/"))
        if target is None:
            log.info("analysis matched no evidence (id=%s url=%s)", evidence_id, url)
            continue

        stance = str(_safe_get(analysis, "stance", default="NEUTRAL") or "NEUTRAL").upper()
        if stance not in VALID_STANCES:
            log.info("unknown stance %r -> NEUTRAL", stance)
            stance = "NEUTRAL"

        quote = str(_safe_get(analysis, "quote", default="") or "")
        claimed_verified = bool(_safe_get(analysis, "quote_verified", default=False))
        # Check against page text when we have it; fall back to the snippet but
        # never pretend that is a source-level verification.
        page_text = str(target.get("full_text") or "").strip()
        snippet_text = str(target.get("snippet") or "")
        region = page_text if page_text else snippet_text
        verified = claimed_verified and (
            quote_present(quote, region) if require_quote_in_text else bool(quote)
        )

        target["stance"] = stance
        target["quote"] = quote if verified else ""
        target["quote_verified"] = verified
        # Record *what* was checked, so the report can never overstate a snippet match
        # as a match against the source. Mirrors the region chosen just above.
        target["quote_scope"] = ("full_text" if page_text else "snippet") if verified else ""
        target["rationale"] = str(_safe_get(analysis, "rationale", default="") or "")

        context_score = _safe_get(analysis, "context_score")
        if isinstance(context_score, (int, float)):
            target["context_score"] = max(0.0, min(1.0, float(context_score)))
        context_gap = _safe_get(analysis, "context_gap")
        if isinstance(context_gap, bool):
            target["context_gap"] = context_gap
        applied += 1

    if applied:
        log.debug("applied %d analyses", applied)
    return list(bundles)


def quote_support(
    item: dict[str, Any], quote: str, *, scope: Optional[str] = None
) -> tuple[bool, str]:
    """Whether ``quote`` literally appears in what we actually retrieved.

    Returns ``(found, scope_used)``. This is a *text check*, not a stance: S3 owns
    the verification policy, this just stops it from checking a quote against text
    we never had. ``scope_used`` is ``"snippet"`` when no page text was available,
    which means a match proves only "present in the search snippet".
    """
    resolved = scope or (
        SCOPE_FULL_TEXT if str(item.get("full_text") or "").strip() else SCOPE_SNIPPET
    )
    region = quote_check_region(item, resolved)
    return quote_present(quote, region), resolved


def verification_scopes(bundles: Sequence[dict[str, Any]]) -> dict[str, int]:
    """Count how many items S3 can verify against full text vs snippet only."""
    counts = {SCOPE_FULL_TEXT: 0, SCOPE_SNIPPET: 0}
    for bundle in bundles:
        for item in bundle.get("evidence", []):
            scope = SCOPE_FULL_TEXT if str(item.get("full_text") or "").strip() else SCOPE_SNIPPET
            counts[scope] += 1
    return counts


def retrieval_summary(bundles: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Small audit block for the report footer, computed from bundles alone."""
    claims = len(bundles)
    evidence = sum(len(b.get("evidence", [])) for b in bundles)
    domains = {
        item.get("domain")
        for b in bundles
        for item in b.get("evidence", [])
        if item.get("domain")
    }
    verified = sum(
        1 for b in bundles for item in b.get("evidence", []) if item.get("quote_verified")
    )
    scopes = verification_scopes(bundles)
    return {
        "claims": claims,
        "evidence_items": evidence,
        "distinct_domains": len(domains),
        "quote_verified_items": verified,
        "claims_without_evidence": sum(1 for b in bundles if not b.get("evidence")),
        "full_text_items": scopes[SCOPE_FULL_TEXT],
        "snippet_only_items": scopes[SCOPE_SNIPPET],
    }


def _safe_get(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        for name in names:
            if name in obj and obj[name] is not None:
                return obj[name]
        return default
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return default
