from fastapi import FastAPI

from app.models import ScoreRequest, VerificationReport
from app.pipeline.orchestrator import (
    RetrievalPreviewRequest,
    RetrievalPreviewResponse,
    build_retrieval_preview,
)
from app.pipeline.s5_report import build_report
from s2_service import aretrieve_for_claims

app = FastAPI(
    title="Factora VerifyAI API",
    version="0.2.0",
    description=(
        "Transparent claim verification backend. S2 retrieval is integrated; "
        "the full `/api/verify` route will be connected when S1/S3 providers are merged."
    ),
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "factora-verifyai"}


@app.post("/api/retrieve", response_model=RetrievalPreviewResponse, tags=["pipeline"])
async def retrieve_claims(request: RetrievalPreviewRequest) -> RetrievalPreviewResponse:
    """S2-only integration preview; returns sources, not a truth verdict."""
    return await build_retrieval_preview(request, retrieve_fn=aretrieve_for_claims)


@app.post("/api/score", response_model=VerificationReport, tags=["scoring"])
def score_report(request: ScoreRequest) -> VerificationReport:
    """Score already-extracted claims and analyzed, quote-verified evidence."""
    return build_report(request)
