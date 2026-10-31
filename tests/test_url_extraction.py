"""Tests: URL validation, SSRF protection, article extraction & failures."""

from __future__ import annotations

import httpx
import pytest

from backend import article_extractor
from backend.article_extractor import (
    assert_public_host,
    clean_article_text,
    extract_article_from_url,
    fetch_html,
    validate_url,
)
from backend.errors import ArticleExtractionError, InvalidURLError
from backend.s1_claims import extract_claims_from_url


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/story",
        "http://news.example.org/a/b?x=1",
        "example.com/story",  # scheme-less input is normalised to https
        "https://example.com:8443/story",
    ],
)
def test_valid_urls_normalise(url):
    normalized = validate_url(url)
    assert normalized.startswith("http")
    assert " " not in normalized


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://example.com/file.txt",
        "gopher://example.com/",
        "https://user:pass@example.com/story",
        "http://localhost/admin",
        "http://127.0.0.1:8000/",
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/",
        "http://internal-host/",  # single-label host
        "https://example.com:22/story",
        "",
        None,
    ],
)
def test_unsafe_urls_are_rejected(url):
    with pytest.raises(InvalidURLError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "http://192.168.1.10/",
        "http://172.16.4.2/",
        "http://0.0.0.0/",
        "http://100.100.100.200/",
        "http://[::1]/",
        "http://[fd00::1]/",
    ],
)
def test_private_and_metadata_addresses_are_blocked(url):
    """SSRF: loopback/link-local/private/CGNAT targets must never be fetched."""
    with pytest.raises(InvalidURLError):
        assert_public_host(validate_url(url))


def test_configured_blocked_hosts(monkeypatch):
    from dataclasses import replace

    from backend.config import get_settings

    settings = replace(get_settings(), blocked_hosts=["example.com"])
    with pytest.raises(InvalidURLError):
        validate_url("https://example.com/story", settings=settings)


# ---------------------------------------------------------------------------
# Fake HTTP layer
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, headers=None, content=b"", raise_on_read=None):
        self.status_code = status_code
        self.headers = httpx.Headers(headers or {})
        self.charset_encoding = None
        self._content = content
        self._raise_on_read = raise_on_read

    def iter_bytes(self, chunk_size: int = 16384):
        if self._raise_on_read is not None:
            raise self._raise_on_read
        for start in range(0, len(self._content), chunk_size):
            yield self._content[start : start + chunk_size]


class FakeStream:
    def __init__(self, response, error=None):
        self._response = response
        self._error = error

    def __enter__(self):
        if self._error is not None:
            raise self._error
        return self._response

    def __exit__(self, *exc_info):
        return False


class FakeClient:
    """Minimal httpx.Client stand-in driven by a list of responses."""

    def __init__(self, queue, **kwargs):
        self.queue = queue
        self.requested = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def stream(self, method, url):
        self.requested.append(url)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            return FakeStream(None, error=item)
        return FakeStream(item)


def _install_http(monkeypatch, responses):
    clients = []

    def factory(**kwargs):
        client = FakeClient(responses, **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(article_extractor.httpx, "Client", factory)
    monkeypatch.setattr(article_extractor, "assert_public_host", lambda *a, **k: None)
    return clients


HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}


# ---------------------------------------------------------------------------
# Successful extraction
# ---------------------------------------------------------------------------


def test_trafilatura_extracts_main_content(monkeypatch, sample_html):
    monkeypatch.setattr(
        article_extractor,
        "fetch_html",
        lambda url, settings=None: (url, sample_html),
    )
    article = extract_article_from_url("https://example.com/solar")

    assert "25 GW" in article
    assert "nav" not in article.lower().split()  # chrome removed
    assert "Buy cheap solar panels" not in article
    assert "Copyright 2026" not in article
    assert "Related:" not in article


def test_beautifulsoup_fallback_when_trafilatura_fails(monkeypatch, sample_html):
    monkeypatch.setattr(
        article_extractor,
        "fetch_html",
        lambda url, settings=None: (url, sample_html),
    )
    monkeypatch.setattr(
        article_extractor, "_extract_with_trafilatura", lambda html, url: ""
    )
    article = extract_article_from_url("https://example.com/solar")

    assert "25 GW" in article
    assert "Buy cheap solar panels" not in article
    assert "Copyright 2026" not in article


def test_fetch_html_follows_redirect_to_public_url(monkeypatch):
    _install_http(
        monkeypatch,
        [
            FakeResponse(301, {"location": "https://example.com/final"}),
            FakeResponse(200, HTML_HEADERS, b"<html><body><p>hello</p></body></html>"),
        ],
    )
    final_url, html = fetch_html("https://example.com/start")

    assert final_url == "https://example.com/final"
    assert "hello" in html


def test_fetch_html_rejects_redirect_to_private_host(monkeypatch):
    _install_http(
        monkeypatch,
        [FakeResponse(302, {"location": "http://127.0.0.1:8000/admin"})],
    )
    with pytest.raises(InvalidURLError):
        fetch_html("https://example.com/start")


