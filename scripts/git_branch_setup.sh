#!/usr/bin/env bash
# Create or refresh Mansimran's branch for S2 retrieval work.
# Usage:  ./scripts/git_branch_setup.sh <repo-url>            # first time (clones)
#         ./scripts/git_branch_setup.sh                       # inside the repo: refresh branch
set -euo pipefail

BRANCH="mansimran/retrieval_and_evidence"

if [[ $# -ge 1 ]]; then
  git clone "$1" verifyai
  cd verifyai
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "Not a git repository. Pass the repo URL: ./scripts/git_branch_setup.sh <repo-url>"
  exit 1
fi

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git switch "$BRANCH"
else
  git switch -c "$BRANCH"
fi

echo "On branch: $(git rev-parse --abbrev-ref HEAD)"
echo
echo "Next:"
echo "  git fetch origin && git rebase origin/main    # stay close to main"
echo "  pytest -q                                     # must be green"
echo "  git add -A && git commit -m 'feat(retrieval): <what changed>'"
echo "  git push -u origin $BRANCH"
