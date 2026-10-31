from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main as api
from backend.errors import (
    ArticleExtractionError,
    ClaimExtractionError,
    EmptyArticleError,
    InvalidURLError,
    LLMError,
)
from backend.schemas import Claim as S1Claim


client = TestClient(api.app)


def _sample_claim() -> S1Claim:
    return S1Claim(
        id="claim-1",
        text="Acme opened a factory in Delhi.",
        entities=["Acme", "Delhi"],
        claim_type="factual",
    )


def test_extract_text_maps_s1_type_and_preserves_entities(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_extract(article_text: str) -> list[S1Claim]:
        seen["article_text"] = article_text
        return [_sample_claim()]

    monkeypatch.setattr(api, "extract_claims", fake_extract)
    response = client.post(
        "/api/extract",
        json={"article_text": "  Acme opened a factory in Delhi.  "},
    )

    assert response.status_code == 200
    assert seen["article_text"] == "Acme opened a factory in Delhi."
    data = response.json()
    assert data["source_type"] == "article_text"
    assert data["stage"] == "S1_EXTRACTION_ONLY"
    assert data["claims"] == [
        {
            "id": "claim-1",
            "text": "Acme opened a factory in Delhi.",
            "type": "factual",
            "entities": ["Acme", "Delhi"],
        }
    ]
    assert "verdict" not in data
    assert "No evidence retrieval" in data["note"]


def test_extract_url_rejects_loopback_via_s1_ssrf_guard() -> None:
    response = client.post("/api/extract", json={"url": "http://127.0.0.1/article"})

    assert response.status_code == 400
    assert "Blocked host" in response.json()["detail"]


def test_extract_url_uses_s1_url_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def fake_extract(url: str) -> list[S1Claim]:
        seen["url"] = url
        return [_sample_claim()]

    monkeypatch.setattr(api, "extract_claims_from_url", fake_extract)
    response = client.post("/api/extract", json={"url": " https://example.com/story "})

    assert response.status_code == 200
    assert seen["url"] == "https://example.com/story"
    assert response.json()["source_type"] == "url"
    assert response.json()["claims"][0]["type"] == "factual"
    assert response.json()["claims"][0]["entities"] == ["Acme", "Delhi"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"article_text": "article", "url": "https://example.com/story"},
        {"article_text": "   "},
        {"url": "   "},
    ],
)
def test_extract_requires_exactly_one_non_empty_source(payload: dict[str, str]) -> None:
    response = client.post("/api/extract", json=payload)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("function_name", "payload", "error", "expected_status"),
    [
        ("extract_claims", {"article_text": "article"}, EmptyArticleError, 400),
        ("extract_claims_from_url", {"url": "http://127.0.0.1/article"}, InvalidURLError, 400),
        ("extract_claims_from_url", {"url": "https://example.com/story"}, ArticleExtractionError, 502),
        ("extract_claims", {"article_text": "article"}, LLMError, 502),
        ("extract_claims", {"article_text": "article"}, ClaimExtractionError, 422),
    ],
)
def test_extract_maps_s1_errors_to_http_status(
    monkeypatch: pytest.MonkeyPatch,
    function_name: str,
    payload: dict[str, str],
    error: type[Exception],
    expected_status: int,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise error("test S1 failure")

    monkeypatch.setattr(api, function_name, fail)
    response = client.post("/api/extract", json=payload)

    assert response.status_code == expected_status
    assert response.json()["detail"] == "test S1 failure"
