"""S1 — Claim Extraction orchestration (public API).

Pipeline
--------
    article text
      -> normalise + sentence index
      -> provider (Gemini / Ollama / Mock) returns candidate claims as JSON
      -> strict validation per claim (numbers, entities, hedges, qualifiers)
      -> de-duplication
      -> priority selection (3-6 when available, never padded)
      -> list[Claim]  (exactly four fields)

Public functions
----------------
    extract_claims(article_text)          -> list[Claim]
    extract_claims_from_url(url)          -> list[Claim]
    extract_article_from_url(url)         -> str
    extract_claims_detailed(article_text) -> ExtractionResult  (internal diagnostics)
"""

from __future__ import annotations

import json
import re
import sys
from typing import Iterable, List, Sequence

from .article_extractor import extract_article_from_url
from .config import Settings, get_settings
from .errors import ClaimExtractionError, EmptyArticleError, LLMError
from .llm_provider import LLMProvider, get_provider, heuristic_extract, verify_provider_or_raise
from .schemas import CandidateClaim, Claim, ExtractionResult, RawClaim
from .validators import (
    ArticleContext,
    build_article_context,
    check_claim,
    content_lemmas,
    extract_dense_propositions,
    extract_entities,
    has_marker,
    main_article_entity,
    normalize_text,
    numeric_occurrences,
    split_sentences,
    word_count,
)

MAX_CLAIMS = 6
MIN_TARGET_CLAIMS = 3

EVENT_VERBS = (
    "announced",
    "announces",
    "launched",
    "launches",
    "approved",
    "approves",
    "signed",
    "signs",
    "acquired",
    "acquires",
    "won",
    "wins",
    "elected",
    "appointed",
    "banned",
    "bans",
    "fined",
    "released",
    "reported",
    "recorded",
    "installed",
    "built",
    "raised",
    "cut",
    "increased",
    "decreased",
    "grew",
    "fell",
    "rose",
    "opened",
    "closed",
    "invested",
    "allocated",
    "introduced",
    "passed",
    "rejected",
    "resigned",
    "died",
    "killed",
    "arrested",
    "sentenced",
    "confirmed",
    "unveiled",
    "declared",
    "started",
    "began",
    "completed",
    "suspended",
    "halted",
    "partnered",
    "merged",
    "visited",
    "met",
)

_BOILERPLATE_CLAIM_RE = re.compile(
    r"^(read more|also read|advertisement|subscribe|sign up|share this|follow us|"
    r"click here|photo|image|credit|tags?:|related:|watch:|listen:|download the app)",
    re.IGNORECASE,
)

_BULLET_RE = re.compile(r"^\s*(?:[-*\u2022\u25cf]|\d+[.)]|[a-z][.)])\s+", re.IGNORECASE)

# For cleaning article text with bullets/abbreviations
_ABBREV_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc|e\.g|i\.e|U\.S|U\.K|U\.N)\.", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_claims(
    article_text: str,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
) -> List[Claim]:
    """Extract 3-6 atomic claims from pasted article text.

    Args:
        article_text: the full article body (primary input method).
        provider: optional provider override (useful for tests / A-B runs).
        settings: optional settings override.

    Returns:
        A list of `Claim` objects with exactly the four mandated fields.

    Raises:
        EmptyArticleError: article text is empty.
        ClaimExtractionError: no claims could be produced.
    """
    return extract_claims_detailed(
        article_text, provider=provider, settings=settings
    ).claims


def extract_claims_from_url(
    url: str,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
) -> List[Claim]:
    """Fetch an article from `url` and extract claims from it.

    Raises:
        InvalidURLError: URL is malformed or blocked by the SSRF policy.
        ArticleExtractionError: page could not be fetched or parsed.
        ClaimExtractionError: no claims could be produced.
    """
    settings = settings or get_settings()
    article_text = extract_article_from_url(url, settings=settings)
    return extract_claims(article_text, provider=provider, settings=settings)


