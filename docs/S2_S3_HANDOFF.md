# S2 → S3 handoff (retrieval → quote verification)

Answers the lead's three questions, with the commit and tests to verify each one.
**Branch:** `mansimran/retrieval_and_evidence` · **S2 is retrieval-only:** no stance, no verdict.

---

## 1. Does S3 get enough verbatim text to verify a quote?

**It was a real gap — now fixed.** Previously `snippet` held a 600–1200 char search-API
fragment and `full_text` was empty, so a quote could only be checked against the
fragment. A match proved *"in the snippet we received"*, not *"in the source"* —
exactly the overclaim you were worried about.

Now S2 fetches page text for the top results and **tells S3 what it may claim**:

| Field (on the bundle) | Meaning |
|---|---|
| `evidence[].snippet` | what the search API returned (unchanged) |
| `evidence[].full_text` | fetched page text (up to 6 000 chars), `""` when unavailable |
| `evidence[].text_scope` | `"full_text"` or `"snippet"` — *the* signal |
| `evidence[].text_scope_reason` | why it is snippet-only, e.g. `HTTP 403`, `timeout`, `paywall landing page` |
| `quote_guard{evidence_id: scope}` | the same information as a flat map, easy for S3 to read |
| `audit.full_text_requested / fetched / failed / chars` | cost and outcome, per claim |

Measured on a live claim (`fetch_full_text=3`):

```
full_text_requested=3  fetched=2  failed=1  chars=9382
  encyclopedia en.wikipedia.org       scope=full_text  3382 ch  (snippet was 734)
  encyclopedia en.wikipedia.org       scope=full_text  6000 ch  (snippet was 1134)
  academic     pubmed.ncbi.nlm.nih.gov scope=snippet   reason: landing page, not article text
audit note: "snippet-only evidence: 1 item(s) could not be fetched in full; quotes for
             those can only be checked against the snippet"
```

### The rule S3 must follow

1. Read `quote_guard[evidence_id]`.
2. `full_text` → a substring match genuinely verifies the quote in the source.
3. `snippet` → a match verifies it **only in the snippet**. Say *"verified against the
   retrieved snippet"*, never *"verified in the source"*. If you cannot check it, set
   `quote_verified = false` rather than guessing.

Helper, if you want it (a text check, not a stance — the policy is still yours):

```python
from s2_service import quote_support

found, scope = quote_support(item, quote)
# found=True,  scope="full_text"  -> real source-level verification
# found=True,  scope="snippet"    -> present in the fragment only
# found=False                     -> drop the claim of verification
```

`apply_analysis()` enforces this on the merge side: a quote marked verified that does
**not** appear in the retrieved region is silently dropped (`quote=""`,
`quote_verified=False`), and the item still counts as retrieved.

Sources that can give full text and sources that cannot, by design:

| Source kind | Full text? | Why |
|---|---|---|
| Wikipedia | ✅ yes | plaintext `extracts` API, fast and reliable |
| Generic web (DuckDuckGoTavily results) | ✅ usually | HTML fetched, boilerplate stripped, 2 MB cap |
| PubMed / DOI | ❌ no | landing pages and paywalls; abstract snippet only |
| OpenAlex | ❌ no | metadata + abstract only |
| Snapshot replay | ✅ if captured | replays whatever text was captured |

Tuning: `RetrievalConfig(fetch_full_text=3, fulltext_deadline_s=4.0, fulltext_max_chars=6000)`.
`fetch_full_text=0` disables fetching (relevance-only mode). It is automatically off in
`offline` and `replay_snapshot` modes because nothing there is fetchable.

---

## 2. Does `aretrieve_for_claims` accept the original S1 claim objects?

**Yes — verified against the real class, not a stand-in.** S1's `Claim` is a pydantic
model with `extra="forbid"` and exactly four fields, so `tests/vendor/s1_schemas_snapshot.py`
is a verbatim copy of `backend/schemas.py` from `main`.

