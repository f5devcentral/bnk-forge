#!/bin/bash
# =============================================================================
# Post-Create — runs ONCE after the devcontainer is first built
# =============================================================================
# This is where you install dependencies. It won't run again on subsequent
# container starts (use post-start.sh for that).
#
# EDIT THIS FILE to match your project layout. The blocks below are common
# patterns; uncomment and adapt the ones you need, delete the rest.

set -e

echo "=== Post-create: installing project dependencies ==="

# ── Python backend ─────────────────────────────────────────────────────────
# if [ -f /workspace/backend/requirements.txt ]; then
#   echo "--- pip install backend ---"
#   cd /workspace/backend
#   pip install -r requirements.txt
#   [ -f requirements-dev.txt ] && pip install -r requirements-dev.txt
# fi

# ── Python via pyproject.toml (poetry / pip) ───────────────────────────────
# if [ -f /workspace/pyproject.toml ]; then
#   cd /workspace
#   pip install -e .
# fi

# ── Node.js frontend ───────────────────────────────────────────────────────
# if [ -f /workspace/frontend/package-lock.json ]; then
#   echo "--- npm ci frontend ---"
#   cd /workspace/frontend
#   npm ci
# fi

# ── pnpm monorepo ──────────────────────────────────────────────────────────
# if [ -f /workspace/pnpm-lock.yaml ]; then
#   cd /workspace
#   corepack enable && corepack prepare pnpm@latest --activate
#   pnpm install --frozen-lockfile
# fi

# ── Pre-commit hooks ───────────────────────────────────────────────────────
# if [ -f /workspace/.pre-commit-config.yaml ]; then
#   cd /workspace
#   pip install pre-commit && pre-commit install
# fi

echo ""
echo "=== Post-create complete ==="
echo "Next start will run post-start.sh to launch dev servers / run migrations."
