#!/usr/bin/env bash
#
# test-backup-restore.sh — Local test for backup/restore feature
#
# Usage:
#   ./scripts/test-backup-restore.sh
#
# Prerequisites:
#   - Running on feat/backup-restore branch
#   - Docker Desktop running
#   - No critical unsaved work (we stop/start the stack)
#
# What this does:
#   1. Builds and deploys the feature branch (make local-deploy)
#   2. Prompts you to create a backup via the UI
#   3. Stashes current volumes (safe copy)
#   4. Wipes volumes to simulate a fresh instance
#   5. Starts fresh and prompts you to restore via the UI
#   6. Optionally rolls back to the original state
#
# Compose setup:
#   - Local mode uses bridge networking (docker-compose.local.yml overlay)
#   - Backend health: http://localhost:8000/api/system/health
#   - UI access:     https://localhost (via proxy on :443)
#   - Volume prefix: <compose-project>_<volume-name>
#     (compose project = directory name, auto-detected below)
#
set -euo pipefail

# ---------- Configuration ----------

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Compose files — mirrors COMPOSE_LOCAL in the Makefile
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.local.yml)

# Auto-detect compose project name (Docker defaults to directory name).
# Override with: COMPOSE_PROJECT_NAME=myproject ./scripts/test-backup-restore.sh
if [[ -z "${COMPOSE_PROJECT_NAME:-}" ]]; then
  COMPOSE_PROJECT="$(basename "$REPO_DIR")"
else
  COMPOSE_PROJECT="$COMPOSE_PROJECT_NAME"
fi

BACKUP_SUFFIX="_pre_test_backup"

# Volumes that hold critical application state — stashed before the wipe,
# restored if the user wants to roll back. Names match docker-compose.yml.
CRITICAL_VOLUMES=(
  "postgres_data"
  "bnk-forge-keys"
  "bnk-forge-data"
  "state_data"
  "redis_data"
  "workspace_data"
)

# All volumes declared in docker-compose.yml (full wipe for the fresh-instance test).
ALL_VOLUMES=(
  "postgres_data"
  "bnk-forge-keys"
  "bnk-forge-data"
  "state_data"
  "redis_data"
  "workspace_data"
  "module_catalog"
  "postgres_backups"
  "helm_cache"
  "helm_config"
  "helm_charts"
)

# ---------- Helpers ----------

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()   { echo -e "${RED}[ERROR]${NC} $*"; }

prompt_continue() {
  local msg="${1:-Continue?}"
  echo ""
  echo -e "${YELLOW}>>> ${msg}${NC}"
  read -rp "Press ENTER to continue (Ctrl+C to abort)... "
  echo ""
}

prompt_yn() {
  local msg="$1"
  local answer
  echo ""
  read -rp "$(echo -e "${YELLOW}>>> ${msg} [y/N]: ${NC}")" answer
  [[ "$answer" =~ ^[Yy] ]]
}

# Return the fully-qualified Docker volume name for a short volume name.
full_volume_name() {
  echo "${COMPOSE_PROJECT}_${1}"
}

# Copy a Docker volume by running an alpine container.
copy_volume() {
  local src="$1"
  local dst="$2"
  docker volume create "$dst" >/dev/null 2>&1 || true
  docker run --rm \
    -v "${src}:/from:ro" \
    -v "${dst}:/to" \
    alpine sh -c "cd /from && cp -a . /to/" 2>/dev/null
}

volume_exists() {
  docker volume inspect "$1" >/dev/null 2>&1
}

# Wait up to $2 seconds for the backend health endpoint to respond.
wait_for_backend() {
  local max_wait="${1:-60}"
  local waited=0
  info "Waiting for backend health (up to ${max_wait}s)..."
  while [[ $waited -lt $max_wait ]]; do
    if curl -sf http://localhost:8000/api/system/health >/dev/null 2>&1; then
      ok "Backend is healthy"
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
    info "  Still waiting... (${waited}/${max_wait}s)"
  done
  warn "Backend did not become healthy within ${max_wait}s"
  warn "Check logs: docker compose ${COMPOSE_FILES[*]} logs backend"
  return 1
}

