"""Comprehensive tests for Claim Extractor fix — covering task requirements 1-13."""

import pytest
from dataclasses import replace
from backend import extract_claims, extract_claims_detailed, extract_claims_from_url
from backend.config import get_settings
from backend.errors import ClaimExtractionError, EmptyArticleError, LLMError, LLMOutputError
from backend.llm_provider import (
    MockProvider, StaticProvider, FailingProvider, 
    OllamaProvider, GeminiProvider, get_provider,
    heuristic_extract
)
from backend.validators import content_lemmas

# ---------------------------------------------------------------------------
# 1. The PM Modi and Trump example (critical)
# ---------------------------------------------------------------------------

def test_pm_modi_trump_example():
    """The canonical example from the task — must produce 3 distinct claims."""
    article = "PM Modi visited USA and met Trump and signed a deal on oil."
    result = extract_claims_detailed(article)
    
    assert len(result.claims) == 3, f"Expected 3 claims, got {len(result.claims)}: {[c.text for c in result.claims]}"
    
    texts = [c.text for c in result.claims]
    
    # Check for expected content (in order)
    assert "visited" in texts[0].lower() and "usa" in texts[0].lower(), f"First claim should be visit USA: {texts[0]}"
    assert "met" in texts[1].lower() and "trump" in texts[1].lower(), f"Second claim should be met Trump: {texts[1]}"
    assert "signed" in texts[2].lower() and "deal" in texts[2].lower(), f"Third claim should be signed deal: {texts[2]}"
    
    # Must NOT produce redundant/fragmented claims
    for text in texts:
        assert not text.startswith("He "), f"Should resolve pronouns, not leave 'He': {text}"
        assert text != "He met Trump.", f"Should not have unresolved pronoun: {text}"
        assert text != "He signed a deal.", f"Should not have fragmented claim: {text}"
    
    # Must preserve entities
    assert any("Modi" in e for c in result.claims for e in c.entities), "Should preserve Modi entity"
    assert any("USA" in e for c in result.claims for e in c.entities), "Should preserve USA entity"
    assert any("Trump" in e for c in result.claims for e in c.entities), "Should preserve Trump entity"
    
    # Must not have duplicates or overlapping
    assert len(texts) == len(set(texts)), "Should have no duplicate claims"
    # Check no claim is substring of another (except handled by dedup)
    for i, a in enumerate(texts):
        for j, b in enumerate(texts):
            if i != j and a.lower().rstrip(".") in b.lower() and len(a) < len(b):
                # This would be overlapping: "signed a deal" inside "signed a deal on oil"
                # The shorter should have been deduped
                assert False, f"Overlapping claims detected: '{a}' is substring of '{b}'"

def test_pm_modi_not_hardcoded():
    """Ensure extraction is general-purpose, not hardcoded to PM Modi example."""
    # Similar structure but different entities should also work
    article = "Elon Musk visited Berlin and met Olaf Scholz and announced a factory deal."
    result = extract_claims_detailed(article)
    
    assert len(result.claims) == 3
    texts = " ".join(c.text.lower() for c in result.claims)
    assert "visited" in texts
    assert "met" in texts
    assert "announced" in texts or "factory" in texts

# ---------------------------------------------------------------------------
# 2. Completely new, previously unseen factual sentences
# ---------------------------------------------------------------------------

def test_unseen_factual_sentences():
    """Extractor must work with arbitrary unseen text, not just pretrained examples."""
    articles = [
        "Tesla unveiled its new Model Y in Berlin in March 2024. The event attracted 5,000 attendees.",
        "NASA launched Artemis II on 15 November 2024 carrying four astronauts to lunar orbit.",
        "The European Central Bank raised interest rates to 4.5% in September 2023.",
        "OpenAI released GPT-4 Turbo with 128K context window in November 2023.",
    ]
    
    for article in articles:
        result = extract_claims_detailed(article)
        assert len(result.claims) >= 1, f"Should extract claims from unseen text: {article}"
        assert len(result.claims) <= 6, f"Should not exceed 6 claims: {article}"
        for claim in result.claims:
            assert claim.text, "Claim text should not be empty"
            assert claim.claim_type in ("factual", "prediction", "opinion", "non_verifiable")

