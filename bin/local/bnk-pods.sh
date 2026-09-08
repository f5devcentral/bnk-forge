#!/usr/bin/env bash
set -euo pipefail

# MAF-LOCAL-PURPOSE: One-shot pods+events+logs snapshot for a kube context/namespace, replaces get/logs/describe loops

###############################################################################
# bnk-pods.sh — one-shot pod diagnostics snapshot
#
# Usage:
#   bin/local/bnk-pods.sh <context> [namespace]
#   bin/local/bnk-pods.sh --help
#
# Single invocation emits: `kubectl get pods -o wide`, recent namespace
# events, and the last N log lines for every pod that is not Ready — against
# the named context/namespace. Replaces repeated get/logs/describe loops.
#
# Env vars:
#   BNK_PODS_NAMESPACE   Default namespace when not given as arg (default: llm-egress)
#   BNK_PODS_LOG_LINES   Log tail length per pod (default: 100)
#
# Committed to this repo. Not managed by the MAF framework installer/updater —
# see bin/lib/install-engine.sh (enumerate_framework_managed_paths never
# globs bin/local/**).
###############################################################################

usage() {
    cat <<'USAGE_EOF'
bin/local/bnk-pods.sh <context> [namespace]

Arguments:
  context      kubectl context to use (required)
  namespace    kubectl namespace (default: llm-egress, or $BNK_PODS_NAMESPACE)

Options:
  --help, -h   Show this help

Env vars:
  BNK_PODS_NAMESPACE   default namespace when not given as arg
  BNK_PODS_LOG_LINES   log tail length per pod (default 100)
USAGE_EOF
}

die() {
    echo "bnk-pods: $*" >&2
    exit 1
}

need_bin() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found in PATH"
}

main() {
    case "${1:-}" in
        --help|-h) usage; exit 0 ;;
    esac

    [[ $# -ge 1 ]] || { usage >&2; die "requires <context>"; }
    local context="$1"
    local namespace="${2:-${BNK_PODS_NAMESPACE:-llm-egress}}"
    local log_lines="${BNK_PODS_LOG_LINES:-100}"

    need_bin kubectl

    kubectl config get-contexts -o name 2>/dev/null | grep -qx "$context" \
        || die "kubectl context '$context' not found (kubectl config get-contexts)"

    local -a kc=(kubectl --context "$context" -n "$namespace")

    "${kc[@]}" get namespace "$namespace" >/dev/null 2>&1 \
        || die "namespace '$namespace' not reachable in context '$context'"

    echo "=== pods ($context / $namespace) ==="
    "${kc[@]}" get pods -o wide

    echo
    echo "=== recent events ($context / $namespace) ==="
    "${kc[@]}" get events --sort-by=.lastTimestamp | tail -n 30

    local not_ready
    not_ready="$("${kc[@]}" get pods --no-headers 2>/dev/null | awk '{split($2,a,"/"); if (a[1] != a[2]) print $1}')"

    if [[ -z "$not_ready" ]]; then
        echo
        echo "=== all pods Ready, no logs to show ==="
        return 0
    fi

    local pod
    while IFS= read -r pod; do
        [[ -n "$pod" ]] || continue
        echo
        echo "=== logs: $pod (last $log_lines lines) ==="
        "${kc[@]}" logs "$pod" --all-containers --tail="$log_lines" 2>&1 || echo "(log fetch failed for $pod)"
    done <<<"$not_ready"
}

main "$@"
