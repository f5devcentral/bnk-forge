#!/bin/bash
#
# BNK-Forge Upgrade Script
# Rebuilds containers and restarts services (non-destructive - keeps volumes)
#
# Usage:
#   ./upgrade.sh              - pull + rebuild + restart
#   ./upgrade.sh --local      - rebuild + restart (no git pull)
#   ./upgrade.sh --no-cache   - rebuild without Docker cache (SLOW - 10+ min)
#   ./upgrade.sh --allow-non-main     - allow running from non-staging branch
#   ./upgrade.sh --allow-same-version - allow commit upgrade without VERSION bump
#
# Phase markers (##PHASE:xxx) are parsed by the backend to report structured
# progress to the frontend UI. Do not remove or rename them.
#
# Exit codes:
#   0 — upgrade succeeded, all services healthy
#   1 — docker compose not found
#   2 — docker compose build failed
#   3 — docker compose up failed
#   4 — database migration failed
#   5 — health check failed (services not healthy after timeout)
#   6 — preflight policy check failed
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
LOCAL_ONLY=false
NO_CACHE=false
ALLOW_DIRTY=false
ALLOW_DIVERGED=false
SKIP_DISK_CHECK=false
ALLOW_NON_MAIN=false
ALLOW_SAME_VERSION=false

for arg in "$@"; do
    case $arg in
        --local|-l) LOCAL_ONLY=true ;;
        --no-cache) NO_CACHE=true ;;
        --allow-dirty) ALLOW_DIRTY=true ;;
        --allow-diverged) ALLOW_DIVERGED=true ;;
        --skip-disk-check) SKIP_DISK_CHECK=true ;;
        --allow-non-main) ALLOW_NON_MAIN=true ;;
        --allow-same-version) ALLOW_SAME_VERSION=true ;;
    esac
done

echo "##PHASE:start"
echo "BNK-Forge Upgrade"
echo "=================================="
echo ""

# ---------------------------------------------------------------------------
# Detect docker compose
# ---------------------------------------------------------------------------
if docker compose version &>/dev/null; then
    COMPOSE="docker compose"
elif docker-compose version &>/dev/null; then
    COMPOSE="docker-compose"
else
    echo "##PHASE:failed"
    echo "Error: Docker Compose not found."
    exit 1
fi

# Record current version for comparison
OLD_VERSION=$(cat VERSION 2>/dev/null || echo "unknown")
OLD_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "Current version: ${OLD_VERSION} (commit: ${OLD_COMMIT})"

# ---------------------------------------------------------------------------
# Phase 0: Preflight policy checks
# ---------------------------------------------------------------------------
echo ""
echo "##PHASE:preflight"
echo "Running preflight checks..."

# Check working tree cleanliness
if [ "$ALLOW_DIRTY" = false ]; then
    if [ -n "$(git status --porcelain 2>/dev/null || true)" ]; then
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: Working tree is not clean."
        echo "  Commit/stash changes before upgrade, or rerun with --allow-dirty"
        echo "  Command: git status --short"
        exit 6
    fi
    echo "  ✓ Git working tree clean"
else
    echo "  ⚠ Skipping dirty-tree check (--allow-dirty)"
fi

# Check branch policy (server upgrades should run from staging unless explicitly overridden)
if [ "$ALLOW_NON_MAIN" = false ]; then
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
    if [ "$CURRENT_BRANCH" != "staging" ]; then
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: Upgrade must run from staging branch (current: ${CURRENT_BRANCH})."
        echo "  Switch to staging and sync first: git checkout staging && git pull --ff-only"
        echo "  Or rerun with --allow-non-main (not recommended)"
        exit 6
    fi
    echo "  ✓ Branch policy check passed (staging)"
else
    echo "  ⚠ Skipping staging-branch check (--allow-non-main)"
fi

# Check disk policy (critical threshold should fail)
if [ "$SKIP_DISK_CHECK" = false ] && [ -x "./scripts/check-disk-space.sh" ]; then
    set +e
    ./scripts/check-disk-space.sh
    DISK_RC=$?
    set -e
    if [ $DISK_RC -eq 2 ]; then
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: Disk usage above critical threshold (>=85%)."
        echo "  Run: ./scripts/docker-cleanup.sh"
        echo "  Or rerun with --skip-disk-check (not recommended)"
        exit 6
    elif [ $DISK_RC -eq 1 ]; then
        echo "  ⚠ Disk usage warning (>=70%). Proceeding."
    else
        echo "  ✓ Disk usage check passed"
    fi
