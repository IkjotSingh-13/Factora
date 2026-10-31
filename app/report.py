from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from app.schemas import (
    Claim, ClaimReport, Evidence, Analysis, Stance, ScoreBreakdown, AuditInfo
)


def build_report(
    claim: Claim,
    verdict: Stance,
    stance_direction: str,
    score: ScoreBreakdown,
    evidence: list[Evidence],
    analyses: list[Analysis],
) -> ClaimReport:
    """Build a complete ClaimReport with audit trail."""

    # Count sources queried (unique domains from evidence)
    sources_queried = list(set(e.domain for e in evidence))

    # Count quotes dropped
    quotes_dropped = sum(1 for a in analyses if not a.quote_verified)
    quote_drop_reasons = []
    for a in analyses:
        if not a.quote_verified:
            quote_drop_reasons.append(f"Quote not found in source text for {a.stance.value} analysis")

    # Sources failed - placeholder for real pipeline
    sources_failed = []

    audit = AuditInfo(
        sources_queried=sources_queried,
        sources_failed=sources_failed,
        total_results_retrieved=len(evidence),
        results_used=sum(1 for a in analyses if a.quote_verified),
        quotes_dropped=quotes_dropped,
        quote_drop_reasons=quote_drop_reasons,
        mock_mode=False,
        processing_time_ms=0,
    )

    return ClaimReport(
        claim=claim,
        verdict=verdict,
        stance_direction=stance_direction,
        evidence_strength=score,
        evidence=evidence,
        analysis=analyses,
        audit=audit,
    )


def format_report_for_display(report: ClaimReport) -> dict:
    """Format report for frontend display."""
    return {
        "claim": {
            "id": report.claim.id,
            "text": report.claim.text,
            "type": report.claim.type.value,
            "article_quote": report.claim.article_quote,
            "time_sensitive": report.claim.time_sensitive,
            "entities": report.claim.entities,
        },
        "verdict": report.verdict.value,
        "stance_direction": report.stance_direction,
        "evidence_strength": {
            "score": report.evidence_strength.S,
            "components": {
                "C": {"value": report.evidence_strength.C, "label": "Source Credibility", "explanation": report.evidence_strength.C_explanation},
                "R": {"value": report.evidence_strength.R, "label": "Relevance", "explanation": report.evidence_strength.R_explanation},
                "A": {"value": report.evidence_strength.A, "label": "Agreement/Diversity", "explanation": report.evidence_strength.A_explanation},
                "T": {"value": report.evidence_strength.T, "label": "Freshness", "explanation": report.evidence_strength.T_explanation},
                "K": {"value": report.evidence_strength.K, "label": "Contextual Consistency", "explanation": report.evidence_strength.K_explanation},
            }
        },
        "evidence": [
            {
                "url": str(e.url),
                "domain": e.domain,
                "title": e.title,
                "snippet": e.text_or_snippet,
                "published_at": e.published_at.isoformat() if e.published_at else None,
                "source_kind": e.source_kind.value,
                "relevance": e.relevance,
            }
            for e in report.evidence
        ],
        "analysis": [
            {
                "stance": a.stance.value,
                "quote": a.quote,
                "quote_verified": a.quote_verified,
                "rationale": a.rationale,
                "context_gap": a.context_gap,
            }
            for a in report.analysis
        ],
        "audit": {
            "sources_queried": report.audit.sources_queried,
            "sources_failed": report.audit.sources_failed,
            "total_results_retrieved": report.audit.total_results_retrieved,
            "results_used": report.audit.results_used,
            "quotes_dropped": report.audit.quotes_dropped,
            "quote_drop_reasons": report.audit.quote_drop_reasons,
            "mock_mode": report.audit.mock_mode,
            "processing_time_ms": report.audit.processing_time_ms,
        }
    }