#!/bin/bash
# Start VerifyAI Development Server
echo "Starting VerifyAI API server..."
export MOCK_MODE=true
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000