def extract_claims_detailed(
    article_text: str,
    *,
    provider: LLMProvider | None = None,
    settings: Settings | None = None,
) -> ExtractionResult:
    """Same as `extract_claims` but returns validation diagnostics.

    Diagnostics (rejected claims + reasons, provider used, fallback flag) live
    here only — never on the `Claim` model.
    """
    settings = settings or get_settings()

    if article_text is None:
        raise EmptyArticleError("article_text is required")
    if not isinstance(article_text, str):
        raise EmptyArticleError(
            f"article_text must be a string, got {type(article_text).__name__}"
        )

    # Pre-process: handle HTML fragments, bullet points, informal text
    article_text = _preprocess_input(article_text)
    
    article = normalize_text(article_text)
    if not article or word_count(article) == 0:
        raise EmptyArticleError("article_text is empty or contains no words")

    sentences = split_sentences(article)
    if not sentences:
        sentences = [article]

    # Track if we truncated for LLM
    truncated = False
    original_sentence_count = len(sentences)
    if len(sentences) > settings.max_sentences_to_llm:
        truncated = True
        sentences = sentences[: settings.max_sentences_to_llm]

    result = ExtractionResult(claims=[], article_sentence_count=len(sentences))
    if word_count(article) < settings.min_article_words:
        result.notes.append("short_article:returning_fewer_claims")
    if truncated:
        result.notes.append(f"truncated_article:{original_sentence_count}->{len(sentences)}_sentences")

    # Computed once and reused for every claim (validation is O(claims), not
    # O(claims x sentences)).
    context = build_article_context(article, sentences)

    provider = provider or get_provider(settings=settings)
    result.provider = provider.name

    # For non-mock providers, verify availability first to give clear errors
    # But don't fail if fallback is enabled and we're in test mode with mock HTTP
    # We attempt verification but don't block extraction if it fails due to network
    # when fallback is enabled - the actual extraction will handle it and fallback
    if provider.name in ("ollama", "gemini") and not provider.is_mock:
        try:
            available, msg = provider.is_available()
            if not available:
                # If fallback disabled, raise immediately with clear message
                if not settings.llm_fallback_to_heuristics:
                    raise LLMError(msg)
                # Otherwise, log it and will fallback during extraction
                result.notes.append(f"provider_check_failed:{msg}")
        except Exception:
            # If availability check itself fails, don't block - let extraction attempt handle it
            pass

    raw_claims = _run_provider(provider, sentences, settings, result)
    if settings.enable_dense_propositions and len(raw_claims) < settings.min_claims:
        dense_propositions = extract_dense_propositions(sentences)
        if dense_propositions:
            raw_claims.extend(dense_propositions)
    result.raw_claim_count = len(raw_claims)

    # --- validate ---------------------------------------------------------
    candidates: List[CandidateClaim] = []
    # Track per-sentence sub-index to preserve chronological order for claims
    # from the same sentence (e.g., "visited USA and met Trump and signed deal" -> 3 claims in order)
    sentence_subindex: dict[int, int] = {}
    for raw in raw_claims:
        text = _clean_claim_text(raw.text)
        if not text:
            continue
        if _BOILERPLATE_CLAIM_RE.match(text):
            result.rejected.append((text, "boilerplate"))
            continue

        report = check_claim(text, article, settings=settings, context=context)
        if not report.ok:
            result.rejected.append((text, report.summary()))
            continue

        entities = extract_entities(
            text, article_text=article, extra_candidates=raw.entities
        )
        source_index = raw.source_sentence_index
        if source_index is None or not (0 <= source_index < len(sentences)):
            source_index = _best_sentence_index(text, context)
        # Preserve chronological order within same sentence: use sub-index
        if source_index is not None:
            sub_idx = sentence_subindex.get(source_index, 0)
            sentence_subindex[source_index] = sub_idx + 1
            article_order = source_index * 1000 + sub_idx
        else:
            article_order = 10**6 + len(candidates)
        candidates.append(
            CandidateClaim(
                text=text,
                claim_type=raw.claim_type,
                entities=entities,
                source_sentence_index=source_index,
                article_order=article_order,
            )
        )

    if not candidates:
        if result.rejected:
            reasons = "; ".join(reason for _, reason in result.rejected[:3])
            raise ClaimExtractionError(
                f"No valid claims survived validation ({len(result.rejected)} rejected): {reasons}"
            )
        # The article genuinely contains no checkable claims.
        # Distinguish between "no claims produced" vs "article too short/nonsense"
        if word_count(article) < 5 or _is_no_claim_content(article):
            return result
        # If provider returned claims but none survived, it's a validation issue
        # But if no claims were returned at all (raw_claim_count==0) and article is substantial,
        # treat as no checkable claims rather than error
        return result

    # --- de-duplicate -----------------------------------------------------
    unique = _deduplicate(candidates, result)

    # --- prioritise + select ---------------------------------------------
    article_lemmas = context.lemmas
    main_entity = main_article_entity(article)
    for candidate in unique:
        candidate.priority = _score_candidate(candidate, article_lemmas, main_entity, settings)

    selected = _select(unique, settings)

    # --- semantic deduplication pass: ensure no overlapping semantic content ---
    # After initial dedup, do a second pass to eliminate claims that are semantically
    # contained within another (e.g., "signed a deal" vs "signed a deal on oil")
    selected = _semantic_deduplicate(selected, result)

    # --- emit -------------------------------------------------------------
    for index, candidate in enumerate(selected, start=1):
        result.claims.append(
            Claim(
                id=f"claim_{index:03d}",
                text=candidate.text,
                entities=candidate.entities,
                claim_type=candidate.claim_type,
            )
        )
    return result


