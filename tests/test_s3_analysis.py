"""S3 stance analysis: the guards this stage exists to provide.

These tests are the reason S3 is safe to demo. Every one of them asserts that the
analyzer *refuses* to produce something — a stance it cannot ground, a quote it cannot
locate, a claim about a source it never fetched.
"""

from __future__ import annotations

import copy

import pytest

from s3_analysis import (
    S3Config,
    align_sentence,
    analyze,
    analyze_bundles,
    analyze_claim_with_llm,
    analyze_item,
    content_tokens,
    normalize_for_match,
    region_for,
    verify_quote,
    weighted_values,
)
from s2_service import apply_analysis

LEXICON = {"mode": "lexicon"}


def evidence_item(
    *,
    item_id: str = "ev_1",
    claim_id: str = "claim_001",
    full_text: str = "",
    snippet: str = "",
    url: str = "https://source.example/a",
) -> dict:
    return {
        "id": item_id,
        "claim_id": claim_id,
        "url": url,
        "domain": "source.example",
        "title": "Source A",
        "snippet": snippet or full_text,
        "full_text": full_text,
        "published_at": "2025-05-01",
        "source_kind": "encyclopedia",
        "relevance": 0.9,
        "credibility": 0.8,
        "stance": "NEUTRAL",
        "quote": "",
        "quote_verified": False,
        "rationale": "",
        "context_score": 0.7,
        "context_gap": False,
    }


def bundle(claim_text: str, items: list[dict], claim_id: str = "claim_001") -> dict:
    return {
        "claim": {
            "id": claim_id,
            "text": claim_text,
            "type": "factual",
            "article_quote": None,
            "time_sensitive": False,
        },
        "evidence": items,
    }


# ---------------------------------------------------------------------------
# text plumbing
# ---------------------------------------------------------------------------
def test_normalize_folds_case_whitespace_and_typographic_quotes() -> None:
    assert normalize_for_match("The  TOWER\u2019s   height") == "the tower's height"
    assert normalize_for_match("300\u2013330 m") == "300-330 m"


def test_verify_quote_is_containment_not_similarity() -> None:
    region = "The tower is 330 metres tall, antennas included."
    assert verify_quote("The tower is 330 metres tall", region)
    # one substituted word is not a quote from this source
    assert not verify_quote("The tower is 335 metres tall", region)
    assert not verify_quote("", region)
    assert not verify_quote("anything", "")


def test_weighted_values_canonicalises_units_and_refuses_unknown_ones() -> None:
    assert weighted_values("330 metres (1,083 ft)") == pytest.approx({"m": [330.0, 330.0984]}, rel=1e-4)
    assert weighted_values("$3 billion")[ "usd"] == [3e9]
    # an unrecognised unit is dropped rather than guessed at
    assert weighted_values("3.7 furlongs") == {}


def test_content_tokens_fold_plurals_and_keep_function_words_out() -> None:
    assert content_tokens("The strikes strike on policy issues") == {"strike", "policy", "issue"}
    assert "does" not in content_tokens("does not compute")


# ---------------------------------------------------------------------------
# stance decisions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "claim, source, expected",
    [
        (
            "The Eiffel Tower is 330 metres tall.",
            "It is 330 metres tall including antennas, and was the tallest structure in the world until 1930.",
            "SUPPORTS",
        ),
        (
            "The Great Wall of China is visible from space with the naked eye.",
            "Contrary to a common myth, the Great Wall is not visible from space with the naked eye.",
            "REFUTES",
        ),
        (
            "Vitamin C prevents the common cold in adults.",
            "Routine supplementation with vitamin C does not reduce the incidence of the common cold in the general population.",
            "REFUTES",
        ),
        (
            "Acme opened a factory in Delhi on Monday.",
            "Acme announced record quarterly revenue and a new headquarters in Mumbai.",
            "NEUTRAL",
        ),
    ],
)
def test_lexical_analyzer_labels_agreement_and_refutation(claim: str, source: str, expected: str) -> None:
    result = analyze_item(claim, evidence_item(full_text=source), config=LEXICON)
    assert result["stance"] == expected


def test_negation_outside_the_verified_sentence_cannot_flip_a_stance() -> None:
    """A page that says "no" about something else must not become a refutation.

    This is why cues are read per sentence and not per document: a 6 000-character page
    contains "not" somewhere, always.
    """
    source = (
        "The Eiffel Tower is 330 metres tall including antennas. "
        "The visitor survey was not published, and ticket prices are not refundable."
    )
    result = analyze_item("The Eiffel Tower is 330 metres tall.", evidence_item(full_text=source), config=LEXICON)
    assert result["stance"] == "SUPPORTS"
    assert result["quote"].startswith("The Eiffel Tower is 330 metres tall")