# ---------- Pre-flight ----------

cd "$REPO_DIR"

echo ""
echo "============================================"
echo "  BNK Forge — Backup/Restore Local Test"
echo "============================================"
echo ""
echo "  Repo:    $REPO_DIR"
echo "  Project: $COMPOSE_PROJECT"
echo "  Branch:  $(git branch --show-current)"
echo ""
echo "  Compose: docker compose ${COMPOSE_FILES[*]}"
echo "  UI:      https://localhost"
echo "  API:     http://localhost:8000/api/system/health"
echo ""

# Check we're on the right branch
BRANCH=$(git branch --show-current)
if [[ "$BRANCH" != "feat/backup-restore" ]]; then
  warn "Not on feat/backup-restore branch (on: $BRANCH)"
  if ! prompt_yn "Continue anyway?"; then
    exit 1
  fi
fi

# Check Docker is running
if ! docker info >/dev/null 2>&1; then
  err "Docker is not running. Start Docker Desktop and try again."
  exit 1
fi

ok "Docker is running."

# ---------- Phase 1: Build & Deploy ----------

echo ""
echo "============================================"
echo "  Phase 1: Build & Deploy Feature Branch"
echo "============================================"
echo ""

info "This will rebuild all images and redeploy the stack."
info "Running containers will be stopped and recreated."
prompt_continue "Ready to run 'make local-deploy'?"

info "Running make local-deploy ..."
make local-deploy

echo ""
wait_for_backend 120 || true   # non-fatal — let the user decide to continue

# ---------- Phase 2: Create Backup via UI ----------

echo ""
echo "============================================"
echo "  Phase 2: Create a Backup"
echo "============================================"
echo ""

info "Open the UI and create a backup:"
echo ""
echo "  1. Go to https://localhost  (accept the self-signed cert warning)"
echo "  2. Log in as admin (password: docker exec bnk-forge-backend cat /app/keys/initial_admin_password)"
echo "  3. Navigate to System → Backup & Restore tab"
echo "  4. Enter a passphrase (12+ chars) — REMEMBER IT!"
echo "  5. Click 'Create Backup'"
echo "  6. Save the downloaded .tar.gz file somewhere safe"
echo ""

prompt_continue "Done creating the backup? (saved the .tar.gz file and remember the passphrase?)"

# ---------- Phase 3: Stash Current Volumes ----------

echo ""
echo "============================================"
echo "  Phase 3: Stash Current Volumes"
echo "============================================"
echo ""

info "Stopping the stack..."
docker compose "${COMPOSE_FILES[@]}" down

echo ""
info "Copying critical volumes to stash copies..."
echo ""

STASH_FAILED=0
for vol in "${CRITICAL_VOLUMES[@]}"; do
  src=$(full_volume_name "$vol")
  dst="${src}${BACKUP_SUFFIX}"

  if ! volume_exists "$src"; then
    warn "  Volume $src does not exist — skipping"
    continue
  fi

  if volume_exists "$dst"; then
    warn "  Stash $dst already exists — removing old stash"
    docker volume rm "$dst" >/dev/null 2>&1 || true
  fi

  info "  Copying $src → $dst"
  if copy_volume "$src" "$dst"; then
    ok "  Copied $vol"
  else
    err "  Failed to copy $vol"
    STASH_FAILED=1
  fi
done

if [[ $STASH_FAILED -ne 0 ]]; then
  err "Some volume copies failed — aborting to avoid data loss."
  exit 1
fi

echo ""
ok "All critical volumes stashed."

# ---------- Phase 4: Wipe & Start Fresh ----------

echo ""
echo "============================================"
echo "  Phase 4: Wipe Volumes & Start Fresh"
echo "============================================"
echo ""

warn "This will REMOVE all application volumes and start with empty data."
warn "Your original data is safely stashed (step 3). You can roll back later."
prompt_continue "Ready to wipe volumes and start a fresh instance?"

