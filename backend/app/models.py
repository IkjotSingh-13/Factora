from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class Stance(str, Enum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    NEUTRAL = "NEUTRAL"


class Verdict(str, Enum):
    SUPPORTED = "SUPPORTED"
    REFUTED = "REFUTED"
    CONFLICTING_EVIDENCE = "CONFLICTING EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT EVIDENCE"
    MIXED = "MIXED / PARTIALLY SUPPORTED"


class ClaimInput(BaseModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    type: str = "other"
    article_quote: Optional[str] = None
    time_sensitive: bool = False


class ClaimExtractionRequest(BaseModel):
    """S1 input: provide exactly one article source."""

    article_text: str | None = Field(default=None, min_length=1, max_length=200_000)
    url: str | None = Field(default=None, min_length=1, max_length=2_048)

    @model_validator(mode="after")
    def validate_source(self) -> "ClaimExtractionRequest":
        if self.article_text is not None and not self.article_text.strip():
            raise ValueError("article_text must contain non-whitespace text")
        if self.url is not None and not self.url.strip():
            raise ValueError("url must contain non-whitespace text")
        if (self.article_text is None) == (self.url is None):
            raise ValueError("provide exactly one of article_text or url")
        if self.article_text is not None:
            self.article_text = self.article_text.strip()
        if self.url is not None:
            self.url = self.url.strip()
        return self


class ExtractedClaim(BaseModel):
    """S1 output normalized to the API's ``type`` field, with entities kept."""

    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    type: str = Field(min_length=1)
    entities: list[str] = Field(default_factory=list)


class ClaimExtractionResponse(BaseModel):
    source_type: Literal["article_text", "url"]
    claims: list[ExtractedClaim]
    stage: Literal["S1_EXTRACTION_ONLY"] = "S1_EXTRACTION_ONLY"
    note: str = (
        "Claim extraction only. No evidence retrieval, stance analysis, or verdict was produced."
    )


class EvidenceItem(BaseModel):
    id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    url: str = ""
    domain: str = ""
    title: str = ""
    snippet: str = ""
    full_text: str = ""
    published_at: Optional[date] = None
    source_kind: str = "web"
    relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    credibility: float = Field(default=0.35, ge=0.0, le=1.0)
    stance: Stance = Stance.NEUTRAL
    quote: str = ""
    quote_verified: bool = False
    rationale: str = ""
    context_score: float = Field(default=0.7, ge=0.0, le=1.0)
    context_gap: bool = False


class ClaimEvidenceBundle(BaseModel):
    claim: ClaimInput
    evidence: list[EvidenceItem] = Field(default_factory=list)


class InputMetadata(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    published: Optional[date] = None


class ScoreRequest(BaseModel):
    input: InputMetadata = Field(default_factory=InputMetadata)
    claims: list[ClaimEvidenceBundle] = Field(min_length=1)
    as_of: Optional[date] = None


class ScoreComponents(BaseModel):
    C: float = Field(ge=0.0, le=1.0)
    R: float = Field(ge=0.0, le=1.0)
    A: float = Field(ge=0.0, le=1.0)
    T: float = Field(ge=0.0, le=1.0)
    K: float = Field(ge=0.0, le=1.0)


class AuditSummary(BaseModel):
    n_retrieved: int = Field(ge=0)
    n_used: int = Field(ge=0)
    n_independent: int = Field(ge=0)
    hallucinated_quotes_dropped: int = Field(ge=0)
    unknown_freshness: bool = False
    sources_failed: list[str] = Field(default_factory=list)


class ClaimReport(BaseModel):
    claim: ClaimInput
    verdict: Verdict
    # Signed direction: -1 is refute-heavy, 0 is balanced/no stance, +1 is support-heavy.
    stance_score: float = Field(ge=-1.0, le=1.0)
    # Evidence strength is an evidence-quality measure, not a probability of truth.
    evidence_strength: float = Field(ge=0.0, le=1.0)
    score_raw: float = Field(ge=0.0, le=1.0)
    shrinkage_factor: float = Field(ge=0.0, le=1.0)
    scores: ScoreComponents
    contributions: dict[str, float]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    audit: AuditSummary


class VerificationReport(BaseModel):
    input: InputMetadata
    claims: list[ClaimReport]
    disclaimer: str = (
        "Evidence-weighted assessment, not a guaranteed truth verdict. "
        "Review the cited sources and context."
    )
