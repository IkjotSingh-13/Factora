"""spaCy-powered NLP layer + STRICT claim validation.

This module owns:
  * text normalisation and sentence splitting (spaCy sentence boundaries),
  * entity extraction (`extract_entities`),
  * numeric / qualifier / hedge utilities,
  * `validate_claim` — the hard gate every claim must pass before it can leave S1.

Design note: validation is deliberately *layered and lexical*, not a single
similarity score. A claim must survive ALL of these independent checks:

  1. basic shape      — non-empty, sane length
  2. lexical grounding— content lemmas must actually occur in the article
  3. numeric grounding— every number in the claim must exist in the article
                        (catches fabricated / altered statistics)
  4. entity grounding — named entities + proper nouns must occur in the article
                        (catches unsupported context expansion)
  5. hedge integrity  — a speculative statement in the article may not be
                        restated as an established fact
  6. qualifier integrity — "approximately 25 GW" may not become "25 GW"

A cosine/embedding score alone would pass 2 but silently fail 3-6, which is why
it is only an auxiliary signal here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, List, Sequence, Tuple

import spacy

from .config import Settings, get_settings
from .schemas import RawClaim, ValidationReport

# ---------------------------------------------------------------------------
# spaCy
# ---------------------------------------------------------------------------

_NLP = None
_NLP_DEGRADED = False

# Entity labels we consider "relevant named entities" for a claim.
ENTITY_LABELS = {
    "PERSON",
    "ORG",
    "GPE",
    "LOC",
    "NORP",
    "FAC",
    "EVENT",
    "PRODUCT",
    "WORK_OF_ART",
    "LAW",
}

# Generic role nouns that are not useful *named* entities on their own.
# (The spec's own example maps "The government announced ..." to ["India"],
#  so bare "government"/"company" style references must not be emitted.)
GENERIC_ENTITY_PHRASES = {
    "government",
    "the government",
    "central government",
    "state government",
    "company",
    "the company",
    "the firm",
    "officials",
    "authorities",
    "the authorities",
    "ministry",
    "the ministry",
    "report",
    "the report",
    "experts",
    "sources",
    "people",
    "it",
    "they",
    "he",
    "she",
    "we",
    "you",
}

# Units, currencies and abbreviations spaCy sometimes mislabels as ORG. These
# are never useful "named entities" for a claim.
NON_ENTITY_TOKENS = {
    "gw", "mw", "kw", "tw", "gwh", "mwh", "kwh", "wh", "mm", "cm", "km", "kg",
    "mt", "bt", "bp", "sq", "ft", "hr", "hrs", "am", "pm", "rs", "inr", "usd",
    "eur", "gbp", "cr", "crore", "lakh", "lakhs", "percent", "pc", "pct",
    "no", "nos", "vol", "vs", "etc", "eg", "ie", "ok", "tv", "ai", "ev",
}

# Generic singleton words that look like entities when capitalized at sentence start
# but are actually common nouns (e.g., "Services revenue grew...")
GENERIC_SINGLETON_ENTITIES = {
    "services", "revenue", "growth", "sales", "profit", "profits", "earnings",
    "market", "company", "firm", "government", "ministry", "report", "data",
    "study", "survey", "analysis", "research", "plan", "project", "programme",
    "program", "initiative", "scheme", "policy", "investment", "capacity",
    "production", "demand", "supply", "price", "prices", "rate", "rates",
    "growth", "increase", "decrease", "rise", "fall", "drop", "cut", "hike",
    "announcement", "announcements", "statement", "claim", "claims", "fact",
    "facts", "news", "update", "updates", "development", "developments",
    "facility", "facilities", "factory", "factories", "plant", "plants",
}

_LEADING_ARTICLES = ("the ", "a ", "an ")


def get_nlp():
    """Load (and cache) the spaCy pipeline.

    Falls back to a blank English pipeline with a sentencizer if the configured
    model is missing, so the module still works (with reduced NER) rather than
    crashing at import time.
    """
    global _NLP, _NLP_DEGRADED
    if _NLP is None:
        settings = get_settings()
        try:
            _NLP = spacy.load(settings.spacy_model)
        except (OSError, ImportError):  # pragma: no cover - env dependent
            _NLP = spacy.blank("en")
            _NLP.add_pipe("sentencizer")
            _NLP_DEGRADED = True
        _NLP.max_length = max(_NLP.max_length, 2_000_000)
    return _NLP


def nlp_is_degraded() -> bool:
    get_nlp()
    return _NLP_DEGRADED


def reset_nlp_cache() -> None:
    """Testing utility: clear cached model so next get_nlp() reloads."""
    global _NLP, _NLP_DEGRADED
    _NLP = None
    _NLP_DEGRADED = False
    _split_sentences_cached.cache_clear()
    _content_lemmas.cache_clear()


# ---------------------------------------------------------------------------
# Text normalisation & sentence splitting
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"[ \t\u00a0\u200b]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Unicode-normalise and collapse whitespace without altering content."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    lines = [_WS_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return _MULTI_NL_RE.sub("\n\n", text).strip()


_SENT_FALLBACK_RE = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9\"'(\[])")
# Additional split for bullet-like and list-like structures
_BULLET_SPLIT_RE = re.compile(r"\n\s*(?:[-*\u2022\u25cf]|\d+[.)]|[a-z][.)])\s+")


def _regex_split_sentences(text: str) -> List[str]:
    """Fallback sentence splitter using regex, handles many edge cases."""
    if not text:
        return []
    # First split by newlines that look like bullet points
    # Keep bullet content as separate sentences
    text = text.replace("\n", " ")
    # Handle common abbreviations to avoid false splits
    # Temporarily replace them
    abbreviations = ["Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Sr.", "Jr.", "St.", "vs.", "etc.", "e.g.", "i.e.", "U.S.", "U.K.", "U.N."]
    placeholder_map = {}
    for idx, abbr in enumerate(abbreviations):
        placeholder = f"__ABBR{idx}__"
        if abbr in text:
            placeholder_map[placeholder] = abbr
            text = text.replace(abbr, placeholder)
    
    # Split on sentence boundaries
    sentences = [s.strip() for s in _SENT_FALLBACK_RE.split(text) if s.strip()]
    
    # Restore abbreviations
    restored = []
    for sent in sentences:
        for placeholder, abbr in placeholder_map.items():
            sent = sent.replace(placeholder, abbr)
        restored.append(sent)
    
    # Further split very long fragments that contain multiple independent clauses
    # separated by semicolons (common in informal text)
    final = []
    for sent in restored:
        # If sentence is very long and contains semicolons, split there too
        if len(sent) > 200 and ";" in sent:
            parts = [p.strip() for p in sent.split(";") if p.strip()]
            final.extend(parts)
        else:
            final.append(sent)
    return final


@lru_cache(maxsize=32)
def _split_sentences_cached(text: str) -> Tuple[str, ...]:
    if not text:
        return ()
    # In degraded mode, always use regex splitter which is more reliable
    # than blank model's sentencizer which may miss boundaries
    if nlp_is_degraded():
        sentences = _regex_split_sentences(text)
        # Also try blank model's sentencizer as supplementary
        try:
            nlp = get_nlp()
            doc = nlp(text)
            sents = [sent.text.strip() for sent in doc.sents if sent.text.strip()]
            # Prefer longer split (more sentences) but not too fragmented
            if len(sents) > len(sentences) and len(sents) <= 20:
                sentences = sents
        except Exception:
            pass
        return tuple(sentences)
    
    nlp = get_nlp()
    doc = nlp(text)
    sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]
    if len(sentences) <= 1 and len(text) > 200:
        # spaCy sometimes returns the whole paragraph; fall back to regex.
        sentences = _regex_split_sentences(text)
    return tuple(sentences)


def split_sentences(text: str) -> List[str]:
    """Split text into sentences (spaCy, with a regex fallback)."""
    return list(_split_sentences_cached(normalize_text(text)))


def word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9\u20b9$%][\w\u20b9$%.,'\-/]*", text or ""))


# ---------------------------------------------------------------------------
# Numbers, currencies, measurements
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?(?![\w])")


def numeric_occurrences(text: str) -> List[Tuple[float, int, int]]:
    """Return (value, start, end) for every numeric token in `text`."""
    out: List[Tuple[float, int, int]] = []
    for match in _NUMBER_RE.finditer(text or ""):
        raw = match.group(0).replace(",", "")
        try:
            value = float(raw)
        except ValueError:  # pragma: no cover - defensive
            continue
        out.append((value, match.start(), match.end()))
    return out


def numeric_values(text: str) -> set[float]:
    """Set of numeric values mentioned in `text` (comma-insensitive)."""
    return {value for value, _, _ in numeric_occurrences(text)}


# Approximation markers - used both to detect dropped qualifiers and to detect
# approximate values restated as exact ones.
APPROXIMATION_MARKERS = (
    "about",
    "around",
    "approximately",
    "approx",
    "roughly",
    "nearly",
    "almost",
    "some",
    "over",
    "under",
    "more than",
    "less than",
    "fewer than",
    "at least",
    "at most",
    "up to",
    "as many as",
    "as much as",
    "an estimated",
    "estimated",
    "close to",
    "in excess of",
    "just under",
    "just over",
)

# Speculative / epistemic markers. If the supporting sentence is speculative and
# the claim drops every trace of speculation (and reports no announcement), the
# claim has converted a possibility into a fact -> reject.
SPECULATIVE_MARKERS = (
    "may",
    "might",
    "could",
    "possibly",
    "probably",
    "likely",
    "unlikely",
    "expect",
    "expects",
    "predict",
    "predicts",
    "predicted",
    "projected",
    "projects",
    "projections",
    "forecast",
    "forecasts",
    "estimate",
    "estimates",
    "estimated",
    "anticipate",
    "anticipates",
    "anticipated",
    "aims",
    "aim",
    "intends",
    "intend",
    "plans",
    "plan",
    "planned",
    "hopes",
    "hope",
    "seeks",
    "appears",
    "seems",
    "suggests",
    "reportedly",
    "allegedly",
    "suspected",
    "believed",
    "rumoured",
    "rumored",
    "potential",
    "potentially",
    "if",
    "whether",
    "set to",
)

# Attribution / announcement markers. Reporting an announcement (even about a
# future plan) is a FACTUAL claim about the announcement, so these count as
# preserving the article's epistemic stance.
ATTRIBUTION_MARKERS = (
    "said",
    "says",
    "announced",
    "announces",
    "announcement",
    "stated",
    "states",
    "declared",
    "unveiled",
    "according to",
    "told",
    "confirmed",
    "informed",
    "mentioned",
    "noted",
    "added",
    "claimed",
    "testified",
    "reported",
    "reports",
)

FUTURE_MARKERS = (
    "will",
    "shall",
    "is going to",
    "are going to",
    "would",
    "by 20",
    "next year",
    "coming years",
    "in the future",
)

_PREDICTION_PATTERNS = re.compile(
    r"\b(?:will|shall|may|might|could|would|expected|expects?|anticipated?|"
    r"anticipates?|projected|forecast(?:s|ed)?|predicts?|predicted|"
    r"set to|on track to|aims? to|intends? to|plans? to|likely to|"
    r"is going to|are going to)\b",
    re.IGNORECASE,
)


def _marker_positions(text: str, markers: Sequence[str]) -> List[Tuple[str, int]]:
    """Find marker occurrences as whole words/phrases, case-insensitive."""
    lowered = (text or "").lower()
    found: List[Tuple[str, int]] = []
    for marker in markers:
        pattern = r"(?<![\w])" + re.escape(marker) + r"(?![\w])"
        for match in re.finditer(pattern, lowered):
            found.append((marker, match.start()))
    return sorted(found, key=lambda item: item[1])


def has_marker(text: str, markers: Sequence[str]) -> bool:
    return bool(_marker_positions(text, markers))


def has_approximation_near(text: str, start: int, window: int = 40) -> bool:
    """True if an approximation qualifier appears shortly before `start`."""
    prefix = (text or "")[max(0, start - window) : start].lower()
    return any(
        re.search(r"(?<![\w])" + re.escape(marker) + r"\s*$", prefix) for marker in APPROXIMATION_MARKERS
    )


# ---------------------------------------------------------------------------
# Lemmas / content words - with degraded fallback
# ---------------------------------------------------------------------------

_STOPISH = {
    "be",
    "have",
    "do",
    "use",
    "make",
    "get",
    "go",
    "come",
    "say",
    "-pron-",
}

# Comprehensive stopwords for degraded mode (fallback when spaCy not available)
_DEGRADED_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "while", "with", "for", "to", "of", "in", "on", "at", "by", "from", "as", "is", "are", "was", "were", "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should", "may", "might", "must", "can", "could", "this", "that", "these", "those", "it", "its", "they", "them", "their", "we", "us", "our", "you", "your", "he", "she", "him", "her", "his", "hers", "i", "me", "my", "mine", "which", "what", "who", "whom", "whose", "where", "when", "why", "how", "so", "than", "too", "very", "just", "only", "also", "even", "now", "then", "here", "there", "up", "out", "about", "into", "over", "after", "before", "between", "under", "again", "further", "once", "more", "most", "some", "any", "no", "not", "nor", "am", "are", "has", "had", "were", "been", "being", "having", "doing", "would", "should", "could", "ought", "ve", "ll", "re", "d", "m", "t", "s",
}

# Simple lemmatization map for common inflections (degraded mode)
_LEMMA_MAP = {
    "launched": "launch",
    "visited": "visit",
    "met": "meet",
    "signed": "sign",
    "installed": "install",
    "announced": "announce",
    "reported": "report",
    "sold": "sell",
    "grew": "grow",
    "projects": "project",
    "factories": "factory",
    "workers": "worker",
    "capacities": "capacity",
    "parks": "park",
}


def _simple_lemmatize(word: str) -> str:
    """Very light lemmatizer for degraded mode."""
    lower = word.lower()
    if lower in _LEMMA_MAP:
        return _LEMMA_MAP[lower]
    # Simple suffix rules
    if lower.endswith("ies") and len(lower) > 4:
        return lower[:-3] + "y"
    if lower.endswith("es") and len(lower) > 4:
        # e.g., "launches" -> "launch"
        candidate = lower[:-2]
        if candidate.endswith("ch") or candidate.endswith("sh") or candidate.endswith("ss") or candidate.endswith("x"):
            return candidate
    if lower.endswith("s") and len(lower) > 3 and not lower.endswith("ss"):
        return lower[:-1]
    if lower.endswith("ing") and len(lower) > 5:
        return lower[:-3]
    if lower.endswith("ed") and len(lower) > 4:
        candidate = lower[:-2]
        if len(candidate) >= 2:
            return candidate
        return lower[:-1]
    return lower


@lru_cache(maxsize=2048)
def _content_lemmas(text: str) -> frozenset[str]:
    # Degraded fallback: use regex tokenization + simple lemmatization
    if nlp_is_degraded():
        return _content_lemmas_degraded(text)
    
    nlp = get_nlp()
    doc = nlp(text or "")
    lemmas = set()
    for token in doc:
        if token.is_stop or token.is_punct or token.is_space:
            continue
        if not (token.is_alpha or token.like_num):
            continue
        lemma = token.lemma_.lower().strip()
        if len(lemma) < 2 and not token.like_num:
            continue
        if lemma in _STOPISH and not token.like_num:
            continue
        lemmas.add(lemma)
    # Fallback if spaCy produced nothing useful (e.g., blank model edge case)
    if not lemmas and text and text.strip():
        return _content_lemmas_degraded(text)
    return frozenset(lemmas)


def _content_lemmas_degraded(text: str) -> frozenset[str]:
    """Regex-based content lemmas for when spaCy model is unavailable."""
    if not text:
        return frozenset()
    # Tokenize into words and numbers
    tokens = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:,\d+)*(?:\.\d+)?%?", text)
    lemmas = set()
    for token in tokens:
        lower = token.lower().strip(".,;:!?\"'()[]")
        if not lower or len(lower) < 2:
            # Keep numbers even if single char? But filter single letters
            if token.isdigit() or re.match(r"^\d", token):
                lemmas.add(lower)
            continue
        if lower in _DEGRADED_STOPWORDS:
            continue
        if lower in NON_ENTITY_TOKENS:
            # Keep numeric units as they are distinctive? But skip generic units
            # Actually keep them as they help distinguish claims, but normalize
            lemmas.add(lower)
            continue
        lemma = _simple_lemmatize(lower)
        if len(lemma) >= 2:
            lemmas.add(lemma)
        elif token.isdigit():
            lemmas.add(lower)
    return frozenset(lemmas)


def content_lemmas(text: str) -> frozenset[str]:
    return _content_lemmas(normalize_text(text))


def lexical_recall(claim_text: str, article_text: str) -> float:
    """Fraction of the claim's content lemmas that occur in the article."""
    claim_lemmas = content_lemmas(claim_text)
    if not claim_lemmas:
        return 0.0
    article_lemmas = content_lemmas(article_text)
    return len(claim_lemmas & article_lemmas) / len(claim_lemmas)


