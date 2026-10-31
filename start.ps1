# Start VerifyAI Development Server
Write-Host "Starting VerifyAI API server..." -ForegroundColor Green
$env:MOCK_MODE = "true"
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000