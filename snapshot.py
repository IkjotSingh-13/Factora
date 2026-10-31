"""Snapshot capture + replay (Mansimran).

Why this exists
---------------
The HTTP cache has a TTL (15 minutes by default), so a "warmed" cache is dead by
the time you demo. This module saves retrieval results to a plain JSON file that
replays **forever**, with no network, no keys and no rate limits.

Honesty rule from the plan: cached output must be labelled as a demo backup,
never presented as live. This module makes that structural, not a promise:

* the replay adapter reports itself as ``source = "snapshot"`` with ``cached=True``
* the audit gets a note: "DEMO BACKUP - served from cached snapshot captured <ts>"
* :attr:`SnapshotSource.captured_at` is available for the UI footer

Usage
-----
    # capture (needs network, run before the demo)
    python scripts/warm_snapshot.py

    # replay (offline, instant)
    python s2_retrieval.py --replay-snapshot data/demo_snapshot.json "vitamin C common cold"

    # or in code
    from s2_retrieval import RetrievalEngine, RetrievalConfig
    engine = RetrievalEngine(RetrievalConfig(replay_snapshot="data/demo_snapshot.json"))
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from sources import RawResult, SourceAdapter, clean_text

SNAPSHOT_VERSION = 1
DEFAULT_SNAPSHOT_PATH = Path(__file__).parent / "data" / "demo_snapshot.json"

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize_claim(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for exact matching."""
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _token_set(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall((text or "").lower()) if len(t) > 2}


def _overlap(a: set[str], b: set[str]) -> float:
    """Similarity that tolerates a short query against a long claim.

    Jaccard alone punishes a 4-word query matching a 14-word claim, which is the
    normal case when the engine re-derives its query at replay time. Take the best
    of Jaccard and *containment* (|A and B| / min(|A|,|B|)), and require at least
    ``MIN_SHARED_TOKENS`` shared content words so a single generic word cannot
    produce a match.
    """
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if shared < 2:
        return 0.0
    jaccard = shared / len(a | b)
    containment = shared / min(len(a), len(b))
    return max(jaccard, containment)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
def build_snapshot_entry(
    claim_text: str,
    *,
    queries: Sequence[str],
    evidence: Iterable[Any],
    audit: Any = None,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Turn one live RetrievalResult into a serialisable snapshot entry."""
    return {
        "claim": claim_text,
        "normalized_claim": normalize_claim(claim_text),
        "queries": list(queries),
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "audit_summary": (
            {
                "sources_ok": getattr(audit, "sources_ok", 0),
                "sources_failed": getattr(audit, "sources_failed", 0),
                "returned": getattr(audit, "returned", 0),
                "distinct_domains": getattr(audit, "distinct_domains", 0),
                "duration_ms": getattr(audit, "duration_ms", 0),
                "notes": list(getattr(audit, "notes", []) or []),
            }
            if audit is not None
            else {}
        ),
        "evidence": [
            {
                "url": ev.url,
                "domain": ev.domain,
                "title": ev.title,
                "text_or_snippet": ev.text_or_snippet,
                "published_at": ev.published_at,
                "source_kind": ev.source_kind,
                "relevance": ev.relevance,
                "full_text": getattr(ev, "full_text", "") or "",
                "text_scope": getattr(ev, "text_scope", "snippet"),
                "text_scope_reason": getattr(ev, "text_scope_reason", None),
            }
            for ev in evidence
        ],
    }


def save_snapshot(
    path: str | Path,
    entries: Sequence[dict[str, Any]],
    *,
    mode: str = "live",
    config: dict[str, Any] | None = None,
) -> Path:
    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "kind": "verifyai_retrieval_snapshot",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "warning": (
            "Cached retrieval results captured before the demo. Replaying these is a "
            "DEMO BACKUP, not a live retrieval. Label it as such in the UI."
        ),
        "config": config or {},
        "claims": list(entries),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def load_snapshot(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"snapshot not found: {target}")
    payload = json.loads(target.read_text(encoding="utf-8"))
    if payload.get("kind") != "verifyai_retrieval_snapshot":
        raise ValueError(f"{target} is not a VerifyAI retrieval snapshot")
    return payload


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------
class SnapshotSource(SourceAdapter):
    """Replays captured evidence for a claim. Never invents anything.

    Matching order: exact normalised claim -> exact stored query -> token overlap
    on the claim (>= ``min_overlap``, at least 2 shared content words). No match
    returns ``[]`` so the honest "insufficient evidence" path still works for
    claims outside the snapshot.
    """

    name = "snapshot"
    kind = "cached"
    budget_s = 0.5
    is_cached = True

    def __init__(self, path: str | Path | None = None, *, min_overlap: float = 0.6) -> None:
        self.path = Path(path) if path else DEFAULT_SNAPSHOT_PATH
        self.min_overlap = min_overlap
        self._cache: dict[str, Any] | None = None

    # ------------------------------------------------------------------ load
    @property
    def payload(self) -> dict[str, Any]:
        if self._cache is None:
            self._cache = load_snapshot(self.path)
        return self._cache

    @property
    def captured_at(self) -> str:
        return str(self.payload.get("captured_at", "unknown"))

    def summary(self) -> dict[str, Any]:
        claims = self.payload.get("claims", [])
        return {
            "path": str(self.path),
            "captured_at": self.captured_at,
            "claims": len(claims),
            "with_evidence": sum(1 for c in claims if c.get("evidence")),
        }

    # ---------------------------------------------------------------- search
    def search(self, query: str, *, limit: int, cache: Any = None) -> list[RawResult]:
        entry = self._find(query)
        if entry is None:
            return []
        results: list[RawResult] = []
        for item in (entry.get("evidence") or [])[:limit]:
            results.append(
                RawResult(
                    title=clean_text(item.get("title"), 300),
                    url=item.get("url", ""),
                    snippet=clean_text(item.get("text_or_snippet"), 1200),
                    published_at=item.get("published_at"),
                    # keep the *original* source kind so credibility scoring still works;
                    # the cached-ness is reported through the source status + audit note
                    source_kind=item.get("source_kind", "web"),
                    extra={
                        "relevance": item.get("relevance"),
                        "captured_at": entry.get("captured_at"),
                        "demo_backup": True,
                        "full_text": item.get("full_text", ""),
                        "text_scope": item.get("text_scope", "snippet"),
                        "text_scope_reason": item.get("text_scope_reason"),
                    },
                )
            )
        return results

    def _find(self, query: str) -> Optional[dict[str, Any]]:
        claims = self.payload.get("claims") or []
        nq = normalize_claim(query)

        for entry in claims:  # exact claim match
            if entry.get("normalized_claim") == nq:
                return entry
        for entry in claims:  # exact query match (engine query == captured query)
            if any(normalize_claim(q) == nq for q in entry.get("queries", [])):
                return entry

        best: tuple[float, Optional[dict[str, Any]]] = (0.0, None)
        tokens = _token_set(query)
        for entry in claims:
            score = max(
                _overlap(tokens, _token_set(entry.get("claim", ""))),
                max(
                    (_overlap(tokens, _token_set(q)) for q in entry.get("queries", [])),
                    default=0.0,
                ),
            )
            if score > best[0]:
                best = (score, entry)
        if best[1] is not None and best[0] >= self.min_overlap:
            return best[1]
        return None
