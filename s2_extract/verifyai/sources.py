"""S2 — source connectors (Mansimran).

Every adapter:
  * searches one external corpus,
  * returns ``RawResult`` items (title / url / snippet / published_at / kind),
  * raises ``SourceDisabled`` when it cannot run at all (e.g. missing API key),
  * lets every network problem bubble up as ``SourceError`` so the engine can
    mark the source as failed and keep going.

Adapters are deliberately dumb: no ranking, no LLM calls, no claim logic.
Ranking lives in ``ranking.py``, orchestration in ``s2_retrieval.py``.
"""

from __future__ import annotations

import html
import json
import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

from cache import Cache
from envload import load_env
from http_util import FetchResult, SourceError, post_form, post_json, request_json, request_text

# keys may come from a .env file (important on Windows, where people rarely
# export variables into the shell)
load_env()

# ---------------------------------------------------------------------------
# Advisory credibility hints.
# The lead owns the C term in S = 0.30C + 0.30R + 0.20A + 0.10T + 0.10K.
# This is only a starter table so scoring does not have to invent numbers.
# ---------------------------------------------------------------------------
CREDIBILITY_HINTS: dict[str, float] = {
    "fact_check": 0.90,
    "encyclopedia": 0.80,
    "academic": 0.85,
    "news": 0.65,
    "web": 0.45,
    "mock": 0.10,
}

TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_", "igshid")


class SourceDisabled(Exception):
    """Adapter is intentionally not running (missing key, offline mode, ...)."""


@dataclass
class RawResult:
    title: str
    url: str
    snippet: str
    published_at: Optional[str] = None
    source_kind: str = "web"
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# URL / text helpers
# ---------------------------------------------------------------------------
def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    return host


def cap_key(url: str) -> str:
    """Grouping key used for the per-domain cap.

    ``doi.org`` would otherwise collapse every scholarly result into one
    "domain"; group by DOI prefix (the publisher) instead so academic sources
    can still contribute more than one item.
    """
    host = domain_of(url)
    if host in ("doi.org", "dx.doi.org"):
        tail = urlparse(url).path.strip("/")
        if tail.startswith("10."):
            return f"doi.org/{tail.split('/')[0]}"
    return host


def canonical_url(url: str) -> str:
    """Lower-risk duplicate detection: drop tracking params, fragments, trailing slash."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()
    if not parsed.scheme:
        parsed = urlparse("https://" + url.strip())
    query = parse_qs(parsed.query, keep_blank_values=False)
    kept = {
        k: v
        for k, v in sorted(query.items())
        if not k.lower().startswith(TRACKING_PARAM_PREFIXES)
    }
    netloc = parsed.netloc.lower().replace(":80", "").replace(":443", "")
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/") or "/"
    flat = "&".join(f"{k}={v[0]}" for k, v in kept.items())
    return f"https://{netloc}{path}" + (f"?{flat}" if flat else "")


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(text: str | None, limit: int = 1200) -> str:
    if not text:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", text))
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


def _iso_date(value: Any) -> Optional[str]:
    """Best-effort ISO-8601 date from the many shapes APIs return."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.utcfromtimestamp(float(value)).date().isoformat()
        text = str(value).strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return text
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", text):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        if re.fullmatch(r"\d{4}", text):
            return f"{text}-01-01"
        return datetime.fromisoformat(text).date().isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Patch points for tests (monkeypatch these names)
# ---------------------------------------------------------------------------
def _get_json(url: str, **kwargs: Any) -> FetchResult:
    return request_json(url, **kwargs)


def _post_form(url: str, form: dict[str, Any], **kwargs: Any) -> FetchResult:
    return post_form(url, form, **kwargs)


