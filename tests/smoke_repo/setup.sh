#!/usr/bin/env bash
# Create and initialize the babel git-branch smoke test repo.
#
# Usage:
#   ./setup.sh [DEST]
#
# DEST defaults to ~/babel-smoke. An existing directory will be wiped and
# re-initialized so the test always starts from a clean slate.

set -euo pipefail

DEST="${1:-$HOME/babel-smoke}"

if [ -d "$DEST" ]; then
    echo "Removing existing repo at $DEST"
    rm -rf "$DEST"
fi

mkdir -p "$DEST"
cd "$DEST"

git init
git checkout -b main

cat > README.md <<'EOF'
# babel smoke test repo

This repo is the target workspace for the babel orchestrator git-branch smoke test.

Expected post-run state
-----------------------
- `main` — unchanged; no commits added by the smoke run
- `feature/calculator` — contains `calculator.py` with add/subtract/multiply/divide
- `feature/greeter` — contains `greeter.py` with greet/farewell only
  (shout is intentionally left on a private sub-branch, not merged)
EOF

git add README.md
git commit -m "Initial commit"

mkdir -p .babel-agent
cat > .babel-agent/config.toml <<'EOF'
[roles.planner]
model = "gpt-5-codex-mini"

[roles.worker]
model = "gpt-5-codex-mini"

[roles.librarian]
model = "gpt-5-codex-mini"
EOF

echo ""
echo "Smoke repo ready at: $DEST"
echo ""
echo "To run the smoke test (cd into the smoke repo first — bab uses cwd as workdir):"
echo "  cd $DEST"
echo "  bab run --max-rounds 2 --max-workers 2 /path/to/babel-research/tests/prompts/smoke_prompt.md"
echo ""
echo "After the run, verify:"
echo "  cd $DEST"
echo "  git log --oneline --all --graph"
echo "  git show feature/calculator:calculator.py"
echo "  git show feature/greeter:greeter.py"
