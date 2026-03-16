#!/bin/bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
ln -sf "$REPO_ROOT/Backend/hooks/pre-commit" "$REPO_ROOT/.git/hooks/pre-commit"
chmod +x "$REPO_ROOT/.git/hooks/pre-commit"
echo "Pre-commit hook installed."
