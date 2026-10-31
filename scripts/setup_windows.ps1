<#
.SYNOPSIS
  One-shot Windows setup for the VerifyAI S2 retrieval module.

.DESCRIPTION
  Creates a virtual environment, installs dependencies, creates .env from the
  template, then runs the offline test suite so you know it works before you
  touch the network.

  NOTE: this file is deliberately ASCII-only. PowerShell 5.1 treats smart
  quotes as string delimiters, so an em dash or curly quote in a .ps1 file can
  break parsing. Keep it plain ASCII.

.EXAMPLE
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\scripts\setup_windows.ps1
#>
[CmdletBinding()]
param(
    [string]$VenvPath = ".venv",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

# UTF-8 everywhere: Windows consoles default to cp1252 and crash on titles
# containing curly quotes or em dashes coming back from Wikipedia / PubMed.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step { param([string]$Text) Write-Host ""; Write-Host "== $Text ==" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "   $Text" -ForegroundColor Green }
function Write-Note { param([string]$Text) Write-Host "   $Text" -ForegroundColor Yellow }
function Write-Fail { param([string]$Text) Write-Host "   $Text" -ForegroundColor Red }

# --- locate python ----------------------------------------------------------
$python = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $python = "python" }
if (-not $python) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $python = "py" }
}
if (-not $python) {
    Write-Fail "python not found on PATH."
    Write-Note "Install Python 3.10+ from python.org, tick 'Add python.exe to PATH', reopen PowerShell."
    exit 1
}

Write-Step "0. Python version"
& $python -c "import sys; print('   ', sys.version.split()[0], '|', sys.executable)"
if ($LASTEXITCODE -ne 0) { Write-Fail "python could not run."; exit 1 }

Write-Step "1. Virtual environment ($VenvPath)"
if (-not (Test-Path $VenvPath)) {
    & $python -m venv $VenvPath
    Write-Ok "created $VenvPath"
} else {
    Write-Ok "already exists"
}
$venvPython = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path $venvPython)) { Write-Fail "venv python not found at $venvPython"; exit 1 }

Write-Step "2. Install dependencies"
& $venvPython -c "import sys; print('   interpreter:', sys.executable)"
& $venvPython -m pip install --quiet --upgrade pip
if (Test-Path "requirements-s2.txt") {
    Write-Ok "using requirements-s2.txt (module-scoped, cannot be overwritten by teammates)"
    & $venvPython -m pip install --quiet -r requirements-s2.txt
} else {
    Write-Note "requirements-s2.txt missing, falling back to requirements.txt"
    & $venvPython -m pip install --quiet -r requirements.txt
}
if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed. Check your network or proxy."; exit 1 }

# Verify by importing, not by trusting pip's exit code. This is how a missing
# pydantic used to slip through to the demo.
& $venvPython -c "import requests, pydantic, pytest; print('   verified:', 'requests', requests.__version__, '| pydantic', pydantic.VERSION, '| pytest', pytest.__version__)"
if ($LASTEXITCODE -ne 0) {
    Write-Fail "dependencies are not importable by $venvPython"
    Write-Note "fix: & '$venvPython' -m pip install pydantic requests pytest"
    Write-Note "then: & '$venvPython' scripts\doctor.py"
    exit 1
}

Write-Step "3. .env"
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Ok "created .env from .env.example (add keys when you have them)"
} else {
    Write-Ok ".env already present, left untouched"
}

Write-Step "4. Tests (offline, no network)"
if ($SkipTests) {
    Write-Note "skipped (-SkipTests)"
} else {
    & $venvPython -m pytest -q
    if ($LASTEXITCODE -ne 0) { Write-Fail "tests failed"; exit 1 }
    Write-Ok "tests green"
}

Write-Step "5. Environment doctor"
& $venvPython scripts\doctor.py

Write-Step "6. Try it offline"
& $venvPython s2_retrieval.py --offline "vitamin C prevents the common cold" --top-k 3

Write-Step "Done. Next steps"
Write-Host "   activate the venv:   .\$VenvPath\Scripts\Activate.ps1"
Write-Host "   live retrieval:      python s2_retrieval.py `"Vitamin C supplementation prevents the common cold in most adults`""
Write-Host "   measured sources:    python scripts/live_smoke.py"
Write-Host "   before pushing:      .\scripts\pre_pr_check.ps1"
Write-Host ""
