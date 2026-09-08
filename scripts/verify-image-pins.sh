#!/usr/bin/env bash
# verify-image-pins.sh — resolve every REGISTRY-qualified image pin in the shipped
# compose files against the container registry and FAIL on `manifest unknown`.
#
# WHY THIS EXISTS (bonnyr-f5 #193 r3, B1):
#   sync-version-artifacts.sh --check keeps the dist/ compose pins in lockstep with
#   the repo VERSION, but VERSION naming a tag is not the same as that tag EXISTING in
#   the registry. Round 2 hard-pinned a forward-dated `4.0.0` that nothing had
#   published, so a fresh `docker compose pull` rendered `manifest unknown` while every
#   green check stayed green. This probe closes that gap with no reviewer involved: it
#   expands the compose env-var defaults exactly as `docker compose` would and asks the
#   registry whether each resulting manifest is actually there.
#
#   Scope: only REGISTRY-QUALIFIED refs (those containing a '/', e.g.
#   ghcr.io/f5devcentral/bnk-forge-api:<v> and tecnativa/docker-socket-proxy:<v>) are
#   probed. Bare local build tags (bnk-forge-api:latest in the dev compose, which have a
#   sibling `build:` context and are never pulled) and official single-segment images
#   (postgres:16-alpine) are skipped — the former are not published, the latter always
#   exist. A ref that still contains an unresolved `${...}` after expansion is reported
#   as un-probeable (non-fatal by default) rather than silently skipped.
#
# ── RELEASE WIRING (for the release/CI owner) ────────────────────────────────────
#   Two gates, two modes (bonnyr-f5 #193 r4, B-2):
#
#   1. PRE-PUSH consistency gate (the PRIMARY guard). Before the tag/GitHub
#      Release/image push, the new version is not yet in the registry, so a probe
#      would fail on the very tag being published. Instead assert every shipped
#      first-party pin already renders to $NEW — a pin left forward-dated (the B-1
#      class) is caught while it is still recoverable. Run from a checkout where the
#      compose files exist (release-final/-manual have a FULL checkout at the root):
#
#          bash scripts/verify-image-pins.sh --expect-version "$NEW_VERSION"
#
#      (No --version: the check must read the COMMITTED compose default, not an
#      override, or it passes vacuously.)
#
#   2. POST-PUSH existence confirmation (secondary). After the push, confirm the
#      published manifests actually resolve. The release-publish job runs the CURRENT
#      tooling from a SPARSE .release-tooling checkout that holds NO compose file, so
#      the compose files must be passed EXPLICITLY by path (they live at the tag
#      checkout at the workspace root), and REGISTRY/VERSION as FLAGS (this script
#      reads flags, NOT env vars):
#
#          bash .release-tooling/scripts/verify-image-pins.sh \
#            --registry "$REGISTRY" --version "$NEW_VERSION" \
#            --file dist/docker-compose.yml \
#            --file dist/docker-compose.local.yml \
#            --file scripts/ibm_cloud_bnk_forge.sh
#
# Usage:
#   verify-image-pins.sh [--registry <host/ns>] [--version <tag>]
#                        [--expect-version <tag>] [--file <compose>]...
#     --registry   override ${BNK_FORGE_REGISTRY:-...} in every pin (default: the
#                  compose default, ghcr.io/f5devcentral)
#     --version    override ${BNK_FORGE_VERSION:-...} in every pin (default: the
#                  compose-baked default, which sync-version-artifacts.sh keeps == VERSION)
#     --expect-version <tag>   CONSISTENCY mode (no registry probe): assert every
#                  first-party (${BNK_FORGE_VERSION}) pin renders to <tag>. For the
#                  pre-push gate, where <tag> is not published yet. Mutually
#                  exclusive with --version.
#     --file       add a compose file to scan (repeatable). Default set: the shipped
#                  dist/ compose, the dist local overlay, the IBM Cloud installer's
#                  embedded compose, and the dev compose files.
#     --strict-unresolved   treat an un-probeable (${...}-carrying) ref as a failure.
#
# Testability: the manifest probe is `${IMAGE_PROBE:-}` when set — invoked as
#   "$IMAGE_PROBE" <ref>  (exit 0 = exists, non-zero = absent/unknown). The self-test
#   (scripts/tests/verify-image-pins.test.sh) sets it to a fake so the four outcomes are
#   proven without a live registry, mirroring registry-tag-probe.test.sh's fake `curl`.
#
# Exit: 0 all probed pins exist; 1 at least one is missing / un-probeable-under-strict /
#       the probe could not run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OVERRIDE_REGISTRY=""
OVERRIDE_VERSION=""
EXPECT_VERSION=""
STRICT_UNRESOLVED=0
FILES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --registry) OVERRIDE_REGISTRY="${2:?--registry needs a value}"; shift 2 ;;
    --version)  OVERRIDE_VERSION="${2:?--version needs a value}"; shift 2 ;;
    --expect-version) EXPECT_VERSION="${2:?--expect-version needs a value}"; shift 2 ;;
    --file)     FILES+=("${2:?--file needs a path}"); shift 2 ;;
    --strict-unresolved) STRICT_UNRESOLVED=1; shift ;;
    -h|--help)  sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "::error::verify-image-pins: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

