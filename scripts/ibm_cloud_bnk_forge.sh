#!/usr/bin/env bash
# =============================================================================
# ibm_cloud_bnk_forge.sh
# -----------------------------------------------------------------------------
# Provision an Ubuntu 24.04 IBM Cloud VPC virtual server (VSI) sized for
# bnk-forge, give it a floating IP, and install bnk-forge from a container
# registry using docker compose (host networking).
#
# Prompts ONLY for:
#   1. IBM Cloud region
#   2. Suggested vCPU count            (mapped to the smallest bx2 profile)
#   3. Suggested RAM (GB)              (mapped to the smallest bx2 profile)
#   4. Which existing IBM Cloud SSH key to use
#   5. Which registry to pull bnk-forge containers from
#   6. Whether that registry is public; if not, a Personal Access Token
#
# Everything else (zone, instance profile, Ubuntu image, registry username,
# image version tag) is derived automatically.
#
# Requires: a pre-installed and logged-in `ibmcloud` CLI with the
# vpc-infrastructure (`is`) plugin, plus `jq` and `curl`.
#
# Outputs: the HTTPS URL to reach BNK Forge.
# =============================================================================
set -euo pipefail

# ── Tunables (override via environment if desired) ───────────────────────────
BNK_FORGE_VERSION="${BNK_FORGE_VERSION:-latest}"   # image tag to pull
NAME_PREFIX="${NAME_PREFIX:-bnk-forge}"
SUFFIX="$(date +%m%d%H%M)"
VPC_NAME="${NAME_PREFIX}-vpc-${SUFFIX}"
SUBNET_NAME="${NAME_PREFIX}-subnet-${SUFFIX}"
VM_NAME="${NAME_PREFIX}-vsi-${SUFFIX}"
FIP_NAME="${NAME_PREFIX}-fip-${SUFFIX}"

# bx2 (balanced, 1 vCPU : 4 GB) profile ladder — ascending.
PROFILE_LADDER=(
  "2 8 bx2-2x8"
  "4 16 bx2-4x16"
  "8 32 bx2-8x32"
  "16 64 bx2-16x64"
  "32 128 bx2-32x128"
  "48 192 bx2-48x192"
)

