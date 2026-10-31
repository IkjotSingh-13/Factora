from datetime import date

from app.models import ClaimInput, EvidenceItem, Stance, Verdict
from app.pipeline.s4_scoring import score_claim


AS_OF = date(2026, 10, 31)


def evidence(eid: str, domain: str, stance: Stance, *, verified: bool = True) -> EvidenceItem:
    return EvidenceItem(
        id=eid,
        claim_id="c1",
        url=f"https://{domain}/claim",
        domain=domain,
        title=f"Source from {domain}",
        published_at=date(2025, 10, 1),
        relevance=0.9,
        credibility=0.9,
        stance=stance,
        quote="The source states a verifiable sentence.",
        quote_verified=verified,
        context_score=0.9,
    )


def test_multiple_independent_supporting_sources_can_support_claim() -> None:
    claim = ClaimInput(id="c1", text="A checkable factual claim.")
    result = score_claim(
        claim,
        [
            evidence("e1", "who.int", Stance.SUPPORTS),
            evidence("e2", "gov.example", Stance.SUPPORTS),
            evidence("e3", "journal.example", Stance.SUPPORTS),
        ],
        as_of=AS_OF,
    )

    assert result.verdict == Verdict.SUPPORTED
    assert result.stance_score > 0
    assert result.evidence_strength > 0.7
    assert result.audit.n_independent == 3
    assert result.audit.hallucinated_quotes_dropped == 0


def test_one_source_is_not_enough_for_a_confident_verdict() -> None:
    claim = ClaimInput(id="c1", text="A checkable factual claim.")
    result = score_claim(claim, [evidence("e1", "who.int", Stance.SUPPORTS)], as_of=AS_OF)

    assert result.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert result.audit.n_independent == 1
    assert result.shrinkage_factor < 1.0


def test_unverified_quote_is_dropped_from_scoring() -> None:
    claim = ClaimInput(id="c1", text="A checkable factual claim.")
    result = score_claim(
        claim,
        [evidence("e1", "who.int", Stance.SUPPORTS, verified=False)],
        as_of=AS_OF,
    )

    assert result.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert result.audit.n_used == 0
    assert result.audit.hallucinated_quotes_dropped == 1
    assert result.evidence == []


def test_balanced_independent_evidence_is_reported_as_conflicting() -> None:
    claim = ClaimInput(id="c1", text="A checkable factual claim.")
    result = score_claim(
        claim,
        [
            evidence("e1", "source-a.example", Stance.SUPPORTS),
            evidence("e2", "source-b.example", Stance.REFUTES),
        ],
        as_of=AS_OF,
    )

    assert result.verdict == Verdict.CONFLICTING_EVIDENCE
    assert result.audit.n_independent == 2
