"""Adapter: S2 output -> the lead's canonical contract (Mansimran).

The lead's live contract is ``backend/app/models.py`` (``EvidenceItem``, ``ClaimInput``,
``ClaimEvidenceBundle``, ``ScoreRequest``) — not the ``contracts.py`` sketch. Field
names differ in three places that would have failed silently at integration:

    S2 (contracts.Evidence)      lead (app.models.EvidenceItem)
    ---------------------------  ------------------------------------------
    (missing)                    id            REQUIRED, min_length=1
    text_or_snippet              snippet       (+ full_text)
    published_at: str "2025-10-01"   published_at: datetime.date
    (missing)                    credibility   used heavily by the C term
    (missing)                    context_score used by the K term

Use this module at the boundary and nothing else needs to change:

    from contract_adapter import to_claim_bundle, to_evidence_item

    rr = engine.search_claim(claim.text, claim_id=claim.id)
    bundle = to_claim_bundle(
        claim_id=claim.id, claim_text=claim.text,
        evidence=rr.evidence, audit=rr.audit,
    )
    # bundle == {"claim": {...ClaimInput...}, "evidence": [{...EvidenceItem...}]}
    # feed straight into ScoreRequest(claims=[bundle]) or Flask/FastAPI JSON

Design rules
------------
* **Never invent stance.** ``stance``/``quote``/``quote_verified``/``rationale`` are
  left at their neutral defaults for S3 (Ikjot) to fill. Retrieval does not judge.
* **Credibility comes from the source kind**, via ``sources.CREDIBILITY_HINTS``,
  so the lead's C term has a real input instead of its 0.35 placeholder.
* ``context_score`` is filled from the coverage proxy (how much of the claim's
  distinctive terms the evidence actually mentions) and is labelled as a proxy.
* ``id`` is deterministic (hash of claim_id + url), so the same evidence keeps the
  same id across runs — safe to use as a UI key or a dedupe key.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Any, Iterable, Optional, Sequence

from sources import CREDIBILITY_HINTS

#: neutral defaults for fields that belong to S3 (stance analysis) and S4 (scoring)
DEFAULT_STANCE = "NEUTRAL"
DEFAULT_CONTEXT_SCORE = 0.7
DEFAULT_FULL_TEXT = ""

#: What S3 may check a quote against, per evidence item.
SCOPE_FULL_TEXT = "full_text"     # page text available: a quote can be verified properly
SCOPE_SNIPPET = "snippet"         # only the search snippet: a match proves much less


def stable_evidence_id(claim_id: str, url: str) -> str:
    """Deterministic id: same claim + same url => same id, every run."""
    digest = hashlib.sha1(f"{claim_id}|{url}".encode("utf-8")).hexdigest()[:10]
    return f"ev_{digest}"


def to_date(value: Any) -> Optional[date]:
    """ISO string / datetime / date -> date, or None. Never fabricates a date."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    year = re.match(r"^(\d{4})$", text)
    if year:  # year-only sources: keep it, but the T term should treat this as coarse
        return date(int(year.group(1)), 1, 1)
    return None


def credibility_for(source_kind: str) -> float:
    """C-term input by source kind. Deliberately coarse and documented."""
    return float(CREDIBILITY_HINTS.get(source_kind, CREDIBILITY_HINTS["web"]))


def _coverage_for(url: str, audit: Any) -> Optional[float]:
    if audit is None:
        return None
    mapping = getattr(audit, "coverage_by_evidence", None) or {}
    for key, value in mapping.items():
        if str(key).rstrip("/") == str(url).rstrip("/"):
            return float(value)
    return None