log()  { printf '\033[1;36m[bnk-forge]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

# ── Preflight ────────────────────────────────────────────────────────────────
command -v ibmcloud >/dev/null 2>&1 || die "ibmcloud CLI not found. Install it first."
command -v jq       >/dev/null 2>&1 || die "jq not found. Install jq (e.g. 'sudo apt-get install -y jq')."
command -v curl     >/dev/null 2>&1 || die "curl not found."
ibmcloud plugin show vpc-infrastructure >/dev/null 2>&1 || die "vpc-infrastructure plugin missing. Run: ibmcloud plugin install vpc-infrastructure"
ibmcloud account show >/dev/null 2>&1 || die "Not logged in. Run: ibmcloud login  (or 'ibmcloud login --sso')"

# ── Prompt 1: region ─────────────────────────────────────────────────────────
read -rp "IBM Cloud region [us-south]: " REGION
REGION="${REGION:-us-south}"
log "Targeting region '${REGION}'..."
ibmcloud target -r "${REGION}" >/dev/null 2>&1 || die "Could not target region '${REGION}'. Run 'ibmcloud is regions' to list valid regions."

# Pick the first available zone in the region.
ZONE="$(ibmcloud is zones --output json | jq -r '[.[] | select(.status=="available")][0].name')"
[ -n "${ZONE}" ] && [ "${ZONE}" != "null" ] || die "No available zone found in region '${REGION}'."
log "Using zone '${ZONE}'."

# ── Prompt 2 & 3: vCPU / RAM → instance profile ──────────────────────────────
read -rp "Suggested vCPUs [4]: " VCPU; VCPU="${VCPU:-4}"
read -rp "Suggested RAM in GB [16]: " RAM; RAM="${RAM:-16}"
[[ "${VCPU}" =~ ^[0-9]+$ ]] || die "vCPUs must be a number."
[[ "${RAM}"  =~ ^[0-9]+$ ]] || die "RAM must be a number."

PROFILE=""
for entry in "${PROFILE_LADDER[@]}"; do
  read -r c m name <<<"${entry}"
  if [ "${c}" -ge "${VCPU}" ] && [ "${m}" -ge "${RAM}" ]; then PROFILE="${name}"; break; fi
done
[ -n "${PROFILE}" ] || { PROFILE="bx2-48x192"; warn "Requested size exceeds the bx2 ladder; using largest (${PROFILE})."; }

# Confirm the chosen profile exists in this region.
ibmcloud is instance-profiles --output json | jq -e --arg p "${PROFILE}" '[.[].name] | index($p)' >/dev/null 2>&1 \
  || die "Profile '${PROFILE}' is not available in '${REGION}'. Check: ibmcloud is instance-profiles"
log "Selected profile '${PROFILE}' (>= ${VCPU} vCPU, >= ${RAM} GB)."

# ── Prompt 4: existing SSH key ───────────────────────────────────────────────
mapfile -t SSH_KEYS < <(ibmcloud is keys --output json | jq -r '.[].name')
[ "${#SSH_KEYS[@]}" -gt 0 ] || die "No SSH keys found in region '${REGION}'. Create one: ibmcloud is key-create ..."
echo "Available IBM Cloud SSH keys:"
PS3="Select the SSH key to install on the VSI: "
select SSH_KEY_NAME in "${SSH_KEYS[@]}"; do
  [ -n "${SSH_KEY_NAME:-}" ] && break
  echo "Invalid selection."
done
log "Using SSH key '${SSH_KEY_NAME}'."

# ── Prompt 5 & 6: registry + (optional) PAT ──────────────────────────────────
read -rp "Container registry to pull bnk-forge images from [ghcr.io/jgruberf5]: " REGISTRY
REGISTRY="${REGISTRY:-ghcr.io/jgruberf5}"
REGISTRY="${REGISTRY%/}"                         # trim any trailing slash
REGISTRY_HOST="${REGISTRY%%/*}"                  # e.g. ghcr.io
# Derive a login username from the registry namespace (e.g. ghcr.io/jgruberf5 -> jgruberf5).
REGISTRY_USER="${REGISTRY#*/}"; REGISTRY_USER="${REGISTRY_USER%%/*}"
[ -n "${REGISTRY_USER}" ] && [ "${REGISTRY_USER}" != "${REGISTRY}" ] || REGISTRY_USER="${NAME_PREFIX}"

read -rp "Is '${REGISTRY}' PUBLIC (pullable without auth)? [y/N]: " PUB
PAT=""
if [[ "${PUB}" =~ ^[Yy] ]]; then
  PUBLIC="yes"
  log "Registry marked public — no login will be configured."
else
  PUBLIC="no"
  read -rsp "Personal Access Token for ${REGISTRY_HOST} (user '${REGISTRY_USER}'): " PAT; echo
  [ -n "${PAT}" ] || die "A Personal Access Token is required for a private registry."
fi

# ── Look up the latest Ubuntu 24.04 amd64 stock image ────────────────────────
log "Looking up the latest Ubuntu 24.04 (amd64) stock image..."
IMAGE_ID="$(ibmcloud is images --visibility public --output json | jq -r '
  [ .[]
    | select(.operating_system.architecture=="amd64")
    | select(((.operating_system.name // "") | test("ubuntu-24-04")) or ((.name // "") | test("ubuntu-24-04")))
    | select(.status=="available") ]
  | sort_by(.created_at) | last | .id // empty')"
[ -n "${IMAGE_ID}" ] || die "Could not find an available Ubuntu 24.04 amd64 image in '${REGION}'."
log "Image: ${IMAGE_ID}"

# ── Build cloud-init user-data ───────────────────────────────────────────────
# Written with a QUOTED heredoc so nothing here is expanded locally; the
# compose ${...}/$$ tokens and the VSI-side shell vars stay literal. The
# __PLACEHOLDER__ tokens are substituted afterwards with the prompt answers.
UD="$(mktemp)"
trap 'rm -f "${UD}"' EXIT

cat > "${UD}" <<'USERDATA_TEMPLATE'
#!/bin/bash
set -euxo pipefail
exec > /var/log/bnk-forge-install.log 2>&1
echo "=== bnk-forge cloud-init install starting: $(date -u) ==="

# 1. Install Docker Engine + compose plugin
export DEBIAN_FRONTEND=noninteractive
for i in $(seq 1 30); do curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && break || sleep 10; done
sh /tmp/get-docker.sh
systemctl enable --now docker

# 2. Application directory
install -d -m 755 /opt/bnk-forge
install -d -m 700 /opt/bnk-forge/secrets
cd /opt/bnk-forge
echo "__VERSION__" > VERSION

# 3. docker-compose.yml (registry-pull, host networking)
cat > docker-compose.yml <<'YAML'
__COMPOSE__
YAML

# 4. .env — secrets generated locally on the VSI
#
# xtrace is OFF for this whole block. cloud-init runs this script under
# `set -euxo pipefail` with all output redirected to /var/log/bnk-forge-install.log,
# which is root-owned but mode 644 — so with xtrace on, the generated Postgres,
# Redis and MCP passwords were echoed in cleartext and readable by any local user
# on a public-IP VSI (issue #408.4). `{ set +x; } 2>/dev/null` disables it without
# the disable itself being traced.
__XTRACE__="$-"                       # remember whether xtrace was on
{ set +x; } 2>/dev/null
PG="$(openssl rand -hex 16)"
RD="$(openssl rand -hex 16)"
MCP="$(openssl rand -hex 24)"
cat > .env <<ENV
COMPOSE_PROJECT_NAME=bnk-forge
BNK_FORGE_REGISTRY=__REGISTRY__
BNK_FORGE_VERSION=__VERSION__
POSTGRES_PASSWORD=${PG}
REDIS_PASSWORD=${RD}
MCP_USERNAME=admin
MCP_PASSWORD=${MCP}
ENV
chmod 600 .env
case "${__XTRACE__}" in *x*) set -x ;; esac

# 5. Registry login (private registries only)
# Same treatment: the PAT is a literal argument to printf, so xtrace would print
# it verbatim into the world-readable install log.
if [ "__PUBLIC__" != "yes" ]; then
  __XTRACE__="$-"
  { set +x; } 2>/dev/null
  printf '%s' '__PAT__' | docker login __REGISTRY_HOST__ -u '__REGISTRY_USER__' --password-stdin
  case "${__XTRACE__}" in *x*) set -x ;; esac
fi

# 6. Pull images
docker compose pull

# 7. Pre-create volume ownership for the uid-1000 app user
PROJECT=bnk-forge
docker run --rm \
  -v "${PROJECT}_bnk-forge-data:/app/projects" \
  -v "${PROJECT}_bnk-forge-keys:/app/keys" \
  -v "${PROJECT}_state_data:/app/state" \
  -v "${PROJECT}_helm_cache:/home/bnkforge/.cache/helm" \
  -v "${PROJECT}_helm_config:/home/bnkforge/.config/helm" \
  -v "${PROJECT}_helm_charts:/app/helm_charts" \
  -v "${PROJECT}_workspace_data:/app/workspaces" \
  alpine:latest sh -c 'mkdir -p /app/projects /app/keys /app/state /app/helm_charts /app/workspaces /home/bnkforge/.cache/helm /home/bnkforge/.config/helm && chown -R 1000:1000 /app/projects /app/keys /app/state /app/helm_charts /app/workspaces /home/bnkforge' || true

# 8. Start infra, wait for DB, then the full stack
# The container-image engine runs artifact steps on this dedicated bridge
# network. Compose cannot create it (no service references it — under host
# networking none can), so create it here or every artifact deployment on this
# server fails with "network not found". Idempotent.
#
# Docker's auto-assigned pool (172.17.0.0/16+, typically landing on
# 172.18.0.0/16) can collide with a host's VPN/management routes, cutting off
# connectivity mid-deploy. No single hardcoded subnet is safe everywhere (a
# fixed 10.200.0.0/24 default also collided on a field site — see issue #422),
# so the subnet is resolved as:
#   1. ARTIFACT_NETWORK_SUBNET set (env or .env) -> "auto" defers to Docker's
#      default-address-pools; any other value is used verbatim.
#   2. Unset -> try each ARTIFACT_NETWORK_SUBNET_CANDIDATES entry in order,
#      picking the first that doesn't overlap the host's routing table.
#   3. No candidate clean -> create with no --subnet and print a warning.
# Compact copy of scripts/artifact_network.sh's resolution logic (standalone
# on the VSI, so it skips the docker-network cross-check but always checks
# host routes).
#
# If the network already EXISTS, resolution is skipped entirely: once
# created, its own subnet becomes a host route, so re-detecting would count
# it as a collision with itself and walk away every time — a mismatch
# warning that never converges (review r2 on issue #422). Re-running this
# install only warns when ARTIFACT_NETWORK_SUBNET is an explicit literal
# CIDR that doesn't match what's actually there; auto-detected values never
# warn.
if [ -z "${ARTIFACT_NETWORK_SUBNET+x}" ] && [ -f .env ]; then
  _an_v="$(grep -E '^[[:space:]]*ARTIFACT_NETWORK_SUBNET=' .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  [ -n "${_an_v}" ] && ARTIFACT_NETWORK_SUBNET="${_an_v}"
fi
if [ -z "${ARTIFACT_NETWORK_SUBNET_CANDIDATES+x}" ] && [ -f .env ]; then
  _an_v="$(grep -E '^[[:space:]]*ARTIFACT_NETWORK_SUBNET_CANDIDATES=' .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  [ -n "${_an_v}" ] && ARTIFACT_NETWORK_SUBNET_CANDIDATES="${_an_v}"
fi
unset _an_v
ARTIFACT_NETWORK_SUBNET="${ARTIFACT_NETWORK_SUBNET:-}"
ARTIFACT_NETWORK_SUBNET_CANDIDATES="${ARTIFACT_NETWORK_SUBNET_CANDIDATES:-10.200.0.0/24 192.168.200.0/24 10.213.37.0/24 172.31.255.0/24}"

_an_overlap() {
  awk -v c1="$1" -v c2="$2" '
    function pow2(n,    r, i) { r = 1; for (i = 0; i < n; i++) r = r * 2; return r }
    function ip2int(ip,    p, n) { n = split(ip, p, "."); if (n != 4) return -1; return p[1]*16777216 + p[2]*65536 + p[3]*256 + p[4] }
    function nstart(ip, pfx,    b) { b = pow2(32 - pfx); return int(ip / b) * b }
    function nend(ip, pfx,    b) { b = pow2(32 - pfx); return nstart(ip, pfx) + b - 1 }
    BEGIN {
      n1 = split(c1, a1, "/"); ip1 = ip2int(a1[1]); p1 = (n1 == 2 ? a1[2] : 32)
      n2 = split(c2, a2, "/"); ip2 = ip2int(a2[1]); p2 = (n2 == 2 ? a2[2] : 32)
      if (ip1 < 0 || ip2 < 0) exit 1
      s1 = nstart(ip1, p1); e1 = nend(ip1, p1); s2 = nstart(ip2, p2); e2 = nend(ip2, p2)
      exit (s1 <= e2 && s2 <= e1) ? 0 : 1
    }'
}

_an_routes() {
  if command -v ip >/dev/null 2>&1; then
    ip route show 2>/dev/null | awk '$1 != "default" && $1 ~ /\// { print $1 }' || true
  elif command -v netstat >/dev/null 2>&1; then
    netstat -rn -f inet 2>/dev/null | awk '
      $1 == "Destination" || $1 == "default" { next }
      $1 ~ /^(link#|lo0|127($|\.)|169\.254)/ { next }
      $1 ~ /^[0-9]+(\.[0-9]+){0,3}(\/[0-9]+)?$/ {
        d = $1
        if (d ~ /\//) { print d; next }
        n = split(d, o, ".")
        for (i = n + 1; i <= 4; i++) o[i] = 0
        printf "%s.%s.%s.%s/%d\n", o[1], o[2], o[3], o[4], n * 8
      }' || true
  fi
  return 0
}

_an_resolve() {
  if [ -n "${ARTIFACT_NETWORK_SUBNET}" ]; then
    echo "${ARTIFACT_NETWORK_SUBNET}"
    return 0
  fi
  _an_routes_out="$(_an_routes)"
  for _an_cand in ${ARTIFACT_NETWORK_SUBNET_CANDIDATES}; do
    _an_clean=true
    for _an_r in ${_an_routes_out}; do
      if _an_overlap "${_an_cand}" "${_an_r}"; then _an_clean=false; break; fi
    done
    if [ "${_an_clean}" = true ]; then
      echo "${_an_cand}"
      return 0
    fi
  done
  echo "auto"
}

if docker network inspect bnk-forge-artifacts >/dev/null 2>&1; then
  # Already exists: nothing to resolve. Only warn if the operator pinned an
  # explicit CIDR (not unset, not "auto") that doesn't match what's there.
  if [ -n "${ARTIFACT_NETWORK_SUBNET}" ] && [ "${ARTIFACT_NETWORK_SUBNET}" != "auto" ]; then
    existing_subnet="$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' bnk-forge-artifacts 2>/dev/null || true)"
    if [ -n "${existing_subnet}" ] && [ "${existing_subnet}" != "${ARTIFACT_NETWORK_SUBNET}" ]; then
      echo "=========================================================="
      echo "  WARNING: bnk-forge-artifacts subnet does not match config"
      echo "  Existing subnet:   ${existing_subnet}"
      echo "  Configured subnet: ${ARTIFACT_NETWORK_SUBNET}"
      echo "  This network was not recreated (containers may be attached)."
      echo "  To apply the configured subnet: stop the stack, run"
      echo "    docker network rm bnk-forge-artifacts"
      echo "  then re-run this install."
      echo "=========================================================="
    fi
  fi
else
  ARTIFACT_NETWORK_RESOLVED="$(_an_resolve)"
  if [ "${ARTIFACT_NETWORK_RESOLVED}" = "auto" ]; then
    if [ "${ARTIFACT_NETWORK_SUBNET}" = "auto" ]; then
      echo "  ARTIFACT_NETWORK_SUBNET=auto: deferring to Docker's default-address-pools."
    else
      echo "=========================================================="
      echo "  WARNING: no candidate subnet is free of host-route collisions:"
      echo "    ${ARTIFACT_NETWORK_SUBNET_CANDIDATES}"
      echo "  Creating bnk-forge-artifacts without --subnet (Docker will pick from"
      echo "  its default pool, which may still collide)."
      echo "  Recommended: set ARTIFACT_NETWORK_SUBNET=<cidr>, or dedicate a Docker"
      echo "  default-address-pool via /etc/docker/daemon.json:"
      echo '    { "default-address-pools": [ { "base": "192.168.200.0/20", "size": 24 } ] }'
      echo "  then restart docker."
      echo "=========================================================="
    fi
    docker network create --driver bridge bnk-forge-artifacts >/dev/null
  else
    docker network create --driver bridge --subnet "${ARTIFACT_NETWORK_RESOLVED}" bnk-forge-artifacts >/dev/null
  fi
fi

docker compose up -d postgres redis
for i in $(seq 1 30); do
  docker exec bnk-forge-postgres pg_isready -U bnkforge >/dev/null 2>&1 && break || sleep 2
done
docker compose up -d

# 9. Wait for backend health then drop a ready marker
for _ in $(seq 1 60); do
  curl -sf http://localhost:8000/api/system/health >/dev/null 2>&1 && break || sleep 5
done
touch /opt/bnk-forge/.bnk-forge-ready
echo "=== bnk-forge cloud-init install finished: $(date -u) ==="
USERDATA_TEMPLATE

# Splice the compose file in (multiline) then substitute the scalar placeholders.
# Prefer the repo's canonical dist/docker-compose.yml when this script is run
# from within the repo (as scripts/ibm_cloud_bnk_forge.sh) or beside a dist
# bundle — that avoids the embedded copy below drifting from the real compose.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_SRC=""
for _cand in "${SCRIPT_DIR}/dist/docker-compose.yml" "${SCRIPT_DIR}/../dist/docker-compose.yml"; do
  [ -f "${_cand}" ] && { COMPOSE_SRC="${_cand}"; break; }
done
python3 - "${UD}" "${COMPOSE_SRC}" <<'PYEOF' 2>/dev/null || COMPOSE_EMBED=1
import sys
ud, src = sys.argv[1], sys.argv[2]
with open(src) as f: compose = f.read()
with open(ud) as f: data = f.read()
data = data.replace("__COMPOSE__", compose.rstrip("\n"))
with open(ud, "w") as f: f.write(data)
PYEOF

# If the repo's dist compose was not found next to this script, embed the
# bundled copy below so the script stays fully self-contained.
if [ "${COMPOSE_EMBED:-0}" = "1" ] || grep -q '__COMPOSE__' "${UD}"; then
  EMB="$(mktemp)"; trap 'rm -f "${UD}" "${EMB}"' EXIT
  cat > "${EMB}" <<'YAML'
x-backend-env: &backend-env
  DATABASE_URL: postgresql://bnkforge:${POSTGRES_PASSWORD:-bnkforge_dev_password}@localhost:5432/bnkforge
  REDIS_URL: redis://:${REDIS_PASSWORD:-bnkforge_redis_dev}@localhost:6379/0
  CELERY_BROKER_URL: redis://:${REDIS_PASSWORD:-bnkforge_redis_dev}@localhost:6379/0
  CELERY_RESULT_BACKEND: redis://:${REDIS_PASSWORD:-bnkforge_redis_dev}@localhost:6379/0
  # Artifact (container-image) engine reaches the Docker daemon through the
  # scoped socket proxy below (loopback-published), never the raw host socket.
  DOCKER_HOST: ${DOCKER_HOST:-tcp://127.0.0.1:2375}

x-worker-volumes: &worker-volumes
  - module_catalog:/tmp/bnk-forge-modules
  - bnk-forge-data:/app/projects
  - bnk-forge-keys:/app/keys
  - state_data:/app/state
  - helm_cache:/home/bnkforge/.cache/helm
  - helm_config:/home/bnkforge/.config/helm
  - helm_charts:/app/helm_charts
  - workspace_data:/app/workspaces
  - ./secrets:/app/secrets:ro

x-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

services:
  postgres:
    image: postgres:16-alpine
    container_name: bnk-forge-postgres
    network_mode: host
    logging: *default-logging
    environment:
      POSTGRES_USER: bnkforge
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-bnkforge_dev_password}
      POSTGRES_DB: bnkforge
      PGDATA: /var/lib/postgresql/data/pgdata
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U bnkforge"]
      interval: 5s
      timeout: 3s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: bnk-forge-redis
    network_mode: host
    logging: *default-logging
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:-bnkforge_redis_dev}
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD:-bnkforge_redis_dev}
    volumes:
      - redis_data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a $$REDIS_PASSWORD ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  # Scoped Docker Engine API proxy for the artifact (container-image) engine.
  # Publishes ONLY to the host loopback (127.0.0.1:2375) — NOT network_mode:
  # host, which would expose a root-equivalent Docker API on 0.0.0.0 (the VSI
  # has a public floating IP). Perms: CONTAINERS/POST/IMAGES/AUTH is exactly
  # what the runner needs (pull, create/start/wait/logs/--rm, volume mounts).
  docker-socket-proxy:
    image: tecnativa/docker-socket-proxy:0.3.0
    container_name: bnk-forge-docker-socket-proxy
    ports:
      - "127.0.0.1:2375:2375"
    logging: *default-logging
    environment:
      CONTAINERS: "1"
      POST: "1"
      IMAGES: "1"
      AUTH: "1"
      EXEC: "0"
      VOLUMES: "0"
      NETWORKS: "0"
      INFO: "0"
      SYSTEM: "0"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped

  backend:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/your-org}/bnk-forge-api:${BNK_FORGE_VERSION:-latest}
    container_name: bnk-forge-backend
    network_mode: host
    logging: *default-logging
    environment:
      <<: *backend-env
      BNK_FORGE_DEPLOY_MODE: ${BNK_FORGE_DEPLOY_MODE:-server}
      HOST_REPO_PATH: ${HOST_REPO_PATH:-}
    volumes:
      - module_catalog:/tmp/bnk-forge-modules
      - bnk-forge-data:/app/projects
      - bnk-forge-keys:/app/keys
      - state_data:/app/state
      - helm_cache:/home/bnkforge/.cache/helm
      - helm_config:/home/bnkforge/.config/helm
      - helm_charts:/app/helm_charts
      - workspace_data:/app/workspaces
      - ./secrets:/app/secrets:ro
      - ./VERSION:/app/VERSION:ro
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/system/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  celery-worker:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/your-org}/bnk-forge-worker:${BNK_FORGE_VERSION:-latest}
    container_name: bnk-forge-celery-worker
    network_mode: host
    logging: *default-logging
    command: celery -A celery_app worker --loglevel=info --concurrency=4 --queues=default,opentofu,orchestrator
    environment:
      <<: *backend-env
    volumes: *worker-volumes
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      backend:
        condition: service_healthy
    restart: unless-stopped

  celery-worker-2:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/your-org}/bnk-forge-worker:${BNK_FORGE_VERSION:-latest}
    container_name: bnk-forge-celery-worker-2
    network_mode: host
    logging: *default-logging
    command: celery -A celery_app worker --loglevel=info --concurrency=4 --queues=default,opentofu
    environment:
      <<: *backend-env
    volumes: *worker-volumes
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      backend:
        condition: service_healthy
    restart: unless-stopped

  celery-beat:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/your-org}/bnk-forge-beat:${BNK_FORGE_VERSION:-latest}
    container_name: bnk-forge-celery-beat
    network_mode: host
    logging: *default-logging
    environment:
      <<: *backend-env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      backend:
        condition: service_healthy
    restart: unless-stopped

  frontend:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/your-org}/bnk-forge-frontend:${BNK_FORGE_VERSION:-latest}
    container_name: bnk-forge-frontend
    network_mode: host
    logging: *default-logging
    depends_on:
      backend:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:8080/ || exit 1"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

  proxy:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/your-org}/bnk-forge-proxy:${BNK_FORGE_VERSION:-latest}
    container_name: bnk-forge-proxy
    network_mode: host
    logging: *default-logging
    depends_on:
      frontend:
        condition: service_healthy
      backend:
        condition: service_healthy
    restart: unless-stopped

  mcp:
    image: ${BNK_FORGE_REGISTRY:-ghcr.io/your-org}/bnk-forge-mcp:${BNK_FORGE_VERSION:-latest}
    container_name: bnk-forge-mcp
    network_mode: host
    logging: *default-logging
    environment:
      BNK_FORGE_API_URL: http://localhost:8000
      BNK_FORGE_USERNAME: ${MCP_USERNAME:-admin}
      BNK_FORGE_PASSWORD: ${MCP_PASSWORD:-changeme}
      MCP_PORT: "8081"
      MCP_LOG_LEVEL: INFO
    depends_on:
      backend:
        condition: service_healthy
    restart: unless-stopped

