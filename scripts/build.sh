#!/bin/bash
# =============================================================================
# BNK-Forge Smart Build Script (OPT-001)
# =============================================================================
#
# Detects what changed since last build and rebuilds only affected containers.
# Uses git diff to determine which directories have changes.
#
# Usage:
#   ./scripts/build.sh              # Smart build (only changed services)
#   ./scripts/build.sh --all        # Build all services
#   ./scripts/build.sh --deploy     # Smart build + restart + health check
#   ./scripts/build.sh --clean      # Force rebuild without cache (rare)
#
# How it works:
#   1. Compares current working tree against the commit hash stored in .build-hash
#   2. If backend/ changed → rebuild backend (api) + celery containers
#   3. If frontend-v2/ changed → rebuild frontend
#   4. If proxy/ changed → rebuild proxy
#   5. If requirements.txt changed → rebuild all backend targets
#   6. If nothing changed → skip build entirely
#
# IMPORTANT: This script NEVER uses --no-cache. BuildKit cache mounts in the
# Dockerfiles handle pip/npm caching. Docker layer cache handles code changes.
# The only time you need --no-cache is when upgrading tool versions in the
# Dockerfile itself (OpenTofu, Helm, kubectl) — use --clean for that.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure BuildKit is enabled
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

BUILD_HASH_FILE=".build-hash"
DEPLOY=false
CLEAN=false
BUILD_ALL=false

# Parse arguments
for arg in "$@"; do
    case $arg in
        --deploy)  DEPLOY=true ;;
        --clean)   CLEAN=true ;;
        --all)     BUILD_ALL=true ;;
        --help|-h)
            echo "Usage: ./scripts/build.sh [options]"
            echo ""
            echo "Options:"
            echo "  --deploy    Build + restart containers + verify health"
            echo "  --all       Build all services (not just changed ones)"
            echo "  --clean     Force rebuild without cache (slow, use sparingly)"
            echo "  --help      Show this help"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            echo "Use --help for usage"
            exit 1
            ;;
    esac
done

# Detect docker compose command
if docker compose version &>/dev/null; then
    COMPOSE="docker compose"
elif docker-compose version &>/dev/null; then
    COMPOSE="docker-compose"
else
    echo -e "${RED}Error: docker compose not found${NC}"
    exit 1
fi

echo -e "${BLUE}BNK-Forge Smart Build${NC}"
echo "=================================="

# Clean build — just run docker compose build --no-cache
if [ "$CLEAN" = true ]; then
    echo -e "${YELLOW}Clean rebuild (no cache) — this will take several minutes...${NC}"
    $COMPOSE build --no-cache
    git rev-parse HEAD > "$BUILD_HASH_FILE" 2>/dev/null || true
    echo -e "${GREEN}✓ Clean rebuild complete${NC}"
    if [ "$DEPLOY" = true ]; then
        echo ""
        exec "$0" --deploy
    fi
    exit 0
fi

# Build all — skip change detection
if [ "$BUILD_ALL" = true ]; then
    echo -e "${YELLOW}Building all services...${NC}"
    $COMPOSE build
    git rev-parse HEAD > "$BUILD_HASH_FILE" 2>/dev/null || true
    echo -e "${GREEN}✓ All services built${NC}"
    if [ "$DEPLOY" = true ]; then
        echo ""
        # Fall through to deploy section below
        :
    else
        exit 0
    fi
fi

