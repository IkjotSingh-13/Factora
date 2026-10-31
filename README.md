# Factora VerifyAI — backend starter

Factora VerifyAI exposes S1 claim extraction, an S2 retrieval-only preview, and a transparent deterministic scoring endpoint. It does **not** yet provide the full `/api/verify` flow because S3 stance analysis and quote verification are still a separate integration step.

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
- `POST /api/extract` — accepts exactly one of `article_text` or `url` and returns S1 claims. The response maps S1's `claim_type` to the API's `type` field and preserves `entities`. It is explicitly an extraction-only response, not a verification verdict.
- `POST /api/retrieve` — accepts claims and returns an S2-only retrieval preview. It does not assign a truth verdict.
- `POST /api/score` — accepts structured claims and evidence from the pipeline, then returns verdict, stance direction, evidence strength, component scores, and audit counts.

Example text request:

```json
{"article_text": "Acme opened a factory in Delhi on Monday."}
```

Example extraction response shape:

```json
{
  "source_type": "article_text",
  "claims": [{"id": "claim-1", "text": "Acme opened a factory in Delhi.", "type": "factual", "entities": ["Acme", "Delhi"]}],
  "stage": "S1_EXTRACTION_ONLY",
  "note": "Claim extraction only. No evidence retrieval, stance analysis, or verdict was produced."
}
```

S1 URL fetching applies the extractor's URL/SSRF checks. `/api/score` only scores evidence that has `quote_verified: true`; this keeps unverified or invented quotes out of the assessment. Evidence strength is **not** a truth probability, and stance direction is reported separately.

## Shared contract

- `/api/extract` returns S1 claims with the shared `type` name plus the S1 `entities` list. This response is extraction-stage output, not yet a `ClaimInput`/verification report.
- At the Python integration boundary, `contract_adapter.to_claim_input` maps S1 `claim_type` to `ClaimInput.type`; S1 entities are kept separately because the scoring contract's `ClaimInput` does not currently define an `entities` field.
- Mansimran's retrieval produces `EvidenceItem` values with source/domain/date/relevance/credibility.
- S3 analysis fills `stance`, `quote`, and `quote_verified` on each `EvidenceItem`.
- The scorer drops unverified quotes and reports that in `audit.hallucinated_quotes_dropped`.

See `backend/app/models.py` for the shared Pydantic contract. Avoid renaming fields without coordinating with the team.

## Dependencies and line endings

Install project dependencies with the root `requirements.txt`; it includes `backend/requirements.txt`, so `pytest` can import the FastAPI app. `.gitattributes` keeps source/config line endings consistent across Windows and other operating systems. If Mansimran already supplied a team-approved `.gitattributes`, compare and use the team's agreed version.

## Git workflow

Copy the project files into your clean local clone on your feature branch. Review the files, run the API and tests, then commit/push from your own terminal. Do not commit `.venv`, model weights, `.env` files, or API keys.