def _post_json(url: str, body: dict[str, Any], **kwargs: Any) -> FetchResult:
    return post_json(url, body, **kwargs)


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
class SourceAdapter(ABC):
    name: str = "base"
    kind: str = "web"
    #: rough cost/latency budget used by the engine's per-source watchdogs
    budget_s: float = 10.0
    #: True when the adapter serves captured data instead of hitting the network
    is_cached: bool = False

    @abstractmethod
    def search(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        raise NotImplementedError

    def _fetch(self, url: str, **kwargs: Any) -> FetchResult:
        kwargs.setdefault("timeout", 8.0)
        kwargs.setdefault("retries", 1)
        return _get_json(url, **kwargs)


class WikipediaSource(SourceAdapter):
    """MediaWiki generator=search + page intro extracts. No key required."""

    name = "wikipedia"
    kind = "encyclopedia"
    budget_s = 9.0
    ENDPOINT = "https://en.wikipedia.org/w/api.php"

    def __init__(self, lang: str = "en") -> None:
        self.endpoint = f"https://{lang}.wikipedia.org/w/api.php"

    def search(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": max(1, min(limit, 10)),
            "gsrnamespace": "0",
            "prop": "extracts",
            "exintro": "1",
            "explaintext": "1",
            "exsentences": "6",
            "redirects": "1",
        }
        payload = self._fetch(self.endpoint, params=params, cache=cache).data
        if isinstance(payload, dict) and payload.get("error"):
            # MediaWiki answers 200 + {"error": ...} for bad parameter mixes
            raise SourceError(f"wikipedia api error: {payload['error'].get('info', '')[:150]}")
        pages = (payload.get("query") or {}).get("pages") or []
        results: list[RawResult] = []
        for page in pages:
            title = page.get("title") or ""
            pageid = page.get("pageid")
            if not title or pageid is None or page.get("missing"):
                continue
            results.append(
                RawResult(
                    title=title,
                    url=f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                    snippet=clean_text(page.get("extract")),
                    published_at=None,
                    source_kind=self.kind,
                    extra={"pageid": pageid, "index": page.get("index")},
                )
            )
        results.sort(key=lambda r: r.extra.get("index") or 99)
        return results


class DuckDuckGoSource(SourceAdapter):
    """DuckDuckGo HTML endpoint (no key, no quota) with an instant-answer fallback."""

    name = "duckduckgo"
    kind = "web"
    budget_s = 12.0
    HTML_ENDPOINT = "https://html.duckduckgo.com/html/"
    IA_ENDPOINT = "https://api.duckduckgo.com/"

    def search(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        results = self._search_html(query, limit=limit, cache=cache)
        if results:
            return results
        return self._search_instant(query, limit=limit, cache=cache)

    def _search_html(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        fetched = _post_form(
            self.HTML_ENDPOINT,
            {"q": query, "kl": "us-en"},
            timeout=9.0,
            retries=1,
            cache=cache,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        body = fetched.data if isinstance(fetched.data, str) else ""
        return self.parse_html(body, limit=limit)

    @staticmethod
    def parse_html(body: str, *, limit: int) -> list[RawResult]:
        """Parse DDG's HTML page without a bs4 hard dependency (regex + tag strip)."""
        results: list[RawResult] = []
        blocks = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="[^"]*result__a|$)',
            body,
            flags=re.S | re.I,
        )
        for href, title_html, rest in blocks:
            url = DuckDuckGoSource._decode_href(href)
            if not url:
                continue
            snippet_match = re.search(
                r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div|span)>', rest, flags=re.S | re.I
            )
            date_match = re.search(r'class="[^"]*result__timestamp[^"]*"[^>]*>(.*?)<', rest, flags=re.S | re.I)
            results.append(
                RawResult(
                    title=clean_text(title_html, limit=300),
                    url=url,
                    snippet=clean_text(snippet_match.group(1) if snippet_match else "", limit=600),
                    published_at=_iso_date(clean_text(date_match.group(1)) if date_match else None),
                    source_kind="web",
                )
            )
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _decode_href(href: str) -> str:
        href = html.unescape(href)
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg")
            if target:
                return unquote(target[0])
        if parsed.scheme in ("http", "https"):
            return href
        return ""

    def _search_instant(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        try:
            payload = _get_json(
                self.IA_ENDPOINT, params=params, timeout=8.0, retries=0, cache=cache
            ).data
        except SourceError:
            return []  # DDG answers non-JSON when it throttles; not a hard failure
        if not isinstance(payload, dict):
            return []
        results: list[RawResult] = []
        abstract = clean_text(payload.get("AbstractText"))
        if abstract:
            abstract_url = payload.get("AbstractURL") or ""
            if abstract_url and domain_of(abstract_url) != "duckduckgo.com":
                results.append(
                    RawResult(
                        title=payload.get("Heading") or query,
                        url=abstract_url,
                        snippet=abstract,
                        source_kind="web",
                        extra={"provider": "ddg-instant-answer"},
                    )
                )
        for topic in (payload.get("RelatedTopics") or [])[:limit]:
            if not isinstance(topic, dict) or not topic.get("Text"):
                continue
            if domain_of(topic.get("FirstURL") or "") == "duckduckgo.com":
                continue  # category pages are navigation, not evidence
            results.append(
                RawResult(
                    title=clean_text(topic.get("Text"), 120),
                    url=(topic.get("FirstURL") or "https://duckduckgo.com/"),
                    snippet=clean_text(topic.get("Text"), 500),
                    source_kind="web",
                    extra={"provider": "ddg-instant-answer"},
                )
            )
            if len(results) >= limit:
                break
        return results


class OpenAlexSource(SourceAdapter):
    """Scholarly works, free and keyless. Good for a third academic source."""

    name = "openalex"
    kind = "academic"
    budget_s = 10.0
    ENDPOINT = "https://api.openalex.org/works"

    def search(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        params = {
            "search": query,
            "per-page": max(1, min(limit, 10)),
            "sort": "relevance_score:desc",
            "select": (
                "id,title,publication_date,doi,abstract_inverted_index,"
                "primary_location,cited_by_count,type"
            ),
            "mailto": os.getenv("OPENALEX_MAILTO", "team@verifyai.local"),
        }
        payload = self._fetch(self.ENDPOINT, params=params, cache=cache).data
        results: list[RawResult] = []
        for work in payload.get("results") or []:
            url = (
                (work.get("primary_location") or {}).get("landing_page_url")
                or work.get("doi")
                or work.get("id")
                or ""
            )
            if not url:
                continue
            results.append(
                RawResult(
                    title=clean_text(work.get("title"), 300),
                    url=url,
                    snippet=clean_text(self._abstract(work.get("abstract_inverted_index")), 900),
                    published_at=_iso_date(work.get("publication_date")),
                    source_kind="academic",
                    extra={"cited_by": work.get("cited_by_count"), "work_type": work.get("type")},
                )
            )
        return results

    @staticmethod
    def _abstract(inverted: dict[str, list[int]] | None) -> str:
        if not inverted:
            return ""
        positions: list[tuple[int, str]] = []
        for word, spots in inverted.items():
            for spot in spots:
                positions.append((spot, word))
        positions.sort()
        return " ".join(word for _, word in positions)


class PubMedSource(SourceAdapter):
    """NCBI E-utilities: esearch (ids) + efetch (abstracts). Keyless and credible."""

    name = "pubmed"
    kind = "academic"
    budget_s = 14.0
    ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

    def search(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        search = self._fetch(
            self.ESEARCH,
            params={
                "db": "pubmed",
                "term": query,
                "retmax": max(1, min(limit, 10)),
                "retmode": "json",
                "sort": "relevance",
            },
            cache=cache,
        ).data
        ids = ((search.get("esearchresult") or {}).get("idlist")) or []
        if not ids:
            return []
        # efetch returns XML, not JSON
        fetched = request_text(
            self.EFETCH,
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "rettype": "abstract"},
            timeout=12.0,
            retries=1,
            cache=cache,
        ).data
        return self.parse_xml(fetched if isinstance(fetched, str) else "", limit=limit)

    @staticmethod
    def parse_xml(xml_text: str, *, limit: int) -> list[RawResult]:
        if not xml_text.strip():
            return []
        try:
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml_text)
        except Exception:
            return []
        results: list[RawResult] = []
        for article in root.findall(".//PubmedArticle")[:limit]:
            pmid = article.findtext(".//PMID") or ""
            if not pmid:
                continue
            title_node = article.find(".//ArticleTitle")
            title = "".join(title_node.itertext()) if title_node is not None else ""
            abstract_parts = [
                "".join(node.itertext())
                for node in article.findall(".//Abstract/AbstractText")
            ]
            journal = article.findtext(".//Journal/Title") or ""
            year = article.findtext(".//JournalIssue/PubDate/Year") or article.findtext(
                ".//JournalIssue/PubDate/MedlineDate"
            )
            doi = ""
            for node in article.findall(".//ArticleId"):
                if (node.get("IdType") or "").lower() == "doi" and node.text:
                    doi = node.text.strip()
                    break
            results.append(
                RawResult(
                    title=clean_text(title, 300),
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    snippet=clean_text(" ".join(abstract_parts), 900) or clean_text(title, 300),
                    published_at=_iso_date(year),
                    source_kind="academic",
                    extra={"pmid": pmid, "journal": journal, "doi": doi},
                )
            )
        return results


class TavilySource(SourceAdapter):
    """Tavily web search — never fails the run; raises SourceDisabled without a key."""

    name = "tavily"
    kind = "web"
    budget_s = 12.0
    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("TAVILY_API_KEY", "").strip()

    def search(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        if not self.api_key:
            raise SourceDisabled("TAVILY_API_KEY not set")
        payload = _post_json(
            self.ENDPOINT,
            {
                "api_key": self.api_key,
                "query": query,
                "max_results": max(1, min(limit, 8)),
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            },
            timeout=11.0,
            retries=1,
            cache=cache,
        ).data
        results: list[RawResult] = []
        for item in payload.get("results") or []:
            if not item.get("url"):
                continue
            results.append(
                RawResult(
                    title=clean_text(item.get("title"), 300),
                    url=item["url"],
                    snippet=clean_text(item.get("content"), 900),
                    published_at=_iso_date(item.get("published_date")),
                    source_kind="web",
                    extra={"score": item.get("score")},
                )
            )
        return results


class GoogleFactCheckSource(SourceAdapter):
    """Google Fact Check Tools — highest-value source, needs a free API key."""

    name = "google_factcheck"
    kind = "fact_check"
    budget_s = 10.0
    ENDPOINT = "https://factchecktools.googleapis.com/v1alpha1/claims:search"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("GOOGLE_FACTCHECK_API_KEY", "").strip()

    def search(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        if not self.api_key:
            raise SourceDisabled("GOOGLE_FACTCHECK_API_KEY not set")
        params = {
            "query": query,
            "key": self.api_key,
            "pageSize": max(1, min(limit, 10)),
            "languageCode": "en",
        }
        payload = self._fetch(self.ENDPOINT, params=params, cache=cache).data
        results: list[RawResult] = []
        for item in payload.get("claims") or []:
            claim_text = clean_text(item.get("text"), 400)
            for review in item.get("claimReview") or []:
                url = review.get("url") or ""
                if not url:
                    continue
                publisher = (review.get("publisher") or {}).get("name") or domain_of(url)
                rating = clean_text(review.get("textualRating"), 80)
                results.append(
                    RawResult(
                        title=clean_text(review.get("title"), 200) or f"Fact check: {claim_text[:120]}",
                        url=url,
                        snippet=clean_text(
                            f"Claim: {claim_text} — Rating by {publisher}: {rating}", 600
                        ),
                        published_at=_iso_date(review.get("reviewDate")),
                        source_kind="fact_check",
                        extra={"rating": rating, "publisher": publisher},
                    )
                )
                if len(results) >= limit:
                    return results
        return results


class LocalCorpusSource(SourceAdapter):
    """Offline/mock fallback so retrieval (and the demo) never returns nothing.

    Reads ``data/mock_corpus.json``: a list of
    ``{"title", "url", "text", "published_at", "keywords": [...]}``.
    Keyword overlap decides matches; output is tagged ``source_kind="mock"`` so
    the UI/report can label it as a demo backup, never as real evidence.
    """

    name = "local_corpus"
    kind = "mock"
    budget_s = 1.0

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else Path(__file__).parent / "data" / "mock_corpus.json"

    def _load(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []

    def search(self, query: str, *, limit: int, cache: Optional[Cache] = None) -> list[RawResult]:
        tokens = {t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2}
        hits: list[tuple[float, dict[str, Any]]] = []
        for entry in self._load():
            keys = {k.lower() for k in entry.get("keywords", [])}
            overlap = len(tokens & keys)
            if overlap:
                hits.append((overlap / max(len(tokens), 1), entry))
        hits.sort(key=lambda item: -item[0])
        return [
            RawResult(
                title=entry.get("title", ""),
                url=entry.get("url", ""),
                snippet=clean_text(entry.get("text"), 900),
                published_at=_iso_date(entry.get("published_at")),
                source_kind="mock",
                extra={"match": round(score, 3), "demo_backup": True},
            )
            for score, entry in hits[:limit]
            if entry.get("url")
        ]


def default_adapters(*, offline: bool = False, mock: bool = False) -> list[SourceAdapter]:
    """Adapter set, ordered by reliability as measured on the hackathon wifi.

    Live-verified keyless trio: wikipedia (encyclopedia), openalex (academic),
    pubmed (academic/health). DuckDuckGo is included last: its HTML endpoint
    sometimes answers 202 + an anti-bot page from datacenter IPs, in which case
    the adapter returns [] instead of failing the run. Tavily and Google Fact
    Check activate automatically once their keys are in .env.
    """
    if offline:
        # demo safety net: never touch the network at all
        return [LocalCorpusSource()]

    adapters: list[SourceAdapter] = [WikipediaSource(), OpenAlexSource(), PubMedSource()]
    if os.getenv("TAVILY_API_KEY", "").strip():
        adapters.append(TavilySource())
    if os.getenv("GOOGLE_FACTCHECK_API_KEY", "").strip():
        adapters.append(GoogleFactCheckSource())
    adapters.append(DuckDuckGoSource())
    if mock:
        adapters.append(LocalCorpusSource())
    return adapters
