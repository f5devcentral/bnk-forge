#!/usr/bin/env bash
# Keep every version-bearing artifact in lockstep with VERSION.
#
# The release job bumps VERSION but historically nothing else, so the Helm chart
# pinned an image tag the release never publishes (:3.0.1) -> ImagePullBackOff,
# and frontend package.json drifted (PR #177 review, Blocker 2). This is the one
# place that writes them, and the same code checks them in CI so drift can't
# reappear silently.
#
# Synced: the Helm image tag (values.yaml), Chart `appVersion`, and frontend
# package.json. NOT synced, deliberately: Chart.yaml's own `version:` — Helm
# treats the chart version and appVersion as independent, and release.yml neither
# packages nor pushes the chart, so a static chart version publishes nothing
# wrong. Leave it alone rather than "fixing" it to match VERSION.
#
# Usage:
#   sync-version-artifacts.sh --write <version>   # set all artifacts to <version>
#   sync-version-artifacts.sh --check             # verify all == VERSION; exit 1 if not
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VALUES="$ROOT/helm/bnk-forge/values.yaml"
CHART="$ROOT/helm/bnk-forge/Chart.yaml"
PKG="$ROOT/frontend-v2/package.json"

# Read each artifact's current version.
_helm_tag()    { grep -m1 -E '^  tag: ' "$VALUES" | sed -E 's/^  tag: "?([^"]*)"?.*/\1/'; }
_appversion()  { grep -m1 -E '^appVersion:' "$CHART" | sed -E 's/^appVersion: "?([^"]*)"?.*/\1/'; }
_pkg_version() { grep -m1 -E '^  "version":' "$PKG" | sed -E 's/^  "version": "([^"]*)".*/\1/'; }

case "${1:-}" in
  --write)
    V="${2:?usage: sync-version-artifacts.sh --write <version>}"
    # The global image tag (2-space indent) — per-service tags are 4-space and
    # fall back to it; postgres/redis tags are external and left alone.
    sed -i -E "s|^  tag: .*|  tag: \"${V}\"|" "$VALUES"
    sed -i -E "s|^appVersion: .*|appVersion: \"${V}\"|" "$CHART"
    sed -i -E "s|^  \"version\": \"[^\"]*\"|  \"version\": \"${V}\"|" "$PKG"
    # Fail closed: a sed whose pattern matched nothing no-ops silently, and the
    # caller commits the unchanged file [skip ci] believing it synced (#180
    # review). Re-read each artifact with the same helpers --check trusts and
    # confirm it actually took ${V}.
    rc=0
    for pair in "helm image.tag:$(_helm_tag)" "Chart appVersion:$(_appversion)" "frontend package.json:$(_pkg_version)"; do
      name="${pair%%:*}"; got="${pair#*:}"
      if [ "$got" != "$V" ]; then
        echo "::error::--write did not take on $name: it is '$got', expected '$V' (the sed pattern matched nothing — the artifact's format changed)" >&2
        rc=1
      fi
    done
    [ "$rc" -eq 0 ] || exit 1
    echo "synced helm tag, appVersion, frontend package.json -> ${V}"
    ;;
  --check)
    EXPECTED="$(cat "$ROOT/VERSION")"
    rc=0
    for pair in "helm image.tag:$(_helm_tag)" "Chart appVersion:$(_appversion)" "frontend package.json:$(_pkg_version)"; do
      name="${pair%%:*}"; got="${pair#*:}"
      if [ "$got" = "$EXPECTED" ]; then
        echo "  OK    $name = $got"
      else
        echo "::error::$name is '$got' but VERSION is '$EXPECTED' — the release publishes only :\${VERSION}, so a mismatch means ImagePullBackOff / drift. Run scripts/sync-version-artifacts.sh --write $EXPECTED"
        rc=1
      fi
    done
    exit "$rc"
    ;;
  *)
    echo "usage: sync-version-artifacts.sh --write <version> | --check" >&2
    exit 2
    ;;
esac
