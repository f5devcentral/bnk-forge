#!/usr/bin/env bash
# =============================================================================
# BNK Forge — One-Command Installer
# =============================================================================
#
# Usage:
#   ./install.sh                  # Linux server (host networking)
#   ./install.sh --local          # macOS / Windows laptop (bridge networking)
#   ./install.sh --help           # Show help
#
# Prerequisites:
#   - Docker Engine 24+ with Compose v2.24+
#   - Network access to ghcr.io (images are public — no registry login required)
#
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="server"
COMPOSE_CMD="docker compose"

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --local|-l)
      MODE="local"
      shift
      ;;
    --help|-h)
      echo "BNK Forge Installer"
      echo ""
      echo "Usage:"
      echo "  ./install.sh            Install on Linux server (host networking)"
      echo "  ./install.sh --local    Install on macOS/Windows laptop (bridge networking)"
      echo ""
      echo "Before running, copy .env.example to .env and set your registry + passwords:"
      echo "  cp .env.example .env"
      echo "  \$EDITOR .env"
      exit 0
      ;;
    *)
      echo "Unknown option: $1"
      echo "Run: ./install.sh --help"
      exit 1
      ;;
  esac
done

cd "$SCRIPT_DIR"

# ── Preflight checks ────────────────────────────────────────────────────────
echo ""
echo "========================================="
echo "  BNK Forge — Installation"
echo "========================================="
echo ""
echo "  Mode:    $MODE"
echo "  Version: $(cat VERSION 2>/dev/null || echo 'unknown')"
echo ""

# Check Docker
if ! command -v docker &>/dev/null; then
  echo "ERROR: Docker is not installed. Install Docker Engine 24+ first."
  exit 1
fi

# Check Docker Compose v2
if ! docker compose version &>/dev/null; then
  echo "ERROR: Docker Compose v2 is not available."
  echo "  Install: https://docs.docker.com/compose/install/"
  exit 1
fi

COMPOSE_VERSION=$(docker compose version --short 2>/dev/null || echo "0.0.0")
echo "  Docker Compose: $COMPOSE_VERSION"

# Check .env
if [ ! -f .env ]; then
  echo ""
  echo "  No .env file found. Creating from .env.example..."
  if [ -f .env.example ]; then
    cp .env.example .env
    echo "  ✓ Created .env from .env.example"
    echo "  ⚠  Review .env before continuing. In particular:"
    echo "       - BNK_FORGE_REGISTRY (where to pull images)"
    echo "       - MCP_SERVICE_PASSWORD (bonnyr-f5 #193: ships EMPTY; the MCP/AI"
    echo "         assistant integration stays unavailable until you set it)"
    echo ""
    echo "  Edit: nano .env"
    echo "  Then re-run: ./install.sh $([ "$MODE" = "local" ] && echo "--local")"
    exit 1
  else
    echo "  ERROR: No .env.example found. Cannot continue."
    exit 1
  fi
fi

# Create secrets directory if missing
mkdir -p secrets

# ── Set compose command based on mode ────────────────────────────────────────
if [ "$MODE" = "local" ]; then
  COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.local.yml"
  echo "  Networking: bridge (Docker Desktop)"
else
  COMPOSE_CMD="docker compose"
  echo "  Networking: host (Linux server)"
fi

# ── Pull images ──────────────────────────────────────────────────────────────
echo ""
echo "=== Pulling images from registry ==="
$COMPOSE_CMD pull
echo "  ✓ All images pulled"

# ── Fix volume permissions ───────────────────────────────────────────────────
echo ""
echo "=== Configuring volume permissions ==="
# Project name must match what docker compose uses (volumes are <project>_<name>).
# Compose reads COMPOSE_PROJECT_NAME from .env; otherwise it normalizes the
# directory name (lowercase, invalid chars like '.' stripped).
PROJECT=$(grep -E '^COMPOSE_PROJECT_NAME=' .env 2>/dev/null | head -1 | cut -d= -f2)
if [ -z "$PROJECT" ]; then
  PROJECT=$(basename "$SCRIPT_DIR" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9_-]//g')
fi
docker run --rm \
  -v "${PROJECT}_bnk-forge-data:/app/projects" \
  -v "${PROJECT}_bnk-forge-keys:/app/keys" \
  -v "${PROJECT}_state_data:/app/state" \
  -v "${PROJECT}_helm_cache:/home/bnkforge/.cache/helm" \
  -v "${PROJECT}_helm_config:/home/bnkforge/.config/helm" \
  -v "${PROJECT}_helm_charts:/app/helm_charts" \
  -v "${PROJECT}_workspace_data:/app/workspaces" \
  alpine:latest sh -c " \
    mkdir -p /app/projects /app/keys /app/state /app/helm_charts /app/workspaces \
      /home/bnkforge/.cache/helm /home/bnkforge/.config/helm && \
    chown -R 1000:1000 /app/projects /app/keys /app/state /app/helm_charts \
      /app/workspaces /home/bnkforge" \
  2>/dev/null && echo "  ✓ Volume permissions configured" \
  || echo "  ⚠  Could not pre-configure permissions (may work anyway)"