# ---------------------------------------------------------------------------
# Entities - with degraded fallback
# ---------------------------------------------------------------------------


def _clean_entity(surface: str) -> str:
    entity = " ".join((surface or "").split())
    for article in _LEADING_ARTICLES:
        if entity.lower().startswith(article):
            entity = entity[len(article) :]
            break
    return entity.strip(" ,.;:'\"()[]")


def _is_generic(entity: str) -> bool:
    lowered = entity.lower()
    if lowered in GENERIC_ENTITY_PHRASES or len(entity) < 2:
        return True
    if lowered in NON_ENTITY_TOKENS or lowered in GENERIC_SINGLETON_ENTITIES:
        return True
    if not any(char.isalpha() for char in entity):
        return True
    # All-caps boilerplate like "GW" or "PMO" that is really a unit/abbrev.
    words = [word.strip(".,") for word in lowered.split() if word.strip(".,")]
    if words and all(word in NON_ENTITY_TOKENS for word in words):
        return True
    # Single generic word that is capitalized but common (e.g., "Services" at sentence start)
    if len(words) == 1 and lowered in GENERIC_SINGLETON_ENTITIES:
        return True
    return False


# Regex for entity extraction in degraded mode
# Matches capitalized sequences: "PM Modi", "United States", "India", "Trump", "New York", "Apple Inc."
_DEGRADED_ENTITY_RE = re.compile(
    r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}|[A-Z]{2,}(?:\s+[A-Z]{2,})?|PM\s+[A-Z][a-z]+|USA|US|UK|UAE|EU)\b"
)
# More specific: includes titles and known entities
_TITLED_ENTITY_RE = re.compile(
    r"\b(?:PM|President|Minister|CEO|Dr|Mr|Mrs|Ms)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b"
)


