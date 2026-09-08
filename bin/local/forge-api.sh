#!/usr/bin/env bash
set -euo pipefail

# MAF-LOCAL-PURPOSE: Authenticated curl wrapper for the Forge API - caches the login token, re-auths on 401, retries once

###############################################################################
# forge-api.sh — authenticated Forge API client
#
# Usage:
#   bin/local/forge-api.sh <METHOD> <path> [--data '<json>'] [--jq '<expr>']
#   bin/local/forge-api.sh --help
#
# Examples:
#   bin/local/forge-api.sh GET /api/clusters
#   bin/local/forge-api.sh GET /api/clusters --jq '.[].name'
#   bin/local/forge-api.sh POST /api/clusters --data '{"name":"foo"}'
#
# Logs in once against POST /api/auth/login, caches the resulting token in a
# per-user file under $TMPDIR (never committed, never printed to stdout), and
# transparently re-authenticates + retries once on a 401 response. Accepts
# either the `token` or `access_token` response key (both have been observed
# in the wild).
#
# Env vars:
#   FORGE_API_BASE_URL   Base URL (default: https://localhost)
#   FORGE_API_USER       Login username (default: admin)
#   FORGE_API_PASSWORD   Login password (default: admin123)
#   FORGE_API_INSECURE   Set to 0 to disable curl -k (default: 1, self-signed local cert)
#
# Committed to this repo. Not managed by the MAF framework installer/updater —
# see bin/lib/install-engine.sh (enumerate_framework_managed_paths never
# globs bin/local/**).
###############################################################################

BASE_URL="${FORGE_API_BASE_URL:-https://localhost}"
API_USER="${FORGE_API_USER:-admin}"
API_PASSWORD="${FORGE_API_PASSWORD:-admin123}"
INSECURE="${FORGE_API_INSECURE:-1}"

TOKEN_CACHE="${TMPDIR:-/tmp}/forge-api-token-${API_USER}.cache"
RESP_BODY="$(mktemp)"

cleanup() { rm -f "$RESP_BODY"; }
trap cleanup EXIT

CURL_BASE_ARGS=(-s --connect-timeout 5 --max-time 30)
[[ "$INSECURE" == "1" ]] && CURL_BASE_ARGS+=(-k)

usage() {
    cat <<'USAGE_EOF'
bin/local/forge-api.sh <METHOD> <path> [--data '<json>'] [--jq '<expr>']

Authenticated curl wrapper for the Forge API. Logs in once, caches the
token, re-authenticates and retries once on 401.

Arguments:
  METHOD           HTTP method: GET, POST, PUT, PATCH, DELETE
  path              API path, e.g. /api/clusters

Options:
  --data '<json>'  Request body (JSON string)
  --jq '<expr>'    Pipe the response body through this jq expression
  --help, -h       Show this help

Env vars:
  FORGE_API_BASE_URL    default https://localhost
  FORGE_API_USER        default admin
  FORGE_API_PASSWORD    default admin123
  FORGE_API_INSECURE    default 1 (curl -k for self-signed local cert)
USAGE_EOF
}

die() {
    echo "forge-api: $*" >&2
    exit 1
}

need_bin() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found in PATH"
}

# login — hit POST /api/auth/login, cache the token, never echo it.
login() {
    local body http_code token
    body="$(printf '{"username":"%s","password":"%s"}' "$API_USER" "$API_PASSWORD")"

    http_code="$(curl "${CURL_BASE_ARGS[@]}" -o "$RESP_BODY" -w '%{http_code}' \
        -X POST "$BASE_URL/api/auth/login" \
        -H 'Content-Type: application/json' \
        -d "$body")" || die "login request failed (network error contacting $BASE_URL)"

    [[ "$http_code" == "200" ]] || die "login failed: HTTP $http_code from $BASE_URL/api/auth/login"

    token="$(jq -r '.token // .access_token // empty' "$RESP_BODY")"
    [[ -n "$token" ]] || die "login response had neither 'token' nor 'access_token' field"

    umask 077
    printf '%s' "$token" > "$TOKEN_CACHE"
}

cached_token() {
    # Always return 0: an absent cache is not an error (set -e would otherwise
    # kill the script silently before login gets a chance to run).
    [[ -f "$TOKEN_CACHE" ]] && cat "$TOKEN_CACHE"
    return 0
}

# do_request <method> <path> <data> <token> — writes response body to $RESP_BODY, prints http code.
do_request() {
    local method="$1" path="$2" data="$3" token="$4"
    local -a args=("${CURL_BASE_ARGS[@]}" -o "$RESP_BODY" -w '%{http_code}' -X "$method" "$BASE_URL$path" -H "Authorization: Bearer $token")
    [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' -d "$data")
    curl "${args[@]}" || die "request failed (network error contacting $BASE_URL for $method $path)"
}

main() {
    local method="" path="" data="" jq_expr=""

    case "${1:-}" in
        --help|-h) usage; exit 0 ;;
    esac

    [[ $# -ge 2 ]] || { usage >&2; die "requires <METHOD> <path>"; }
    method="$1"; shift
    path="$1"; shift

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --data)
                [[ $# -ge 2 ]] || die "--data requires a value"
                data="$2"; shift 2 ;;
            --jq)
                [[ $# -ge 2 ]] || die "--jq requires a value"
                jq_expr="$2"; shift 2 ;;
            --help|-h) usage; exit 0 ;;
            *) die "unknown argument '$1' (see --help)" ;;
        esac
    done

    need_bin curl
    need_bin jq

    local token http_code
    token="$(cached_token)"
    [[ -n "$token" ]] || { login; token="$(cached_token)"; }

    http_code="$(do_request "$method" "$path" "$data" "$token")"

    if [[ "$http_code" == "401" ]]; then
        login
        token="$(cached_token)"
        http_code="$(do_request "$method" "$path" "$data" "$token")"
    fi

    if [[ "$http_code" -lt 200 || "$http_code" -ge 300 ]]; then
        cat "$RESP_BODY" >&2
        die "request failed: HTTP $http_code for $method $path"
    fi

    if [[ -n "$jq_expr" ]]; then
        jq -r "$jq_expr" "$RESP_BODY"
    else
        cat "$RESP_BODY"
        echo
    fi
}

main "$@"