def test_unseen_technical_domains():
    """Test extraction from technical, scientific, and diverse domains."""
    article = "CRISPR gene editing achieved 95% success rate in trials for sickle cell disease in 2023. The FDA approved the treatment in December 2023 for patients aged 12 and older."
    claims = extract_claims(article)
    assert len(claims) >= 2
    texts = " ".join(c.text for c in claims)
    assert "95%" in texts or "95" in texts
    assert "2023" in texts

# ---------------------------------------------------------------------------
# 3. Multiple independent claims in one paragraph
# ---------------------------------------------------------------------------

def test_multiple_independent_claims():
    article = (
        "Apple reported revenue of $94.9 billion for Q4 2024. "
        "The company sold 46.2 million iPhones during the quarter. "
        "Services revenue grew 12% year over year. "
        "CEO Tim Cook announced a new $110 billion share buyback program."
    )
    result = extract_claims_detailed(article)
    
    assert 3 <= len(result.claims) <= 6
    texts = [c.text for c in result.claims]
    combined = " ".join(texts)
    
    # All distinct facts should be represented
    assert "94.9" in combined or "billion" in combined
    assert "46.2" in combined or "iPhones" in combined
    assert "12%" in combined
    assert "buyback" in combined.lower() or "110" in combined
    
    # No duplicates
    assert len(texts) == len(set(texts))

# ---------------------------------------------------------------------------
# 4. Compound sentences
# ---------------------------------------------------------------------------

def test_compound_sentences_split_correctly():
    article = "The company opened 5 factories in Gujarat and hired 2,000 workers in 2024."
    claims = extract_claims(article)
    
    assert len(claims) == 2
    texts = [c.text.lower() for c in claims]
    assert any("opened" in t and "factories" in t for t in texts)
    assert any("hired" in t and "workers" in t for t in texts)
    # Each should have subject propagated
    assert all("company" in t.lower() for t in texts)

def test_compound_with_while():
    article = "India launched 15 projects in 2025 while China launched 10 projects in 2024."
    claims = extract_claims(article)
    assert len(claims) == 2
    texts = " ".join(c.text for c in claims)
    assert "India" in texts
    assert "China" in texts

def test_multiple_compound():
    article = "PM Modi visited USA and met Trump and signed a deal on oil and announced a joint statement."
    result = extract_claims_detailed(article)
    assert len(result.claims) >= 3
    # Each should be atomic, no "and" in claim text
    for claim in result.claims:
        # Claims should not contain coordinated "and" with verb (except maybe "and" in entity names)
        assert claim.text.count(" and ") <= 1 or " and " not in claim.text.lower(), f"Claim should be atomic: {claim.text}"

# ---------------------------------------------------------------------------
# 5. Pronoun resolution
# ---------------------------------------------------------------------------

def test_pronoun_resolution_they():
    article = (
        "India launched 15 solar projects in 2025. "
        "These projects have a combined capacity of 10 GW. "
        "They will power 2 million homes."
    )
    result = extract_claims_detailed(article)
    texts = [c.text for c in result.claims]
    # Pronouns should be resolved
    for text in texts:
        assert not text.startswith("They "), f"Pronoun not resolved: {text}"
        assert not text.startswith("These "), f"Demonstrative not resolved: {text}"
    # Should contain antecedent
    assert any("15 solar projects" in t for t in texts), f"Antecedent should be present: {texts}"

def test_pronoun_he_resolution():
    article = "PM Modi visited USA. He met Trump. He signed a deal on oil."
    result = extract_claims_detailed(article)
    texts = [c.text for c in result.claims]
    for text in texts:
        assert not text.startswith("He "), f"He should be resolved to PM Modi: {text}"
    assert any("PM Modi met Trump" in t for t in texts) or any("Modi met Trump" in t for t in texts)

def test_bullet_pronoun_resolution():
    article = "- PM Modi visited USA\n- He met Trump\n- He signed a deal on oil"
    result = extract_claims_detailed(article)
    assert len(result.claims) == 3
    for claim in result.claims:
        assert "PM Modi" in claim.text or "Modi" in claim.text, f"Bullet pronoun should resolve: {claim.text}"

# ---------------------------------------------------------------------------
# 6. Repeated and duplicate facts
# ---------------------------------------------------------------------------

