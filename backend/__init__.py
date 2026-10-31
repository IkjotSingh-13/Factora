"""VerifyAI — S1 Claim Extraction module (Ikjot).

Public surface (integration-friendly):

    from backend import (
        Claim,
        extract_claims,
        extract_article_from_url,
        extract_claims_from_url,
        extract_entities,
        validate_claim,
    )

S1 is responsible ONLY for producing atomic, independently verifiable claims
from an article. Evidence retrieval (S2), stance (S3), scoring (S4) and
reporting (S5) are explicitly out of scope.
"""

from .article_extractor import extract_article_from_url
from .errors import (
    ArticleExtractionError,
    ClaimExtractionError,
    EmptyArticleError,
    InvalidURLError,
    LLMError,
    LLMOutputError,
)
from .s1_claims import (
    MAX_CLAIMS,
    MIN_TARGET_CLAIMS,
    extract_claims,
    extract_claims_detailed,
    extract_claims_from_url,
)
from .schemas import ALLOWED_CLAIM_TYPES, Claim
from .validators import extract_entities, validate_claim

__all__ = [
    "Claim",
    "ALLOWED_CLAIM_TYPES",
    "extract_claims",
    "extract_claims_detailed",
    "extract_claims_from_url",
    "extract_article_from_url",
    "extract_entities",
    "validate_claim",
    "MAX_CLAIMS",
    "MIN_TARGET_CLAIMS",
    "ArticleExtractionError",
    "ClaimExtractionError",
    "EmptyArticleError",
    "InvalidURLError",
    "LLMError",
    "LLMOutputError",
]

__version__ = "1.0.0"
