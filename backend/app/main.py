import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.models import (
    ClaimExtractionRequest,
    ClaimExtractionResponse,
    ExtractedClaim,
    ScoreRequest,
    VerificationReport,
)
from backend import (
    ArticleExtractionError,
    ClaimExtractionError,
    EmptyArticleError,
    InvalidURLError,
    LLMError,
    extract_claims,
    extract_claims_from_url,
)
from app.pipeline.orchestrator import (
    RetrievalPreviewRequest,
    RetrievalPreviewResponse,
    build_retrieval_preview,
)
from app.pipeline.s5_report import build_report
from app.pipeline.verify import (
    ExtractionFailure,
    VerifyInputError,
    VerifyRequest,
    VerifyResponse,
    run_verify,
)
from s2_service import aretrieve_for_claims

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(
    title="Factora VerifyAI API",
    version="0.4.0",
    description=(
        "Transparent claim verification. S1 extracts checkable claims, S2 retrieves and "
        "ranks evidence, S3 assigns a stance and verifies every quote against the "
        "retrieved text, S4/S5 score it. Evidence strength is not a probability of truth."
    ),
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "factora-verifyai"}


@app.post("/api/extract", response_model=ClaimExtractionResponse, tags=["pipeline"])
def extract_article_claims(request: ClaimExtractionRequest) -> ClaimExtractionResponse:
    """Run S1 only; this endpoint does not retrieve evidence or assign a verdict."""
    try:
        if request.url is not None:
            source_type = "url"
            s1_claims = extract_claims_from_url(request.url)
        else:
            source_type = "article_text"
            # The request validator guarantees that article_text is present here.
            s1_claims = extract_claims(request.article_text or "")
    except (EmptyArticleError, InvalidURLError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ArticleExtractionError, LLMError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ClaimExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # S1 calls this field ``claim_type``; the shared API contract calls it ``type``.
    # Keep S1's entity list in the extraction response instead of silently dropping it.
    claims = [
        ExtractedClaim(
            id=claim.id,
            text=claim.text,
            type=claim.claim_type,
            entities=claim.entities,
        )
        for claim in s1_claims
    ]
    return ClaimExtractionResponse(source_type=source_type, claims=claims)


@app.post("/api/retrieve", response_model=RetrievalPreviewResponse, tags=["pipeline"])
async def retrieve_claims(request: RetrievalPreviewRequest) -> RetrievalPreviewResponse:
    """S2-only integration preview; returns sources, not a truth verdict."""
    return await build_retrieval_preview(request, retrieve_fn=aretrieve_for_claims)


@app.post("/api/score", response_model=VerificationReport, tags=["scoring"])
def score_report(request: ScoreRequest) -> VerificationReport:
    """Score already-extracted claims and analyzed, quote-verified evidence."""
    return build_report(request)


@app.post("/api/verify", response_model=VerifyResponse, tags=["pipeline"])
async def verify_article(request: VerifyRequest) -> VerifyResponse:
    """Run the whole pipeline on one article: S1 → S2 → S3 → S4/S5.

    Every stage fails soft and is named in ``warnings``, so a report says what it
    could not do instead of hiding it. ``mode`` selects the evidence path: ``live``,
    ``offline`` (local corpus) or ``snapshot`` (the captured demo backup).
    """
    try:
        return await run_verify(request)
    except VerifyInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ExtractionFailure as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/samples", tags=["demo"])
def sample_articles() -> dict[str, Any]:
    """Prepared demo inputs, each written to exercise one outcome of the pipeline."""
    path = FRONTEND_DIR.parent / "data" / "samples.json"
    if not path.exists():
        return {"samples": [], "note": "data/samples.json is not present in this checkout."}
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/config", tags=["system"])
def pipeline_config() -> dict[str, Any]:
    """What this running process will actually do — the first thing to check when a
    demo behaves differently from the README."""
    from backend.config import get_settings

    settings = get_settings()
    snapshot = os.getenv("VERIFY_SNAPSHOT_PATH", "data/demo_snapshot.json")
    return {
        "s1_provider": settings.llm_provider,
        "s1_fallback_enabled": settings.llm_fallback_to_heuristics,
        "s3_mode": os.getenv("S3_MODE", "auto (lexical fallback)"),
        "verify_mode": os.getenv("VERIFY_MODE", "auto"),
        "snapshot_available": Path(snapshot).exists(),
        "fact_check_key_configured": bool(os.getenv("GOOGLE_FACTCHECK_API_KEY")),
        "tavily_key_configured": bool(os.getenv("TAVILY_API_KEY")),
        "disclaimer": (
            "Evidence strength is a measure of the evidence available, not a "
            "probability that a claim is true."
        ),
    }


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="ui")
