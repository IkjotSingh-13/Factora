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
| **Product UI = the `frontend` branch's design** | **Frontend** | **done** | vendored as `frontend-app/` (Vite/React/TS/Tailwind), served at `/`; `src/lib/api.ts` maps `POST /api/verify` onto its `Report` schema and the branch's `src/lib/verifyEngine.ts` (scripted quotes, invented sources, hard-coded scores) is deleted. `frontend/index.html` stays at `/lite` as the zero-build fallback |
| UI shows quote scope honestly (full text vs snippet vs unverified) | Frontend | **done** | `quote_scope` added to the contract + chip on every evidence card |
| Sample inputs for the demo | QA | **done** | `data/samples.json` (5 samples, each written to land on a specific honest outcome), served by `GET /api/samples` |
| **Labelled QA test set + recorded results** | **QA** | **done** | `data/test_set.json` (10 cases), `scripts/run_test_set.py`, `data/test_set_results.json`, `docs/TEST_SET.md`; guarded by `tests/test_test_set.py` |
| README / demo script / scoring doc | all | **done** | `README.md`, `docs/DEMO.md` (+ rehearsal checklist), `docs/SCORING.md`, `docs/TEST_SET.md`, `.env.example` (S3 + verify vars documented) |
| Architecture doc + slides (hour 19–21 deliverable) | all | **done** | `docs/ARCHITECTURE.md`; `docs/slides.html` (9 slides, speaker notes, zero-build; navigation verified by executing its own script) |
| Failed sources + entities visible in the UI | Frontend | **done** | audit footer row `sources that failed`, entities chip joined from `grounding.claims`; both asserted by `scripts/ui_render_check.mjs` on a crafted payload |
| Stale duplicate tree removed | all | **done** | `s2_extract/verifyai/**` (36 files) deleted here — the same change as the unmerged `mansimran/cleanup-s2extract` branch |
| One-command setup (venv, deps, model, `.env`, suite) | Lead | **done, executed** | `scripts/bootstrap.sh` + `scripts/bootstrap.ps1`, `make install`; run end to end on a clean copy of this tree → `BOOTSTRAP_EXIT=0`, then the whole suite passes in that venv (416 then, 423 as of the UI adoption) |
| Connectivity diagnostics for keys / Ollama / sources | Lead | **done** | `scripts/check_connections.py` (ok / degraded / absent / fail, prints no secrets), `make connections`; documented in `docs/SETUP.md` |
| Ollama path proven, not assumed | Lead | **done** | `tests/test_ollama_wiring.py` (11 tests against a mock `/api/chat`) + `tests/test_ollama_provider.py`; asserts `analyzer=llm`, hallucinated quote dropped, unreachable server → lexical fallback, `LLM_PROVIDER=ollama` really selects `OllamaProvider` |
| Frontend polish, verified in a browser | Frontend | **done** | `frontend/index.html` rewritten on a token system, and the vendored React app re-verified the same way: headless-Chromium pass = 0 console errors, **0 requests to any host but localhost**, 0 horizontal overflow at 1440/390 px, no WCAG contrast failure in the palette, 2 px focus ring; `docs/screens/` are screenshots of the React UI at those widths |

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

## Two environment-sensitive things found by running the installer for real

1. **`bootstrap.sh` could not survive a half-built `.venv`.** If `python -m venv` is
   interrupted (a full disk does exactly this), the directory exists, activation appears to
   succeed, and `python` silently resolves to the *system* interpreter — the next `pip
   install` then rewrites someone's global site-packages. The script now checks that a
   `.venv` is usable (`bin/python` **and** `pyvenv.cfg`), rebuilds it when it is not, and
   refuses to install unless `sys.prefix != sys.base_prefix`. Each of the three guards was
   exercised: rebuild path, "could not activate" exit, and "refusing to install into the
   system interpreter" exit — the last two stop before pip is ever invoked.