def to_evidence_item(
    evidence: Any,
    *,
    claim_id: str,
    audit: Any = None,
    stance: str = DEFAULT_STANCE,
    credibility: Optional[float] = None,
) -> dict[str, Any]:
    """Map one ``contracts.Evidence`` to an ``app.models.EvidenceItem`` dict."""
    url = str(getattr(evidence, "url", "") or "")
    coverage = _coverage_for(url, audit)
    return {
        "id": stable_evidence_id(claim_id, url),
        "claim_id": claim_id,
        "url": url,
        "domain": str(getattr(evidence, "domain", "") or ""),
        "title": str(getattr(evidence, "title", "") or ""),
        # snippet = what the search API returned; full_text = fetched page text
        "snippet": str(getattr(evidence, "text_or_snippet", "") or ""),
        "full_text": str(getattr(evidence, "full_text", "") or ""),
        "published_at": to_date(getattr(evidence, "published_at", None)),
        "source_kind": str(getattr(evidence, "source_kind", "web") or "web"),
        "relevance": float(getattr(evidence, "relevance", 0.0)),
        "credibility": (
            float(credibility)
            if credibility is not None
            else credibility_for(str(getattr(evidence, "source_kind", "web")))
        ),
        # --- S3 fills these; retrieval must not guess ---
        "stance": stance,
        "quote": "",
        "quote_verified": False,
        "rationale": "",
        # --- K-term proxy from retrieval coverage; scoring may override ---
        "context_score": float(coverage) if coverage is not None else DEFAULT_CONTEXT_SCORE,
        "context_gap": bool(coverage is not None and coverage < 0.5),
    }


def quote_scope(evidence: Any) -> str:
    """Which region a quote may be verified against for this evidence item."""
    scope = str(getattr(evidence, "text_scope", "") or "")
    if scope == SCOPE_FULL_TEXT and str(getattr(evidence, "full_text", "") or "").strip():
        return SCOPE_FULL_TEXT
    return SCOPE_SNIPPET


def quote_check_region(item: dict[str, Any], scope: str) -> str:
    """The exact text a quote must appear in, given a scope."""
    if scope == SCOPE_FULL_TEXT:
        return str(item.get("full_text") or "")
    return str(item.get("snippet") or "")


def quote_guard_map(evidence: Iterable[Any]) -> dict[str, str]:
    """{evidence_id: scope} so S3 knows what it is allowed to claim."""
    guard: dict[str, str] = {}
    for ev in evidence:
        claim_id = str(getattr(ev, "claim_id", "") or "")
        url = str(getattr(ev, "url", "") or "")
        guard[stable_evidence_id(claim_id, url)] = quote_scope(ev)
    return guard


def to_evidence_items(
    evidence: Iterable[Any], *, claim_id: str, audit: Any = None
) -> list[dict[str, Any]]:
    return [to_evidence_item(ev, claim_id=claim_id, audit=audit) for ev in evidence]


# ---------------------------------------------------------------------------
# Claims: three shapes exist in this repo, normalise them all here
# ---------------------------------------------------------------------------
#   plan sketch (contracts.Claim):  id, text, type, article_quote, time_sensitive, entities
#   lead (app.models.ClaimInput):   id, text, type, article_quote, time_sensitive
#   Ikjot (s1_claims.Claim):        id, text, entities, claim_type
#
# Only ``claim_type`` differs in name, and ``entities`` has no home in the lead's
# model at all. Both are handled below so no stage has to change.

_CLAIM_TYPE_ALIASES = ("claim_type", "type", "kind", "claimType")


def _read(obj: Any, *names: str, default: Any = None) -> Any:
    """Read a field from a dict, pydantic model, dataclass or plain object."""
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


def extract_entities(claim_or_obj: Any) -> list[str]:
    """Pull ``entities`` off any claim shape; [] when absent. Never None."""
    raw = _read(claim_or_obj, "entities", "entity", "mentions", default=[]) or []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def claim_type_of(claim_or_obj: Any, default: str = "other") -> str:
    """``claim_type`` (Ikjot) or ``type`` (lead) - whichever this object uses."""
    value = _read(claim_or_obj, *_CLAIM_TYPE_ALIASES, default=default)
    return str(value) if value else default