def test_fetch_html_decodes_body(monkeypatch):
    _install_http(
        monkeypatch,
        [FakeResponse(200, HTML_HEADERS, "<html><p>caf\u00e9</p></html>".encode("utf-8"))],
    )
    _, html = fetch_html("https://example.com/x")
    assert "caf\u00e9" in html


# ---------------------------------------------------------------------------
# 15. URL extraction failures
# ---------------------------------------------------------------------------


def test_http_error_status_raises(monkeypatch):
    _install_http(monkeypatch, [FakeResponse(404, HTML_HEADERS, b"not found")])
    with pytest.raises(ArticleExtractionError):
        fetch_html("https://example.com/missing")


def test_timeout_raises(monkeypatch):
    _install_http(monkeypatch, [httpx.ConnectTimeout("timed out")])
    with pytest.raises(ArticleExtractionError):
        fetch_html("https://example.com/slow")


def test_connection_error_raises(monkeypatch):
    _install_http(monkeypatch, [httpx.ConnectError("no route to host")])
    with pytest.raises(ArticleExtractionError):
        fetch_html("https://example.com/down")


def test_oversized_content_length_is_rejected(monkeypatch):
    _install_http(
        monkeypatch,
        [
            FakeResponse(
                200,
                {**HTML_HEADERS, "content-length": str(500 * 1024 * 1024)},
                b"x",
            )
        ],
    )
    with pytest.raises(ArticleExtractionError, match="too large"):
        fetch_html("https://example.com/huge")


def test_download_is_capped_while_streaming(monkeypatch):
    from dataclasses import replace

    from backend.config import get_settings

    monkeypatch.setattr(
        article_extractor,
        "get_settings",
        lambda reload=False: replace(get_settings(), max_download_bytes=1024),
    )
    _install_http(monkeypatch, [FakeResponse(200, HTML_HEADERS, b"a" * 4096)])
    with pytest.raises(ArticleExtractionError, match="exceeded"):
        fetch_html("https://example.com/huge")


def test_unsupported_content_type_is_rejected(monkeypatch):
    _install_http(
        monkeypatch, [FakeResponse(200, {"content-type": "application/pdf"}, b"%PDF")]
    )
    with pytest.raises(ArticleExtractionError):
        fetch_html("https://example.com/file.pdf")


def test_too_many_redirects(monkeypatch):
    from dataclasses import replace

    from backend.config import get_settings

    monkeypatch.setattr(
        article_extractor,
        "get_settings",
        lambda reload=False: replace(get_settings(), max_redirects=2),
    )
    _install_http(
        monkeypatch,
        [FakeResponse(302, {"location": f"https://example.com/{i}"}) for i in range(5)],
    )
    with pytest.raises(ArticleExtractionError, match="redirects"):
        fetch_html("https://example.com/start")


def test_page_without_article_content_raises(monkeypatch):
    monkeypatch.setattr(
        article_extractor,
        "fetch_html",
        lambda url, settings=None: (url, "<html><body><div></div></body></html>"),
    )
    with pytest.raises(ArticleExtractionError):
        extract_article_from_url("https://example.com/empty")


def test_invalid_url_propagates_from_extract_article_from_url():
    with pytest.raises(InvalidURLError):
        extract_article_from_url("http://127.0.0.1/admin")


# ---------------------------------------------------------------------------
# End-to-end: URL -> claims
# ---------------------------------------------------------------------------


def test_extract_claims_from_url(monkeypatch, sample_html):
    monkeypatch.setattr(
        article_extractor,
        "fetch_html",
        lambda url, settings=None: (url, sample_html),
    )
    claims = extract_claims_from_url("https://example.com/solar")

    assert 1 <= len(claims) <= 6
    assert all(set(claim.model_dump()) == {"id", "text", "entities", "claim_type"} for claim in claims)
    assert any("25 GW" in claim.text for claim in claims)


def test_extract_claims_from_url_uses_extractor(monkeypatch):
    import backend.s1_claims as s1

    captured = {}

    def fake_extract(url, settings=None):
        captured["url"] = url
        return (
            "India installed 25 GW of solar capacity in 2025. "
            "The government announced Rs 5,000 crore for solar parks in 2025."
        )

    monkeypatch.setattr(s1, "extract_article_from_url", fake_extract)
    claims = s1.extract_claims_from_url("https://example.com/solar")

    assert captured["url"] == "https://example.com/solar"
    assert claims


def test_clean_article_text_removes_boilerplate():
    raw = (
        "Subscribe to our newsletter\n"
        "India installed 25 GW of solar capacity in 2025.\n"
        "Advertisement\n"
        "By Jane Doe\n"
        "Copyright 2026 Example News\n"
    )
    cleaned = clean_article_text(raw)
    assert "25 GW" in cleaned
    assert "Subscribe" not in cleaned
    assert "Advertisement" not in cleaned
    assert "Copyright" not in cleaned
