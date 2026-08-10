#!/usr/bin/env bash
# =============================================================================
# artifact_network.sh — resolve + create the bnk-forge-artifacts bridge network
# =============================================================================
#
# Single source of truth for the artifact-runner network subnet, used by the
# Makefile, scripts/build.sh, dist/install.sh, and scripts/ibm_cloud_bnk_forge.sh
# (the last two carry a standalone inline copy since they ship without this
# file — keep them in sync by hand).
#
# Background (issue #422): the container-image engine attaches artifact steps
# to a dedicated bridge network so they don't share the default bridge. A
# fixed default subnet collides with some hosts' VPN/management routes,
# cutting off connectivity mid-deploy. A *second* fixed default
# (10.200.0.0/24) also collided on a field site — no single hardcoded subnet
# is safe everywhere, so this auto-detects one against the host's routing
# table and existing docker networks, with full override support.
#
# Resolution order (only when the network does NOT already exist):
#   1. ARTIFACT_NETWORK_SUBNET set:
#        - "auto"   -> create with no --subnet, defer to Docker's
#                      default-address-pools (daemon.json)
#        - <cidr>   -> use verbatim
#   2. Unset/empty -> try each ARTIFACT_NETWORK_SUBNET_CANDIDATES entry in
#      order, picking the first that overlaps neither the host routing table
#      nor any existing docker network's subnet.
#   3. No candidate clean -> create with no --subnet (same as "auto") and
#      print a warning with remediation options.
#
# If the network already exists, resolution is skipped entirely — there is
# nothing to decide. This matters because once a network is created, its own
# subnet becomes BOTH a host route (docker installs one for the bridge) and
# an existing docker network subnet; re-running auto-detection at that point
# would count the network's own subnet as a collision with itself, walk to
# the next candidate, and print a mismatch warning that never converges (see
# review r2 on issue #422). So:
#   - `ensure` on an existing network is a no-op, except:
#   - it still warns on mismatch, but ONLY when ARTIFACT_NETWORK_SUBNET is
#     explicitly set to a literal CIDR (not unset, not "auto") and the
#     existing network's subnet differs from it. Auto-detected values never
#     produce a warning, since there's no fixed expectation to compare against.
#   - `resolve` on an existing network prints its ACTUAL current subnet (via
#     docker inspect) instead of running detection, for consistency with what
#     `ensure` would do.
#
# Usage:
#   scripts/artifact_network.sh ensure       # create/verify the network
#   scripts/artifact_network.sh resolve      # print the resolved/actual subnet or "auto"
#   scripts/artifact_network.sh --self-test  # pure-logic unit tests, no docker/routes
#
set -u

ARTIFACT_NETWORK="${ARTIFACT_NETWORK:-bnk-forge-artifacts}"
DEFAULT_CANDIDATES="10.200.0.0/24 192.168.200.0/24 10.213.37.0/24 172.31.255.0/24"

# ---- .env (CWD) — read only the two ARTIFACT_NETWORK_* keys; never source ----
# Env vars always win: only consulted when the variable isn't already set.
_env_key() {
  key="$1"
  [ -f .env ] || return 0
  grep -E "^[[:space:]]*${key}=" .env 2>/dev/null | tail -n1 \
    | cut -d= -f2- | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
        -e "s/^[\"']//" -e "s/[\"']\$//"
}

if [ -z "${ARTIFACT_NETWORK_SUBNET+x}" ]; then
  _v="$(_env_key ARTIFACT_NETWORK_SUBNET)"
  [ -n "$_v" ] && ARTIFACT_NETWORK_SUBNET="$_v"
fi
if [ -z "${ARTIFACT_NETWORK_SUBNET_CANDIDATES+x}" ]; then
  _v="$(_env_key ARTIFACT_NETWORK_SUBNET_CANDIDATES)"
  [ -n "$_v" ] && ARTIFACT_NETWORK_SUBNET_CANDIDATES="$_v"
fi
unset _v

