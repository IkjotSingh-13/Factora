#!/bin/bash
# Run this to initialize git and commit
git init
git add .
git commit -m "Initial commit: VerifyAI repo skeleton with FastAPI, schemas, scoring, report, and MOCK mode

- FastAPI /api/verify endpoint with MOCK mode for demo fallback
- Pydantic schemas for Claim, Evidence, Analysis, ClaimReport, ScoreBreakdown
- Transparent scoring: S = 0.30C + 0.30R + 0.20A + 0.10T + 0.10K
- Report builder with audit trail
- Tests for scoring and report modules

Co-Authored-By: Claude Code <noreply@anthropic.com>"