# Smart build — detect changes
if [ "$BUILD_ALL" = false ]; then
    # Get the last build commit hash
    LAST_HASH=""
    if [ -f "$BUILD_HASH_FILE" ]; then
        LAST_HASH=$(cat "$BUILD_HASH_FILE")
    fi

    # Determine what changed
    BACKEND_CHANGED=false
    FRONTEND_CHANGED=false
    PROXY_CHANGED=false
    REQUIREMENTS_CHANGED=false

    if [ -z "$LAST_HASH" ]; then
        # No previous build — build everything
        echo -e "${YELLOW}No previous build hash found — building all services${NC}"
        BACKEND_CHANGED=true
        FRONTEND_CHANGED=true
        PROXY_CHANGED=true
    else
        echo "  Last build: ${LAST_HASH:0:8}"
        echo "  Checking for changes..."

        # Check for uncommitted changes (working tree + staged)
        # Also check against last build hash for committed changes
        CHANGED_FILES=""

        # Uncommitted changes (staged + unstaged)
        UNCOMMITTED=$(git diff --name-only HEAD 2>/dev/null || true)
        STAGED=$(git diff --name-only --cached 2>/dev/null || true)

        # Committed changes since last build
        COMMITTED=""
        if git cat-file -t "$LAST_HASH" &>/dev/null; then
            COMMITTED=$(git diff --name-only "$LAST_HASH" HEAD 2>/dev/null || true)
        fi

        CHANGED_FILES=$(echo -e "${UNCOMMITTED}\n${STAGED}\n${COMMITTED}" | sort -u | grep -v '^$' || true)

        if [ -z "$CHANGED_FILES" ]; then
            echo -e "${GREEN}  No changes detected — nothing to build${NC}"
            if [ "$DEPLOY" = false ]; then
                exit 0
            fi
        else
            # Categorize changes
            if echo "$CHANGED_FILES" | grep -q "^backend/requirements"; then
                REQUIREMENTS_CHANGED=true
                BACKEND_CHANGED=true
                echo -e "  ${YELLOW}⚠ requirements.txt changed — rebuilding all backend targets${NC}"
            fi

            if echo "$CHANGED_FILES" | grep -q "^backend/"; then
                BACKEND_CHANGED=true
            fi

            if echo "$CHANGED_FILES" | grep -q "^frontend-v2/"; then
                FRONTEND_CHANGED=true
            fi

            if echo "$CHANGED_FILES" | grep -q "^proxy/"; then
                PROXY_CHANGED=true
            fi

            if echo "$CHANGED_FILES" | grep -q "^VERSION$"; then
                FRONTEND_CHANGED=true  # VERSION is baked into frontend
            fi

            if echo "$CHANGED_FILES" | grep -q "^docker-compose"; then
                echo -e "  ${YELLOW}⚠ docker-compose.yml changed — building all${NC}"
                BACKEND_CHANGED=true
                FRONTEND_CHANGED=true
                PROXY_CHANGED=true
            fi
        fi
    fi

    # Build affected services
    SERVICES_TO_BUILD=""
    SERVICES_TO_RESTART=""

    if [ "$BACKEND_CHANGED" = true ]; then
        echo -e "  ${BLUE}→ Backend changed${NC}"
        SERVICES_TO_BUILD="$SERVICES_TO_BUILD backend"
        SERVICES_TO_RESTART="$SERVICES_TO_RESTART backend celery-worker celery-worker-2 celery-beat"
        if [ "$REQUIREMENTS_CHANGED" = true ]; then
            SERVICES_TO_BUILD="$SERVICES_TO_BUILD celery-worker celery-beat"
        fi
    fi

    if [ "$FRONTEND_CHANGED" = true ]; then
        echo -e "  ${BLUE}→ Frontend changed${NC}"
        SERVICES_TO_BUILD="$SERVICES_TO_BUILD frontend"
        SERVICES_TO_RESTART="$SERVICES_TO_RESTART frontend"
    fi

    if [ "$PROXY_CHANGED" = true ]; then
        echo -e "  ${BLUE}→ Proxy changed${NC}"
        SERVICES_TO_BUILD="$SERVICES_TO_BUILD proxy"
        SERVICES_TO_RESTART="$SERVICES_TO_RESTART proxy"
    fi

    if [ -n "$SERVICES_TO_BUILD" ]; then
        echo ""
        echo -e "${BLUE}Building:${NC}$SERVICES_TO_BUILD"
        START_TIME=$(date +%s)
        $COMPOSE build $SERVICES_TO_BUILD
        END_TIME=$(date +%s)
        ELAPSED=$((END_TIME - START_TIME))
        echo -e "${GREEN}✓ Build complete in ${ELAPSED}s${NC}"
    fi

    # Update build hash
    git rev-parse HEAD > "$BUILD_HASH_FILE" 2>/dev/null || true
fi

# Deploy if requested
if [ "$DEPLOY" = true ]; then
    echo ""
    echo -e "${BLUE}Deploying...${NC}"

    # Determine what to restart
    if [ -z "$SERVICES_TO_RESTART" ]; then
        SERVICES_TO_RESTART="backend frontend celery-worker celery-worker-2 celery-beat proxy"
    fi

    # Artifact steps run on this dedicated bridge network; compose cannot create
    # it (no service references it — under host networking none can), so a
    # deploy that skips it leaves every artifact step failing "network not found".
    # Subnet resolution (explicit override, "auto", or auto-detection against
    # host routes/docker networks) lives in scripts/artifact_network.sh — see
    # issue #422.
    "$SCRIPT_DIR/artifact_network.sh" ensure

    echo "  Restarting:$SERVICES_TO_RESTART"
    $COMPOSE up -d --force-recreate $SERVICES_TO_RESTART

    echo ""
    echo "  Waiting for health checks..."
    HEALTHY=false
    for i in $(seq 1 12); do
        if curl -sf http://localhost:8000/api/system/health > /dev/null 2>&1; then
            HEALTHY=true
            break
        fi
        echo "    Waiting... ($i/12)"
        sleep 5
    done

    if [ "$HEALTHY" = true ]; then
        echo -e "  ${GREEN}✓ Backend healthy${NC}"
    else
        echo -e "  ${RED}✗ Backend did not become healthy within 60s${NC}"
        echo "    Check: docker logs bnk-forge-backend --tail 20"
    fi

    echo ""
    $COMPOSE ps
    echo ""
    echo -e "${GREEN}=========================================${NC}"
    echo -e "${GREEN}  Deploy complete${NC}"
    echo -e "${GREEN}=========================================${NC}"
fi
