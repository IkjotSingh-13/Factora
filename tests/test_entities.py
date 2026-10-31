"""Tests: entity extraction quality and relevance."""

from __future__ import annotations

from backend import extract_entities
from backend.s1_claims import extract_claims

ARTICLE = (
    "Apple acquired a startup in California. "
    "Tim Cook said the deal would help Apple expand its services business. "
    "The company paid an undisclosed amount for the acquisition."
)


def test_entities_from_spec_examples():
    assert extract_entities("India installed 25 GW of solar capacity in 2025.") == ["India"]
    assert extract_entities("Apple acquired a startup in California.") == [
        "Apple",
        "California",
    ]


def test_entities_are_relevant_to_the_claim_only():
    """Entities from other sentences must not leak into a claim's entity list."""
    entities = extract_entities(
        "Apple acquired a startup in California.",
        article_text=ARTICLE,
    )
    assert "Apple" in entities
    assert "California" in entities
    assert "Tim Cook" not in entities
    assert "India" not in entities


def test_entity_must_be_grounded_by_the_article():
    """A fabricated entity is dropped even if the LLM suggested it."""
    entities = extract_entities(
        "Apple acquired a startup in California.",
        article_text="Apple acquired a startup last year.",
    )
    assert entities == ["Apple"]


def test_llm_suggested_entities_are_kept_when_grounded():
    entities = extract_entities(
        "The 15 solar projects have a combined capacity of 10 GW.",
        article_text="India launched 15 solar projects in 2025.",
        extra_candidates=["India"],
    )
    assert entities == ["India"]


def test_entities_are_deduplicated():
    entities = extract_entities(
        "India and India installed capacity in India.",
        article_text="India installed 25 GW of solar capacity in 2025.",
    )
    assert entities == ["India"]


def test_generic_role_nouns_are_not_entities():
    """The spec maps 'The government announced ...' to ['India'], not
    ['The government']."""
    assert extract_entities("The government announced an investment of Rs 5,000 crore.") == []
    assert extract_entities("The company said it would expand.") == []


def test_units_and_abbreviations_are_not_entities():
    entities = extract_entities("The plant has a capacity of 10 GW and 5 MW.")
    assert "GW" not in entities
    assert "MW" not in entities


def test_entities_in_final_claims_are_grounded(solar_article):
    claims = extract_claims(solar_article)
    for claim in claims:
        for entity in claim.entities:
            assert entity.casefold() in solar_article.casefold(), (
                f"ungrounded entity {entity!r} in claim {claim.text!r}"
            )
        assert len(claim.entities) == len({e.casefold() for e in claim.entities})


def test_context_dependent_claim_inherits_article_subject(solar_article):
    """'The 15 solar projects ...' has no entity of its own: it inherits India."""
    claims = extract_claims(solar_article)
    project_claim = next(c for c in claims if "combined capacity" in c.text)
    assert project_claim.entities == ["India"]


def test_multiple_entity_types():
    entities = extract_entities(
        "The European Union fined Apple in Ireland.",
        article_text="The European Union fined Apple in Ireland on Tuesday.",
    )
    assert "Apple" in entities
    assert any("European" in entity for entity in entities)


def test_entity_extraction_is_stable_across_calls():
    first = extract_entities("Apple acquired a startup in California.")
    second = extract_entities("Apple acquired a startup in California.")
    assert first == second
