"""Shared handoff contract (frozen by hour 1).

OWNER OF THIS FILE: Team Lead (API & Scoring).
Mansimran's retrieval module only *produces* these objects; it never renames
fields. If a field must change, the lead changes it here and everyone re-syncs.
Keep field names stable after the first integration.

Contract (from the 24h plan):
  Claim:    id, text, type, article_quote, time_sensitive, entities
  Evidence: claim_id, url, domain, title, text_or_snippet, published_at,
            source_kind, relevance
  Analysis: stance, quote, quote_verified, rationale, context_gap
  Claim report: claim, verdict, stance_direction, evidence_strength,
                scores, evidence[], audit
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal["encyclopedia", "web", "fact_check", "academic", "news", "mock"]


class Evidence(BaseModel):
    """One normalized piece of retrieved evidence. Produced by S2 (Mansimran)."""

    model_config = ConfigDict(extra="ignore")

    claim_id: Optional[str] = None
    url: str
    domain: str
    title: str = ""
    text_or_snippet: str = ""
    published_at: Optional[str] = None  # ISO-8601 date/datetime string, else None
    source_kind: SourceKind = "web"
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)


class SourceStatus(BaseModel):
    """Audit record for one source adapter on one retrieval run."""

    name: str
    kind: str = "web"
    status: Literal["ok", "empty", "error", "timeout", "skipped"] = "ok"
    results: int = 0
    used: int = 0
    latency_ms: int = 0
    cached: bool = False
    query: str = ""
    error: Optional[str] = None


class RetrievalAudit(BaseModel):
    """Everything the report/audit footer needs from the retrieval stage."""

    queries: list[str] = Field(default_factory=list)
    sources: list[SourceStatus] = Field(default_factory=list)
    sources_queried: int = 0
    sources_ok: int = 0
    sources_failed: int = 0
    candidates_total: int = 0
    dropped_low_coverage: int = 0
    coverage_by_evidence: dict[str, float] = Field(default_factory=dict)
    mean_coverage: float = 0.0
    dropped_duplicates: int = 0
    dropped_over_domain_cap: int = 0
    returned: int = 0
    distinct_domains: int = 0
    cache_hits: int = 0
    duration_ms: int = 0
    notes: list[str] = Field(default_factory=list)

    @property
    def failed_source_names(self) -> list[str]:
        return [s.name for s in self.sources if s.status in ("error", "timeout")]


class RetrievalResult(BaseModel):
    """S2 -> S3/API payload."""

    claim_id: Optional[str] = None
    claim_text: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    audit: RetrievalAudit = Field(default_factory=RetrievalAudit)

    # Convenience for the API layer; the lead owns the "insufficient evidence"
    # policy, this is only a raw signal.
    @property
    def has_evidence(self) -> bool:
        return len(self.evidence) > 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
