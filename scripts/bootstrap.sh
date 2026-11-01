#!/usr/bin/env bash
# One command from a fresh clone to a running VerifyAI, on macOS and Linux.
#
#   ./scripts/bootstrap.sh                 venv + deps + spacy + .env + tests
#   ./scripts/bootstrap.sh --ollama        ...and pull the local model (~4.7 GB, opt-in)
#   ./scripts/bootstrap.sh --ui            also install npm deps and build the React UI
#   ./scripts/bootstrap.sh --skip-tests    stop before the suite
#   ./scripts/bootstrap.sh --port 8010     change the port the summary suggests
#
# Design rules this script follows, and you should keep them:
#   * idempotent - run it twice, nothing breaks and nothing is re-downloaded
#   * every optional step is non-fatal, with the reason printed
#   * it never edits a .env you already have
#   * it ends by verifying, not by hoping
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT=$(pwd)
RUN_TESTS=1
BUILD_UI=0
PULL_OLLAMA=0
OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"
PORT=8000

while [ $# -gt 0 ]; do
  case "$1" in
    --ollama) PULL_OLLAMA=1 ;;
    --ollama-model) shift; OLLAMA_MODEL="${1:-qwen2.5:7b}" ;;
    --skip-tests) RUN_TESTS=0 ;;
    --ui) BUILD_UI=1 ;;
    --port) shift; PORT="${1:-8000}" ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) printf 'unknown flag: %s (try --help)\n' "$1"; exit 2 ;;
  esac
  shift
done

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mskip\033[0m %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; exit 1; }

say "Python"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || fail "python3 is not on PATH. Install Python 3.11+ first."
"$PY" - <<'EOF' || fail "Python 3.11+ required (this is $(python3 -V 2>&1))."
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
EOF
ok "$("$PY" -V) at $(command -v "$PY")"

say "Virtual environment (.venv)"
# A .venv left half-built (interrupted run, full disk) still *looks* present but has no
# usable interpreter; activating it silently leaves `python` resolving to the SYSTEM one,
# and the pip install below would then rewrite someone's global site-packages. So the venv
# is verified, rebuilt if broken, and the interpreter is checked before anything installs.
venv_ok() { [ -x .venv/bin/python ] && [ -f .venv/pyvenv.cfg ]; }
if [ -d .venv ] && ! venv_ok; then
  warn ".venv exists but is incomplete - rebuilding it"
  rm -rf .venv
fi
if [ -d .venv ]; then
  ok "already present"
else
  "$PY" -m venv .venv \
    || fail "could not create .venv (debian/ubuntu: apt install python3-venv; if the disk is full, that is the error - check 'df -h .')"
  venv_ok || fail ".venv was created but is not usable - delete .venv and re-run"
  ok "created"
fi
# shellcheck disable=SC1091
. .venv/bin/activate || fail "could not activate .venv - delete it and re-run"
python -c 'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)' \
  || fail "the active python is NOT inside .venv; refusing to install into the system interpreter. Delete .venv and re-run."
python -m pip install --quiet --upgrade pip setuptools wheel >/dev/null 2>&1 || true
ok "pip $(python -m pip --version | awk '{print $2}') (isolated in .venv)"

say "Dependencies"
# The three stage files together: backend (fastapi, pydantic, httpx, pytest) +
# S1 (spacy, trafilatura, dotenv) + S2 (requests). Versions are pinned by ranges
# in those files; do not add a fourth requirements file here.
python -m pip install -r requirements.txt || fail "pip install failed - see the output above"
ok "requirements.txt installed"

say "spaCy model (optional: S1 falls back to spacy.blank if this fails)"
python - <<'EOF' 2>/dev/null && ok "english pipeline loads" || warn "model not present; trying the download"
import spacy
spacy.load("en_core_web_sm")
EOF
python -m spacy download en_core_web_sm >/dev/null 2>&1 \
  && ok "en_core_web_sm installed" \
  || warn "could not fetch the model (offline?). Extraction still works with the blank pipeline."

say "Environment file"
if [ -f .env ]; then
  ok ".env already exists - left untouched"
else
  cp .env.example .env
  ok ".env created from .env.example (all keys optional; live retrieval needs one)"
fi

if [ "$PULL_OLLAMA" = "1" ]; then
  say "Ollama (local model for S1 + S3 stance analysis)"
  if ! command -v ollama >/dev/null 2>&1; then
    warn "ollama not on PATH - install from ollama.com, then re-run with --ollama"
  else
    if ! curl -s --max-time 3 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      warn "no server on :11434 - start it with 'ollama serve' in another terminal"
    else
      ok "server reachable on 127.0.0.1:11434"
      if ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
        ok "$OLLAMA_MODEL already pulled"
      else
        printf '  pulling %s (this is a multi-GB download, ~5-15 min on wifi)\n' "$OLLAMA_MODEL"
        ollama pull "$OLLAMA_MODEL" && ok "pulled" || warn "pull failed - rerun 'ollama pull %s'" "$OLLAMA_MODEL"
      fi
      python - <<EOF
import re, pathlib
p = pathlib.Path(".env"); t = p.read_text()
t = re.sub(r"^LLM_PROVIDER=.*$", "LLM_PROVIDER=ollama", t, flags=re.M)
t = re.sub(r"^OLLAMA_MODEL=.*$", "OLLAMA_MODEL=$OLLAMA_MODEL", t, flags=re.M)
p.write_text(t)
print("  ok   .env now selects LLM_PROVIDER=ollama with $OLLAMA_MODEL")
EOF
    fi
  fi
fi

# ---------------------------------------------------------------- React UI
# / serves frontend-app/dist when it exists, /lite serves the zero-build file
# either way. So the build is optional by design: no npm, no network, still a UI.
if [ "$BUILD_UI" = "1" ]; then
  say "React UI (frontend-app -> dist)"
  if ! command -v npm >/dev/null 2>&1; then
    warn "npm is not on PATH - skipping. The single-file UI at /lite needs nothing."
  elif ! command -v node >/dev/null 2>&1; then
    warn "node is not on PATH - skipping the UI build."
  else
    ( cd frontend-app && npm install --no-audit --no-fund && npm run build ) \
      || fail "frontend-app build failed - the /lite UI still works; fix npm before shipping /"
    [ -f frontend-app/dist/index.html ] || fail "npm run build reported success but dist/index.html is missing"
    ok "built frontend-app/dist - served at http://localhost:$PORT/"
  fi
fi

if [ "$RUN_TESTS" = "1" ]; then
  say "Test suite (this is the real check that the install works)"
  python -m pytest -q || fail "tests failed - fix before demoing"
fi

say "Ready"
cat <<EOF
  run the API + UI:      python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $PORT
  then open:             http://localhost:$PORT/   (React build if you passed --ui, else /lite)
  what is reachable:     python scripts/check_connections.py
  offline demo (no key, no network):
                         python scripts/run_test_set.py
EOF