def test_negation_scope_reaching_a_verification_predicate_refutes() -> None:
    """"No astronaut has confirmed it" denies the claim even without the word "not"."""
    source = "No astronaut has confirmed the wall as visible to the naked eye from orbit."
    result = analyze_item(
        "The Great Wall of China is visible from space with the naked eye.",
        evidence_item(full_text=source),
        config=LEXICON,
    )
    assert result["stance"] == "REFUTES"


def test_mention_frame_without_a_verdict_is_neutral() -> None:
    """A sentence that only *quotes* the claim is background, not support."""
    source = "Whether the Great Wall of China is visible from space by naked eye has been debated for years."
    result = analyze_item(
        "The Great Wall of China is visible from space with the naked eye.",
        evidence_item(full_text=source),
        config=LEXICON,
    )
    assert result["stance"] == "NEUTRAL"
    assert result["quote_verified"] is False
    assert "mentions" in result["rationale"]


def test_polarity_mismatch_is_refutation_not_support() -> None:
    """"Lightning never strikes twice" vs "it strikes repeatedly" is a contradiction."""
    source = "Lightning can and does strike the same place repeatedly."
    result = analyze_item(
        "Lightning never strikes the same place twice.",
        evidence_item(full_text=source),
        config=LEXICON,
    )
    assert result["stance"] == "REFUTES"
    assert "polarity" in result["rationale"]


def test_matching_number_supports_and_conflicting_number_refutes() -> None:
    assert analyze_item(
        "The tower is 330 metres tall.",
        evidence_item(full_text="The tower stands 330 metres tall to its tip."),
        config=LEXICON,
    )["stance"] == "SUPPORTS"
    refute = analyze_item(
        "Company revenue grew 45 percent in 2024.",
        evidence_item(full_text="Company revenue grew 12 percent in 2024."),
        config=LEXICON,
    )
    assert refute["stance"] == "REFUTES"
    assert "different value" in refute["rationale"]


def test_confidence_below_threshold_is_neutral_even_with_a_bigram_match() -> None:
    """The threshold is the point: a thin overlap yields no stance, not a weak one."""
    result = analyze_item(
        "The council approved a 12 million rupee bridge over the river in March.",
        evidence_item(full_text="The river flooded the bridge area."),
        config=LEXICON,
    )
    assert result["stance"] == "NEUTRAL"
    assert result["confidence"] < S3Config.resolve(LEXICON).min_confidence


# ---------------------------------------------------------------------------
# the quote guard
# ---------------------------------------------------------------------------
def test_quote_must_survive_verification_and_is_recorded_verbatim() -> None:
    source = "The tower is 330 metres tall, antennas included, per the 2022 survey."
    result = analyze_item("The tower is 330 metres tall.", evidence_item(full_text=source), config=LEXICON)
    assert result["quote_verified"] is True
    assert result["quote"] in source  # display text is the source's own, not rewritten


def test_region_follows_whatever_was_actually_retrieved() -> None:
    """Snippet-only evidence is checked against the snippet — and says so."""
    item = evidence_item(snippet="The tower is 330 metres tall.", full_text="")
    region, scope = region_for(item)
    assert region == "The tower is 330 metres tall."
    assert scope == "snippet"

    result = analyze_item("The tower is 330 metres tall.", item, config=LEXICON)
    assert result["quote_scope"] == "snippet"

    both = evidence_item(snippet="ignored when page text exists", full_text="The tower is 330 metres tall.")
    region, scope = region_for(both)
    assert (scope, region) == ("full_text", "The tower is 330 metres tall.")


def test_analyzer_never_emits_a_quote_absent_from_the_region() -> None:
    """Even if the selection step were wrong, the guard must convert it to a drop."""
    claim = "The tower is 330 metres tall."
    item = evidence_item(full_text="The tower is 330 metres tall.")

    def broken_candidate(*_args, **_kwargs):
        return type(
            "A",
            (),
            {
                "sentence": "The tower is 335 metres tall, roughly.",
                "align": 1.0,
                "confidence": 1.0,
                "missing": frozenset(),
                "negation": (),
                "support": (),
                "number_conflict": False,
                "number_match": False,
                "mention_frame": False,
                "polarity_mismatch": False,
            },
        )()

    import s3_analysis as module

    original = module.align_sentence
    module.align_sentence = broken_candidate
    try:
        result = analyze_item(claim, item, config=LEXICON)
    finally:
        module.align_sentence = original

    assert result["stance"] == "NEUTRAL"
    assert result["quote"] == ""
    assert result["quote_verified"] is False
    assert result["dropped_quote"] is True


