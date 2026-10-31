# VerifyAI architecture

What this is: the map a new teammate (or a judge in a Q&A) needs to find a thing in 30
seconds, plus the rules each stage may not break. The scoring arithmetic lives in
`docs/SCORING.md`; the field-by-field contract lives in `docs/CONTRACT.md`. This file covers
how the pieces connect and what happens when they fail.

## The one request

```
                 POST /api/verify  { article_text | url, mode, as_of, max_claims }
                                     │
   ┌─────────────────────────────────┼──────────────────────────────────────────┐
   │ S1  extraction        backend/s1_claims.py (+ article_extractor.py,        │
   │                       validators.py, llm_provider.py)                      │
   │   text or URL → 3–6 atomic claims (≤6 enforced), opinions/predictions      │
   │   removed, deduped; each claim keeps the sentence it came from             │
   │                              │                                             │
   │ S1g grounding     backend/app/pipeline/article_quotes.py                   │
   │   claim → verbatim sentence of the SUBMITTED text (alignment-scored,       │
   │   relocated if S1 reworded it); reporting frame stripped for checking and │
   │   kept for display; time-sensitive flagged; ungroundable claim DROPPED     │
   │                              │                                             │
   │ S2  retrieval              s2_retrieval.py + sources.py, ranking.py,       │
   │                            cache.py, fulltext.py, snapshot.py              │
   │   query built from assertion + entities → Google Fact Check / Tavily /     │
   │   OpenAlex / local corpus (mode-dependent) → normalise → rank/dedupe →     │
   │   optional page fetch for top-N → ClaimBundle per claim                    │
   │                              │                                             │
   │ S3  stance + quote guard          s3_analysis.py                            │
   │   align claim↔passage → SUPPORTS/REFUTES/NEUTRAL + exact quote +           │
   │   rationale + context_gap + confidence; quote must appear verbatim in the  │
   │   retrieved text or it is dropped and counted                             │
   │                              │                                             │
   │ S4/S5  score + report      backend/app/pipeline/s4_scoring.py, s5_report.py │
   │   only quote_verified evidence is scored; C/R/A/T/K → score_raw →          │
   │   shrinkage by n_independent/3 → verdict; direction kept separate          │
   └─────────────────────────────┼─────────────────────────────────────────────┘
                                 │
        VerifyResponse { report, stages[], s1, grounding, s3_audit,
                         retrieval_summary, per_claim_retrieval,
                         warnings[], demo_backup, note }
                                 │
                     frontend/index.html (StaticFiles at "/")
```

Everything between the brackets is one Python process. There is no queue, no worker, no
database and no build step, on purpose: the plan's rule was modest dependencies and one
reliable path.

## Seams, and who owns what

| Seam | Object | Owner | Enforced by |
| --- | --- | --- | --- |
| S1 → S1g | `Claim(id, text, entities, claim_type)` | Ikjot | `tests/test_claim_extraction.py`, `tests/test_s1_api.py` |
| S1g → S2 | `to_claim_input` / `to_claim_bundle` (`contract_adapter.py`) | glue | `scripts/contract_check.py`, `docs/CONTRACT.md` |
| S2 → S3 | `ClaimBundle{claim, evidence[], retrieval_audit, extras}` | Mansimran | `scripts/check_s3_contract.py` |
| S3 → S4 | `EvidenceItem.stance/quote/quote_verified/quote_scope/rationale/context_gap` | Ikjot | `scripts/check_s3_contract.py`, `tests/test_s3_analysis.py` |
| S4 → UI | `VerificationReport` (`backend/app/models.py`) | lead | `tests/test_verify_api.py`, `node scripts/ui_render_check.mjs` |

`backend/app/models.py` is the frozen shared schema. Two deviations from the plan sketch are
documented rather than silent: `text_or_snippet` became `snippet` + `full_text` (S2 gets API
snippets, and full text only when fetched), and the report's direction number is called
`stance_score` rather than `stance_direction` because it is signed, not an enum.

`entities` is the one plan-contract field with no home in the scoring schema: `ClaimInput`
does not define it on purpose, so a rename could not silently change what gets scored.
Entities are used where they help (building the retrieval query) and are carried for the human
in `grounding.claims[].entities`, which is what the claim card renders. `scripts/ui_render_check.mjs`
asserts that join in a crafted payload, so the field cannot disappear unnoticed.

## The invariant that shapes everything

**An unverified quote must never be shown or scored.** It is enforced three times, on purpose:

1. `s3_analysis.verify_quote()` — normalises whitespace/case, requires containment in the text
   that was retrieved; on failure the quote is emptied, `dropped_quote` is set, and the stance
   is forced to NEUTRAL.
