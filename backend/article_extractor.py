"""Article text extraction from a URL (secondary input method).

Security model (SSRF hardening)
-------------------------------
1. Only http/https, no embedded credentials, allow-listed ports.
2. Hostnames are resolved BEFORE connecting; every resolved address must be
   globally routable. Private, loopback, link-local, CGNAT, multicast, reserved
   and cloud-metadata addresses are rejected (this blocks
   http://169.254.169.254/, http://127.0.0.1:8000/, http://[::1]/ etc.).
3. Redirects are followed manually and every hop is re-validated, so a public
   URL cannot bounce the fetcher onto an internal address.
4. Downloads are streamed with a hard byte cap (default 5 MB).
5. Content-Type must be HTML/XHTML/plain text.

Known residual risk: DNS rebinding between our resolution and httpx's
connection is not fully eliminated (that would require pinning the socket to the
validated IP). Documented rather than hidden — acceptable for a hackathon S1,
and the resolver check still blocks the classic metadata/internal attacks.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Tuple
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from .config import Settings, get_settings
from .errors import ArticleExtractionError, InvalidURLError
from .validators import normalize_text

_ALLOWED_CONTENT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "application/xml",
)

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "0.0.0.0",
    "[::1]",
    "::1",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "kubernetes",
    "kubernetes.default",
    "kubernetes.default.svc",
}

_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".svc", ".cluster.local")

_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?$", re.IGNORECASE)

_BOILERPLATE_LINE_RE = re.compile(
    r"^(advertisement|subscribe|sign up|sign in|log in|share this|share on|"
    r"follow us|read more|also read|related:|recommended|trending|photo:|"
    r"image:|credit:|getty images|download the app|click here|watch:|listen:|"
    r"cookie|we use cookies|accept all|manage preferences|comments?\b|"
    r"terms of (use|service)|privacy policy|all rights reserved|"
    r"copyright|\u00a9|skip to (main )?content|accept cookies)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# URL validation / SSRF protection
# ---------------------------------------------------------------------------


def _is_public_ip(ip_value: str) -> bool:
    """True only for globally routable unicast addresses."""
    try:
        ip = ipaddress.ip_address(ip_value)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def _resolve_host(hostname: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise InvalidURLError(f"Could not resolve host {hostname!r}: {exc}") from exc
    addresses = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)
    return addresses


def validate_url(url: str, settings: Settings | None = None) -> str:
    """Validate a URL for fetching and return its normalised form.

    Raises:
        InvalidURLError: scheme/credentials/port/host policy violation.
    """
    settings = settings or get_settings()

    if not isinstance(url, str) or not url.strip():
        raise InvalidURLError("URL must be a non-empty string")

    candidate = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://", candidate):
        # Be forgiving: "example.com/story" -> "https://example.com/story"
        candidate = "https://" + candidate.lstrip("/")

    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in settings.allowed_schemes:
        raise InvalidURLError(
            f"Unsupported URL scheme {scheme!r}; only {settings.allowed_schemes} are allowed"
        )
    if "@" in parts.netloc:
        raise InvalidURLError("URLs with embedded credentials are not allowed")

    hostname = (parts.hostname or "").strip().lower()
    if not hostname:
        raise InvalidURLError(f"URL has no host: {url!r}")
    if hostname in _BLOCKED_HOSTNAMES or hostname.strip("[]") in _BLOCKED_HOSTNAMES:
        raise InvalidURLError(f"Blocked host: {hostname!r}")
    if any(hostname.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        raise InvalidURLError(f"Blocked internal hostname: {hostname!r}")
    if hostname in {blocked.lower() for blocked in settings.blocked_hosts}:
        raise InvalidURLError(f"Host blocked by configuration: {hostname!r}")
    # Defence in depth: reject non-public IP literals before any DNS lookup.
    bare_host = hostname.strip("[]")
    if _is_ip_literal(bare_host) and not _is_public_ip(bare_host):
        raise InvalidURLError(f"Blocked non-public IP address: {bare_host}")

    if "." not in hostname and not re.match(r"^[\d.]+$", hostname):
        # Single-label hostnames are always internal (except IPs handled above).
        raise InvalidURLError(f"Refusing single-label hostname: {hostname!r}")

    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidURLError(f"Invalid port in URL: {exc}") from exc
    if port is not None and port not in settings.allowed_ports:
        raise InvalidURLError(
            f"Port {port} is not allowed; allowed ports are {settings.allowed_ports}"
        )

    if not _HOSTNAME_RE.match(hostname.replace(":", "")) and not _is_ip_literal(hostname):
        raise InvalidURLError(f"Invalid hostname: {hostname!r}")

    return urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _is_ip_literal(hostname: str) -> bool:
    try:
        ipaddress.ip_address(hostname.strip("[]"))
        return True
    except ValueError:
        return False


def assert_public_host(url: str, settings: Settings | None = None) -> None:
    """Resolve the URL host and ensure every address is publicly routable."""
    settings = settings or get_settings()
    hostname = (urlsplit(url).hostname or "").strip()
    if not hostname:
        raise InvalidURLError(f"URL has no host: {url!r}")

    bare = hostname.strip("[]")
    if _is_ip_literal(bare):
        if not _is_public_ip(bare):
            raise InvalidURLError(f"Blocked non-public IP address: {bare}")
        return

    addresses = _resolve_host(hostname)
    if not addresses:
        raise InvalidURLError(f"Host {hostname!r} did not resolve to any address")
    non_public = [address for address in addresses if not _is_public_ip(address)]
    if non_public:
        raise InvalidURLError(
            f"Host {hostname!r} resolves to non-public address(es): {non_public[:3]}"
        )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def fetch_html(url: str, settings: Settings | None = None) -> Tuple[str, str]:
    """Download a URL safely. Returns (final_url, html_text)."""
    settings = settings or get_settings()
    current = validate_url(url, settings)

    timeout = httpx.Timeout(settings.http_timeout, connect=min(settings.http_timeout, 10.0))
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    for _hop in range(settings.max_redirects + 1):
        assert_public_host(current, settings)
        try:
            with httpx.Client(
                follow_redirects=False, timeout=timeout, headers=headers
            ) as client:
                with client.stream("GET", current) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise ArticleExtractionError(
                                f"Redirect without Location header from {current}"
                            )
                        current = validate_url(urljoin(current, location), settings)
                        continue

                    if response.status_code >= 400:
                        raise ArticleExtractionError(
                            f"HTTP {response.status_code} while fetching {current}"
                        )

                    content_type = (response.headers.get("content-type") or "").lower()
                    if content_type and not any(
                        content_type.startswith(allowed)
                        for allowed in _ALLOWED_CONTENT_TYPES
                    ):
                        raise ArticleExtractionError(
                            f"Unsupported content type {content_type!r} at {current}"
                        )

                    declared_length = response.headers.get("content-length")
                    if declared_length and declared_length.isdigit():
                        if int(declared_length) > settings.max_download_bytes:
                            raise ArticleExtractionError(
                                f"Response too large ({declared_length} bytes > "
                                f"{settings.max_download_bytes} byte limit)"
                            )

                    raw = bytearray()
                    for chunk in response.iter_bytes(chunk_size=16384):
                        raw.extend(chunk)
                        if len(raw) > settings.max_download_bytes:
                            raise ArticleExtractionError(
                                f"Response exceeded {settings.max_download_bytes} byte limit"
                            )
                    encoding = response.charset_encoding or _sniff_encoding(bytes(raw))
                    return current, bytes(raw).decode(encoding, errors="replace")
        except httpx.TimeoutException as exc:
            raise ArticleExtractionError(f"Timed out fetching {current}") from exc
        except httpx.HTTPError as exc:
            raise ArticleExtractionError(
                f"Failed to fetch {current}: {type(exc).__name__}"
            ) from exc

    raise ArticleExtractionError(f"Too many redirects while fetching {url}")


def _sniff_encoding(raw: bytes, default: str = "utf-8") -> str:
    head = raw[:2048].decode("ascii", errors="ignore").lower()
    match = re.search(r"charset=[\"']?([a-z0-9\-_]+)", head)
    if match:
        return match.group(1)
    return default


# ---------------------------------------------------------------------------
# HTML -> article text
# ---------------------------------------------------------------------------


def _extract_with_trafilatura(html: str, url: str | None) -> str:
    try:
        import trafilatura
    except ImportError:  # pragma: no cover - trafilatura is a hard requirement
        return ""
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=True,
            include_links=False,
            favor_recall=True,
            deduplicate=True,
            output_format="txt",
        )
    except Exception:  # pragma: no cover - malformed HTML edge cases
        return ""
    return extracted or ""


def _extract_with_beautifulsoup(html: str) -> str:
    """Fallback: strip chrome, then take the densest article-like container."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:  # pragma: no cover
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "nav",
            "header",
            "footer",
            "aside",
            "form",
            "iframe",
            "svg",
            "figure",
            "button",
            "input",
            "select",
            "textarea",
        ]
    ):
        tag.decompose()

    # Drop obvious non-content blocks by class/id.
    junk_pattern = re.compile(
        r"(nav|menu|sidebar|footer|header|advert|ads?|promo|banner|cookie|consent|"
        r"social|share|comment|related|recommend|newsletter|subscribe|popup|modal)",
        re.IGNORECASE,
    )
    for tag in soup.find_all(attrs={"class": junk_pattern}):
        tag.decompose()
    for tag in soup.find_all(attrs={"id": junk_pattern}):
        tag.decompose()

    candidates = []
    for tag in soup.find_all("article"):
        candidates.append(tag)
    for attrs in (
        {"class": re.compile(r"(article|story|post|entry|content|body|main|text)", re.IGNORECASE)},
        {"id": re.compile(r"(article|story|post|entry|content|body|main|text)", re.IGNORECASE)},
        {"role": "main"},
    ):
        candidates.extend(soup.find_all(attrs=attrs))
    if soup.body:
        candidates.append(soup.body)
    if soup:
        candidates.append(soup)

    def _text_of(node) -> str:
        paragraphs = [p.get_text(" ", strip=True) for p in node.find_all("p")]
        paragraphs = [p for p in paragraphs if len(p) > 40]
        if paragraphs:
            return "\n".join(paragraphs)
        return node.get_text("\n", strip=True)

    best_text, best_score = "", 0
    for node in candidates:
        text = _text_of(node)
        score = len(text)
        if score > best_score:
            best_text, best_score = text, score
    return best_text


