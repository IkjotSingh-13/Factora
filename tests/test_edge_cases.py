"""Tests: provider failures, malformed LLM output, robustness edge cases."""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend import Claim, extract_claims, extract_claims_detailed
from backend.config import get_settings
from backend.errors import ClaimExtractionError, EmptyArticleError, LLMError, LLMOutputError
from backend.llm_provider import (
    FailingProvider,
    MockProvider,
    get_provider,
    parse_claims_payload,
)
from backend.schemas import ALLOWED_CLAIM_TYPES

ARTICLE = (
    "India installed 25 GW of solar capacity in 2025. "
    "The government announced an investment of Rs 5,000 crore for solar parks. "
    "India may become the world's largest solar producer by 2030."
)


# ---------------------------------------------------------------------------
# 16. LLM API failures
# ---------------------------------------------------------------------------


def test_llm_failure_falls_back_to_heuristics():
    detailed = extract_claims_detailed(ARTICLE, provider=FailingProvider("API down"))

    assert detailed.used_fallback is True
    assert detailed.claims, "fallback must still produce grounded claims"
    assert any("failed" in note for note in detailed.notes)
    for claim in detailed.claims:
        assert claim.claim_type in ALLOWED_CLAIM_TYPES


def test_llm_failure_raises_when_fallback_disabled():
    settings = replace(get_settings(), llm_fallback_to_heuristics=False)
    with pytest.raises(LLMError):
        extract_claims(ARTICLE, provider=FailingProvider("API down"), settings=settings)


def test_gemini_without_api_key_raises():
    settings = replace(get_settings(), llm_provider="gemini", gemini_api_key=None)
    with pytest.raises(LLMError):
        get_provider("gemini", settings=settings)


def test_invalid_provider_name_raises():
    with pytest.raises(LLMError):
        get_provider("not-a-provider")


def test_provider_factory_returns_configured_provider():
    assert get_provider("mock").name == "mock"
    assert get_provider("ollama", settings=replace(get_settings(), ollama_model="qwen2.5:7b")).name == "ollama"


# ---------------------------------------------------------------------------
# 17. Invalid LLM JSON output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "I'm sorry, I can't help with that.",
        "<html><body>502 Bad Gateway</body></html>",
        '{"claims": [{"text": "India installed 25 GW.',  # truncated JSON
        '{"claims": "not-a-list"}',
        "",
    ],
)
def test_invalid_llm_output_is_handled(payload):
    """Malformed model output must not crash the module: it falls back."""
    detailed = extract_claims_detailed(ARTICLE, provider=MockProvider(response_override=payload))

    assert detailed.used_fallback is True
    assert detailed.claims
    assert any("failed" in note or "no_claims" in note for note in detailed.notes)


def test_invalid_llm_output_raises_when_fallback_disabled():
    settings = replace(get_settings(), llm_fallback_to_heuristics=False)
    with pytest.raises(LLMOutputError):
        extract_claims(
            ARTICLE,
            provider=MockProvider(response_override="totally not json"),
            settings=settings,
        )


def test_empty_claim_list_is_respected():
    detailed = extract_claims_detailed(
        ARTICLE, provider=MockProvider(response_override='{"claims": []}')
    )
    # A model that returns nothing on a rich article triggers the fallback...
    assert detailed.used_fallback is True
    assert detailed.claims


def test_empty_claim_list_on_tiny_article_returns_nothing():
    detailed = extract_claims_detailed(
        "Rain fell.", provider=MockProvider(response_override='{"claims": []}')
    )
    assert detailed.claims == []
    assert detailed.used_fallback is False


# ---------------------------------------------------------------------------
# JSON parsing robustness
# ---------------------------------------------------------------------------


def test_parse_fenced_json():
    raw = """Here you go:
    ```json
    {"claims": [{"text": "India installed 25 GW.", "claim_type": "factual"}]}
    ```
    """
    claims = parse_claims_payload(raw)
    assert len(claims) == 1
    assert claims[0].text == "India installed 25 GW."


def test_parse_bare_array():
    claims = parse_claims_payload(
        '[{"text": "India installed 25 GW.", "claim_type": "Factual", "entities": ["India"]}]'
    )
    assert claims[0].claim_type == "factual"
    assert claims[0].entities == ["India"]


def test_parse_recovers_trailing_comma():
    claims = parse_claims_payload(
        '{"claims": [{"text": "India installed 25 GW.", "claim_type": "factual"},]}'
    )
    assert len(claims) == 1


def test_parse_string_items():
    claims = parse_claims_payload('{"claims": ["India installed 25 GW."]}')
    assert claims[0].text == "India installed 25 GW."
    assert claims[0].claim_type == "factual"


def test_parse_unknown_claim_type_defaults_to_non_verifiable():
    claims = parse_claims_payload(
        '[{"text": "Something happened.", "claim_type": "wild_guess"}]'
    )
    assert claims[0].claim_type == "non_verifiable"


