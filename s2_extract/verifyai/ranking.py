"""S2 — relevance ranking + de-duplication (Mansimran).

Design goals: deterministic, dependency-free by default, and explainable ("we
ranked this source 0.71 relevant because ..."). No model downloads needed, so
the demo cannot stall on a 400 MB download at hour 3.

Signals
-------
1. **Semantic-ish similarity** — TF-IDF cosine over the claim and candidate
   texts. Swap in sentence-transformers later via ``SimilarityBackend``; the
   interface is already there.
2. **BM25** — classical lexical relevance, normalised to 0..1.
3. **RRF** — Reciprocal Rank Fusion of each source's own ordering, so a source
   that is confident about result #1 is trusted even if its text is short.

Final relevance = weighted blend, rescaled to 0..1.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable, Iterable, Optional, Sequence

STOPWORDS = {
    "a", "about", "above", "after", "again", "against", "all", "also", "am", "an", "and",
    "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between",
    "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each",
    "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers",
    "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its",
    "itself", "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of",
    "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over",
    "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their",
    "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what",
    "when", "where", "which", "while", "who", "whom", "why", "will", "with", "you", "your",
    "yours", "yourself", "yourselves", "says", "said", "say", "according", "reported",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def content_tokens(text: str) -> list[str]:
    tokens = tokenize(text)
    kept = [t for t in tokens if t not in STOPWORDS and len(t) > 2]
    return kept or tokens


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------
def tfidf_cosine(query: str, documents: Sequence[str]) -> list[float]:
    """Cosine similarity between the query and each document, in 0..1."""
    doc_tokens = [content_tokens(doc) for doc in documents]
    query_tokens = content_tokens(query)
    if not query_tokens or not doc_tokens:
        return [0.0] * len(documents)

    corpus = doc_tokens + [query_tokens]
    doc_freq = Counter()
    for tokens in corpus:
        doc_freq.update(set(tokens))
    n_docs = len(corpus)
    idf = {term: math.log((n_docs + 1) / (count + 1)) + 1.0 for term, count in doc_freq.items()}

    def vector(tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        total = sum(counts.values()) or 1
        return {term: (count / total) * idf.get(term, 1.0) for term, count in counts.items()}

    def cosine(a: dict[str, float], b: dict[str, float]) -> float:
        if not a or not b:
            return 0.0
        shared = set(a) & set(b)
        num = sum(a[t] * b[t] for t in shared)
        den = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
        return num / den if den else 0.0

    query_vec = vector(query_tokens)
    return [max(0.0, min(1.0, cosine(query_vec, vector(tokens)))) for tokens in doc_tokens]


def bm25_scores(
    query: str, documents: Sequence[str], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """Okapi BM25, rescaled to 0..1 for blending."""
    query_tokens = content_tokens(query)
    docs = [content_tokens(doc) for doc in documents]
    if not query_tokens or not docs:
        return [0.0] * len(documents)

    n_docs = len(docs)
    avg_len = sum(len(d) for d in docs) / n_docs or 1.0
    df = Counter()
    for tokens in docs:
        df.update(set(tokens))

    raw: list[float] = []
    for tokens in docs:
        counts = Counter(tokens)
        length = len(tokens) or 1
        score = 0.0
        for term in query_tokens:
            freq = counts.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * length / avg_len))
        raw.append(score)
    return rescale(raw)


def rrf_scores(ranks: Sequence[Optional[int]], *, k: int = 60) -> list[float]:
    """Reciprocal Rank Fusion scores from each candidate's within-source rank."""
    values = [1.0 / (k + rank) if rank and rank > 0 else 0.0 for rank in ranks]
    return rescale(values)


def rescale(values: Sequence[float]) -> list[float]:
    finite = [v for v in values if v is not None and math.isfinite(v)]
    top = max(finite) if finite else 0.0
    if top <= 0:
        return [0.0] * len(values)
    return [round(max(0.0, v) / top, 4) for v in values]


_GENERIC_CLAIM_WORDS = {
    "people", "person", "many", "much", "most", "some", "all", "every", "world", "worldwide",
    "year", "years", "study", "studies", "report", "reported", "according", "claim", "claims",
    "true", "false", "fact", "facts", "believed", "known", "often", "always", "never", "usually",
    "make", "makes", "made", "use", "used", "uses", "increase", "increases", "reduce", "reduces",
    "cause", "causes", "caused", "prevent", "prevents", "help", "helps", "improve", "improves",
}


def distinctive_terms(claim: str) -> set[str]:
    """Terms that make a claim checkable: entities, numbers, and specific nouns.

    Used for *coverage*, which is how retrieval signals "nothing on the web
    actually talks about this claim" — that signal feeds the honest
    insufficient-evidence state instead of a fake verdict.
    """
    terms = set()
    for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-\.]*", claim or ""):
        core = word.strip(".'-").lower()
        if len(core) < 3:
            continue
        if core in STOPWORDS or core in _GENERIC_CLAIM_WORDS:
            continue
        terms.add(core)
    return terms


