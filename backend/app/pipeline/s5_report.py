from app.models import ScoreRequest, VerificationReport
from app.pipeline.s4_scoring import score_claim


def build_report(request: ScoreRequest) -> VerificationReport:
    """Assemble the API report from claim/evidence bundles from upstream stages."""
    scored_claims = [
        score_claim(bundle.claim, bundle.evidence, as_of=request.as_of)
        for bundle in request.claims
    ]
    return VerificationReport(input=request.input, claims=scored_claims)
