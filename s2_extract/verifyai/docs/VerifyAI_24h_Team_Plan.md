# VerifyAI — 24-Hour Hackathon Plan (4 Members)

**Problem 21:** Extract factual claims from online content, retrieve credible evidence, compare sources, and show a transparent assessment—not an unsupported AI verdict.

**Team:** You, Ikjot, Mansimran, Taranpreet  
**Core pitch:** “We show what the evidence says, where it came from, and how the assessment was calculated. If evidence is thin or conflicting, we say so.”

---

## 1. Recommended MVP

Build one reliable end-to-end path before adding advanced features:

1. User pastes article text or enters a URL.
2. Extract **3–6 atomic, checkable claims** and retain the exact article quote for each.
3. Retrieve evidence from **2–3 sources**; normalize and rank the results.
4. Label each evidence item **SUPPORTS / REFUTES / NEUTRAL** and verify that any displayed quote really appears in the source text.
5. Calculate a transparent evidence-strength score, show the direction of evidence separately, and provide source links.
6. Display an audit trail and a clear **“insufficient evidence”** state when the evidence is weak.

**Keep the first version small:** use a single-page UI or Streamlit, an in-memory corpus, and a few source adapters. Add Qdrant, a cross-encoder, many APIs, and bootstrap confidence intervals only if the core demo works early.

---

## 2. Roles and ownership

| Member | Primary role | Owns | Definition of done |
|---|---|---|---|
| **You — Team Lead, API & Scoring** | Backend integration and transparent scoring | Freeze the Pydantic/API contract; FastAPI `/api/verify`; `s4_scoring.py`; `s5_report.py`; integration and demo fallback | A mocked report works at the start; later the API accepts real pipeline outputs and returns stance, score breakdown, evidence, and audit fields. |
| **Ikjot — NLP / Claim & Evidence Analysis** | Claim extraction and stance analysis (S1 + S3) | `s1_claims.py`, `s3_analysis.py`, structured LLM/NLI calls, claim filters, exact-quote guard | Produces checkable claims with article quotes; assigns support/refute/neutral to evidence; drops or flags any quote not verified in source text. |
| **Mansimran — Retrieval & Evidence Ranking** | Search, source connectors, and ranking (S2) | `sources.py`, `s2_retrieval.py`, timeouts/retries/cache, result normalization, relevance ranking | Returns a ranked list of normalized evidence items with URLs, domains, dates, text/snippets, source type, and relevance. |
| **Taranpreet — Frontend, QA & Demo** | User experience and demo readiness | UI; loading/error states; evidence cards and links; test cases; demo script and backup | UI works against mock JSON first, then the real API; shows evidence and score explanations clearly and never presents a score as a guaranteed truth probability. |

### Shared handoff contract — freeze by hour 1

Agree on these objects before building in parallel. Keep field names stable after the first integration:

- **Claim:** `id`, `text`, `type`, `article_quote`, `time_sensitive`, `entities`
- **Evidence:** `claim_id`, `url`, `domain`, `title`, `text_or_snippet`, `published_at`, `source_kind`, `relevance`
- **Analysis:** `stance`, `quote`, `quote_verified`, `rationale`, `context_gap`
- **Claim report:** `claim`, `verdict`, `stance_direction`, `evidence_strength`, `scores`, `evidence[]`, `audit`

You own the shared schema; each other member owns their module and returns the agreed object. Taranpreet should build against sample/mock JSON immediately rather than wait for the pipeline.

---

## 3. Responsibilities in more detail

### You — API, integration, scoring and report
- Create the repo skeleton and API contract; keep one working `/api/verify` endpoint from the beginning.
- Add a **MOCK mode** so the UI and demo can run even if a local model or search API fails.
- Implement a readable, deterministic score with component values and an explanation for each. Suggested terms from the plan:
  - **C:** source credibility
  - **R:** evidence relevance
  - **A:** independent agreement / source diversity
  - **T:** freshness
  - **K:** contextual consistency
  - `S = 0.30C + 0.30R + 0.20A + 0.10T + 0.10K`
- Keep **evidence direction** (support vs. refute) separate from **evidence strength** (`S`). A claim can have strong evidence that refutes it.
- Return scores, contributing values, evidence, failed sources, quote-drop count, and disclaimer in the report.
- Integrate modules at checkpoints; keep a demo-safe cached result available.

### Ikjot — extraction and evidence analysis
- Start with pasted text; add URL article extraction if time allows.
- Extract at most 3–6 atomic factual claims; exclude opinions and predictions.
- Keep a verbatim quote from the submitted article for each claim; reject claims whose quote is not present in the article.
- For each retrieved passage, return `SUPPORTS`, `REFUTES`, or `NEUTRAL`, plus a short rationale and exact source quote.
- Implement the **substring quote guard**: normalize whitespace/case; if the quote cannot be matched to the fetched text, drop it and increment an audit counter.
- If model inference is slow or unavailable, use a documented fallback or MOCK mode; never invent a stance or quote to make the demo look complete.