def _is_no_claim_content(text: str) -> bool:
    """Check if article genuinely contains no extractable factual claims."""
    lowered = text.lower()
    # Check for greeting/smalltalk patterns with no factual content
    no_claim_patterns = [
        r"^(good morning|good evening|hello|hi|hey|greetings)[.!]?\s*$",
        r"^(thank you|thanks|cheers|goodbye|bye|see you)[.!]?\s*$",
        r"^(how are you|what.?s up|nice to meet you)[?.!]?$",
    ]
    # If entire article matches no-claim pattern
    text_stripped = lowered.strip()
    for pattern in no_claim_patterns:
        if re.match(pattern, text_stripped):
            return True
    
    # Check if article is only greetings/smalltalk
    words = text_stripped.split()
    if len(words) <= 6:
        greeting_words = {"good", "morning", "evening", "afternoon", "hello", "hi", "hey", "thanks", "thank", "you", "see", "tomorrow", "today", "bye", "goodbye", "please", "welcome"}
        if all(w.strip(".,!?") in greeting_words for w in words):
            return True
    
    # Check if article has no nouns/verbs/entities/numbers/dates that could be claims
    # Simple: if no capitalized entities and no numbers and very short
    if word_count(text) < 8 and not re.search(r"\b\d+\b", text) and not re.search(r"\b[A-Z][a-z]{2,}\b", text):
        # Check if it's just common smalltalk
        if not re.search(r"\b(?:announced|launched|visited|met|signed|installed|reported|sold|grew|built|opened|investment|project|capacity|revenue|billion|million|GW|MW|crore|USA|India|Trump|Modi)\b", text, re.IGNORECASE):
            return True
    
    return False


def _preprocess_input(text: str) -> str:
    """Handle various input formats: HTML fragments, bullets, informal text."""
    if not text:
        return text
    
    # Handle HTML fragments: strip tags but preserve text
    if "<" in text and ">" in text and re.search(r"<(p|div|br|li|ul|ol|h[1-6]|span|a|b|i|strong|em)[^>]*>", text, re.IGNORECASE):
        # Simple HTML tag stripping
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
    
    # Handle bullet points: convert bullets to sentence boundaries
    # e.g., "- PM Modi visited USA\n- He met Trump" -> "PM Modi visited USA. He met Trump."
    lines = text.split("\n")
    processed_lines = []
    for line in lines:
        # Check if line is a bullet
        bullet_match = re.match(r"^\s*(?:[-*\u2022\u25cf]|\d+[.)]|[a-z][.)])\s+(.+)", line)
        if bullet_match:
            content = bullet_match.group(1).strip()
            # Ensure bullet content ends with period if it's a complete thought
            if content and content[-1] not in ".!?":
                # Check if it's a complete clause (has subject+verb)
                if len(content.split()) >= 3:
                    content += "."
            processed_lines.append(content)
        else:
            processed_lines.append(line)
    
    text = " ".join(processed_lines)
    
    # Handle excessive whitespace and clean up
    text = re.sub(r"\s+", " ", text).strip()
    
    # Handle informal text: normalize inconsistent capitalization
    # But preserve proper nouns (capitalized words that are likely entities)
    # We don't correct spelling - we preserve as is per requirements
    
    # Handle conversational text: remove filler like "you know", "like", etc. is NOT needed
    # We preserve meaning, so keep it
    
    return text


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _run_provider(
    provider: LLMProvider,
    sentences: Sequence[str],
    settings: Settings,
    result: ExtractionResult,
) -> List[RawClaim]:
    """Call the provider, falling back to heuristics on any provider failure."""
    try:
        raw_claims = provider.extract_raw_claims(sentences, settings=settings)
        if raw_claims:
            return raw_claims
        if not settings.llm_fallback_to_heuristics:
            return raw_claims
        # An LLM that returns an empty list on a non-trivial article is treated
        # as a soft failure and retried with the deterministic extractor.
        if len(sentences) > 2:
            result.notes.append(f"{provider.name}_returned_no_claims:using_heuristics")
            result.used_fallback = True
            return heuristic_extract(sentences)
        return raw_claims
    except Exception as exc:  # provider/network/parse failures
        if not settings.llm_fallback_to_heuristics:
            if isinstance(exc, LLMError):
                raise
            raise ClaimExtractionError(f"LLM extraction failed: {exc}") from exc
        # Enhance error message for Ollama cases
        error_msg = str(exc)
        if provider.name == "ollama" and "ollama pull" not in error_msg.lower():
            # Add helpful guidance if not already present
            error_msg = f"{error_msg} (try: ollama pull {settings.ollama_model})"
            result.notes.append(f"{provider.name}_failed:{type(exc).__name__}:{error_msg}")
        else:
            result.notes.append(f"{provider.name}_failed:{type(exc).__name__}:{exc}")
        result.used_fallback = True
        return heuristic_extract(sentences)