def test_dropped_quotes_are_counted_for_the_audit() -> None:
    """The count is a deliverable: the report must say what was thrown away."""
    claim = "The tower is 330 metres tall."
    items = [evidence_item(item_id=f"ev_{index}", full_text="The tower is 330 metres tall.") for index in range(3)]
    result = analyze_bundles([claim], [bundle(claim, items)], config=LEXICON)
    assert result.audit["evidence_analyzed"] == 3
    assert result.audit["quote_dropped_count"] == 0

    import s3_analysis as module

    original = module.verify_quote
    module.verify_quote = lambda *_args, **_kwargs: False
    try:
        dropped = module.analyze_bundles([claim], [bundle(claim, items)], config=module.S3Config.resolve(LEXICON))
    finally:
        module.verify_quote = original
    assert dropped.audit["quote_dropped_count"] == 3


# ---------------------------------------------------------------------------
# bundle level + the S2 seam
# ---------------------------------------------------------------------------
def test_analyze_signature_matches_the_orchestrator_seam() -> None:
    """`analyze(claims, bundles)` is what run_verified_claim_pipeline calls."""
    claim = "The Eiffel Tower is 330 metres tall."
    source = "The tower is 330 metres tall including its antennas."
    analyses = analyze([{"id": "claim_001", "text": claim}], [bundle(claim, [evidence_item(full_text=source)])], config=LEXICON)
    assert len(analyses) == 1
    assert set(analyses[0]) >= {"evidence_id", "stance", "quote", "quote_verified", "rationale", "context_score", "context_gap"}

    merged = apply_analysis([bundle(claim, [evidence_item(full_text=source)])], analyses)
    item = merged[0]["evidence"][0]
    assert item["stance"] == "SUPPORTS"
    assert item["quote_verified"] is True
    assert item["quote_scope"] == "full_text"


def test_analysis_matching_no_evidence_is_ignored_not_invented() -> None:
    """An analysis for an unknown evidence id must not create an evidence item."""
    claim = "Some claim about a thing."
    bundles = [bundle(claim, [])]
    merged = apply_analysis(
        bundles,
        [{"evidence_id": "ev_nonexistent", "stance": "SUPPORTS", "quote": "x", "quote_verified": True}],
    )
    assert merged[0]["evidence"] == []


def test_claim_order_and_missing_claims_do_not_break_alignment() -> None:
    """Bundles are matched by claim id first, position second."""
    first = "The Eiffel Tower is 330 metres tall."
    second = "The Great Wall is not visible from space."
    bundles = [
        bundle(second, [evidence_item(item_id="ev_b", claim_id="claim_002", full_text="The Great Wall is not visible from space with the naked eye.")], claim_id="claim_002"),
        bundle(first, [evidence_item(item_id="ev_a", claim_id="claim_001", full_text="The tower is 330 metres tall including antennas.")], claim_id="claim_001"),
    ]
    analyses = analyze_bundles(
        [{"id": "claim_001", "text": first}, {"id": "claim_002", "text": second}],
        bundles,
        config=LEXICON,
    ).analyses
    by_id = {a["evidence_id"]: a for a in analyses}
    assert by_id["ev_a"]["stance"] == "SUPPORTS"
    assert by_id["ev_b"]["stance"] == "REFUTES"


def test_empty_and_broken_bundles_fail_soft() -> None:
    result = analyze_bundles([{"id": "c", "text": "A claim."}], [{"claim": {"id": "c"}, "evidence": []}], config=LEXICON)
    assert result.analyses == []
    assert result.audit["evidence_analyzed"] == 0

    # evidence with no text at all still returns a shape, never an exception
    result = analyze_bundles(
        [{"id": "c", "text": "A claim."}],
        [{"claim": {"id": "c", "text": "A claim."}, "evidence": [evidence_item(full_text="", snippet="")]}],
        config=LEXICON,
    )
    assert result.analyses[0]["stance"] == "NEUTRAL"
    assert result.analyses[0]["quote_verified"] is False