def _extract_entities_degraded(text: str) -> List[str]:
    """Extract entities using regex heuristics when spaCy model unavailable."""
    entities = []
    seen = set()
    
    def _push_candidate(candidate: str):
        cleaned = _clean_entity(candidate)
        if not cleaned or _is_generic(cleaned):
            return
        # Filter out single common words that are capitalized but not entities
        if len(cleaned.split()) == 1:
            lower = cleaned.lower()
            # Skip if it's a common word at sentence start that's not likely an entity
            if lower in _DEGRADED_STOPWORDS and lower not in {"india", "china", "usa", "russia", "japan", "germany", "france", "italy", "canada", "australia", "brazil", "mexico"}:
                # But keep if it's all caps or title case and appears multiple times? Simple: skip common words
                if lower not in {"india", "america", "usa", "russia", "china", "japan", "germany", "france"}:
                    # Actually check if word is capitalized in middle of sentence vs start
                    # For now, be permissive: keep capitalized words that look like proper nouns
                    pass
            # Need at least one uppercase letter and not all lowercase
            if not any(c.isupper() for c in cleaned):
                return
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            entities.append(cleaned)
    
    # Find titled entities first (higher priority)
    for match in _TITLED_ENTITY_RE.finditer(text):
        _push_candidate(match.group(0))
    
    # Find general capitalized entities
    for match in _DEGRADED_ENTITY_RE.finditer(text):
        candidate = match.group(0).strip()
        # Avoid single-letter matches
        if len(candidate) < 2:
            continue
        # Skip if it's just "The", "India" is okay, but "The" alone not
        if candidate.lower() in {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "of", "for", "with", "this", "that", "these", "those"}:
            continue
        _push_candidate(candidate)
    
    # Also detect well-known entities via simple dictionary
    known_entities = ["India", "USA", "U.S.A", "America", "Trump", "Modi", "PM Modi", "PM Narendra Modi", "Narendra Modi", "Apple", "Google", "Microsoft", "China", "Russia", "Japan", "Germany", "France", "UK", "UAE", "Israel", "Pakistan", "Bangladesh", "Solar", "Gujarat", "Delhi", "Mumbai"]
    lowered_text = text.lower()
    for known in known_entities:
        if known.lower() in lowered_text:
            # Extract with original casing from text
            pattern = re.compile(re.escape(known), re.IGNORECASE)
            for m in pattern.finditer(text):
                _push_candidate(m.group(0))
    
    return entities


