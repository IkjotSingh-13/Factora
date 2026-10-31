from __future__ import annotations

from datetime import date
from math import exp, log
from statistics import median
from typing import Iterable, Optional

from app.models import (
    AuditSummary,
    ClaimInput,
    ClaimReport,
    EvidenceItem,
    ScoreComponents,
    Stance,
    Verdict,
)


def _independent_representatives(items: Iterable[EvidenceItem]) -> list[EvidenceItem]:
    """Keep the strongest item per normalized domain for stance aggregation."""
    best_by_domain: dict[str, EvidenceItem] = {}
    for item in items:
        key = item.domain.strip().lower() or item.id.strip().lower()
        current = best_by_domain.get(key)
        if current is None or item.relevance * item.credibility > current.relevance * current.credibility:
            best_by_domain[key] = item
    return list(best_by_domain.values())


def _freshness(items: list[EvidenceItem], time_sensitive: bool, as_of: date) -> tuple[float, bool]:
    dates = [item.published_at for item in items if item.published_at is not None]
    if not dates:
        return 0.5, True

    median_ordinal = round(median(d.toordinal() for d in dates))
    median_date = date.fromordinal(median_ordinal)
    age_years = max(0.0, (as_of - median_date).days / 365.25)

    if time_sensitive:
        value = exp(-log(2) * age_years / 1.5)
    else:
        value = 0.85 - 0.15 * min(1.0, age_years / 10.0)
    return min(1.0, max(0.0, value)), False


def score_claim(
    claim: ClaimInput,
    evidence: list[EvidenceItem],
    as_of: Optional[date] = None,
) -> ClaimReport:
    """Score one claim using only quote-verified evidence.

    The weights follow the hackathon plan: S_raw = .30C + .30R + .20A + .10T + .10K.
    When fewer than three independent stance-bearing domains are available, the raw
    score is shrunk toward 0.5 so a thin evidence set cannot look overconfident.
    """
    reference_date = as_of or date.today()
    used = [item for item in evidence if item.quote_verified and item.quote.strip()]
    dropped = len(evidence) - len(used)

    if used:
        relevance_weights = [max(item.relevance, 0.05) for item in used]
        weight_total = sum(relevance_weights)
        C = sum(w * item.credibility for w, item in zip(relevance_weights, used)) / weight_total
        R = sum(item.relevance for item in used) / len(used)
        mean_context = sum(item.context_score for item in used) / len(used)
        no_context_gaps = all(not item.context_gap for item in used)
        K = 0.7 * mean_context + 0.3 * (1.0 if no_context_gaps else 0.0)
    else:
        C, R, K = 0.35, 0.0, 0.5

    T, unknown_freshness = _freshness(used, claim.time_sensitive, reference_date)

    stance_items = [item for item in used if item.stance != Stance.NEUTRAL]
    independent = _independent_representatives(stance_items)
    n_independent = len(independent)

    support_weight = sum(
        item.relevance * item.credibility
        for item in independent
        if item.stance == Stance.SUPPORTS
    )
    refute_weight = sum(
        item.relevance * item.credibility
        for item in independent
        if item.stance == Stance.REFUTES
    )
    directional_total = support_weight + refute_weight
    if directional_total:
        agreement = abs(support_weight - refute_weight) / directional_total
        stance_score = (support_weight - refute_weight) / directional_total
    else:
        agreement = 0.0
        stance_score = 0.0

    independence = 1.0 - exp(-n_independent / 2.0) if n_independent else 0.0
    A = agreement * independence

    score_raw = 0.30 * C + 0.30 * R + 0.20 * A + 0.10 * T + 0.10 * K
    shrinkage_factor = min(1.0, n_independent / 3.0)
    evidence_strength = 0.5 + (score_raw - 0.5) * shrinkage_factor
    evidence_strength = min(1.0, max(0.0, evidence_strength))

    # Resolve clear two-sided disagreement before the low-score fallback.
    if n_independent >= 2 and directional_total > 0 and agreement < 0.25:
        verdict = Verdict.CONFLICTING_EVIDENCE
    elif n_independent < 2 or evidence_strength < 0.45:
        verdict = Verdict.INSUFFICIENT_EVIDENCE
    elif evidence_strength >= 0.70 and support_weight > refute_weight:
        verdict = Verdict.SUPPORTED
    elif evidence_strength >= 0.65 and refute_weight > support_weight:
        verdict = Verdict.REFUTED
    else:
        verdict = Verdict.MIXED

    conflicts: list[str] = []
    if support_weight > 0 and refute_weight > 0:
        conflicts = [
            item.title or item.domain or item.url
            for item in independent
            if item.stance in (Stance.SUPPORTS, Stance.REFUTES)
        ]

    scores = ScoreComponents(
        C=round(C, 4),
        R=round(R, 4),
        A=round(A, 4),
        T=round(T, 4),
        K=round(K, 4),
    )
    contributions = {
        "C": round(0.30 * C, 4),
        "R": round(0.30 * R, 4),
        "A": round(0.20 * A, 4),
        "T": round(0.10 * T, 4),
        "K": round(0.10 * K, 4),
    }

    return ClaimReport(
        claim=claim,
        verdict=verdict,
        stance_score=round(stance_score, 4),
        evidence_strength=round(evidence_strength, 4),
        score_raw=round(score_raw, 4),
        shrinkage_factor=round(shrinkage_factor, 4),
        scores=scores,
        contributions=contributions,
        evidence=used,
        conflicts=conflicts,
        audit=AuditSummary(
            n_retrieved=len(evidence),
            n_used=len(used),
            n_independent=n_independent,
            hallucinated_quotes_dropped=dropped,
            unknown_freshness=unknown_freshness,
        ),
    )