# --expect-version and --version are mutually exclusive: --version OVERRIDES the
# rendered tag (so a consistency check would pass vacuously against its own
# override), while --expect-version ASSERTS the committed default renders to a
# specific tag. Refuse the combination rather than silently ignore one.
if [ -n "$EXPECT_VERSION" ] && [ -n "$OVERRIDE_VERSION" ]; then
  echo "::error::verify-image-pins: --expect-version and --version are mutually exclusive (the consistency check must read the committed compose default, not an override)." >&2
  exit 2
fi

if [ "${#FILES[@]}" -eq 0 ]; then
  # Default set = every SHIPPED registry-qualified pin source (bonnyr-f5 #193 r4,
  # B-2 file-set widening): the dist/ compose the operator runs, the dist local
  # overlay, AND the IBM Cloud installer's EMBEDDED compose (which pins the same
  # ${BNK_FORGE_VERSION:-...} images via `image:` lines the grep below matches).
  # The repo-root dev compose files carry only bare local build tags (skipped as
  # non-registry-qualified) but are kept so a stray registry pin there is caught too.
  FILES=(
    "$ROOT/dist/docker-compose.yml"
    "$ROOT/dist/docker-compose.local.yml"
    "$ROOT/scripts/ibm_cloud_bnk_forge.sh"
    "$ROOT/docker-compose.yml"
    "$ROOT/docker-compose.local.yml"
  )
fi

# Resolve one compose `image:` value into a concrete ref, applying the overrides and
# the compose `${VAR:-default}` semantics. Echoes the resolved ref (may still contain
# an unresolved ${...} if a var has no default and no override).
_resolve_ref() {
  local ref="$1"
  # BNK_FORGE_REGISTRY / BNK_FORGE_VERSION: override wins, else the baked default.
  if [ -n "$OVERRIDE_REGISTRY" ]; then
    ref="$(printf '%s' "$ref" | sed -E "s|\\\$\{BNK_FORGE_REGISTRY:-[^}]*\}|${OVERRIDE_REGISTRY}|g")"
  fi
  if [ -n "$OVERRIDE_VERSION" ]; then
    ref="$(printf '%s' "$ref" | sed -E "s|\\\$\{BNK_FORGE_VERSION:-[^}]*\}|${OVERRIDE_VERSION}|g")"
  fi
  # Any remaining ${VAR:-default} -> its default (what compose renders when VAR unset).
  ref="$(printf '%s' "$ref" | sed -E 's|\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}|\1|g')"
  printf '%s' "$ref"
}

# The manifest probe. Overridable for tests via $IMAGE_PROBE. Returns:
#   0 exists · 1 absent/unknown · 2 cannot probe (no tool)
probe_manifest() {
  local ref="$1"
  if [ -n "${IMAGE_PROBE:-}" ]; then
    "$IMAGE_PROBE" "$ref" >/dev/null 2>&1
    return $?
  fi
  if command -v docker >/dev/null 2>&1; then
    docker manifest inspect "$ref" >/dev/null 2>&1 && return 0
    docker buildx imagetools inspect "$ref" >/dev/null 2>&1 && return 0
    return 1
  fi
  return 2
}