def extract_entities(
    claim_text: str,
    article_text: str | None = None,
    extra_candidates: Iterable[str] | None = None,
) -> List[str]:
    """Extract the named entities relevant to `claim_text`.

    Args:
        claim_text: the claim to analyse.
        article_text: optional article used to resolve context-dependent claims
            (e.g. "The 15 projects ..." -> the article's subject "India") and to
            ground every returned entity. Entities not present in the article
            are dropped.
        extra_candidates: entities suggested by the LLM; kept only if they are
            grounded by the article, de-duplicated case-insensitively.

    Returns:
        Ordered, de-duplicated list of entity surfaces.
    """
    claim_text = normalize_text(claim_text)
    article_text = normalize_text(article_text) if article_text else ""

    nlp = get_nlp()
    doc = nlp(claim_text)
    entities: List[str] = []
    seen: set[str] = set()

    def _push(entity: str) -> None:
        entity = _clean_entity(entity)
        if not entity or _is_generic(entity):
            return
        key = entity.casefold()
        if key in seen:
            return
        # Grounding: an entity must be supported by the article, whether it came
        # from the claim text or from the provider's suggestions. This is what
        # stops fabricated entities from surviving into a claim.
        if article_text and key not in article_text.casefold():
            return
        seen.add(key)
        entities.append(entity)

    # Use spaCy entities if available and not degraded
    if not nlp_is_degraded():
        for ent in doc.ents:
            if ent.label_ in ENTITY_LABELS:
                _push(ent.text)
    else:
        # Degraded: use regex entities
        for entity in _extract_entities_degraded(claim_text):
            _push(entity)

    for candidate in extra_candidates or []:
        if candidate and isinstance(candidate, str):
            _push(candidate)

    if not entities and article_text:
        # Context-dependent claim (e.g. "The projects have 10 GW capacity"):
        # borrow the most salient entity of the article so the claim carries a
        # subject, but only if that entity is genuinely grounded.
        if not nlp_is_degraded():
            for ent in nlp(article_text).ents:
                if ent.label_ in ENTITY_LABELS:
                    candidate = _clean_entity(ent.text)
                    if candidate and not _is_generic(candidate):
                        _push(candidate)
                        break
        else:
            # Degraded fallback: use most frequent capitalized entity from article
            degraded_ents = _extract_entities_degraded(article_text)
            if degraded_ents:
                # Count occurrences
                counts = {}
                for ent in degraded_ents:
                    key = ent.casefold()
                    counts[key] = counts.get(key, 0) + 1
                if counts:
                    best = max(counts, key=lambda k: counts[k])
                    for ent in degraded_ents:
                        if ent.casefold() == best:
                            _push(ent)
                            break

    return entities


