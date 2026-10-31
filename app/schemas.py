from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, HttpUrl
from datetime import datetime
from enum import Enum


class ClaimType(str, Enum):
    FACTUAL = "factual"
    STATISTICAL = "statistical"
    CAUSAL = "causal"
    DEFINITIONAL = "definitional"


class Stance(str, Enum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    NEUTRAL = "NEUTRAL"


class SourceKind(str, Enum):
    NEWS = "news"
    ACADEMIC = "academic"
    WIKIPEDIA = "wikipedia"
    FACT_CHECK = "fact_check"
    GOVERNMENT = "government"
    OTHER = "other"


class Claim(BaseModel):
    id: str
    text: str
    type: ClaimType
    article_quote: str
    time_sensitive: bool = False
    entities: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    claim_id: str
    url: HttpUrl
    domain: str
    title: str
    text_or_snippet: str
    published_at: Optional[datetime] = None
    source_kind: SourceKind
    relevance: float = Field(ge=0.0, le=1.0)


class Analysis(BaseModel):
    stance: Stance
    quote: str
    quote_verified: bool
    rationale: str
    context_gap: Optional[str] = None


class ScoreBreakdown(BaseModel):
    C: float = Field(description="Source credibility", ge=0.0, le=1.0)
    R: float = Field(description="Evidence relevance", ge=0.0, le=1.0)
    A: float = Field(description="Independent agreement / source diversity", ge=0.0, le=1.0)
    T: float = Field(description="Freshness", ge=0.0, le=1.0)
    K: float = Field(description="Contextual consistency", ge=0.0, le=1.0)
    S: float = Field(description="Final evidence strength score", ge=0.0, le=1.0)

    # Component explanations
    C_explanation: str = ""
    R_explanation: str = ""
    A_explanation: str = ""
    T_explanation: str = ""
    K_explanation: str = ""


class ClaimReport(BaseModel):
    claim: Claim
    verdict: Stance
    stance_direction: Literal["supports", "refutes", "mixed", "insufficient"]
    evidence_strength: ScoreBreakdown
    evidence: list[Evidence]
    analysis: list[Analysis]
    audit: AuditInfo


class AuditInfo(BaseModel):
    sources_queried: list[str]
    sources_failed: list[str]
    total_results_retrieved: int
    results_used: int
    quotes_dropped: int
    quote_drop_reasons: list[str] = Field(default_factory=list)
    mock_mode: bool = False
    processing_time_ms: int = 0


class VerifyRequest(BaseModel):
    text: str = Field(min_length=50, description="Article text to verify")
    url: Optional[HttpUrl] = Field(default=None, description="Optional source URL")


class VerifyResponse(BaseModel):
    reports: list[ClaimReport]
    meta: dict = Field(default_factory=dict)