def _clean_claim_text(text: str) -> str:
    claim = normalize_text(text or "")
    claim = _BULLET_RE.sub("", claim)
    claim = claim.strip().strip("\"'").strip()
    claim = re.sub(r"\s+([,.;:])", r"\1", claim)
    claim = " ".join(claim.split())
    if not claim:
        return ""
    # Remove any remaining HTML artifacts
    claim = re.sub(r"<[^>]+>", "", claim).strip()
    # Handle truncated claims
    if claim[-1] not in ".!?":
        claim = claim + "."
    # Ensure proper capitalization
    if claim and claim[0].islower():
        claim = claim[0].upper() + claim[1:]
    return claim


def _best_sentence_index(claim_text: str, context: ArticleContext) -> int | None:
    claim_lemmas = content_lemmas(claim_text)
    if not claim_lemmas:
        return None
    best_index, best_overlap = None, 0.0
    for index, lemmas in enumerate(context.sentence_lemmas):
        if not lemmas:
            continue
        overlap = len(claim_lemmas & lemmas) / len(claim_lemmas)
        if overlap > best_overlap:
            best_index, best_overlap = index, overlap
    return best_index


def _signature(text: str) -> frozenset[str]:
    return content_lemmas(text)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _numeric_signature(text: str) -> frozenset[float]:
    return frozenset(value for value, _, _ in numeric_occurrences(text))


DUPLICATE_JACCARD = 0.75


def _is_duplicate(
    lemmas_a: frozenset[str],
    lemmas_b: frozenset[str],
    numbers_a: frozenset[float],
    numbers_b: frozenset[float],
    type_a: str,
    type_b: str,
) -> bool:
    """Two claims are duplicates when they restate the same fact.

    Numbers are part of the fact's identity: "25 GW in 2025" and "24 GW in 2024"
    are never duplicates, however similar the wording is.
    """
    similarity = _jaccard(lemmas_a, lemmas_b)
    if similarity >= 0.90:
        return True
    return (
        similarity >= DUPLICATE_JACCARD
        and numbers_a == numbers_b
        and (type_a == type_b or {type_a, type_b} == {"factual", "opinion"})
    )


def _deduplicate(
    candidates: List[CandidateClaim], result: ExtractionResult
) -> List[CandidateClaim]:
    """Drop restatements of the same fact, keeping the most specific version."""
    unique: List[CandidateClaim] = []
    signatures: List[frozenset[str]] = []
    numbers: List[frozenset[float]] = []
    for candidate in candidates:
        signature = _signature(candidate.text)
        number_set = _numeric_signature(candidate.text)
        duplicate = False
        for index, existing in enumerate(signatures):
            if _is_duplicate(
                signature,
                existing,
                number_set,
                numbers[index],
                candidate.claim_type,
                unique[index].claim_type,
            ):
                duplicate = True
                # Keep the longer (more specific) claim, and its entity set.
                if len(candidate.text.split()) > len(unique[index].text.split()):
                    unique[index] = candidate
                    signatures[index] = signature
                    numbers[index] = number_set
                break
        if duplicate:
            result.deduplicated += 1
            continue
        unique.append(candidate)
        signatures.append(signature)
        numbers.append(number_set)
    return unique