def main_article_entity(
    text: str, labels: tuple[str, ...] = ("ORG", "GPE", "PERSON", "NORP", "LOC")
) -> str | None:
    """Most frequently mentioned entity of the given labels in an article."""
    if nlp_is_degraded():
        entities = _extract_entities_degraded(normalize_text(text))
        if not entities:
            return None
        counts: dict[str, int] = {}
        surfaces: dict[str, str] = {}
        # Score entities: PERSON/ORG get bonus, longer/more specific gets bonus
        scores: dict[str, float] = {}
        for ent in entities:
            candidate = _clean_entity(ent)
            if not candidate or _is_generic(candidate):
                continue
            key = candidate.casefold()
            counts[key] = counts.get(key, 0) + 1
            surfaces.setdefault(key, candidate)
            # Heuristic: Prefer PERSON-like (e.g., PM Modi, Trump) and longer entities
            # PM Modi has 2 words, USA has 1 -> PM Modi should win on tie
            # Also, known person names get bonus
            score_bonus = 0.0
            if len(candidate.split()) > 1:
                score_bonus += 0.5  # multi-word entities are more specific
            if any(title in candidate for title in ["PM ", "President", "Minister", "Dr ", "Mr ", "Ms ", "Mrs "]):
                score_bonus += 1.0
            # Prefer entities that appear as PERSON (contain Modi, Trump, etc. vs USA)
            if candidate.lower() not in {"usa", "us", "uk", "uae", "india", "china", "russia"} or len(candidate.split()) > 1:
                # Don't give extra penalty for GPE, but prefer non-GPE when tied
                pass
            scores[key] = score_bonus

        if not counts:
            return None
        # Sort by count, then bonus, then length (longer wins)
        def sort_key(k):
            return (counts[k], scores.get(k, 0), len(k))
        best = max(counts, key=sort_key)
        return surfaces[best]

    counts: dict[str, int] = {}
    surfaces: dict[str, str] = {}
    for ent in get_nlp()(normalize_text(text)).ents:
        if ent.label_ not in labels:
            continue
        candidate = _clean_entity(ent.text)
        if not candidate or _is_generic(candidate):
            continue
        key = candidate.casefold()
        counts[key] = counts.get(key, 0) + 1
        surfaces.setdefault(key, candidate)
    if not counts:
        return None
    # Prefer more frequent, then longer entity (more specific)
    best = max(counts, key=lambda key: (counts[key], len(key)))
    return surfaces[best]


