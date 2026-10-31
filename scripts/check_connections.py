#!/usr/bin/env python3
"""Report what this machine can actually reach, and what to do about the rest.

Every integration VerifyAI has — local model server, optional search APIs, keyless
sources, the API process itself — is probed here, in one command, with a remediation
line per failure. This exists because the number one cause of "the demo didn't work"
is an environment that was never checked: a model that was never pulled, a key in the
wrong shell, a server on another port.

    python scripts/check_connections.py            # everything, ~10 s worst case
    python scripts/check_connections.py --only ollama,tavily
    python scripts/check_connections.py --strict    # exit 1 if anything failed

Design rules, deliberately the same as the app's:
* nothing here is required. An absent key disables a source; it does not "fail" the run.
* no secret is ever printed — only whether it is set, and its length.
* every probe has a timeout and a fix line, because a red row without advice is noise.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK, DEGRADED, ABSENT, FAIL, INFO = "ok", "degraded", "absent", "fail", "info"
GLYPH = {OK: "[ ok ]", DEGRADED: "[warn]", ABSENT: "[ - ]", FAIL: "[FAIL]", INFO: "[info]"}


@dataclass
class Result:
    name: str
    status: str
    detail: str
    fix: str = ""

    @property
    def glyph(self) -> str:
        return GLYPH.get(self.status, "[?]")


def _get(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "VerifyAI-doctor/1.0", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed hosts
        return response.status, response.read(64_000).decode("utf-8", "replace")


def _post_json(url: str, payload: dict[str, Any], *, timeout: float) -> tuple[int, str]:
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": "VerifyAI-doctor/1.0"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return response.status, response.read(200_000).decode("utf-8", "replace")


# ------------------------------------------------------------------ probes
def probe_ollama(host: str, model: str, *, timeout: float = 6.0, generate: bool = True) -> Result:
    """Ollama: server reachable, model pulled, and one token of inference working.

    Split into three findings on purpose. "Ollama is up" and "your model is there" and
    "inference answers in reasonable time" fail independently and need different fixes.
    """
    base = host.rstrip("/")
    try:
        status, text = _get(f"{base}/api/tags", timeout=timeout)
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return Result(
            "ollama · server",
            ABSENT,
            f"nothing answering on {base} ({type(reason).__name__})",
            f"start it:  ollama serve   (then re-run this). For a laptop GPU: OLLAMA_HOST=0.0.0.0 {base}",
        )
    if status != 200:
        return Result("ollama · server", FAIL, f"{base}/api/tags returned HTTP {status}", "is something else on that port?")

    try:
        models = [m.get("name", "") for m in (json.loads(text).get("models") or [])]
    except (ValueError, AttributeError):
        return Result("ollama · server", FAIL, "reachable but /api/tags was not the JSON Ollama serves", "check a proxy in front of it")

    wanted = model.lower()
    have = any(name.lower() == wanted or name.split(":")[0].lower() == wanted.split(":")[0] for name in models)
    if not have:
        return Result(
            "ollama · model",
            DEGRADED,
            f"'{model}' not pulled (available: {', '.join(models[:6]) or 'none'})",
            f"ollama pull {model}",
        )
    if not generate:
        return Result("ollama · model", OK, f"'{model}' present, inference not exercised (--no-generate)")
    try:
        status, text = _post_json(
            f"{base}/api/chat",
            {"model": model, "messages": [{"role": "user", "content": "Reply with exactly {\"ok\":true}"}],
             "stream": False, "format": "json", "options": {"temperature": 0, "num_predict": 16}},
            timeout=max(timeout, 25.0),
        )
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        return Result("ollama · inference", FAIL, f"chat request failed: {type(exc).__name__}", "try: ollama run " + model)
    if status != 200:
        return Result("ollama · inference", FAIL, f"HTTP {status} from /api/chat", "check `ollama serve` logs")
    try:
        content = json.loads(text).get("message", {}).get("content", "")
    except ValueError:
        return Result("ollama · inference", FAIL, "/api/chat did not return JSON while format=json was set", "some builds need `ollama pull` again")
    if not content.strip():
        return Result("ollama · inference", DEGRADED, "empty completion — model answered with nothing", "raise OLLAMA_TIMEOUT or pick a smaller model")
    return Result("ollama · inference", OK, f"'{model}' answered in one call ({len(content)} chars)")


def probe_gemini(key: str, model: str, *, timeout: float = 8.0, base_url: str = "") -> Result:
    if not key:
        return Result("gemini · key", ABSENT, "GEMINI_API_KEY not set", "set it in .env, or use LLM_PROVIDER=ollama for a local model")
    root = (base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    try:
        status, text = _get(f"{root}/models?key={urllib.parse.quote(key)}", timeout=timeout)
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return Result("gemini · key", FAIL, f"could not reach the API ({type(exc).__name__})", "no network, or a proxy blocking it")
    if status != 200:
        return Result("gemini · key", FAIL, f"HTTP {status} listing models — the key was refused", "check the key, and that the Generative Language API is enabled in the project")
    named = f"models/{model}" in text or model in text
    return Result(
        "gemini · model",
        OK if named else DEGRADED,
        f"key accepted; {model} {'listed' if named else 'not in the model list'}",
        "" if named else "GEMINI_MODEL=<one of the listed generateContent models>",
    )


def probe_tavily(key: str, *, timeout: float = 10.0) -> Result:
    if not key:
        return Result("tavily · key", ABSENT, "TAVILY_API_KEY not set", "free tier: tavily.com → API keys; without it the source is skipped, not failed")
    try:
        status, _ = _post_json(
            "https://api.tavily.com/search",
            {"api_key": key, "query": "Eiffel Tower height metres", "max_results": 1, "include_answer": False},
            timeout=timeout,
        )
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return Result("tavily · search", FAIL, f"request failed ({type(exc).__name__})", "network, or an expired key")
    if status == 401:
        return Result("tavily · search", FAIL, "key rejected (401)", "regenerate the key")
    if status >= 400:
        return Result("tavily · search", DEGRADED, f"HTTP {status}", "rate limited? the app fails soft either way")
    return Result("tavily · search", OK, "one test query returned results")


def probe_factcheck(key: str, *, timeout: float = 10.0) -> Result:
    if not key:
        return Result("google fact check · key", ABSENT, "GOOGLE_FACTCHECK_API_KEY not set", "Claim Review Tools API console page; highest-value source for this app")
    url = ("https://factchecktools.googleapis.com/v1alpha1/claims:search?query=Eiffel%20Tower%20height&key="
           + urllib.parse.quote(key))
    try:
        status, _ = _get(url, timeout=timeout)
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return Result("google fact check · search", FAIL, f"request failed ({type(exc).__name__})", "network or quota")
    if status == 403:
        return Result("google fact check · search", FAIL, "key refused (403)", "enable the API for that key")
    if status >= 400:
        return Result("google fact check · search", DEGRADED, f"HTTP {status}", "the run continues without it")
    return Result("google fact check · search", OK, "claims:search answered")


def probe_keyless(name: str, url: str, *, timeout: float = 8.0, expect: str = "") -> Result:
    try:
        status, text = _get(url, timeout=timeout)
    except (urllib.error.URLError, socket.timeout, OSError) as exc:
        return Result(name, FAIL, f"{type(exc).__name__} reaching it", "offline? the app degrades to the local corpus")
    if status >= 400:
        return Result(name, DEGRADED, f"HTTP {status}", "site throttling; the run continues")
    if expect and expect.lower() not in text.lower():
        return Result(name, DEGRADED, f"reached, but no '{expect}' in the reply", "API shape changed?")
    return Result(name, OK, f"reachable (HTTP {status}, keyless)")


def probe_api(base: str, *, timeout: float = 4.0) -> tuple[Result, Result]:
    """The running VerifyAI process, and what it says it will do."""
    try:
        status, text = _get(f"{base.rstrip('/')}/health", timeout=timeout)
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as exc:
        absent = Result(
            "api · process", ABSENT, f"nothing on {base}",
            "python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000",
        )
        return absent, Result("api · config", ABSENT, "not queried", "")
    health = Result("api · process", OK if status == 200 else FAIL, f"/health HTTP {status}: {text[:60]}")
    try:
        _, raw = _get(f"{base.rstrip('/')}/api/config", timeout=timeout)
        config = json.loads(raw)
    except (urllib.error.URLError, socket.timeout, ValueError, OSError) as exc:
        return health, Result("api · config", FAIL, f"/api/config unreadable ({type(exc).__name__})", "")
    lines = ", ".join(f"{k}={v}" for k, v in sorted(config.items()) if k != "disclaimer")
    status2 = OK if config.get("s1_provider") not in (None, "") else INFO
    return health, Result("api · config", status2, lines)


# ------------------------------------------------------------------- runner
def all_probes(*, generate: bool = True, strict_ollama: bool = False) -> list[Result]:
    try:
        from backend.config import get_settings  # noqa: PLC0415 - optional, this script must still run without it
        settings = get_settings()
    except Exception:  # noqa: BLE001 - a missing dependency must not hide the network results
        settings = None

    def cfg(name: str, default: str) -> str:
        value = getattr(settings, name, None) if settings is not None else None
        return str(value or os.getenv(name.upper(), default))

    env = lambda key, default="": (os.getenv(key) or getattr(settings, key.lower(), "") or default)  # noqa: E731

    out: list[Result] = []
    out.append(probe_ollama(env("OLLAMA_HOST", "http://localhost:11434"), env("OLLAMA_MODEL", "qwen2.5:7b"), generate=generate))
    out.append(probe_gemini(env("GEMINI_API_KEY"), env("GEMINI_MODEL", "gemini-2.0-flash")))
    out.append(probe_tavily(env("TAVILY_API_KEY")))
    out.append(probe_factcheck(env("GOOGLE_FACTCHECK_API_KEY")))
    out.append(probe_keyless(
        "wikipedia · api",
        "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=Eiffel%20Tower&format=json&srlimit=1",
        expect="query",
    ))
    out.append(probe_keyless("openalex · api", "https://api.openalex.org/works?per-page=1&mailto=team@verifyai.local", expect="results"))
    if strict_ollama:
        out.append(Result("ollama · mode", INFO, f"LLM_PROVIDER={env('LLM_PROVIDER', 'mock')}, S3_MODE={env('S3_MODE', 'auto')}",
                          "for local-model stance analysis: LLM_PROVIDER=ollama and S3_MODE=llm"))
    health, config = probe_api(env("VERIFY_API_BASE", "http://127.0.0.1:8000"))
    out.extend([health, config])
    if settings is None:
        out.append(Result("settings", DEGRADED, "backend.config not importable in this interpreter", "activate the venv: source .venv/bin/activate"))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe every external dependency VerifyAI uses")
    parser.add_argument("--only", default="", help="comma list: ollama,gemini,tavily,factcheck,wikipedia,openalex,api")
    parser.add_argument("--no-generate", action="store_true", help="do not spend an inference call on the model check")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any probe failed (absent is still fine)")
    args = parser.parse_args(argv)

    wanted = {w.strip() for w in args.only.split(",") if w.strip()}
    results = [r for r in all_probes(generate=not args.no_generate, strict_ollama=True)
               if not wanted or any(w in r.name for w in wanted)]

    if args.json:
        print(json.dumps([{"name": r.name, "status": r.status, "detail": r.detail, "fix": r.fix} for r in results], indent=2))
    else:
        print("\nVerifyAI connection report")
        print("=" * 78)
        for r in results:
            print(f"{r.glyph} {r.name:24s} {r.detail[:44]}")
            if r.fix and r.status in (FAIL, DEGRADED, ABSENT):
                print(f"{'':7s}→ {r.fix}")
        counts = {s: sum(1 for r in results if r.status == s) for s in (OK, DEGRADED, ABSENT, FAIL)}
        print("-" * 78)
        print(f"{counts[OK]} ok · {counts[DEGRADED]} degraded · {counts[ABSENT]} absent · {counts[FAIL]} failed")
        print("Absent is a supported state: every one of these fails soft in the app.")
        print("The demo runs offline with nothing set at all (mode=offline in the UI).")

    if args.strict and any(r.status == FAIL for r in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
