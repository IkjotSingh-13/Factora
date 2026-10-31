from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone
from app.schemas import Evidence, Analysis, ScoreBreakdown, Stance, SourceKind


# Source credibility weights by kind
CREDIBILITY_WEIGHTS = {
    SourceKind.GOVERNMENT: 1.0,
    SourceKind.ACADEMIC: 0.95,
    SourceKind.FACT_CHECK: 0.9,
    SourceKind.NEWS: 0.7,
    SourceKind.WIKIPEDIA: 0.6,
    SourceKind.OTHER: 0.4,
}


def calculate_credibility(evidence: list[Evidence]) -> tuple[float, str]:
    """C: Source credibility - weighted by source kind."""
    if not evidence:
        return 0.0, "No evidence provided"

    total_weight = 0.0
    weighted_sum = 0.0
    for e in evidence:
        weight = CREDIBILITY_WEIGHTS.get(e.source_kind, 0.4)
        weighted_sum += weight * e.relevance
        total_weight += weight

    if total_weight == 0:
        return 0.0, "No valid sources"

    score = weighted_sum / total_weight
    kinds = [e.source_kind.value for e in evidence]
    return round(score, 3), f"Sources: {', '.join(set(kinds))}"


def calculate_relevance(evidence: list[Evidence], analyses: list[Analysis]) -> tuple[float, str]:
    """R: Evidence relevance - average relevance of evidence that has verified quotes."""
    if not evidence:
        return 0.0, "No evidence"

    verified_evidence = [e for e, a in zip(evidence, analyses) if a.quote_verified]
    if not verified_evidence:
        return 0.0, "No verified quotes"

    avg_relevance = sum(e.relevance for e in verified_evidence) / len(verified_evidence)
    return round(avg_relevance, 3), f"{len(verified_evidence)}/{len(evidence)} evidence items have verified quotes"


def calculate_agreement(evidence: list[Evidence], analyses: list[Analysis]) -> tuple[float, str]:
    """A: Independent agreement / source diversity - stance consistency across diverse domains."""
    if not analyses:
        return 0.0, "No analyses"

    # Group by domain
    domain_stances = {}
    for e, a in zip(evidence, analyses):
        if a.quote_verified:
            domain_stances.setdefault(e.domain, []).append(a.stance)

    if not domain_stances:
        return 0.0, "No verified analyses"

    # Check consistency per domain
    consistent_domains = 0
    for domain, stances in domain_stances.items():
        if all(s == stances[0] for s in stances):
            consistent_domains += 1

    # Diversity bonus: more unique domains = better
    diversity_factor = min(len(domain_stances) / 3.0, 1.0)  # caps at 3 domains
    consistency_ratio = consistent_domains / len(domain_stances) if domain_stances else 0

    score = (0.6 * consistency_ratio) + (0.4 * diversity_factor)
    return round(score, 3), f"{len(domain_stances)} unique domains, {consistent_domains} internally consistent"


def calculate_freshness(evidence: list[Evidence]) -> tuple[float, str]:
    """T: Freshness - how recent is the evidence?"""
    if not evidence:
        return 0.0, "No evidence"

    now = datetime.now(timezone.utc)
    scores = []
    for e in evidence:
        if e.published_at:
            # Ensure timezone-aware
            pub = e.published_at
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            age_days = (now - pub).days
            # Exponential decay: 1.0 at 0 days, ~0.5 at 365 days, ~0.1 at 1825 days (5 years)
            freshness = 2 ** (-age_days / 365.0)
            scores.append(freshness)
        else:
            scores.append(0.5)  # Unknown date = neutral

    avg = sum(scores) / len(scores) if scores else 0.0
    dated = sum(1 for e in evidence if e.published_at)
    return round(avg, 3), f"{dated}/{len(evidence)} items dated"


def calculate_consistency(evidence: list[Evidence], analyses: list[Analysis]) -> tuple[float, str]:
    """K: Contextual consistency - do verified quotes actually support the stance?"""
    if not analyses:
        return 0.0, "No analyses"

    verified = [a for a in analyses if a.quote_verified]
    if not verified:
        return 0.0, "No verified quotes"

    # For now, simple check: stance != NEUTRAL means quote is relevant
    non_neutral = sum(1 for a in verified if a.stance != Stance.NEUTRAL)
    score = non_neutral / len(verified)
    return round(score, 3), f"{non_neutral}/{len(verified)} verified quotes are non-neutral"


def calculate_score(evidence: list[Evidence], analyses: list[Analysis]) -> ScoreBreakdown:
    """
    Calculate evidence strength score S = 0.30C + 0.30R + 0.20A + 0.10T + 0.10K

    Returns ScoreBreakdown with component values and explanations.
    """
    C, C_exp = calculate_credibility(evidence)
    R, R_exp = calculate_relevance(evidence, analyses)
    A, A_exp = calculate_agreement(evidence, analyses)
    T, T_exp = calculate_freshness(evidence)
    K, K_exp = calculate_consistency(evidence, analyses)

    S = round(0.30 * C + 0.30 * R + 0.20 * A + 0.10 * T + 0.10 * K, 3)

    return ScoreBreakdown(
        C=C, R=R, A=A, T=T, K=K, S=S,
        C_explanation=C_exp,
        R_explanation=R_exp,
        A_explanation=A_exp,
        T_explanation=T_exp,
        K_explanation=K_exp,
    )