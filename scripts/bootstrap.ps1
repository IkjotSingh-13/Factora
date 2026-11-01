<#
.SYNOPSIS
  One command from a fresh clone to a running VerifyAI, on Windows.

.DESCRIPTION
  Creates .venv, installs the three requirements files, loads the spacy model,
  copies .env.example to .env, and runs the test suite. Idempotent: run it twice
  and it changes nothing you did not ask for.

  .\scripts\bootstrap.ps1                  deps + tests
  .\scripts\bootstrap.ps1 -Ollama          also pull the local model (multi-GB)
  .\scripts\bootstrap.ps1 -UI              also install npm deps and build the React UI
  .\scripts\bootstrap.ps1 -SkipTests       stop before the suite
  .\scripts\bootstrap.ps1 -Port 8010       port suggested at the end

  NOTE: this file is deliberately ASCII-only. Windows PowerShell 5.1 treats curly
  quotes as string delimiters and mangles em dashes, so non-ASCII here can break
  parsing on a teammate's machine. Keep it plain.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
#>
[CmdletBinding()]
param(
    [switch]$Ollama,
    [switch]$UI,
    [switch]$SkipTests,
    [string]$OllamaModel = "qwen2.5:7b",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Say($m)  { Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  ok   $m" -ForegroundColor Green }
function Skip($m) { Write-Host "  skip $m" -ForegroundColor DarkYellow }
function Bad($m)  { Write-Host "  FAIL $m" -ForegroundColor Red; throw $m }

Say "Python"
$py = $null
foreach ($c in @("py -3", "python", "python3")) {
    $name = ($c -split " ")[0]
    if (Get-Command $name -ErrorAction SilentlyContinue) {
        $probe = if ($c -eq "py -3") { & py -3 -c "import sys;print(sys.version_info>=(3,11))" 2>$null } else { & $name -c "import sys;print(sys.version_info>=(3,11))" 2>$null }
        if ($LASTEXITCODE -eq 0 -and "$probe".Trim() -eq "True") { $py = $c; break }
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
    }
}
if (-not $py) { Bad "no Python 3.11+ found. Install from python.org and tick 'Add to PATH'." }
& $py -V
Ok "using '$py'"

Say "Virtual environment (.venv)"
# A half-created .venv (interrupted run, no disk space) can leave an interpreter that falls
# back to the system site-packages. Verify it before installing anything, and verify the
# install target afterwards, so a broken venv never rewrites a global Python.
function VenvUsable($dir) {
  (Test-Path (Join-Path $dir "Scripts\python.exe")) -and (Test-Path (Join-Path $dir "pyvenv.cfg"))
}
if ((Test-Path ".venv") -and -not (VenvUsable ".venv")) {
  Skip ".venv exists but is incomplete - rebuilding it"
  Remove-Item -Recurse -Force ".venv"
}
if (Test-Path ".venv") { Ok "already present" } else { & $py -m venv .venv; Ok "created" }
$venvPy = Join-Path ".venv" "Scripts\python.exe"
if (-not (VenvUsable ".venv")) { Bad ".venv is not usable (Scripts\python.exe or pyvenv.cfg missing) - delete .venv and rerun" }
& $venvPy -c "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)"
if ($LASTEXITCODE -ne 0) { Bad "the venv python is not isolated (sys.prefix == base_prefix) - delete .venv and rerun" }
& $venvPy -m pip install --quiet --upgrade pip setuptools wheel | Out-Null
Ok "pip ready"

Say "Dependencies"
# backend (fastapi, pydantic, httpx, pytest) + S1 (spacy, trafilatura, dotenv) + S2 (requests)
& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Bad "pip install failed - scroll up for the wheel that broke" }
Ok "requirements.txt installed"

Say "spacy model (optional: S1 falls back to a blank pipeline)"
& $venvPy -c "import spacy; spacy.load('en_core_web_sm')" 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { Ok "english pipeline loads" }
else {
    Skip "model not present, downloading"
    & $venvPy -m spacy download en_core_web_sm | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "en_core_web_sm installed" } else { Skip "download failed (offline?). Extraction still works." }
}

Say "Environment file"
if (Test-Path ".env") { Ok ".env exists - left untouched" }
else { Copy-Item ".env.example" ".env"; Ok ".env created from .env.example (every key optional)" }

if ($Ollama) {
    Say "Ollama"
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        Skip "ollama not on PATH - install from ollama.com, then rerun with -Ollama"
    } else {
        $tags = $null
        try { $tags = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 3 } catch { $tags = $null }
        if (-not $tags) { Skip "no server on :11434 - run 'ollama serve' (or install the desktop app) and retry" }
        else {
            Ok "server reachable"
            $have = $tags.models | Where-Object { $_.name -like "$OllamaModel*" }
            if ($have) { Ok "$OllamaModel already pulled" }
            else {
                Write-Host "  pulling $OllamaModel (multi-GB, 5-15 min on wifi)"
                ollama pull $OllamaModel
                if ($LASTEXITCODE -ne 0) { Skip "pull failed - rerun: ollama pull $OllamaModel" } else { Ok "pulled" }
            }
            (Get-Content ".env") `
                -replace "^LLM_PROVIDER=.*", "LLM_PROVIDER=ollama" `
                -replace "^OLLAMA_MODEL=.*", "OLLAMA_MODEL=$OllamaModel" | Set-Content ".env"
            Ok ".env selects LLM_PROVIDER=ollama with $OllamaModel"
        }
    }
}

# ---------------------------------------------------------------- React UI
# / serves frontend-app/dist when it exists; /lite serves the zero-build file
# either way. The build is optional by design: no node, no npm, still a UI.
if ($UI) {
    Say "React UI (frontend-app -> dist)"
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Skip "npm is not on PATH - skipping. The single-file UI at /lite needs nothing."
    } else {
        Push-Location "frontend-app"
        try {
            & npm install --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
            & npm run build
            if ($LASTEXITCODE -ne 0) { throw "npm run build failed" }
            if (Test-Path "dist\index.html") { Ok "built dist - served at http://localhost:$Port/" }
            else { Skip "npm reported success but dist\index.html is missing - check the log above" }
        } catch {
            Skip "UI build stopped: $($_.Exception.Message). The /lite UI still works without node."
        } finally {
            Pop-Location
        }
    }
}

if (-not $SkipTests) {
    Say "Test suite (the real check that the install works)"
    & $venvPy -m pytest -q
    if ($LASTEXITCODE -ne 0) { Bad "tests failed - fix before demoing" }
}

Say "Ready"
Write-Host "  run the API + UI:      .venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $Port"
Write-Host "  then open:             http://localhost:$Port/  (React build if you passed -UI, else /lite)"
Write-Host "  what is reachable:     .venv\Scripts\python.exe scripts\check_connections.py"
Write-Host "  offline demo (no key): .venv\Scripts\python.exe scripts\run_test_set.py"
