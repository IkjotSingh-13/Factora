"""Claim grounding: attach a verbatim article quote and a time-sensitivity flag.

Why this module exists
----------------------
The plan (S1, §3) says every claim must keep *the exact article quote* it came from,
and that a claim whose quote cannot be found in the article is rejected. S1's public
``Claim`` model carries exactly four fields (``id, text, entities, claim_type``) and is
``extra="forbid"``, so it structurally cannot hold a quote — ``tests/test_s2.py:975``
pins that ``article_quote`` arrives as ``None`` today. That silently disabled two parts
of the design: the article-side quote guard, and the fresh-fast half of the ``T`` term
(which only applies when ``time_sensitive`` is true, and it never was).

Rather than break S1's frozen model, this module grounds claims at the integration
boundary, where the article text is still in hand:

    claims (S1)  +  article text
      -> align each claim to the sentence it came from
      -> take that sentence VERBATIM from the article as ``article_quote``
      -> require it to be literally present in the article, else drop the claim
      -> set ``time_sensitive`` from explicit date/record/"current" cues

The alignment utilities are shared with S3 (:mod:`s3_analysis`) on purpose: quoting a
source and quoting the submitted article are the same operation, and one guard should
serve both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from s3_analysis import (
    QUANTIFIER_RE,
    S3Config,
    align_sentence,
    content_tokens,
    normalize_for_match,
    split_region_sentences,
    verify_quote,
)

__all__ = ["GroundedClaim", "ground_claims", "detect_time_sensitive", "DEFAULT_GROUNDING"]

#: A claim is "the same statement" only when it shares this much of its key terms with
#: the article sentence it is being pinned to.
MIN_ALIGN = 0.5

# Cues that make a claim expire. Each entry is (pattern, human-readable reason).
_TIME_CUES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(this|last|next|coming|current|present)\b.{0,18}"
                r"\b(year|month|week|quarter|season|election|budget)\b", re.I),
     "relative date expression"),
    (re.compile(r"\b(recently|currently|now|today|yesterday|tonight|this week|last week|"
                r"as of|to date|so far|yet|still|ongoing|upcoming)\b", re.I),
     "currency word"),
    (re.compile(r"\b(will|plans to|is expected to|expects to|set to|going to|would|"
                r"announced that|has until|deadline)\b", re.I),
     "forward-looking statement"),
    (re.compile(r"\b(record|highest|lowest|largest|smallest|fastest|slowest|biggest|"
                r"most|least|first|last|only|world'?s (largest|biggest|best))\b", re.I),
     "superlative or record claim"),
    (re.compile(r"\b\d{1,3}(,\d{3})+\b|\b\d+(?:\.\d+)?\s*(?:%|percent|billion|million|"
                r"trillion|crore|lakh)\b", re.I),
     "specific magnitude that can change"),
    (re.compile(r"\b(?:in|by|since|until|during)\s+(19|20)\d{2}\b", re.I),
     "explicit year"),
    (re.compile(r"\b(price|prices|stock|shares|revenue|gdp|inflation|unemployment|"
                r"population|rate|total|count)\b", re.I),
     "metric that moves over time"),
)

#: Statements that are stable forever; they must not be punished for being old.
_TIMELESS_CUES = re.compile(
    r"\b(?:is defined as|was born|died in|the capital of|is located in|invented|"
    r"discovered|was built in|signed the treaty)\b",
    re.I,
)


@dataclass
class GroundedClaim:
    """A claim plus its verbatim article quote and provenance of that quote."""

    id: str
    text: str
    type: str
    entities: list[str] = field(default_factory=list)
    article_quote: str | None = None
    time_sensitive: bool = False
    time_sensitive_reason: str | None = None
    align: float = 0.0
    quote_source: str = "none"  # exact | relocated | none
    #: the claim with its reporting frame removed - what retrieval and S3 work on
    assertion: str = ""
    removed_frame: str | None = None

    def to_claim_input(self) -> dict[str, Any]:
        """The ``app.models.ClaimInput`` payload for this claim."""
        return {
            "id": self.id,
            "text": self.text,
            "type": self.type,
            "article_quote": self.article_quote,
            "time_sensitive": self.time_sensitive,
        }


def _claim_fields(claim: Any) -> tuple[str, str, str, list[str]]:
    """Read (id, text, type, entities) off any claim shape S1 or the API produces."""
    if isinstance(claim, dict):
        get = lambda *names, default=None: next(  # noqa: E731
            (claim[n] for n in names if n in claim and claim[n] is not None), default
        )
        return (
            str(get("id", "claim_id", default="")),
            str(get("text", "claim_text", default="")),
            str(get("claim_type", "type", default="other")),
            [str(e) for e in (get("entities", default=[]) or [])],
        )
    get = lambda *names, default=None: next(  # noqa: E731
        (getattr(claim, n) for n in names if getattr(claim, n, None) is not None), default
    )
    return (
        str(get("id", "claim_id", default="")),
        str(get("text", "claim_text", default="")),
        str(get("claim_type", "type", default="other")),
        [str(e) for e in (get("entities", default=[]) or [])],
    )


def _verbatim_slice(article: str, sentence: str) -> str | None:
    """Locate ``sentence`` inside the original article and return the original slice.

    Whitespace and case may differ between the splitter's output and the article, so
    the search walks normalised text and maps the offset back to the raw string. The
    value returned is therefore byte-identical to the article — what the UI prints is
    provably what the user pasted.
    """
    if not sentence:
        return None
    target = normalize_for_match(sentence)
    if not target:
        return None

    # Fast path: the sentence is already verbatim.
    if sentence in article:
        return sentence

    # Slow path: rebuild a normalised->raw index map. Only needed when whitespace or
    # case actually differs, which for a pasted article means smart quotes or newlines.
    normalized_chars: list[str] = []
    raw_index: list[int] = []
    pending_space = False
    for position, char in enumerate(article):
        if char.isspace():
            pending_space = True
            continue
        folded = normalize_for_match(char)
        if not folded:
            continue
        if pending_space and normalized_chars:
            normalized_chars.append(" ")
            raw_index.append(position - 1)
            pending_space = False
        normalized_chars.append(folded)
        raw_index.append(position)
    haystack = "".join(normalized_chars)

    found = haystack.find(target)
    if found < 0:
        # Fall back to a token-insensitive search on a prefix, then widen to the whole
        # raw sentence span so the displayed quote is still the article's text.
        probe = " ".join(target.split()[:6])
        found = haystack.find(probe)
        if found < 0:
            return None
        start_raw = raw_index[found]
        tail = article[start_raw:]
        end_match = re.search(r"[.!?](?:\s|$|\"|”)", tail)
        raw_sentence = tail[: end_match.end()].strip() if end_match else tail.strip()
        return raw_sentence if verify_quote(raw_sentence, article) else None

    start_raw = raw_index[found]
    end_raw = raw_index[min(found + len(target) - 1, len(raw_index) - 1)]
    # Snap to whole words in the original text (the normalised form can drop a hyphen
    # or apostrophe, which shifts the raw end position by a character or two).
    while end_raw + 1 < len(article) and article[end_raw + 1].isalnum():
        end_raw += 1
    raw_sentence = article[start_raw : end_raw + 1].strip()
    return raw_sentence if verify_quote(raw_sentence, article) else None


def detect_time_sensitive(text: str) -> tuple[bool, str | None]:
    """Whether a claim should be judged on freshness, plus the cue that said so."""
    if not text:
        return False, None
    if _TIMELESS_CUES.search(text):
        return False, None
    for pattern, reason in _TIME_CUES:
        if pattern.search(text):
            return True, reason
    return False, None


#: Attribution / reporting frames that carry no checkable content. A claim of the
#: form "A widely shared brief states that X" is verified by checking X, not the
#: brief, so retrieval and stance alignment both work better on the embedded
#: assertion. The claim as S1 extracted it is never lost: the article's own sentence
#: (frame included) travels in ``article_quote``, and every strip is listed in the
#: grounding audit, so a reviewer can see exactly what was reworded.
#: Only verbs whose object is normally a *clause*. "repeats that statement" and
#: "cites the report" take noun phrases, so a verb like that would have the
#: determiner eaten instead of a complementiser - which is why they are absent here.
_ATTRIBUTION_RE = re.compile(
    r"^(?P<frame>[^.!?]{0,90}?)\b(?P<verb>"
    r"states|state|stated|says|say|said|claims|claimed|asserts|asserted|argues|argued|"
    r"reports|reported|notes|noted|announces|announced|suggests|suggested|tells|told|"
    r"insists|insisted|writes|wrote|according to)\b"
    # The tail is the rest of the claim, so a sentence-final period does not defeat the
    # match; how much of it belongs to the *frame* is decided by _COMPLEMENT_RE below.
    r"(?P<tail>.*)$",
    re.IGNORECASE | re.DOTALL,
)
#: Only these verbs can take a bare clause ("said the plant will open"), so only these
#: may be cut without a "that" or a comma. "cites" and "according to" take noun
#: phrases, and cutting after them would turn "The report cites experts who say X" into
#: a claim about experts.
_CLAUSAL_VERBS = frozenset(
    """
    states state stated says say said claims claimed asserts asserted argues argued
    reports reported notes noted announces announced suggests suggested tells told
    insists insisted writes wrote
    """.split()
)

#: ``that`` / ``to be`` opening the embedded clause. The optional prefix absorbs an
#: adverbial ("announced **on Tuesday** that ...") and a bare comma/colon
#: ("According to the ministry, ..."), and is bounded so it cannot eat the assertion.
_COMPLEMENT_RE = re.compile(
    r"^\s*(?:[^.!?]{0,50}?)?\b(?:that|to\s+be|as\s+follows)\b\s*[,:]?\s*",
    re.IGNORECASE,
)
_COMMA_RE = re.compile(r"^\s*[,:;]\s*(?=[A-Z0-9\"“])")
#: ``said X`` with no complementiser and no comma. Cutting after the verb is only safe
#: when a clause subject follows it: "said **the** plant will open" is a clause, while
#: "said nothing about the merger" or "cites experts" is the verb's own phrase.
_DIRECT_RE = re.compile(
    r"^\s+(?:the|a|an|it|its|he|him|she|her|they|them|their|we|our|you|your|i|my|"
    r"no|none|all|both|more|most|few|many|in|on|at|after|before|since|while|though|"
    r"although|but|and|if|when|where|as|there|here)\b",
    re.IGNORECASE,
)
_PRONOUN_RE = re.compile(r"\b(?:it|its|they|them|their|he|she|his|her|this|that|these|those)\b", re.IGNORECASE)
_PRONOUN_LEAD = re.compile(
    r"^(?:it|its|they|them|their|he|she|his|her|this|that|these|those|there|here)\b",
    re.IGNORECASE,
)
_VERBISH_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being|will|would|shall|should|can|could|may|might|"
    r"must|do|does|did|has|have|had|\w+(?:s|ed|ing)|not)\b",
    re.IGNORECASE,
)


#: Quote/dash characters that can surround an embedded clause. Built from code points
#: so this file stays free of nested-quote escapes.
_QUOTE_CHARS = "".join(chr(c) for c in (34, 39, 8216, 8217, 8220, 8221, 8212, 8211))


def assertion_of(claim_text: str, *, entities: Sequence[str] = ()) -> tuple[str, str | None]:
    """Return ``(checkable_assertion, removed_frame_or_None)``.

    Strips only a leading reporting frame — never the assertion itself — so a quote
    verified against the assertion is still a quote about what the article said. The
    strip is refused (original text returned) whenever the remainder would be a
    fragment, a pronoun with no entity to resolve it, or too short to be a claim.
    """
    text = (claim_text or "").strip()
    if not text:
        return text, None
    match = _ATTRIBUTION_RE.match(text)
    if not match:
        return text, None

    tail = match.group("tail") or ""
    frame_head = (match.group("frame") or "").strip()
    verb = (match.group("verb") or "").strip()
    complement = _COMPLEMENT_RE.match(tail)
    comma = _COMMA_RE.match(tail)
    if complement:
        remainder = tail[complement.end() :].strip()
        frame = f"{frame_head} {verb} {tail[: complement.end()]}".strip()
    elif comma:
        remainder = tail[comma.end() :].strip()
        frame = f"{frame_head} {verb}".strip()
    elif verb.lower() in _CLAUSAL_VERBS and _DIRECT_RE.match(tail):
        # "The company said the plant will create 4,500 jobs" - a bare clause.
        remainder = tail.strip()
        frame = f"{frame_head} {verb}".strip()
    else:
        # No complementiser, no punctuation break and no clausal verb: the "frame" is
        # probably the claim's own subject, so leave the text untouched.
        return text, None

    remainder = remainder.strip(_QUOTE_CHARS + " ,.:;").rstrip(".")
    if not remainder or len(remainder.split()) < 5:
        return text, None
    if not _VERBISH_RE.search(remainder):
        return text, None  # no predicate: "the 330 metres" is not a claim

    if _PRONOUN_LEAD.match(remainder):
        # "It will open a factory" is unanswerable on its own, so substitute the
        # claim's subject for the leading pronoun - and give up rather than emit a
        # pronoun-led "assertion" if that is not available.
        subject = next((entity for entity in entities if entity), None)
        if not subject:
            return text, None
        remainder = re.sub(
            r"^\b(?:it|its|they|them|their|he|she|his|her|this|that)\b",
            subject,
            remainder,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        if _PRONOUN_LEAD.match(remainder):
            return text, None

    frame = re.sub(r"\s+", " ", frame).strip(" ,.:;")
    if frame.lower() == remainder.lower():
        return text, None
    if not remainder[0].isupper():
        remainder = remainder[0].upper() + remainder[1:]
    if not remainder.endswith((".", "!", "?")):
        remainder = f"{remainder}."
    return remainder, (frame or None)


def _qualifier_terms(text: str) -> set[str]:
    return {m.group(0).lower() for m in QUANTIFIER_RE.finditer(text or "")}


def ground_claims(
    claims: Sequence[Any],
    article_text: str,
    *,
    min_align: float = MIN_ALIGN,
    drop_ungrounded: bool = True,
    detect_time: bool = True,
    config: S3Config | None = None,
) -> tuple[list[GroundedClaim], dict[str, Any]]:
    """Pin each claim to a verbatim article sentence.

    Returns ``(grounded_claims, audit)``. Claims whose best-aligned article sentence
    overlaps them on at least ``min_align`` of their key terms get that sentence as
    ``article_quote``; everything else is dropped and counted, per the plan's rule
    that an unlocatable quote must never be displayed.
    """
    config = config or S3Config.resolve()
    article = article_text or ""
    sentences = split_region_sentences(article)
    normalized_article = normalize_for_match(article)

    grounded: list[GroundedClaim] = []
    dropped: list[dict[str, Any]] = []
    exact = fuzzy = none_matched = 0

    for index, claim in enumerate(claims or [], 1):
        claim_id, claim_text, claim_type, entities = _claim_fields(claim)
        claim_id = claim_id or f"claim_{index:03d}"
        if not claim_text:
            continue

        record = GroundedClaim(
            id=claim_id,
            text=claim_text,
            type=claim_type or "other",
            entities=list(entities),
            time_sensitive=False,
        )
        # The article sentence usually *includes* the reporting frame, so grounding
        # aligns against the claim as extracted; retrieval and stance work on the
        # frameless assertion. Using the assertion for grounding would be a subtle
        # way of throwing away the one signal that proves the quote is real.
        record.assertion, record.removed_frame = assertion_of(claim_text, entities=entities)
        if detect_time:
            sensitive, reason = detect_time_sensitive(record.assertion or claim_text)
            record.time_sensitive = sensitive
            record.time_sensitive_reason = reason

        claim_tokens = content_tokens(claim_text)
        candidates = [align_sentence(claim_text, sentence, claim_tokens) for sentence in sentences]
        candidates = [c for c in candidates if c.align > 0] or candidates
        candidates.sort(key=lambda c: (c.confidence, c.align), reverse=True)

        matched: str | None = None
        best_align = 0.0
        for candidate in candidates[:6]:
            best_align = max(best_align, candidate.align)
            if candidate.align < min_align:
                continue
            slice_ = _verbatim_slice(article, candidate.sentence)
            if slice_ and verify_quote(slice_, normalized_article):
                matched = slice_
                if candidate.sentence in article:
                    exact += 1
                    record.quote_source = "exact"
                else:
                    fuzzy += 1
                    record.quote_source = "relocated"
                break

        if matched:
            record.article_quote = matched
            record.align = round(best_align, 4)
            grounded.append(record)
        else:
            none_matched += 1
            dropped.append(
                {
                    "id": claim_id,
                    "text": claim_text,
                    "best_align": round(best_align, 4),
                    "reason": (
                        "no article sentence overlaps this claim on enough key terms"
                        if best_align < min_align
                        else "the best-matching sentence could not be located verbatim in the article"
                    ),
                }
            )
            if not drop_ungrounded:
                record.align = round(best_align, 4)
                grounded.append(record)

    note: list[str] = []
    if not sentences:
        # Nothing to check against at all: returning claims here would imply a guard
        # ran when it could not have.
        return [], {
            "stage": "S1_GROUNDING",
            "claims_in": len(list(claims or [])),
            "claims_kept": 0,
            "claims_dropped_ungrounded": len(list(claims or [])),
            "quotes_exact": 0,
            "quotes_relocated_in_article": 0,
            "quotes_unmatched": len(list(claims or [])),
            "time_sensitive_claims": 0,
            "reporting_frames_stripped": 0,
            "stripped": [],
            "article_sentences": 0,
            "min_align": min_align,
            "dropped": [],
            "notes": ["no article text was available, so no claim could be quoted or verified"],
        }
    if grounded == [] and dropped and not drop_ungrounded:
        note.append("no claim could be grounded in the article text")
    if not grounded and dropped and drop_ungrounded:
        # Never blank the screen: keep the best-aligned claim, quote-free, and say so.
        best = max(dropped, key=lambda item: item["best_align"])
        grounded.append(
            GroundedClaim(
                id=str(best["id"]),
                text=str(best["text"]),
                type="other",
                article_quote=None,
                align=float(best["best_align"]),
                quote_source="none",
            )
        )
        dropped.remove(best)
        none_matched = max(0, none_matched - 1)
        note.append(
            "no claim could be verified against the article text; the closest one was "
            "kept WITHOUT a quote instead of returning nothing"
        )

    audit = {
        "stage": "S1_GROUNDING",
        "claims_in": len(list(claims or [])),
        "claims_kept": len(grounded),
        "claims_dropped_ungrounded": len(dropped),
        "quotes_exact": exact,
        "quotes_relocated_in_article": fuzzy,
        "quotes_unmatched": none_matched,
        "time_sensitive_claims": sum(1 for c in grounded if c.time_sensitive),
        "reporting_frames_stripped": sum(1 for c in grounded if c.removed_frame),
        "stripped": [
            {"id": c.id, "removed": c.removed_frame, "assertion": c.assertion}
            for c in grounded
            if c.removed_frame
        ][:8],
        "article_sentences": len(sentences),
        "min_align": min_align,
        "dropped": dropped[:10],
        "notes": note,
    }
    return grounded, audit


#: Convenience for callers that only want the claim dicts for the next stage.
DEFAULT_GROUNDING = ground_claims


def to_pipeline_claims(grounded: Iterable[GroundedClaim]) -> list[dict[str, Any]]:
    """Grounded claims -> the dict S2 accepts (keeps ``claim_type`` naming S1 uses).

    ``contract_adapter.to_claim_input`` reads ``claim_type`` and maps it to ``type``,
    and reads ``article_quote``/``time_sensitive`` straight off the dict, so this is
    the one shape that carries every contract field through to the scorer.

    ``text`` is the *assertion*: what retrieval queries and what S3 aligns against.
    A reporting frame ("a viral post claims that X") is not the checkable part, and
    leaving it in the query demonstrably hurts — the Eiffel claim retrieved nothing
    useful until the frame came off. The claim as S1 produced it is not lost: the
    article's own sentence, frame included, is in ``article_quote``, and every strip
    is itemised in the grounding audit.
    """
    payload: list[dict[str, Any]] = []
    for claim in grounded:
        payload.append(
            {
                "id": claim.id,
                "text": claim.assertion or claim.text,
                "claim_type": claim.type,
                "entities": list(claim.entities),
                "article_quote": claim.article_quote,
                "time_sensitive": claim.time_sensitive,
            }
        )
    return payload


def to_display_claims(grounded: Iterable[GroundedClaim]) -> list[dict[str, Any]]:
    """The same claims as *authored*, for the UI: original text plus what was stripped.

    Emitted by ``/api/verify`` as ``grounding.claims`` so a reviewer can see the claim
    the article made and the assertion the pipeline checked, side by side.
    """
    return [
        {
            "id": claim.id,
            "original_text": claim.text,
            "assertion": claim.assertion or claim.text,
            "reporting_frame_removed": claim.removed_frame,
            "article_quote": claim.article_quote,
            "quote_source": claim.quote_source,
            "align": claim.align,
            "type": claim.type,
            "entities": list(claim.entities),
            "time_sensitive": claim.time_sensitive,
            "time_sensitive_reason": claim.time_sensitive_reason,
        }
        for claim in grounded
    ]
