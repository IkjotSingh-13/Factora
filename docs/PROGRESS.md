# VerifyAI / Factora — status against the 24-hour plan

Audited at commit `cbc4864` (merge of PR #7, `mansimran/retrieval_and_evidence`), then
finished on top of it in the working tree described below. Method for both halves: read every
module, run the whole suite, and check each plan deliverable against the code that actually
exists — not against what a document claims.

## Where each plan item stands

| Plan item | Owner | Status | Evidence |
|---|---|---|---|
| Repo skeleton, shared schema, MOCK mode | Lead | **done** | `backend/app/models.py`, `contracts.py`, `MockProvider` in `backend/llm_provider.py` |
| S1 claim extraction (3–6 atomic claims, filters, dedupe) | Ikjot | **done** | `backend/s1_claims.py` + `validators.py`, `tests/test_claim_extraction.py` (28 tests) |
| S1 exact-quote grounding (`article_quote` per claim) | S3/S4 glue | **done** | `backend/app/pipeline/article_quotes.py` — alignment-scored, relocated-when-needed, ungroundable claims dropped; 26 tests |
| S2 retrieval: 3 sources, normalise, rank, cache | Mansimran | **done** | `s2_retrieval.py`, `sources.py`, `ranking.py`, `cache.py`, `fulltext.py`; `tests/test_s2.py` (76 tests) |
| S2 full-text fetch + quote guard | Mansimran | **done, bounded** | `fulltext.py`; `S2_FETCH_FULL_TEXT` caps page fetches per claim (default 3), and `quote_scope` records what was actually checked |
| Demo snapshot (network-independent fallback) | Mansimran | **done** | `data/demo_snapshot.json`, `snapshot.py`, `scripts/warm_snapshot.py`; `/api/verify mode=snapshot` labels it `demo_backup: true` |
| **S3 stance analysis + quote verification** | **S3** | **done** | `s3_analysis.py` — deterministic lexical analyser + optional LLM path, `verify_quote`, negation-scope and number-conflict rules, `ALIGN`-gated stances; 29 tests |
| S3 ↔ S2 seam gate | Mansimran | **done, passing** | `python scripts/check_s3_contract.py --analyze s3_analysis:analyze --replay data/demo_snapshot.json` → "S3 is compatible with the S2 seam", 10/10 items analysed, 0 fabricated quotes |
| S4 transparent scoring (`0.30C+0.30R+0.20A+0.10T+0.10K`) | Lead | **done** | `backend/app/pipeline/s4_scoring.py`, derived in `docs/SCORING.md`; `tests/test_scoring.py` |
| Direction separated from strength | Lead | **done** | `stance_score` vs `evidence_strength`, two gauges in the UI, `shrinkage_factor` visible |
| Insufficient-evidence state | Lead | **done** | `Verdict.INSUFFICIENT_EVIDENCE` + `n_independent < 2 or strength < 0.45` rule, explanatory note in the UI |
| **S5 one-request endpoint for the UI** | **Orchestrator** | **done** | `POST /api/verify` (`backend/app/pipeline/verify.py`) — S1 → grounding → S2 → S3 → S4/S5 in one call, per-stage `stages[]`, `warnings[]`, three evidence modes |
| Frontend: claims, evidence, score breakdown, audit trail | Frontend | **done** | `frontend/index.html`, one zero-build file; `node scripts/ui_render_check.mjs` renders a real response (6 claim cards, 3 evidence cards, audit footer) |
| UI shows quote scope honestly (full text vs snippet vs unverified) | Frontend | **done** | `quote_scope` added to the contract + chip on every evidence card |
| Sample inputs for the demo | QA | **done** | `data/samples.json` (5 samples, each written to land on a specific honest outcome), served by `GET /api/samples` |
| **Labelled QA test set + recorded results** | **QA** | **done** | `data/test_set.json` (10 cases), `scripts/run_test_set.py`, `data/test_set_results.json`, `docs/TEST_SET.md`; guarded by `tests/test_test_set.py` |
| README / demo script / scoring doc | all | **done** | `README.md`, `docs/DEMO.md` (+ rehearsal checklist), `docs/SCORING.md`, `docs/TEST_SET.md`, `.env.example` (S3 + verify vars documented) |
| Architecture doc + slides (hour 19–21 deliverable) | all | **done** | `docs/ARCHITECTURE.md`; `docs/slides.html` (9 slides, speaker notes, zero-build; navigation verified by executing its own script) |
| Failed sources + entities visible in the UI | Frontend | **done** | audit footer row `sources that failed`, entities chip joined from `grounding.claims`; both asserted by `scripts/ui_render_check.mjs` on a crafted payload |
| Stale duplicate tree removed | all | **done** | `s2_extract/verifyai/**` (36 files) deleted here — the same change as the unmerged `mansimran/cleanup-s2extract` branch |
| One-command setup (venv, deps, model, `.env`, suite) | Lead | **done, executed** | `scripts/bootstrap.sh` + `scripts/bootstrap.ps1`, `make install`; run end to end on a clean copy of this tree → `BOOTSTRAP_EXIT=0`, then 415 tests pass in that venv |
| Connectivity diagnostics for keys / Ollama / sources | Lead | **done** | `scripts/check_connections.py` (ok / degraded / absent / fail, prints no secrets), `make connections`; documented in `docs/SETUP.md` |
| Ollama path proven, not assumed | Lead | **done** | `tests/test_ollama_wiring.py` (11 tests against a mock `/api/chat`) + `tests/test_ollama_provider.py`; asserts `analyzer=llm`, hallucinated quote dropped, unreachable server → lexical fallback, `LLM_PROVIDER=ollama` really selects `OllamaProvider` |
| Frontend polish, verified in a browser | Frontend | **done** | `frontend/index.html` rewritten on a token system; headless-Chromium pass = 0 console errors, 0 horizontal overflow at 1440/390 px, no WCAG contrast failure in the palette, 2 px focus ring; `docs/screens/` are screenshots of that run |

## Cut list — what we deliberately did *not* build

Per the plan's "if behind, cut in this order", these are out and the tool still satisfies the
problem statement. They are listed so a judge can be told the truth instead of being shown a
half-finished feature:

- cross-encoder reranking (`ranking.SentenceTransformerBackend` exists, is not on by default);
- extra APIs beyond Fact Check / Tavily / OpenAlex;
- Qdrant vector store (nothing persists between runs except the disk cache);
- bootstrap confidence intervals around the score — instead the score is exact arithmetic;
- full-text fetch for *every* result (bounded by `S2_FETCH_FULL_TEXT`, so some quotes are
  verified against the snippet, and the UI says which).

## Measured state of this tree

```
python -m pytest -q            → 415 tests (306 pre-existing + 29 S3 + 26 quote-guard + 34 verify API + 7 QA-set + 11 Ollama wiring + 2 provider tests moved in from the repo root)
   (all offline; no key, no model server, no network)
scripts/check_s3_contract.py   → compatible, exit 0, 10/10 analysed, 0 fabricated quotes
scripts/run_test_set.py        → offline 9/9 directions met, 0 invariant violations, 1 unmeasured
scripts/run_test_set.py snapshot → 5/9 met, 0 violations, 1 unmeasured
scripts/check_connections.py   → 4 ok, 4 absent (no keys, no Ollama here), 0 failed
scripts/bootstrap.sh (clean copy) → exit 0; that venv then passes the whole suite
node scripts/ui_render_check.mjs → passed
headless Chromium (load, each sample, run in each mode, empty input, URL tab) → 0 errors
```

Timings from the same runs: offline ~35–50 ms warm (~280 ms cold), snapshot ~50–320 ms,
live ~4.3–4.5 s for a 5-claim article (19 items, 6 domains, 9 quote-verified, 6 with page
text). URL paste against a site that blocks the fetcher returns HTTP 502 with the extractor's
own reason — measured against Wikipedia, which returned 0 usable characters.

## Limits worth knowing before you extend this

1. **Lexical stance analysis is a real constraint, not a footnote.** It never invents a quote,
   but it can also be fooled by a sentence that *mentions* a number. `docs/TEST_SET.md` records
   two snapshot cases where that cost us the direction. Fixing this is an NLI model or LLM
   prompt-quality problem, and `s3_audit.analyzer` is on screen so nobody mistakes the fallback
   for the model.
2. **Offline verdicts cap out around MIXED/CONFLICTING by design.** Every `data/mock_corpus.json`
   entry is `source_kind=mock` at credibility 0.10, so `C` cannot lift the score. Inflating
   that number would have made the demo look better and meant nothing; instead the run carries
   a warning saying what it is.
3. **Snippet-scope verification is weaker than full text** and is labelled as such everywhere
   (`quote_scope`, the audit warning, the seam checker). Live results therefore depend on which
   pages answer.
4. **`INSUFFICIENT EVIDENCE` is common** for short inputs with one number in them. That is the
   two-independent-source rule working, and the UI explains the rule on the card itself.
5. `data/test_set.json` has 10 cases. It is a regression net and a demo asset, not a benchmark;
   the plan's "do not claim an accuracy figure that was not measured" is honoured in
   `docs/TEST_SET.md`, which quotes measured counts and refuses to convert them into accuracy.