ARTIFACT_NETWORK_SUBNET="${ARTIFACT_NETWORK_SUBNET:-}"
ARTIFACT_NETWORK_SUBNET_CANDIDATES="${ARTIFACT_NETWORK_SUBNET_CANDIDATES:-$DEFAULT_CANDIDATES}"

# ---- CIDR overlap math (pure awk, no bc/python; portable to BSD/GNU awk) ----
# Two CIDR blocks overlap iff each network's start address falls at or before
# the other's broadcast address. Prefix masks are computed by floor-division
# rather than bitwise AND, since POSIX awk has no portable bitwise operators.
cidr_overlap() {
  awk -v c1="$1" -v c2="$2" '
    function pow2(n,    r, i) { r = 1; for (i = 0; i < n; i++) r = r * 2; return r }
    function ip2int(ip,    p, n) {
      n = split(ip, p, ".")
      if (n != 4) return -1
      return p[1] * 16777216 + p[2] * 65536 + p[3] * 256 + p[4]
    }
    function net_start(ipint, prefix,    blocksz) {
      blocksz = pow2(32 - prefix)
      return int(ipint / blocksz) * blocksz
    }
    function net_end(ipint, prefix,    blocksz) {
      blocksz = pow2(32 - prefix)
      return net_start(ipint, prefix) + blocksz - 1
    }
    BEGIN {
      n1 = split(c1, a1, "/"); ip1 = ip2int(a1[1]); p1 = (n1 == 2 ? a1[2] : 32)
      n2 = split(c2, a2, "/"); ip2 = ip2int(a2[1]); p2 = (n2 == 2 ? a2[2] : 32)
      if (ip1 < 0 || ip2 < 0 || p1 < 0 || p1 > 32 || p2 < 0 || p2 > 32) exit 1
      s1 = net_start(ip1, p1); e1 = net_end(ip1, p1)
      s2 = net_start(ip2, p2); e2 = net_end(ip2, p2)
      exit (s1 <= e2 && s2 <= e1) ? 0 : 1
    }
  '
}

# ---- Pure candidate selection (no docker/route I/O — testable in isolation) ----
# _candidate_is_clean CAND EXCLUDE COLLISION...
#   True unless CAND overlaps some entry in COLLISION (a space-separated blob
#   of routes + docker subnets), ignoring any entry equal to EXCLUDE. EXCLUDE
#   exists so a caller can ask "is this candidate clean, disregarding a
#   subnet we know is our own" — used only by --self-test today, since the
#   real ensure/resolve flow skips detection entirely once the network
#   exists (see header). Kept as a first-class parameter so that guarantee
#   is covered by a docker-free regression test rather than just prose.
_candidate_is_clean() {
  cand="$1"; exclude="$2"; shift 2
  for item in "$@"; do
    [ -n "$exclude" ] && [ "$item" = "$exclude" ] && continue
    if cidr_overlap "$cand" "$item"; then
      return 1
    fi
  done
  return 0
}

# _select_candidate "CAND1 CAND2 ..." EXCLUDE "COLLISION1 COLLISION2 ..."
#   Prints the first candidate clean of COLLISION (modulo EXCLUDE), or "auto"
#   if none are clean.
_select_candidate() {
  candidates="$1"; exclude="$2"; collisions="$3"
  for cand in $candidates; do
    if _candidate_is_clean "$cand" "$exclude" $collisions; then
      echo "$cand"
      return 0
    fi
  done
  echo "auto"
}

