"""Bounded full-text fetching for quote verification (Mansimran).

Why: search APIs give us a ~600-1200 character *snippet*. If S3 verifies a quote
against a snippet, it can only ever prove "this sentence is in the fragment we
happened to receive" — not "this sentence is in the source". That is the
difference between an honest claim and an overclaim.

This module fetches the page for a small number of top-ranked results, extracts
readable text, and reports honestly what it got:

    status="full_text"  we have the page text; quotes can be verified against it
    status="snippet"    fetch failed/refused/skipped; ONLY the snippet is checkable

Never raises. Every failure degrades to the snippet, with a reason in the audit.

Design constraints (plan: "every external call must fail soft"):
  * hard per-page timeout, byte cap and character cap
  * text/html only, 2 MB max, no PDFs/binaries
  * skips scripts/styles/nav/footer boilerplate
  * Wikipedia uses the plaintext extracts API instead of scraping HTML
  * results cached through the same TTL cache as search
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import unquote, urlparse

import requests

from cache import Cache, make_key
from http_util import USER_AGENT

log = logging.getLogger("verifyai.fulltext")

MAX_BYTES = 2_000_000
DEFAULT_MAX_CHARS = 6000

_DROP_BLOCKS = re.compile(
    r"<(script|style|noscript|svg|canvas|form|nav|footer|header|aside|iframe)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_BLOCK_TAGS = re.compile(r"</?(p|div|br|li|h[1-6]|tr|section|article|blockquote)\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NEWLINES = re.compile(r"\n{3,}")


@dataclass
class PageText:
    """Outcome of one full-text attempt."""

    url: str
    status: str            # "full_text" | "snippet"
    text: str = ""
    chars: int = 0
    reason: Optional[str] = None
    cached: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "full_text" and bool(self.text)


def html_to_text(markup: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Readable text from HTML: drop boilerplate, keep paragraph structure.

    Deliberately regex-based: no bs4/lxml dependency, so the demo cannot fail on
    a missing package. Prefers <article>, then <main>, then <body>.
    """
    if not markup:
        return ""
    text = _COMMENT.sub(" ", markup)
    text = _DROP_BLOCKS.sub(" ", text)

    for tag in ("article", "main"):
        match = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", text, re.IGNORECASE | re.DOTALL)
        if match and len(match.group(1)) > 400:
            text = match.group(1)
            break

    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _NEWLINES.sub("\n\n", text).strip()
    return text[:max_chars]


def wikipedia_title(url: str) -> Optional[str]:
    """'https://en.wikipedia.org/wiki/Solar_power_in_India' -> 'Solar power in India'."""
    parsed = urlparse(url)
    if not parsed.netloc.endswith("wikipedia.org") or not parsed.path.startswith("/wiki/"):
        return None
    title = unquote(parsed.path[len("/wiki/") :]).replace("_", " ")
    return title or None


def _wikipedia_full_text(url: str, *, timeout: float, max_chars: int, cache: Optional[Cache]) -> PageText:
    """Cheap, reliable plaintext extract via the MediaWiki API."""
    title = wikipedia_title(url)
    lang = urlparse(url).netloc.split(".")[0] or "en"
    if not title:
        return PageText(url=url, status="snippet", reason="not a wikipedia article url")

    key = make_key("wikipedia_extract", lang, title)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            return PageText(url=url, status="full_text", text=hit, chars=len(hit), cached=True)

    try:
        response = requests.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "extracts",
                "explaintext": "1",
                "redirects": "1",
                "titles": title,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=(3.0, timeout),
        )
        response.raise_for_status()
        payload = response.json()
        pages = (payload.get("query") or {}).get("pages") or []
        text = (pages[0].get("extract") or "") if pages else ""
    except Exception as exc:
        return PageText(url=url, status="snippet", reason=f"wikipedia extract failed: {type(exc).__name__}")

    text = _WS.sub(" ", text).strip()[:max_chars]
    if not text:
        return PageText(url=url, status="snippet", reason="wikipedia extract empty")
    if cache is not None:
        cache.set(key, text)
    return PageText(url=url, status="full_text", text=text, chars=len(text))


def fetch_page_text(
    url: str,
    *,
    timeout: float = 5.0,
    max_chars: int = DEFAULT_MAX_CHARS,
    cache: Optional[Cache] = None,
) -> PageText:
    """Fetch readable text for one URL. Never raises; degrades to 'snippet'."""
    if not url or not url.startswith(("http://", "https://")):
        return PageText(url=url or "", status="snippet", reason="not an http(s) url")

    if wikipedia_title(url):
        wiki = _wikipedia_full_text(url, timeout=timeout, max_chars=max_chars, cache=cache)
        if wiki.ok:
            return wiki

    host = urlparse(url).netloc.lower()
    if host in ("doi.org", "dx.doi.org", "pubmed.ncbi.nlm.nih.gov"):
        # publisher/paywall pages: usually a landing page or a redirect wall
        return PageText(url=url, status="snippet", reason=f"{host} returns a landing page, not article text")

    key = make_key("page_text", url)
    if cache is not None:
        hit = cache.get(key)
        if hit is not None:
            text = str(hit)
            return PageText(url=url, status="full_text", text=text, chars=len(text), cached=True)

    try:
        response = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
            timeout=(3.0, timeout),
            stream=True,
            allow_redirects=True,
        )
        if response.status_code >= 400:
            return PageText(url=url, status="snippet", reason=f"HTTP {response.status_code}")
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "html" not in content_type and "text/plain" not in content_type:
            return PageText(url=url, status="snippet", reason=f"unsupported content-type {content_type[:40]!r}")

        raw = response.raw.read(MAX_BYTES + 1, decode_content=True) or b""
        if len(raw) > MAX_BYTES:
            return PageText(url=url, status="snippet", reason="page larger than 2 MB")

        encoding = response.encoding or "utf-8"
        try:
            markup = raw.decode(encoding, errors="replace")
        except LookupError:
            markup = raw.decode("utf-8", errors="replace")
    except requests.Timeout:
        return PageText(url=url, status="snippet", reason="timeout")
    except requests.RequestException as exc:
        return PageText(url=url, status="snippet", reason=type(exc).__name__)
    except Exception as exc:  # pragma: no cover - defensive
        return PageText(url=url, status="snippet", reason=f"{type(exc).__name__}: {exc}"[:80])

    text = html_to_text(markup, max_chars=max_chars)
    if len(text) < 200:
        return PageText(url=url, status="snippet", reason=f"extracted text too short ({len(text)} chars)")
    if cache is not None:
        cache.set(key, text)
    return PageText(url=url, status="full_text", text=text, chars=len(text))
