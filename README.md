# VerifyAI — S1: Claim Extraction

**Owner:** Ikjot · **Scope:** S1 only (claim extraction). No retrieval (S2), stance (S3), scoring (S4), reporting (S5) or frontend.

Turns an article (pasted text or URL) into **3–6 atomic, independently verifiable claims**, each with a type and grounded entities.

```python
from backend import extract_claims

claims = extract_claims(article_text)
# [Claim(id='claim_001', text='India launched 15 solar projects in 2025.',
#        entities=['India'], claim_type='factual'), ...]
```

---

## 1. The Claim contract

```python
class Claim(BaseModel):
    id: str
    text: str
    entities: List[str]
    claim_type: str      # factual | prediction | opinion | non_verifiable
```

Exactly four fields — enforced with `extra="forbid"`, so an accidental extra field raises instead of silently leaking into the pipeline. All internal bookkeeping (source sentence index, validation reasons, priority score) lives in separate dataclasses (`RawClaim`, `CandidateClaim`, `ValidationReport`) and **never** on `Claim`.

---

## 2. Architecture

```
article text ─┐
              ├─► normalise + sentence index (spaCy)
article URL ──┘        (trafilatura → BeautifulSoup fallback, SSRF-guarded)
                              │
                              ▼
                   LLMProvider.complete(system, prompt)
                   ├─ GeminiProvider  (REST via httpx)
                   ├─ OllamaProvider  (qwen2.5:7b)
                   └─ MockProvider    (deterministic heuristics)
                              │
                              ▼  strict JSON parsing (fences, spans, repair)
                    candidate claims + source_sentence_index
                              │
                              ▼  per-claim validation (6 independent gates)
              numbers · entities · grounding · hedges · qualifiers · support
                              │
                              ▼  de-duplicate → prioritise → select 3–6
                          list[Claim]
```

| File | Responsibility |
|---|---|
| `backend/schemas.py` | `Claim` (public) + internal `RawClaim`, `CandidateClaim`, `ValidationReport`, `ExtractionResult` |
| `backend/config.py` | env-driven `Settings` (`.env` via python-dotenv), no hardcoded secrets |
| `backend/llm_provider.py` | provider abstraction, prompt building, JSON parsing, heuristic extractor |
| `backend/validators.py` | spaCy layer, entities, numbers, **strict validation**, article context cache |
| `backend/article_extractor.py` | URL validation/SSRF guard, safe fetch, trafilatura + BS4 extraction |
| `backend/s1_claims.py` | orchestration + public API + CLI |
| `backend/errors.py` | typed exceptions mappable to HTTP codes |

---

## 3. Public API

```python
extract_claims(article_text: str) -> list[Claim]
extract_article_from_url(url: str) -> str
extract_claims_from_url(url: str) -> list[Claim]
extract_entities(claim_text: str) -> list[str]
validate_claim(claim_text: str, article_text: str) -> bool

# diagnostics (not part of the deliverable schema)
extract_claims_detailed(article_text) -> ExtractionResult   # .claims, .rejected, .summary()
```

Exceptions: `EmptyArticleError`, `InvalidURLError`, `ArticleExtractionError`, `LLMError`, `LLMOutputError`, `ClaimExtractionError`.

### FastAPI integration (S2–S5 consume this)

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend import Claim, extract_claims, extract_claims_from_url, extract_article_from_url
from backend.errors import (
    ArticleExtractionError, ClaimExtractionError, EmptyArticleError, InvalidURLError,
)

router = APIRouter(prefix="/s1", tags=["claims"])

class TextIn(BaseModel):
    article_text: str | None = None
    url: str | None = None

