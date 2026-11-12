#!/usr/bin/env bash
# Join the ATC autoresearch with one command:
#
#   curl -fsSL https://raw.githubusercontent.com/kyleboas/atc-autoresearch/main/tools/join.sh | bash
#   # or, from a clone:  tools/join.sh [experiments]
#
# Forks and clones the repo (if needed), checks your setup, starts from the current best, and,
# if Claude Code is installed, runs an agent that follows collab.md for N experiments (default
# 10) on your own account. Other agents: point them at collab.md.
set -euo pipefail
UPSTREAM="${ATC_UPSTREAM:-kyleboas/atc-autoresearch}"
N="${1:-10}"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing $1. $2"; exit 1; }; }
need git "Install git first."
need gh "Install the GitHub CLI: https://cli.github.com"
need python3 "Install Python 3.14: https://www.python.org/downloads/"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 14))' || { echo "Python 3.14 or newer is needed (python3 is $(python3 -V))."; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Log in to GitHub first: gh auth login"; exit 1; }

if [ ! -f collab.md ] || [ ! -f tools/collab.py ]; then
  dir="$(basename "$UPSTREAM")"
  if [ ! -d "$dir" ]; then
    echo "Forking and cloning $UPSTREAM into ./$dir ..."
    gh repo fork "$UPSTREAM" --clone --default-branch-only >/dev/null
  fi
  cd "$dir"
fi
git remote get-url upstream >/dev/null 2>&1 || git remote add upstream "https://github.com/$UPSTREAM.git"

echo "Checking the simulator (about a minute) ..."
python3 evaluate.py --split smoke | grep -E '^(cost|hard):'
python3 tools/collab.py pull-best
python3 tools/collab.py status | head -25

if command -v claude >/dev/null 2>&1; then
  echo
  echo "Starting a Claude Code agent for $N experiments (on your account; Ctrl-C to stop) ..."
  claude -p "You are a contributor to a shared autoresearch project in $(pwd). Read collab.md, program.md, README.md and atc/commands.py, run python3 tools/fetch_manual.py and python3 tools/diagnose.py, then follow collab.md exactly for $N experiments: status, claim one new idea with tools/collab.py start, edit only controller.py, tools/collab.py finish, and tools/collab.py submit whenever finish says you beat the upstream best. Then stop." \
    --permission-mode acceptEdits \
    --allowedTools "Read" "Edit" "Grep" "Glob" "Bash(python3:*)" "Bash(git status:*)" "Bash(git diff:*)" "Bash(git log:*)" "Bash(gh issue view:*)"
else
  echo
  echo "Ready. To run an agent, point it at collab.md (for example: \"Read collab.md and follow it for $N experiments\")."
  echo "By hand: python3 tools/collab.py start \"idea\"; edit controller.py; python3 tools/collab.py finish"
fi
