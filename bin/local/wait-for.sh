#!/usr/bin/env bash
set -euo pipefail

# MAF-LOCAL-PURPOSE: Backoff-poll a URL or pod selector until ready, exits non-zero on timeout instead of guessed sleeps

###############################################################################
# wait-for.sh — poll-until-ready, replaces guessed sleep durations
#
# Usage:
#   bin/local/wait-for.sh --url <url> [--timeout N]
#   bin/local/wait-for.sh --pod <selector> [--context c] [--namespace ns] [--timeout N]
#   bin/local/wait-for.sh --help
#
# Polls with exponential backoff (1s, 2s, 4s, ... capped at 10s) until the
# URL returns a 2xx/3xx response or the pod selector matches an all-Ready
# pod. Exits non-zero when --timeout elapses.
#
# Committed to this repo. Not managed by the MAF framework installer/updater —
# see bin/lib/install-engine.sh (enumerate_framework_managed_paths never
# globs bin/local/**).
###############################################################################

usage() {
    cat <<'USAGE_EOF'
bin/local/wait-for.sh --url <url> [--timeout N]
bin/local/wait-for.sh --pod <selector> [--context c] [--namespace ns] [--timeout N]

Options:
  --url <url>          Poll this URL until it returns HTTP 2xx/3xx (or 401/403 - service up, auth required)
  --pod <selector>      Poll `kubectl get pods -l <selector>` until all matched pods are Ready
  --context <c>          kubectl context (used with --pod)
  --namespace <ns>       kubectl namespace (used with --pod, default: default)
  --timeout <N>          Max seconds to wait (default: 120)
  --help, -h             Show this help
USAGE_EOF
}

die() {
    echo "wait-for: $*" >&2
    exit 1
}

need_bin() {
    command -v "$1" >/dev/null 2>&1 || die "'$1' is required but not found in PATH"
}

# check_url — ready when the URL returns 2xx/3xx, or 401/403 (service is up
# and routing; it just wants auth — e.g. https://localhost/api/health is
# auth-gated behind the proxy, only /api/system/health is exempt).
check_url() {
    local url="$1" code
    code="$(curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 2 --max-time 5 "$url" 2>/dev/null || echo 000)"
    [[ "$code" -ge 200 && "$code" -lt 400 ]] || [[ "$code" == "401" || "$code" == "403" ]]
}

check_pod() {
    local selector="$1" context="$2" namespace="$3"
    local -a kc=(kubectl)
    [[ -n "$context" ]] && kc+=(--context "$context")
    kc+=(-n "$namespace" get pods -l "$selector" --no-headers)

    local out
    out="$("${kc[@]}" 2>/dev/null)" || return 1
    [[ -n "$out" ]] || return 1

    while IFS= read -r line; do
        local ready
        ready="$(awk '{print $2}' <<<"$line")"
        local have="${ready%/*}" want="${ready#*/}"
        [[ "$have" == "$want" ]] || return 1
    done <<<"$out"
    return 0
}

main() {
    local mode="" target="" context="" namespace="default" timeout=120

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --url)
                [[ $# -ge 2 ]] || die "--url requires a value"
                mode="url"; target="$2"; shift 2 ;;
            --pod)
                [[ $# -ge 2 ]] || die "--pod requires a value"
                mode="pod"; target="$2"; shift 2 ;;
            --context)
                [[ $# -ge 2 ]] || die "--context requires a value"
                context="$2"; shift 2 ;;
            --namespace)
                [[ $# -ge 2 ]] || die "--namespace requires a value"
                namespace="$2"; shift 2 ;;
            --timeout)
                [[ $# -ge 2 ]] || die "--timeout requires a value"
                timeout="$2"; shift 2 ;;
            --help|-h) usage; exit 0 ;;
            *) die "unknown argument '$1' (see --help)" ;;
        esac
    done

    [[ -n "$mode" ]] || { usage >&2; die "requires --url or --pod"; }

    need_bin curl
    [[ "$mode" == "pod" ]] && need_bin kubectl

    local start elapsed interval=1
    start="$(date +%s)"

    while true; do
        if [[ "$mode" == "url" ]]; then
            check_url "$target" && { echo "wait-for: ready — $target"; exit 0; }
        else
            check_pod "$target" "$context" "$namespace" && { echo "wait-for: ready — pod selector '$target'"; exit 0; }
        fi

        elapsed=$(( $(date +%s) - start ))
        if [[ "$elapsed" -ge "$timeout" ]]; then
            die "timed out after ${timeout}s waiting for $mode target '$target'"
        fi

        sleep "$interval"
        interval=$(( interval * 2 ))
        [[ "$interval" -gt 10 ]] && interval=10
    done
}

main "$@"
