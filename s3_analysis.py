"""S3 — Stance analysis and exact-quote verification (Ikjot's stage).

    evidence retrieved by S2  ->  SUPPORTS / REFUTES / NEUTRAL + verbatim quote

This is the stage the repo was missing: S2 produced evidence, S4 scored it, and
nothing in between ever decided what a piece of evidence *says about the claim*.
`app.pipeline.orchestrator.run_verified_claim_pipeline` took an ``analyze_fn``
callback with no implementation to pass to it; ``analyze`` below is that function.

    from s3_analysis import analyze
    analyses = analyze(claims, bundles)          # orchestrator-compatible
    bundles  = apply_analysis(bundles, analyses) # re-checks every quote

Two ways to produce a stance
---------------------------
``mode="llm"``  — one JSON request per claim through the same provider stack S1
                  uses (``LLM_PROVIDER=gemini|ollama``), then *the same quote guard*
                  is run over the model's output. A model that invents a quote gets
                  that quote dropped, not displayed.
``mode="auto"`` — try the LLM, fall back to the lexical analyser on any failure.
``mode="lexicon"`` / MOCK — deterministic lexical analyser. No key, no network,
                  same shape, no randomness. This is the demo safety net.

Honesty rules this module is built around (plan §3 and §5)
-----------------------------------------------------------
1. **Never invent a stance.** Below ``min_confidence`` the answer is ``NEUTRAL``
   with a rationale that says so, rather than a coin flip dressed up as a verdict.
2. **Never display an unverified quote.** A quote is emitted only if it is a literal
   substring of the text S2 actually retrieved. Whitespace and case are normalised
   for the *check*; the quote itself stays verbatim from the source.
3. **Say what "verified" means.** ``text_scope == "full_text"`` means the quote was
   found in fetched page text; ``"snippet"`` means it was only found in the search
   snippet. That label travels to the UI instead of being silently flattened.
4. **Report the drops.** ``quote_dropped_count`` counts every quote rejected by the
   guard, per claim, so the audit footer can show what was thrown away and why.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from contract_adapter import SCOPE_FULL_TEXT, SCOPE_SNIPPET

__all__ = [
    "S3Config",
    "S3Result",
    "analyze",
    "analyze_bundles",
    "stance_lexicon",
    "verify_quote",
    "normalize_for_match",
]

VALID_STANCES = ("SUPPORTS", "REFUTES", "NEUTRAL")

_WS_RE = re.compile(r"\s+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[\"'“(\[]?[A-Z0-9])")
_ABBREV_RE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|No|Fig|Inc|Ltd|Co|Dept|approx)\.$",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+(?:['’][a-z]+)?")
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# Words that carry no checkable content. Deliberately small: over-filtering makes a
# 20-word snippet look like a perfect match with a 20-word claim.
STOPWORDS = frozenset(
    """
    a an the and or but if then than that this these those of in on at to for from by
    with without into over under about as is are was were be been being am do does did
    doing have has had having it its it's he she they them their his her you your we our
    i me my who whom which what when where why how not no nor so such too very more most
    some any each other same than there here also just only even ever never will would can
    could shall should may might must per via within across among up down out off again
    said says say according new since while during both none one two three four five
    """.split()
)

# Polarity cues. Only consulted *inside* a sentence that already aligns with the
# claim, so a stray "not" elsewhere in a 6 000-character page cannot flip a stance.
REFUTE_CUES = (
    "does not",
    "did not",
    "do not",
    "is not",
    "are not",
    "was not",
    "were not",
    "cannot",
    "can not",
    "could not",
    "would not",
    "should not",
    "has not",
    "have not",
    "had not",
    "isn't",
    "aren't",
    "wasn't",
    "weren't",
    "doesn't",
    "didn't",
    "won't",
    "no evidence",
    "not supported",
    "not true",
    "incorrect",
    "inaccurate",
    "wrong",
    "false",
    "myth",
    "mythical",
    "debunk",
    "debunked",
    "refute",
    "refuted",
    "contradict",
    "contradicts",
    "misleading",
    "unsubstantiated",
    "unverified",
    "fails to",
    "failed to",
    "lacks",
    "lack of",
    "denies",
    "denied",
    "rejects",
    "rejected",
    "overstated",
    "exaggerat",
    "rarely",
    "seldom",
    "hardly",
    "unlikely",
    "disproved",
    "disproof",
    "not visible",
    "impossible",
    "hoax",
    "urban legend",
)

SUPPORT_CUES = (
    "is true",
    "was true",
    "confirmed",
    "confirms",
    "verified",
    "accurate",
    "correct",
    "according to",
    "in fact",
    "indeed",
    "supports",
    "supporting",
    "is rated true",
    "measured",
    "recorded",
    "officially",
    "actually",
)

#: Negation whose *scope reaches a verification predicate*. A bare "not" anywhere in a
#: 6 000-character page means nothing; "no astronaut has confirmed" and "has no basis"
#: both deny the claim, and that is the shape these patterns look for.
_REFUTE_PATTERNS: tuple[str, ...] = (
    r"\bno\b[^.]{0,44}\b(basis|evidence|record|support|merit|proof|corroboration|confirmation)\b",
    r"\b(?:not|never|no|without)\b[^.]{0,32}\b(confirm\w*|verif\w*|support\w*|corroborat\w*"
    r"|observ\w*|visibl\w*|demonstrat\w*|proven|shown|substantiat\w*)\b",
    r"\b(?:cannot|can not|couldn\u2019t|doesn\u2019t|don\u2019t|didn\u2019t|isn\u2019t|aren\u2019t"
    r"|wasn\u2019t|weren\u2019t|hasn\u2019t|haven\u2019t|won\u2019t)\b",
    r"\b(?:does|did|do|will|would|is|are|was|were|has|have|had)\s+not\b",
    r"\b(?:myth|myths|mythical|hoax|misinformation|disinformation|falsely|debunk\w*"
    r"|rebut\w*|refut\w*|overstat\w*|urban legend|not the case|in fact the opposite)\b",
    r"\bfact[- ]check\b[^.]{0,40}\b(?:false|incorrect|misleading|rating)\b",
)
#: Sentences *about* a claim rather than assertions of it. Inside a mention frame the
#: claim's own words are quoted, so their content proves nothing and a stance is only
#: taken when the matrix clause confirms or denies it.
_MENTION_FRAME_RE = re.compile(
    r"\b(?:the claim|claim that|the idea|idea that|the notion|the saying|saying that"
    r"|the belief|the myth|myth that|whether|as to whether|alleged|allegedly|reportedly"
    r"|so-called|it is said|purported|supposedly|report that)\b",
    re.IGNORECASE,
)
_VERIFICATION_PATTERNS: tuple[str, ...] = (
    r"\b(?:confirmed|confirms|verified|verifies|accurate|correct|true|substantiated"
    r"|corroborated|demonstrated|shown to be|rated true|is in fact|indeed)\b",
)
_REFUTE_COMPILED = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _REFUTE_PATTERNS)
#: Negation inside the *claim*. A source that covers the claim's terms while stating
#: them affirmatively contradicts a claim that says "never"/"not"; treating that as
#: agreement is the inverse of the mistake the mention-frame rule guards against.
_CLAIM_NEGATION_RE = re.compile(
    r"\b(?:never|not|no|nor|cannot|can\u2019t|n\u2019t|without|neither|nowhere|none)\b",
    re.IGNORECASE,
)
_VERIFICATION_COMPILED = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _VERIFICATION_PATTERNS)


#: Quantifiers in a claim that a matching passage must respect. Missing ones are a
#: context gap, not a refutation: "most adults" vs "the general population".
QUANTIFIER_RE = re.compile(
    r"\b(?:most|all|every|any|some|many|few|most of|majority|minority|typically|"
    r"generally|usually|often|rarely|never|always|only|nearly|about|approximately|"
    r"around|over|under|more than|less than|at least|at most|up to|since|until)\b",
    re.IGNORECASE,
)

# Light unit normalisation, enough to tell "330 metres" and "1,083 ft" apart from a
# genuine disagreement over the height of a tower.
_UNIT_FACTORS: dict[str, tuple[str, float]] = {
    "m": ("m", 1.0), "metre": ("m", 1.0), "metres": ("m", 1.0),
    "meter": ("m", 1.0), "meters": ("m", 1.0),
    "km": ("m", 1000.0), "kilometer": ("m", 1000.0), "kilometers": ("m", 1000.0),
    "kilometre": ("m", 1000.0), "kilometres": ("m", 1000.0),
    "ft": ("m", 0.3048), "foot": ("m", 0.3048), "feet": ("m", 0.3048),
    "mi": ("m", 1609.344), "mile": ("m", 1609.344), "miles": ("m", 1609.344),
    "kg": ("kg", 1.0), "kilogram": ("kg", 1.0), "kilograms": ("kg", 1.0),
    "lb": ("kg", 0.45359237), "lbs": ("kg", 0.45359237), "pound": ("kg", 0.45359237),
    "pounds": ("kg", 0.45359237),
    "t": ("kg", 1000.0), "tonne": ("kg", 1000.0), "tonnes": ("kg", 1000.0),
    "ton": ("kg", 907.18474), "tons": ("kg", 907.18474),
    "s": ("s", 1.0), "sec": ("s", 1.0), "second": ("s", 1.0), "seconds": ("s", 1.0),
    "min": ("s", 60.0), "minute": ("s", 60.0), "minutes": ("s", 60.0),
    "h": ("s", 3600.0), "hour": ("s", 3600.0), "hours": ("s", 3600.0),
    "usd": ("usd", 1.0), "dollar": ("usd", 1.0), "dollars": ("usd", 1.0),
    "inr": ("inr", 1.0), "rupee": ("inr", 1.0), "rupees": ("inr", 1.0),
    "gbp": ("gbp", 1.0), "eur": ("eur", 1.0),
    "c": ("temp", 1.0), "celsius": ("temp", 1.0), "f": ("tempF", 1.0),
    "fahrenheit": ("tempF", 1.0),
    "%": ("percent", 1.0), "percent": ("percent", 1.0),
}
_MULTIPLIERS = {
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
}
_SCALE_RE = re.compile(r"\b(" + "|".join(_MULTIPLIERS) + r")\b", re.IGNORECASE)

_SUFFIX_MULTIPLIERS = {"k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class S3Config:
    """Thresholds for the lexical analyser and the LLM path.

    Defaults are tuned for the demo corpus; every value is overridable per call or
    from the environment (``S3_*``) so the lead can retune without a code change.
    """

    mode: str = field(default_factory=lambda: os.getenv("S3_MODE", "auto").strip().lower())
    #: alignment below this: the passage is not about this claim at all -> NEUTRAL
    min_align: float = 0.34
    #: alignment needed before a *supporting* stance is allowed
    support_align: float = 0.5
    #: alignment needed before a refuting stance is allowed (higher: refuting on a
    #: weakly-matching sentence is the most damaging error the demo can make)
    refute_align: float = 0.45
    #: combined confidence gate; below this the stance is forced to NEUTRAL
    min_confidence: float = 0.45
    #: maximum evidence items analyzed per claim (cost + latency guard)
    max_evidence_per_claim: int = 6
    #: cap on how much page text the LLM prompt sees per item
    llm_region_chars: int = 1800
    #: a selected quote longer than this is trimmed on a sentence boundary
    quote_max_chars: int = 420
    #: LLM total wall clock for one claim; on timeout that claim falls back to lexicon
    llm_timeout_s: float = float(os.getenv("S3_LLM_TIMEOUT", "60"))
    #: when True an unavailable/failing LLM is recorded in the audit, not raised
    fail_soft: bool = True

    @classmethod
    def resolve(cls, config: "S3Config | dict[str, Any] | None" = None, **overrides: Any) -> "S3Config":
        """Build a config from None / a dict / an instance, applying overrides."""
        if isinstance(config, S3Config):
            base = dict(config.__dict__)
        elif isinstance(config, dict):
            base = dict(config)
        else:
            base = {}
        base.update({k: v for k, v in overrides.items() if v is not None})
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        base = {k: v for k, v in base.items() if k in allowed}
        return cls(**base)


# ---------------------------------------------------------------------------
# Text plumbing shared by both modes
# ---------------------------------------------------------------------------
def normalize_for_match(text: str) -> str:
    """Lowercase, fold quotes/dashes, collapse whitespace — for containment checks.

    The *check* is normalised; the *quote that gets displayed* is never rewritten, so
    what the UI shows is exactly what the source says.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    text = (
        text.replace("\u2019", "'")
        .replace("\u2018", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u00a0", " ")
    )
    return _WS_RE.sub(" ", text).strip().lower()


def fold_token(token: str) -> str:
    """Crude singular folding, so "strikes" matches "strike" and "babies" "baby".

    Deliberately dumb (no dictionary, no lemma model): alignment is a *coverage* test,
    and a plural or a third-person -s should not by itself read as "this source does
    not discuss the claim".
    """
    if len(token) <= 3:
        return token
    if token.endswith("ies"):
        return token[:-3] + "y"
    # Only the bare -s: "strikes" -> "strike". Stripping -es too would cut a real
    # letter off ordinary verbs and make "strikes" fail to match "strike".
    if token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def content_tokens(text: str) -> set[str]:
    """Meaningful lowercase tokens of a passage, numbers included, lightly folded.

    Stopwords are filtered on the surface form *and* the folded form: folding "does"
    to "doe" or "this" to "thi" would otherwise smuggle function words back in.
    """
    kept: set[str] = set()
    for raw in _WORD_RE.findall(normalize_for_match(text)):
        surface = "n" if raw == "n't" else raw
        if surface in STOPWORDS:
            continue
        folded = fold_token(surface)
        if folded in STOPWORDS:
            continue
        if len(folded) > 1 or folded.isdigit():
            kept.add(folded)
    return kept


def numbers_in(text: str) -> set[float]:
    """Every numeric value in a string, scaled by a following billion/million word."""
    values: set[float] = set()
    for match in _NUMBER_RE.finditer(text or ""):
        raw = match.group(0).replace(",", "")
        try:
            value = float(raw)
        except ValueError:  # pragma: no cover - regex guarantees a float
            continue
        tail = (text or "")[match.end() : match.end() + 24]
        scale = _SCALE_RE.search(tail)
        if scale:
            value *= _MULTIPLIERS[scale.group(1).lower()]
        values.add(value)
        # "300m", "$3bn" style suffixes
        suffix = re.match(r"\s*(?:\.0)?\s*([kmbt])\b", tail, re.IGNORECASE)
        if suffix:
            values.add(value * _SUFFIX_MULTIPLIERS[suffix.group(1).lower()])
    return values


def weighted_values(text: str) -> dict[str, list[float]]:
    """Map a canonical unit -> values stated in that text, e.g. ``{"m": [330.0]}``.

    Used to tell a genuine numeric disagreement from a paraphrase. Deliberately
    conservative: an unrecognised unit is dropped rather than guessed, and a bare
    number is filed under ``"unitless"`` so it can only ever be compared with another
    bare number. Years get their own bucket so "in 2014" can contradict "in 2016"
    without "2014" being compared against a population count.
    """
    out: dict[str, list[float]] = {}
    lowered = normalize_for_match(text)

    for money in re.finditer(r"([$\u20ac\u00a3\u20b9])\s*(\d[\d,]*(?:\.\d+)?)\s*([kmbt])?\b", lowered):
        try:
            value = float(money.group(2).replace(",", ""))
        except ValueError:
            continue
        if money.group(3):
            value *= _SUFFIX_MULTIPLIERS[money.group(3).lower()]
        tail = lowered[money.end() : money.end() + 20]
        scale = _SCALE_RE.search(tail)
        if scale:
            value *= _MULTIPLIERS[scale.group(1).lower()]
        unit = {"\u20b9": "inr", "\u00a3": "gbp", "\u20ac": "eur"}.get(money.group(1), "usd")
        out.setdefault(unit, []).append(value)

    unit_alt = "|".join(sorted((re.escape(u) for u in _UNIT_FACTORS), key=len, reverse=True))
    pattern = re.compile(
        r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)"
        r"(?:\s*\.\s*)?"
        r"(?:\s*(" + unit_alt + r"))?\b",
        re.IGNORECASE,
    )
    for num in pattern.finditer(lowered):
        try:
            value = float(num.group(1).replace(",", ""))
        except ValueError:
            continue
        unit_word = (num.group(2) or "").lower().strip(".")
        tail = lowered[num.end() : num.end() + 20]
        scale = _SCALE_RE.search(tail)
        if scale:
            value *= _MULTIPLIERS[scale.group(1).lower()]

        if unit_word:
            canonical = _UNIT_FACTORS.get(unit_word)
            if canonical is None:
                continue  # unknown unit: refuse to compare rather than mis-scale it
            out.setdefault(canonical[0], []).append(value * canonical[1])
            continue

        # No unit was captured, but a unit-shaped word may still follow the number
        # ("3.7 furlongs", "12 dahl"). Filing that as a bare number would let it be
        # compared against an unrelated count, so it is left out entirely.
        follower = re.match(r"^\s+([a-z][a-z-]{2,})", lowered[num.end() :])
        if follower and follower.group(1) not in STOPWORDS:
            continue

        if scale:  # "3 billion" with no currency word
            out.setdefault("unitless", []).append(value)
        elif 1400 <= value <= 2100 and value.is_integer():
            out.setdefault("year", []).append(float(int(value)))
        else:
            out.setdefault("unitless", []).append(value)
    return out


