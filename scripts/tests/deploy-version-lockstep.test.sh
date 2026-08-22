#!/usr/bin/env bash
# Deploy version lockstep (bonnyr-f5 #193 r3, M-1b).
#
# Asserts the Helm chart, the sibling bnk-operator chart, and the packaged dist/
# install path can NEVER diverge on the pinned image version: VERSION == chart
# appVersion == values.yaml image.tag == the OPERATOR chart appVersion + image.tag
# (bonnyr-f5 #193 r4 deploy minor: the operator image also publishes at :${VERSION},
# so a stale operator pin is the same ImagePullBackOff class) == every
# dist/docker-compose.yml pin == dist/.env.example default == dist/VERSION stamp ==
# the IBM Cloud installer default (and its embedded compose pins). scripts/sync-version-artifacts.sh
# --write moves them together and --check enforces ==VERSION; this test is the
# standalone "they move in lockstep" assertion the review asked for (the chart used to
# pin the pre-guard image while dist/ pinned a forward-dated one nobody published).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
VERSION="$(cat "$ROOT/VERSION")"

fail=0
check() {  # label, value
  if [ "$2" = "$VERSION" ]; then printf 'PASS  %-34s = %s\n' "$1" "$2"
  else printf 'FAIL  %-34s = %s (expected VERSION=%s)\n' "$1" "$2" "$VERSION"; fail=1; fi
}

[ -n "$VERSION" ] || { echo "FAIL  VERSION file is empty"; exit 1; }

# Helm chart appVersion + image.tag.
check "chart appVersion" \
  "$(sed -nE 's/^appVersion: "?([^"]*)"?.*/\1/p' "$ROOT/helm/bnk-forge/Chart.yaml" | head -1)"
check "values image.tag" \
  "$(sed -nE '/^image:/,/^[^[:space:] #]/{/^  tag: /s/^  tag: "?([^"]*)"?.*/\1/p;}' "$ROOT/helm/bnk-forge/values.yaml" | head -1)"

# Sibling bnk-operator chart appVersion + image.tag (bonnyr-f5 #193 r4 deploy minor):
# the operator image is on the same VERSION train, so its pins must move in lockstep too.
check "operator appVersion" \
  "$(sed -nE 's/^appVersion: "?([^"]*)"?.*/\1/p' "$ROOT/bnk-operator/charts/bnk-operator/Chart.yaml" | head -1)"
check "operator image.tag" \
  "$(sed -nE '/^image:/,/^[^[:space:] #]/{/^  tag: /s/^  tag: "?([^"]*)"?.*/\1/p;}' "$ROOT/bnk-operator/charts/bnk-operator/values.yaml" | head -1)"

# dist/.env.example default.
check "dist .env default" \
  "$(sed -nE 's/^BNK_FORGE_VERSION=(.*)/\1/p' "$ROOT/dist/.env.example" | head -1)"

# dist/VERSION plain stamp (bonnyr-f5 #193 r4: now single-sourced by sync-version-artifacts.sh).
check "dist VERSION stamp" \
  "$(sed -nE '1{s/^[[:space:]]*([A-Za-z0-9._+-]+).*/\1/p;}' "$ROOT/dist/VERSION")"

# Every ${BNK_FORGE_VERSION:-<v>} default in the dist compose and the IBM installer.
n=0
while IFS= read -r v; do n=$((n + 1)); check "dist compose pin #$n" "$v"; done \
  < <(grep -oE '\$\{BNK_FORGE_VERSION:-[^}]*\}' "$ROOT/dist/docker-compose.yml" | sed -E 's/.*:-([^}]*)\}/\1/')
[ "$n" -ge 6 ] || { echo "FAIL  expected >=6 dist compose pins, found $n (parser drift?)"; fail=1; }

m=0
while IFS= read -r v; do m=$((m + 1)); check "ibm-cloud pin #$m" "$v"; done \
  < <(grep -oE '\$\{BNK_FORGE_VERSION:-[^}]*\}' "$ROOT/scripts/ibm_cloud_bnk_forge.sh" | sed -E 's/.*:-([^}]*)\}/\1/')
[ "$m" -ge 6 ] || { echo "FAIL  expected >=6 ibm-cloud pins, found $m (parser drift?)"; fail=1; }

echo "----"
[ "$fail" = 0 ] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