def salient_org_entity(text: str) -> str | None:
    """Most frequent organisation/person entity ("the company" -> Apple)."""
    return main_article_entity(text, labels=("ORG", "PERSON"))


def _proper_nouns(text: str) -> List[str]:
    if nlp_is_degraded():
        # In degraded mode, consider capitalized words as proper nouns
        # but filter to avoid false positives
        candidates = re.findall(r"\b[A-Z][a-z]{2,}\b", normalize_text(text))
        # Filter stopwords and generic
        out = []
        for cand in candidates:
            if cand.lower() in _DEGRADED_STOPWORDS:
                continue
            if _is_generic(cand):
                continue
            out.append(cand)
        # Also include all-caps like USA, PM etc.
        caps = re.findall(r"\b[A-Z]{2,}\b", normalize_text(text))
        for cand in caps:
            if cand not in out and not _is_generic(cand):
                out.append(cand)
        return out
    
    doc = get_nlp()(normalize_text(text))
    out = []
    for token in doc:
        if token.pos_ == "PROPN" and len(token.text) > 1:
            out.append(token.text)
    return out


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

_FLOAT_TOLERANCE = 1e-6


def _values_match(a: float, b: float) -> bool:
    return abs(a - b) <= _FLOAT_TOLERANCE


def _number_in_article(value: float, article_values: set[float]) -> bool:
    return any(_values_match(value, candidate) for candidate in article_values)


def _fuzzy_in(candidate: str, text: str) -> bool:
    c_lower = candidate.strip().casefold()
    t_lower = text.casefold()
    if c_lower in t_lower:
        return True
    if len(c_lower) < 5:
        return False
    import difflib
    words = re.findall(r"\b\w+\b", t_lower)
    return bool(difflib.get_close_matches(c_lower, words, n=1, cutoff=0.75))


