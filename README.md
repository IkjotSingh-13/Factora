# Factora VerifyAI — backend starter

This is Manmohit's first backend slice: shared API models, a transparent deterministic scorer, a report builder, and a FastAPI endpoint. It does **not** yet extract claims from articles or search the web; Ikjot's and Mansimran's pipeline stages will feed this API later.

## Run locally on Windows PowerShell

From the repository root:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m uvicorn app.main:app --reload --app-dir backend
```

Open `http://127.0.0.1:8000/docs` and `http://127.0.0.1:8000/health`.

Run tests:

```powershell
py -m pytest -q
```

## API

- `GET /health` — confirms the API is running.
- `POST /api/score` — accepts structured claims and evidence from the pipeline, then returns verdict, stance direction, evidence strength, component scores, and audit counts.

`/api/score` only scores evidence that has `quote_verified: true`. This keeps unverified or invented quotes out of the assessment. Evidence strength is **not** a truth probability, and stance direction is reported separately.

## Shared contract

- Ikjot's claim extraction produces `ClaimInput` values.
- Mansimran's retrieval produces `EvidenceItem` values with source/domain/date/relevance/credibility.
- Ikjot's analysis fills `stance`, `quote`, and `quote_verified` on each `EvidenceItem`.
- The scorer drops unverified quotes and reports that in `audit.hallucinated_quotes_dropped`.

See `backend/app/models.py` for the frozen Pydantic contract. Avoid renaming fields without coordinating with the team.

## Dependencies and line endings

Install project dependencies with the root `requirements.txt`; it includes `backend/requirements.txt`, so `pytest` can import the FastAPI app. `.gitattributes` keeps source/config line endings consistent across Windows and other operating systems. If Mansimran already supplied a team-approved `.gitattributes`, compare and use the team's agreed version.

## Git workflow

Copy the project files into your clean local clone on your feature branch. Review the files, run the API and tests, then commit/push from your own terminal. Do not commit `.venv`, model weights, `.env` files, or API keys.