# ── Artifact runner network ─────────────────────────────────────────────────
# The container-image engine attaches artifact steps to this dedicated bridge
# network (keeps them off the default bridge while preserving the egress they
# need to reach cloud control planes). It must be created explicitly: no service
# joins it — under host networking a service cannot also join a bridge network —
# and compose does not create a network that no service references.
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
# This is a compact copy of scripts/artifact_network.sh's resolution logic for
# this standalone bundle (it skips the docker-network cross-check to stay
# short, but always checks host routes).
#
# If the network already EXISTS, resolution is skipped entirely: once
# created, its own subnet becomes both a host route and (in the full script)
# an existing docker network subnet, so re-detecting would count it as a
# collision with itself and walk away every time — a mismatch warning that
# never converges (review r2 on issue #422). Re-running install.sh only warns
# when ARTIFACT_NETWORK_SUBNET is an explicit literal CIDR that doesn't match
# what's actually there; auto-detected values never warn.
ARTIFACT_NETWORK="${ARTIFACT_NETWORK:-bnk-forge-artifacts}"
if [ -z "${ARTIFACT_NETWORK_SUBNET+x}" ] && [ -f .env ]; then
  _an_v="$(grep -E '^[[:space:]]*ARTIFACT_NETWORK_SUBNET=' .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  [ -n "$_an_v" ] && ARTIFACT_NETWORK_SUBNET="$_an_v"
fi
if [ -z "${ARTIFACT_NETWORK_SUBNET_CANDIDATES+x}" ] && [ -f .env ]; then
  _an_v="$(grep -E '^[[:space:]]*ARTIFACT_NETWORK_SUBNET_CANDIDATES=' .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  [ -n "$_an_v" ] && ARTIFACT_NETWORK_SUBNET_CANDIDATES="$_an_v"
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
  if [ -n "$ARTIFACT_NETWORK_SUBNET" ]; then
    echo "$ARTIFACT_NETWORK_SUBNET"
    return 0
  fi
  _an_routes_out="$(_an_routes)"
  for _an_cand in $ARTIFACT_NETWORK_SUBNET_CANDIDATES; do
    _an_clean=true
    for _an_r in $_an_routes_out; do
      if _an_overlap "$_an_cand" "$_an_r"; then _an_clean=false; break; fi
    done
    if [ "$_an_clean" = true ]; then
      echo "$_an_cand"
      return 0
    fi
  done
  echo "auto"
}

if docker network inspect "$ARTIFACT_NETWORK" >/dev/null 2>&1; then
  # Already exists: nothing to resolve. Only warn if the operator pinned an
  # explicit CIDR (not unset, not "auto") that doesn't match what's there.
  if [ -n "$ARTIFACT_NETWORK_SUBNET" ] && [ "$ARTIFACT_NETWORK_SUBNET" != "auto" ]; then
    existing_subnet=$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$ARTIFACT_NETWORK" 2>/dev/null || true)
    if [ -n "$existing_subnet" ] && [ "$existing_subnet" != "$ARTIFACT_NETWORK_SUBNET" ]; then
      echo ""
      echo "=========================================================="
      echo "  WARNING: $ARTIFACT_NETWORK subnet does not match config"
      echo "  Existing subnet:   $existing_subnet"
      echo "  Configured subnet: $ARTIFACT_NETWORK_SUBNET"
      echo "  This network was not recreated (containers may be attached)."
      echo "  To apply the configured subnet: stop the stack, run"
      echo "    docker network rm $ARTIFACT_NETWORK"
      echo "  then re-run this script."
      echo "=========================================================="
    fi
  fi
else
  ARTIFACT_NETWORK_RESOLVED="$(_an_resolve)"
  echo ""
  echo "=== Creating artifact runner network ($ARTIFACT_NETWORK) ==="
  if [ "$ARTIFACT_NETWORK_RESOLVED" = "auto" ]; then
    if [ "$ARTIFACT_NETWORK_SUBNET" = "auto" ]; then
      echo "  ARTIFACT_NETWORK_SUBNET=auto: deferring to Docker's default-address-pools."
    else
      echo "=========================================================="
      echo "  WARNING: no candidate subnet is free of host-route collisions:"
      echo "    $ARTIFACT_NETWORK_SUBNET_CANDIDATES"
      echo "  Creating $ARTIFACT_NETWORK without --subnet (Docker will pick from its"
      echo "  default pool, which may still collide)."
      echo "  Recommended: set ARTIFACT_NETWORK_SUBNET=<cidr>, or dedicate a Docker"
      echo "  default-address-pool via /etc/docker/daemon.json:"
      echo '    { "default-address-pools": [ { "base": "192.168.200.0/20", "size": 24 } ] }'
      echo "  then restart docker."
      echo "=========================================================="
    fi
    docker network create --driver bridge "$ARTIFACT_NETWORK" >/dev/null
  else
    docker network create --driver bridge --subnet "$ARTIFACT_NETWORK_RESOLVED" "$ARTIFACT_NETWORK" >/dev/null
  fi