def _semantic_deduplicate(
    candidates: List[CandidateClaim], result: ExtractionResult
) -> List[CandidateClaim]:
    """Second pass: remove claims that are semantically contained in another.
    
    E.g., "PM Modi signed a deal." is contained in "PM Modi signed a deal on oil."
    Keep the longer/more specific one.
    """
    if len(candidates) <= 1:
        return candidates
    
    # Sort by length descending so longer claims are kept
    sorted_cands = sorted(candidates, key=lambda c: len(c.text), reverse=True)
    kept = []
    kept_signatures = []
    
    for cand in sorted_cands:
        cand_lemmas = content_lemmas(cand.text)
        is_contained = False
        for kept_sig in kept_signatures:
            # If cand's lemmas are subset of kept's lemmas, it's redundant
            # e.g., {pm, modi, sign, deal} subset of {pm, modi, sign, deal, oil}
            # Only apply if candidate has multiple lemmas to avoid dropping atomic relational facts
            if cand_lemmas and len(cand_lemmas) >= 2 and cand_lemmas.issubset(kept_sig):
                is_contained = True
                break
            # Also check Jaccard for high overlap where one is substring of other
            # e.g., "signed a deal" vs "signed a deal on oil" -> Jaccard 0.8, but one is substring
            jaccard = _jaccard(cand_lemmas, kept_sig)
            if jaccard >= 0.70:
                # Check if shorter is substring of longer (text containment)
                shorter_text = cand.text.lower()
                for kept_cand in kept:
                    if shorter_text.rstrip(".").lower() in kept_cand.text.lower():
                        # Shorter is contained in longer text
                        is_contained = True
                        break
                if is_contained:
                    break
        
        if is_contained:
            result.deduplicated += 1
            continue
        
        kept.append(cand)
        kept_signatures.append(cand_lemmas)
    
    # Restore original order (by article position)
    kept.sort(key=lambda c: c.article_order)
    return kept


def _score_candidate(
    candidate: CandidateClaim,
    article_lemmas: frozenset[str],
    main_entity: str | None,
    settings: Settings,
) -> float:
    """Deterministic priority score. Truth is explicitly NOT a factor."""
    text = candidate.text
    score = 0.0

    numbers = numeric_occurrences(text)
    score += min(len(numbers), 3) * 3.0
    if re.search(r"\b(?:19|20)\d{2}\b", text):
        score += 2.0
    score += min(len(candidate.entities), 3) * 2.0

    if candidate.claim_type == "factual":
        score += 3.0
    elif candidate.claim_type == "prediction":
        score += 1.0
    elif candidate.claim_type == "opinion":
        score += 0.5
    else:  # non_verifiable
        score -= 4.0

    if has_marker(text, EVENT_VERBS):
        score += 2.0

    tokens = len(text.split())
    if 6 <= tokens <= 30:
        score += 1.0
    elif tokens > settings.max_claim_tokens:
        score -= 2.0

    if main_entity and main_entity.casefold() in text.casefold():
        score += 2.0

    # Reward claims that stay close to the article's own vocabulary.
    claim_lemmas = content_lemmas(text)
    if claim_lemmas:
        score += 2.0 * (len(claim_lemmas & article_lemmas) / len(claim_lemmas))

    return round(score, 4)


def _select(
    candidates: List[CandidateClaim], settings: Settings
) -> List[CandidateClaim]:
    """Pick at most `max_claims` candidates, then restore article order.

    Never pads: if fewer than `min_claims` pass validation, fewer are returned.
    """
    ranked = sorted(
        candidates,
        key=lambda c: (-c.priority, c.article_order),
    )
    selected = ranked[: settings.max_claims]
    selected.sort(key=lambda c: (c.article_order, -c.priority))
    return selected


# ---------------------------------------------------------------------------
# CLI: python -m backend.s1_claims --text "..."  |  --url https://...  [--debug]
# ---------------------------------------------------------------------------


def _main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="VerifyAI S1 claim extraction")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text", help="article text (or '-' to read stdin)")
    group.add_argument("--url", help="article URL")
    parser.add_argument("--provider", choices=["mock", "gemini", "ollama"], default=None)
    parser.add_argument("--debug", action="store_true", help="print validation diagnostics")
    args = parser.parse_args(argv)

    settings = get_settings()
    provider = get_provider(args.provider, settings=settings) if args.provider else None

    if args.url:
        article = extract_article_from_url(args.url, settings=settings)
        print(f"[fetched {len(article)} chars from {args.url}]", file=sys.stderr)
    else:
        article = sys.stdin.read() if args.text == "-" else args.text

    result = extract_claims_detailed(article, provider=provider, settings=settings)
    payload = [claim.model_dump() for claim in result.claims]
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if args.debug:
        print(json.dumps(result.summary(), indent=2, ensure_ascii=False), file=sys.stderr)
        for text, reason in result.rejected:
            print(f"REJECTED [{reason}] {text}", file=sys.stderr)
        if result.used_fallback:
            print(f"WARNING: Used fallback heuristics (provider {result.provider} failed). Notes: {result.notes}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