@router.post("/claims", response_model=list[Claim])
def claims(payload: TextIn):
    try:
        if payload.article_text:
            return extract_claims(payload.article_text)
        if payload.url:
            return extract_claims_from_url(payload.url)
        raise HTTPException(400, "Provide article_text or url")
    except (EmptyArticleError, InvalidURLError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except ArticleExtractionError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ClaimExtractionError as exc:
        raise HTTPException(422, str(exc)) from exc
```

### CLI

```bash
python -m backend --text "India installed 25 GW of solar capacity in 2025. ..."
python -m backend --url https://example.com/story --debug
echo "$ARTICLE" | python -m backend --text -
python -m backend --provider ollama --url https://example.com/story
```

---

## 4. Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm   # only if the wheel in requirements.txt fails
cp .env.example .env                      # defaults to LLM_PROVIDER=mock (no key needed)
pytest
```

Run against Gemini:

```env
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-key-here
```

Run against a local model:

```bash
ollama serve && ollama pull qwen2.5:7b
LLM_PROVIDER=ollama python -m backend --url https://example.com/story
```

Switching providers requires **no code change** — same prompts, same parsing, same validation.

---

## 5. What "strict validation" actually means

Every claim must pass all six gates; a claim is rejected (and the reason recorded in `ExtractionResult.rejected`) if any gate fails. A single embedding-similarity score is *not* used as the gate — it would pass a claim whose only error is a flipped number.

| Gate | Catches | Example rejected |
|---|---|---|
| Lexical grounding (content-lemma recall vs article) | unrelated/fabricated text | "The cricket team won the world cup" |
| Numeric grounding (every number must exist in the article) | fabricated/altered statistics | article says 25 GW, claim says **35 GW** |
| Entity + proper-noun grounding | invented entities, context expansion | article about India, claim says **China** |
| Hedge integrity (speculation must survive) | prediction → fact conversion | article: "India **may** become the largest by 2030" → claim: "India **will**…" |
| Qualifier integrity | approximation → exact conversion | article: "**approximately** 40%" → claim: "40%" |
| Support-sentence existence | claims unrelated to any sentence | — |

Deliberate nuance: **a government announcement about a future plan is `factual`**, because the announcement itself happened. "The ministry announced it will build 100 solar parks by 2027" → `factual`; "The government will build 100 solar parks by 2027" (no announcement, no hedge) → `prediction`.

Entities are filtered to *named* entities relevant to the claim (orgs, places, people, laws…). Bare role nouns (`the government`, `the company`) and units/abbreviations (`GW`, `Rs`) are not emitted — `"The government announced … ₹5,000 crore"` gets `["India"]`, not `["The government"]`.

---

## 6. Atomicity and reference resolution

The compound sentence `"The company opened 5 factories in Gujarat and hired 2,000 workers in 2024."` becomes two claims, with the shared subject propagated to the second:

```
The company opened 5 factories in Gujarat.
The company hired 2,000 workers in 2024.
```

References are resolved before validation, using the antecedent noun phrase and the article's salient entities:

```
"These projects have a combined capacity of 10 GW."   → "The 15 solar projects have a combined capacity of 10 GW."
"... an investment of Rs 5,000 crore for them."       → "... for the 15 solar projects."
"The country currently has ..."                        → "India currently has ..."
"The company sold 46.2 million iPhones."               → "Apple sold 46.2 million iPhones."
```

---

## 7. Selection rules

* Aim for 3–6 claims; **never pad** — a short article returns fewer, and an article with no checkable statements returns `[]`.
* Priority score (deterministic, never truth-based): numbers/dates (specificity), named entities, event/announcement verbs, article-centrality of the entity, article-vocabulary overlap, type weight (`factual` > `prediction` > `opinion` > `non_verifiable`).
* De-duplication compares lemma sets **and** numeric signatures, so `"25 GW in 2025"` / `"24 GW in 2024"` are never merged, while `"India installed 25 GW…"` / `"India added 25 GW…"` are.
* Final output is ordered by article position with sequential `claim_001…` ids.

---

## 8. MOCK mode (offline, no API key)

`LLM_PROVIDER=mock` uses a **deterministic heuristic extractor** that emits the same JSON contract as the LLM. It splits compounds, resolves simple references and labels claim types with rules — it does not paraphrase. This means MOCK mode exercises the full parse → validate → dedupe → select path (tests are meaningful, not canned), and the same code is reused as the **fallback when the LLM fails**.

MOCK mode is weaker than a good LLM on heavily paraphrased or unusually structured text. It is the default so the module runs anywhere; set `LLM_PROVIDER=gemini` or `ollama` for production quality.

---

## 9. URL extraction & SSRF policy

* trafilatura primary → BeautifulSoup fallback (chrome/nav/ads/sidebar removed).
* `http`/`https` only, no credentials in URL, port allow-list.
* Hostnames resolved **before** connecting; loopback/private/link-local/CGNAT/multicast/reserved/cloud-metadata addresses rejected — blocks `169.254.169.254`, `127.0.0.1`, `[::1]`, `10.x`, `192.168.x`, `metadata.google.internal`…
* Redirects followed manually, **every hop re-validated** (a public URL cannot bounce the fetcher onto an internal address).
* Streamed download with a hard byte cap (5 MB default) + Content-Type check + timeouts.

Residual risk, stated honestly: DNS is resolved once for validation and again by httpx when connecting, so a determined rebinding attacker could still target a narrow window; fully closing it needs socket-level IP pinning. Acceptable for a hackathon S1 — the classic metadata/internal attacks are blocked.

---

## 10. Tests

```bash
pytest                    # 159 tests, ~12s
```

| File | Covers |
|---|---|
| `test_claim_extraction.py` | normal article, statistics, historical events, announcements, predictions, opinions, non-verifiable, compound splitting, reference resolution, duplicates, ≤6 selection, fewer-than-3, empty input, mocked-LLM path |
| `test_entities.py` | spec examples, claim-relevance, grounding, dedupe, generic/unit filtering, inherited subject |
| `test_validation.py` | incorrect numbers, invented dates, fabricated claims/entities, context expansion, hedge & qualifier preservation, garbage input, "not similarity-only" |
| `test_url_extraction.py` | URL validation, SSRF blocklist (loopback/metadata/private/CGNAT), trafilatura + BS4 fallback, redirects, timeouts, 404s, size caps, content-type, URL→claims end-to-end |
| `test_edge_cases.py` | LLM API failures, fallback on/off, invalid JSON (5 shapes), fenced/bare/repaired JSON, claim-type normalization, model contract, unicode/emoji/HTML input, 400-sentence article, Gemini/Ollama transports (mocked) |

All LLM calls in tests are mocked; no network or API key is required. No test asserts a fabricated expectation to force a pass — where behaviour surprised me, the code was fixed (e.g. a real bug where entity grounding was skipped when the entity appeared in the claim text, and a classification bug that labelled analyst expectations as fact).

**Performance:** typical article ≈ 0.2 s; 400-sentence article ≈ 5 s (the article context — sentence lemmas, numeric spans — is computed once and reused; a naive per-claim implementation was 137 s on the same input).

---

## 11. Known limitations

* Heuristic/MOCK mode does not paraphrase and can miss implications; entity quality for unusual domains depends on `en_core_web_sm`.
* Validation is lexical + rule-based by design (deterministic, debuggable). A heavily paraphrased but faithful claim may be rejected — thresholds are configurable in `.env` (`S1_LEXICAL_RECALL`, `S1_CHECK_*`). Prefer the LLM path, which is instructed to stay close to the article's wording.
* Coreference resolution covers common patterns (demonstratives, plural pronouns, `the company` / `the country`); long-range or ambiguous reference chains are left to the LLM.
* Only English is supported (model + marker lists).