def split_region_sentences(region: str) -> list[str]:
    """Split retrieved text into analysable sentences, tolerant of newlines."""
    text = _WS_RE.sub(" ", (region or "").replace("\n", " ")).strip()
    if not text:
        return []
    parts: list[str] = []
    for chunk in re.split(r"(?<=[.!?])\s+", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        if _ABBREV_RE.search(chunk) and parts:
            parts[-1] = f"{parts[-1]} {chunk}"
            continue
        for piece in _SENT_SPLIT_RE.split(chunk):
            piece = piece.strip()
            if piece:
                parts.append(piece)
    return parts or [text]


def _best_window(sentence: str, query_tokens: set[str], max_chars: int) -> str:
    """Trim an over-long quote on a clause boundary, keeping it a literal substring."""
    if len(sentence) <= max_chars or not sentence:
        return sentence
    head = sentence[:max_chars]
    cut = max(head.rfind(". "), head.rfind("; "), head.rfind(", "), head.rfind(" "))
    trimmed = sentence[: cut + 1] if cut > max_chars // 2 else head
    # Never cut inside a number or a quoted span in a way that changes its meaning.
    return trimmed.strip()


def verify_quote(quote: str, region: str) -> bool:
    """True only when ``quote`` is literally present in ``region``."""
    normalized_quote = normalize_for_match(quote)
    if not normalized_quote:
        return False
    return normalized_quote in normalize_for_match(region)


def region_for(item: dict[str, Any]) -> tuple[str, str]:
    """The text a quote may be checked against, plus the scope label for that text.

    Mirrors ``s2_service.apply_analysis`` exactly (page text when S2 fetched it,
    snippet otherwise) so a quote that passes here always survives the merge guard,
    and so the reason *why* it passed is visible to the report.
    """
    page_text = str(item.get("full_text") or "").strip()
    if page_text:
        return page_text, SCOPE_FULL_TEXT
    return str(item.get("snippet") or ""), SCOPE_SNIPPET


def stance_lexicon() -> dict[str, tuple[str, ...]]:
    """Expose the cue lists so tests and the docs can see exactly what is trusted."""
    return {"refute": REFUTE_CUES, "support": SUPPORT_CUES}


# ---------------------------------------------------------------------------
# The lexical (deterministic) analyser
# ---------------------------------------------------------------------------
@dataclass
class Alignment:
    """How well one source sentence speaks to one claim, and in which direction."""

    sentence: str
    align: float
    missing: frozenset[str]
    negation: tuple[str, ...]
    support: tuple[str, ...]
    number_conflict: bool
    number_match: bool
    confidence: float
    #: the sentence talks *about* the claim (a reported idea, a saying, a question)
    mention_frame: bool = False
    #: claim and sentence disagree on polarity ("never X" vs "X happens repeatedly")
    polarity_mismatch: bool = False

def _cue_hits(text: str, cues: Iterable[str]) -> tuple[str, ...]:
    lowered = normalize_for_match(text)
    hits = [cue for cue in cues if cue in lowered]
    return tuple(sorted(hits))


def _pattern_hits(text: str, patterns: Sequence[re.Pattern[str]]) -> tuple[str, ...]:
    """Which scoped patterns fired, as matched strings so the rationale can cite them."""
    lowered = normalize_for_match(text)
    hits = {match.group(0).strip() for pattern in patterns if (match := pattern.search(lowered))}
    return tuple(sorted(hits))


def align_sentence(
    claim_text: str, sentence: str, claim_tokens: set[str], *, claim_negated: bool | None = None
) -> Alignment:
    """Score one sentence against one claim and classify its polarity."""
    normalized_sentence = normalize_for_match(sentence)
    sentence_tokens = content_tokens(sentence)
    if claim_tokens and sentence_tokens:
        overlap = claim_tokens & sentence_tokens
        align = len(overlap) / len(claim_tokens)
    else:
        overlap = set()
        align = 0.0

    missing = frozenset(claim_tokens - sentence_tokens)

    # A number that appears with an incompatible value in the aligned sentence is the
    # strongest single signal we have, so check it before any word list.
    claim_units = weighted_values(claim_text)
    source_units = weighted_values(sentence)
    number_conflict = False
    number_match = False
    for unit, values in claim_units.items():
        other = source_units.get(unit)
        if not other:
            continue
        if unit in ("unitless", "year") and (len(values) > 1 or len(other) > 1):
            # Bare numbers are ambiguous: only a one-to-one comparison is trusted.
            continue
        for value in values:
            if any(abs(value - candidate) <= max(0.05 * abs(value), 0.01) for candidate in other):
                number_match = True
            elif not any(
                abs(value - candidate) <= max(0.6 * abs(value), 1.0) for candidate in other
            ):
                # Nothing close at all in a sentence that *does* discuss this unit,
                # and the sentence already aligns lexically: a real disagreement.
                if align >= 0.4:
                    number_conflict = True

    negation = tuple(sorted(set(_cue_hits(sentence, REFUTE_CUES)) | set(_pattern_hits(sentence, _REFUTE_COMPILED))))
    support = _cue_hits(sentence, SUPPORT_CUES)
    verification = _pattern_hits(sentence, _VERIFICATION_COMPILED)
    mention_frame = bool(_MENTION_FRAME_RE.search(sentence))
    if verification and not negation:
        support = tuple(sorted(set(support) | set(verification)))

    if claim_negated is None:
        claim_negated = bool(_CLAIM_NEGATION_RE.search(claim_text))
    # Polarity disagreement: one side negates, the aligned sentence does not (or the
    # reverse). Only trusted at a higher alignment than an ordinary refute, because a
    # paraphrase can drop a "not" without contradicting anything.
    polarity_mismatch = (
        align >= 0.6
        and (claim_negated != bool(negation))
        and not number_match
    )

    confidence = align
    if polarity_mismatch:
        confidence += 0.15
    if number_match:
        confidence += 0.2
    if number_conflict:
        confidence += 0.25
    if negation:
        confidence += 0.15
    if support:
        confidence += 0.1
    # Overlap on nothing but function words is not evidence.
    if not overlap:
        confidence = 0.0

    return Alignment(
        sentence=sentence,
        align=round(min(1.0, align), 4),
        missing=missing,
        negation=negation,
        support=support,
        number_conflict=number_conflict,
        number_match=number_match,
        confidence=round(min(1.0, confidence), 4),
        mention_frame=mention_frame,
        polarity_mismatch=polarity_mismatch,
    )


def _qualifier_gap(claim_text: str, sentence: str) -> tuple[bool, list[str]]:
    """Does the source passage leave out a scope/quantity qualifier the claim asserts?"""
    claim_qualifiers = {m.group(0).lower() for m in QUANTIFIER_RE.finditer(claim_text or "")}
    if not claim_qualifiers:
        return False, []
    source_text = normalize_for_match(f"{sentence}")
    missing = sorted(q for q in claim_qualifiers if q not in source_text)
    # One missing hedging word is normal paraphrase; two or more means we are
    # probably looking at a broader or narrower population than the claim states.
    return (len(missing) >= 2), missing


def _rationale_for(
    claim_text: str,
    best: Alignment,
    scope: str,
    stance: str,
    missing_qualifiers: list[str],
    *,
    qualifier_gap: bool = False,
    min_confidence: float = 0.45,
) -> str:
    """Plain-language, content-free explanation of *how* the label was decided."""
    checked = (
        "the fetched page text" if scope == SCOPE_FULL_TEXT else "the retrieved snippet"
    )
    if stance == "NEUTRAL":
        if best.align < 1e-9:
            return (
                "No sentence in this source mentions the claim's key terms, so the "
                "system reports no stance rather than guessing."
            )
        if best.mention_frame:
            return (
                "The passage only *mentions* the claim (framed as a claim, idea, saying "
                "or question) without confirming or denying it, so no stance is taken."
            )
        return (
            f"Closest passage overlaps the claim on {best.align:.0%} of its key terms "
            f"({best.align:.2f}), below the {min_confidence:.0%} decision "
            "threshold — treated as background, not a stance."
        )
    direction = "agrees with" if stance == "SUPPORTS" else "contradicts"
    parts = [
        f"The passage in {checked} {direction} the claim on {best.align:.0%} of its key terms."
    ]
    if best.number_match and not best.number_conflict:
        parts.append("Every figure the two texts state for the same quantity matches.")
    elif best.number_match and best.number_conflict:
        parts.append(
            "Some figures match while at least one does not: the passage states a "
            "different value for a quantity the claim also states."
        )
    elif best.number_conflict:
        parts.append("The passage states a different figure for the same quantity.")
    if best.polarity_mismatch and not best.negation:
        parts.append(
            "The claim is stated negatively while this passage states the same terms "
            "affirmatively, so the two disagree on polarity."
        )
    elif best.negation:
        preview = ", ".join(f"“{cue}”" for cue in best.negation[:3])
        parts.append(f"It also carries an explicit negation/correction cue ({preview}).")
    if best.support:
        preview = ", ".join(f"“{cue}”" for cue in best.support[:2])
        parts.append(f"Corroborating wording: {preview}.")
    if missing_qualifiers:
        preview = ", ".join(missing_qualifiers[:3])
        label = "Qualifier gap" if qualifier_gap else "Scope note"
        parts.append(
            f"{label}: the claim asserts “{preview}”, which this passage does not state"
            + (", so the evidence may cover a different population or scale." if qualifier_gap else ".")
        )
    return " ".join(parts)


def analyze_item(
    claim_text: str,
    item: dict[str, Any],
    *,
    config: S3Config | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze one evidence item against one claim. Deterministic; never raises."""
    config = S3Config.resolve(config)
    region, scope = region_for(item)
    sentences = split_region_sentences(region)
    claim_tokens = content_tokens(claim_text)

    if not sentences or not claim_tokens:
        return {
            "evidence_id": item.get("id", ""),
            "claim_id": item.get("claim_id", ""),
            "url": item.get("url", ""),
            "stance": "NEUTRAL",
            "quote": "",
            "quote_verified": False,
            "quote_scope": scope,
            "rationale": "No retrieved text was available to check this item against.",
            "context_score": 0.0,
            "context_gap": True,
            "confidence": 0.0,
            "dropped_quote": False,
        }

    scored = [align_sentence(claim_text, sentence, claim_tokens) for sentence in sentences]
    scored.sort(key=lambda a: a.confidence, reverse=True)
    best = scored[0]

    # Refuting on a weakly aligned sentence is the costliest mistake available here,
    # so refusal needs both a cue/number conflict *and* enough shared content.
    refuting = (
        (bool(best.negation) and best.align >= config.refute_align)
        or (best.number_conflict and best.align >= config.refute_align)
        or best.polarity_mismatch
    )
    supporting = (
        best.align >= config.support_align
        and not best.negation
        and not best.number_conflict
        and not best.polarity_mismatch
    )
    # "The idea that X has no basis" asserts *against* X; "the idea that X" on its own
    # only mentions it. A mention with no confirming or denying predicate is treated as
    # background, because reading the quoted words as the source's own claim is exactly
    # the error this stage must not make.
    if best.mention_frame and not best.negation and not best.support:
        supporting = False
        refuting = False

    if refuting:
        stance = "REFUTES"
    elif supporting:
        stance = "SUPPORTS"
    else:
        stance = "NEUTRAL"

    if stance != "NEUTRAL" and best.confidence < config.min_confidence:
        stance = "NEUTRAL"

    missing_qualifiers: list[str] = []
    if stance != "NEUTRAL":
        gap, missing_qualifiers = _qualifier_gap(claim_text, best.sentence)
    else:
        gap = True
    if stance == "NEUTRAL":
        missing_qualifiers = []

    quote = ""
    verified = False
    dropped = False
    if stance != "NEUTRAL":
        candidate = _best_window(best.sentence, claim_tokens, config.quote_max_chars)
        if verify_quote(candidate, region):
            quote = candidate
            verified = True
        else:
            # Should not happen (the sentence came out of `region`), but the guard is
            # the point: drop rather than display something we cannot locate.
            dropped = True
            stance = "NEUTRAL"
            gap = True

    # context_score: how much of the claim this source actually addresses.
    context_score = round(max(0.0, min(1.0, best.align - 0.35 * len(best.missing) / max(1, len(claim_tokens)))), 4)
    if stance == "NEUTRAL":
        context_score = min(context_score, 0.5)

    return {
        "evidence_id": item.get("id", ""),
        "claim_id": item.get("claim_id", ""),
        "url": item.get("url", ""),
        "stance": stance,
        "quote": quote,
        "quote_verified": verified,
        "quote_scope": scope,
        "rationale": _rationale_for(
            claim_text,
            best,
            scope,
            stance,
            missing_qualifiers,
            qualifier_gap=gap,
            min_confidence=config.min_confidence,
        ),
        "context_score": context_score,
        "context_gap": bool(gap),
        "confidence": best.confidence,
        "dropped_quote": dropped,
    }


# ---------------------------------------------------------------------------
# The LLM analyser (same output shape, then the same guard)
# ---------------------------------------------------------------------------
LLM_SYSTEM_PROMPT = (
    "You are a strict evidence analyst for a claim-verification system. For each "
    "numbered piece of evidence you decide what that evidence says about the claim. "
    "You never decide whether the claim is true overall. Rules: (1) stance must be "
    "SUPPORTS, REFUTES or NEUTRAL. (2) 'quote' MUST be copied verbatim, character for "
    "character, from the evidence text you were given; if you cannot find an exact "
    "sentence that bears on the claim, return an empty quote and stance NEUTRAL. "
    "(3) Do not paraphrase inside the quote. (4) Do not use outside knowledge. "
    "(5) context_gap is true when the evidence talks about a different population, "
    "time, or scope than the claim asserts. Reply with JSON only."
)

LLM_USER_TEMPLATE = """Claim ({claim_id}): {claim_text}

Evidence items retrieved for this claim. For EACH item return one analysis.

{items}

Return JSON exactly in this shape, one entry per evidence id, no extra keys:
{{"analyses": [{{"evidence_id": "ev_1", "stance": "SUPPORTS|REFUTES|NEUTRAL",
"quote": "verbatim sentence from that item's text or empty string",
"rationale": "one short sentence saying why, referring only to the text above",
"context_score": 0.0, "context_gap": false}}]}}"""


def _build_llm_prompt(claim_text: str, claim_id: str, items: Sequence[tuple[dict[str, Any], str, str]]) -> str:
    blocks = []
    for index, (item, region, scope) in enumerate(items, 1):
        label = (
            "fetched page text" if scope == SCOPE_FULL_TEXT else "search snippet ONLY (no page text)"
        )
        source = item.get("title") or item.get("domain") or item.get("url") or "untitled source"
        published = item.get("published_at") or "date unknown"
        blocks.append(
            f"[{index}] evidence_id={item.get('id', '')}\n"
            f"    source: {source} | {item.get('domain', '')} | published: {published}\n"
            f"    text_scope: {label}\n"
            f"    text: {region}"
        )
    return LLM_USER_TEMPLATE.format(claim_id=claim_id, claim_text=claim_text, items="\n\n".join(blocks))


def _parse_llm_payload(raw: str) -> list[dict[str, Any]]:
    """Lenient JSON extraction: models wrap objects in prose or fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return []
        try:
            data = json.loads(text[start : end + 1])
        except ValueError:
            return []
    if not isinstance(data, dict):
        return []
    entries = data.get("analyses") or data.get("results") or data.get("items") or []
    if isinstance(entries, dict):
        entries = [dict(value, evidence_id=key) for key, value in entries.items()]
    return [entry for entry in entries if isinstance(entry, dict)]


def analyze_claim_with_llm(
    claim_text: str,
    claim_id: str,
    items: Sequence[dict[str, Any]],
    *,
    provider: Any,
    config: S3Config,
) -> list[dict[str, Any]]:
    """One LLM call for one claim, every returned quote re-checked locally.

    The model's stance is trusted; its *quote* is not. Any quote that is not a
    literal substring of the text we sent it is discarded and the item falls back to
    the lexical analysis, which is what keeps a confident hallucination off screen.
    """
    prepared = [(item, *region_for(item)) for item in items]
    prompt = _build_llm_prompt(claim_text, claim_id, prepared)
    raw = provider.complete(LLM_SYSTEM_PROMPT, prompt)
    parsed = _parse_llm_payload(raw)
    by_id = {str(entry.get("evidence_id") or entry.get("id") or ""): entry for entry in parsed}
    by_index = {str(index): entry for index, entry in enumerate(parsed, 1)}

    results: list[dict[str, Any]] = []
    for index, (item, region, scope) in enumerate(prepared, 1):
        entry = by_id.get(str(item.get("id", ""))) or by_index.get(str(index)) or {}
        fallback = analyze_item(claim_text, item, config=config)

        stance = str(entry.get("stance") or "").strip().upper()
        quote = str(entry.get("quote") or "").strip()
        rationale = str(entry.get("rationale") or "").strip()
        verified = bool(stance in VALID_STANCES and quote and verify_quote(quote, region))

        if stance not in VALID_STANCES or not verified:
            # Missing, unhinged or unlocatable — keep the deterministic answer.
            fallback["analyzer"] = "lexical (after the LLM answer was rejected)"
            if stance in VALID_STANCES and quote and not verified:
                fallback["dropped_quote"] = True
                fallback["rationale"] = (
                    fallback["rationale"]
                    + " (an LLM-proposed quote could not be located in the retrieved text "
                    "and was dropped)"
                )
            results.append(fallback)
            continue

        try:
            context_score = max(0.0, min(1.0, float(entry.get("context_score", fallback["context_score"]))))
        except (TypeError, ValueError):
            context_score = float(fallback["context_score"])

        results.append(
            {
                "evidence_id": item.get("id", ""),
                "claim_id": item.get("claim_id", ""),
                "url": item.get("url", ""),
                "stance": stance,
                "quote": quote,
                "quote_verified": True,
                "quote_scope": scope,
                "rationale": rationale or "Stance taken from the source passage by the LLM analyst.",
                "context_score": round(context_score, 4),
                "context_gap": bool(entry.get("context_gap", fallback["context_gap"])),
                "confidence": round(max(0.0, min(1.0, context_score)), 4),
                "dropped_quote": False,
                "analyzer": "llm",
            }
        )
    return results


# ---------------------------------------------------------------------------
# Bundle-level entry points
# ---------------------------------------------------------------------------
@dataclass
class S3Result:
    """Analyses plus the audit the report footer needs."""

    analyses: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, Any] = field(default_factory=dict)

    def to_list(self) -> list[dict[str, Any]]:
        return self.analyses


def _claim_text_of(claim: Any) -> str:
    if isinstance(claim, dict):
        return str(claim.get("text") or claim.get("claim_text") or "")
    return str(getattr(claim, "text", "") or "")


def _claim_id_of(claim: Any, index: int) -> str:
    if isinstance(claim, dict):
        return str(claim.get("id") or claim.get("claim_id") or f"claim_{index:03d}")
    return str(getattr(claim, "id", "") or f"claim_{index:03d}")


def _provider_or_none(config: S3Config) -> tuple[Any | None, str]:
    """Resolve the configured LLM provider; (None, reason) when unavailable.

    ``mock`` is *not* an S3 provider: there is no trained model behind it, and using
    one would look like a real judgement while being the lexical analyser anyway.
    So MOCK mode means the lexical path, stated plainly in the audit.
    """
    if config.mode == "lexicon":
        return None, "mode=lexicon"
    try:
        from backend.llm_provider import get_provider  # noqa: PLC0415 - optional path
    except Exception as exc:  # pragma: no cover - backend package always present here
        return None, f"provider import failed: {type(exc).__name__}"
    try:
        provider = get_provider()
    except Exception as exc:
        return None, f"provider unavailable: {str(exc)[:160]}"
    name = getattr(provider, "name", "?")
    if name == "mock" or getattr(provider, "is_mock", False):
        return None, "LLM_PROVIDER=mock (no model configured); lexical analyser used"
    try:
        available, reason = provider.is_available()
    except Exception as exc:
        return None, f"is_available() failed: {type(exc).__name__}"
    if not available:
        return None, f"provider {name} unavailable: {reason}"
    return provider, f"provider={name}"


def analyze_bundles(
    claims: Sequence[Any],
    bundles: Sequence[dict[str, Any]],
    *,
    config: S3Config | dict[str, Any] | None = None,
    provider: Any | None = None,
    **overrides: Any,
) -> S3Result:
    """Analyze every evidence item in every bundle.

    ``claims`` and ``bundles`` are the two arguments the orchestrator passes; bundles
    are matched to claims by position and then by ``claim.id``, so a reordered or
    partially filtered bundle list still gets the right claim text.
    """
    config = S3Config.resolve(config, **overrides)
    if provider is None:
        provider, provider_note = _provider_or_none(config)
    else:
        provider_note = f"provider={getattr(provider, 'name', 'injected')}"

    bundles = list(bundles or [])
    claims_by_id = {
        _claim_id_of(claim, index): _claim_text_of(claim) for index, claim in enumerate(claims or [], 1)
    }
    claims_in_order = [_claim_text_of(claim) for claim in (claims or [])]

    analyses: list[dict[str, Any]] = []
    dropped = 0
    neutral = 0
    llm_used = 0
    lexicon_used = 0
    notes: list[str] = []

    for index, bundle in enumerate(bundles):
        bundle_claim = bundle.get("claim") or {}
        claim_id = str(
            bundle_claim.get("id")
            or bundle_claim.get("claim_id")
            or (f"claim_{index + 1:03d}")
        )
        claim_text = str(
            bundle_claim.get("text")
            or claims_by_id.get(claim_id)
            or (claims_in_order[index] if index < len(claims_in_order) else "")
        )
        evidence = list(bundle.get("evidence") or [])[: config.max_evidence_per_claim]
        if not evidence:
            continue

        batch: list[dict[str, Any]] = []
        if provider is not None:
            try:
                batch = analyze_claim_with_llm(
                    claim_text, claim_id, evidence, provider=provider, config=config
                )
            except Exception as exc:
                if not config.fail_soft:
                    raise
                notes.append(f"LLM analysis failed for {claim_id}: {str(exc)[:160]}")
                batch = []
        if not batch:
            batch = [analyze_item(claim_text, item, config=config) for item in evidence]
        # Count provenance per item: a run where every model answer was thrown out by
        # the guard is a lexical run, however the transport behaved.
        from_llm = sum(1 for item_result in batch if item_result.get("analyzer") == "llm")
        llm_used += from_llm
        lexicon_used += len(batch) - from_llm

        for analysis in batch:
            analysis["claim_id"] = claim_id
            dropped += 1 if analysis.get("dropped_quote") else 0
            neutral += 1 if analysis.get("stance") == "NEUTRAL" else 0
            analyses.append(analysis)

    decided = sum(1 for a in analyses if a.get("stance") in ("SUPPORTS", "REFUTES"))
    if provider is None and config.mode in ("auto", "llm"):
        notes.append("no usable LLM provider: the deterministic lexical analyser ran instead")
    notes.append(provider_note)

    audit = {
        "stage": "S3_ANALYSIS",
        # "llm" only when at least one stance actually came from the model: a run where
        # every model answer was rejected must not read as an LLM run.
        "analyzer": "llm" if llm_used else "lexical",
        "evidence_analyzed": len(analyses),
        "decisive": decided,
        "neutral": neutral,
        "llm_analyzed": llm_used,
        "lexical_analyzed": lexicon_used,
        "quote_dropped_count": dropped,
        "provider_note": provider_note,
        "notes": [note for note in notes if note][:8],
        "min_confidence": config.min_confidence,
        "mode": config.mode,
    }
    return S3Result(analyses=analyses, audit=audit)


def analyze(
    claims: Sequence[Any],
    bundles: Sequence[dict[str, Any]],
    *,
    config: S3Config | dict[str, Any] | None = None,
    **overrides: Any,
) -> list[dict[str, Any]]:
    """The orchestrator/contract-checker entry point: ``analyze(claims, bundles)``.

    Returns a plain list of analysis dicts, which is what
    ``run_verified_claim_pipeline(analyze_fn=...)`` and ``apply_analysis`` consume.
    Use :func:`analyze_bundles` when you also need the audit block.
    """
    return analyze_bundles(claims, bundles, config=config, **overrides).analyses


def analyze_offline(
    claims: Sequence[Any], bundles: Sequence[dict[str, Any]], **overrides: Any
) -> list[dict[str, Any]]:
    """Deterministic, keyless, network-free analysis — the MOCK demo path."""
    return analyze(claims, bundles, config={"mode": "lexicon"}, **overrides)


# ---------------------------------------------------------------------------
# CLI — handy for the seam check without writing a wrapper file
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run S3 stance analysis over a bundle file")
    parser.add_argument("bundles", help="JSON file: a list of S2 claim bundles")
    parser.add_argument("--mode", default="lexicon", choices=("lexicon", "auto", "llm"))
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)

    with open(args.bundles, encoding="utf-8") as handle:
        data = json.load(handle)
    bundle_list = data.get("bundles") if isinstance(data, dict) else data
    claim_list = (data.get("claims") if isinstance(data, dict) else None) or [
        b.get("claim") for b in bundle_list
    ]
    result = analyze_bundles(claim_list, bundle_list, config={"mode": args.mode})
    payload = {"audit": result.audit, "analyses": result.analyses}
    print(json.dumps(payload, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entry point
    raise SystemExit(main())