### Mansimran — retrieval and evidence ranking
- Start with two reliable, low-friction sources; add another only after those work. For example: Wikipedia, DuckDuckGo/Tavily, and Google Fact Check (if the key is ready); OpenAlex can be an academic-source option.
- Always include a literal keyword query; treat LLM-generated query rewrites as optional.
- Add reasonable timeouts, retries, and caching; a failed source should return an empty result, not fail verification.
- Normalize all results to the shared `Evidence` object.
- Rank with semantic similarity plus BM25/RRF if practical; deduplicate/cap repeated domains. Full-text fetching and a cross-encoder are stretch items.
- Record which sources were queried, how many results were retrieved, and which were used.

### Taranpreet — UI, QA and demo
- Begin with mock JSON from hour 1. Show: input, progress/loading state, claim cards, stance, evidence-strength score, a short score breakdown, citations, and an audit footer.
- Make source links easy to open; show the verified quote beside its source.
- Add empty, error, and “insufficient evidence” states. Avoid a big unsupported TRUE/FALSE badge.
- Prepare a small test set with known supported, refuted, and misleading/context-sensitive claims. Record actual test results; do not claim an accuracy figure that was not measured.
- Own the 3-minute demo flow and an offline/cached backup.

---

## 4. 24-hour execution timeline

| Time | You | Ikjot | Mansimran | Taranpreet | Checkpoint |
|---|---|---|---|---|---|
| **0–1 h** | Create repo, API skeleton, shared schema and MOCK mode | Check model/local inference; agree on claim and analysis fields | Choose initial source adapters and result fields | Choose UI approach; get sample JSON | Everyone can run the project; contract is frozen. Start model downloads early if using local models. |
| **1–3 h** | `/api/verify` returns sample report | Basic extraction and claim schema | One source adapter returns normalized evidence | UI renders sample report | A judge could click through a mocked end-to-end result. |
| **3–6 h** | Scoring/report skeleton | Real claim extraction, filters, article quote guard | Two sources, timeouts and cache | Input, loading, claim and evidence layout | Paste text or URL → real claims; UI still works with mocks. |
| **6–9 h** | Wire pipeline contracts; add error handling | Stance labels, rationales and source quote guard | Query, retrieve, rank, deduplicate | Connect UI to API; show citations | Claims → ranked evidence → analysis, at least on one example. |
| **9–12 h** | Finish score components and report/audit | Test edge cases and neutral/disputed evidence | Add a third source or better ranking only if stable | Show score breakdown and “insufficient evidence” state | Full vertical slice works on one supported and one refuted example. |
| **12–14 h** | Lead integration; freeze features | Fix highest-impact analysis/quote bugs | Fix source failures and caching | Fix visible UX blockers | **Feature freeze.** No major rewrites after this point. |
| **14–17 h** | Run score sanity checks; document formula | Test misleading/context-sensitive claims | Check duplicates, dates and source diversity | Run QA checklist; improve empty/error states | Test a small labelled set; capture real outcomes and limitations. |
| **17–19 h** | Ensure cached/MOCK demo path works | Help validate hard claims | Warm cache and verify offline fallback | Polish demo flow and prepare example inputs | Demo can continue if a model or API is unavailable. |
| **19–21 h** | README, architecture and score explanation | Help with technical explanation | Note source/API limitations | Prepare slides and 3-minute script | Slides explain problem → pipeline → transparency → test results. |
| **21–23 h** | Rehearse and keep a known-good build | Rehearse technical Q&A | Rehearse retrieval/evidence explanation | Lead demo rehearsal; save video/screenshots if possible | Rehearse the demo at least twice; use a backup article/result. |
| **23–24 h** | Buffer; avoid risky changes | Buffer | Buffer | Buffer | Fix only show-stoppers. |

---

## 5. Integration rules (to avoid last-minute merge problems)

1. **Integrate every 2–3 hours.** Do not wait until a module is “perfect.”
2. **Use mocks first.** The UI, API, and report should run before every model and API is live.
3. **Keep dependencies modest.** Do not add Qdrant or a complicated frontend build unless the MVP is already stable.
4. **Every external call fails soft:** timeout, catch the error, log the failed source, continue with the sources that worked.
5. **Never display an unverified quote.** If quote verification fails, drop it and report the count.
6. **Show uncertainty honestly.** A small number of sources should lead to “insufficient evidence,” not a confident verdict.
7. **No feature work after hour 14** unless fixing a blocker.

---

## 6. Cut list if the team falls behind

Cut in this order:

1. Cross-encoder reranking.
2. Extra APIs and advanced query generation.
3. Qdrant/vector database; use an in-memory session corpus.
4. Bootstrap confidence intervals and the full contextual-consistency rubric.
5. Full-text fetching for every result; fetch only the top few.

**Protect these:** working end-to-end demo, exact-quote guard, source links, evidence/stance separation, transparent score breakdown, and insufficient-evidence state.

---

## 7. Suggested 3-minute demo

1. **Hook (15 sec):** “We don’t ask an LLM to guess whether a whole article is true. We extract claims and show the evidence behind each assessment.”
2. **Run (45–60 sec):** Paste a prepared article. Show claim extraction and retrieval progress.
3. **Inspect (60 sec):** Open one claim; show a source quote, source/date, stance, and score components. Point out how the quote was verified.
4. **Show uncertainty (30 sec):** Demonstrate a conflicting or thin-evidence case and explain why the system does not force a verdict.
5. **Close (15 sec):** Explain the audit trail, source limitations, and what you actually tested.

**Important:** use prepared examples and a cached result as backup, but label cached output as a demo backup. Do not present unmeasured accuracy or fabricated evidence as real.
