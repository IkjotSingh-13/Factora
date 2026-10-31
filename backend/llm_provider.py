"""LLM provider abstraction for S1.

Supported providers (select with LLM_PROVIDER):

  * "gemini" — Google Gemini via the REST API (httpx, primary/remote)
  * "ollama" — local model server, default qwen2.5:7b
  * "mock"   — offline deterministic heuristic extractor (no API key, no network)

All providers share one contract::

    provider.complete(system_prompt, user_prompt) -> str   # raw text/JSON

The extraction pipeline owns prompt building and JSON parsing, so switching
between a remote API and a local Ollama model requires **no** changes to
`s1_claims.py`.

The MOCK provider is intentionally a real (if simple) rule-based extractor that
emits the same JSON contract as an LLM. That means MOCK mode exercises the
entire parse -> validate -> dedupe -> select pipeline for real, which is what
makes offline tests meaningful.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, List, Sequence

import httpx

from .config import Settings, get_settings
from .errors import LLMError, LLMOutputError
from .schemas import RawClaim
from .validators import (
    ATTRIBUTION_MARKERS,
    get_nlp,
    has_marker,
    looks_like_opinion,
    looks_like_prediction,
    looks_vague,
    normalize_text,
)

# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are the claim-extraction engine of VerifyAI, a fact-checking system. "
    "You turn news articles into atomic, independently verifiable claims. "
    "You never invent facts, numbers or entities. "
    "You respond with strict JSON only - no commentary, no markdown fences."
)

PROMPT_TEMPLATE = """Extract the most useful claims from the article sentences below.

RULES
1. ATOMIC: exactly one independently verifiable fact per claim. Split compound
   sentences that state several facts.
2. RESOLVE REFERENCES: replace "these projects", "the scheme", "the company",
   "he", "they" with the actual antecedent from the article
   (e.g. "These projects ..." -> "The 15 solar projects ...").
3. COPY numbers, dates, currencies, units and qualifiers EXACTLY as written
   ("25 GW", "Rs 5,000 crore", "about 40%", "nearly 2 million", "2025").
4. NEVER STRENGTHEN. Keep hedges ("may", "plans to", "expected to"). A sentence
   that reports an announcement ("The government announced ...") is factual,
   not a prediction. Do not turn a prediction into an established fact.
5. DO NOT INVENT facts, context, entities, numbers or dates that are not in the
   article. Do not expand scope beyond the sentences you are given.
6. "claim_type" must be exactly one of these lowercase values:
   - "factual": statistics, completed events, historical facts, government
     announcements, officially announced plans
   - "prediction": forward-looking statement ("may become", "by 2030")
   - "opinion": subjective judgement, not checkable as stated
   - "non_verifiable": too vague / missing specificity to be checked
