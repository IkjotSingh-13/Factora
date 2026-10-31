"""Tiny .env loader (no dependency on python-dotenv).

Why this exists: `sources.py` reads API keys from the environment, but nothing
was actually loading `.env`. On Linux/macOS people often `export` things by hand;
on Windows PowerShell they usually don't, and the symptom is a silent
"source skipped: no key" in the audit trail.

Behaviour
---------
* looks for `.env` next to this file, then walks up to the repo root
* `KEY=value`, `KEY = value`, `export KEY=value`, quoted values, `#` comments
* never overwrites a variable that is already set in the real environment
  (so `$env:TAVILY_API_KEY="..."` in PowerShell still wins)
* safe to call multiple times
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("verifyai.env")

_LOADED = False

#: variables this project cares about, used for a friendly startup report
KNOWN_KEYS = (
    "GOOGLE_FACTCHECK_API_KEY",
    "TAVILY_API_KEY",
    "OPENALEX_MAILTO",
    "ENABLE_OPENALEX",
)


def parse_env_text(text: str) -> dict[str, str]:
    """Parse dotenv-format text into a dict (pure function, easy to test)."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # strip trailing inline comment for unquoted values
        if value and not value.startswith(("'", '"')):
            value = value.split(" #", 1)[0].strip()
        if key:
            values[key] = value
    return values


def find_env_file(start: Optional[Path] = None) -> Optional[Path]:
    here = (start or Path(__file__)).resolve()
    if here.is_file():
        here = here.parent
    for directory in [here, *here.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_env(path: str | os.PathLike | None = None, *, override: bool = False) -> dict[str, str]:
    """Load .env into os.environ. Returns the values that were applied."""
    global _LOADED
    if _LOADED and path is None:
        return {}

    env_path = Path(path) if path else find_env_file()
    if env_path is None or not env_path.is_file():
        _LOADED = True
        return {}

    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except OSError as exc:  # unreadable .env must never break a run
        log.info("could not read %s: %s", env_path, exc)
        _LOADED = True
        return {}

    applied: dict[str, str] = {}
    for key, value in parse_env_text(text).items():
        if not override and os.environ.get(key):
            continue
        os.environ[key] = value
        applied[key] = value
    _LOADED = True
    return applied


def env_report() -> str:
    """One-line, key-name-only status (never prints secrets)."""
    parts = []
    for key in KNOWN_KEYS:
        value = os.environ.get(key, "")
        if key == "ENABLE_OPENALEX":
            parts.append(f"{key}={value or '1'}")
        else:
            parts.append(f"{key}={'set' if value else 'unset'}")
    return " | ".join(parts)


def describe_config() -> Iterable[str]:
    yield f"env: {env_report()}"