elif [ "$SKIP_DISK_CHECK" = true ]; then
    echo "  ⚠ Skipping disk check (--skip-disk-check)"
else
    echo "  ⚠ Disk check script not found or not executable: ./scripts/check-disk-space.sh"
fi

# ---------------------------------------------------------------------------
# Phase 1: Pull latest code
# ---------------------------------------------------------------------------
echo ""
echo "##PHASE:pull"

if [ "$LOCAL_ONLY" = false ]; then
    echo "Pulling latest code..."
    # gh auth is optional — don't fail if not available
    gh auth setup-git 2>/dev/null || true

    if git pull --ff-only; then
        NEW_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
        if [ "$OLD_COMMIT" != "$NEW_COMMIT" ]; then
            echo "  Updated: ${OLD_COMMIT} -> ${NEW_COMMIT}"
            git diff --stat "${OLD_COMMIT}..${NEW_COMMIT}" 2>/dev/null | head -10
        else
            echo "  Already up to date."
        fi
    else
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: git pull --ff-only failed."
        echo "  Resolve branch divergence first, then retry: git pull --ff-only"
        exit 6
    fi
else
    echo "Local mode: skipping git pull"
fi

# Sync policy check: in --local mode, HEAD must match origin/staging unless overridden.
# In pull mode, enforce this after pull to ensure target commit was actually reached.
if [ "$ALLOW_DIVERGED" = false ]; then
    git fetch origin staging >/dev/null 2>&1 || true
    HEAD_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")
    ORIGIN_STAGING_COMMIT=$(git rev-parse origin/staging 2>/dev/null || echo "unknown")
    if [ "$HEAD_COMMIT" != "$ORIGIN_STAGING_COMMIT" ]; then
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: Local HEAD does not match origin/staging."
        echo "  HEAD:        ${HEAD_COMMIT}"
        echo "  origin/staging: ${ORIGIN_STAGING_COMMIT}"
        if [ "$LOCAL_ONLY" = true ]; then
            echo "  Local mode requires repo to already be synced."
            echo "  Run: git pull --ff-only"
        else
            echo "  Pull did not converge to origin/staging. Resolve and retry."
        fi
        echo "  Or rerun with --allow-diverged (not recommended)"
        exit 6
    fi
    echo "  ✓ Git HEAD aligned with origin/staging"
else
    echo "  ⚠ Skipping origin/staging sync check (--allow-diverged)"
fi

NEW_VERSION=$(cat VERSION 2>/dev/null || echo "unknown")
echo "  Target version: ${NEW_VERSION}"

# Version policy check (D17): if commit changed during this upgrade, VERSION should also change.
CURRENT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
if [ "$ALLOW_SAME_VERSION" = false ] && [ "$OLD_COMMIT" != "$CURRENT_COMMIT" ] && [ "$OLD_VERSION" = "$NEW_VERSION" ]; then
    echo ""
    echo "##PHASE:failed"
    echo "ERROR: Commit changed but VERSION was not bumped (policy D17)."
    echo "  Old commit: ${OLD_COMMIT}"
    echo "  New commit: ${CURRENT_COMMIT}"
    echo "  Version:    ${OLD_VERSION}"
    echo "  Bump VERSION (and frontend-v2/package.json if required), commit, and retry."
    echo "  Or rerun with --allow-same-version (not recommended)"
    exit 6
fi
if [ "$OLD_COMMIT" != "$CURRENT_COMMIT" ] && [ "$OLD_VERSION" = "$NEW_VERSION" ]; then
    echo "  ⚠ Version unchanged while commit changed (--allow-same-version)"
elif [ "$OLD_COMMIT" != "$CURRENT_COMMIT" ]; then
    echo "  ✓ Version policy check passed (${OLD_VERSION} -> ${NEW_VERSION})"
fi

# ---------------------------------------------------------------------------
# Phase 2: Build containers (SMART BUILD — only rebuild what changed)
# ---------------------------------------------------------------------------
echo ""
echo "##PHASE:build"

