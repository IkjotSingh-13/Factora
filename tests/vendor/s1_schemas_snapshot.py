"""SNAPSHOT of S1's contract, copied verbatim from ``backend/schemas.py`` on
``main`` (2026-10-31, after /api/extract merged).

S1's ``Claim`` is a pydantic model with ``extra="forbid"`` and exactly four
fields: id, text, entities, claim_type. Vendoring it means our handoff tests
run against the real object, so a change on their side fails our suite.

Refresh with:
    git show origin/main:backend/schemas.py > tests/vendor/s1_schemas_snapshot.py

--- original module docstring follows ---

The mandatory S1 data structures.

`Claim` is the ONLY object that leaves this module, and it has exactly four
fields — by contract and by enforcement (`extra="forbid"`).

Everything else in this file is *internal* bookkeeping (source sentence index,
validation notes, priority score). Those never appear on a Claim instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Literal

from pydantic import BaseModel, ConfigDict, field_validator

# The only permitted claim types, exact lowercase values.
ALLOWED_CLAIM_TYPES = ("factual", "prediction", "opinion", "non_verifiable")
ClaimType = Literal["factual", "prediction", "opinion", "non_verifiable"]


class Claim(BaseModel):
    """An atomic, independently verifiable statement extracted from an article."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str
    text: str
    entities: List[str]
    claim_type: str

    @field_validator("id")
    @classmethod
    def _id_not_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Claim.id must be a non-empty string")
        return value.strip()

    @field_validator("text")
    @classmethod
    def _text_not_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Claim.text must be a non-empty string")
        return " ".join(value.split())

    @field_validator("entities")
    @classmethod
    def _clean_entities(cls, value: List[str]) -> List[str]:
        """De-duplicate case-insensitively while preserving first-seen order."""
        seen: set[str] = set()
        cleaned: List[str] = []
        for item in value or []:
            entity = " ".join(str(item).split())
            if not entity:
                continue
            key = entity.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(entity)
        return cleaned

    @field_validator("claim_type")
    @classmethod
    def _valid_claim_type(cls, value: str) -> str:
        normalized = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in ALLOWED_CLAIM_TYPES:
            raise ValueError(
                f"claim_type must be one of {ALLOWED_CLAIM_TYPES}, got {value!r}"
            )
        return normalized


# ---------------------------------------------------------------------------
# Internal-only structures (never returned to callers)
# ---------------------------------------------------------------------------


@dataclass
class RawClaim:
    """Unvalidated claim exactly as produced by a provider (LLM or heuristics)."""

    text: str
    claim_type: str = "factual"
    source_sentence_index: int | None = None
    entities: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    """Result of strict validation for a single claim (internal diagnostics)."""

    ok: bool
    reasons: List[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        self.ok = False
        self.reasons.append(reason)

    def summary(self) -> str:
        return "ok" if self.ok else "; ".join(self.reasons)


@dataclass
class CandidateClaim:
    """A claim that passed validation, carrying internal scoring metadata."""

    text: str
    claim_type: str
    entities: List[str]
    source_sentence_index: int | None = None
    priority: float = 0.0
    article_order: int = 10**6


@dataclass
class ExtractionResult:
    """Rich, internal result of an extraction run — used by tests and the CLI.

    Callers of the public API only ever see `list[Claim]` (`.claims`).
    """

    claims: list[Claim]
    provider: str = "mock"
    used_fallback: bool = False
    article_sentence_count: int = 0
    raw_claim_count: int = 0
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (claim_text, reason)
    deduplicated: int = 0
    notes: List[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "provider": self.provider,
            "used_fallback": self.used_fallback,
            "article_sentences": self.article_sentence_count,
            "raw_claims_from_provider": self.raw_claim_count,
            "valid_claims": len(self.claims),
            "rejected": len(self.rejected),
            "deduplicated": self.deduplicated,
            "claim_types": [c.claim_type for c in self.claims],
            "notes": self.notes,
        }
