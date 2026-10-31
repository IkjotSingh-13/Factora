# Factora — VerifyAI

Extract the factual claims from a piece of online content, retrieve credible evidence for
each one, compare what the sources actually say, and show a transparent assessment — with
the reasoning visible and never an unsupported AI verdict.

What the system guarantees, in one line each:

- **A claim is never verified against an invented quote.** Every displayed evidence quote is
  checked to appear verbatim in the text we retrieved; anything that fails is dropped from the
  score and labelled as dropped.
- **Direction is separate from strength.** "The evidence points toward refuting" and "the
  evidence is thin" are two different numbers on screen, in two different gauges.
- **Insufficient evidence is a result, not a failure.** Fewer than two independent domains, or
  nothing quotable, produces `INSUFFICIENT EVIDENCE` with an explanation of why.
- **Every number is reproducible arithmetic.** `C/R/A/T/K` with their weights and
  contributions are in the response; `docs/SCORING.md` derives them.

## Run it

From the repository root (Windows PowerShell users: `py` instead of `python`):

```bash
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --app-dir backend --host 0.0.0.0 --port 8000
```

Open **`http://localhost:8000/`** for the UI, `/docs` for Swagger, `/health` for a liveness
check. The UI is one self-contained file (`frontend/index.html`): no build step, no npm, no
CDN, no external request — it is served by `StaticFiles` and talks only to relative `/api/*`
paths, so it works offline and on any machine that has the repo.

No API keys are needed to run everything except live retrieval: S1 falls back to its
deterministic extractor and S3 to its lexical analyser, and both fallbacks are *named* in the
response warnings.

## API

| Route | What it does |
| --- | --- |
| `POST /api/verify` | The whole pipeline on one article: S1 → S2 → S3 → S4/S5. Accepts exactly one of `article_text` or `url`, plus `mode`, `as_of`, `max_claims`. |
| `POST /api/extract` | S1 only. Extraction, explicitly not a verdict (`stage: "S1_EXTRACTION_ONLY"`). |
| `POST /api/retrieve` | S2 only. Retrieval preview; no stance, no verdict. |
| `POST /api/score` | S4/S5 only. Scores claims + evidence you already have; only `quote_verified: true` evidence is scored. |
| `GET /api/samples` | Prepared demo inputs, each written to exercise one honest outcome. |
| `GET /api/config` | What this process will actually do: provider, mode, snapshot availability, which keys are set. |
| `GET /health` | Liveness. |

```bash
curl -s localhost:8000/api/verify -H 'content-type: application/json' -d '{
  "article_text": "The Eiffel Tower is 330 metres tall.", "mode": "offline", "as_of": "2026-10-31"}'
```

`/api/verify` returns `report` (the claim cards: verdict, `stance_score`,
`evidence_strength`, `score_raw`, `shrinkage_factor`, `scores`, `contributions`, `evidence`,
`audit`), plus the audit trail around it: `stages[]` (per-stage status and milliseconds),
`s1`, `grounding`, `s3_audit`, `retrieval_summary`, `per_claim_retrieval`, `warnings[]`,
`demo_backup` and a `note`. A stage that could not do its job degrades and says so in
`warnings` — the request does not fail silently and does not paper over a gap.

### Evidence modes

| `mode` | Evidence path | Honest label on the result |
| --- | --- | --- |
| `live` | Real HTTP retrieval (Google Fact Check, Tavily, OpenAlex, …), optional page fetch for full-text quote checks | `full_text_items` / `snippet_only_items` in `retrieval_summary` |
| `offline` | `data/mock_corpus.json` only — no network | warning: every source is `source_kind=mock`, credibility 0.10 **by design**, so "this shows the mechanism, not what real evidence does" |
| `snapshot` | Replays `data/demo_snapshot.json` (real pages captured earlier) | `demo_backup: true` + "DEMO BACKUP, not a live retrieval" |
| `auto` | `live`, unless `VERIFY_OFFLINE` / `VERIFY_SNAPSHOT_ONLY` / `VERIFY_MODE` say otherwise | never silently substitutes the snapshot |

