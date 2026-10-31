import pytest
from app.schemas import Claim, Evidence, Analysis, Stance, SourceKind, ClaimType, ScoreBreakdown
from app.scoring import calculate_score
from app.report import build_report


def test_score_calculation():
    """Test that scoring produces valid results."""
    evidence = [
        Evidence(
            claim_id="claim-1",
            url="https://example.gov/report",
            domain="example.gov",
            title="Government Report",
            text_or_snippet="Temperature rose 1.1°C.",
            published_at="2023-01-01T00:00:00",
            source_kind=SourceKind.GOVERNMENT,
            relevance=0.9,
        ),
        Evidence(
            claim_id="claim-1",
            url="https://example.edu/study",
            domain="example.edu",
            title="Academic Study",
            text_or_snippet="Warming of 1.1°C observed.",
            published_at="2024-01-01T00:00:00",
            source_kind=SourceKind.ACADEMIC,
            relevance=0.85,
        ),
    ]
    analyses = [
        Analysis(stance=Stance.SUPPORTS, quote="Temperature rose 1.1°C.", quote_verified=True, rationale="Matches"),
        Analysis(stance=Stance.SUPPORTS, quote="Warming of 1.1°C observed.", quote_verified=True, rationale="Matches"),
    ]

    score = calculate_score(evidence, analyses)

    assert 0.0 <= score.S <= 1.0
    assert 0.0 <= score.C <= 1.0
    assert 0.0 <= score.R <= 1.0
    assert 0.0 <= score.A <= 1.0
    assert 0.0 <= score.T <= 1.0
    assert 0.0 <= score.K <= 1.0
    assert score.C_explanation
    assert score.R_explanation


def test_build_report():
    """Test report building."""
    claim = Claim(
        id="claim-1",
        text="Test claim",
        type=ClaimType.FACTUAL,
        article_quote="Test claim",
        time_sensitive=False,
        entities=["test"],
    )
    evidence = [
        Evidence(
            claim_id="claim-1",
            url="https://example.com",
            domain="example.com",
            title="Source",
            text_or_snippet="Evidence text",
            source_kind=SourceKind.OTHER,
            relevance=0.8,
        )
    ]
    analyses = [
        Analysis(stance=Stance.SUPPORTS, quote="Evidence text", quote_verified=True, rationale="Direct match"),
    ]
    score = ScoreBreakdown(C=0.8, R=0.8, A=0.8, T=0.8, K=0.8, S=0.8)

    report = build_report(claim, Stance.SUPPORTS, "supports", score, evidence, analyses)

    assert report.claim.id == "claim-1"
    assert report.verdict == Stance.SUPPORTS
    assert report.stance_direction == "supports"
    assert report.evidence_strength.S == 0.8
    assert len(report.evidence) == 1
    assert len(report.analysis) == 1
    assert report.audit.total_results_retrieved == 1
    assert report.audit.results_used == 1


if __name__ == "__main__":
    test_score_calculation()
    test_build_report()
    print("All tests passed!")