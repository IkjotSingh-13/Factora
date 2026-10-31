from fastapi import FastAPI

from app.models import ScoreRequest, VerificationReport
from app.pipeline.s5_report import build_report

app = FastAPI(
    title="Factora VerifyAI API",
    version="0.1.0",
    description=(
        "Transparent claim verification backend. This starter exposes the scoring/report "
        "stage; article extraction and evidence retrieval are integrated next."
    ),
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "factora-verifyai"}


@app.post("/api/score", response_model=VerificationReport, tags=["scoring"])
def score_report(request: ScoreRequest) -> VerificationReport:
    """Score already-extracted claims and analyzed, quote-verified evidence."""
    return build_report(request)
