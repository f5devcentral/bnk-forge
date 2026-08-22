#!/usr/bin/env bash
# Keep EVERY version-bearing DEPLOYMENT artifact in lockstep with VERSION:
#   - the bnk-forge Helm chart image tag (values.yaml) and Chart `appVersion`
#   - the frontend package.json version
#   - the sibling bnk-operator chart image tag (values.yaml) and `appVersion`
#   - the packaged dist/ compose image pins (dist/docker-compose.yml — the 7
#     `${BNK_FORGE_VERSION:-<v>}` defaults) and dist/.env.example's
#     BNK_FORGE_VERSION default
#   - the IBM Cloud installer's BNK_FORGE_VERSION default AND the pins in the
#     compose template it embeds (scripts/ibm_cloud_bnk_forge.sh)
#
# ALL of these resolve to an image published at :${VERSION} on the release train
# — docker-bake.hcl's `default` group builds the operator image alongside the
# rest — so any drift means an image tag the release never publishes ->
# ImagePullBackOff / `manifest unknown`. This is the ONE place that writes them,
# and --check verifies them in CI so drift can't reappear silently.
#
# WHY dist/ is now IN scope (bonnyr-f5 #193 r3, B1): the dist/ compose + env +
# the IBM installer previously hard-pinned a FORWARD-DATED version (`4.0.0`) that
# nothing had published, so a fresh install rendered `manifest unknown` while
# --check stayed green because it could not see dist/. The pins are now DERIVED
# from VERSION here: at release, `--write $NEW` re-stamps every one atomically and
# release.yml stages exactly `--list`, so the packaged pin can never name a tag
# the release did not cut. Never re-hardcode a version into these files — set it
# here and let --write propagate it.
#
# STILL out of scope, deliberately:
#   - frontend-v2/package-lock.json's root `version` (npm owns it; `npm ci`
#     tolerates the desync).
#   - dist/docker-compose.local.yml — a pure networking OVERLAY that carries NO
#     image: line of its own (it inherits every pin from dist/docker-compose.yml),
#     so there is nothing here to stamp; adding it would only make --check vacuous.
#   - each Chart.yaml's own `version:` — Helm treats chart version and appVersion
#     as independent, and release.yml neither packages nor pushes the chart, so a
#     static chart version publishes nothing wrong. Leave it alone.
#
# Usage:
#   sync-version-artifacts.sh --write <version>   # set all artifacts to <version>
#   sync-version-artifacts.sh --check             # verify all == VERSION; exit 1 if not
#   sync-version-artifacts.sh --list              # print artifact paths (repo-relative)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VALUES="$ROOT/helm/bnk-forge/values.yaml"
CHART="$ROOT/helm/bnk-forge/Chart.yaml"
PKG="$ROOT/frontend-v2/package.json"
# The sibling operator chart is on the VERSION train (its image publishes at
# :${VERSION}), so it is synced here too rather than pinned.
OPVALUES="$ROOT/bnk-operator/charts/bnk-operator/values.yaml"
OPCHART="$ROOT/bnk-operator/charts/bnk-operator/Chart.yaml"
# Packaged dist/ install path and the IBM Cloud installer (bonnyr-f5 #193 r3, B1).
DISTENV="$ROOT/dist/.env.example"
DISTCOMPOSE="$ROOT/dist/docker-compose.yml"
IBMCLOUD="$ROOT/scripts/ibm_cloud_bnk_forge.sh"

# Canonical artifact list. --write, --list, and the release job's `git add` all
# derive the file set from HERE, so the writer and its stager cannot diverge and
# leave a synced-but-unstaged file behind (bonnyr-f5 #180 r3, BLOCKER 1).
SYNCED_FILES=("$VALUES" "$CHART" "$PKG" "$OPVALUES" "$OPCHART" \
              "$DISTENV" "$DISTCOMPOSE" "$IBMCLOUD")

