from fastapi import FastAPI, HTTPException

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
from s2_service import aretrieve_for_claims

app = FastAPI(
    title="Factora VerifyAI API",
    version="0.3.0",
    description=(
        "Transparent claim verification backend. S1 claim extraction and S2 retrieval "
        "are available as separate stages; a full `/api/verify` verdict still requires S3 analysis."
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