_BYLINE_RE = re.compile(
    r"^(by\s+[A-Z][\w\.\-']+(?:\s+[A-Z][\w\.\-']+){0,3})\s*$", re.IGNORECASE
)


def clean_article_text(text: str) -> str:
    """Remove boilerplate lines and normalise whitespace."""
    if not text:
        return ""
    text = normalize_text(text)
    kept = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _BOILERPLATE_LINE_RE.match(stripped):
            continue
        if _BYLINE_RE.match(stripped):
            continue
        if len(stripped) < 3 and not stripped.endswith((".", "!", "?")):
            continue
        kept.append(stripped)
    cleaned = "\n".join(kept)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def extract_article_from_url(url: str, settings: Settings | None = None) -> str:
    """Fetch and extract the main article text from `url`.

    Trafilatura is the primary extractor; BeautifulSoup is the fallback.

    Raises:
        InvalidURLError: URL rejected by validation / SSRF policy.
        ArticleExtractionError: fetch failed, or no usable article text found.
    """
    settings = settings or get_settings()

    final_url, html = fetch_html(url, settings=settings)

    article = clean_article_text(_extract_with_trafilatura(html, final_url))
    extractor = "trafilatura"

    if len(article) < settings.min_article_chars:
        fallback = clean_article_text(_extract_with_beautifulsoup(html))
        if len(fallback) > len(article):
            article, extractor = fallback, "beautifulsoup"

    if len(article) < settings.min_article_chars:
        raise ArticleExtractionError(
            f"Could not extract article content from {final_url} "
            f"(best extractor: {extractor}, {len(article)} chars "
            f"< {settings.min_article_chars} required)"
        )

    return article
