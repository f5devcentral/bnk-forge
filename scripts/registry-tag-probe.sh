#!/usr/bin/env bash
# registry-tag-probe.sh — Authoritative "does :VERSION already exist?" probe for
# the BNK Forge release image set, used to protect the IMMUTABLE :VERSION tag.
#
# WHY THIS EXISTS (bonnyr-f5 #181 round 5, F1):
#   The earlier probe shelled out to `docker manifest inspect` and classified by
#   grepping the CLI's combined stdout/stderr. That text CANNOT separate the two
#   things a release must tell apart:
#     • a package/repo that does not exist yet (the first release in a fork or
#       mirror namespace, or a future 8th image) — SAFE to publish, and
#     • no permission to read an existing package — MUST fail closed.
#   Both surface identically as  Get "https://<host>/token…": denied .  Reading
#   `denied` as "not found" fails OPEN (overwrites an immutable tag); reading it
#   as "unknown" fails CLOSED and hard-fails the very first publish in a
#   namespace — after the tag and GitHub Release are already pushed.
#
#   The registry HTTP API answers this unambiguously with a STATUS CODE:
#     200 → the manifest exists                       -> exists
#     404 → definitively not found (MANIFEST_UNKNOWN / NAME_UNKNOWN, i.e. the
#           tag is absent OR the repo does not exist yet) -> absent (safe)
#     401 / 403 → authentication / permission           -> unknown (fail closed)
#     000 / 429 / 5xx / anything else → transient/network -> unknown (fail closed)
#   Only a definitive 404 is treated as "safe to publish"; every other outcome
#   is "unknown" and the CALLER refuses unless a force flag is set.
#
# CONTRACT
#   Env in:
#     REGISTRY  (required)  e.g. ghcr.io/f5devcentral  (host + namespace path)
#     VERSION   (required)  e.g. 3.1.6                  (the immutable tag)
#     REGISTRY_USERNAME / REGISTRY_PASSWORD (optional) — Basic creds used when
#         the registry issues a Bearer challenge. Falls back to an anonymous
#         token request (sufficient for public images); a private/absent repo
#         probed anonymously returns 401/403 → unknown → the caller fails closed.
#   Stdout: one TAB-separated line per image:
#       <status>\t<ref>\t<detail>      status ∈ exists | absent | unknown
#   Exit: 0 once every image is classified (regardless of verdict); non-zero
#         only on a usage error. Policy (refuse / force / message) lives in the
#         caller so the CI path and the operator `make push-images` path can
#         share ONE probe but keep their own force semantics.
#
#   registry-tag-probe.sh --images   prints the canonical image list, one per
#         line, so callers single-source it instead of re-hardcoding 7 names
#         (bonnyr-f5 #181 round 5, F6).
set -euo pipefail

# ─── Canonical image set (single source of truth — F6) ───────────────────────
# MUST stay in lockstep with the "default" group in docker-bake.hcl and the
# IMAGES array in scripts/publish-signed-images.sh. The self-test
# (scripts/tests/registry-tag-probe.test.sh) asserts the count and the
# docker-bake.hcl parity.
IMAGES=(
  "bnk-forge-api"
  "bnk-forge-worker"
  "bnk-forge-beat"
  "bnk-forge-frontend"
  "bnk-forge-proxy"
  "bnk-forge-mcp"
  "bnk-forge-operator"
)

if [ "${1:-}" = "--images" ]; then
  printf '%s\n' "${IMAGES[@]}"
  exit 0
fi

: "${REGISTRY:?REGISTRY is required (e.g. ghcr.io/your-org)}"
: "${VERSION:?VERSION is required (e.g. 3.1.6)}"

HOST="${REGISTRY%%/*}"          # ghcr.io
NAMESPACE="${REGISTRY#*/}"      # f5devcentral  (may be multi-segment)
if [ "$HOST" = "$REGISTRY" ] || [ -z "$NAMESPACE" ]; then
  echo "ERROR: REGISTRY must be <host>/<namespace> (got '$REGISTRY')" >&2
  exit 2
fi

ACCEPT='application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.oci.image.manifest.v1+json,application/vnd.docker.distribution.manifest.v2+json'

