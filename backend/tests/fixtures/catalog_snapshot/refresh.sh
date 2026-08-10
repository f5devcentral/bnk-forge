#!/usr/bin/env bash
# ADR-204 catalog-snapshot refresh (issue #439).
#
# Re-vendors the pinned catalog snapshot used by the parity gate
# (test_adr204_ssh_parity.py). Clones bnk-forge-modules at a given commit and
# copies ONLY the load-bearing files per module — bnkforge.pack.json, manifests/,
# and values.yaml. The .tf / module.json / README / examples are deliberately NOT
# vendored (the manifest renderer never reads them — see issue #440).
#
# Usage:
#   ./refresh.sh <commit-sha> [branch]
#
# After running:
#   1. Bump EXPECTED_CATALOG_SHA in tests/unit/test_adr204_catalog_snapshot.py.
#   2. Update the commit/branch in SNAPSHOT.md.
#   3. Re-run: pytest tests/unit/test_adr204_ssh_parity.py \
#              tests/unit/test_adr204_catalog_snapshot.py
#   Any parity change is a real behaviour delta — review it, don't just re-baseline.
set -euo pipefail

REPO="${CATALOG_REPO:-git@github.com:JLCode-tech/bnk-forge-modules.git}"
SHA="${1:?usage: refresh.sh <commit-sha> [branch]}"
BRANCH="${2:-}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODULES=(
  bnk/bnk-gatewayclass
  bnk/cneinstance
  bnk/flo
  k8s/bnk-cert-issuer
  k8s/bnk-namespaces
  k8s/cert-manager
  k8s/network-setup
)

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Cloning $REPO ${BRANCH:+(branch $BRANCH) }@ $SHA ..."
git clone --quiet ${BRANCH:+--branch "$BRANCH"} "$REPO" "$TMP/src"
git -C "$TMP/src" checkout --quiet "$SHA"

for m in "${MODULES[@]}"; do
  src="$TMP/src/$m"
  dst="$HERE/$m"
  [ -d "$src" ] || { echo "  WARN: $m not found upstream — skipping"; continue; }
  echo "  vendoring $m"
  rm -rf "$dst"
  mkdir -p "$dst"
  # Load-bearing only.
  [ -f "$src/bnkforge.pack.json" ] && cp "$src/bnkforge.pack.json" "$dst/"
  [ -f "$src/values.yaml" ] && cp "$src/values.yaml" "$dst/"
  [ -d "$src/manifests" ] && cp -R "$src/manifests" "$dst/"
done

echo "$SHA" > "$HERE/CATALOG_SHA"
echo "Wrote CATALOG_SHA=$SHA"
echo "NEXT: bump EXPECTED_CATALOG_SHA in test_adr204_catalog_snapshot.py + update SNAPSHOT.md, then run the parity tests."
