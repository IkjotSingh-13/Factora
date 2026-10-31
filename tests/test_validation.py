"""Tests: strict validation (grounding, numbers, hedges, qualifiers)."""

from __future__ import annotations

import pytest

from backend import validate_claim
from backend.validators import check_claim, lexical_recall

ARTICLE = (
    "India installed 25 GW of solar capacity in 2025. "
    "The government announced an investment of Rs 5,000 crore for solar parks. "
    "India may become the world's largest solar producer by 2030. "
    "Solar capacity grew by approximately 40% during the year. "
    "The Ministry of New and Renewable Energy published the data on Tuesday."
)


# ---------------------------------------------------------------------------
# Grounded claims pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "claim",
    [
        "India installed 25 GW of solar capacity in 2025.",
        "The government announced an investment of Rs 5,000 crore for solar parks.",
        "India may become the world's largest solar producer by 2030.",
        "Solar capacity grew by approximately 40% during the year.",
        "The Ministry of New and Renewable Energy published the data on Tuesday.",
    ],
)
def test_grounded_claims_pass(claim):
    assert validate_claim(claim, ARTICLE) is True


# ---------------------------------------------------------------------------
# 10. Incorrect numerical values
# ---------------------------------------------------------------------------


def test_incorrect_number_is_rejected():
    assert validate_claim("India installed 35 GW of solar capacity in 2025.", ARTICLE) is False


def test_invented_date_is_rejected():
    assert validate_claim("India installed 25 GW of solar capacity in 2019.", ARTICLE) is False


def test_number_matches_with_different_formatting():
    assert validate_claim("The government announced Rs 5000 crore for solar parks.", ARTICLE)


# ---------------------------------------------------------------------------
# 11. Unsupported / fabricated claims
# ---------------------------------------------------------------------------


def test_fabricated_claim_is_rejected():
    assert validate_claim("India banned all coal plants in 2025.", ARTICLE) is False


def test_fabricated_entity_is_rejected():
    assert validate_claim(
        "China installed 25 GW of solar capacity in 2025.", ARTICLE
    ) is False


def test_unsupported_context_expansion_is_rejected():
    assert validate_claim(
        "Apple acquired a solar startup in California last year.", ARTICLE
    ) is False


def test_claim_with_unrelated_vocabulary_is_rejected():
    assert validate_claim(
        "The cricket team won the world cup final in Mumbai.", ARTICLE
    ) is False


# ---------------------------------------------------------------------------
# Hedge integrity: predictions must not become facts
# ---------------------------------------------------------------------------


def test_prediction_restated_as_fact_is_rejected():
    assert validate_claim(
        "India will become the world's largest solar producer by 2030.", ARTICLE
    ) is False


def test_prediction_preserved_passes():
    assert validate_claim(
        "India may become the world's largest solar producer by 2030.", ARTICLE
    ) is True


def test_announcement_about_future_plan_is_allowed():
    article = "The government plans to build 100 solar parks by 2027."
    assert validate_claim(
        "The government plans to build 100 solar parks by 2027.", article
    ) is True
    assert validate_claim(
        "The government announced a plan to build 100 solar parks by 2027.", article
    ) is True


def test_plan_stated_as_certain_outcome_is_rejected():
    article = "The government plans to build 100 solar parks by 2027."
    assert validate_claim("The government will build 100 solar parks by 2027.", article) is False


# ---------------------------------------------------------------------------
# Qualifier integrity: approximations stay approximate
# ---------------------------------------------------------------------------


def test_dropped_approximation_is_rejected():
    assert validate_claim("Solar capacity grew by 40% during the year.", ARTICLE) is False


def test_kept_approximation_passes():
    assert validate_claim(
        "Solar capacity grew by approximately 40% during the year.", ARTICLE
    ) is True


def test_exact_number_may_be_reported_where_article_is_exact():
    article = "India installed 25 GW of solar capacity in 2025."
    assert validate_claim("India installed 25 GW of solar capacity in 2025.", article)


# ---------------------------------------------------------------------------
# Structural validation
# ---------------------------------------------------------------------------


def test_empty_claim_is_rejected():
    assert validate_claim("", ARTICLE) is False
    assert validate_claim("   ", ARTICLE) is False


def test_empty_article_is_rejected():
    assert validate_claim("India installed 25 GW of solar capacity.", "") is False


def test_claim_longer_than_article_is_rejected():
    assert validate_claim(
        "India installed 25 GW of solar capacity in 2025 after a record auction "
        "season that drew bids from twelve developers who promised to build "
        "manufacturing plants in three states.",
        ARTICLE,
    ) is False


def test_validation_never_raises_on_garbage():
    assert validate_claim("!!!", ARTICLE) is False
    assert validate_claim("India installed 25 GW", None) is False  # type: ignore[arg-type]
    assert validate_claim(None, ARTICLE) is False  # type: ignore[arg-type]


def test_report_explains_rejection():
    report = check_claim("India installed 35 GW of solar capacity in 2025.", ARTICLE)
    assert report.ok is False
    assert any(reason.startswith("ungrounded_number") for reason in report.reasons)


def test_report_explains_hedge_rejection():
    report = check_claim(
        "India will become the world's largest solar producer by 2030.", ARTICLE
    )
    assert "dropped_speculation" in report.reasons


def test_report_explains_qualifier_rejection():
    report = check_claim("Solar capacity grew by 40% during the year.", ARTICLE)
    assert any(reason.startswith("dropped_approximation") for reason in report.reasons)


def test_semantic_check_is_not_similarity_only():
    """A high-overlap claim that flips a number must still be rejected."""
    claim = "India installed 25 GW of solar capacity in 2025."
    flipped = "India installed 52 GW of solar capacity in 2025."

    assert lexical_recall(claim, ARTICLE) > 0.8
    assert lexical_recall(flipped, ARTICLE) > 0.8
    assert validate_claim(claim, ARTICLE) is True
    assert validate_claim(flipped, ARTICLE) is False


def test_validation_uses_article_sentences_internally():
    report = check_claim("India installed 25 GW of solar capacity in 2025.", ARTICLE)
    assert report.ok is True
    assert report.reasons == []