def coverage(claim: str, text: str) -> float:
    """Share of the claim's distinctive terms that appear in the evidence text."""
    wanted = distinctive_terms(claim)
    if not wanted:
        return 0.0
    have = set(content_tokens(text)) | {w.strip(".'-").lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'’\-\.]*", text or "")}
    return round(len(wanted & have) / len(wanted), 4)


def token_overlap(query: str, text: str) -> float:
    """Share of the claim's content tokens that appear in the text (0..1)."""
    q = set(content_tokens(query))
    if not q:
        return 0.0
    return len(q & set(content_tokens(text))) / len(q)


# ---------------------------------------------------------------------------
# Pluggable semantic backend (stretch item: sentence-transformers / cross-encoder)
# ---------------------------------------------------------------------------
class SimilarityBackend:
    """Default: TF-IDF cosine. Subclass to plug an embedding model in later."""

    name = "tfidf"

    def scores(self, query: str, documents: Sequence[str]) -> list[float]:
        return tfidf_cosine(query, documents)


class SentenceTransformerBackend(SimilarityBackend):
    """Optional embedding backend. Falls back silently if the package is absent."""

    name = "sentence-transformers"

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise ImportError(f"sentence-transformers unavailable: {exc}") from exc
        self._model = SentenceTransformer(model_name)
        self._fallback = SimilarityBackend()

    def scores(self, query: str, documents: Sequence[str]) -> list[float]:
        try:
            import numpy as np

            embeddings = self._model.encode([query, *documents], normalize_embeddings=True)
            query_vec, doc_vecs = embeddings[0], embeddings[1:]
            sims = doc_vecs @ query_vec
            return [max(0.0, min(1.0, float(s))) for s in np.atleast_1d(sims)]
        except Exception:  # pragma: no cover - keep the demo alive
            return self._fallback.scores(query, documents)


DEFAULT_WEIGHTS = {"semantic": 0.50, "bm25": 0.30, "rrf": 0.20}


def rank_candidates(
    query: str,
    documents: Sequence[str],
    ranks: Sequence[Optional[int]] | None = None,
    *,
    weights: dict[str, float] | None = None,
    backend: SimilarityBackend | None = None,
    rerank_fn: Optional[Callable[[str, list[str]], Sequence[float]]] = None,
) -> list[dict[str, float]]:
    """Return per-document score breakdowns (``relevance`` is the blended 0..1)."""
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    backend = backend or SimilarityBackend()
    ranks = ranks or [None] * len(documents)

    coverages = [coverage(query, doc) for doc in documents]
    semantic = backend.scores(query, documents)
    lexical = bm25_scores(query, documents)
    fusion = rrf_scores(ranks)

    out: list[dict[str, float]] = []
    for i, doc in enumerate(documents):
        overlap = token_overlap(query, doc)
        blended = (
            weights["semantic"] * semantic[i]
            + weights["bm25"] * lexical[i]
            + weights["rrf"] * fusion[i]
        )
        # a candidate that shares no content token with the claim is not relevance
        if overlap == 0.0:
            blended *= 0.25
        out.append(
            {
                "semantic": round(semantic[i], 4),
                "bm25": round(lexical[i], 4),
                "rrf": round(fusion[i], 4),
                "overlap": round(overlap, 4),
                "coverage": round(coverages[i], 4),
                "relevance": round(max(0.0, min(1.0, blended)), 4),
            }
        )

    if rerank_fn is not None:
        try:
            extra = list(rerank_fn(query, list(documents)))
            for i, row in enumerate(out):
                if i < len(extra):
                    row["cross_encoder"] = round(float(extra[i]), 4)
                    row["relevance"] = round(
                        max(0.0, min(1.0, 0.7 * row["relevance"] + 0.3 * float(extra[i]))), 4
                    )
        except Exception:  # optional reranker must never break retrieval
            pass
    return out


# ---------------------------------------------------------------------------
# De-duplication
# ---------------------------------------------------------------------------
def shingles(text: str, n: int = 3) -> set[str]:
    tokens = content_tokens(text)
    if len(tokens) < n:
        return {" ".join(tokens)} if tokens else set()
    return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dedupe(
    candidates: Sequence[dict],
    *,
    key_url: Callable[[dict], str],
    key_text: Callable[[dict], str],
    sim_threshold: float = 0.85,
    max_per_domain: int | None = 2,
    domain_of: Callable[[str], str] | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Drop duplicate URLs, near-duplicate text, and over-represented domains.

    ``candidates`` must already be sorted best-first; the first occurrence wins.
    Returns (kept, stats) where stats counts each kind of drop.
    """
    kept: list[dict] = []
    seen_urls: set[str] = set()
    seen_shingles: list[set[str]] = []
    domain_counts: dict[str, int] = {}
    stats = {"duplicate_url": 0, "duplicate_text": 0, "domain_cap": 0}

    for cand in candidates:
        url = key_url(cand)
        if url in seen_urls:
            stats["duplicate_url"] += 1
            continue
        marks = shingles(key_text(cand))
        if any(jaccard(marks, existing) >= sim_threshold for existing in seen_shingles):
            stats["duplicate_text"] += 1
            continue
        domain = (domain_of or (lambda u: ""))(url)
        if max_per_domain is not None and domain_counts.get(domain, 0) >= max_per_domain:
            stats["domain_cap"] += 1
            continue

        seen_urls.add(url)
        seen_shingles.append(marks)
        domain_counts[domain] = domain_counts.get(domain, 0) + 1
        kept.append(cand)
    return kept, stats