```python
from backend.schemas import Claim
from s2_service import aretrieve_for_claims

claim = Claim(id="claim_001",
              text="India is the world's largest solar energy producer",
              entities=["India"], claim_type="factual")

bundles = await aretrieve_for_claims([claim])     # unchanged S1 object
bundles[0]["claim"]
# {'id': 'claim_001',
#  'text': "India is the world's largest solar energy producer",
#  'type': 'factual',            <- claim_type mapped, not lost
#  'article_quote': None,        <- S1 does not emit one
#  'time_sensitive': False}      <- S1 does not emit one
```

Tests that pin this:

- `test_aretrieve_accepts_real_s1_claim_objects` — the real `Claim`, asserts
  `model_fields_set == {id, text, entities, claim_type}` and the `claim_type → type` mapping.
- `test_s1_entities_enrich_the_query_without_changing_retrieval_contract` — a recording
  adapter proves `"India"` reaches the query string.
- `test_expanded_s1_claim_still_validates_against_lead_models` — bundle → `ClaimEvidenceBundle`.

**Entities** are used only to build an extra literal query, e.g. for
`"Apple acquired Beats for 3 billion dollars in 2014"` with `["Apple", "Beats"]`:

```
"Apple acquired Beats 3 billion dollars 2014"      (literal, always emitted)
"Apple Beats acquired 3 billion dollars 2014"      (entity-anchored)
"Apple Beats 3 2014"                               (short variant)
```

They never affect ranking, and they are not emitted as a field because
`ClaimInput` has no slot for them. If you want them on the wire, say so and I will
emit `extras.entities`.

---

## 3. Does S2 stay retrieval-only?

**Yes, and now there is a test that fails if it ever stops being true.**
`test_s2_output_contains_no_stance_or_verdict_decisions` asserts that a bundle has no
`verdict`, `evidence_strength` or `stance_score`, and that every evidence item leaves
with `stance="NEUTRAL"`, `quote_verified=False`, `quote=""`, `rationale=""`.

`apply_analysis()` is a **merge**, not an analysis: it takes stance objects S3 produced
and copies them onto evidence. It never derives a stance from text, and an empty or
unknown stance falls back to `NEUTRAL`
(`test_apply_analysis_never_invents_a_stance_it_was_not_given`).

---

## Sample returned bundle

`data/sample_s2_bundle.json` — regenerate with:

```bash
python scripts/make_sample_bundle.py            # offline, deterministic
python scripts/make_sample_bundle.py --live     # real sources + full text
```

Shape (trimmed):

```json
{
  "retrieval_summary": {
    "claims": 3, "evidence_items": 4, "distinct_domains": 4,
    "quote_verified_items": 0, "claims_without_evidence": 1,
    "full_text_items": 0, "snippet_only_items": 4
  },
  "bundles": [
    {
      "claim": {"id": "claim_001", "text": "...", "type": "factual",
                "article_quote": null, "time_sensitive": false},
      "quote_guard": {"ev_d2ea075ca8": "snippet", "ev_b21071f892": "snippet"},
      "evidence": [
        {
          "id": "ev_d2ea075ca8", "claim_id": "claim_001",
          "url": "https://...", "domain": "en.wikipedia.org", "title": "...",
          "snippet": "...", "full_text": "",
          "published_at": "2024-06-01", "source_kind": "encyclopedia",
          "relevance": 0.67, "credibility": 0.8,
          "stance": "NEUTRAL", "quote": "", "quote_verified": false, "rationale": "",
          "context_score": 0.55, "context_gap": false
        }
      ]
    }
  ]
}
```

---

## Tests and commit

```bash
pytest -q tests/test_s2.py          # 74 tests, no network needed
python scripts/make_sample_bundle.py
python scripts/contract_check.py    # validates against app.models
```

Key tests for this handoff: `test_html_to_text_strips_boilerplate_and_keeps_paragraphs`,
`test_full_text_marks_scope_and_never_pretends`, `test_quote_guard_reports_what_s3_may_claim`,
`test_quote_check_only_passes_against_the_text_we_actually_have`,
`test_apply_analysis_verifies_against_full_text_and_refuses_without_it`,
plus the three S1 tests and the retrieval-only invariant test.

