"""Configuration for the S1 Claim Extraction module.

All values come from the environment (optionally via a .env file) so that no
secret is ever hardcoded and the module can run in MOCK mode with zero config.

    from backend.config import get_settings
    settings = get_settings()
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

# Load .env once at import time (no-op if the file is absent).
load_dotenv(override=False)

VALID_PROVIDERS = ("gemini", "ollama", "mock")


def _env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_opt_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_int(name: str, default: int) -> int:
    raw = _env_opt_str(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env_opt_str(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env_opt_str(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on", "y")


def _env_list(name: str, default: List[str]) -> List[str]:
    raw = _env_opt_str(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    """Immutable-ish settings snapshot built from the environment."""

    # --- Provider selection -------------------------------------------------
    # "gemini" (remote, primary) | "ollama" (local) | "mock" (offline, no key)
    llm_provider: str = field(default_factory=lambda: _env_str("LLM_PROVIDER", "mock").lower())
    # If the configured provider fails, fall back to the deterministic
    # heuristic extractor instead of raising.
    llm_fallback_to_heuristics: bool = field(
        default_factory=lambda: _env_bool("S1_LLM_FALLBACK", True)
    )
    llm_max_output_chars: int = field(default_factory=lambda: _env_int("S1_LLM_MAX_OUTPUT_CHARS", 20000))

    # --- Gemini -------------------------------------------------------------
    gemini_api_key: str | None = field(default_factory=lambda: _env_opt_str("GEMINI_API_KEY"))
    gemini_model: str = field(default_factory=lambda: _env_str("GEMINI_MODEL", "gemini-2.0-flash"))
    gemini_base_url: str = field(
        default_factory=lambda: _env_str(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        )
    )
    gemini_timeout: float = field(default_factory=lambda: _env_float("GEMINI_TIMEOUT", 45.0))

    # --- Ollama -------------------------------------------------------------
    ollama_host: str = field(default_factory=lambda: _env_str("OLLAMA_HOST", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: _env_str("OLLAMA_MODEL", "qwen2.5:7b"))
    ollama_timeout: float = field(default_factory=lambda: _env_float("OLLAMA_TIMEOUT", 120.0))

    # --- Extraction behaviour ----------------------------------------------
    min_claims: int = field(default_factory=lambda: _env_int("S1_MIN_CLAIMS", 3))
    max_claims: int = field(default_factory=lambda: _env_int("S1_MAX_CLAIMS", 6))
    # Articles shorter than this many words are treated as "very short": we
    # return whatever survives validation instead of forcing extra claims.
    min_article_words: int = field(default_factory=lambda: _env_int("S1_MIN_ARTICLE_WORDS", 8))
    min_claim_tokens: int = field(default_factory=lambda: _env_int("S1_MIN_CLAIM_TOKENS", 3))
    max_claim_tokens: int = field(default_factory=lambda: _env_int("S1_MAX_CLAIM_TOKENS", 45))
    # Sentences beyond this count are not sent to the LLM (cost/context guard).
    max_sentences_to_llm: int = field(default_factory=lambda: _env_int("S1_MAX_SENTENCES", 120))

    # --- Validation thresholds ---------------------------------------------
    lexical_recall_threshold: float = field(
        default_factory=lambda: _env_float("S1_LEXICAL_RECALL", 0.55)
    )
    enable_number_check: bool = field(default_factory=lambda: _env_bool("S1_CHECK_NUMBERS", True))
    enable_entity_grounding: bool = field(default_factory=lambda: _env_bool("S1_CHECK_ENTITIES", True))
    enable_hedge_check: bool = field(default_factory=lambda: _env_bool("S1_CHECK_HEDGES", True))
    enable_qualifier_check: bool = field(
        default_factory=lambda: _env_bool("S1_CHECK_QUALIFIERS", True)
    )
    enable_dense_propositions: bool = field(
        default_factory=lambda: _env_bool("S1_DENSE_PROPOSITIONS", True)
    )

    # --- HTTP / article fetching -------------------------------------------
    http_timeout: float = field(default_factory=lambda: _env_float("S1_HTTP_TIMEOUT", 15.0))
    max_download_bytes: int = field(
        default_factory=lambda: _env_int("S1_MAX_DOWNLOAD_BYTES", 5 * 1024 * 1024)
    )
    user_agent: str = field(
        default_factory=lambda: _env_str(
            "S1_USER_AGENT",
            "VerifyAI-S1/1.0 (+claim-extraction; contact: team@verifyai.local)",
        )
    )
    allowed_schemes: List[str] = field(default_factory=lambda: ["http", "https"])
    allowed_ports: List[int] = field(default_factory=lambda: [80, 443, 8080, 8443])
    # Extra hosts to deny outright (comma separated), on top of the built-in
    # private/loopback/metadata blocklist.
    blocked_hosts: List[str] = field(default_factory=lambda: _env_list("S1_BLOCKED_HOSTS", []))
    # Set False in production if you want to forbid same-host redirects too.
    max_redirects: int = field(default_factory=lambda: _env_int("S1_MAX_REDIRECTS", 5))
    min_article_chars: int = field(default_factory=lambda: _env_int("S1_MIN_ARTICLE_CHARS", 200))

    # --- spaCy --------------------------------------------------------------
    spacy_model: str = field(default_factory=lambda: _env_str("SPACY_MODEL", "en_core_web_sm"))

    def validate(self) -> None:
        """Fail fast on obviously broken configuration."""
        if self.llm_provider not in VALID_PROVIDERS:
            raise ValueError(
                f"Invalid LLM_PROVIDER={self.llm_provider!r}; "
                f"expected one of {VALID_PROVIDERS}"
            )
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ValueError(
                "LLM_PROVIDER=gemini requires GEMINI_API_KEY. "
                "Set it in .env or use LLM_PROVIDER=mock."
            )
        if self.min_claims < 1 or self.max_claims < self.min_claims:
            raise ValueError("Require 1 <= S1_MIN_CLAIMS <= S1_MAX_CLAIMS")
        if not 0.0 <= self.lexical_recall_threshold <= 1.0:
            raise ValueError("S1_LEXICAL_RECALL must be within [0, 1]")


def get_settings(reload: bool = False) -> Settings:
    """Return a (cached) Settings instance. Pass reload=True after changing env."""
    global _SETTINGS
    if reload or _SETTINGS is None:
        _SETTINGS = Settings()
        _SETTINGS.validate()
    return _SETTINGS


_SETTINGS: Settings | None = None
