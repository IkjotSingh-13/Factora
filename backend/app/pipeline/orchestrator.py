"""Orchestration helpers that join S2 retrieval, S3 analysis, and S4/S5 reporting.

S3 is supplied as a callback so this module can be merged before Ikjot's final
provider/function signature is ready. The `/api/retrieve` preview deliberately
returns retrieval-only evidence and does not pretend it has verified a claim.
"""
from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import (
    ClaimEvidenceBundle,
    ClaimInput,
    InputMetadata,
    ScoreRequest,
    VerificationReport,
)
from app.pipeline.s5_report import build_report
from contract_adapter import to_score_request
from s2_service import apply_analysis, retrieval_summary


RetrievalFn = Callable[[Sequence[Any]], Awaitable[list[dict[str, Any]]]]
AnalysisFn = Callable[
    [Sequence[Any], Sequence[dict[str, Any]]],
    Sequence[Any] | Awaitable[Sequence[Any]],
]


class RetrievalPreviewRequest(BaseModel):
    """Debug/integration request for S2, using the shared claim contract."""

    input: InputMetadata = Field(default_factory=InputMetadata)
    claims: list[ClaimInput] = Field(min_length=1, max_length=6)


class RetrievalPreviewResponse(BaseModel):
    """S2 output only; intentionally contains no verdict."""

    input: InputMetadata
    claims: list[ClaimEvidenceBundle]
    retrieval_summary: dict[str, int]
    stage: Literal["S2_RETRIEVAL_ONLY"] = "S2_RETRIEVAL_ONLY"
    note: str = (
        "Retrieval only: evidence has not been analyzed for stance or quote-verified. "
        "Do not treat this response as a claim verdict."
    )


async def _default_retrieval(claims: Sequence[Any]) -> list[dict[str, Any]]:
    # Lazy import keeps the integration seam easy to replace in tests and avoids
    # starting retrieval work merely by importing the FastAPI application.
    from s2_service import aretrieve_for_claims

    return await aretrieve_for_claims(claims)


async def build_retrieval_preview(
    request: RetrievalPreviewRequest,
    *,
    retrieve_fn: RetrievalFn | None = None,
) -> RetrievalPreviewResponse:
    """Run S2 and return typed bundles for frontend/contract smoke tests."""
    bundles = await (retrieve_fn or _default_retrieval)(request.claims)
    typed_bundles = [
        ClaimEvidenceBundle.model_validate(
            {"claim": bundle.get("claim"), "evidence": bundle.get("evidence", [])}
        )
        for bundle in bundles
    ]
    return RetrievalPreviewResponse(
        input=request.input,
        claims=typed_bundles,
        retrieval_summary=retrieval_summary(bundles),
    )


async def run_verified_claim_pipeline(
    claims: Sequence[Any],
    *,
    analyze_fn: AnalysisFn,
    input_meta: Mapping[str, Any] | None = None,
    as_of: date | None = None,
    retrieve_fn: RetrievalFn | None = None,
) -> VerificationReport:
    """Run S2 -> S3 quote/stance merge -> S4/S5 report.

    ``analyze_fn`` is Ikjot's S3 entry point. It must return analyses keyed by
    ``evidence_id`` or ``url`` with fields such as ``stance``, ``quote``,
    ``quote_verified``, ``rationale``, ``context_score`` and ``context_gap``.
    `apply_analysis` independently re-checks quotes against the retrieved text
    before any evidence reaches scoring.
    """
    if not claims:
        raise ValueError("at least one claim is required")

    bundles = await (retrieve_fn or _default_retrieval)(claims)
    analysis_result = analyze_fn(claims, bundles)
    if inspect.isawaitable(analysis_result):
        analysis_result = await analysis_result

    analyzed_bundles = apply_analysis(bundles, analysis_result)
    payload = to_score_request(
        analyzed_bundles,
        input_meta=dict(input_meta or {}),
        as_of=as_of,
    )
    request = ScoreRequest.model_validate(payload)
    return build_report(request)