volumes:
  module_catalog: { driver: local }
  bnk-forge-data: { driver: local }
  bnk-forge-keys: { driver: local }
  state_data: { driver: local }
  workspace_data: { driver: local }
  helm_cache: { driver: local }
  helm_config: { driver: local }
  helm_charts: { driver: local }
  postgres_data: { driver: local }
  redis_data: { driver: local }
YAML
  # Splice the embedded compose into the placeholder.
  python3 - "${UD}" "${EMB}" <<'PYEOF'
import sys
ud, src = sys.argv[1], sys.argv[2]
with open(src) as f: compose = f.read()
with open(ud) as f: data = f.read()
data = data.replace("__COMPOSE__", compose.rstrip("\n"))
with open(ud, "w") as f: f.write(data)
PYEOF
fi

# Substitute the scalar placeholders (| delimiter avoids clashes with / in registry).
sed -i \
  -e "s|__VERSION__|${BNK_FORGE_VERSION}|g" \
  -e "s|__REGISTRY__|${REGISTRY}|g" \
  -e "s|__REGISTRY_HOST__|${REGISTRY_HOST}|g" \
  -e "s|__REGISTRY_USER__|${REGISTRY_USER}|g" \
  -e "s|__PUBLIC__|${PUBLIC}|g" \
  "${UD}"
