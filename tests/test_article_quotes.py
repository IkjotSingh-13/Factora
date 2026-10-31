"""Claim grounding: verbatim article quotes, time sensitivity, and frame stripping.

The plan's rule is that a claim which cannot be quoted from the article is not a claim
this system will carry forward. These tests pin that, and pin the parts that were added
to make it work over real text (line-wrapped paragraphs, curly quotes, attributions).
"""

from __future__ import annotations

import pytest

from app.pipeline.article_quotes import (
    GroundedClaim,
    assertion_of,
    detect_time_sensitive,
    ground_claims,
    to_display_claims,
    to_pipeline_claims,
)

ARTICLE = (
    "Acme Industries announced on Tuesday that it will open a new battery factory in Ludhiana,\n"
    "Punjab, by 2027. The company said the plant will create 4,500 jobs and cost $3 billion.\n"
    "Acme\u2019s chief executive, Riya Sharma, made the announcement at an industry event in Mumbai.\n"
    "Analysts said the move reflects rising demand for grid storage. Sharma previously worked\n"
    "at Tesla for nine years. The company\u2019s shares rose 6 percent in after-hours trading."
)


def claim(cid: str, text: str, entities=None, ctype: str = "factual") -> dict:
    return {"id": cid, "text": text, "entities": entities or [], "claim_type": ctype}


# ---------------------------------------------------------------------------
# article quotes
# ---------------------------------------------------------------------------
def test_grounding_returns_a_verbatim_sentence_from_the_article() -> None:
    grounded, audit = ground_claims(
        [claim("c1", "The plant will create 4,500 jobs and cost $3 billion.")], ARTICLE
    )
    assert audit["claims_kept"] == 1
    quote = grounded[0].article_quote
    assert quote and quote in ARTICLE            # present in the *original* text
    assert quote == "The company said the plant will create 4,500 jobs and cost $3 billion."


def test_grounding_survives_line_wraps_and_smart_quotes() -> None:
    """A claim split across a hard newline must still be quotable, verbatim."""
    grounded, audit = ground_claims(
        [claim("c1", "Sharma previously worked at Tesla for nine years.")], ARTICLE
    )
    assert audit["quotes_relocated_in_article"] == 1
    quote = grounded[0].article_quote
    assert quote and "\n" in quote               # the article's own line break is kept
    assert quote in ARTICLE
    assert "\u2019" not in quote                 # this sentence has no apostrophe at all


def test_claims_the_article_does_not_make_are_dropped_and_counted() -> None:
    grounded, audit = ground_claims(
        [
            claim("c1", "The plant will create 4,500 jobs and cost $3 billion."),
            claim("c2", "Battery factories are usually very expensive to build worldwide."),
        ],
        ARTICLE,
    )
    assert [c.id for c in grounded] == ["c1"]
    assert audit["claims_dropped_ungrounded"] == 1
    assert audit["dropped"][0]["id"] == "c2"


def test_if_nothing_can_be_grounded_one_claim_is_kept_and_said_so() -> None:
    """A blank screen reads as a bug; a kept-but-unquoted claim reads as an honest limit."""
    grounded, audit = ground_claims(
        [claim("c1", "The consortium finalised a hydrogen aircraft partnership with the agency.")],
        "Totally unrelated text about pasta shapes and their regional varieties in Italy.",
    )
    assert len(grounded) == 1
    assert grounded[0].article_quote is None
    assert grounded[0].quote_source == "none"
    assert any("WITHOUT a quote" in note for note in audit["notes"])


def test_empty_article_and_empty_claim_list_are_safe() -> None:
    grounded, audit = ground_claims([], ARTICLE)
    assert grounded == [] and audit["claims_kept"] == 0
    grounded, audit = ground_claims([claim("c1", "Any claim at all.")], "")
    assert grounded == []


# ---------------------------------------------------------------------------
# time sensitivity
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Acme will open a new factory in 2027.", True),
        ("The GDP grew 7 percent this year.", True),
        ("India is the world's largest milk producer.", True),
        ("The company reported revenue of 4.2 billion last quarter.", True),
        ("The Eiffel Tower was completed in 1889.", False),
        ("Water boils at 100 degrees Celsius at sea level.", False),
        ("A triangle has three sides.", False),
    ],
)
def test_time_sensitivity_detection(text: str, expected: bool) -> None:
    assert detect_time_sensitive(text)[0] is expected


def test_time_sensitivity_reason_is_reported() -> None:
    sensitive, reason = detect_time_sensitive("The population of Delhi is 32 million as of 2024.")
    assert sensitive and reason


