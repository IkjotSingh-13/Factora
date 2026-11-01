# PR: S3 + quote grounding + `/api/verify` + the UI — the MVP path closed end to end

Commits: `45734dd` + `da319ef` + the setup-and-polish commit, all on `main` locally and none
pushed yet — push directly or move them to a branch first:
`git branch -m verifyai/complete-mvp && git push -u origin verifyai/complete-mvp`
Base: `cbc4864` (merge PR #7, S2 retrieval + quote guard)
Suite: `python -m pytest -q` → **423 passed, exit 0** (306 pre-existing + 29 S3 + 26 quote-guard + 34 verify API + 7 QA-set + 11 Ollama wiring + 2 provider tests moved in from the repo root + 1 S2 bound test + 7 UI-serving contract tests)

---

## What was missing, and what this adds

Before this commit the repo had S1, S2 and the scorer, but **no S3 at all**, no verbatim
`article_quote` reaching the report, and **no frontend and no single endpoint to put it on**.
The plan's MVP steps 4–6 were unimplemented. This closes them:

| Piece | Purpose |
|---|---|
| `s3_analysis.py` | Stance assignment (`SUPPORTS`/`REFUTES`/`NEUTRAL`) + the substring quote guard + verbatim quote selection. Deterministic lexical analyser by default, optional LLM path (`S3_MODE=auto\|llm\|lexicon`) that falls back and *says* it fell back. Stdlib-only imports. |
| `backend/app/pipeline/article_quotes.py` | S1g: pins every claim to a verbatim sentence of the submitted article, strips reporting frames for checking (keeps them for display), flags time-sensitive claims, drops what it cannot ground. |
| `backend/app/pipeline/verify.py` + `POST /api/verify` | One request, whole pipeline, per-stage status/milliseconds, warnings, `live`/`offline`/`snapshot`/`auto` evidence modes, every external call failing soft. `merge_and_score()` is now shared with the orchestrator so there is one scoring path. |
| `frontend-app/` | The product UI: the `frontend` branch's React/TypeScript/Tailwind design (hero, claim rail, claim detail with C/R/A/T/K, evidence cards, audit tab), wired to the pipeline by `src/lib/api.ts` — the branch's scripted `verifyEngine.ts` is deleted. Served at `/` when built (`make build-ui`); its evidence cards name the quote's scope and its header banner says when a run is not live. |
| `frontend/index.html` | The zero-build fallback: one file, no build step, no CDN, no external request. Claim cards (article quote, direction gauge, strength gauge, verdict-in-words, C/R/A/T/K breakdown), evidence cards (link + verified quote + scope chip), insufficient/conflicting states, audit footer with 11 counters and per-stage timing. Served at `/lite`, and at `/` when the React build is absent. |
| `data/samples.json`, `GET /api/samples` | Five prepared inputs, each written to land on a specific honest outcome, and `expect` text so the demo can be narrated without improvising. |
| `data/test_set.json`, `scripts/run_test_set.py`, `docs/TEST_SET.md` | Labelled QA set + runner that records actual outcomes to `data/test_set_results.json`, checked by `tests/test_test_set.py` so the documented numbers cannot drift. |
| `EvidenceItem.quote_scope` | `full_text` \| `snippet` \| unset — makes "verified against page text" and "verified against the snippet only" different, checkable statements on the wire and on screen. |
| `docs/{ARCHITECTURE,SCORING,DEMO,TEST_SET,PROGRESS}.md`, `docs/slides.html`, README, `.env.example` | The hour 19–21 deliverables: architecture map, score derivation with a worked example from a real run, 3-minute demo script with a fallback ladder and rehearsal checklist, measured QA results, deck. |
| `scripts/bootstrap.sh` / `.ps1`, `Makefile`, `docs/SETUP.md` | Setup a reviewer can actually run: one command creates the venv, installs the three requirements files, gets the spaCy model, copies `.env`, runs the suite, and prints the next step. Idempotent, `--ollama` opt-in, no sudo, nothing written outside the repo. |
| `scripts/check_connections.py` | Per-dependency probe (Ollama server/model/inference, Gemini, Tavily, Fact Check, keyless sources, the running API) with a fix line per problem, `--json` for CI, and it never prints a key. |
| `tests/test_ollama_wiring.py` | The LLM path asserted against a mock `/api/chat`: `analyzer=llm` reaches the audit, a hallucinated quote is dropped and the lexical answer takes its place, an unreachable server degrades instead of failing, and `LLM_PROVIDER=ollama` really selects `OllamaProvider`. |
| `docs/screens/*.png` | Four screenshots of the running UI (idle, results, one claim card, mobile), captured headlessly with zero console errors — evidence for the visual state, not a design mock. |

Deleted: `s2_extract/verifyai/**` (36 stale duplicate files) — same change as the unmerged
`mansimran/cleanup-s2extract` branch, so that branch can be closed.

---

## Two things reviewers keep asking about

**Why is the offline demo capped at MIXED?** Because every entry in `data/mock_corpus.json` is
tagged `source_kind=mock` and holds credibility **0.10 by design**. `C` is a 0.30-weight term, so
an all-mock run cannot reach a decisive verdict, and the response says so in a warning. Padding
those numbers would have made the demo look better and meant nothing. Use `live` (real
credibility 0.81–0.85 measured) or `snapshot` (labelled `demo_backup: true`).

**Why does a claim that mentions a thing not count as support?** `s3_analysis` separates a
*mention frame* from a *verification predicate*: an aligned sentence that only names the claim's
entities, without confirming or denying anything, is forced to `NEUTRAL`. Refuting additionally
requires either a scoped negation, a unit-matched number conflict, or a polarity mismatch at
`align ≥ 0.6`. This came out of the QA set: the first version called a comparison sentence
("taller than the Eiffel Tower") support for an unrelated height claim.

---

## Evidence, not claims

| Check | Result |
|---|---|
| `python -m pytest -q` | 423 passed, exit 0 |
| `python scripts/check_connections.py` | 4 ok / 4 absent / 0 failed on this machine (no keys, no Ollama here) |
| `python scripts/check_s3_contract.py --analyze s3_analysis:analyze --replay data/demo_snapshot.json` | "S3 is compatible with the S2 seam"; 10/10 items analysed; **0 fabricated quotes**, 0 empty-but-claimed |
| `python scripts/run_test_set.py` | offline: **9/9 directions met, 0 invariant violations**, 1 case recorded unmeasured |
| `python scripts/run_test_set.py --mode snapshot` | real captured sources: **5/9 met, 0 invariant violations** — four misses explained case by case in `docs/TEST_SET.md` |
| `node scripts/ui_render_check.mjs` | renders a captured real response: 6 claim cards, 3 evidence cards, audit footer; also drives a crafted payload to assert the entities join and the failed-source counter |
| `python scripts/doctor.py` | "Verdict: ready. Live retrieval needs network; replay needs nothing." |

Measured timings, same runs: offline ~35–50 ms warm (≈280 ms cold), snapshot 50–320 ms, live
4.3–4.5 s for a 5-claim article. `direction_met` is **not** an accuracy figure and no document in
this repo presents it as one.

## Not built (the plan's cut order, executed deliberately)

Cross-encoder reranking (hook present in `ranking.py`, off by default) · extra APIs and LLM query
rewrites · Qdrant/persistent store · bootstrap CIs · full-text for every result (`S2_FETCH_FULL_TEXT=3`).
Nothing on the protected list was cut: end-to-end demo, exact-quote guard, source links,
evidence/stance separation, transparent breakdown, insufficient-evidence state.

## Known limits, stated plainly

1. Lexical stance can still be fooled by a sentence that mentions a number in a different sense —
   `s3_audit.analyzer` records which analyser ran, on screen.
2. Snippet-scope verification is weaker than full text; both modes label themselves.
3. URL paste depends on the target site: measured `https://en.wikipedia.org/wiki/Eiffel_Tower`
   → `502 … trafilatura, 0 chars < 200 required`. The failure is loud and explains itself; pasted
   text is the demo path.
4. `data/test_set.json` has 10 cases — a regression net, not a benchmark.

## Merge notes

- `backend/app/models.py` gained exactly one optional field (`quote_scope`); `tests/vendor/app_models_snapshot.py`
  was regenerated to match. No field was renamed, so S1/S2/S3 code paths are untouched.
- `POST /api/score` and `POST /api/extract` are unchanged and still covered by their own tests.
- The vendored-models test compares `models.py` structurally, so any future contract change has
  to regenerate `tests/vendor/app_models_snapshot.py` deliberately — which is the point.
- After merging, no env var is required to run the demo; `.env.example` documents `S3_MODE`,
  `S3_LLM_TIMEOUT`, `VERIFY_MODE`, `VERIFY_OFFLINE`, `VERIFY_SNAPSHOT_ONLY`, `VERIFY_SNAPSHOT_PATH`,
  `S2_FETCH_FULL_TEXT`.