# Detect what changed between old commit and current HEAD
BACKEND_CHANGED=false
FRONTEND_CHANGED=false
PROXY_CHANGED=false
REQUIREMENTS_CHANGED=false

if [ "$OLD_COMMIT" != "unknown" ] && [ "$CURRENT_COMMIT" != "unknown" ] && [ "$OLD_COMMIT" != "$CURRENT_COMMIT" ]; then
    echo "Detecting changes: ${OLD_COMMIT} → ${CURRENT_COMMIT}..."
    CHANGED_FILES=$(git diff --name-only "$OLD_COMMIT" "$CURRENT_COMMIT" 2>/dev/null || echo "")
    
    if [ -n "$CHANGED_FILES" ]; then
        # Check requirements.txt changes (triggers full backend rebuild)
        if echo "$CHANGED_FILES" | grep -q "^backend/requirements"; then
            REQUIREMENTS_CHANGED=true
            BACKEND_CHANGED=true
            echo "  ⚠ requirements.txt changed — will rebuild all backend targets"
        fi
        
        # Check backend changes
        if echo "$CHANGED_FILES" | grep -q "^backend/"; then
            BACKEND_CHANGED=true
        fi
        
        # Check frontend changes
        if echo "$CHANGED_FILES" | grep -q "^frontend-v2/"; then
            FRONTEND_CHANGED=true
        fi
        
        # Check proxy changes
        if echo "$CHANGED_FILES" | grep -q "^proxy/"; then
            PROXY_CHANGED=true
        fi
        
        # VERSION changes affect frontend (baked in at build time)
        if echo "$CHANGED_FILES" | grep -q "^VERSION$"; then
            FRONTEND_CHANGED=true
        fi
        
        # docker-compose changes require full rebuild
        if echo "$CHANGED_FILES" | grep -q "^docker-compose"; then
            echo "  ⚠ docker-compose.yml changed — will rebuild all"
            BACKEND_CHANGED=true
            FRONTEND_CHANGED=true
            PROXY_CHANGED=true
        fi
    fi
else
    # No commit comparison possible — rebuild everything
    echo "  No commit comparison available — will rebuild all"
    BACKEND_CHANGED=true
    FRONTEND_CHANGED=true
    PROXY_CHANGED=true
fi

# Build affected services
SERVICES_TO_BUILD=""
SERVICES_TO_RESTART=""

if [ "$BACKEND_CHANGED" = true ]; then
    echo "  → Backend changed"
    SERVICES_TO_BUILD="$SERVICES_TO_BUILD backend"
    SERVICES_TO_RESTART="backend celery-worker celery-worker-2 celery-beat"
    if [ "$REQUIREMENTS_CHANGED" = true ]; then
        # Requirements change means we need to rebuild worker too (different base)
        SERVICES_TO_BUILD="$SERVICES_TO_BUILD celery-worker celery-beat"
    fi
fi

if [ "$FRONTEND_CHANGED" = true ]; then
    echo "  → Frontend changed"
    SERVICES_TO_BUILD="$SERVICES_TO_BUILD frontend"
    SERVICES_TO_RESTART="$SERVICES_TO_RESTART frontend"
fi

if [ "$PROXY_CHANGED" = true ]; then
    echo "  → Proxy changed"
    SERVICES_TO_BUILD="$SERVICES_TO_BUILD proxy"
    SERVICES_TO_RESTART="$SERVICES_TO_RESTART proxy"
fi

# Deduplicate and trim
SERVICES_TO_BUILD=$(echo "$SERVICES_TO_BUILD" | tr ' ' '\n' | sort -u | tr '\n' ' ' | xargs)
SERVICES_TO_RESTART=$(echo "$SERVICES_TO_RESTART" | tr ' ' '\n' | sort -u | tr '\n' ' ' | xargs)

if [ -z "$SERVICES_TO_BUILD" ]; then
    echo "  ✓ No code changes detected — skipping build"