# PAT last and on its own (may contain no sed-special chars for GitHub PATs).
PAT_ESCAPED="$(printf '%s' "${PAT}" | sed -e 's/[&|\\]/\\&/g')"
sed -i "s|__PAT__|${PAT_ESCAPED}|g" "${UD}"

# ── Create VPC + subnet + security-group rules ───────────────────────────────
log "Creating VPC '${VPC_NAME}'..."
VPC_JSON="$(ibmcloud is vpc-create "${VPC_NAME}" --output json)"
VPC_ID="$(echo "${VPC_JSON}" | jq -r '.id')"
SG_ID="$(echo "${VPC_JSON}" | jq -r '.default_security_group.id')"
[ -n "${VPC_ID}" ] && [ "${VPC_ID}" != "null" ] || die "VPC creation failed."

log "Creating subnet '${SUBNET_NAME}' in ${ZONE}..."
SUBNET_JSON="$(ibmcloud is subnet-create "${SUBNET_NAME}" "${VPC_ID}" --zone "${ZONE}" --ipv4-address-count 256 --output json)"
SUBNET_ID="$(echo "${SUBNET_JSON}" | jq -r '.id')"
[ -n "${SUBNET_ID}" ] && [ "${SUBNET_ID}" != "null" ] || die "Subnet creation failed."

