"""S2 — retrieval orchestration (Mansimran).

Public surface used by the API layer:

    from s2_retrieval import RetrievalEngine
    engine = RetrievalEngine()
    result = engine.search_claim(claim.text, claim_id=claim.id)   # -> RetrievalResult
    result.evidence   # list[contracts.Evidence]  (ranked, deduped, capped)
    result.audit      # contracts.RetrievalAudit  (sources queried/failed/used, timings)

Guarantees
----------
* one failing source never fails the run (timeout / error / disabled -> audit entry)
* candidates are normalised, deduped (URL, near-duplicate text, domain cap),
  ranked, then truncated to ``top_k``
* a literal keyword query is always issued; LLM rewrites are optional extras
* every call is cached, so the demo can be replayed offline

CLI
---
    python s2_retrieval.py "Vitamin C prevents the common cold" --top-k 5
    python s2_retrieval.py --file claim.txt --json
    python s2_retrieval.py "..." --offline        # local corpus only
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from cache import Cache, make_key
from envload import describe_config, load_env

try:
    from contracts import Evidence, RetrievalAudit, RetrievalResult, SourceStatus
except ModuleNotFoundError as exc:  # most often: someone ran a different interpreter
    _missing = getattr(exc, "name", "a dependency")
    raise SystemExit(
        f"\nMissing dependency: {_missing}\n"
        f"  interpreter being used: {sys.executable}\n\n"
        "This usually means the package was installed into a different Python than the\n"
        "one running this file (common on Windows with several Pythons or an inactive venv).\n\n"
        "Fix, using the same interpreter that runs this script:\n"
        f'  "{sys.executable}" -m pip install -r requirements-s2.txt\n\n'
        f'Or diagnose everything at once:\n  "{sys.executable}" scripts/doctor.py\n'
    ) from exc

from http_util import SourceError
from ranking import (
    DEFAULT_WEIGHTS,
    SimilarityBackend,
    coverage,
    dedupe,
    rank_candidates,
    rescale,
)
from sources import (
    LocalCorpusSource,
    cap_key,
    RawResult,
    SourceAdapter,
    SourceDisabled,
    canonical_url,
    clean_text,
    default_adapters,
    domain_of,
)

log = logging.getLogger("verifyai.s2")

# Windows consoles default to cp1252 and crash on titles containing ’ — “ etc.
# Replace rather than raise: a mangled character must never kill a demo.
try:  # pragma: no cover - platform dependent
    import sys as _sys

    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

load_env()

# A small set of news/encyclopedia stopwords that hurt literal search queries.
_QUERY_STOPWORDS = {
    "according", "reported", "reports", "said", "says", "study", "studies", "researchers",
    "approximately", "about", "around", "nearly", "more", "than", "most", "many", "some",
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "is", "are", "was",
    "were", "be", "been", "has", "have", "had", "that", "this", "these", "those", "it", "its",
    "will", "would", "can", "could", "may", "might", "should", "do", "does", "did", "not",
    "no", "but", "with", "by", "from", "as", "which", "who", "when", "where", "why", "how",
}


@dataclass
class RetrievalConfig:
    top_k: int = 6
    per_source_limit: int = 5
    max_per_domain: int = 2
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    timeout_s: float = 8.0
    retries: int = 1
    cache_ttl_s: int = 900
    disk_cache_dir: Optional[str] = None
    max_workers: int = 4
    per_source_budget_s: float = 15.0
    # Hard wall-clock budget for ONE claim's retrieval. Sources still running when
    # the deadline hits are abandoned and recorded as timeouts. Measured on the
    # hackathon network: a single slow source could stretch one claim to 10.4 s,
    # which is fatal for a 3-minute demo. 7 s keeps the worst case bounded.
    total_deadline_s: float = 7.0
    min_relevance: float = 0.0          # drop candidates below this relevance
    min_coverage: float = 0.34          # drop candidates covering < this share of the claim's distinctive terms
    # Calibration: ranking similarity (TF-IDF/BM25 blend) peaks around 0.6-0.7 for a
    # genuinely on-topic source, but the lead's R term treats relevance as a quality
    # weight where strong evidence should be ~0.9. A monotone scale factor preserves
    # ordering while putting values on the scale the scorer expects.
    relevance_scale: float = 1.45
    offline: bool = False               # only local corpus (demo safety net)
    replay_snapshot: Optional[str] = None  # serve captured evidence only (offline demo backup)
    use_cache: bool = True              # False forces real requests (honest measurement)
    # --- full text for quote verification (S2 -> S3 handoff) -----------------
    # How many top-ranked results to fetch page text for. 0 disables fetching.
    # Without this, S3 can only verify quotes against a snippet, and must not
    # claim it verified them in the source.
    fetch_full_text: int = 3
    fulltext_deadline_s: float = 4.0
    fulltext_max_chars: int = 6000
    fulltext_timeout_s: float = 5.0
    mock: bool = False                  # add local corpus to the real sources
    use_llm_query_rewrite: bool = False  # optional, off by default
    extra_queries: int = 0              # how many rewrites to add (if enabled)

    def to_dict(self) -> dict:
        return {
            "top_k": self.top_k,
            "per_source_limit": self.per_source_limit,
            "max_per_domain": self.max_per_domain,
            "weights": self.weights,
            "timeout_s": self.timeout_s,
            "retries": self.retries,
            "min_coverage": self.min_coverage,
            "relevance_scale": self.relevance_scale,
            "total_deadline_s": self.total_deadline_s,
            "use_cache": self.use_cache,
            "fetch_full_text": self.fetch_full_text,
            "offline": self.offline,
            "replay_snapshot": self.replay_snapshot,
            "mock": self.mock,
        }


@dataclass
class _Candidate:
    """A RawResult plus everything needed to rank and audit it."""

    raw: RawResult
    source_name: str
    source_rank: int
    query: str

    def text_for_matching(self) -> str:
        return f"{self.raw.title}. {self.raw.snippet}"


# ---------------------------------------------------------------------------
# Query building
# ---------------------------------------------------------------------------
def build_queries(
    claim_text: str,
    *,
    extra: Optional[Sequence[str]] = None,
    entities: Optional[Sequence[str]] = None,
    max_extras: int = 2,
    max_chars: int = 220,
) -> list[str]:
    """Literal keyword query first (always), then optional extras.

    * keeps entities/numbers/quoted phrases,
    * strips conversational filler that dilutes search results,
    * never leaves the list empty (falls back to the raw claim).
    """
    claim = clean_text(claim_text, 1000)
    words = re_words(claim)
    kept = [w for w in words if w.lower() not in _QUERY_STOPWORDS]

    literal = " ".join(kept)
    literal = literal.strip() or claim
    queries = [literal[:max_chars]]

    # entities from S1 (Ikjot's Claim.entities) beat guessing proper nouns
    typed_entities = [
        str(e).strip() for e in (entities or []) if str(e).strip()
    ]
    if typed_entities:
        topical = [w for w in kept if w.lower() not in {e.lower() for e in typed_entities}]
        anchored = " ".join(dict.fromkeys(typed_entities + topical[:10]))[:max_chars]
        if anchored and anchored not in queries and anchored != literal:
            queries.append(anchored)

    # a short, entity-heavy variant often beats the long literal query
    proper = [w for w in kept if w[:1].isupper()]
    numbers = [w for w in kept if any(ch.isdigit() for ch in w)]
    short = " ".join(dict.fromkeys(proper + numbers))[:max_chars]
    if short and len(short) >= 6 and short not in queries and short != literal:
        queries.append(short)

    extras = 0
    for q in extra or []:
        cleaned = clean_text(q, max_chars)
        if cleaned and cleaned not in queries and extras < max_extras:
            queries.append(cleaned)
            extras += 1

    return queries


def calibrate_relevance(similarity: float, scale: float = 1.45) -> float:
    """Map a 0..1 ranking similarity onto the scorer's relevance scale.

    Monotone (linear, clipped), so it never changes ordering or invents strength:
    a weak match stays weak, a strong match reaches the ~0.9 the R term expects.
    Set ``relevance_scale=1.0`` for raw values.
    """
    if scale <= 0:
        return max(0.0, min(1.0, similarity))
    return round(max(0.0, min(1.0, similarity * scale)), 4)


def re_words(text: str) -> list[str]:
    import re

    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-\.]*", text or "")


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class RetrievalEngine:
    def __init__(
        self,
        config: RetrievalConfig | None = None,
        adapters: Optional[Sequence[SourceAdapter]] = None,
        cache: Optional[Cache] = None,
        query_rewriter: Optional[Callable[[str], Iterable[str]]] = None,
        backend: Optional[SimilarityBackend] = None,
        rerank_fn: Optional[Callable[[str, list[str]], Sequence[float]]] = None,
    ) -> None:
        self.config = config or RetrievalConfig()
        if adapters is not None:
            self.adapters = list(adapters)
        elif self.config.replay_snapshot:
            # demo backup mode: no network at all, nothing but the captured file
            from snapshot import SnapshotSource

            self.adapters = [SnapshotSource(self.config.replay_snapshot)]
        else:
            self.adapters = default_adapters(offline=self.config.offline, mock=self.config.mock)
        if cache is not None:
            self.cache = cache
        elif not self.config.use_cache:
            from cache import NullCache

            self.cache = NullCache()
        else:
            self.cache = Cache(
                ttl_s=self.config.cache_ttl_s, disk_dir=self.config.disk_cache_dir
            )
        self.query_rewriter = query_rewriter
        self.backend = backend or SimilarityBackend()
        self.rerank_fn = rerank_fn

    # ------------------------------------------------------------- public API
    def search_claim(
        self,
        claim_text: str,
        claim_id: str | None = None,
        entities: Optional[Sequence[str]] = None,
    ) -> RetrievalResult:
        """Retrieve evidence for one claim.

        ``entities`` (optional) come from S1 - they are used to build a better
        literal query, which is the only thing they change. They never affect
        ranking or stance.
        """
        started = time.perf_counter()
        queries = self._queries_for(claim_text, entities)
        audit = RetrievalAudit(queries=queries, sources_queried=len(self.adapters))

        candidates, statuses = self._collect(queries, audit)
        audit.sources = statuses
        audit.candidates_total = len(candidates)

        if not candidates:
            audit.duration_ms = int((time.perf_counter() - started) * 1000)
            audit.notes.append("no source returned usable results")
            if self.config.replay_snapshot:
                audit.notes.append(
                    "claim is not in the snapshot — capture it with scripts/warm_snapshot.py "
                    "before the demo, or present it as a genuine insufficient-evidence case"
                )
            return RetrievalResult(
                claim_id=claim_id, claim_text=claim_text, evidence=[], audit=audit
            )

        # ---- rank before dedupe so the best copy of a duplicate survives
        scored = rank_candidates(
            claim_text,
            [c.text_for_matching() for c in candidates],
            [c.source_rank for c in candidates],
            weights=self.config.weights,
            backend=self.backend,
            rerank_fn=self.rerank_fn,
        )
        for candidate, row in zip(candidates, scored):
            candidate.raw.extra["scores"] = row  # keep the breakdown for the audit

        order = sorted(
            range(len(candidates)),
            key=lambda i: scored[i]["relevance"],
            reverse=True,
        )
        ordered = [candidates[i] for i in order]

        # ---- normalise -> dedupe -> cap
        normalized = [self._to_evidence(c, claim_id) for c in ordered]
        kept, drop_stats = dedupe(
            normalized,
            key_url=lambda ev: canonical_url(ev.url),
            key_text=lambda ev: f"{ev.title} {ev.text_or_snippet}",
            max_per_domain=self.config.max_per_domain,
            domain_of=cap_key,
        )
        if self.config.min_relevance > 0:
            kept = [ev for ev in kept if ev.relevance >= self.config.min_relevance]
        coverage_map: dict[str, float] = {}
        if self.config.min_coverage > 0:
            filtered: list[Evidence] = []
            for ev in kept:
                value = coverage(claim_text, f"{ev.title} {ev.text_or_snippet}")
                coverage_map[canonical_url(ev.url)] = value
                if value >= self.config.min_coverage:
                    filtered.append(ev)
                else:
                    audit.dropped_low_coverage += 1
            kept = filtered
        else:
            coverage_map = {
                canonical_url(ev.url): coverage(claim_text, f"{ev.title} {ev.text_or_snippet}")
                for ev in kept
            }

        final = self._ensure_source_diversity(kept)[: self.config.top_k]

        # ---- full text for the quote-verification guard (bounded, fail-soft)
        self._attach_full_text(final, claim_text, audit)

        # ---- audit
        used_urls = {canonical_url(ev.url) for ev in final}
        for status in audit.sources:
            status.used = sum(
                1 for c in candidates if c.source_name == status.name and canonical_url(c.raw.url) in used_urls
            )
        audit.dropped_duplicates = drop_stats["duplicate_url"] + drop_stats["duplicate_text"]
        audit.dropped_over_domain_cap = drop_stats["domain_cap"]
        audit.returned = len(final)
        audit.coverage_by_evidence = {
            canonical_url(ev.url): coverage_map.get(canonical_url(ev.url), 0.0) for ev in final
        }
        audit.mean_coverage = (
            round(sum(audit.coverage_by_evidence.values()) / len(final), 4) if final else 0.0
        )
        audit.distinct_domains = len({ev.domain for ev in final})
        audit.sources_ok = sum(1 for s in statuses if s.status == "ok")
        audit.sources_failed = sum(1 for s in statuses if s.status in ("error", "timeout"))
        audit.cache_hits = sum(1 for s in statuses if s.cached)
        if any(s.name == "snapshot" for s in statuses):
            captured = ""
            for adapter in self.adapters:
                if adapter.name == "snapshot":
                    captured = str(getattr(adapter, "captured_at", ""))
            audit.notes.append(
                "DEMO BACKUP — evidence replayed from a cached snapshot"
                + (f" captured {captured}" if captured else "")
                + "; this is not a live retrieval, say so out loud when demoing"
            )
        audit.duration_ms = int((time.perf_counter() - started) * 1000)
        if audit.sources_failed:
            audit.notes.append(
                "failed sources: " + ", ".join(s.name for s in statuses if s.status in ("error", "timeout"))
            )
        if audit.distinct_domains <= 1 and final:
            audit.notes.append("all evidence came from a single domain — treat agreement as weak")

        return RetrievalResult(
            claim_id=claim_id, claim_text=claim_text, evidence=final, audit=audit
        )

    def warm(self, claims: Sequence[str]) -> dict:
        """Pre-populate the cache before the demo (plumbing, not magic)."""
        warmed = 0
        for claim in claims:
            result = self.search_claim(claim)
            warmed += 1 if result.evidence else 0
        return {"claims": len(claims), "with_evidence": warmed, "cache": self.cache.stats()}

    # ------------------------------------------------------------- internals
    def _queries_for(
        self, claim_text: str, entities: Optional[Sequence[str]] = None
    ) -> list[str]:
        extras: list[str] = []
        if self.config.use_llm_query_rewrite and self.query_rewriter:
            try:
                extras = list(self.query_rewriter(claim_text))[: self.config.extra_queries]
            except Exception as exc:  # rewriting is optional, never fatal
                log.info("query rewriter failed: %s", exc)
        return build_queries(
            claim_text,
            extra=extras,
            entities=entities,
            max_extras=max(1, self.config.extra_queries or 1),
        )

    def _collect(
        self, queries: Sequence[str], audit: RetrievalAudit
    ) -> tuple[list[_Candidate], list[SourceStatus]]:
        candidates: list[_Candidate] = []
        statuses: list[SourceStatus] = []
        jobs = [(adapter, query) for adapter in self.adapters for query in queries]
        merged_by_name: dict[str, SourceStatus] = {}

        if not jobs:
            return candidates, statuses

        pool = ThreadPoolExecutor(max_workers=max(1, min(self.config.max_workers, len(jobs))))
        futures = {
            pool.submit(self._run_one, adapter, query): (adapter, query)
            for adapter, query in jobs
        }
        deadline = time.perf_counter() + max(0.5, self.config.total_deadline_s)

        try:
            for future in as_completed(futures, timeout=max(0.1, deadline - time.perf_counter())):
                adapter, query = futures[future]
                try:
                    status = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    status = SourceStatus(
                        name=adapter.name,
                        kind=adapter.kind,
                        status="error",
                        query=query,
                        error=str(exc)[:200],
                    )
                candidates.extend(getattr(status, "_candidates", []) or [])
                merged = merged_by_name.get(status.name)
                if merged is None:
                    merged_by_name[status.name] = status
                else:
                    self._merge_status(merged, status)
        except TimeoutError:
            # sources still running at the deadline: abandon them, do not block
            for future, (adapter, query) in futures.items():
                if future.done():
                    continue
                late = SourceStatus(
                    name=adapter.name,
                    kind=adapter.kind,
                    status="timeout",
                    query=query,
                    error=f"abandoned after {self.config.total_deadline_s:.1f}s deadline",
                )
                merged = merged_by_name.get(late.name)
                if merged is None:
                    merged_by_name[late.name] = late
                else:
                    self._merge_status(merged, late)
            audit.notes.append(
                f"deadline {self.config.total_deadline_s:.1f}s reached; slow sources abandoned"
            )
        finally:
            # never wait for stragglers: their own request timeouts will end them
            pool.shutdown(wait=False, cancel_futures=True)

        return candidates, list(merged_by_name.values())

    @staticmethod
    def _merge_status(into: SourceStatus, other: SourceStatus) -> None:
        """Collapse per-query statuses into one row per adapter for the audit UI."""
        into.results += other.results
        into.latency_ms += other.latency_ms
        into.cached = into.cached or other.cached
        if other.query and other.query not in into.query.split(" | "):
            into.query = " | ".join([into.query, other.query]) if into.query else other.query
        severity = {"ok": 0, "empty": 1, "skipped": 2, "error": 3, "timeout": 4}
        if severity.get(other.status, 0) > severity.get(into.status, 0):
            into.status = other.status
        if other.error and not into.error:
            into.error = other.error

    def _run_one(self, adapter: SourceAdapter, query: str) -> SourceStatus:
        """Never raises: every outcome becomes a SourceStatus."""
        status = SourceStatus(name=adapter.name, kind=adapter.kind, query=query)
        started = time.perf_counter()
        try:
            results = adapter.search(
                query, limit=self.config.per_source_limit, cache=self.cache
            )
        except SourceDisabled as exc:
            status.status = "skipped"
            status.error = str(exc)[:200]
            status.latency_ms = int((time.perf_counter() - started) * 1000)
            return _attach(status, [])
        except SourceError as exc:
            status.status = "error"
            status.error = str(exc)[:200]
            status.latency_ms = int((time.perf_counter() - started) * 1000)
            log.info("source %s failed: %s", adapter.name, exc)
            return _attach(status, [])
        except Exception as exc:  # parsing bugs must not kill the run either
            status.status = "error"
            status.error = f"{type(exc).__name__}: {exc}"[:200]
            status.latency_ms = int((time.perf_counter() - started) * 1000)
            log.info("source %s crashed: %s", adapter.name, exc)
            return _attach(status, [])

        status.latency_ms = int((time.perf_counter() - started) * 1000)
        status.results = len(results)
        status.status = "ok" if results else "empty"
        if not results:
            status.error = None
        status.cached = bool(getattr(adapter, "is_cached", False)) or (
            bool(self.cache.hits) and status.latency_ms < 5
        )

        picked: list[_Candidate] = []
        for rank, raw in enumerate(results, start=1):
            if not raw.url:
                continue
            if not (raw.snippet or raw.title):
                continue
            picked.append(
                _Candidate(
                    raw=raw,
                    source_name=adapter.name,
                    source_rank=rank,
                    query=query,
                )
            )
        return _attach(status, picked)

    def _attach_full_text(
        self, final: list[Evidence], claim_text: str, audit: RetrievalAudit
    ) -> None:
        """Fetch page text for the top results so S3 can verify quotes honestly.

        Bounded three ways: only ``fetch_full_text`` items, in parallel, behind
        ``fulltext_deadline_s``. Anything not fetched keeps ``text_scope="snippet"``
        plus a reason, so the guard can never overclaim.
        """
        if not final or self.config.fetch_full_text <= 0 or self.config.offline:
            return
        if self.config.replay_snapshot:
            return  # snapshots carry their own captured text

        from fulltext import fetch_page_text

        targets = final[: self.config.fetch_full_text]
        audit.full_text_requested = len(targets)
        deadline = time.perf_counter() + max(0.5, self.config.fulltext_deadline_s)

        def work(ev: Evidence):
            return ev, fetch_page_text(
                ev.url,
                timeout=self.config.fulltext_timeout_s,
                max_chars=self.config.fulltext_max_chars,
                cache=self.cache,
            )

        pool = ThreadPoolExecutor(max_workers=min(4, len(targets)))
        futures = [pool.submit(work, ev) for ev in targets]
        try:
            for future in futures:
                remaining = deadline - time.perf_counter()
                if remaining <= 0.1:
                    break
                try:
                    ev, page = future.result(timeout=remaining)
                except TimeoutError:
                    continue
                except Exception as exc:  # pragma: no cover - defensive
                    audit.full_text_reasons.append(f"{type(exc).__name__}")
                    continue
                if page.ok:
                    ev.full_text = page.text
                    ev.text_scope = "full_text"
                    ev.text_scope_reason = None
                    audit.full_text_fetched += 1
                    audit.full_text_chars += page.chars
                else:
                    ev.text_scope = "snippet"
                    ev.text_scope_reason = page.reason
                    audit.full_text_failed += 1
                    audit.full_text_reasons.append(f"{domain_of(ev.url)}: {page.reason}")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        if audit.full_text_failed:
            audit.notes.append(
                f"snippet-only evidence: {audit.full_text_failed} item(s) could not be "
                "fetched in full; quotes for those can only be checked against the snippet"
            )

    def _ensure_source_diversity(self, ordered: list[Evidence]) -> list[Evidence]:
        """Keep the relevance order, but make sure each *kind* of source that
        produced something is represented (when top_k allows).

        Why: the A term (independent agreement) is meaningless if all evidence
        comes from one publisher. This is a ranking policy, not a truth signal —
        the lead still decides what "insufficient evidence" means.
        """
        limit = self.config.top_k
        kinds = {ev.source_kind for ev in ordered}
        if len(kinds) <= 1 or len(ordered) <= 1:
            return ordered

        head = ordered[:limit]
        represented = {ev.source_kind for ev in head}
        promoted: list[Evidence] = []
        for kind in sorted(kinds - represented):
            for ev in ordered:
                if ev.source_kind == kind and ev not in promoted:
                    promoted.append(ev)
                    break
        if not promoted:
            return ordered

        # reserve one slot per missing kind by demoting the weakest tail items
        head = head[: max(1, limit - len(promoted))] + promoted
        head = sorted(head, key=lambda ev: ev.relevance, reverse=True)
        rest = [ev for ev in ordered if ev not in head]
        return head + rest

    def _to_evidence(self, candidate: _Candidate, claim_id: str | None) -> Evidence:
        raw = candidate.raw
        raw_similarity = float(raw.extra.get("scores", {}).get("relevance", 0.0))
        return Evidence(
            claim_id=claim_id,
            url=raw.url,
            domain=domain_of(raw.url),
            title=clean_text(raw.title, 300),
            text_or_snippet=clean_text(raw.snippet, 1200),
            published_at=raw.published_at,
            source_kind=raw.source_kind,  # type: ignore[arg-type]
            relevance=calibrate_relevance(raw_similarity, self.config.relevance_scale),
            full_text=str(raw.extra.get("full_text", "") or ""),
            text_scope=raw.extra.get("text_scope", "snippet"),  # type: ignore[arg-type]
            text_scope_reason=raw.extra.get("text_scope_reason"),
        )


def _attach(status: SourceStatus, candidates: list[_Candidate]) -> SourceStatus:
    """Stash candidates on the status object while keeping the contract clean."""
    object.__setattr__(status, "_candidates", candidates)
    return status


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VerifyAI retrieval (S2) — fetch + rank evidence")
    parser.add_argument("claim", nargs="?", help="claim text")
    parser.add_argument("--file", help="read claim text from a file")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--offline", action="store_true", help="local corpus only (demo fallback)")
    parser.add_argument("--mock", action="store_true", help="add local corpus to live sources")
    parser.add_argument(
        "--replay-snapshot",
        metavar="PATH",
        help="serve captured evidence from a snapshot file (offline demo backup, no network)",
    )
    parser.add_argument("--json", action="store_true", help="pretty JSON output")
    parser.add_argument(
        "--out",
        help="write JSON to this file (use this instead of `> file.json` on Windows: "
        "PowerShell 5.1 redirection writes UTF-16)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    claim = args.claim
    if args.file:
        claim = open(args.file, encoding="utf-8").read()
    if not claim:
        parser.error("provide a claim or --file")

    engine = RetrievalEngine(
        RetrievalConfig(
            top_k=args.top_k,
            offline=args.offline,
            mock=args.mock,
            replay_snapshot=args.replay_snapshot,
        )
    )
    result = engine.search_claim(claim)

    if args.out:
        Path(args.out).write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"wrote {args.out}")
        if not args.json:
            return 0

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        return 0

    for line in describe_config():
        print(f"\n{line}")
    print(f"\nCLAIM: {claim.strip()[:200]}")
    print(f"QUERIES: {result.audit.queries}")
    print(f"SOURCES: {result.audit.sources_ok} ok / {result.audit.sources_failed} failed "
          f"of {result.audit.sources_queried}  ({result.audit.duration_ms} ms)")
    for status in result.audit.sources:
        note = f" — {status.error}" if status.error else ""
        print(f"  - {status.name:<18} {status.status:<8} results={status.results:<3} used={status.used} "
              f"{status.latency_ms}ms{note}")
    print(f"\nEVIDENCE ({len(result.evidence)}):")
    for i, ev in enumerate(result.evidence, 1):
        print(f"  {i}. [{ev.relevance:.2f}] ({ev.source_kind}) {ev.title[:90]}")
        print(f"     {ev.domain} | {ev.published_at or 'no date'} | {ev.url}")
        print(f"     {ev.text_or_snippet[:180]}...")
    print(f"\nAUDIT: candidates={result.audit.candidates_total} "
          f"dupes_dropped={result.audit.dropped_duplicates} "
          f"domain_cap={result.audit.dropped_over_domain_cap} "
          f"domains={result.audit.distinct_domains}")
    for note in result.audit.notes:
        print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
