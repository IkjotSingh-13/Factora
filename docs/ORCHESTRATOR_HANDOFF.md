# Manmohit orchestration patch

This patch is based on the repository's `main` after the S2 merge. It adds a safe S2 preview route and an injectable S2→S3→S4/S5 orchestration function.

## Files in this patch

- `backend/app/pipeline/orchestrator.py` — typed S2-only preview plus `run_verified_claim_pipeline(...)`.
- `backend/app/main.py` — adds `POST /api/retrieve`; leaves `/api/score` unchanged.
- `tests/test_orchestrator.py` — tests the real quote guard and the new preview endpoint with synthetic fixtures.

## Why there are two entry points

`POST /api/retrieve` is usable now, before S3 is ready. It returns a response labeled `S2_RETRIEVAL_ONLY`; it does **not** return a truth verdict.

`run_verified_claim_pipeline(...)` is ready for Ikjot's S3 function. It performs:

```text
claims → await S2 retrieval → S3 analysis callback → apply_analysis quote guard → ScoreRequest → report
```

The S3 callback contract is:

```python
analyze_fn(claims, bundles) -> list[analysis]
```

Each analysis should identify its evidence with `evidence_id` (preferred) or `url` and return `stance`, `quote`, `quote_verified`, `rationale`, `context_score`, and `context_gap`. `apply_analysis` rechecks the quote against the retrieved text before scoring.

## Apply on Manmohit's branch

From the repository root, after updating your branch from `main`, copy the files in this patch to the same relative paths. **Do not extract this ZIP inside `backend/`; extract it at the repository root.**

Run:

```powershell
py -m pip install -r requirements.txt
py -m pytest -q
```

The patch was tested against the merged S2 `main`: **71 tests passed** (one existing Starlette/httpx deprecation warning).

Once Ikjot shares the S3 function signature, call `run_verified_claim_pipeline(...)` from the final `/api/verify` route after S1 has produced claims. Until then, use `/api/retrieve` only as a retrieval smoke test.