@dataclass(frozen=True)
class ArticleContext:
    """Everything about the article that validation needs, computed once.

    Building this once per extraction run (instead of per claim) keeps a
    100-sentence article at ~1s instead of ~2 minutes: sentence lemmas, article
    lemmas and numeric spans are reused across every claim.
    """

    text: str
    sentences: tuple[str, ...]
    sentence_lemmas: tuple[frozenset[str], ...]
    lemmas: frozenset[str]
    numbers: frozenset[float]
    numeric_spans: tuple[tuple[float, int, int], ...]


def build_article_context(
    article_text: str, sentences: Sequence[str] | None = None
) -> ArticleContext:
    article = normalize_text(article_text)
    sentence_list = tuple(sentences) if sentences is not None else tuple(split_sentences(article))
    if not sentence_list:
        sentence_list = (article,)
    return ArticleContext(
        text=article,
        sentences=sentence_list,
        sentence_lemmas=tuple(content_lemmas(sentence) for sentence in sentence_list),
        lemmas=content_lemmas(article),
        numbers=frozenset(numeric_values(article)),
        numeric_spans=tuple(numeric_occurrences(article)),
    )


def find_support_sentence(
    claim_text: str,
    sentences: Sequence[str],
    sentence_lemmas: Sequence[frozenset[str]] | None = None,
) -> tuple[int, str, float]:
    """Return (index, sentence, overlap) of the article sentence that best
    supports the claim, using content-lemma overlap."""
    claim_lemmas = content_lemmas(claim_text)
    best_index, best_sentence, best_score = -1, "", 0.0
    if not claim_lemmas:
        return best_index, best_sentence, best_score
    if sentence_lemmas is None:
        sentence_lemmas = [content_lemmas(sentence) for sentence in sentences]
    for index, lemmas in enumerate(sentence_lemmas):
        if not lemmas:
            continue
        overlap = len(claim_lemmas & lemmas) / len(claim_lemmas)
        if overlap > best_score:
            best_index, best_sentence, best_score = index, sentences[index], overlap
    return best_index, best_sentence, best_score


def check_claim(
    claim_text: str,
    article_text: str,
    settings: Settings | None = None,
    sentences: Sequence[str] | None = None,
    context: ArticleContext | None = None,
) -> ValidationReport:
    """Run every strict-validation check and return a detailed report.

    Pass `context` to reuse article-level work across many claims.
    """
    settings = settings or get_settings()
    report = ValidationReport(ok=True)

    claim = normalize_text(claim_text)
    if context is None:
        article = normalize_text(article_text)
    else:
        article = context.text

    if not claim:
        report.add("empty_claim")
        return report
    if not article:
        report.add("empty_article")
        return report

    if context is None:
        context = build_article_context(article, sentences)

    claim_tokens = claim.split()
    if len(claim_tokens) < settings.min_claim_tokens:
        report.add(f"claim_too_short:{len(claim_tokens)}")
    if len(claim_tokens) > settings.max_claim_tokens:
        report.add(f"claim_too_long:{len(claim_tokens)}")

    claim_lemmas = content_lemmas(claim)
    if not claim_lemmas:
        report.add("no_content_words")
        return report

    # 1) Lexical grounding (lemma recall against the whole article).
    recall = len(claim_lemmas & context.lemmas) / len(claim_lemmas)
    if recall < settings.lexical_recall_threshold:
        report.add(f"low_lexical_recall:{recall:.2f}")

    # 2) Numeric grounding - no invented or altered numbers.
    if settings.enable_number_check:
        for value in sorted(numeric_values(claim)):
            if not _number_in_article(value, context.numbers):
                report.add(f"ungrounded_number:{value:g}")

    # 3) Entity grounding - no unsupported context expansion.
    if settings.enable_entity_grounding:
        for entity in extract_entities(claim, article_text=None):
            if not _fuzzy_in(entity, article):
                report.add(f"ungrounded_entity:{entity}")
        # Proper nouns that are not in the article are a fabrication smell.
        for proper in _proper_nouns(claim):
            if not _fuzzy_in(proper, article):
                report.add(f"ungrounded_proper_noun:{proper}")

    # Support sentence for stance checks.
    _, support, overlap = find_support_sentence(
        claim, context.sentences, context.sentence_lemmas
    )
    if overlap <= 0.0:
        report.add("no_supporting_sentence")

    # 4) Hedge integrity - predictions must not become established facts.
    if settings.enable_hedge_check and support:
        support_speculative = has_marker(support, SPECULATIVE_MARKERS)
        claim_has_stance = has_marker(claim, SPECULATIVE_MARKERS) or has_marker(
            claim, ATTRIBUTION_MARKERS
        )
        if support_speculative and not claim_has_stance:
            report.add("dropped_speculation")

    # 5) Qualifier integrity - approximate values must stay approximate.
    if settings.enable_qualifier_check:
        for value, start, _ in numeric_occurrences(claim):
            occurrences = [
                (article_start, article_end)
                for article_value, article_start, article_end in context.numeric_spans
                if _values_match(value, article_value)
            ]
            if not occurrences:
                continue  # already reported as ungrounded
            if all(
                has_approximation_near(article, article_start)
                for article_start, _ in occurrences
            ) and not has_approximation_near(claim, start):
                report.add(f"dropped_approximation:{value:g}")

    return report