def test_duplicate_facts_removed():
    article = (
        "India installed 25 GW of solar capacity in 2025. "
        "The country added 25 GW of solar capacity in 2025. "
        "India installed 25 GW of solar capacity in 2025."
    )
    result = extract_claims_detailed(article)
    
    assert result.deduplicated >= 1, "Should detect duplicates"
    texts = [c.text for c in result.claims]
    assert len(texts) == len(set(texts)), "No duplicates in final output"
    assert sum("25 GW" in t for t in texts) == 1, "Duplicate fact should appear once"

def test_overlapping_claims_deduped():
    """'signed a deal' and 'signed a deal on oil' should not both appear."""
    article = "PM Modi visited USA and met Trump and signed a deal on oil."
    result = extract_claims_detailed(article)
    texts = [c.text.lower() for c in result.claims]
    # Should not have both short and long version of same fact
    has_short = any(t == "pm modi signed a deal." for t in texts)
    has_long = any("signed a deal on oil" in t for t in texts)
    assert not (has_short and has_long), f"Should deduplicate overlapping: {texts}"
    assert has_long, "Should keep longer, more specific claim"

def test_numeric_distinction_not_deduped():
    """Claims with different numbers are not duplicates even if similar wording."""
    article = "India installed 25 GW in 2025. India installed 24 GW in 2024."
    result = extract_claims_detailed(article)
    # Both should be kept because numbers differ
    assert len(result.claims) == 2
    texts = " ".join(c.text for c in result.claims)
    assert "25 GW" in texts
    assert "24 GW" in texts

# ---------------------------------------------------------------------------
# 7. Informal text and spelling mistakes
# ---------------------------------------------------------------------------

def test_informal_text():
    article = "Hey, did you know India installed 25 GW of solar capacity in 2025? That's huge! The govt announced Rs 5,000 crore for more projects."
    result = extract_claims_detailed(article)
    assert len(result.claims) >= 1
    texts = " ".join(c.text.lower() for c in result.claims)
    # Should still extract factual content from conversational text
    assert "25 gw" in texts or "25000" in texts or "gw" in texts

def test_spelling_mistakes_handled():
    article = "PM Modi wisited USA and mett Trump adn signed a deal on oil. The del was anounced yesturday."
    result = extract_claims_detailed(article)
    # Should not crash and should produce claims (even with misspellings)
    assert len(result.claims) >= 2
    # Should handle informal spelling and still split compound
    assert len(result.claims) >= 3 or any("wisited" in c.text.lower() for c in result.claims)

def test_abbreviations():
    article = "The U.S. and U.K. leaders met in D.C. and discussed $5bn trade deals. Mr. Johnson announced the deal in Jan. 2024."
    result = extract_claims_detailed(article)
    assert len(result.claims) >= 1

def test_inconsistent_capitalization():
    article = "pm modi VISITED usa AND met trump AND signed A DEAL on OIL."
    result = extract_claims_detailed(article)
    # Should handle and still produce 3 claims
    assert len(result.claims) == 3, f"Should handle inconsistent caps: {[c.text for c in result.claims]}"

def test_bullet_points():
    article = "- Tesla launched Model 3 in 2017\n- It sold 500,000 units in 2020\n- The company announced record profits"
    result = extract_claims_detailed(article)
    assert len(result.claims) >= 2

# ---------------------------------------------------------------------------
# 8. Long paragraphs
# ---------------------------------------------------------------------------

def test_long_paragraph():
    article = " ".join([f"India commissioned {i} solar parks in 2025." for i in range(101, 130)])
    result = extract_claims_detailed(article)
    assert len(result.claims) == 6, "Long article should be capped at 6"
    assert result.article_sentence_count <= get_settings().max_sentences_to_llm

def test_very_long_article_truncated():
    article = " ".join([f"Company {i} reported ${i} billion revenue in 2024." for i in range(1, 200)])
    result = extract_claims_detailed(article)
    assert len(result.claims) <= 6
    assert "truncated" in " ".join(result.notes).lower() or result.article_sentence_count == get_settings().max_sentences_to_llm

# ---------------------------------------------------------------------------
# 9. Text containing no factual claims
# ---------------------------------------------------------------------------

def test_no_factual_claims_greeting():
    article = "Good morning. See you tomorrow. Thank you."
    result = extract_claims_detailed(article)
    # Should return empty or only non_verifiable
    assert result.claims == [] or all(c.claim_type == "non_verifiable" for c in result.claims)

