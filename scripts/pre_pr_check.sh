#!/usr/bin/env bash
# Pre-PR check for Mansimran's S2 branch (mansimran/retrieval_and_evidence).
#
# Run before every push that matters. It:
#   1. shows how far behind main you are
#   2. rebases onto origin/main (skipped with --no-rebase)
#   3. runs the offline test suite
#   4. prints the Evidence schema fields so contract drift is visible
#   5. tells you the exact push command
#
# Usage:
#   ./scripts/pre_pr_check.sh              # rebase + test + schema check
#   ./scripts/pre_pr_check.sh --no-rebase  # inspect only
set -uo pipefail

BRANCH="mansimran/retrieval_and_evidence"
REBASE=1
[[ "${1:-}" == "--no-rebase" ]] && REBASE=0

current=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "not-a-repo")
if [[ "$current" == "not-a-repo" ]]; then
  echo "Not a git repository."; exit 1
fi
if [[ "$current" != "$BRANCH" ]]; then
  echo "You are on '$current', expected '$BRANCH'."
  echo "switch with: git switch $BRANCH"
  exit 1
fi

echo "== 1. Fetch and compare to main =="
git fetch origin --quiet || { echo "fetch failed (offline?) — continuing"; }
counts=$(git rev-list --left-right --count origin/main..."$BRANCH" 2>/dev/null || echo "0 0")
behind=$(echo "$counts" | awk '{print $1}')
ahead=$(echo "$counts" | awk '{print $2}')
echo "   behind main: $behind   ahead: $ahead"

echo
echo "== 2. Contract drift check (the thing that breaks integrations) =="
if git cat-file -e origin/main:contracts.py 2>/dev/null; then
  echo "   contracts.py exists on main — diffing against your copy:"
  if git diff --quiet origin/main:contracts.py -- contracts.py 2>/dev/null; then
    echo "   SAME as main. Good."
  else
    echo "   DIFFERS from main:"
    git diff --no-index --stat <(git show origin/main:contracts.py) contracts.py 2>/dev/null | tail -3
    echo
    echo "   >>> Do NOT rename fields yourself. Send this diff to the lead and let them decide."
    echo "   >>> Full diff:  git diff origin/main:contracts.py contracts.py"
  fi
else
  echo "   no contracts.py on main yet — you are the first to define it. Tell the lead it exists."
fi

echo
if [[ $REBASE -eq 1 && "$behind" != "0" ]]; then
  echo "== 3. Rebasing onto origin/main =="
  if git rebase origin/main; then
    echo "   rebase clean."
  else
    echo "   CONFLICT. Remember: during a rebase, --ours = main, --theirs = your commit."
    echo "   keep main's shared files:   git checkout --ours contracts.py README.md requirements.txt"
    echo "   keep your module files:     git checkout --theirs sources.py s2_retrieval.py ranking.py"
    echo "   then: git add -A && git rebase --continue"
    echo "   or back out safely:         git rebase --abort"
    exit 1
  fi
else
  echo "== 3. Rebase skipped (already current or --no-rebase) =="
fi

echo
echo "== 4. Tests =="
if python -m pytest -q 2>&1 | tail -2; then :; else
  echo "   TESTS FAILED — fix before pushing."; exit 1
fi

echo
echo "== 5. Evidence schema you are shipping =="
python - <<'PY'
try:
    from contracts import Evidence
    print("   ", list(Evidence.model_fields))
except Exception as exc:
    print("    could not import contracts:", exc)
PY

echo
echo "== Next =="
echo "   git add -A && git commit -m 'feat(retrieval): <what changed>'"
echo "   git push origin $BRANCH        # add --force-with-lease only after a rebase"
