#!/bin/bash
# =============================================================================
# Post-Start — runs EVERY time the devcontainer starts
# =============================================================================
# Migrations, seed data, and launching dev servers in the background.
#
# Intentionally NO `set -e` — a failed migration should not prevent dev servers
# from starting. Each block logs its own success/failure.
#
# Background-process pattern:
#   nohup <cmd> < /dev/null > /tmp/<name>.log 2>&1 &
#   disown
#
# The `< /dev/null` is important — some tools (Vite, nodemon) exit immediately
# if stdin isn't attached to something. `disown` detaches the job so it
# survives when the post-start script exits.

echo "=== Post-start ==="

# ── Wait for Postgres (belt-and-braces — depends_on already handles this) ──
# The devcontainer service already waits on postgres health via depends_on,
# so this is usually redundant. Kept here as an example if you need finer
# control (e.g., running an init script before the backend boots).
#
# until pg_isready -h postgres -U devuser >/dev/null 2>&1; do
#   echo "Waiting for postgres..."
#   sleep 1
# done

# ── Database migrations ────────────────────────────────────────────────────
# Alembic (SQLAlchemy):
# cd /workspace/backend
# python -m alembic upgrade head || echo "WARNING: alembic migration failed"
#
# Django:
# cd /workspace/backend
# python manage.py migrate --noinput || echo "WARNING: django migrate failed"

# ── Start backend (FastAPI / Django / Flask / ...) ─────────────────────────
# FastAPI via uvicorn:
# echo "=== Starting backend on :8000 ==="
# cd /workspace/backend
# nohup python -m uvicorn main:app \
#     --host 0.0.0.0 --port 8000 --reload \
#     < /dev/null > /tmp/backend.log 2>&1 &
# disown
# echo "  PID: $! — logs: tail -f /tmp/backend.log"

# ── Start frontend (Vite / Next.js / CRA) ──────────────────────────────────
# Vite (React/Vue/Svelte):
# echo "=== Starting frontend on :8080 ==="
# cd /workspace/frontend
# nohup node_modules/.bin/vite --host 0.0.0.0 --port 8080 \
#     < /dev/null > /tmp/frontend.log 2>&1 &
# disown
# echo "  PID: $! — logs: tail -f /tmp/frontend.log"

# Next.js:
# cd /workspace/frontend
# nohup npm run dev -- --hostname 0.0.0.0 --port 3000 \
#     < /dev/null > /tmp/frontend.log 2>&1 &
# disown

# ── Verify services bound their ports ──────────────────────────────────────
# sleep 2
# for port in 8000 8080; do
#   if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
#     echo "  :${port} UP"
#   else
#     echo "  :${port} FAILED — check /tmp/*.log"
#   fi
# done

echo ""
echo "=== Post-start complete ==="
