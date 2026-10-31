"""Tests: claim extraction behaviour, atomicity, classification, schema."""

from __future__ import annotations

import re

import pytest

from backend import Claim, extract_claims, extract_claims_detailed
from backend.errors import ClaimExtractionError, EmptyArticleError
from backend.llm_provider import MockProvider, StaticProvider
from backend.schemas import ALLOWED_CLAIM_TYPES

CLAIM_FIELDS = {"id", "text", "entities", "claim_type"}


def assert_mandatory_schema(claims: list[Claim]) -> None:
    """Every returned object must have exactly the four mandatory fields."""
    for claim in claims:
        assert isinstance(claim, Claim)
        assert set(claim.model_dump().keys()) == CLAIM_FIELDS
        assert isinstance(claim.id, str) and claim.id
        assert isinstance(claim.text, str) and claim.text.strip()
        assert isinstance(claim.entities, list)
        assert all(isinstance(entity, str) for entity in claim.entities)
        assert claim.claim_type in ALLOWED_CLAIM_TYPES
        # ids must be stable and sequential
        assert re.fullmatch(r"claim_\d{3}", claim.id)


# ---------------------------------------------------------------------------
# 1. Normal news article
# ---------------------------------------------------------------------------


def test_normal_news_article_returns_three_to_six_claims(solar_article):
    claims = extract_claims(solar_article)

    assert 3 <= len(claims) <= 6
    assert_mandatory_schema(claims)
    assert [claim.id for claim in claims] == [
        f"claim_{index:03d}" for index in range(1, len(claims) + 1)
    ]


def test_claims_are_grounded_in_the_article(solar_article):
    claims = extract_claims(solar_article)
    for claim in claims:
        # every number in a claim must exist in the article
        for number in re.findall(r"\d[\d,]*(?:\.\d+)?", claim.text):
            normalized = number.replace(",", "")
            assert normalized in solar_article.replace(",", ""), (
                f"claim invented number {number}: {claim.text}"
            )


# ---------------------------------------------------------------------------
# 2. Numerical statistics
# ---------------------------------------------------------------------------


def test_numerical_statistics_preserved(stats_article):
    claims = extract_claims(stats_article)
    texts = " ".join(claim.text for claim in claims)

    assert "$94.9 billion" in texts or "94.9" in texts
    assert "46.2 million" in texts or "46.2" in texts
    # "approximately 94 billion" stays approximate
    assert "12%" in texts
    assert_mandatory_schema(claims)


def test_statistics_claims_are_classified_factual(stats_article):
    claims = extract_claims(stats_article)
    reported = [c for c in claims if any(token in c.text for token in ("94.9", "46.2", "12%"))]

    assert len(reported) == 3
    assert all(claim.claim_type == "factual" for claim in reported)
    # An analyst expectation is forward-looking, not a reported fact.
    expectation = next(c for c in claims if "expected" in c.text.lower())
    assert expectation.claim_type == "prediction"


# ---------------------------------------------------------------------------
# 3. Historical events
# ---------------------------------------------------------------------------


def test_historical_event_extracted():
    article = (
        "India gained independence from British rule in 1947. "
        "The Indian Constitution came into effect on 26 January 1950. "
        "Jawaharlal Nehru became the first Prime Minister of India."
    )
    claims = extract_claims(article)

    assert 1 <= len(claims) <= 3
    assert any("1947" in claim.text for claim in claims)
    assert all(claim.claim_type == "factual" for claim in claims)
    assert_mandatory_schema(claims)


# ---------------------------------------------------------------------------
# 4. Government announcements (future plan => factual, not prediction)
# ---------------------------------------------------------------------------


def test_government_announcement_is_factual_not_prediction(announcement_article):
    claims = extract_claims(announcement_article)
    announcement_claims = [c for c in claims if "solar parks" in c.text.lower()]

    assert announcement_claims, "the announcement claim should be preserved"
    for claim in announcement_claims:
        assert claim.claim_type == "factual", claim.text
    # the announcement wording itself is preserved (no strengthening)
    assert any("announced" in c.text.lower() for c in announcement_claims)


# ---------------------------------------------------------------------------
# 5. Predictions
# ---------------------------------------------------------------------------