fail=0
probed=0
for f in "${FILES[@]}"; do
  [ -f "$f" ] || { echo "  skip  (absent) ${f#"$ROOT"/}"; continue; }
  # Pull each `image:` value, stripping a trailing inline comment and whitespace.
  while IFS= read -r raw; do
    raw="${raw%%#*}"
    raw="$(printf '%s' "$raw" | sed -E 's/^[[:space:]]*image:[[:space:]]*//; s/[[:space:]]+$//')"
    [ -n "$raw" ] || continue
    # Is this a FIRST-PARTY pin — i.e. does its tag come from our ${BNK_FORGE_VERSION}
    # variable? (Used by the consistency gate; recorded BEFORE resolution flattens it.)
    uses_our_version=0
    case "$raw" in *'${BNK_FORGE_VERSION'*) uses_our_version=1 ;; esac
    ref="$(_resolve_ref "$raw")"
    # Only registry-qualified refs (contain a '/'): skip bare local build tags and
    # official single-segment images.
    case "$ref" in */*) : ;; *) continue ;; esac
    if printf '%s' "$ref" | grep -q '\${'; then
      echo "::warning::verify-image-pins: cannot resolve '$ref' in ${f#"$ROOT"/} (a var with no default/override)"
      [ "$STRICT_UNRESOLVED" = 1 ] && fail=1
      continue
    fi
    if [ -n "$EXPECT_VERSION" ]; then
      # ── Consistency mode (bonnyr-f5 #193 r4, B-2 PRE-PUSH gate) ────────────────
      # The registry does NOT yet hold $EXPECT_VERSION at pre-push time, so probing
      # would fail-closed on the very version we are about to publish. Instead assert
      # that every FIRST-PARTY pin (its tag rendered from ${BNK_FORGE_VERSION}) equals
      # $EXPECT_VERSION. A shipped pin left at a stale / forward-dated default — the
      # B-1 class dist/ pinned 4.0.0 nobody published — is caught HERE, before the
      # tag/GitHub Release/image push/signing, while it is still recoverable. The
      # push that follows then guarantees existence. Third-party pins (e.g.
      # tecnativa/docker-socket-proxy) carry their own tag and are not asserted.
      [ "$uses_our_version" = 1 ] || continue
      probed=$((probed + 1))
      tag="${ref##*:}"
      if [ "$tag" = "$EXPECT_VERSION" ]; then
        echo "  OK    (== $EXPECT_VERSION) $ref"
      else
        echo "::error::verify-image-pins: ${f#"$ROOT"/} pins '$ref' (tag '$tag') but the release being published is v$EXPECT_VERSION — a shipped compose pin does not match the version. Re-run scripts/sync-version-artifacts.sh --write $EXPECT_VERSION before releasing."
        fail=1
      fi
      continue
    fi
    probed=$((probed + 1))
    if probe_manifest "$ref"; then
      echo "  OK    $ref"
    else
      rc=$?
      if [ "$rc" = 2 ]; then
        echo "::error::verify-image-pins: no probe available (need docker, or set IMAGE_PROBE) to resolve '$ref'"
        fail=1
      else
        echo "::error::verify-image-pins: '$ref' is not in the registry (manifest unknown). A shipped compose file pins a tag that was never published — fix the pin (scripts/sync-version-artifacts.sh) or publish the image before releasing."
        fail=1
      fi
    fi
  done < <(grep -E '^[[:space:]]*image:' "$f" || true)
done

if [ "$probed" -eq 0 ] && [ "$fail" -eq 0 ]; then
  if [ -n "$EXPECT_VERSION" ]; then
    echo "::error::verify-image-pins: no first-party (\${BNK_FORGE_VERSION}) compose pin found to check against v$EXPECT_VERSION — refusing to pass vacuously (wrong file set, or the pins lost their version variable?)" >&2
  else
    echo "::error::verify-image-pins: found no registry-qualified image pins to probe — refusing to pass vacuously" >&2
  fi
  exit 1
fi
if [ "$fail" -ne 0 ]; then
  echo "verify-image-pins: FAILURES above." >&2
elif [ -n "$EXPECT_VERSION" ]; then
  echo "verify-image-pins: all $probed first-party pin(s) equal v$EXPECT_VERSION."
else
  echo "verify-image-pins: all $probed registry-qualified pin(s) resolve."
fi
exit "$fail"