def test_parse_missing_text_items_are_skipped():
    claims = parse_claims_payload('[{"claim_type": "factual"}, {"text": "  "}]')
    assert claims == []


def test_parse_json_span_ignores_prose_around_object():
    claims = parse_claims_payload(
        'Sure! {"claims": [{"text": "India installed 25 GW.", "claim_type": "factual"}]} Hope that helps.'
    )
    assert len(claims) == 1


# ---------------------------------------------------------------------------
# Claim model contract
# ---------------------------------------------------------------------------


def test_claim_model_forbids_extra_fields():
    with pytest.raises(Exception):
        Claim(
            id="claim_001",
            text="India installed 25 GW.",
            entities=["India"],
            claim_type="factual",
            confidence=0.9,  # must be rejected
        )


@pytest.mark.parametrize(
    "claim_type", ["factual", "prediction", "opinion", "non_verifiable"]
)
def test_claim_accepts_only_allowed_types(claim_type):
    assert Claim(
        id="claim_001", text="X happened.", entities=[], claim_type=claim_type
    ).claim_type == claim_type


@pytest.mark.parametrize("bad_type", ["Factual-ish", "claim", "", "TRUE"])
def test_claim_rejects_invalid_types(bad_type):
    with pytest.raises(Exception):
        Claim(id="claim_001", text="X happened.", entities=[], claim_type=bad_type)


def test_claim_entities_are_deduplicated():
    claim = Claim(
        id="claim_001",
        text="India installed 25 GW.",
        entities=["India", "india", "India"],
        claim_type="factual",
    )
    assert claim.entities == ["India"]


def test_returned_claims_serialise_to_four_fields():
    claims = extract_claims(ARTICLE)
    payload = [claim.model_dump() for claim in claims]
    assert all(set(item) == {"id", "text", "entities", "claim_type"} for item in payload)


# ---------------------------------------------------------------------------
# Text robustness
# ---------------------------------------------------------------------------


def test_unicode_currency_and_units_preserved():
    article = (
        "The government allocated \u20b95,000 crore for 3 solar parks in 2025. "
        "The parks will have a combined capacity of 750 MW. "
        "Solar tariffs fell to \u20b92.5 per unit in 2025."
    )
    claims = extract_claims(article)
    texts = " ".join(claim.text for claim in claims)
    assert "\u20b95,000 crore" in texts or "\u20b92.5" in texts
    assert "750 MW" in texts or "3 solar parks" in texts


def test_emoji_and_zero_width_characters():
    article = (
        "India installed 25 GW of solar capacity in 2025 \U0001f680\U0001f680. "
        "The government announced Rs 5,000 crore for solar parks\u200b."
    )
    claims = extract_claims(article)
    assert claims


def test_very_long_article_is_truncated_not_crashed():
    article = " ".join(
        f"India commissioned {index} solar parks in 2025."
        for index in range(1, 400)
    )
    detailed = extract_claims_detailed(article)
    assert len(detailed.claims) <= 6
    assert detailed.article_sentence_count <= get_settings().max_sentences_to_llm


def test_questions_are_not_claims():
    article = (
        "Will India reach its 2030 target? "
        "India installed 25 GW of solar capacity in 2025. "
        "Can the grid handle the load?"
    )
    claims = extract_claims(article)
    assert all(not claim.text.endswith("?") for claim in claims)
    assert any("25 GW" in claim.text for claim in claims)


def test_article_of_only_questions_returns_no_claims():
    detailed = extract_claims_detailed("Will India reach its target? Can it double capacity?")
    assert detailed.claims == []


def test_html_fragment_in_pasted_text_is_handled():
    article = (
        "<p>India installed 25 GW of solar capacity in 2025.</p> "
        "<p>The government announced Rs 5,000 crore for solar parks.</p>"
    )
    claims = extract_claims(article)
    assert claims


def test_non_string_input_raises():
    with pytest.raises(EmptyArticleError):
        extract_claims(12345)  # type: ignore[arg-type]


def test_claim_with_no_surviving_claims_raises():
    article = "The weather was pleasant on Tuesday morning."
    with pytest.raises(ClaimExtractionError):
        extract_claims(
            article,
            provider=MockProvider(
                response_override='{"claims": [{"text": "Mars colonised in 2031.", '
                '"claim_type": "factual"}]}'
            ),
        )


# ---------------------------------------------------------------------------
# Remote / local provider transports (mocked HTTP - no network, no API key)
# ---------------------------------------------------------------------------


class _FakeHTTPResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ("" if payload is None else "payload")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _gemini_ok(text: str) -> _FakeHTTPResponse:
    return _FakeHTTPResponse(200, {"candidates": [{"content": {"parts": [{"text": text}]}}]})


