#!/usr/bin/env bash
#
# scripts/e2e.sh — operator-friendly wrapper around `python -m scripts.e2e`.
#
# Discovers `e2e-config.yaml` (cwd first, then repo root), prints what's
# about to run, asks for confirmation (because phase 2 reflashes DPUs),
# activates the backend venv if it exists, and forwards the rest of the
# args to the Python module.
#
# Examples:
#   scripts/e2e.sh                     # interactive — phase 1 + phase 2
#   scripts/e2e.sh --yes               # skip the confirmation (CI / cron)
#   scripts/e2e.sh --steps phase1      # phase 1 only
#   scripts/e2e.sh -y --steps probe_dpu_os,run_matrix
#
# Anything not recognised here is forwarded verbatim to `python -m
# scripts.e2e`, so all of its CLI flags (`--dry-run`,
# `--continue-on-failure`, `--steps`, `--report-dir`, `-v`) keep working.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── Resolve config ─────────────────────────────────────────────────────────
CONFIG=""
for candidate in "./e2e-config.yaml" "$REPO_ROOT/e2e-config.yaml"; do
    if [[ -f "$candidate" ]]; then
        CONFIG="$(cd "$(dirname "$candidate")" && pwd)/$(basename "$candidate")"
        break
    fi
done
if [[ -z "$CONFIG" ]]; then
    cat >&2 <<EOF
e2e: no e2e-config.yaml found in cwd or $REPO_ROOT.

  cp scripts/e2e/e2e-config.example.yaml e2e-config.yaml
  \$EDITOR e2e-config.yaml

EOF
    exit 2
fi

# ── Parse our own flags; forward the rest ──────────────────────────────────
YES=0
PYTHON_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -y|--yes) YES=1; shift;;
        -h|--help)
            cat <<EOF
Usage: scripts/e2e.sh [-y|--yes] [--] [args forwarded to python -m scripts.e2e]

Looks for e2e-config.yaml in cwd or $REPO_ROOT and runs the harness against it.
Asks for confirmation unless -y / --yes is passed.

Env:
  BNK_FORGE_PASSWORD   admin password for bnk-forge login (must be set
                       unless the config carries it explicitly).
EOF
            exit 0
            ;;
        --) shift; PYTHON_ARGS+=("$@"); break;;
        *)  PYTHON_ARGS+=("$1"); shift;;
    esac
done

# ── Confirmation ───────────────────────────────────────────────────────────
if [[ "$YES" -ne 1 ]]; then
    URL=$(awk '/^bnk_forge_url:/{print $2; exit}' "$CONFIG" || true)
    BFB=$(awk '/^bfb_image:/{print $2; exit}' "$CONFIG" || true)
    TPL=$(awk '/^bf_template:/{print $2; exit}' "$CONFIG" || true)
    cat <<EOF

About to run the bnk-forge e2e harness.

  config:   $CONFIG
  target:   ${URL:-?}
  template: ${TPL:-?}
  bfb:      ${BFB:-?}

WARNING: phase 2 reflashes every DPU. Use --steps phase1 to skip it.
EOF
    read -r -p "Continue? [y/N] " ans
    if [[ ! "$ans" =~ ^[Yy] ]]; then
        echo "aborted." >&2
        exit 1
    fi
fi

# ── Activate venv if present ───────────────────────────────────────────────
if [[ -f "$REPO_ROOT/backend/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$REPO_ROOT/backend/.venv/bin/activate"
fi

# ── Hand off ───────────────────────────────────────────────────────────────
cd "$REPO_ROOT"
exec python3 -m scripts.e2e "$CONFIG" "${PYTHON_ARGS[@]}"