# Fetch a Bearer token for a pull-scoped challenge. Echoes the token or nothing.
_fetch_token() {
  local realm="$1" service="$2" scope="$3"
  local url="$realm"
  local sep='?'
  [ -n "$service" ] && { url="${url}${sep}service=${service}"; sep='&'; }
  [ -n "$scope" ]   && { url="${url}${sep}scope=${scope}"; sep='&'; }
  local body
  if [ -n "${REGISTRY_USERNAME:-}" ] && [ -n "${REGISTRY_PASSWORD:-}" ]; then
    body="$(curl -sS --max-time 20 -u "${REGISTRY_USERNAME}:${REGISTRY_PASSWORD}" "$url" 2>/dev/null || true)"
  else
    body="$(curl -sS --max-time 20 "$url" 2>/dev/null || true)"
  fi
  # GHCR/Docker return {"token":...}; some registries use {"access_token":...}.
  # Use `sed -nE` (ERE): the `\|` BRE alternation is a GNU-only extension, so on
  # BSD/macOS sed the old expression matched nothing, _fetch_token returned empty,
  # every image classified `unknown`, and the operator was routed into FORCE_LATEST=1
  # (which SKIPS this probe) — the exact INV-24 harm (bonnyr-f5 #193 B4). ERE
  # `(access_token|token)` is portable across GNU and BSD sed.
  printf '%s' "$body" | sed -nE 's/.*"(access_token|token)"[[:space:]]*:[[:space:]]*"([^"]*)".*/\2/p' | head -1
}

# HTTP status for GET <manifest_url>, following one Bearer challenge if issued.
# Echoes a 3-digit code, or 000 on a curl/network failure.
_manifest_status() {
  local url="$1"
  local hdr code
  hdr="$(mktemp)"
  code="$(curl -sS --max-time 25 -o /dev/null -D "$hdr" -w '%{http_code}' \
            -H "Accept: ${ACCEPT}" "$url" 2>/dev/null || echo 000)"
  if [ "$code" = "401" ]; then
    local challenge realm service scope
    challenge="$(grep -i '^www-authenticate:' "$hdr" | head -1 | tr -d '\r')"
    realm="$(printf '%s' "$challenge"   | sed -n 's/.*realm="\([^"]*\)".*/\1/p')"
    service="$(printf '%s' "$challenge" | sed -n 's/.*service="\([^"]*\)".*/\1/p')"
    scope="$(printf '%s' "$challenge"   | sed -n 's/.*scope="\([^"]*\)".*/\1/p')"
    if [ -n "$realm" ]; then
      local token
      token="$(_fetch_token "$realm" "$service" "$scope")"
      if [ -n "$token" ]; then
        code="$(curl -sS --max-time 25 -o /dev/null -w '%{http_code}' \
                  -H "Accept: ${ACCEPT}" -H "Authorization: Bearer ${token}" \
                  "$url" 2>/dev/null || echo 000)"
      fi
      # token empty => the token endpoint denied us => leave code=401 (unknown).
    fi
  fi
  rm -f "$hdr"
  printf '%s' "$code"
}

for name in "${IMAGES[@]}"; do
  ref="${REGISTRY}/${name}:${VERSION}"
  url="https://${HOST}/v2/${NAMESPACE}/${name}/manifests/${VERSION}"
  code="$(_manifest_status "$url")"
  case "$code" in
    200|203)  printf '%s\t%s\t%s\n' exists  "$ref" "HTTP ${code}" ;;
    404)      printf '%s\t%s\t%s\n' absent  "$ref" "HTTP 404 (not found)" ;;
    401|403)  printf '%s\t%s\t%s\n' unknown "$ref" "HTTP ${code} (auth/permission — cannot confirm)" ;;
    # Network/curl failure. curl writes its own `%{http_code}` of "000" to stdout
    # AND exits non-zero, so `... || echo 000` appends a SECOND "000" -> the real
    # shape is "000000" (or a bare "000" if curl emitted nothing). The old `000)`
    # arm matched neither doubled shape and fell through to `*)` — dead code
    # (bonnyr-f5 #193 r3 minor). Match the `^000` prefix so it actually fires.
    000*)     printf '%s\t%s\t%s\n' unknown "$ref" "network/curl failure (code '${code}') — cannot confirm" ;;
    429)      printf '%s\t%s\t%s\n' unknown "$ref" "HTTP 429 (rate limited — cannot confirm)" ;;
    5??)      printf '%s\t%s\t%s\n' unknown "$ref" "HTTP ${code} (registry error — cannot confirm)" ;;
    *)        printf '%s\t%s\t%s\n' unknown "$ref" "HTTP ${code} (unexpected — cannot confirm)" ;;
  esac
done
