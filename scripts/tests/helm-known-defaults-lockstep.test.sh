#!/usr/bin/env bash
# Helm <-> Python known-default denylist lockstep (bonnyr-f5 #193 r3, minor).
#
# The chart's fail-guards in helm/bnk-forge/templates/secrets.yaml refuse a shipped
# default password so the chart never emits a value the backend rejects. That denylist
# is a THIRD copy of the Python source of truth:
#   - $mcpDefaults   MUST match backend/core/config.py       MCP_KNOWN_DEFAULT_PASSWORDS
#   - $adminDefaults MUST match backend/services/auth_service.py _KNOWN_DEFAULT_ADMIN_PASSWORDS
# Nothing asserted the lockstep the comments claim; if they drift, the chart can emit a
# value the backend fatal-rejects (crashloop) or fail-guard a value the backend accepts.
# This test greps both sides and asserts set-equality (order-independent).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SECRETS="$ROOT/helm/bnk-forge/templates/secrets.yaml"
CONFIG="$ROOT/backend/core/config.py"
AUTH="$ROOT/backend/services/auth_service.py"

fail=0

# Extract the quoted tokens from a Helm `$<var> := list "a" "b"` assignment. Grab the
# whole assignment line first, then every quoted token on it (the line carries no other
# quoted content), so a hyphenated token like "mcp-service-changeme" is not truncated.
_helm_list() {  # var-name, file
  grep -E "\\\$$1 := list " "$2" | head -1 \
    | grep -oE '"[^"]*"' | tr -d '"' | sort | tr '\n' ' ' | sed 's/ $//'
}
# Extract the quoted tokens from a Python tuple assignment `NAME = ("a", "b")`.
_py_tuple() {  # NAME, file
  grep -E "^$1 = \(" "$2" | head -1 \
    | grep -oE '"[^"]*"' | tr -d '"' | sort | tr '\n' ' ' | sed 's/ $//'
}

compare() {  # label, helm-set, py-set
  if [ "$2" = "$3" ]; then printf 'PASS  %-28s [%s]\n' "$1" "$2"
  else printf 'FAIL  %-28s helm=[%s] python=[%s]\n' "$1" "$2" "$3"; fail=1; fi
}

HELM_MCP="$(_helm_list mcpDefaults "$SECRETS")"
PY_MCP="$(_py_tuple MCP_KNOWN_DEFAULT_PASSWORDS "$CONFIG")"
compare "mcp defaults" "$HELM_MCP" "$PY_MCP"

HELM_ADMIN="$(_helm_list adminDefaults "$SECRETS")"
PY_ADMIN="$(_py_tuple _KNOWN_DEFAULT_ADMIN_PASSWORDS "$AUTH")"
compare "admin defaults" "$HELM_ADMIN" "$PY_ADMIN"

# Guard against a vacuous pass if a grep silently matched nothing.
for v in "$HELM_MCP" "$PY_MCP" "$HELM_ADMIN" "$PY_ADMIN"; do
  [ -n "$v" ] || { echo "FAIL  a denylist extracted EMPTY (grep drift) — treat as vacuous"; fail=1; }
done

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
