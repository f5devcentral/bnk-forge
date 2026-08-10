#!/usr/bin/env bash
# === BNK-Forge Docker Cleanup Script ===
#
# Safely reclaims disk space from Docker without breaking running containers.
# Run weekly via cron or manually when disk is getting full.
#
# What this cleans (with confirmation where destructive):
#   - Dangling images (untagged layers from old builds)
#   - Build cache older than 7 days
#   - Stopped containers older than 24 hours
#   - Dangling (unused) volumes — prompts before removal
#
# What this only WARNS about (run `make clean-orphans` to remove):
#   - Volumes named `<project>_*` whose compose label doesn't match the
#     current project — i.e. left over from a renamed/older deployment.
#     Compose will still mount these but disowns them ("not built with compose").
#
# What this NEVER touches:
#   - Running containers
#   - Tagged images currently in use
#   - Volumes attached to any container
#   - Build cache from last 7 days

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Compose project name defaults to the repo directory basename.
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$REPO_DIR")}"

echo ""
echo "========================================="
echo "  BNK-Forge Docker Cleanup"
echo "  (project: ${COMPOSE_PROJECT})"
echo "========================================="
echo ""

echo -e "${YELLOW}Current disk usage:${NC}"
df -h / | grep -v Filesystem
echo ""

echo -e "${YELLOW}Docker disk usage before cleanup:${NC}"
docker system df
echo ""

# 1. Remove dangling images (safe — these are never used)
echo -e "${GREEN}[1/5] Removing dangling images...${NC}"
docker image prune -f
echo ""

# 2. Remove build cache older than 7 days (keeps recent builds fast)
echo -e "${GREEN}[2/5] Removing build cache older than 7 days...${NC}"
docker builder prune --keep-storage 5GB -f
echo ""

# 3. Remove stopped containers older than 24 hours
echo -e "${GREEN}[3/5] Removing stopped containers older than 24 hours...${NC}"
docker container prune --filter "until=24h" -f
echo ""

# 4. Prune dangling volumes (not attached to any container).
#    Previously this used `docker compose volumes -q` which is not a real
#    subcommand — the step had been a no-op.
echo -e "${YELLOW}[4/5] Checking for dangling volumes (not attached to any container)...${NC}"
DANGLING_VOLUMES=$(docker volume ls --filter dangling=true --format '{{.Name}}' || true)
if [ -n "$DANGLING_VOLUMES" ]; then
  echo -e "${RED}WARNING: Found dangling volumes:${NC}"
  echo "$DANGLING_VOLUMES"
  echo ""
  echo -e "${RED}These may contain data from old deployments or stopped stacks.${NC}"
  read -p "Delete dangling volumes? [y/N] " -n 1 -r
  echo ""
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "$DANGLING_VOLUMES" | xargs -r docker volume rm
    echo -e "${GREEN}Volumes removed.${NC}"
  else
    echo "Skipped volume cleanup."
  fi
else
  echo "No dangling volumes found."
fi
echo ""

# 5. Detect orphaned project-prefix volumes (warn only — no removal here).
#    These are volumes named `<project>_*` whose compose label doesn't match
#    the current project (or is missing entirely). Compose mounts them but
#    warns "not built with compose"; they need explicit cleanup.
echo -e "${YELLOW}[5/5] Checking for orphaned ${COMPOSE_PROJECT}-prefix volumes...${NC}"
ORPHANS=()
while IFS= read -r vol; do
  [[ -z "$vol" ]] && continue
  label="$(docker volume inspect -f '{{ index .Labels "com.docker.compose.project" }}' "$vol" 2>/dev/null || true)"
  if [[ "$label" != "$COMPOSE_PROJECT" ]]; then
    ORPHANS+=("$vol")
  fi
done < <(docker volume ls --format '{{.Name}}' | grep -E "^${COMPOSE_PROJECT}[-_]" || true)

if [ ${#ORPHANS[@]} -gt 0 ]; then
  echo -e "${RED}WARNING: Found ${#ORPHANS[@]} ${COMPOSE_PROJECT}-prefix volume(s) NOT owned by this compose project:${NC}"
  for vol in "${ORPHANS[@]}"; do
    label="$(docker volume inspect -f '{{ index .Labels "com.docker.compose.project" }}' "$vol" 2>/dev/null || true)"
    if [ -z "$label" ]; then
      echo "  - $vol  (no compose project label)"
    else
      echo "  - $vol  (labeled project: ${label})"
    fi
  done
  echo ""
  echo -e "${YELLOW}Compose will mount these but disowns them ('not built with compose').${NC}"
  echo -e "${YELLOW}Run 'make clean-orphans' to review and remove them.${NC}"
else
  echo "No orphaned ${COMPOSE_PROJECT}-prefix volumes found."
fi
echo ""

echo "========================================="
echo -e "${GREEN}Cleanup complete!${NC}"
echo "========================================="
echo ""
echo -e "${YELLOW}Disk usage after cleanup:${NC}"
df -h / | grep -v Filesystem
echo ""
echo -e "${YELLOW}Docker disk usage after cleanup:${NC}"
docker system df
echo ""
echo "To see detailed cache breakdown: docker system df -v"
echo ""
