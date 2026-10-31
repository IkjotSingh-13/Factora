"""Typed exceptions for the S1 claim-extraction module.

These are intentionally small and explicit so the FastAPI layer can map them to
HTTP status codes without guessing:

    EmptyArticleError / InvalidURLError  -> 400
    ArticleExtractionError               -> 502 (upstream page unusable)
    LLMError / LLMOutputError            -> 502
    ClaimExtractionError                 -> 422 / 500
"""


class S1Error(Exception):
    """Base class for all S1 module errors."""


class EmptyArticleError(S1Error, ValueError):
    """Raised when the supplied article text is empty or too short to be usable."""


class InvalidURLError(S1Error, ValueError):
    """Raised when a URL is malformed, unsupported, or blocked by SSRF policy."""


class ArticleExtractionError(S1Error, RuntimeError):
    """Raised when an article could not be fetched or its body could not be extracted."""


class LLMError(S1Error, RuntimeError):
    """Raised when the configured LLM provider fails (network, auth, timeout, ...)."""


class LLMOutputError(LLMError):
    """Raised when the provider returned output that is not usable claim JSON."""


class ClaimExtractionError(S1Error, RuntimeError):
    """Raised when no valid claims could be produced, even after fallbacks."""