def test_grounding_marks_claims_time_sensitive_for_the_freshness_term() -> None:
    grounded, audit = ground_claims([claim("c1", "Acme will open a new battery factory in Ludhiana by 2027.")], ARTICLE)
    assert grounded[0].time_sensitive is True
    assert audit["time_sensitive_claims"] == 1
    assert to_pipeline_claims(grounded)[0]["time_sensitive"] is True


# ---------------------------------------------------------------------------
# reporting-frame stripping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "A widely shared travel brief states that the Eiffel Tower is 330 metres tall.",
            "The Eiffel Tower is 330 metres tall.",
        ),
        (
            "The company said the plant will create 4,500 jobs and cost $3 billion.",
            "The plant will create 4,500 jobs and cost $3 billion.",
        ),
        ("The Eiffel Tower is 330 metres tall.", "The Eiffel Tower is 330 metres tall."),
    ],
)
def test_assertion_of_strips_only_the_frame(text: str, expected: str) -> None:
    assert assertion_of(text)[0] == expected


@pytest.mark.parametrize(
    "text",
    [
        # "that" here is a determiner: cutting after "repeats" would eat the noun
        "An image-post repeats that statement as though it were an established fact.",
        # negation is part of the assertion, not a frame
        "The company said nothing about the merger for several weeks.",
        # no reporting verb at all
        "The Eiffel Tower is 330 metres tall.",
    ],
)
def test_assertion_of_refuses_to_cut_where_it_would_mangle(text: str) -> None:
    assertion, removed = assertion_of(text)
    assert removed is None
    assert assertion == text


def test_nested_reporting_is_cut_to_the_innermost_checkable_clause() -> None:
    """"A cites B who say X" is verified by checking X, so that is the assertion.

    Not a mangle: the outer clause is the article's own framing, and the frame that was
    removed is recorded so a reviewer can see the cut.
    """
    assertion, removed = assertion_of("The report cites experts who say the bridge is unsafe at night.")
    assert assertion == "The bridge is unsafe at night."
    assert removed == "The report cites experts who say"


def test_pronoun_subject_is_resolved_from_entities_or_not_at_all() -> None:
    text = "Acme announced on Tuesday that it will open a new battery factory in Ludhiana."
    resolved, removed = assertion_of(text, entities=["Acme Industries"])
    assert removed and resolved.startswith("Acme Industries will open")
    # without an entity to substitute, the frame stays: "It will open..." retrieves nothing
    kept, removed = assertion_of(text)
    assert removed is None


def test_stripping_is_recorded_in_the_audit_and_displayed() -> None:
    grounded, audit = ground_claims(
        [claim("c1", "A widely shared travel brief states that the Eiffel Tower is 330 metres tall.")],
        "A widely shared travel brief states that the Eiffel Tower is 330 metres tall. "
        "The figure was repeated in three syndicated editions.",
    )
    assert audit["reporting_frames_stripped"] == 1
    assert audit["stripped"][0]["removed"].startswith("A widely shared travel brief")

    pipeline = to_pipeline_claims(grounded)[0]
    display = to_display_claims(grounded)[0]
    assert pipeline["text"] == "The Eiffel Tower is 330 metres tall."
    # both views exist: what was checked, and what the article actually said
    assert display["original_text"].startswith("A widely shared travel brief")
    assert display["article_quote"].startswith("A widely shared travel brief")
    assert display["reporting_frame_removed"]


def test_grounding_aligns_on_the_authored_text_not_the_stripped_one() -> None:
    """The article sentence contains the frame, so grounding must use the full claim."""
    article = "A widely shared travel brief states that the Eiffel Tower is 330 metres tall."
    grounded, audit = ground_claims(
        [claim("c1", "A widely shared travel brief states that the Eiffel Tower is 330 metres tall.")],
        article,
    )
    assert audit["claims_kept"] == 1
    assert grounded[0].article_quote == article
    assert grounded[0].align == pytest.approx(1.0)


def test_claim_objects_from_s1_are_accepted_not_just_dicts() -> None:
    from backend.schemas import Claim

    grounded, audit = ground_claims(
        [Claim(id="c1", text="The company's shares rose 6 percent in after-hours trading.", entities=["Acme"], claim_type="factual")],
        ARTICLE,
    )
    assert audit["claims_kept"] == 1
    assert grounded[0].article_quote.endswith("after-hours trading.")


def test_pipeline_claims_carry_every_contract_field() -> None:
    record = GroundedClaim(id="c1", text="A claim.", type="factual", entities=["X"], article_quote="A claim.", time_sensitive=True)
    payload = to_pipeline_claims([record])[0]
    assert payload == {
        "id": "c1",
        "text": "A claim.",
        "claim_type": "factual",
        "entities": ["X"],
        "article_quote": "A claim.",
        "time_sensitive": True,
    }
