<#
.SYNOPSIS
  Pre-PR check for Mansimran's S2 branch, PowerShell edition (Windows).

.DESCRIPTION
  Replaces scripts/pre_pr_check.sh. Five steps:
    1. fetch + how far behind main you are
    2. contract-drift diff on contracts.py (the thing that breaks integrations)
    3. rebase onto origin/main
    4. run the offline test suite
    5. print the Evidence schema you are shipping

  ASCII-only on purpose: PowerShell 5.1 treats smart quotes as string
  delimiters, so em dashes / curly quotes in a .ps1 file cause parse errors.

.EXAMPLE
  .\scripts\pre_pr_check.ps1

.EXAMPLE
  .\scripts\pre_pr_check.ps1 -NoRebase
#>
[CmdletBinding()]
param(
    [switch]$NoRebase,
    [string]$Branch = "mansimran/retrieval_and_evidence"
)

$ErrorActionPreference = "Stop"

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

function Write-Step { param([string]$Text) Write-Host ""; Write-Host "== $Text ==" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "   $Text" -ForegroundColor Green }
function Write-Note { param([string]$Text) Write-Host "   $Text" -ForegroundColor Yellow }
function Write-Fail { param([string]$Text) Write-Host "   $Text" -ForegroundColor Red }

$python = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $python = "python" }
if (-not $python) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $python = "py" }
}
if (-not $python) { Write-Fail "python not found on PATH."; exit 1 }

if (-not (Test-Path ".git")) { Write-Fail "Not a git repository. cd into the repo root first."; exit 1 }

$current = (git rev-parse --abbrev-ref HEAD).Trim()
if ($current -ne $Branch) {
    Write-Fail "You are on '$current', expected '$Branch'."
    Write-Note "switch with: git switch $Branch"
    exit 1
}

Write-Step "1. Fetch and compare to main"
git fetch origin --quiet 2>$null
$counts = (git rev-list --left-right --count "origin/main...$Branch" 2>$null) -split "\s+"
$behind = 0
$ahead = 0
if ($counts.Count -ge 2) {
    $behind = [int]$counts[0]
    $ahead = [int]$counts[1]
}
Write-Ok "behind main: $behind   ahead: $ahead"

Write-Step "2. Contract drift check"
$hasMainContracts = $true
git cat-file -e "origin/main:contracts.py" 2>$null
if ($LASTEXITCODE -ne 0) { $hasMainContracts = $false }
if ($hasMainContracts) {
    $diffText = (git diff "origin/main:contracts.py" -- contracts.py 2>$null) -join "`n"
    if ([string]::IsNullOrWhiteSpace($diffText)) {
        Write-Ok "contracts.py is SAME as main. Good."
    } else {
        Write-Fail "contracts.py DIFFERS from main:"
        $diffText -split "`n" | Select-Object -First 25 | ForEach-Object { Write-Host "     $_" }
        Write-Note "Do NOT rename fields yourself. Send this diff to the lead."
        Write-Note "Full diff:  git diff origin/main:contracts.py contracts.py"
    }
} else {
    Write-Note "no contracts.py on main yet - you are first to define it. Tell the lead it exists."
}

Write-Step "3. Rebase"
if ($NoRebase) {
    Write-Ok "skipped (-NoRebase)"
} elseif ($behind -eq 0) {
    Write-Ok "already current with main"
} else {
    git rebase origin/main
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "CONFLICT. During a rebase: --ours = main, --theirs = your commit"
        Write-Note "keep main's shared files:  git checkout --ours contracts.py README.md requirements.txt"
        Write-Note "keep your module files:    git checkout --theirs sources.py s2_retrieval.py ranking.py"
        Write-Note "then: git add -A ; git rebase --continue"
        Write-Note "back out safely: git rebase --abort"
        exit 1
    }
    Write-Ok "rebase clean"
}

Write-Step "4. Tests"
& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { Write-Fail "TESTS FAILED - fix before pushing."; exit 1 }

Write-Step "5. Evidence schema you are shipping"
& $python -c "from contracts import Evidence; print('   ', list(Evidence.model_fields))"

Write-Step "Next"
Write-Host "   git add -A ; git commit -m 'feat(retrieval): <what changed>'"
Write-Host "   git push origin $Branch"
Write-Host ""