log "Opening inbound 22/80/443 on the default security group..."
for port in 22 80 443; do
  ibmcloud is security-group-rule-add "${SG_ID}" inbound tcp --port-min "${port}" --port-max "${port}" --remote 0.0.0.0/0 >/dev/null
done

# ── Create the VSI ───────────────────────────────────────────────────────────
log "Creating VSI '${VM_NAME}' (${PROFILE})..."
INST_JSON="$(ibmcloud is instance-create "${VM_NAME}" "${VPC_ID}" "${ZONE}" "${PROFILE}" "${SUBNET_ID}" \
  --image "${IMAGE_ID}" --keys "${SSH_KEY_NAME}" --user-data "$(cat "${UD}")" --output json)"
VM_ID="$(echo "${INST_JSON}" | jq -r '.id')"
[ -n "${VM_ID}" ] && [ "${VM_ID}" != "null" ] || die "Instance creation failed."

log "Waiting for the VSI to reach 'running'..."
for _ in $(seq 1 60); do
  ST="$(ibmcloud is instance "${VM_ID}" --output json | jq -r '.status')"
  [ "${ST}" = "running" ] && break
  [ "${ST}" = "failed" ] && die "Instance entered 'failed' state."
  sleep 5
done

# ── Attach a floating IP (appropriate to the network interface type) ─────────
# Modern VSIs expose a virtual network interface (VNI) behind a network
# attachment — bind the FIP to the VNI (--vni). Only genuinely old instances
# expose a bindable classic network interface with no attachment (--nic). Note
# that .primary_network_interface.id on a VNI instance is the *attachment* id,
# which cannot take a floating IP directly, so the VNI path must be preferred.
INST_NOW="$(ibmcloud is instance "${VM_ID}" --output json)"
VNI_ID="$(echo "${INST_NOW}" | jq -r '.primary_network_attachment.virtual_network_interface.id // empty')"
NIC_ID="$(echo "${INST_NOW}" | jq -r '.primary_network_interface.id // empty')"

