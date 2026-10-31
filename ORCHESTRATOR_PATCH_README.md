# VerifyAI orchestrator patch

Extract at the **repository root** (`D:\factora_clean`), not inside `backend\`.

It contains:

- `backend/app/main.py` (adds an S2-only `/api/retrieve` route; existing `/api/score` remains)
- `backend/app/pipeline/orchestrator.py` (mockable S2→S3→S4/S5 orchestration seam)
- `tests/test_orchestrator.py`
- `docs/ORCHESTRATOR_HANDOFF.md`

Then run `py -m pip install -r requirements.txt` and `py -m pytest -q`. The patch was tested against current `main` and all 71 tests pass. Do not present `/api/retrieve` as a verification verdict; it is explicitly retrieval-only until S3 analysis is connected.