def to_claim_input(
    claim: Any = None,
    *,
    claim_id: Optional[str] = None,
    claim_text: Optional[str] = None,
    claim_type: Optional[str] = None,
    article_quote: Optional[str] = None,
    time_sensitive: Optional[bool] = None,
) -> dict[str, Any]:
    """Emit the lead's ``ClaimInput`` from *any* claim shape.

    Accepts Ikjot's ``Claim(id, text, entities, claim_type)``, the plan sketch's
    ``Claim(id, text, type, article_quote, time_sensitive, entities)``, a dict, or
    explicit keyword arguments. Explicit kwargs win over values read from ``claim``.

    ``entities`` are intentionally **not** emitted: the lead's ClaimInput has no
    such field. Use :func:`extract_entities` to feed them into retrieval queries,
    which is where they actually help.
    """
    resolved_id = claim_id if claim_id is not None else _read(claim, "id", "claim_id")
    resolved_text = claim_text if claim_text is not None else _read(claim, "text", "claim_text")
    if not resolved_id:
        raise ValueError("claim id is required (pass claim with .id, or claim_id=...)")
    if not resolved_text:
        raise ValueError("claim text is required (pass claim with .text, or claim_text=...)")

    resolved_type = (
        claim_type
        if claim_type is not None
        else claim_type_of(claim, default="other")
    )
    if article_quote is None:
        article_quote = _read(claim, "article_quote", "quote", "source_quote")
    if time_sensitive is None:
        time_sensitive = _read(claim, "time_sensitive", "timeSensitive", default=False)

    return {
        "id": str(resolved_id),
        "text": str(resolved_text),
        "type": str(resolved_type),
        "article_quote": article_quote if article_quote else None,
        "time_sensitive": bool(time_sensitive),
    }


def to_claim_bundle(
    claim: Any = None,
    *,
    claim_id: Optional[str] = None,
    claim_text: Optional[str] = None,
    evidence: Sequence[Any],
    audit: Any = None,
    claim_type: Optional[str] = None,
    article_quote: Optional[str] = None,
    time_sensitive: Optional[bool] = None,
    include_extras: bool = False,
) -> dict[str, Any]:
    """The object ``ScoreRequest.claims[]`` expects: ``{claim, evidence[]}``.

    Works with Ikjot's Claim object directly::

        bundle = to_claim_bundle(claim, evidence=rr.evidence, audit=rr.audit)

    or with explicit values::

        bundle = to_claim_bundle(claim_id="c1", claim_text="...", evidence=[...])

    ``include_extras=True`` adds a top-level ``"extras": {"entities": [...]}`` key
    so entities survive to the report/UI even though ClaimInput cannot hold them.
    Off by default: keep the payload exactly what the lead's model accepts.
    """
    claim_payload = to_claim_input(
        claim,
        claim_id=claim_id,
        claim_text=claim_text,
        claim_type=claim_type,
        article_quote=article_quote,
        time_sensitive=time_sensitive,
    )
    bundle: dict[str, Any] = {
        "claim": claim_payload,
        "evidence": to_evidence_items(
            evidence, claim_id=claim_payload["id"], audit=audit
        ),
        # S3 reads this to decide what a verified quote is allowed to mean.
        # A plain additive key: the lead's ClaimEvidenceBundle ignores extras.
        "quote_guard": quote_guard_map(evidence),
    }
    if include_extras:
        bundle["extras"] = {"entities": extract_entities(claim)}
    return bundle


def to_score_request(
    bundles: Sequence[dict[str, Any]],
    *,
    as_of: Optional[date] = None,
    input_meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "input": input_meta or {"url": None, "title": None, "author": None, "published": None},
        "claims": list(bundles),
        "as_of": (as_of or date.today()).isoformat(),
    }


def validate_with_lead_models(payload: dict[str, Any]) -> tuple[bool, str]:
    """Optional hard check against ``backend/app/models.py`` if it is importable.

    Returns (ok, message). Never raises: validation being unavailable is not an error.
    """
    try:
        from app.models import ClaimEvidenceBundle  # type: ignore
    except Exception as exc:
        return True, f"lead models not importable ({type(exc).__name__}); skipped validation"
    try:
        ClaimEvidenceBundle(**payload)
        return True, "validated against app.models.ClaimEvidenceBundle"
    except Exception as exc:
        return False, f"rejected by app.models.ClaimEvidenceBundle: {exc}"