# ── Value readers ─────────────────────────────────────────────────────────────
# Each reads EVERY matching version line (not grep -m1), so a second occurrence
# can't drift unseen behind a global write (bonnyr-f5 #180 r3). `^  tag: ` (two
# spaces) matches only the top-level image tag — the postgres/redis/per-service
# tags are 4-space and never match.
TAG_RE='^  tag: '
TAG_SED='s/^  tag: "?([^"]*)"?.*/\1/'
APPVER_RE='^appVersion:'
APPVER_SED='s/^appVersion: "?([^"]*)"?.*/\1/'
PKGVER_RE='^  "version":'
PKGVER_SED='s/^  "version": "([^"]*)".*/\1/'
# dist/.env.example: a bare shell assignment `BNK_FORGE_VERSION=<v>` (the compose
# files read this at runtime as ${BNK_FORGE_VERSION:-<default>}). Only the
# top-level (column-0) assignment matches — the commented example above it is
# `#   BNK_FORGE_VERSION=...` and is skipped.
DISTENV_RE='^BNK_FORGE_VERSION='
DISTENV_SED='s/^BNK_FORGE_VERSION=(.*)/\1/'
# Compose image pins AND the IBM installer default, both written as the shell
# default-expansion `${BNK_FORGE_VERSION:-<v>}`. The reader pulls the <v> out of
# EVERY such site (7 pins in dist/docker-compose.yml; 1 default + 7 embedded pins
# in the IBM installer), so a second occurrence can't drift unseen. `$`, `{`, `}`
# are escaped so grep -E / sed -E read them literally, not as anchor/interval.
PIN_RE='\$\{BNK_FORGE_VERSION:-'
PIN_SED='s/.*\$\{BNK_FORGE_VERSION:-([^}]*)\}.*/\1/'

# The image tag lives inside the top-level `image:` block. The WRITER scopes its
# substitution to that block (sed range below); the READERS (--check and --write's
# post-write verify) MUST use the SAME range, or writer and checker diverge:
# a `tag:` the writer can't reach or a stray 2-space `tag:` under another key would
# be read by a file-global checker but never written — CI green while the next
# release hard-fails, or CI red on a line --write can't fix (bonnyr-f5 #180 r5, F1).
# One expression, used by both.
#
# The range END is a column-0 line that is NOT a comment (`^[^[:space:] #]`). A
# YAML `# comment` at column 0 is legal ANYWHERE, including inside the image block,
# and is NOT the next top-level key — the old `^[^[:space:]]` ended the range on it,
# so a column-0 comment placed after `^image:` hid the `tag:` from BOTH the writer
# and the checker, making --check report "key renamed/removed? — vacuous" and print
# a --write remediation that also could not reach the tag (bonnyr-f5 #193 minor).
IMG_RANGE='/^image:/,/^[^[:space:] #]/'

# Emit the candidate version lines for a reader. When RANGE is given, the grep is
# scoped to that sed address range (the image-tag case) so the reader sees EXACTLY
# the site set the writer's ranged sed touches; otherwise it is file-global.
_version_lines() {  # file, grep-ERE, range(optional)
  local file="$1" gre="$2" range="${3:-}"
  if [ -n "$range" ]; then
    sed -nE "${range}{/${gre}/p;}" "$file"
  else
    grep -E "$gre" "$file" || true
  fi
}