Commit message: `feat(retrieval): full-text fetching for the quote guard + S1 handoff tests`

---

## One latency note for the orchestrator

Full-text fetching costs ~1–2 s per claim on top of retrieval (bounded by
`fulltext_deadline_s=4.0`). If `/api/retrieve` needs to feel instant for the UI, pass
`fetch_full_text=0` there and leave it on for `/api/verify` — quotes are only needed
for the verify path. Your call; both are one config flag.


---

## Verified against main's real orchestrator (not just unit tests)

Ran on `main` at `9309745` with `backend/` on `sys.path`, my S2 code, and a
**clearly-labelled S3 test double** (it is not Ikjot's analyzer; it exists only to
exercise the seam):

```python
from backend.schemas import Claim
from app.pipeline.orchestrator import build_retrieval_preview, run_verified_claim_pipeline

# /api/retrieve preview
preview = await build_retrieval_preview(RetrievalPreviewRequest(claims=[...]))
#   stage = "S2_RETRIEVAL_ONLY"
#   summary = {'claims': 2, 'evidence_items': 4, 'distinct_domains': 2,
#              'quote_verified_items': 0, 'claims_without_evidence': 1,
#              'full_text_items': 1, 'snippet_only_items': 3}

# full pipeline
report = await run_verified_claim_pipeline(claims, analyze_fn=stub_analyze)
#   claim_001: verdict='MIXED / PARTIALLY SUPPORTED'
#      strength=0.634 direction=-1.00 n_used=2 n_indep=2 dropped=2
#      C=0.83 R=0.67 A=0.63 T=0.75 K=0.49
#   claim_002: verdict='INSUFFICIENT EVIDENCE'   (no evidence retrieved)
```

Both call sites work unchanged — `aretrieve_for_claims`, `apply_analysis`,
`retrieval_summary` and `to_score_request` match what `orchestrator.py` expects,
including the `retrieval_summary` keys it validates against.

Note what limited `claim_001` to `MIXED`: only **2 independent domains** were
available in that sandbox run, so `n_independent/3` shrank the score. On the venue
network retrieval returns 4–5 domains for the same claim, which is enough for a
decisive verdict. This is a *retrieval coverage* dependency, not an analysis bug —
worth knowing when the demo claim is chosen.

Also worth knowing: during this testing the sandbox hit **HTTP 429 from both
Wikipedia and OpenAlex**. Retrieval degraded to zero evidence with an audit note
instead of raising — the fail-soft design working as intended, and a realistic
risk if several teams hammer the same public APIs at the venue.

---

## Tool for Ikjot: `scripts/check_s3_contract.py`

Run it against his analyzer before the freeze. It answers "will the scorer do
something sensible with my output?" rather than "is my code correct".

```bash
# against the captured snapshot, offline
python scripts/check_s3_contract.py --analyze my_s3:analyze --replay data/demo_snapshot.json

# live sources, with full-text fetching on
python scripts/check_s3_contract.py --analyze my_s3:analyze

# without an analyzer: show what the seam receives
python scripts/check_s3_contract.py --replay data/demo_snapshot.json
```

Sample output:

```
per claim:
  claim_001: 2 verified item(s), 2 domain(s), stances=['REFUTES']  [shrink-limited]

quote guard: 0 fabricated, 0 empty-but-claimed, 4/10 evidence items analysed

findings:
  [warning] claim_001: only 2 independent domain(s); the scorer shrinks by
            n_independent/3, so a decisive verdict needs 3+
  [warning] 4 quote(s) only checkable against a snippet: say 'verified against
            the retrieved snippet', not 'verified in the source'

Verdict: S3 is compatible with the S2 seam.
```

It flags as **BLOCKER**: analyses matching no evidence, `quote_verified=True` with an
empty quote, quotes absent from the retrieved text, invalid stance values, and zero
verified items per claim. As **warning**: too few independent domains, and
snippet-scope verifications. Exit code 1 on any blocker, so it can run in CI.
