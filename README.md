# VerifyAI

Extract factual claims from online content, retrieve credible evidence, compare sources, and show a transparent assessment.

## Quick Start

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## API

POST `/api/verify` with JSON:
```json
{ "text": "Article text here...", "url": "optional source URL" }
```

Returns a `ClaimReport` with claims, evidence, stances, scores, and audit trail.