case "${1:-}" in
  --write)
    V="${2:?usage: sync-version-artifacts.sh --write <version>}"
    # V is interpolated into sed replacement strings, so a `|`/`&`/`\`/`"` would
    # corrupt the substitution. It only fails-closed at the post-write verify
    # today (bonnyr-f5 #180 r5 nit) — reject metacharacters up front with a clear
    # message. The class is permissive enough for full semver incl. prerelease and
    # build metadata (e.g. 1.2.3-rc.1+build.5).
    if ! printf '%s' "$V" | grep -qE '^[A-Za-z0-9._+-]+$'; then
      echo "::error::--write version '$V' contains characters outside [A-Za-z0-9._+-] — refusing (would corrupt the sed substitution)" >&2
      exit 2
    fi
    # Anchor every substitution to its key PATH, not a bare 2-space `tag:`. The
    # image-tag writes are scoped to the top-level `image:` block via a sed range
    # (`/^image:/` to the next column-0 key) so a future unrelated 2-space `tag:`
    # elsewhere is never repinned to VERSION (bonnyr-f5 #180 r3, unbounded writer).
    # -i.syncbak (attached suffix) is the one in-place form both GNU and BSD sed
    # accept; `-i -E` makes BSD swallow -E as the suffix and litter *-E files.
    sed -i.syncbak -E "${IMG_RANGE} s|^  tag: .*|  tag: \"${V}\"|" "$VALUES"
    sed -i.syncbak -E "s|^appVersion: .*|appVersion: \"${V}\"|" "$CHART"
    sed -i.syncbak -E "s|^  \"version\": \"[^\"]*\"|  \"version\": \"${V}\"|" "$PKG"
    sed -i.syncbak -E "${IMG_RANGE} s|^  tag: .*|  tag: \"${V}\"|" "$OPVALUES"
    sed -i.syncbak -E "s|^appVersion: .*|appVersion: \"${V}\"|" "$OPCHART"
    # dist/.env.example: rewrite the bare top-level assignment only.
    sed -i.syncbak -E 's|^BNK_FORGE_VERSION=.*|BNK_FORGE_VERSION='"${V}"'|' "$DISTENV"
    # Every `${BNK_FORGE_VERSION:-<old>}` default -> `${BNK_FORGE_VERSION:-<V>}`.
    # The LHS pattern is single-quoted (literal to the shell) and the replacement
    # concatenates single-quoted literals around the interpolated ${V}; V is
    # validated above to [A-Za-z0-9._+-], so it carries no sed-replacement
    # metacharacter (`&`/`\`/delimiter). Applies to the dist compose pins and to
    # BOTH the IBM installer default (line ~32) and its embedded compose pins.
    sed -i.syncbak -E 's|\$\{BNK_FORGE_VERSION:-[^}]*\}|${BNK_FORGE_VERSION:-'"${V}"'}|g' "$DISTCOMPOSE"
    sed -i.syncbak -E 's|\$\{BNK_FORGE_VERSION:-[^}]*\}|${BNK_FORGE_VERSION:-'"${V}"'}|g' "$IBMCLOUD"
    for f in "${SYNCED_FILES[@]}"; do rm -f "${f}.syncbak"; done

    # Fail closed: a sed whose pattern matched nothing no-ops silently, and the
    # caller would commit the unchanged file believing it synced (#177 review).
    # Re-read every version line in every artifact with the SAME readers --check
    # uses and confirm each one actually took ${V} — and that at least one line
    # matched per artifact, so a renamed key can't pass as "nothing to change".
    rc=0
    _verify_file() {  # label, file, grep-ERE, extract-sed, range(optional)
      local label="$1" file="$2" gre="$3" ext="$4" range="${5:-}" n=0 line val
      while IFS= read -r line; do
        val=$(sed -E "$ext" <<< "$line"); n=$((n + 1))
        if [ "$val" != "$V" ]; then
          echo "::error::--write did not take on $label: it is '$val', expected '$V' (the sed pattern matched nothing — the artifact's format changed)" >&2
          rc=1
        fi
      done < <(_version_lines "$file" "$gre" "$range")
      if [ "$n" -eq 0 ]; then
        echo "::error::--write found no '$label' line in $file (key renamed/removed?) — nothing was synced" >&2
        rc=1
      fi
    }
    #                                                                    range ↓ (tag only: same scope as the writer)
    _verify_file "helm image.tag"      "$VALUES"   "$TAG_RE"    "$TAG_SED"    "$IMG_RANGE"
    _verify_file "Chart appVersion"    "$CHART"    "$APPVER_RE" "$APPVER_SED"
    _verify_file "frontend version"    "$PKG"      "$PKGVER_RE" "$PKGVER_SED"
    _verify_file "operator image.tag"  "$OPVALUES" "$TAG_RE"    "$TAG_SED"    "$IMG_RANGE"
    _verify_file "operator appVersion" "$OPCHART"  "$APPVER_RE" "$APPVER_SED"
    _verify_file "dist env default"    "$DISTENV"  "$DISTENV_RE" "$DISTENV_SED"
    _verify_file "dist compose pins"   "$DISTCOMPOSE" "$PIN_RE"  "$PIN_SED"
    _verify_file "ibm-cloud pins"      "$IBMCLOUD" "$PIN_RE"     "$PIN_SED"
    [ "$rc" -eq 0 ] || exit 1
    echo "synced bnk-forge tag+appVersion, frontend package.json, operator tag+appVersion, dist compose+env, ibm-cloud installer -> ${V}" >&2
    ;;

  --check)
    EXPECTED="$(cat "$ROOT/VERSION")"
    # VERSION itself must be non-empty, or every artifact would "match" an empty
    # string and the gate would pass on a tree with no version data at all
    # (bonnyr-f5 #180 r3, BLOCKER 2).
    if [ -z "$EXPECTED" ]; then
      echo "::error::VERSION is empty — refusing to validate artifacts against nothing" >&2
      exit 1
    fi
    rc=0; total=0
    # Assert EVERY version line is NON-EMPTY and equals VERSION, and that each
    # artifact contributed at least one matched line. `total` counts MATCHED
    # LINES, never loop iterations — an artifact whose key vanished contributes
    # zero and both trips its own error and lowers the vacuity floor (bonnyr-f5
    # #180 r3: the old `checked` counted a literal 5-item list, so its >=5 guard
    # was unreachable and --check was green on an empty tree).
    _check_file() {  # label, file, grep-ERE, extract-sed, range(optional)
      local label="$1" file="$2" gre="$3" ext="$4" range="${5:-}" n=0 line val
      while IFS= read -r line; do
        val=$(sed -E "$ext" <<< "$line"); n=$((n + 1)); total=$((total + 1))
        if [ -z "$val" ]; then
          echo "::error::$label in $file has an empty version — expected '$EXPECTED'"; rc=1
        elif [ "$val" != "$EXPECTED" ]; then
          echo "::error::$label is '$val' but VERSION is '$EXPECTED' — the release publishes only :\${VERSION}, so a mismatch means ImagePullBackOff / drift. Run scripts/sync-version-artifacts.sh --write $EXPECTED"; rc=1
        else
          echo "  OK    $label = $val"
        fi
      done < <(_version_lines "$file" "$gre" "$range")
      if [ "$n" -eq 0 ]; then
        echo "::error::$label: no version line matched in $file (key renamed/removed?) — vacuous check"; rc=1
      fi
    }
    #                                                                   range ↓ (tag only: same scope as the writer)
    _check_file "helm image.tag"      "$VALUES"   "$TAG_RE"    "$TAG_SED"    "$IMG_RANGE"
    _check_file "Chart appVersion"    "$CHART"    "$APPVER_RE" "$APPVER_SED"
    _check_file "frontend version"    "$PKG"      "$PKGVER_RE" "$PKGVER_SED"
    _check_file "operator image.tag"  "$OPVALUES" "$TAG_RE"    "$TAG_SED"    "$IMG_RANGE"
    _check_file "operator appVersion" "$OPCHART"  "$APPVER_RE" "$APPVER_SED"
    _check_file "dist env default"    "$DISTENV"  "$DISTENV_RE" "$DISTENV_SED"
    _check_file "dist compose pins"   "$DISTCOMPOSE" "$PIN_RE"  "$PIN_SED"
    _check_file "ibm-cloud pins"      "$IBMCLOUD" "$PIN_RE"     "$PIN_SED"
    # Backstop: eight artifacts, each with >=1 version line, is the minimum a
    # healthy tree yields (the dist compose contributes 7 pins and the IBM
    # installer 8, so the real total is far higher — this floor only catches a
    # catastrophic "every key vanished"). Fewer means a key vanished — vacuous.
    if [ "$total" -lt 8 ]; then
      echo "::error::--check matched only $total version lines (expected >=8) — vacuous" >&2
      exit 1
    fi
    exit "$rc"
    ;;

  --list)
    # Print the canonical artifact paths (repo-relative) so the release job stages
    # EXACTLY what --write touches. Adding an artifact above updates all three.
    for f in "${SYNCED_FILES[@]}"; do
      printf '%s\n' "${f#"$ROOT"/}"
    done
    ;;

  *)
    echo "usage: sync-version-artifacts.sh --write <version> | --check | --list" >&2
    exit 2
    ;;
esac
