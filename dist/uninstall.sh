#!/usr/bin/env bash
# =============================================================================
# BNK Forge — Uninstaller
# =============================================================================
#
# Usage:
#   ./uninstall.sh                  # Stop containers, keep data volumes
#   ./uninstall.sh --purge          # Stop containers AND delete all data
#   ./uninstall.sh --help           # Show help
#
# This script stops all BNK Forge containers and optionally removes volumes.
#
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PURGE=false
FORCE=false

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge|-p)
      PURGE=true
      shift
      ;;
    --force|-f)
      FORCE=true
      shift
      ;;
    --help|-h)
      echo "BNK Forge Uninstaller"
      echo ""
      echo "Usage:"
      echo "  ./uninstall.sh            Stop containers (keeps data volumes)"
      echo "  ./uninstall.sh --purge    Stop containers AND delete all data"
      echo "  ./uninstall.sh --force    Skip confirmation prompts"
      echo ""
      echo "Options:"
      echo "  --purge, -p    Remove all volumes (database, projects, keys, etc.)"
      echo "  --force, -f    Skip confirmation prompts"
      echo "  --help, -h     Show this help"
      echo ""
      echo "Volumes that will be removed with --purge:"
      echo "  - postgres_data       PostgreSQL database"
      echo "  - postgres_backups    Database backups"
      echo "  - redis_data          Redis cache"
      echo "  - bnk-forge-data      Projects and configurations"
      echo "  - bnk-forge-keys      SSH keys and credentials"
      echo "  - state_data          OpenTofu state files"
      echo "  - workspace_data      OpenTofu workspaces"
      echo "  - helm_cache          Helm repository cache"
      echo "  - helm_config         Helm configuration"
      echo "  - helm_charts         Uploaded Helm charts"
      echo "  - module_catalog      Module library cache"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Run: ./uninstall.sh --help"
      exit 1
      ;;
  esac
done

cd "$SCRIPT_DIR"

# ── Determine compose command ────────────────────────────────────────────────
# Check if local overlay exists and was used
if [ -f docker-compose.local.yml ]; then
  COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.local.yml"
else
  COMPOSE_CMD="docker compose"
fi

# ── Show current status ──────────────────────────────────────────────────────
echo ""
echo "========================================="
echo "  BNK Forge — Uninstall"
echo "========================================="
echo ""
echo "  Version: $(cat VERSION 2>/dev/null || echo 'unknown')"
echo ""

# Check if any containers are running
RUNNING=$($COMPOSE_CMD ps --status running -q 2>/dev/null | wc -l | tr -d ' ')
if [ "$RUNNING" -gt 0 ]; then
  echo "  Running containers: $RUNNING"
  $COMPOSE_CMD ps --status running 2>/dev/null || true
else
  echo "  No containers running"
fi
echo ""

# ── Confirmation ─────────────────────────────────────────────────────────────
if [ "$PURGE" = true ]; then
  echo -e "${RED}⚠️  WARNING: --purge will delete ALL data including:${NC}"
  echo "     • PostgreSQL database (projects, users, settings)"
  echo "     • Redis cache"
  echo "     • Project files and configurations"
  echo "     • SSH keys and credentials"
  echo "     • OpenTofu state files (CRITICAL for infra management!)"
  echo "     • Helm charts and cache"
  echo ""
  
  if [ "$FORCE" = false ]; then
    echo -e "${YELLOW}This action is IRREVERSIBLE. Data cannot be recovered.${NC}"
    echo ""
    read -p "Type 'DELETE ALL DATA' to confirm: " confirmation
    if [ "$confirmation" != "DELETE ALL DATA" ]; then
      echo ""
      echo "Uninstall cancelled."
      exit 1
    fi
  fi
fi

if [ "$FORCE" = false ] && [ "$PURGE" = false ]; then
  read -p "Stop all BNK Forge containers? [y/N] " confirm
  if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Uninstall cancelled."
    exit 1
  fi
fi

# ── Stop containers ──────────────────────────────────────────────────────────
echo ""
echo "=== Stopping containers ==="
$COMPOSE_CMD down 2>/dev/null || echo "  No containers to stop"
echo -e "  ${GREEN}✓${NC} Containers stopped"

# ── Remove volumes if --purge ────────────────────────────────────────────────
if [ "$PURGE" = true ]; then
  echo ""
  echo "=== Removing volumes ==="
  
  # Get the project name — must match what docker compose uses (volumes are <project>_<name>).
  # Compose reads COMPOSE_PROJECT_NAME from .env; otherwise it normalizes the
  # directory name (lowercase, invalid chars like '.' stripped).
  PROJECT_NAME=$(grep -E '^COMPOSE_PROJECT_NAME=' .env 2>/dev/null | head -1 | cut -d= -f2)
  if [ -z "$PROJECT_NAME" ]; then
    PROJECT_NAME=$(basename "$SCRIPT_DIR" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')
  fi
  
  # List of volumes defined in docker-compose.yml
  VOLUMES=(
    "postgres_data"
    "postgres_backups"
    "redis_data"
    "bnk-forge-data"
    "bnk-forge-keys"
    "state_data"
    "workspace_data"
    "helm_cache"
    "helm_config"
    "helm_charts"
    "module_catalog"
  )
  
  for vol in "${VOLUMES[@]}"; do
    FULL_VOL="${PROJECT_NAME}_${vol}"
    if docker volume inspect "$FULL_VOL" &>/dev/null; then
      docker volume rm "$FULL_VOL" 2>/dev/null && \
        echo "  Removed: $FULL_VOL" || \
        echo "  Failed to remove: $FULL_VOL (may be in use)"
    fi
  done
  
  echo -e "  ${GREEN}✓${NC} Volumes removed"
fi

# ── Remove images (optional, commented out) ──────────────────────────────────
# Uncomment to also remove pulled images:
# echo ""
# echo "=== Removing images ==="
# docker images | grep bnk-forge | awk '{print $3}' | xargs docker rmi 2>/dev/null || true

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "========================================="
if [ "$PURGE" = true ]; then
  echo -e "  ${GREEN}✅ BNK Forge completely removed${NC}"
  echo ""
  echo "  All containers stopped and volumes deleted."
  echo "  To reinstall: ./install.sh"
else
  echo -e "  ${GREEN}✅ BNK Forge stopped${NC}"
  echo ""
  echo "  Containers stopped. Data volumes preserved."
  echo "  To restart:   docker compose up -d"
  echo "  To reinstall: ./install.sh"
  echo "  To purge all: ./uninstall.sh --purge"
fi
echo "========================================="