def test_prediction_is_labeled_as_prediction(solar_article):
    claims = extract_claims(solar_article)
    predictions = [claim for claim in claims if claim.claim_type == "prediction"]

    assert predictions, "predictions must not be silently dropped"
    assert any("2030" in claim.text for claim in predictions)
    assert any("may" in claim.text.lower() for claim in predictions)


def test_predictions_are_not_converted_to_facts():
    article = (
        "India may become the world's largest solar producer by 2030. "
        "The country installed 25 GW of solar capacity in 2025. "
        "Analysts expect solar capacity to double by 2028."
    )
    claims = extract_claims(article)
    for claim in claims:
        if "2030" in claim.text or "2028" in claim.text:
            assert claim.claim_type != "factual", claim.text
            assert any(
                marker in claim.text.lower() for marker in ("may", "expect", "could", "might")
            ), claim.text


# ---------------------------------------------------------------------------
# 6. Opinions
# ---------------------------------------------------------------------------


def test_opinion_is_labeled_as_opinion(solar_article):
    claims = extract_claims(solar_article)
    opinions = [claim for claim in claims if claim.claim_type == "opinion"]

    assert opinions, "opinions must be preserved and labelled, not discarded"
    assert any("impressive" in claim.text.lower() for claim in opinions)


def test_non_verifiable_is_labeled():
    article = (
        "Many people believe the government is hiding the real numbers. "
        "Several sources say the data was manipulated. "
        "The ministry released its annual report in 2025."
    )
    claims = extract_claims(article)
    types = {claim.claim_type for claim in claims}
    assert "non_verifiable" in types
    assert_mandatory_schema(claims)


# ---------------------------------------------------------------------------
# 7. Compound sentences / atomicity
# ---------------------------------------------------------------------------


def test_compound_sentence_is_split_into_atomic_claims(solar_article):
    claims = extract_claims(solar_article)
    texts = [claim.text for claim in claims]

    assert "India launched 15 solar projects in 2025." in texts
    assert "The 15 solar projects have a combined capacity of 10 GW." in texts
    assert (
        "The government announced an investment of Rs 5,000 crore for the 15 solar projects."
        in texts
    )


def test_each_claim_holds_a_single_fact():
    article = (
        "The company opened 5 factories in Gujarat and hired 2,000 workers in 2024. "
        "It spent $40 million on the expansion."
    )
    claims = extract_claims(article)
    for claim in claims:
        # a claim must not contain two distinct capitalised statistics blocks
        # unless it is a single coordinated statistic
        assert " and hired " not in claim.text.lower(), claim.text


# ---------------------------------------------------------------------------
# 8. Context-dependent references
# ---------------------------------------------------------------------------


def test_context_dependent_references_resolved(solar_article):
    claims = extract_claims(solar_article)
    for claim in claims:
        lowered = claim.text.lower()
        assert not lowered.startswith("these ")
        assert not lowered.startswith("those ")
        assert " for them" not in lowered
        assert " these projects" not in lowered


def test_reference_resolution_brings_antecedent_into_claim(solar_article):
    detailed = extract_claims_detailed(solar_article)
    texts = [claim.text for claim in detailed.claims]
    resolved = [t for t in texts if "15 solar projects" in t]
    assert len(resolved) >= 2, texts


# ---------------------------------------------------------------------------
# 9. Duplicate claims
# ---------------------------------------------------------------------------


def test_duplicate_claims_are_removed():
    article = (
        "India installed 25 GW of solar capacity in 2025. "
        "The country added 25 GW of solar capacity in 2025. "
        "India installed 25 GW of solar capacity in 2025. "
        "The government announced a new solar policy in 2025."
    )
    detailed = extract_claims_detailed(article)

    assert detailed.deduplicated >= 1
    texts = [claim.text for claim in detailed.claims]
    assert len(texts) == len(set(texts))
    assert sum("25 GW" in text for text in texts) == 1


def test_repeated_sentence_appears_once(solar_article):
    doubled = solar_article + " " + solar_article
    detailed = extract_claims_detailed(doubled)
    texts = [claim.text for claim in detailed.claims]
    assert len(texts) == len(set(texts))
    assert detailed.deduplicated >= 1