2. **`tests/test_s2.py::test_slow_source_is_abandoned_at_the_deadline` measured the wrong
   stage once `trafilatura` was actually installed.** The engine's source deadline fires at
   0.5 s as designed, but the run then entered its separately bounded page-text stage and
   spent `fulltext_deadline_s` (4 s) trying to resolve the stub's fake `enc.example` URL —
   so `elapsed` was 4.5 s against a 1.5 s assertion. The test now disables fetching
   (`fetch_full_text=0`), which is what it means to test, and a new test asserts the
   page-text stage is bounded by its own budget and leaves `text_scope` honest. Verified by
   mutation: ignoring `fulltext_deadline_s` in `s2_retrieval.py` fails that new test, and
   restoring it makes the suite pass (423 tests as of this writing).

## Adopting the frontend branch's UI as the product UI

Taranpreet's `origin/frontend` branch (`dc2fcf3`, orphan — no merge base, 36 files) is a
Vite/React/TypeScript/Tailwind app that **never called a backend**: no `fetch`, no `/api`, no
axios anywhere in it. Adopting it meant keeping the design and replacing the data path.

**What it took, and why each piece is the way it is:**

1. `frontend-app/src/lib/api.ts` (new) is the only place the UI touches the pipeline. It posts
   `{article_text | url, mode}` to `/api/verify`, and maps the response onto the branch's
   `Report` type field by field — every number, label and link on screen is read out of the
   response (`scores`, `contributions`, `audit`, `retrieval_summary`, `s3_audit.analyzer`), never
   computed in the browser. `quote_verified` decides whether a quote is shown at all; the
   weights (0.30/0.30/0.20/0.10/0.10) appear only as the formula string they are, not as a result.
2. `src/lib/verifyEngine.ts` (deleted, `git rm`) had invented `en.wikipedia.org/wiki/…` and
   `reuters.com/fact-check/…` URLs, marked sliced sentences `quote_verified: true`, hard-coded
   `C = 0.92 / R = 0.89`, and played 1.4 s of `setTimeout` "progress" theatre. A judge finding
   that in a tool whose whole claim is quote verification is the worst failure mode this project
   has, so it is gone and `tests/test_ui_serving.py` fails if it returns.
3. `VerifyInput.tsx` now awaits the real call, validates non-empty input and URL shape client
   side, counts real elapsed seconds (`performance.now` + interval, cleaned up on unmount), and
   carries an evidence-mode pill row `auto | live | offline | snapshot` with a one-line
   explanation of what the chosen mode means for what follows.
4. `ReportHeader.tsx` gained the banner the branch never had: unless `mode === "live"` it prints
   the demo label and why (`OFFLINE RUN AGAINST THE LOCAL DEMO CORPUS - mode=offline. No live
   source was queried…`). Grep of the branch first showed only `ReportView.tsx:177` mentioning
   `mode === "mock"`, in a string no one could read as a caveat.
5. Four components got honesty edits, all of them "say what the data says, not more":
   `AuditView.tsx` quote-guard column now reports `Verified (n/m)` / `No evidence` / `No quote
   matched` per claim instead of a fixed green *Verified*; `ClaimDetail.tsx` header reads
   `N of M quotes matched verbatim` under **Evidence Passages Retrieved** (retrieved evidence is
   not automatically corroborating, and refuting evidence is certainly not); the source modal
   names the *scope* of the match (`…in the fetched page` vs `…in the retrieved snippet, not the
   page`); and a missing date says `date unknown` where the branch printed `Historical record`.
   `ReportHeader.tsx` also counts the claims the guard refused to score (`3 Insufficient`), so
   the chips add up to the claim count.
6. `index.html` had four remote references (Google Fonts preconnect + stylesheet, two
   `db.onlinewebfonts.com` stylesheets) despite the branch README claiming vendored assets, and
   `DistillHero.tsx` hot-linked six `cloudfront.net` webp thumbnails. Fonts were already vendored
   into `src/assets/fonts`; the six thumbnails were downloaded into `public/hero/` and the
   hot-links removed, so the page makes zero external requests (measured: `external hosts: NONE`).
   `vite.config.ts` got a dev proxy for `/api` → `127.0.0.1:8000` so `npm run dev` works too.