`docs/DEMO.md` is a 3-minute script built around those labels, including what to say when a
mode returns nothing.

`docs/ARCHITECTURE.md` is the map of how those pieces connect (seams, ownership, failure
policy, extension points); `docs/SCORING.md` derives the number; `docs/slides.html` is the
deck.

## Checks

```bash
python -m pytest -q                                            # 393 tests
python scripts/doctor.py                                       # env + wiring report
python scripts/check_s3_contract.py --analyze s3_analysis:analyze \
    --replay data/demo_snapshot.json                           # S3 ↔ S2 seam gate
python scripts/run_test_set.py                                 # labelled QA set, measured
node scripts/ui_render_check.mjs                               # UI renders every state
```

(Node is only needed for that last one; nothing in the shipped app requires it.)

The last two are not optional extras: the seam checker is what tells S3 authors whether their
analyzer is compatible with the evidence bundle shape, and `run_test_set.py` writes
`data/test_set_results.json`, which `docs/TEST_SET.md` quotes verbatim. `tests/test_test_set.py`
re-runs the set and fails if the committed results no longer match, so no documented number can
drift from the code.

Latest measured state on this checkout: 393 tests pass; seam gate reports "S3 is compatible
with the S2 seam" with 10/10 evidence items analysed and 0 fabricated quotes; QA set offline
**9/9 directions met, 0 invariant violations, 1 case unmeasured**, on captured real sources
(snapshot) 5/9. Neither number is an accuracy figure — see `docs/TEST_SET.md` for what they do
and do not mean.

## Shared contract

- `backend/app/models.py` is the shared Pydantic contract between stages. Do not rename fields
  without coordinating with the team.
- `/api/extract` returns S1 claims with the shared `type` name plus S1's `entities` list. At the
  Python boundary, `contract_adapter.to_claim_input` maps S1's `claim_type` to
  `ClaimInput.type`; entities are kept separately because `ClaimInput` does not define them.
- S2 produces `EvidenceItem` values with source/domain/date/relevance/credibility.
- S3 (`s3_analysis.py`) fills `stance`, `quote`, `quote_verified` and `quote_scope`
  (`full_text` | `snippet` | unset). `scripts/check_s3_contract.py` validates that seam.
- S4 scores only evidence with `quote_verified: true` and a non-empty quote, and reports the
  remainder as `audit.hallucinated_quotes_dropped`.
- `data/ui_response_fixture.json` is a captured real response used by the UI check; refresh it
  with a fresh `/api/verify` payload when the response contract changes.

## Layout

```
s1:   backend/s1_claims.py, backend/llm_provider.py, backend/config.py
s2:   s2_retrieval.py, s2_service.py, sources.py, ranking.py, cache.py, fulltext.py, snapshot.py
s3:   s3_analysis.py, scripts/check_s3_contract.py
s4/5: backend/app/pipeline/s4_scoring.py, s5_report.py
glue: backend/app/pipeline/{article_quotes,verify,orchestrator}.py, contract_adapter.py
ui:   frontend/index.html (one file), scripts/ui_render_check.mjs
qa:   data/test_set.json, scripts/run_test_set.py, docs/TEST_SET.md
docs: docs/{ARCHITECTURE,SCORING,DEMO,TEST_SET,PROGRESS,CONTRACT,INTEGRATION,WINDOWS}.md
deck: docs/slides.html (self-contained, no build step — open and press →)
```

Dependencies stay where the plan put them: `requirements.txt` pulls in the three stage files
(`backend/` → fastapi, uvicorn, pydantic, pytest, httpx; `-s1` → spacy, trafilatura,
beautifulsoup4, python-dotenv; `-s2` → requests). The S3 analyser and the pipeline glue add
nothing at all — `s3_analysis.py` imports only the standard library plus two scope constants
from `contract_adapter`, so the stage that decides what may be quoted has no dependency that
can fail. `.gitattributes` keeps line endings consistent across Windows and other operating
systems.

## Git workflow

Work on a feature branch, review the diff, run the checks above, then commit and push from your
own terminal. Do not commit `.venv`, model weights, `.env` files, or API keys.