7. Return 3 to 6 claims. If the article contains fewer suitable claims, return
   fewer. If it contains more, keep the most specific, verifiable and
   important ones (relevance to the article's main subject matters).
8. "source_sentence_index" must be the index of the sentence the claim is
   based on.

OUTPUT (strict JSON, no markdown):
{{"claims": [{{"text": "...", "claim_type": "factual", "source_sentence_index": 0, "entities": ["India"]}}]}}

ARTICLE SENTENCES
{sentences}
"""


def format_numbered_sentences(sentences: Sequence[str], limit: int | None = None) -> str:
    lines = []
    for index, sentence in enumerate(sentences):
        if limit is not None and index >= limit:
            break
        lines.append(f"[{index}] {' '.join(sentence.split())}")
    return "\n".join(lines)


def build_prompts(
    sentences: Sequence[str], settings: Settings | None = None
) -> tuple[str, str]:
    settings = settings or get_settings()
    body = format_numbered_sentences(sentences, limit=settings.max_sentences_to_llm)
    return SYSTEM_PROMPT, PROMPT_TEMPLATE.format(sentences=body)


# ---------------------------------------------------------------------------
# JSON parsing
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_CLAIM_TYPE_ALIASES = {
    "factual": "factual",
    "fact": "factual",
    "statement": "factual",
    "verifiable": "factual",
    "numeric": "factual",
    "statistic": "factual",
    "statistical": "factual",
    "prediction": "prediction",
    "forecast": "prediction",
    "speculation": "prediction",
    "opinion": "opinion",
    "subjective": "opinion",
    "judgement": "opinion",
    "judgment": "opinion",
    "non_verifiable": "non_verifiable",
    "nonverifiable": "non_verifiable",
    "unverifiable": "non_verifiable",
    "not_verifiable": "non_verifiable",
    "non-verifiable": "non_verifiable",
}


def normalize_claim_type(value: object, default: str = "non_verifiable") -> str:
    """Map an LLM-supplied claim type onto the four mandated lowercase values."""
    if not isinstance(value, str):
        return default
    key = value.strip().lower().replace(" ", "_").replace("-", "_")
    return _CLAIM_TYPE_ALIASES.get(key, default)


def _strip_fences(text: str) -> str:
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _extract_json_span(text: str) -> str | None:
    """Return the first balanced {...} or [...] span found in `text`."""
    start_positions = [pos for pos in (text.find("{"), text.find("[")) if pos != -1]
    if not start_positions:
        return None
    start = min(start_positions)
    opening = text[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _loads_lenient(candidate: str):
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    repaired = re.sub(r",\s*([}\]])", r"\1", candidate)  # trailing commas
    repaired = repaired.replace("\u201c", '"').replace("\u201d", '"')
    return json.loads(repaired)


def parse_claims_payload(raw: str) -> List[RawClaim]:
    """Parse provider output into RawClaim objects.

    Raises:
        LLMOutputError: when the payload contains no usable claim JSON.
    """
    if raw is None or not str(raw).strip():
        raise LLMOutputError("LLM returned an empty response")

    text = _strip_fences(str(raw))
    span = _extract_json_span(text)
    if span is None:
        raise LLMOutputError(
            f"LLM output contained no JSON object/array (first 120 chars: {text[:120]!r})"
        )

    try:
        data = _loads_lenient(span)
    except json.JSONDecodeError as exc:
        raise LLMOutputError(f"LLM output was not valid JSON: {exc}") from exc

    if isinstance(data, dict):
        matched = False
        for key in ("claims", "items", "results", "data"):
            if key not in data:
                continue
            if not isinstance(data[key], list):
                raise LLMOutputError(
                    f"Field {key!r} must be a JSON array, got "
                    f"{type(data[key]).__name__}"
                )
            data = data[key]
            matched = True
            break
        if not matched:
            single = data.get("text") or data.get("claim")
            if isinstance(single, str):
                data = [data]
            else:
                raise LLMOutputError(
                    "JSON object contained no 'claims' array and no claim text"
                )
    if not isinstance(data, list):
        raise LLMOutputError(
            f"Expected a JSON array of claims, got {type(data).__name__}"
        )

    raw_claims: List[RawClaim] = []
    for item in data:
        if isinstance(item, str):
            text_value = item
            claim_type = "factual"
            source_index = None
            entities: Iterable[str] = []
        elif isinstance(item, dict):
            text_value = (
                item.get("text")
                or item.get("claim")
                or item.get("claim_text")
                or item.get("statement")
                or ""
            )
            claim_type = item.get("claim_type") or item.get("type") or "factual"
            source_index = item.get("source_sentence_index", item.get("sentence_index"))
            entities = item.get("entities") or []
        else:
            continue

        if not isinstance(text_value, str) or not text_value.strip():
            continue
        if not isinstance(entities, list):
            entities = [entities] if isinstance(entities, str) else []

        if isinstance(source_index, bool) or not isinstance(source_index, int):
            source_index = None

        raw_claims.append(
            RawClaim(
                text=" ".join(text_value.split()),
                claim_type=normalize_claim_type(claim_type),
                source_sentence_index=source_index,
                entities=[str(entity) for entity in entities if str(entity).strip()],
            )
        )
    return raw_claims


# ---------------------------------------------------------------------------
# Provider base class + implementations
# ---------------------------------------------------------------------------


class LLMProvider:
    """Contract every provider must satisfy."""

    name = "base"
    is_mock = False

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise NotImplementedError

    def extract_raw_claims(
        self, sentences: Sequence[str], settings: Settings | None = None
    ) -> List[RawClaim]:
        """Build prompts, call the model, parse the JSON payload."""
        system_prompt, user_prompt = build_prompts(sentences, settings=settings)
        raw = self.complete(system_prompt, user_prompt)
        return parse_claims_payload(raw)

    def close(self) -> None:  # pragma: no cover - overridden when needed
        return None


class GeminiProvider(LLMProvider):
    """Google Gemini REST provider (no SDK dependency, uses httpx)."""

    name = "gemini"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.gemini_api_key:
            raise LLMError(
                "GEMINI_API_KEY is not set. Configure it in .env or use LLM_PROVIDER=mock."
            )

    @property
    def _endpoint(self) -> str:
        return (
            f"{self.settings.gemini_base_url.rstrip('/')}/models/"
            f"{self.settings.gemini_model}:generateContent"
        )

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "topP": 0.9,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
            },
        }
        try:
            response = httpx.post(
                self._endpoint,
                params={"key": self.settings.gemini_api_key},
                json=payload,
                timeout=self.settings.gemini_timeout,
            )
        except httpx.TimeoutException as exc:
            raise LLMError(f"Gemini request timed out after {self.settings.gemini_timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini request failed: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            # Never surface the API key: only the status and a short reason.
            raise LLMError(
                f"Gemini API error {response.status_code}: {_short_body(response)}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMOutputError("Gemini returned a non-JSON HTTP body") from exc

        if isinstance(data.get("promptFeedback"), dict) and data["promptFeedback"].get("blockReason"):
            raise LLMError(f"Gemini blocked the prompt: {data['promptFeedback']['blockReason']}")

        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMOutputError("Gemini returned no candidates")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        if not text.strip():
            raise LLMOutputError("Gemini returned an empty candidate")
        return text


class OllamaProvider(LLMProvider):
    """Local Ollama provider — default model qwen2.5:7b."""

    name = "ollama"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def _endpoint(self) -> str:
        return f"{self.settings.ollama_host.rstrip('/')}/api/chat"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.settings.ollama_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 2048},
        }
        try:
            response = httpx.post(
                self._endpoint, json=payload, timeout=self.settings.ollama_timeout
            )
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"Ollama request timed out after {self.settings.ollama_timeout}s "
                f"(is `ollama serve` running with {self.settings.ollama_model} pulled?)"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama request failed: {type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise LLMError(f"Ollama API error {response.status_code}: {_short_body(response)}")

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMOutputError("Ollama returned a non-JSON HTTP body") from exc

        content = (data.get("message") or {}).get("content") or data.get("response") or ""
        if not str(content).strip():
            raise LLMOutputError("Ollama returned an empty message")
        return str(content)


class MockProvider(LLMProvider):
    """Offline provider.

    Two modes:
      * default: deterministic heuristic extraction from the numbered sentences
        in the prompt (a genuine, if simple, rule-based pipeline).
      * `response_override`: return a fixed raw string instead — used by tests to
        simulate malformed LLM output.
    """

    name = "mock"
    is_mock = True

    def __init__(self, response_override: str | None = None) -> None:
        self.response_override = response_override

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        if self.response_override is not None:
            return self.response_override
        sentences = parse_numbered_sentences(user_prompt)
        claims = heuristic_extract(sentences)
        return json.dumps(
            {
                "claims": [
                    {
                        "text": claim.text,
                        "claim_type": claim.claim_type,
                        "source_sentence_index": claim.source_sentence_index,
                        "entities": claim.entities,
                    }
                    for claim in claims
                ]
            },
            ensure_ascii=False,
        )


class StaticProvider(LLMProvider):
    """Testing utility: always returns a fixed raw payload."""

    name = "static"

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self.payload


class FailingProvider(LLMProvider):
    """Testing utility: always raises an LLMError."""

    name = "failing"

    def __init__(self, message: str = "simulated provider failure") -> None:
        self.message = message

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        raise LLMError(self.message)


def _short_body(response: httpx.Response, limit: int = 200) -> str:
    try:
        body = response.text or ""
    except Exception:  # pragma: no cover - defensive
        return "<unreadable body>"
    return " ".join(body.split())[:limit]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDERS = {
    "gemini": GeminiProvider,
    "ollama": OllamaProvider,
    "mock": MockProvider,
}


def get_provider(
    name: str | None = None, settings: Settings | None = None, **kwargs
) -> LLMProvider:
    """Instantiate the configured provider.

    Args:
        name: override for LLM_PROVIDER ("gemini" | "ollama" | "mock").
        settings: optional settings object (useful in tests).
        **kwargs: forwarded to the provider constructor.
    """
    settings = settings or get_settings()
    provider_name = (name or settings.llm_provider or "mock").strip().lower()
    provider_cls = _PROVIDERS.get(provider_name)
    if provider_cls is None:
        raise LLMError(
            f"Unknown LLM provider {provider_name!r}; expected one of {sorted(_PROVIDERS)}"
        )
    if provider_cls is MockProvider:
        return MockProvider(**kwargs)
    return provider_cls(settings=settings, **kwargs)


# ---------------------------------------------------------------------------
# Heuristic extractor (MOCK provider + safety fallback)
# ---------------------------------------------------------------------------

_NUMBERED_LINE_RE = re.compile(r"^\s*\[(\d+)\]\s?(.*)$")

# Boilerplate lines that are never claims.
_BOILERPLATE_RE = re.compile(
    r"^(read more|also read|advertisement|photo:|image:|credit:|subscribe|"
    r"sign up|share this|follow us|watch:|listen:|related:|correction|"
    r"disclaimer|tags?:)",
    re.IGNORECASE,
)

_COMPOUND_SPLIT_RE = re.compile(r"(?:;\s+|\s+and\s+|\s+while\s+|\s+but\s+|,\s+and\s+)")


def parse_numbered_sentences(prompt: str) -> List[str]:
    """Recover the numbered article sentences from a built prompt."""
    sentences: List[str] = []
    in_block = False
    for line in prompt.splitlines():
        if line.strip().startswith("ARTICLE SENTENCES"):
            in_block = True
            continue
        if not in_block:
            continue
        match = _NUMBERED_LINE_RE.match(line)
        if match:
            sentences.append(match.group(2).strip())
    return sentences


def _noun_chunk_antecedent(sentence: str) -> str | None:
    """Find a countable antecedent NP, e.g. "15 solar projects".

    Skips measurement chunks like "10 GW" so the antecedent stays a *thing*
    ("15 solar projects") rather than a quantity.
    """
    if not sentence:
        return None
    from .validators import NON_ENTITY_TOKENS

    doc = get_nlp()(sentence)
    for chunk in doc.noun_chunks:
        if not any(token.like_num for token in chunk):
            continue
        head = chunk[-1]
        if head.pos_ != "NOUN" or head.lemma_.lower() in NON_ENTITY_TOKENS:
            continue
        text = " ".join(chunk.text.split())
        for article in ("the ", "a ", "an "):
            if text.lower().startswith(article):
                text = text[len(article) :]
        return text.strip()
    return None


def _split_compound(sentence: str) -> List[str]:
    """Split a compound sentence into clauses that each own a subject + verb.

    Handles two shapes:
      * full clauses:  "India launched 15 projects and the ministry funded them"
      * elliptical:    "The company opened 5 factories and hired 2,000 workers"
                       -> the shared subject is propagated to the second claim.
    """
    parts = _COMPOUND_SPLIT_RE.split(sentence)
    if len(parts) <= 1:
        return [sentence]

    units: List[str] = []
    buffer = parts[0]
    for part in parts[1:]:
        candidate_left, candidate_right = buffer.strip(), part.strip()

        if _is_standalone_clause(candidate_right) and _has_finite_verb(candidate_left):
            units.append(candidate_left)
            buffer = candidate_right
            continue

        # Elliptical coordination: right side starts with a verb, so it inherits
        # the left clause's subject ("... and hired 2,000 workers in 2024").
        if _starts_with_verb(candidate_right) and _has_finite_verb(candidate_left):
            subject = _subject_of(candidate_left)
            if subject:
                units.append(candidate_left)
                buffer = f"{subject} {candidate_right}"
                continue

        joiner = ", and " if ", and " in sentence else " and "
        buffer = f"{buffer}{joiner}{candidate_right}"
    units.append(buffer.strip())
    return [unit for unit in units if unit]


def _starts_with_verb(text: str) -> bool:
    if len(text.split()) < 3:
        return False
    tokens = [token for token in get_nlp()(text) if not token.is_space]
    return bool(tokens) and tokens[0].pos_ in ("VERB", "AUX")


def _subject_of(text: str) -> str | None:
    """Return the surface form of the clause's subject, if any."""
    doc = get_nlp()(text)
    for token in doc:
        if token.dep_ in ("nsubj", "nsubjpass") and token.head.pos_ in ("VERB", "AUX"):
            subtree = " ".join(child.text for child in token.subtree)
            return " ".join(subtree.split())
    return None


def _has_finite_verb(text: str) -> bool:
    doc = get_nlp()(text)
    return any(token.pos_ in ("VERB", "AUX") for token in doc)


def _is_standalone_clause(text: str) -> bool:
    """True when `text` starts with its own subject and contains a verb."""
    if len(text.split()) < 3:
        return False
    doc = get_nlp()(text)
    tokens = [token for token in doc if not token.is_space]
    if not tokens:
        return False
    first = tokens[0]
    starts_with_subject = first.pos_ in ("PRON", "PROPN", "DET", "NUM")
    has_verb = any(token.pos_ in ("VERB", "AUX") for token in tokens)
    return starts_with_subject and has_verb


_COUNTRY_REFERENCE_WORDS = ("country", "nation", "state", "region", "economy")
_ORG_REFERENCE_WORDS = (
    "company",
    "firm",
    "business",
    "startup",
    "group",
    "giant",
    "maker",
    "manufacturer",
    "corporation",
    "organisation",
    "organization",
)


def _resolve_leading_reference(
    unit: str,
    antecedent: str | None,
    main_entity: str | None,
    org_entity: str | None = None,
) -> str:
    """Replace a leading context-dependent reference with its antecedent.

    "These projects have 10 GW."          -> "The 15 solar projects have 10 GW."
    "They have 10 GW."                    -> "The 15 solar projects have 10 GW."
    "The country has 25 GW of solar."     -> "India has 25 GW of solar."
    """
    doc = get_nlp()(unit)
    tokens = [token for token in doc if not token.is_space]
    if not tokens:
        return unit
    first = tokens[0]

    # (a) "The country / the nation" -> article's main entity;
    #     "The company / the firm"   -> article's main organisation.
    if first.lower_ in ("the", "this", "that") and len(tokens) > 1:
        reference_word = tokens[1].lemma_.lower()
        replacement = None
        if reference_word in _COUNTRY_REFERENCE_WORDS:
            replacement = main_entity
        elif reference_word in _ORG_REFERENCE_WORDS:
            replacement = org_entity
        if replacement:
            end = tokens[1].idx + len(tokens[1].text)
            rest = unit[end:].lstrip()
            if rest:
                return f"{replacement} {rest}"

    if not antecedent:
        return unit

    # (b) Plural/animate pronoun subject: "They have 10 GW." -> antecedent.
    if first.pos_ == "PRON" and first.lemma_.lower() in ("they",):
        rest = unit[first.idx + len(first.text) :].lstrip()
        if rest:
            return f"The {antecedent} {rest}"

    # (c) Demonstrative NP: "These projects ..." -> "The 15 solar projects ...".
    if first.lower_ not in ("these", "those", "this", "such"):
        return unit

    reference_end = 0
    for token in tokens[:6]:
        reference_end = token.idx + len(token.text)
        if token.pos_ in ("NOUN", "PROPN"):
            break
    reference = unit[:reference_end].strip()
    if not reference:
        return unit

    head_nouns = [token for token in get_nlp()(reference) if token.pos_ in ("NOUN", "PROPN")]
    antecedent_head = {
        token.lemma_.lower()
        for token in get_nlp()(antecedent)
        if token.pos_ in ("NOUN", "PROPN")
    }
    if head_nouns and antecedent_head and not any(
        token.lemma_.lower() in antecedent_head for token in head_nouns
    ):
        # "these projects" vs antecedent "the scheme" -> different referents.
        return unit

    remainder = unit[reference_end:].lstrip()
    if not remainder:
        return unit
    resolved = f"The {antecedent} {remainder}"
    return resolved[0].upper() + resolved[1:]


def _resolve_trailing_pronoun(unit: str, antecedent: str | None) -> str:
    """"... an investment of Rs 5,000 crore for them." -> "... for the 15 solar projects."."""
    if not antecedent or not re.search(r"\bthem\b", unit, re.IGNORECASE):
        return unit
    return re.sub(r"\bthem\b", f"the {antecedent}", unit, count=1, flags=re.IGNORECASE)


_ANNOUNCEMENT_VERB_RE = re.compile(
    r"\b(announced|announces|unveiled|declared|approved|approves|committed|"
    r"pledged|set a target|set targets?|signed|introduced|passed|sanctioned|"
    r"allocated|launched|launches)\b",
    re.IGNORECASE,
)


# "raised its forecast to 7.2%" is a factual action about a forecast, not a
# prediction. Actions performed ON a forecast/target count as announcements.
_FORECAST_ACTION_RE = re.compile(
    r"\b(raised|cut|revised|lowered|maintained|kept|set|retained|trimmed|hiked|"
    r"slashed|updated|revised upwards|revised downwards)\b[^.]{0,60}"
    r"\b(forecast|projection|estimate|target|guidance|outlook)\b",
    re.IGNORECASE,
)


def _classify(unit: str) -> str:
    """Rule-based claim-type classification used by the heuristic extractor.

    Order matters: an *announcement about* a future plan is a factual claim
    about the announcement, so announcement verbs are checked before the
    forward-looking patterns.
    """
    text = unit.strip()
    if looks_like_opinion(text):
        return "opinion"

    has_number = bool(_numbers(text))
    has_date = bool(re.search(r"\b(?:19|20)\d{2}\b", text))
    reports_announcement = (
        _ANNOUNCEMENT_VERB_RE.search(text)
        or _FORECAST_ACTION_RE.search(text)
        or has_marker(text, ATTRIBUTION_MARKERS)
    )

    if reports_announcement and (has_number or has_date or looks_like_prediction(text)):
        return "factual"
    if looks_like_prediction(text):
        return "prediction"
    if looks_vague(text) and not (has_number or has_date):
        return "non_verifiable"
    if has_number or has_date or reports_announcement:
        return "factual"

    doc = get_nlp()(text)
    has_verb = any(token.pos_ in ("VERB", "AUX") for token in doc)
    has_entity = any(
        ent.label_ in ("PERSON", "ORG", "GPE", "LOC", "NORP") for ent in doc.ents
    )
    has_named_subject = any(token.pos_ == "PROPN" for token in doc)
    if has_verb and (has_entity or has_named_subject):
        return "factual"
    return "non_verifiable"


def _numbers(text: str):
    from .validators import numeric_occurrences

    return numeric_occurrences(text)


def heuristic_extract(sentences: Sequence[str]) -> List[RawClaim]:
    """Deterministic rule-based claim extraction.

    Used by MOCK mode and as the safety fallback when the configured LLM fails.
    Intentionally simple: it splits compounds, resolves simple references and
    labels each unit. It never paraphrases or invents content.
    """
    from .validators import main_article_entity, salient_org_entity

    full_text = " ".join(sentences)
    claims: List[RawClaim] = []
    previous_sentence = ""
    antecedent: str | None = None
    main_entity = main_article_entity(full_text)
    org_entity = salient_org_entity(full_text)

    for index, sentence in enumerate(sentences):
        sentence = normalize_text(sentence)
        if not sentence or _BOILERPLATE_RE.match(sentence):
            previous_sentence = sentence or previous_sentence
            continue
        if sentence.endswith("?"):
            previous_sentence = sentence  # questions are not claims
            continue

        # Antecedents persist across sentences: "These projects ..." two
        # sentences after "15 solar projects" still resolves.
        candidate_antecedent = _noun_chunk_antecedent(previous_sentence)
        if candidate_antecedent:
            antecedent = candidate_antecedent

        for unit in _split_compound(sentence):
            unit = normalize_text(unit).strip(" ,;:")
            if not unit:
                continue
            unit = _resolve_leading_reference(unit, antecedent, main_entity, org_entity)
            unit = _resolve_trailing_pronoun(unit, antecedent)
            if not unit.endswith((".", "!", "?")):
                unit = unit + "."
            unit = unit[0].upper() + unit[1:]
            if len(unit.split()) < 4:
                continue
            claims.append(
                RawClaim(
                    text=unit,
                    claim_type=_classify(unit),
                    source_sentence_index=index,
                    entities=[],
                )
            )
        previous_sentence = sentence
    return claims