if [ -n "${VNI_ID}" ]; then
  log "Reserving floating IP and binding to virtual network interface ${VNI_ID}..."
  FIP_JSON="$(ibmcloud is floating-ip-reserve "${FIP_NAME}" --vni "${VNI_ID}" --output json)"
elif [ -n "${NIC_ID}" ]; then
  log "Reserving floating IP and binding to network interface ${NIC_ID}..."
  FIP_JSON="$(ibmcloud is floating-ip-reserve "${FIP_NAME}" --nic "${NIC_ID}" --output json)"
else
  die "Could not determine the instance's primary network interface type."
fi
FIP="$(echo "${FIP_JSON}" | jq -r '.address')"
[ -n "${FIP}" ] && [ "${FIP}" != "null" ] || die "Failed to obtain a floating IP address."
log "Floating IP: ${FIP}"

# ── Wait for bnk-forge to come up (Docker install + image pulls take minutes) ─
URL="https://${FIP}"
log "Installing bnk-forge on the VSI (this can take 5–10 minutes)..."
READY=0
for _ in $(seq 1 90); do
  CODE="$(curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 5 "${URL}/api/system/health" 2>/dev/null || true)"
  if [ "${CODE}" = "200" ]; then READY=1; break; fi
  sleep 10
done

echo
echo "========================================================================"
if [ "${READY}" = "1" ]; then
  echo "  ✅ BNK Forge is up."