info "Removing volumes..."
for vol in "${ALL_VOLUMES[@]}"; do
  name=$(full_volume_name "$vol")
  if volume_exists "$name"; then
    if docker volume rm "$name" >/dev/null 2>&1; then
      info "  Removed $name"
    else
      warn "  Could not remove $name (may still be in use — try again after containers fully stop)"
    fi
  fi
done

echo ""
info "Deploying a fresh instance..."
make local-deploy

echo ""
wait_for_backend 120 || true

# ---------- Phase 5: Restore Backup via UI ----------

echo ""
echo "============================================"
echo "  Phase 5: Restore the Backup"
echo "============================================"
echo ""

info "Open the UI on the FRESH instance and restore your backup:"
echo ""
echo "  1. Go to https://localhost"
echo "  2. Log in as admin (password from /app/keys/initial_admin_password or DEFAULT_ADMIN_PASSWORD)"
echo "  3. Navigate to System → Backup & Restore tab"
echo "  4. Upload the .tar.gz backup file you saved in Phase 2"
echo "  5. Enter the SAME passphrase you used when creating the backup"
echo "  6. Click 'Review Restore' → review the summary → 'Confirm Restore'"
echo "  7. Watch the maintenance banner appear"
echo "  8. Wait for the system to automatically restart"
echo "  9. Refresh your browser once the page reconnects"
echo ""

prompt_continue "Done restoring? Is your original data back in the UI?"

# ---------- Phase 6: Verify ----------

echo ""
echo "============================================"
echo "  Phase 6: Verification Checklist"
echo "============================================"
echo ""

info "Please verify each of the following manually:"
echo ""
echo "  [ ] Projects are present (matching what you had before the backup)"
echo "  [ ] Kubernetes clusters are visible"
echo "  [ ] SSH credentials can be viewed (decryption key restored correctly)"
echo "  [ ] Audit log shows 'backup_created' and 'restore_completed' events"
echo "  [ ] System health is green (System → Health)"
echo ""

# Automated health spot-check
if curl -sf http://localhost:8000/api/system/health >/dev/null 2>&1; then
  ok "Backend health endpoint: OK"
else
  err "Backend health endpoint: FAIL — check 'docker compose logs backend'"
fi

prompt_continue "Verification complete?"

# ---------- Phase 7: Cleanup / Rollback ----------

echo ""
echo "============================================"
echo "  Phase 7: Cleanup"
echo "============================================"
echo ""

if prompt_yn "Roll back to the ORIGINAL data (before the test started)?"; then
  info "Stopping the stack..."
  docker compose "${COMPOSE_FILES[@]}" down

  info "Restoring original volumes from stash..."
  for vol in "${CRITICAL_VOLUMES[@]}"; do
    name=$(full_volume_name "$vol")
    backup="${name}${BACKUP_SUFFIX}"

    if ! volume_exists "$backup"; then
      warn "  No stash found for $vol — skipping"
      continue
    fi

    docker volume rm "$name" >/dev/null 2>&1 || true
    info "  Restoring $vol ..."
    if copy_volume "$backup" "$name"; then
      ok "  Restored $vol"
    else
      err "  Failed to restore $vol from stash"
    fi
  done

  info "Restarting stack with original data..."
  make local-deploy

  echo ""
  ok "Rolled back to original state."
fi

echo ""
if prompt_yn "Remove the stashed volumes now? (safe if rollback succeeded or not needed)"; then
  for vol in "${CRITICAL_VOLUMES[@]}"; do
    backup="$(full_volume_name "$vol")${BACKUP_SUFFIX}"
    if volume_exists "$backup"; then
      docker volume rm "$backup" >/dev/null 2>&1
      info "  Removed $backup"
    fi
  done
  ok "Stash volumes cleaned up."
fi

echo ""
echo "============================================"
echo "  Test Complete!"
echo "============================================"
echo ""
ok "Backup/restore local test finished."
echo ""
