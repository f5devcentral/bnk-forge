#!/usr/bin/env bash
# === BNK-Forge Orphan Volume Cleanup ===
#
# Removes Docker volumes whose name starts with the compose project prefix
# (default: bnk-forge_ or bnk-forge-) but whose `com.docker.compose.project`
# label is missing or doesn't match the current project.
#
# These are typically left over from a renamed deployment, a previous project
# name, or volumes that were created outside of compose. Compose will mount
# them but disowns them with the warning "not built with compose".
#
# Always prompts before removing. Volumes currently in use by a container
# cannot be removed and will be skipped by `docker volume rm`.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$REPO_DIR")}"

echo ""
echo "========================================="
echo "  BNK-Forge Orphan Volume Cleanup"
echo "  (project: ${COMPOSE_PROJECT})"
echo "========================================="
echo ""

ORPHANS=()
while IFS= read -r vol; do
  [[ -z "$vol" ]] && continue
  label="$(docker volume inspect -f '{{ index .Labels "com.docker.compose.project" }}' "$vol" 2>/dev/null || true)"
  if [[ "$label" != "$COMPOSE_PROJECT" ]]; then
    ORPHANS+=("$vol")
  fi
done < <(docker volume ls --format '{{.Name}}' | grep -E "^${COMPOSE_PROJECT}[-_]" || true)

if [ ${#ORPHANS[@]} -eq 0 ]; then
  echo -e "${GREEN}No orphaned ${COMPOSE_PROJECT}-prefix volumes found.${NC}"
  echo ""
  exit 0
fi

echo -e "${RED}Found ${#ORPHANS[@]} orphaned volume(s):${NC}"
for vol in "${ORPHANS[@]}"; do
  label="$(docker volume inspect -f '{{ index .Labels "com.docker.compose.project" }}' "$vol" 2>/dev/null || true)"
  if [ -z "$label" ]; then
    echo "  - $vol  (no compose project label)"
  else
    echo "  - $vol  (labeled project: ${label})"
  fi
done
echo ""
echo -e "${YELLOW}WARNING: Removing these will permanently delete their contents.${NC}"
echo -e "${YELLOW}Volumes currently in use by a container will be skipped automatically.${NC}"
echo ""

read -p "Type 'yes' to remove these volumes: " -r REPLY
echo ""
if [[ "$REPLY" != "yes" ]]; then
  echo "Aborted. No volumes removed."
  exit 0
fi

REMOVED=0
SKIPPED=0
for vol in "${ORPHANS[@]}"; do
  if docker volume rm "$vol" >/dev/null 2>&1; then
    echo -e "${GREEN}Removed:${NC} $vol"
    REMOVED=$((REMOVED + 1))
  else
    echo -e "${YELLOW}Skipped (in use or error):${NC} $vol"
    SKIPPED=$((SKIPPED + 1))
  fi
done

echo ""
echo -e "${GREEN}Done. Removed: ${REMOVED}, Skipped: ${SKIPPED}.${NC}"
echo ""
