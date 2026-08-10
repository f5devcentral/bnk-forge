#!/usr/bin/env bash
# =============================================================================
# retry.sh — retry a command on transient failure
# =============================================================================
#
# Docker `ADD` is used for github.com downloads during image builds (OpenTofu,
# llmtop) because the Docker daemon trusts the corporate DLP/TLS-interception CA
# while build containers do not — see docs/adr/D-035-docker-netskope-tls-interception.md.
# Unlike `curl --retry`, `ADD` has NO built-in retry, so a single transient
# CDN/TLS disconnect fails the whole build. This wrapper re-runs the command;
# BuildKit's layer cache makes each retry cheap — only the layer that actually
# failed is re-executed, everything already built is reused.
#
# Usage:
#   scripts/retry.sh [-n attempts] [-d delay_seconds] -- <command> [args...]
#   RETRY_ATTEMPTS=5 RETRY_DELAY=10 scripts/retry.sh <command> [args...]
#
# The `--` separator is optional but recommended so flags meant for <command>
# are not mistaken for retry.sh's own flags.
#
# Defaults: 3 attempts, 5s delay. Exit code is the last attempt's exit code
# (0 on first success), so it is safe to use in CI and Make recipes.
#
set -euo pipefail

attempts="${RETRY_ATTEMPTS:-3}"
delay="${RETRY_DELAY:-5}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--attempts) attempts="$2"; shift 2 ;;
        -d|--delay)    delay="$2";    shift 2 ;;
        --)            shift; break ;;
        -*)            echo "retry.sh: unknown option '$1'" >&2; exit 2 ;;
        *)             break ;;
    esac
done

if [[ $# -eq 0 ]]; then
    echo "retry.sh: no command given" >&2
    echo "Usage: retry.sh [-n attempts] [-d delay] -- <command> [args...]" >&2
    exit 2
fi

n=1
while true; do
    # Capture the command's real exit code. `|| rc=$?` both shields the call
    # from `set -e` and preserves the code — reading $? after an `if ...; fi`
    # would instead yield the `if` statement's status (0), masking failures.
    rc=0
    "$@" || rc=$?
    if [[ "$rc" -eq 0 ]]; then
        exit 0
    fi
    if [[ "$n" -ge "$attempts" ]]; then
        echo "retry.sh: command failed after ${attempts} attempt(s) (exit ${rc}): $*" >&2
        exit "$rc"
    fi
    echo "retry.sh: attempt ${n}/${attempts} failed (exit ${rc}); retrying in ${delay}s..." >&2
    sleep "$delay"
    n=$((n + 1))
done