# ---------------------------------------------------------------------------
# 10-11. Selection behaviour
# ---------------------------------------------------------------------------


def test_never_more_than_six_claims():
    article = " ".join(
        f"India commissioned {index} solar parks in 2025."
        for index in range(101, 121)
    )
    claims = extract_claims(article)
    assert len(claims) == 6


def test_fewer_claims_returned_when_article_is_small():
    article = "India installed 25 GW of solar capacity in 2025."
    claims = extract_claims(article)

    assert 1 <= len(claims) <= 3
    assert_mandatory_schema(claims)


def test_no_claims_are_padded_when_none_exist():
    article = "Good morning. See you tomorrow. Thank you."
    detailed = extract_claims_detailed(article)
    assert detailed.claims == [] or all(
        claim.claim_type == "non_verifiable" for claim in detailed.claims
    )


# ---------------------------------------------------------------------------
# 12. Empty input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", ["", "   \n\t  ", "\u00a0\u00a0"])
def test_empty_input_raises(payload):
    with pytest.raises(EmptyArticleError):
        extract_claims(payload)


def test_none_input_raises():
    with pytest.raises(EmptyArticleError):
        extract_claims(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Mocked LLM path (deterministic)
# ---------------------------------------------------------------------------


def test_mocked_llm_response_produces_expected_claims(solar_article):
    payload = """
    ```json
    {"claims": [
      {"text": "India launched 15 solar projects in 2025.", "claim_type": "factual",
       "source_sentence_index": 0, "entities": ["India"]},
      {"text": "The 15 solar projects have a combined capacity of 10 GW.",
       "claim_type": "factual", "source_sentence_index": 1, "entities": ["India"]},
      {"text": "India may become the world's largest solar producer by 2030.",
       "claim_type": "prediction", "source_sentence_index": 3, "entities": ["India"]}
    ]}
    ```
    """
    claims = extract_claims(solar_article, provider=StaticProvider(payload))

    assert len(claims) == 3
    assert_mandatory_schema(claims)
    assert claims[0].text == "India launched 15 solar projects in 2025."
    assert claims[0].entities == ["India"]
    assert claims[2].claim_type == "prediction"


def test_mocked_llm_claim_order_follows_article_order(solar_article):
    payload = """{"claims": [
      {"text": "India installed 25 GW of solar capacity in 2025.", "claim_type": "factual"},
      {"text": "India launched 15 solar projects in 2025.", "claim_type": "factual"}
    ]}"""
    claims = extract_claims(solar_article, provider=StaticProvider(payload))
    positions = [solar_article.index(claim.text.rstrip(".")) for claim in claims]
    assert positions == sorted(positions)


def test_fabricated_llm_claim_is_rejected_and_raises(solar_article):
    payload = """{"claims": [
      {"text": "India installed 90 GW of solar capacity in 2025.", "claim_type": "factual"}
    ]}"""
    with pytest.raises(ClaimExtractionError):
        extract_claims(solar_article, provider=StaticProvider(payload))


def test_mock_provider_is_default_and_offline(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    claims = extract_claims(
        "India installed 25 GW of solar capacity in 2025. "
        "The government announced Rs 5,000 crore for solar parks."
    )
    assert claims
    assert_mandatory_schema(claims)
    assert isinstance(MockProvider(), MockProvider)


def test_forecast_action_is_factual_but_expectation_is_prediction():
    """Raising a forecast is a factual action; expecting a cut is a prediction."""
    article = (
        "The Reserve Bank of India raised its GDP growth forecast for the current "
        "fiscal year to 7.2%. Retail inflation eased to 4.8% in September. "
        "Economists expect the RBI to cut rates by 25 basis points in December."
    )
    claims = extract_claims(article)

    forecast_claim = next(c for c in claims if "forecast" in c.text.lower())
    expectation_claim = next(c for c in claims if "expect" in c.text.lower())

    assert forecast_claim.claim_type == "factual", forecast_claim.text
    assert expectation_claim.claim_type == "prediction", expectation_claim.text
    assert any("4.8%" in c.text and c.claim_type == "factual" for c in claims)