fi

# ── Start infrastructure ────────────────────────────────────────────────────
echo ""
echo "=== Starting infrastructure (postgres, redis) ==="
$COMPOSE_CMD up -d postgres redis
echo "  Waiting for database..."
for i in $(seq 1 20); do
  if docker exec bnk-forge-postgres pg_isready -U bnkforge > /dev/null 2>&1; then
    echo "  ✓ Database ready"
    break
  fi
  if [ "$i" = "20" ]; then
    echo "  ERROR: Database did not become ready within 20s"
    echo "  Check: docker logs bnk-forge-postgres"
    exit 1
  fi
  sleep 1
done

# ── Start all services ──────────────────────────────────────────────────────
echo ""
echo "=== Starting all services ==="
$COMPOSE_CMD up -d

# ── Wait for health ─────────────────────────────────────────────────────────
echo ""
echo "=== Waiting for backend health ==="
for i in $(seq 1 12); do
  if curl -sf http://localhost:8000/api/system/health > /dev/null 2>&1; then
    echo "  ✓ Backend healthy"
    break
  fi
  if [ "$i" = "12" ]; then
    echo "  ⚠  Backend did not become healthy within 60s"
    echo "  Check: docker logs bnk-forge-backend"
  fi
  echo "  Waiting... ($i/12)"
  sleep 5
done

# ── Verify the Docker socket proxy (container-image / artifact engine) ───────
# The artifact deploy engine runs each step as a sibling container through this
# scoped proxy. Without it, a blueprint deploy fails at the first step.
echo ""
echo "=== Verifying container-engine socket proxy ==="
if docker ps --filter name=bnk-forge-docker-socket-proxy --filter status=running -q | grep -q .; then
  echo "  ✓ docker-socket-proxy is running"
  # In server (host-net) mode the proxy publishes to the host loopback, so the
  # Docker API is reachable here at 127.0.0.1:2375 (a scoped, read-only view).
  if [ "$MODE" = "server" ]; then
    if curl -sf http://127.0.0.1:2375/version > /dev/null 2>&1; then
      echo "  ✓ Scoped Docker API reachable at tcp://127.0.0.1:2375"
    else
      echo "  ⚠  Proxy is up but tcp://127.0.0.1:2375/version did not respond yet."
    fi
  fi
else
  echo "  ⚠  docker-socket-proxy is NOT running — the container-image (artifact)"
  echo "     deploy engine will fail at its first step."
  echo "     Check: docker logs bnk-forge-docker-socket-proxy"
fi

# ── Show status ─────────────────────────────────────────────────────────────
echo ""
$COMPOSE_CMD ps
echo ""

# Determine access URL
if [ "$MODE" = "local" ]; then
  URL="https://localhost"
else
  HOST_IP=$(ip route get 1 2>/dev/null | awk '{print $7; exit}' \
    || hostname -I 2>/dev/null | awk '{print $1}' \
    || echo "localhost")
  if [ -z "$HOST_IP" ] || [ "$HOST_IP" = "127.0.0.1" ]; then HOST_IP="localhost"; fi
  URL="https://$HOST_IP"
fi

# bonnyr-f5 #193 (minor): the shipped .env leaves MCP_SERVICE_PASSWORD empty, so a
# default install brings the mcp service up but its auth probe can never pass (the
# backend leaves the 'mcp' account unseeded). Surface that here instead of printing
# an unqualified "complete!" — the rest of the stack is genuinely healthy, MCP is not.
MCP_PW=$(grep -E '^[[:space:]]*MCP_SERVICE_PASSWORD=' .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)
# Fall back to the legacy alias the compose files still honor (MCP_PASSWORD).
if [ -z "$MCP_PW" ]; then
  MCP_PW=$(grep -E '^[[:space:]]*MCP_PASSWORD=' .env 2>/dev/null | tail -n1 | cut -d= -f2- || true)
fi

echo "========================================="
echo "  ✅ Installation complete!"
echo ""
echo "  Version: $(cat VERSION 2>/dev/null || echo 'unknown')"
echo ""
if [ -z "$MCP_PW" ]; then
  echo "  ⚠  MCP (AI assistant) integration is NOT active: MCP_SERVICE_PASSWORD is"
  echo "     unset in .env, so the bundled MCP server cannot authenticate and its"
  echo "     health probe will report UNHEALTHY. The rest of the stack is unaffected."
  echo "     To enable it: set MCP_SERVICE_PASSWORD in .env to a strong value and run"
  echo "       $COMPOSE_CMD up -d"
  echo ""
fi
echo "  Open: $URL"
if [ "$URL" != "https://localhost" ]; then
  echo "        (accept the self-signed certificate warning)"
fi
echo ""
echo "  Login: admin  (password: DEFAULT_ADMIN_PASSWORD if set, else run: docker compose exec backend cat /app/keys/initial_admin_password)"
echo ""
echo "  Next steps:"
echo "    1. Change your password on first login"
echo "    2. Browse deployable blueprints in Build → Catalog"
echo "    3. Create your first project in Build → Projects"
echo "========================================="