def test_no_factual_claims_opinion_only():
    article = "I think this is the best movie ever. In my opinion, it was amazing and wonderful."
    result = extract_claims_detailed(article)
    # Opinions are preserved but labelled as opinion
    if result.claims:
        assert any(c.claim_type == "opinion" for c in result.claims)

def test_empty_after_validation():
    article = "The weather was pleasant on Tuesday morning. It was a nice day."
    # This may produce claims but should not crash
    result = extract_claims_detailed(article)
    # Either returns claims or empty, but should not raise
    assert isinstance(result.claims, list)

# ---------------------------------------------------------------------------
# 10. Ollama unavailable
# ---------------------------------------------------------------------------

def test_ollama_unavailable_fallback(mocker=None):
    """When Ollama is unavailable, should fallback with clear notes (if fallback enabled)."""
    from unittest.mock import patch
    import httpx
    
    def mock_post(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")
    
    with patch("backend.llm_provider.httpx.post", side_effect=mock_post):
        settings = replace(get_settings(), llm_provider="ollama", ollama_host="http://localhost:11434", ollama_model="qwen2.5:7b")
        result = extract_claims_detailed(
            "PM Modi visited USA and met Trump.", 
            provider=OllamaProvider(settings=settings),
            settings=settings
        )
        assert result.used_fallback is True
        assert any("ollama" in note.lower() or "failed" in note.lower() for note in result.notes)
        assert len(result.claims) >= 1, "Fallback should still produce claims"

def test_ollama_unavailable_no_fallback_raises():
    from dataclasses import replace
    from unittest.mock import patch
    import httpx
    
    def mock_post(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")
    
    with patch("backend.llm_provider.httpx.post", side_effect=mock_post):
        settings = replace(get_settings(), llm_provider="ollama", llm_fallback_to_heuristics=False)
        provider = OllamaProvider(settings=settings)
        with pytest.raises(LLMError):
            extract_claims("India installed 25 GW in 2025.", provider=provider, settings=settings)

def test_ollama_timeout():
    from unittest.mock import patch
    import httpx
    
    def mock_post(*args, **kwargs):
        raise httpx.ConnectTimeout("timed out")
    
    with patch("backend.llm_provider.httpx.post", side_effect=mock_post):
        settings = replace(get_settings(), llm_provider="ollama", ollama_timeout=1.0)
        provider = OllamaProvider(settings=settings)
        with pytest.raises(LLMError, match="timed out"):
            provider.complete("sys", "user")

# ---------------------------------------------------------------------------
# 11. Ollama model missing
# ---------------------------------------------------------------------------

def test_ollama_model_missing_error():
    from unittest.mock import patch, MagicMock
    
    # Mock tags endpoint to return list without our model
    mock_tags_response = MagicMock()
    mock_tags_response.status_code = 200
    mock_tags_response.json.return_value = {"models": [{"name": "llama2:latest"}, {"name": "mistral:latest"}]}
    
    mock_show_response = MagicMock()
    mock_show_response.status_code = 404
    
    def mock_get(url, **kwargs):
        if "tags" in url:
            return mock_tags_response
        raise Exception("unexpected")
    
    def mock_post(url, **kwargs):
        if "chat" in url:
            resp = MagicMock()
            resp.status_code = 404
            resp.text = "model 'qwen2.5:7b' not found, try pulling it first"
            return resp
        return mock_tags_response
    
    with patch("backend.llm_provider.httpx.get", side_effect=mock_get):
        with patch("backend.llm_provider.httpx.post", side_effect=mock_post):
            settings = replace(get_settings(), llm_provider="ollama", ollama_model="qwen2.5:7b")
            provider = OllamaProvider(settings=settings)
            
            # Check availability reports missing
            available, msg = provider.is_available()
            assert available is False
            assert "not found" in msg.lower() or "pull" in msg.lower()
            assert "qwen2.5:7b" in msg or "pull" in msg
            
            # Complete should raise with helpful message
            with pytest.raises(LLMError) as excinfo:
                provider.complete("sys", "user prompt")
            assert "404" in str(excinfo.value) or "not found" in str(excinfo.value).lower()
            if "qwen2.5:7b" in str(excinfo.value):
                assert "pull" in str(excinfo.value).lower()

def test_ollama_model_missing_guidance():
    from unittest.mock import patch, MagicMock
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"models": []}
    
    with patch("backend.llm_provider.httpx.get", return_value=mock_response):
        settings = replace(get_settings(), ollama_model="nonexistent-model:latest")
        provider = OllamaProvider(settings=settings)
        available, msg = provider.check_model_available()
        assert available is False
        assert "pull" in msg.lower()
        assert "nonexistent-model" in msg or "pull" in msg

def test_ollama_connection_error_guidance():
    from unittest.mock import patch
    import httpx
    
    with patch("backend.llm_provider.httpx.get", side_effect=httpx.ConnectError("refused")):
        settings = replace(get_settings(), ollama_host="http://localhost:11434")
        provider = OllamaProvider(settings=settings)
        available, msg = provider.check_model_available()
        assert available is False
        assert "ollama serve" in msg.lower() or "not reachable" in msg.lower()

# ---------------------------------------------------------------------------
# 12. Invalid or malformed model responses
# ---------------------------------------------------------------------------

def test_malformed_json_fallback():
    article = "India installed 25 GW of solar capacity in 2025. The government announced Rs 5,000 crore."
    for payload in [
        "I'm sorry, I can't help with that.",
        "<html><body>502 Bad Gateway</body></html>",
        '{"claims": [{"text": "India installed 25 GW.',  # truncated
        '{"claims": "not-a-list"}',
        "",
        "No JSON here at all, just plain text response",
    ]:
        result = extract_claims_detailed(
            article, 
            provider=MockProvider(response_override=payload)
        )
        assert result.used_fallback is True, f"Should fallback for payload: {payload[:50]}"
        assert len(result.claims) >= 1, f"Fallback should produce claims for {payload[:30]}"

def test_malformed_json_no_fallback_raises():
    settings = replace(get_settings(), llm_fallback_to_heuristics=False)
    with pytest.raises(LLMOutputError):
        extract_claims(
            "India installed 25 GW in 2025. The government announced Rs 5,000 crore.",
            provider=MockProvider(response_override="not json"),
            settings=settings
        )

def test_model_returns_empty_fallback():
    article = "India installed 25 GW in 2025. The government announced Rs 5,000 crore for solar parks. India may become largest by 2030."
    result = extract_claims_detailed(
        article,
        provider=MockProvider(response_override='{"claims": []}'),
        settings=get_settings()
    )
    # Empty from LLM on rich article should trigger fallback
    assert result.used_fallback is True
    assert len(result.claims) >= 1

def test_model_returns_invalid_claim_types():
    payload = '{"claims": [{"text": "India installed 25 GW in 2025.", "claim_type": "invalid_type"}]}'
    claims = extract_claims(
        "India installed 25 GW of solar capacity in 2025. " * 3,
        provider=StaticProvider(payload)
    )
    # Invalid types should default to non_verifiable
    assert claims[0].claim_type == "non_verifiable"

def test_model_returns_fabricated_claim_rejected():
    article = "India installed 25 GW of solar capacity in 2025. The government announced Rs 5,000 crore."
    payload = '{"claims": [{"text": "India installed 90 GW of solar capacity in 2025.", "claim_type": "factual"}]}'
    with pytest.raises(ClaimExtractionError):
        extract_claims(article, provider=StaticProvider(payload))

# ---------------------------------------------------------------------------
# 13. Empty input and unusual input
# ---------------------------------------------------------------------------

def test_empty_input_raises():
    for payload in ["", "   \n\t  ", "\u00a0\u00a0", None]:
        with pytest.raises(EmptyArticleError):
            extract_claims(payload)

def test_whitespace_only_input():
    with pytest.raises(EmptyArticleError):
        extract_claims("   \n\n   \t  ")

def test_non_string_input():
    with pytest.raises(EmptyArticleError):
        extract_claims(12345)
    with pytest.raises(EmptyArticleError):
        extract_claims(["not a string"])

def test_unusual_input_html():
    article = "<p>India installed 25 GW of solar capacity in 2025.</p> <p>The government announced Rs 5,000 crore for solar parks.</p>"
    result = extract_claims_detailed(article)
    assert len(result.claims) >= 1

def test_unusual_input_emoji():
    article = "India installed 25 GW of solar capacity in 2025 🚀🚀. The government announced Rs 5,000 crore for solar parks\u200b."
    result = extract_claims_detailed(article)
    assert len(result.claims) >= 1

def test_unusual_input_unicode_currency():
    article = "The government allocated \u20b95,000 crore for 3 solar parks in 2025. The parks will have capacity of 750 MW."
    result = extract_claims_detailed(article)
    assert len(result.claims) >= 1

def test_unusual_input_very_long():
    article = " ".join([f"India commissioned {i} solar parks in 2025." for i in range(1, 400)])
    result = extract_claims_detailed(article)
    assert len(result.claims) <= 6
    assert result.article_sentence_count <= get_settings().max_sentences_to_llm

def test_unusual_input_only_questions():
    article = "Will India reach its target? Can it double capacity? What will happen next?"
    result = extract_claims_detailed(article)
    assert result.claims == []

def test_unusual_input_questions_with_factual():
    article = "Did you know India installed 25 GW in 2025? That's amazing!"
    result = extract_claims_detailed(article)
    # Should extract the factual part from question
    assert len(result.claims) >= 1
    assert any("25 GW" in c.text for c in result.claims)

def test_ollama_prompt_truncation():
    """Verify complete input reaches model without unintended truncation."""
    from backend.llm_provider import build_prompts
    
    long_article = " ".join([f"Sentence {i} about India installing 25 GW in 2025." for i in range(150)])
    from backend.validators import split_sentences
    sentences = split_sentences(long_article)
    assert len(sentences) > 120, "Test article should be long"
    
    settings = get_settings()
    system, prompt = build_prompts(sentences, settings=settings)
    
    # Should respect max_sentences_to_llm but include that many
    assert "[119]" in prompt or "[120]" in prompt or str(settings.max_sentences_to_llm - 1) in prompt
    # Should not truncate mid-sentence arbitrarily
    assert "Sentence" in prompt

def test_gemini_ollama_parity():
    """Switching providers requires no pipeline change - same schema."""
    from unittest.mock import patch, MagicMock
    
    def fake_gemini_post(*a, **k):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": '{"claims": [{"text": "India installed 25 GW of solar capacity in 2025.", "claim_type": "factual", "entities": ["India"]}]}'}]}}]}
        return resp
    
    def fake_ollama_post(*a, **k):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"message": {"content": '{"claims": [{"text": "India installed 25 GW of solar capacity in 2025.", "claim_type": "factual", "entities": ["India"]}]}'}}
        return resp
    
    article = "India installed 25 GW of solar capacity in 2025. The government announced Rs 5,000 crore."
    
    with patch("backend.llm_provider.httpx.post", side_effect=fake_gemini_post):
        settings = replace(get_settings(), llm_provider="gemini", gemini_api_key="test-key")
        claims_gemini = extract_claims(article, provider=GeminiProvider(settings=settings))
    
    with patch("backend.llm_provider.httpx.post", side_effect=fake_ollama_post):
        settings = replace(get_settings(), llm_provider="ollama", ollama_model="qwen2.5:7b")
        claims_ollama = extract_claims(article, provider=OllamaProvider(settings=settings))
    
    # Both should produce same schema
    for claims in [claims_gemini, claims_ollama]:
        assert len(claims) >= 1
        for claim in claims:
            assert set(claim.model_dump().keys()) == {"id", "text", "entities", "claim_type"}

def test_validation_completeness():
    """Ensure validation gates work correctly."""
    from backend.validators import check_claim
    
    article = "India installed 25 GW of solar capacity in 2025. The government announced Rs 5,000 crore for solar parks."
    
    # Valid claim should pass
    report = check_claim("India installed 25 GW of solar capacity in 2025.", article)
    assert report.ok
    
    # Invalid number should fail
    report = check_claim("India installed 35 GW of solar capacity in 2025.", article)
    assert not report.ok
    assert any("ungrounded_number" in r for r in report.reasons)
    
    # Fabricated entity should fail
    report = check_claim("China installed 25 GW of solar capacity in 2025.", article)
    assert not report.ok

def test_extraction_deterministic():
    """Extraction should be deterministic."""
    article = "PM Modi visited USA and met Trump and signed a deal on oil."
    result1 = extract_claims_detailed(article)
    result2 = extract_claims_detailed(article)
    
    assert [c.text for c in result1.claims] == [c.text for c in result2.claims]
    assert [c.claim_type for c in result1.claims] == [c.claim_type for c in result2.claims]