def validate_claim(
    claim_text: str,
    article_text: str,
    settings: Settings | None = None,
) -> bool:
    """Return True only if `claim_text` is a faithful, grounded claim from the
    article. Never raises on malformed input; returns False instead."""
    try:
        return check_claim(claim_text, article_text, settings=settings).ok
    except Exception:  # pragma: no cover - defensive: validation must not crash
        return False


# ---------------------------------------------------------------------------
# Claim-type helpers (shared with the heuristic extractor)
# ---------------------------------------------------------------------------


def looks_like_prediction(text: str) -> bool:
    return bool(_PREDICTION_PATTERNS.search(text or ""))


_OPINION_MARKERS = (
    "i think",
    "i believe",
    "in my opinion",
    "arguably",
    "unfortunately",
    "impressive",
    "most impressive",
    "amazing",
    "brilliant",
    "terrible",
    "disappointing",
    "should",
    "must",
    "ought to",
    "beautiful",
    "worst",
    "best",
    "shameful",
    "outstanding",
    "ridiculous",
    "clearly the",
    "obviously the",
    "overrated",
    "underrated",
    "superb",
    "wonderful",
    "disgraceful",
)


def looks_like_opinion(text: str) -> bool:
    lowered = (text or "").lower()
    return has_marker(lowered, _OPINION_MARKERS)


_VAGUE_MARKERS = (
    "many people",
    "some people",
    "several sources",
    "sources say",
    "it is said",
    "widely regarded",
    "everyone knows",
    "they say",
    "various reports",
    "some experts",
    "a lot of",
    "huge number",
    "countless",
)


def looks_vague(text: str) -> bool:
    return has_marker((text or "").lower(), _VAGUE_MARKERS)


RELATIONAL_HEAD_NOUNS = {
    "couple": "They are a couple.",
    "spouses": "They are spouses.",
    "partners": "They are partners.",
    "husband": "The individual is a husband.",
    "wife": "The individual is a wife.",
    "brothers": "They are brothers.",
    "sisters": "They are sisters.",
}


def extract_dense_propositions(sentences: Sequence[str]) -> List[RawClaim]:
    """Extract fine-grained relational and demographic propositions from sentences."""
    propositions: List[RawClaim] = []
    nlp = get_nlp()
    for s_idx, sentence in enumerate(sentences):
        cleaned = normalize_text(sentence)
        if not cleaned:
            continue
        try:
            doc = nlp(cleaned)
        except Exception:
            continue
        for token in doc:
            lemma = token.lemma_.lower()
            # 1. Relational Noun Extraction (e.g. couple, spouses)
            if lemma in RELATIONAL_HEAD_NOUNS and token.dep_ in ("nsubj", "nsubjpass", "pobj", "dobj", "attr"):
                propositions.append(
                    RawClaim(
                        text=RELATIONAL_HEAD_NOUNS[lemma],
                        claim_type="factual",
                        source_sentence_index=s_idx,
                        entities=[],
                    )
                )

            # 2. Nationalities/Attributes modifying relational nouns (e.g. "bangaldeshi couple")
            _NON_ATTR = {
                "the", "this", "that", "each", "every", "some", "other", "all",
                "first", "second", "third", "last", "next", "prime", "chief",
                "former", "current", "acting", "deputy", "senior", "junior",
                "new", "old", "young", "top", "lead", "main", "good", "bad",
                "very", "more", "most", "less", "least", "great", "small", "big"
            }
            if token.dep_ in ("compound", "amod") and token.head.lemma_.lower() in ("couple", "spouses", "family"):
                attr_text = token.text
                head_text = token.head.text
                if len(attr_text) >= 3 and attr_text.lower() not in _NON_ATTR:
                    claim_text = f"The {head_text.lower()} is {attr_text}."
                    claim_text = claim_text[0].upper() + claim_text[1:]
                    propositions.append(
                        RawClaim(
                            text=claim_text,
                            claim_type="factual",
                            source_sentence_index=s_idx,
                            entities=[attr_text] if token.pos_ in ("PROPN", "ADJ") else [],
                        )
                    )
    return propositions
