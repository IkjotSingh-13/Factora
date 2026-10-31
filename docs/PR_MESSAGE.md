# PR: S2 -> S3 handoff — full text for the quote guard, plus a test-regression fix

Branch: `mansimran/retrieval_and_evidence` -> `main`
Base tested against: `9309745` (merge PR #6, S1 `/api/extract`)
Suite: `python -m pytest -q tests/test_s2.py` -> **76 passed**

---

## Heads-up for the lead: this PR also repairs `main`

`main` currently carries a version of `tests/test_s2.py` that:

1. **contains the flaky timing assertion** `parallel_elapsed < 1.0` (absolute
   wall-clock threshold, fails on slow Windows machines), and
2. **is missing 14 tests** — the S1-claim, full-text, quote-guard and
   retrieval-only blocks were dropped by a bad edit that shipped in `473be53`.

Nothing in the shipped *code* was affected, and no S2 behaviour changed. It was
a test-file regression only. This PR restores the 14 tests and replaces the
absolute threshold with a relative one (ratio vs serial, after warm-up).

CI on `main` will stay red until this lands. It is a test bug, not a code bug.

---

## What this adds

| Piece | Purpose |
|---|---|
| `fulltext.py` | Fetches real page text for the quote-verification guard. `PageText(status="full_text" \| "snippet", text, chars, reason)`. Wikipedia via MediaWiki `explaintext`; generic HTML via boilerplate-stripped extraction (2 MB cap). |
| `contracts.py` | `Evidence` gains `full_text`, `text_scope`, `text_scope_reason`. `RetrievalAudit` gains `full_text_requested/fetched/failed/chars/full_text_reasons`. |
| `s2_retrieval.py` | `fetch_full_text=3`, `fulltext_deadline_s=4.0`, `fulltext_max_chars=6000`. Deadline-bounded so full text never blows the retrieval budget. |
| `contract_adapter.py` | `quote_guard_map()` -> `{evidence_id: "full_text" \| "snippet"}` inside every bundle, so S3 can see what it is allowed to claim. |
| `s2_service.py` | `quote_support()` returns `(found, scope)`. `apply_analysis` verifies against `full_text` when present, else the snippet, and **refuses** a snippet-only quote that is absent from the snippet. |
| `scripts/check_s3_contract.py` | Exit-1 seam checker: catches an analyzer that asserts `quote_verified=True` on an empty quote, a quote absent from retrieved text, an invalid stance, or zero verified items. |
| `scripts/make_sample_bundle.py` | Produces the sample bundle for Ikjot (`--live`, `--offline`, `--claims-file`). |
| `docs/S2_S3_HANDOFF.md` | The full written handoff: answers, measurements, limitations, S3 rules. |
| `data/demo_snapshot.json` | Regenerated live so the demo now contains **real full text** (previously captured before `fulltext.py` existed, so it was 0 full-text items). |

---

## Answering the three questions

### (a) Does every bundle give S3 enough verbatim text for the quote guard?

**Yes where it is possible, and it says so explicitly where it is not.**

Live measurement on `"Solar power in India"`: 3 full-text requests -> 2 fetched
(3382 and 6000 chars from Wikipedia), 1 failed (PubMed landing page) = 9382
characters of checkable text.

Coverage by source:

| Source | Full text? | Why |
|---|---|---|
| Wikipedia | Yes | MediaWiki `prop=extracts&explaintext=1`, keyless |
| Generic web | Yes | HTML fetch + boilerplate strip, 2 MB cap |
| PubMed | **No** | HTML landing page carries no article body |
| DOI resolver | **No** | Redirect landing page, not the paper |
| OpenAlex | **No** | Metadata + inverted-index abstract only |

These four are permanent limits, disclosed rather than fought. Every
snippet-only item carries a `text_scope_reason` so S3 is never guessing.

**The limitation, stated plainly for S3:** verifying a quote against a
`snippet` proves only *"this string appears in the search fragment"* — **not**
*"this string appears in the source"*. When `quote_guard[id] == "snippet"`, S3
must phrase the result as **"verified against the retrieved snippet"**, never as
"verified against the source".

### (b) Does `aretrieve_for_claims` accept original S1 claim objects and use `entities`?

**Yes, losslessly.** S1's `Claim` is pydantic with `extra="forbid"` and exactly
four fields. Tested against a vendored copy of the real schema
(`tests/vendor/s1_schemas_snapshot.py`):

| S1 field | S2 field | Loss? |
|---|---|---|
| `id` | `id` | none |
| `text` | `text` | none |
| `entities` | query variants (enrichment only) | none — they add queries, they do not replace the literal query or change ranking inputs |
| `claim_type` | `type` | none — `factual \| prediction \| opinion \| non_verifiable` passes through unchanged |
| — | `article_quote = None` | S1 does not emit one |
| — | `time_sensitive = False` | S1 does not emit one |

Ikjot is not asked to rename anything or drop `entities`.

### (c) Is S2 still retrieval-only?

**Yes, and there is a test that fails if that ever changes**
(`test_s2_output_contains_no_stance_or_verdict_decisions`). Every item leaves S2
with `stance="NEUTRAL"`, `quote_verified=False`, `quote=""`, `rationale=""`.
No `verdict`, no `evidence_strength`, no `stance_score` keys. S2 ranks retrieval
and reports what text it holds; S3 decides everything else.

### (d) Sample bundle + tests + commit

`data/sample_s2_bundle.json` (offline, deterministic) and
`data/demo_snapshot.json` (live, includes full text). 76 tests.

---

## Orchestrator seam — verified against `main`, not asserted

Ran against the real `backend/app/pipeline/orchestrator.py` at `9309745` with a
labelled S3 test double:

- `build_retrieval_preview(request, retrieve_fn=aretrieve_for_claims)`
  -> `stage="S2_RETRIEVAL_ONLY"`, summary
  `{claims: 2, evidence_items: 4, distinct_domains: 2, quote_verified_items: 0,
  claims_without_evidence: 1, full_text_items: 1, snippet_only_items: 3}`
- `run_verified_claim_pipeline` -> c1 `MIXED / PARTIALLY SUPPORTED`, strength
  0.634, n_independent 2; c2 `INSUFFICIENT EVIDENCE`

Both entry points accept my modules **unmodified**, including every key in
`retrieval_summary`.

---

## Two facts S3 needs, measured

1. `score_claim` keeps only items where `quote_verified and quote.strip()` are
   both set. **S2-only output is always `INSUFFICIENT EVIDENCE`.** S3 must do
   the analysis, that is expected.
2. **Verdicts are decided by distinct-domain coverage, not analysis quality.**
   1 verified item / 1 domain -> `INSUFFICIENT`; 2 domains -> `MIXED`;
   **3 distinct domains -> `SUPPORTED` / `REFUTED`**.
   So: spread verified quotes across at least 3 domains, or the claim cannot
   reach a decisive verdict no matter how good the analysis is.

---

## Checker Ikjot can run before the freeze

```
python scripts/check_s3_contract.py --replay data/demo_snapshot.json
python scripts/check_s3_contract.py --analyze your_module:your_fn --live
```

Exit 0 = compatible. Exit 1 = a real blocker, with the reason printed.

---

## Scope discipline

Touches only S2-owned files plus `docs/` and `data/`. No shared file
(`README.md`, `.pytestignore`, `requirements.txt`, `.gitattributes`,
`pytest.ini`) is reverted; no S1 or orchestrator file is modified. Non-S2 tests
were not run here only because `fastapi`/`dotenv` are absent from this sandbox,
which is an environment gap, not a code issue.

## Separate follow-up (not in this PR)

`main` has a leftover `s2_extract/verifyai/**` tree — 36 files committed by an
old `Copy-Item` mistake. Remove it in its own cleanup PR:
`git rm -r --cached s2_extract && git rm -r s2_extract`.