else
    echo ""
    echo "Building: $SERVICES_TO_BUILD"
    BUILD_START=$(date +%s)
    
    if [ "$NO_CACHE" = true ]; then
        echo "  (no-cache mode — this will be SLOW)"
        # shellcheck disable=SC2086
        if ! $COMPOSE build --no-cache $SERVICES_TO_BUILD; then
            echo ""
            echo "##PHASE:failed"
            echo "ERROR: Docker build failed. No changes have been applied."
            echo "  The running system is still on version ${OLD_VERSION}."
            echo "  Check the build output above for errors."
            exit 2
        fi
    else
        # shellcheck disable=SC2086
        if ! $COMPOSE build $SERVICES_TO_BUILD; then
            echo ""
            echo "##PHASE:failed"
            echo "ERROR: Docker build failed. No changes have been applied."
            echo "  The running system is still on version ${OLD_VERSION}."
            echo "  Check the build output above for errors."
            exit 2
        fi
    fi
    
    BUILD_END=$(date +%s)
    BUILD_ELAPSED=$((BUILD_END - BUILD_START))
    echo "  ✓ Build complete in ${BUILD_ELAPSED}s"
fi

# ---------------------------------------------------------------------------
# Phase 3: Restart services (only restart what was rebuilt)
# ---------------------------------------------------------------------------
echo ""
echo "##PHASE:restart"

# The container-image engine attaches artifact steps to this dedicated bridge
# network. Compose does not create it (no service references it — under host
# networking none can), so an upgraded server would otherwise pick up the new
# runner with no network and fail every artifact step with "network not found".
# Subnet resolution (explicit override, "auto", or auto-detection against host
# routes/docker networks) lives in scripts/artifact_network.sh — see issue #422.
./scripts/artifact_network.sh ensure

if [ -z "$SERVICES_TO_RESTART" ]; then
    echo "No services to restart — skipping."
else
    echo "Restarting: $SERVICES_TO_RESTART"
    # shellcheck disable=SC2086
    if ! $COMPOSE up -d --force-recreate $SERVICES_TO_RESTART; then
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: Failed to restart services."
        echo "  Some containers may be in a bad state."
        echo "  Run: docker compose ps"
        exit 3
    fi
fi

# Also remove orphans (containers from removed services)
$COMPOSE up -d --remove-orphans 2>/dev/null || true

# Wait a few seconds for containers to initialize before running commands in them
echo "  Waiting for containers to initialize..."
sleep 5

# Fix volume permissions (non-fatal — warn but don't abort)
echo "  Fixing volume permissions..."
if ! docker exec -u root bnk-forge-backend chown -R bnkforge:bnkforge /app/state /app/keys /app/projects /app/workspaces /app/helm_charts 2>/dev/null; then
    echo "  WARNING: Could not fix volume permissions. This may cause issues."
    echo "  You can fix manually: docker exec -u root bnk-forge-backend chown -R bnkforge:bnkforge /app/state /app/keys /app/projects /app/workspaces /app/helm_charts"
fi

# ---------------------------------------------------------------------------
# Phase 4: Database migrations
# ---------------------------------------------------------------------------
echo ""
echo "##PHASE:migrate"
echo "Running database migrations..."

# Capture migration output and check for actual errors
MIGRATION_OUTPUT=$(docker exec bnk-forge-backend alembic upgrade head 2>&1) || {
    MIGRATION_EXIT=$?
    echo "  ERROR: Database migration failed (exit code: ${MIGRATION_EXIT})"
    echo "  Migration output:"
    echo "${MIGRATION_OUTPUT//$'\n'/$'\n'    }"
    echo ""
    echo "##PHASE:failed"
    echo "ERROR: Database migration failed. Services are running but the database"
    echo "  may be in an inconsistent state. Check the migration output above."
    echo "  You may need to fix the migration manually and re-run:"
    echo "    docker exec bnk-forge-backend alembic upgrade head"
    exit 4
}

# Show non-INFO migration output (filtered for readability)
echo "${MIGRATION_OUTPUT}" | grep -v "^INFO" | grep -v "^$" | head -20 || true
echo "  Migrations applied successfully."

# ---------------------------------------------------------------------------
# Phase 5: Health verification
# ---------------------------------------------------------------------------
echo ""
echo "##PHASE:verify"
echo "Waiting for services to be healthy..."

HEALTH_OK=false
MAX_WAIT=60
WAITED=0

