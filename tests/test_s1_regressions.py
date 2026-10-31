def test_no_claim_content_returns_empty():
    from backend import extract_claims

    assert extract_claims("Good morning. See you tomorrow. Thank you.") == []


def test_company_reference_keeps_grounded_subject():
    from backend import extract_claims

    article = (
        "Apple reported $94.9 billion revenue in Q4 2024. "
        "The company sold 46.2 million iPhones. Services grew 12%."
    )
    texts = [claim.text.casefold() for claim in extract_claims(article)]

    assert any(
        ("apple" in text or "company" in text)
        and "46.2 million iphones" in text
        for text in texts
    ), texts
    assert not any(text.startswith("iphones sold") for text in texts), texts