def test_gemini_provider_parses_response(monkeypatch):
    from backend.llm_provider import GeminiProvider

    captured = {}

    def fake_post(url, params=None, json=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return _gemini_ok(
            '{"claims": [{"text": "India installed 25 GW of solar capacity in 2025.", '
            '"claim_type": "factual", "entities": ["India"]}]}'
        )

    monkeypatch.setattr("backend.llm_provider.httpx.post", fake_post)
    settings = replace(get_settings(), llm_provider="gemini", gemini_api_key="test-key")
    provider = GeminiProvider(settings=settings)

    claims = provider.extract_raw_claims(["India installed 25 GW of solar capacity in 2025."])

    assert claims[0].text == "India installed 25 GW of solar capacity in 2025."
    assert "generateContent" in captured["url"]
    assert captured["params"]["key"] == "test-key"
    assert captured["json"]["generationConfig"]["responseMimeType"] == "application/json"
    assert captured["json"]["contents"][0]["role"] == "user"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_gemini_http_errors_raise_llm_error(monkeypatch, status):
    from backend.llm_provider import GeminiProvider

    monkeypatch.setattr(
        "backend.llm_provider.httpx.post",
        lambda *a, **k: _FakeHTTPResponse(status, {"error": "nope"}, text="nope"),
    )
    settings = replace(get_settings(), llm_provider="gemini", gemini_api_key="secret-key")
    provider = GeminiProvider(settings=settings)

    with pytest.raises(LLMError) as excinfo:
        provider.complete("sys", "user")
    assert str(status) in str(excinfo.value)
    assert "secret-key" not in str(excinfo.value)  # never leak the API key


def test_gemini_timeout_raises_llm_error(monkeypatch):
    import httpx as _httpx

    from backend.llm_provider import GeminiProvider

    def raise_timeout(*args, **kwargs):
        raise _httpx.ConnectTimeout("too slow")

    monkeypatch.setattr("backend.llm_provider.httpx.post", raise_timeout)
    settings = replace(get_settings(), llm_provider="gemini", gemini_api_key="k")
    with pytest.raises(LLMError, match="timed out"):
        GeminiProvider(settings=settings).complete("sys", "user")


def test_gemini_safety_block_raises_llm_error(monkeypatch):
    from backend.llm_provider import GeminiProvider

    monkeypatch.setattr(
        "backend.llm_provider.httpx.post",
        lambda *a, **k: _FakeHTTPResponse(200, {"promptFeedback": {"blockReason": "SAFETY"}}),
    )
    settings = replace(get_settings(), llm_provider="gemini", gemini_api_key="k")
    with pytest.raises(LLMError, match="blocked"):
        GeminiProvider(settings=settings).complete("sys", "user")


def test_gemini_empty_candidate_raises(monkeypatch):
    from backend.llm_provider import GeminiProvider

    monkeypatch.setattr(
        "backend.llm_provider.httpx.post",
        lambda *a, **k: _FakeHTTPResponse(200, {"candidates": []}),
    )
    settings = replace(get_settings(), llm_provider="gemini", gemini_api_key="k")
    with pytest.raises(LLMOutputError):
        GeminiProvider(settings=settings).complete("sys", "user")


def test_ollama_provider_uses_configured_local_model(monkeypatch):
    from backend.llm_provider import OllamaProvider

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeHTTPResponse(
            200,
            {
                "message": {
                    "content": '{"claims": [{"text": "India installed 25 GW of solar '
                    'capacity in 2025.", "claim_type": "factual"}]}'
                }
            },
        )

    monkeypatch.setattr("backend.llm_provider.httpx.post", fake_post)
    settings = replace(get_settings(), llm_provider="ollama", ollama_model="qwen2.5:7b")
    provider = OllamaProvider(settings=settings)

    claims = provider.extract_raw_claims(["India installed 25 GW of solar capacity in 2025."])

    assert claims[0].claim_type == "factual"
    assert captured["url"].endswith("/api/chat")
    assert captured["json"]["model"] == "qwen2.5:7b"
    assert captured["json"]["format"] == "json"
    assert captured["json"]["stream"] is False


def test_switching_provider_needs_no_pipeline_change(monkeypatch):
    """Same extraction pipeline, remote -> local -> mock, identical schema."""
    from backend.llm_provider import OllamaProvider, StaticProvider

    monkeypatch.setattr(
        "backend.llm_provider.httpx.post",
        lambda *a, **k: _FakeHTTPResponse(
            200,
            {
                "message": {
                    "content": '{"claims": [{"text": "India installed 25 GW of solar '
                    'capacity in 2025.", "claim_type": "factual"}]}'
                }
            },
        ),
    )
    settings = replace(get_settings(), llm_provider="ollama")
    payload = '{"claims": [{"text": "India installed 25 GW of solar capacity in 2025.", "claim_type": "factual"}]}'

    for provider in (
        OllamaProvider(settings=settings),
        StaticProvider(payload),
        MockProvider(),
    ):
        claims = extract_claims(ARTICLE, provider=provider)
        assert claims, provider.name
        assert all(
            set(claim.model_dump()) == {"id", "text", "entities", "claim_type"}
            for claim in claims
        )