self_test() {
  ok=0
  fail=0
  _case() {
    desc="$1"; a="$2"; b="$3"; want="$4"
    if cidr_overlap "$a" "$b"; then got="overlap"; else got="disjoint"; fi
    if [ "$got" = "$want" ]; then
      ok=$((ok + 1))
      echo "PASS: $desc ($a vs $b -> $got)"
    else
      fail=$((fail + 1))
      echo "FAIL: $desc ($a vs $b -> $got, expected $want)"
    fi
  }
  _case "overlap (identical)"        "10.200.0.0/24"  "10.200.0.0/24"   overlap
  _case "disjoint"                   "192.168.1.0/24" "192.168.2.0/24"  disjoint
  _case "containing (/16 vs /24)"    "10.200.0.0/16"  "10.200.5.0/24"   overlap
  _case "touching boundary"          "10.200.0.0/24"  "10.200.1.0/24"   disjoint
  _case "/16 vs /24 overlap"         "172.16.0.0/16"  "172.16.55.0/24"  overlap

  _case_select() {
    desc="$1"; candidates="$2"; exclude="$3"; collisions="$4"; want="$5"
    got="$(_select_candidate "$candidates" "$exclude" "$collisions")"
    if [ "$got" = "$want" ]; then
      ok=$((ok + 1))
      echo "PASS: $desc -> $got"
    else
      fail=$((fail + 1))
      echo "FAIL: $desc -> $got, expected $want"
    fi
  }
  # Regression for the self-collision bug (review r2): once a network exists
  # at candidate #1, its own subnet shows up as both a host route and a
  # docker network subnet. Without exclusion, that walks resolution away to
  # candidate #2 every time and the mismatch warning never converges. With
  # the network's own subnet passed as EXCLUDE, resolution stays put.
  _case_select "self-collision excluded stays on own subnet" \
    "10.200.0.0/24 192.168.200.0/24" "10.200.0.0/24" "10.200.0.0/24" \
    "10.200.0.0/24"
  # Same inputs, no exclusion: demonstrates the bug this guards against —
  # the unexcluded "own subnet" collision pushes selection to candidate #2.
  _case_select "unexcluded self-collision walks to next candidate" \
    "10.200.0.0/24 192.168.200.0/24" "" "10.200.0.0/24" \
    "192.168.200.0/24"
  # A genuine collision distinct from the excluded subnet is still honored —
  # exclusion is an exact-match skip, not a blanket "ignore all collisions".
  _case_select "real collision (not the excluded subnet) still skips candidate" \
    "10.200.0.0/24 192.168.200.0/24" "10.200.0.0/24" "10.200.0.128/25" \
    "192.168.200.0/24"

  echo "---"
  echo "$ok passed, $fail failed"
  [ "$fail" -eq 0 ]
}

# ---- Host routing table (destination CIDRs only, no default route) ----
_linux_routes() {
  command -v ip >/dev/null 2>&1 || return 0
  ip route show 2>/dev/null | awk '$1 != "default" && $1 ~ /\// { print $1 }'
}

_macos_routes() {
  command -v netstat >/dev/null 2>&1 || return 0
  netstat -rn -f inet 2>/dev/null | awk '
    $1 == "Destination" || $1 == "default" { next }
    $1 ~ /^(link#|lo0|127($|\.)|169\.254)/ { next }
    $1 ~ /^[0-9]+(\.[0-9]+){0,3}(\/[0-9]+)?$/ {
      dest = $1
      if (dest ~ /\//) { print dest; next }
      n = split(dest, o, ".")
      for (i = n + 1; i <= 4; i++) o[i] = 0
      printf "%s.%s.%s.%s/%d\n", o[1], o[2], o[3], o[4], n * 8
    }
  '
}

_collect_routes() {
  case "$(uname -s 2>/dev/null)" in
    Darwin) _macos_routes ;;
    *) _linux_routes || _macos_routes ;;
  esac
}

# ---- Existing docker network subnets (empty if docker absent/unreachable) ----
_docker() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 3 docker "$@" 2>/dev/null
  else
    docker "$@" 2>/dev/null
  fi
}

_docker_subnets() {
  command -v docker >/dev/null 2>&1 || return 0
  ids="$(_docker network ls -q)"
  [ -n "$ids" ] || return 0
  for nid in $ids; do
    _docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}} {{end}}' "$nid"
  done
}