else
  echo "  ⚠  BNK Forge did not report healthy within the wait window."
  echo "     It may still be pulling images. Check on the host:"
  echo "       ssh ubuntu@${FIP} 'sudo tail -f /var/log/bnk-forge-install.log'"
fi
echo
echo "  URL:      ${URL}"
echo "            (self-signed certificate — accept the browser warning)"
echo "  Login:    admin / changeme   (change on first login)"
echo
echo "  Host IP:  ${FIP}   (SSH: ssh ubuntu@${FIP})"
echo "  Region:   ${REGION} / ${ZONE}    Profile: ${PROFILE}    Image: Ubuntu 24.04"
echo "  Images:   ${REGISTRY}/bnk-forge-*:${BNK_FORGE_VERSION}"
echo
echo "  Created resources (for teardown):"
echo "    instance      ${VM_NAME}      (${VM_ID})"
echo "    floating-ip   ${FIP_NAME}"
echo "    subnet        ${SUBNET_NAME}  (${SUBNET_ID})"
echo "    vpc           ${VPC_NAME}     (${VPC_ID})"
echo "    Teardown: ibmcloud is instance-delete ${VM_ID} --force && \\"
echo "              ibmcloud is floating-ip-release ${FIP_NAME} --force && \\"
echo "              ibmcloud is subnet-delete ${SUBNET_ID} --force && \\"
echo "              ibmcloud is vpc-delete ${VPC_ID} --force"
echo "========================================================================"

# Emit the URL as the final line for easy scripting.
echo "${URL}"
