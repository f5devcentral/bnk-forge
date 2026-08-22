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
#   release.yml must call this AFTER images are pushed and BEFORE the release is
#   finalized, so a bad pin is caught while it is still recoverable. Run it against the
#   tag actually published (the release job knows REGISTRY + the new VERSION):
#
#       bash scripts/verify-image-pins.sh \
#         --registry "$REGISTRY" --version "$NEW_VERSION"
#
#   (Without overrides it validates the pins as a fresh install would render them from
#   the committed VERSION/defaults — useful in a pre-merge gate too.)
#
# Usage:
#   verify-image-pins.sh [--registry <host/ns>] [--version <tag>] [--file <compose>]...
#     --registry   override ${BNK_FORGE_REGISTRY:-...} in every pin (default: the
#                  compose default, ghcr.io/f5devcentral)
#     --version    override ${BNK_FORGE_VERSION:-...} in every pin (default: the
#                  compose-baked default, which sync-version-artifacts.sh keeps == VERSION)
#     --file       add a compose file to scan (repeatable). Default set: the shipped
#                  dist/ and dev compose files.
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
STRICT_UNRESOLVED=0
FILES=()

while [ $# -gt 0 ]; do
  case "$1" in
    --registry) OVERRIDE_REGISTRY="${2:?--registry needs a value}"; shift 2 ;;
    --version)  OVERRIDE_VERSION="${2:?--version needs a value}"; shift 2 ;;
    --file)     FILES+=("${2:?--file needs a path}"); shift 2 ;;
    --strict-unresolved) STRICT_UNRESOLVED=1; shift ;;
    -h|--help)  sed -n '2,40p' "$0"; exit 0 ;;
    *) echo "::error::verify-image-pins: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

if [ "${#FILES[@]}" -eq 0 ]; then
  FILES=(
    "$ROOT/dist/docker-compose.yml"
    "$ROOT/dist/docker-compose.local.yml"
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
    ref="$(_resolve_ref "$raw")"
    # Only registry-qualified refs (contain a '/'): skip bare local build tags and
    # official single-segment images.
    case "$ref" in */*) : ;; *) continue ;; esac
    if printf '%s' "$ref" | grep -q '\${'; then
      echo "::warning::verify-image-pins: cannot resolve '$ref' in ${f#"$ROOT"/} (a var with no default/override)"
      [ "$STRICT_UNRESOLVED" = 1 ] && fail=1
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
  echo "::error::verify-image-pins: found no registry-qualified image pins to probe — refusing to pass vacuously" >&2
  exit 1
fi
[ "$fail" -eq 0 ] && echo "verify-image-pins: all $probed registry-qualified pin(s) resolve." || echo "verify-image-pins: FAILURES above." >&2
exit "$fail"