# ---- Resolution ----
# Prints the resolved/actual subnet CIDR, or "auto" when Docker should pick.
# If the network already exists, prints its actual current subnet instead of
# running detection (see header) — this keeps `resolve` consistent with what
# `ensure` does for an existing network.
resolve_subnet() {
  if docker network inspect "$ARTIFACT_NETWORK" >/dev/null 2>&1; then
    existing_subnet="$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$ARTIFACT_NETWORK" 2>/dev/null)"
    echo "${existing_subnet:-auto}"
    return 0
  fi

  if [ -n "$ARTIFACT_NETWORK_SUBNET" ]; then
    if [ "$ARTIFACT_NETWORK_SUBNET" = "auto" ]; then
      echo "auto"
    else
      echo "$ARTIFACT_NETWORK_SUBNET"
    fi
    return 0
  fi

  routes="$(_collect_routes)"
  docker_subnets="$(_docker_subnets)"
  _select_candidate "$ARTIFACT_NETWORK_SUBNET_CANDIDATES" "" "$routes $docker_subnets"
}

_warn_no_clean_candidate() {
  echo "=========================================================="
  echo "  WARNING: no candidate subnet is free of host-route or"
  echo "  docker-network collisions:"
  echo "    $ARTIFACT_NETWORK_SUBNET_CANDIDATES"
  echo "  Creating $ARTIFACT_NETWORK without --subnet (Docker will pick"
  echo "  from its default pool, which may still collide)."
  echo ""
  echo "  Recommended: set ARTIFACT_NETWORK_SUBNET=<cidr> to a subnet you"
  echo "  know is free, or dedicate a Docker default-address-pool to this"
  echo "  network via /etc/docker/daemon.json:"
  echo '    { "default-address-pools": [ { "base": "192.168.200.0/20", "size": 24 } ] }'
  echo "  then restart docker."
  echo "=========================================================="
}

# Mismatch warning only makes sense against a fixed, configured expectation
# (an explicit CIDR). Auto-detected values are never compared here — there's
# nothing wrong to report, since detection just means "whatever's there is
# fine" (see header for why re-detecting against an existing network is
# actively wrong, not just unhelpful).
_warn_if_mismatch_explicit() {
  expected="$1"
  existing_subnet=$(docker network inspect -f '{{(index .IPAM.Config 0).Subnet}}' "$ARTIFACT_NETWORK" 2>/dev/null)
  if [ -n "$existing_subnet" ] && [ "$existing_subnet" != "$expected" ]; then
    echo "=========================================================="
    echo "  WARNING: $ARTIFACT_NETWORK subnet does not match config"
    echo "  Existing subnet:   $existing_subnet"
    echo "  Configured subnet: $expected"
    echo "  This network was not recreated (containers may be attached)."
    echo "  To apply the configured subnet: stop the stack, run"
    echo "    docker network rm $ARTIFACT_NETWORK"
    echo "  then re-run."
    echo "=========================================================="
  fi
}

ensure_network() {
  if docker network inspect "$ARTIFACT_NETWORK" >/dev/null 2>&1; then
    # Already exists: nothing to resolve. Only warn if the operator pinned an
    # explicit CIDR (not unset, not "auto") that doesn't match what's there.
    if [ -n "$ARTIFACT_NETWORK_SUBNET" ] && [ "$ARTIFACT_NETWORK_SUBNET" != "auto" ]; then
      _warn_if_mismatch_explicit "$ARTIFACT_NETWORK_SUBNET"
    fi
    return 0
  fi

  resolved="$(resolve_subnet)"
  echo "=== Creating artifact runner network ($ARTIFACT_NETWORK) ==="
  if [ "$resolved" = "auto" ]; then
    if [ "$ARTIFACT_NETWORK_SUBNET" = "auto" ]; then
      echo "  ARTIFACT_NETWORK_SUBNET=auto: deferring to Docker's default-address-pools."
    else
      _warn_no_clean_candidate
    fi
    docker network create --driver bridge "$ARTIFACT_NETWORK" >/dev/null
  else
    docker network create --driver bridge --subnet "$resolved" "$ARTIFACT_NETWORK" >/dev/null
  fi
}

case "${1:-}" in
  ensure) ensure_network ;;
  resolve) resolve_subnet ;;
  --self-test) self_test ;;
  *)
    echo "Usage: $0 {ensure|resolve|--self-test}" >&2
    exit 1
    ;;
esac