2. `s4_scoring.score_claim()` — `used = [i for i in evidence if i.quote_verified and i.quote]`,
   and the remainder is counted in `audit.hallucinated_quotes_dropped`. A stage-3 bug cannot
   smuggle a quote into the score.
3. `article_quotes.ground_claims()` — the same idea on the other side: a *claim* must be
   verbatim in the article the user submitted, or it is not verified at all.

`quote_scope` (`full_text` | `snippet` | unset) records what "verified" meant for each item,
and every surface (API warning, evidence card chip, seam checker) repeats it. Snippet-scope
verification is weaker, and saying so is cheaper than being caught saying otherwise.

## Failure policy

Nothing outside the process is trusted to work.

| Failure | What happens | Where you see it |
| --- | --- | --- |
| LLM provider missing/slow/bad JSON (S1) | deterministic heuristic extractor | `s1.used_fallback`, `warnings[]` |
| LLM provider missing/slow/bad JSON (S3) | lexical analyser; a rejected LLM answer is relabelled | `s3_audit.analyzer`, per-item `analyzer` |
| A source errors, times out, or returns nothing | that source contributes nothing; others continue | `retrieval.sources_failed`, `failed_source_names`, audit footer |
| Page fetch fails | quote verified against snippet only (or dropped) | `snippet_only_items`, `full_text_failed` |
| Every source empty | report still produced, all claims INSUFFICIENT | warning "no source returned usable evidence for any claim" |
| Analyzer raises mid-run | claim falls back to unassessed, request still returns | `stages[].status: degraded` |
| URL unreachable / blocked / non-public | `400`/`502` with the extractor's own reason | `detail` |
| Empty or oversized input | `422` from the request model | validation error |

`max_claims` is capped at 6 by the model, the SSRF guard in `backend/article_extractor.py`
resolves the hostname *before* connecting and refuses private/link-local/metadata addresses
(and `.local`/`.internal`-style suffixes), and downloads are bounded by `S1_MAX_DOWNLOAD_BYTES`
and `S1_MAX_REDIRECTS`. The UI sends only relative `/api/*` requests, escapes every string it
interpolates (`esc()`), and loads nothing from a CDN — so the page works on a plane and cannot
be made to fetch a third-party script by pasting content.

## Evidence modes

```
mode=auto      → live, unless VERIFY_MODE / VERIFY_OFFLINE / VERIFY_SNAPSHOT_ONLY say otherwise
mode=live      → real adapters; fetch_full_text = S2_FETCH_FULL_TEXT (default 3 per claim)
mode=offline   → data/mock_corpus.json only; every item source_kind=mock at credibility 0.10
mode=snapshot  → replay data/demo_snapshot.json; response carries demo_backup=true
```

`auto` never silently substitutes the snapshot — a demo that quietly replays a captured file
and looks like live retrieval is the one failure mode this project cannot afford. Snapshot and
offline both print a warning naming themselves. The credibility of the mock corpus is *low by
design*; it is not padded so the demo looks better (see the warning in
`data/mock_corpus.json` handling in `verify.py`).

## State

* In memory: one request's bundles and scores. Nothing accumulates between requests.
* On disk: `S2_DISK_CACHE_DIR` (default `.cache/retrieval`), JSON per query with TTL
  `S2_CACHE_TTL_S`; the snapshot file; `data/test_set_results.json`, rewritten by the runner.
* Not present: a vector store, a DB, a user session. Qdrant was cut by the plan; the seam it
  would slot into already exists as a switch — `RetrievalConfig.offline` / `.mock` route
  retrieval to the local corpus loader, so a persistent corpus would be a third value there
  rather than a rewrite of the retrieval path.

## Extension points (each one is a seam, not a rewrite)

| Want | Touch |
| --- | --- |
| a new source | `sources.py` adapter returning normalized items + `CREDIBILITY_HINTS` entry |
| a trained stance model | `s3_analysis.analyze_item` — keep the returned dict keys; `scripts/check_s3_contract.py` gates it |
| cross-encoder reranking | `ranking.py` backend list (`SentenceTransformerBackend` is already there, unused by default) |
| bootstrap CIs on the score | `s4_scoring.score_claim` — add a field; the UI renders any extra keys in `scores` |
| full text for every item | raise `S2_FETCH_FULL_TEXT`; nothing else changes |
| more QA cases | append to `data/test_set.json` and run `scripts/run_test_set.py` |

## What is deliberately not built

Cross-encoder reranking, extra APIs and LLM query rewriting, Qdrant, bootstrap confidence
intervals, and full-text for every result — the plan's cut list, cut in the stated order and
stated openly (`docs/PROGRESS.md` §"Cut list"). The protected items — end-to-end demo,
exact-quote guard, source links, evidence/stance separation, transparent breakdown,
insufficient-evidence state — are all present, all measured, and all covered by tests.
