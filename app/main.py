from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import time
import os

from app.schemas import (
    VerifyRequest, VerifyResponse, ClaimReport, Claim, Evidence, Analysis,
    ClaimType, Stance, SourceKind, ScoreBreakdown, AuditInfo
)
from app.scoring import calculate_score
from app.report import build_report


MOCK_MODE = os.getenv("MOCK_MODE", "true").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"VerifyAI starting up (MOCK_MODE={MOCK_MODE})")
    yield
    print("VerifyAI shutting down")


app = FastAPI(title="VerifyAI", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Mock data for demo/development
MOCK_CLAIMS = [
    Claim(
        id="claim-1",
        text="Global temperatures have risen by 1.1°C since pre-industrial times.",
        type=ClaimType.STATISTICAL,
        article_quote="Global temperatures have risen by 1.1°C since pre-industrial times.",
        time_sensitive=False,
        entities=["global temperatures", "pre-industrial times", "1.1°C"]
    ),
    Claim(
        id="claim-2",
        text="The Arctic sea ice extent has declined by 13% per decade since 1979.",
        type=ClaimType.STATISTICAL,
        article_quote="The Arctic sea ice extent has declined by 13% per decade since 1979.",
        time_sensitive=True,
        entities=["Arctic sea ice", "13%", "decade", "1979"]
    ),
]

MOCK_EVIDENCE = [
    Evidence(
        claim_id="claim-1",
        url="https://www.ipcc.ch/report/ar6/wg1/",
        domain="ipcc.ch",
        title="IPCC Sixth Assessment Report - Working Group I",
        text_or_snippet="Global surface temperature was 1.09°C higher in 2011–2020 than 1850–1900.",
        published_at="2021-08-09T00:00:00",
        source_kind=SourceKind.GOVERNMENT,
        relevance=0.95
    ),
    Evidence(
        claim_id="claim-1",
        url="https://climate.nasa.gov/vital-signs/global-temperature/",
        domain="climate.nasa.gov",
        title="Global Temperature | NASA Climate",
        text_or_snippet="Earth's average surface temperature has risen about 1.1°C since the late 19th century.",
        published_at="2024-01-15T00:00:00",
        source_kind=SourceKind.GOVERNMENT,
        relevance=0.92
    ),
    Evidence(
        claim_id="claim-2",
        url="https://nsidc.org/arctic-sea-ice-news",
        domain="nsidc.org",
        title="Arctic Sea Ice News and Analysis | NSIDC",
        text_or_snippet="Arctic sea ice extent has declined 13% per decade relative to the 1981-2010 average.",
        published_at="2024-03-01T00:00:00",
        source_kind=SourceKind.ACADEMIC,
        relevance=0.94
    ),
]

MOCK_ANALYSES = [
    Analysis(
        stance=Stance.SUPPORTS,
        quote="Global surface temperature was 1.09°C higher in 2011–2020 than 1850–1900.",
        quote_verified=True,
        rationale="IPCC AR6 reports 1.09°C warming, consistent with the 1.1°C claim.",
        context_gap=None
    ),
    Analysis(
        stance=Stance.SUPPORTS,
        quote="Earth's average surface temperature has risen about 1.1°C since the late 19th century.",
        quote_verified=True,
        rationale="NASA vital signs page confirms ~1.1°C warming since late 19th century.",
        context_gap=None
    ),
    Analysis(
        stance=Stance.SUPPORTS,
        quote="Arctic sea ice extent has declined 13% per decade relative to the 1981-2010 average.",
        quote_verified=True,
        rationale="NSIDC data shows 13% per decade decline, matching the claim.",
        context_gap=None
    ),
]


def get_mock_reports() -> list[ClaimReport]:
    reports = []
    for claim in MOCK_CLAIMS:
        claim_evidence = [e for e in MOCK_EVIDENCE if e.claim_id == claim.id]
        claim_analyses = [a for a in MOCK_ANALYSES if any(e.claim_id == claim.id for e in MOCK_EVIDENCE)]

        # Determine verdict from analyses
        stances = [a.stance for a in claim_analyses]
        if all(s == Stance.SUPPORTS for s in stances):
            verdict = Stance.SUPPORTS
            stance_direction = "supports"
        elif all(s == Stance.REFUTES for s in stances):
            verdict = Stance.REFUTES
            stance_direction = "refutes"
        elif Stance.SUPPORTS in stances and Stance.REFUTES in stances:
            verdict = Stance.SUPPORTS  # mixed but we pick one
            stance_direction = "mixed"
        else:
            verdict = Stance.NEUTRAL
            stance_direction = "insufficient"

        score = calculate_score(claim_evidence, claim_analyses)
        report = build_report(claim, verdict, stance_direction, score, claim_evidence, claim_analyses)
        reports.append(report)
    return reports


@app.get("/health")
async def health():
    return {"status": "ok", "mock_mode": MOCK_MODE}


@app.post("/api/verify", response_model=VerifyResponse)
async def verify(request: VerifyRequest):
    start_time = time.time()

    if MOCK_MODE:
        # Simulate processing time
        import asyncio
        await asyncio.sleep(0.5)
        reports = get_mock_reports()
        for r in reports:
            r.audit.mock_mode = True
            r.audit.processing_time_ms = int((time.time() - start_time) * 1000)
        return VerifyResponse(reports=reports, meta={"mode": "mock"})

    # TODO: Real pipeline integration
    # 1. Extract claims from request.text (call Ikjot's s1_claims.py)
    # 2. Retrieve evidence for each claim (call Mansimran's s2_retrieval.py)
    # 3. Analyze stance for each evidence (call Ikjot's s3_analysis.py)
    # 4. Score and build report (this module)

    raise HTTPException(status_code=501, detail="Real pipeline not yet implemented. Set MOCK_MODE=true.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)