def test_max_evidence_per_claim_bounds_the_work() -> None:
    claim = "The tower is 330 metres tall."
    items = [evidence_item(item_id=f"ev_{i}", full_text="The tower is 330 metres tall.") for i in range(9)]
    result = analyze_bundles([claim], [bundle(claim, items)], config={"mode": "lexicon", "max_evidence_per_claim": 4})
    assert result.audit["evidence_analyzed"] == 4


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------
class FakeProvider:
    """Stands in for Gemini/Ollama: returns a canned string, records the prompt."""

    name = "fake"
    is_mock = False

    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return self.payload

    def is_available(self) -> tuple[bool, str]:
        return True, "fake"


def test_llm_quote_is_accepted_only_after_local_verification() -> None:
    source = "The tower is 330 metres tall including its antennas."
    claim = "The Eiffel Tower is 330 metres tall."
    provider = FakeProvider(
        '{"analyses": [{"evidence_id": "ev_1", "stance": "SUPPORTS",'
        ' "quote": "The tower is 330 metres tall including its antennas.",'
        ' "rationale": "States the height.", "context_score": 0.9, "context_gap": false}]}'
    )
    results = analyze_claim_with_llm(claim, "claim_001", [evidence_item(full_text=source)], provider=provider, config=S3Config.resolve())
    assert results[0]["stance"] == "SUPPORTS"
    assert results[0]["quote_verified"] is True
    assert results[0]["analyzer"] == "llm"
    # the prompt must carry the real evidence text, or "verified" would mean nothing
    assert source in provider.prompts[0][1]


def test_hallucinated_llm_quote_falls_back_and_is_reported() -> None:
    source = "The tower is 330 metres tall including its antennas."
    claim = "The Eiffel Tower is 330 metres tall."
    provider = FakeProvider(
        '{"analyses": [{"evidence_id": "ev_1", "stance": "REFUTES",'
        ' "quote": "The tower was demolished in 1950.",'
        ' "rationale": "Invented.", "context_score": 0.9, "context_gap": false}]}'
    )
    results = analyze_claim_with_llm(claim, "claim_001", [evidence_item(full_text=source)], provider=provider, config=S3Config.resolve())
    only = results[0]
    assert only["stance"] != "REFUTES"          # the invented refutation does not survive
    assert only["dropped_quote"] is True
    assert "could not be located" in only["rationale"]
    assert only["analyzer"].startswith("lexical")
    # Whatever survives must be a real substring of the source, or nothing was verified.
    if only["quote_verified"]:
        assert only["quote"] in source
    assert only["rationale"].count("The tower") <= 2


def test_unparseable_llm_output_degrades_to_the_lexical_analyser() -> None:
    claim = "The Eiffel Tower is 330 metres tall."
    source = "The tower is 330 metres tall including its antennas."
    result = analyze_bundles(
        [{"id": "claim_001", "text": claim}],
        [bundle(claim, [evidence_item(full_text=source)])],
        provider=FakeProvider("I am not JSON at all"),
        config={"mode": "auto"},
    )
    assert result.analyses[0]["stance"] == "SUPPORTS"   # lexical analysis still ran
    assert result.audit["analyzer"] == "lexical"


def test_provider_that_raises_does_not_break_the_run() -> None:
    class Boom(FakeProvider):
        def complete(self, *_args):
            raise RuntimeError("429 rate limited")

    claim = "The Eiffel Tower is 330 metres tall."
    source = "The tower is 330 metres tall including its antennas."
    result = analyze_bundles(
        [{"id": "claim_001", "text": claim}],
        [bundle(claim, [evidence_item(full_text=source)])],
        provider=Boom("{}"),
        config={"mode": "auto"},
    )
    assert result.analyses[0]["stance"] == "SUPPORTS"
    assert any("LLM analysis failed" in note for note in result.audit["notes"])


def test_config_resolution_accepts_dict_instance_and_overrides() -> None:
    assert S3Config.resolve({"mode": "lexicon"}).mode == "lexicon"
    assert S3Config.resolve(S3Config(min_align=0.9), min_align=None).min_align == 0.9
    assert S3Config.resolve(None, support_align=0.7).support_align == 0.7
    assert S3Config.resolve({"nonsense": 1}).mode == S3Config().mode


def test_alignment_is_reusable_by_the_article_quote_guard() -> None:
    """article_quotes imports align_sentence; pin the contract so it cannot drift."""
    alignment = align_sentence(
        "The tower is 330 metres tall.",
        "The tower is 330 metres tall, per the survey.",
        content_tokens("The tower is 330 metres tall."),
    )
    assert alignment.align > 0.75
    assert isinstance(alignment.missing, frozenset)
    assert copy.copy(alignment).sentence.startswith("The tower")