while [ $WAITED -lt $MAX_WAIT ]; do
    HEALTH_RESPONSE=$(docker exec bnk-forge-backend curl -sf http://localhost:8000/api/system/health 2>/dev/null) || true

    if [ -n "$HEALTH_RESPONSE" ]; then
        # Check if all services report healthy
        if echo "$HEALTH_RESPONSE" | grep -q '"status":"healthy"'; then
            HEALTH_OK=true
            break
        fi
    fi

    WAITED=$((WAITED + 2))
    echo "  Waiting... (${WAITED}s / ${MAX_WAIT}s)"
    sleep 2
done

if [ "$HEALTH_OK" = false ]; then
    echo ""
    echo "##PHASE:failed"
    echo "ERROR: Services did not become healthy within ${MAX_WAIT} seconds."
    echo ""
    echo "  Service status:"
    $COMPOSE ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || $COMPOSE ps
    echo ""
    echo "  Last health response: ${HEALTH_RESPONSE:-none}"
    echo ""
    echo "  Troubleshooting:"
    echo "    docker compose logs backend --tail 50"
    echo "    docker compose logs celery-worker --tail 50"
    exit 5
fi

# Gate 1: critical services must be running and healthy
echo "  Verifying container health gate..."
CRITICAL_SERVICES=(
  bnk-forge-backend
  bnk-forge-frontend
  bnk-forge-proxy
  bnk-forge-postgres
  bnk-forge-redis
  bnk-forge-celery-worker
  bnk-forge-celery-worker-2
  bnk-forge-celery-beat
)

for svc in "${CRITICAL_SERVICES[@]}"; do
    if ! docker inspect "$svc" >/dev/null 2>&1; then
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: Required container not found: $svc"
        printf '  Troubleshoot: docker ps -a --format "table {{.Names}}\t{{.Status}}"\n'
        exit 5
    fi

    STATUS=$(docker inspect -f '{{.State.Status}}' "$svc" 2>/dev/null || echo "unknown")
    HEALTH=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$svc" 2>/dev/null || echo "unknown")

    if [ "$STATUS" != "running" ]; then
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: Container is not running: $svc (status=$STATUS)"
        echo "  Troubleshoot: docker logs $svc --tail 50"
        exit 5
    fi

    if [ "$HEALTH" != "none" ] && [ "$HEALTH" != "healthy" ]; then
        echo ""
        echo "##PHASE:failed"
        echo "ERROR: Container health not healthy: $svc (health=$HEALTH)"
        echo "  Troubleshoot: docker logs $svc --tail 50"
        exit 5
    fi
done
echo "  ✓ Container health gate passed"

# Gate 2: backend logs sanity check
echo "  Verifying backend log sanity gate..."
BACKEND_LOG_TAIL=$(docker logs bnk-forge-backend --tail 50 2>&1 || true)
if echo "$BACKEND_LOG_TAIL" | grep -E 'Traceback \(most recent call last\)|ModuleNotFoundError|ImportError|SyntaxError|ERROR: +Exception in ASGI application' >/dev/null 2>&1; then
    echo ""
    echo "##PHASE:failed"
    echo "ERROR: Backend logs contain fatal startup patterns."
    echo "  Check: docker logs bnk-forge-backend --tail 200"
    exit 5
fi
echo "  ✓ Backend log sanity gate passed"

# Gate 3: health endpoint gate
echo "  Verifying health endpoint gate..."
HEALTH_RESPONSE=$(docker exec bnk-forge-backend curl -sf http://localhost:8000/api/system/health 2>/dev/null) || true
if [ -z "$HEALTH_RESPONSE" ] || ! echo "$HEALTH_RESPONSE" | grep -q '"status":"healthy"'; then
    echo ""
    echo "##PHASE:failed"
    echo "ERROR: Health endpoint check failed after restart."
    echo "  Check: docker exec bnk-forge-backend curl -s http://localhost:8000/api/system/health"
    exit 5
fi
echo "  ✓ Health endpoint gate passed"

# ---------------------------------------------------------------------------
# Phase 6: Complete
# ---------------------------------------------------------------------------
echo ""
echo "##PHASE:complete"
echo "=================================="
echo "Upgrade complete!"
echo ""
NEW_VERSION=$(cat VERSION 2>/dev/null || echo "unknown")
NEW_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "  Version: ${OLD_VERSION} -> ${NEW_VERSION}"
echo "  Commit:  ${OLD_COMMIT} -> ${NEW_COMMIT}"
echo ""
$COMPOSE ps --format "table {{.Name}}\t{{.Status}}" 2>/dev/null || $COMPOSE ps
echo ""
echo "  All services healthy."
