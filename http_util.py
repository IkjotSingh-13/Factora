"""HTTP with hard timeouts, bounded retries and optional caching.

Rule from the plan: **every external call fails soft.** This module never lets a
network problem escape as anything other than a ``SourceError``, which the
retrieval engine records in the audit and then ignores.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from cache import Cache, make_key

log = logging.getLogger("verifyai.http")

USER_AGENT = (
    "VerifyAI-Hackathon/0.2 (evidence retrieval for claim verification; "
    "non-commercial demo)"
)

_thread_local = threading.local()


class SourceError(Exception):
    """Raised for any unrecoverable problem talking to a source."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class FetchResult:
    data: Any
    status_code: int
    latency_ms: int
    cached: bool = False


def _session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        _thread_local.session = session
    return session


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
    retries: int = 1,
    backoff: float = 0.6,
    cache: Optional[Cache] = None,
    cache_ttl_s: int = 600,
) -> FetchResult:
    key = None
    if cache is not None:
        key = make_key(method, url, params, data, json_body)
        hit = cache.get(key)
        if hit is not None:
            return FetchResult(data=hit["body"], status_code=hit["status"], latency_ms=0, cached=True)

    last_error: Exception | None = None
    started = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            response = _session().request(
                method,
                url,
                params=params,
                data=data,
                json=json_body,
                headers=headers,
                timeout=(min(4.0, timeout), timeout),
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise SourceError(
                    f"HTTP {response.status_code} from {url}", status_code=response.status_code
                )
            if response.status_code >= 400:
                # 4xx other than 429: retrying will not help
                raise SourceError(
                    f"HTTP {response.status_code} from {url}", status_code=response.status_code
                )
            latency = int((time.perf_counter() - started) * 1000)
            try:
                body = response.json()
            except ValueError:
                body = response.text
            if cache is not None and key is not None:
                cache.set(key, {"body": body, "status": response.status_code})
            return FetchResult(data=body, status_code=response.status_code, latency_ms=latency)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            log.info("network error (attempt %d/%d) %s: %s", attempt + 1, retries + 1, url, exc)
        except SourceError as exc:
            if exc.status_code is not None and exc.status_code < 500 and exc.status_code != 429:
                raise
            last_error = exc
            log.info("source error (attempt %d/%d) %s: %s", attempt + 1, retries + 1, url, exc)
        if attempt < retries:
            time.sleep(backoff * (2**attempt) + random.uniform(0, 0.15))

    raise SourceError(f"request failed for {url}: {last_error}")


def request_json(url: str, **kwargs: Any) -> FetchResult:
    result = _request("GET", url, **kwargs)
    if not isinstance(result.data, (dict, list)):
        raise SourceError(f"expected JSON from {url}")
    return result


def request_text(url: str, **kwargs: Any) -> FetchResult:
    return _request("GET", url, **kwargs)


def post_form(url: str, form: dict[str, Any], **kwargs: Any) -> FetchResult:
    return _request("POST", url, data=form, **kwargs)


def post_json(url: str, body: dict[str, Any], **kwargs: Any) -> FetchResult:
    """POST a JSON body; used by Tavily-style search APIs."""
    kwargs.setdefault("headers", {})
    kwargs["headers"] = {**kwargs["headers"], "Content-Type": "application/json"}
    result = _request("POST", url, json_body=body, **kwargs)
    if not isinstance(result.data, (dict, list)):
        raise SourceError(f"expected JSON from {url}")
    return result