7. The hero's decorative audit cards asserted `quote_guard substring_verified="true"`,
   `status="VERIFIED"` against real-looking domains and `100% AUDITED EVIDENCE`. They are mock-ups
   of the product, but on a screen next to the demo they read as results, so each now says
   `DEMO FIXTURE - NOT A LIVE RUN` and the source rows read `status="DEMO FIXTURE - not fetched"`.
   `Analysis.quote_scope` was added to their own `types.ts` (optional, documented) rather than
   overloading `quote_mode`, which in their schema means "the quote needed insertions", not
   "we only hold a snippet".
8. `backend/app/main.py` mounts `frontend-app/dist` at `/` when it exists, `frontend/` at
   `/lite` (registration order matters: `/lite` first, and all `/api/*` routes before both), and
   `/api/config` grew a `ui` field saying which one you are looking at. `make build-ui`,
   `./scripts/bootstrap.sh --ui` and `bootstrap.ps1 -UI` do the npm step; all three skip with a
   reason when node is absent, because `/lite` needs nothing.
9. `scripts/ui_render_check.mjs` now checks both frontends: the zero-build file still renders a
   real captured response, and the React app is asserted on source — that it calls `/api/verify`,
   that the scripted engine stays deleted, that the banner exists, that nothing hot-links, and
   that every reference in a built `dist/index.html` resolves to a file that is on disk.

**Two test bugs the adoption exposed, both real:**

- `test_verify_api.py::test_ui_is_served_at_the_root` asserted the *string* `VerifyAI` was in
  `/`, which was true only while `/` served the single-file UI. It now asserts the contract —
  an HTML document, the React shell's `id="root"` or the fallback, and never a directory listing
  — and the mount choice itself is pinned by the new `tests/test_ui_serving.py` (7 tests).
- `test_s2.py::test_parallel_claims_preserve_order_and_cut_wall_clock` was load-sensitive and
  started failing (ratio 0.78 against a 0.75 bound). Root cause was not the machine: the stub's
  fake `echo.example` URLs sent the engine's page-text stage into DNS lookups plus its 4 s tail,
  which landed on **both** sides of the comparison. Same class as the `test_slow_source…` fix
  above, so it got the same treatment (`fetch_full_text=0`) plus best-of-N timing, and the
  measured ratio is now 0.25 stable across runs (3× pass in a row, and 4.4 s → 1.6 s per
  measurement). Mutation-verified: running the "parallel" side serially fails with `ratio=1.00`.

## Measured state of this tree

```
python -m pytest -q            → 423 tests (306 pre-existing + 1 new S2 bound test + 29 S3 + 26 quote-guard + 34 verify API + 7 QA-set + 11 Ollama wiring + 2 provider tests moved in from the repo root + 7 UI-serving contract tests)
   (all offline; no key, no model server, no network)
scripts/check_s3_contract.py   → compatible, exit 0, 10/10 analysed, 0 fabricated quotes
scripts/run_test_set.py        → offline 9/9 directions met, 0 invariant violations, 1 unmeasured
scripts/run_test_set.py snapshot → 5/9 met, 0 violations, 1 unmeasured
scripts/check_connections.py   → 4 ok, 4 absent (no keys, no Ollama here), 0 failed
scripts/bootstrap.sh (clean copy) → exit 0; that venv then passes the whole suite
node scripts/ui_render_check.mjs → passed (zero-build UI renders; React app wired to /api/verify,
  scripted engine absent, no hot-links, every dist reference on disk)
npm run build (frontend-app)     → tsc clean, dist = 0.81 kB html + 276.6 kB js (82.2 kB gzip) + 36.5 kB css
headless Chromium, React UI at / (load, live run, offline run, evidence modal, audit tab,
  fixture toggle, 390 px)         → 0 console errors, 0 non-localhost requests, 0 horizontal overflow
headless Chromium, /lite (load, each sample, run in each mode, empty input, URL tab) → 0 errors
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
