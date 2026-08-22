#!/usr/bin/env bash
# registry-overwrite-guard.sh — overwrite-guard POLICY around registry-tag-probe.sh.
#
# Classifies every release image via registry-tag-probe.sh and REFUSES (exit 1) if
# any immutable :VERSION manifest already exists, OR the probe is inconclusive, OR
# the probe cannot run — unless FORCE=true. Single-sourced so the release.yml
# PRE-PUSH gate (INV-31) shares ONE policy instead of open-coding it per site
# (bonnyr-f5 #193 M3/M4). It exists so nothing irreversible (the `git push origin
# main` + tag push) happens before this fail-closed check.
#
# TWO independence properties the inline guard historically got wrong:
#   • the vacuity floor (how many images to expect) comes from docker-bake.hcl's
#     "default" group, NOT from the probe we are guarding — so an unavailable probe
#     cannot make "0 expected == 0 classified" look like "safe to publish" (M3);
#   • the probe's EXIT STATUS is asserted before its output is trusted — a probe
#     that cannot run fails CLOSED (M3).
#
# The floor is derived from the TOOL (`docker buildx bake --print default` + jq),
# scoped to the DEFAULT group, NOT by counting `targets = [` lines in the file. The
# old unscoped `sed` counted EVERY `targets = [` line, so adding any SECOND bake
# group (an ordinary edit) inflated the floor and made this gate refuse forever,
# blaming the registry for a bake-file change (bonnyr-f5 #193 M2). A parse/tooling
# failure is now reported as exactly that, distinct from "registry unreachable".
#
# Env in:
#   REGISTRY, VERSION            (required; forwarded to registry-tag-probe.sh + bake)
#   FORCE                        ("true" overrides a refusal, deliberately)
#   REGISTRY_USERNAME/PASSWORD   (optional; forwarded for the Bearer challenge)
#   PROBE                        (path to registry-tag-probe.sh; default: sibling)
#   BAKE_FILE                    (path to docker-bake.hcl; default: ./docker-bake.hcl)
# Exit: 0 safe-to-publish (or FORCE overrode a refusal); 1 refuse.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PROBE="${PROBE:-$HERE/registry-tag-probe.sh}"
BAKE_FILE="${BAKE_FILE:-docker-bake.hcl}"
FORCE="${FORCE:-}"
: "${REGISTRY:?REGISTRY is required (e.g. ghcr.io/your-org)}"
: "${VERSION:?VERSION is required (e.g. 3.1.6)}"

# ── Independent vacuity floor (M2/M3): count docker-bake.hcl's DEFAULT group via
# the tool, scoped to `.group.default.targets`, so a second bake group cannot wedge
# the gate. This is a BAKE-FILE/tooling concern, kept strictly separate from the
# registry probe below and from its "registry unreachable" remediation text.
if ! command -v docker >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
  echo "::error::registry-overwrite-guard: 'docker' and 'jq' are required to count the bake default group (a tooling problem, NOT a registry problem). Install them and re-run." >&2
  exit 1
fi
IMAGES_N="$(REGISTRY="$REGISTRY" VERSION="$VERSION" \
  docker buildx bake --file "$BAKE_FILE" --print default 2>/dev/null \
  | jq -r '.group.default.targets | length' 2>/dev/null || true)"
if ! printf '%s' "${IMAGES_N:-}" | grep -qE '^[0-9]+$' || [ "${IMAGES_N:-0}" -lt 1 ]; then
  echo "::error::registry-overwrite-guard: could not count the DEFAULT bake group in $BAKE_FILE via 'docker buildx bake --print default' + jq (a bake-file PARSE / tooling problem — NOT a registry problem). Fix $BAKE_FILE / the buildx+jq tooling; do not set FORCE for this." >&2
  exit 1
fi

# ── Assert the probe's EXIT STATUS before trusting its output (M3) ──────────────
if ! PROBE_OUT="$(REGISTRY="$REGISTRY" VERSION="$VERSION" bash "$PROBE")"; then
  if [ "$FORCE" = "true" ]; then
    echo "::warning::force=true — publishing despite the registry probe being unrunnable; any already-published :${VERSION} tags would be overwritten and their cosign/SBOM/SLSA attestations orphaned." >&2
    exit 0
  fi
  echo "::error::the registry existence probe ($PROBE) could not run. Refusing to publish — an already-published immutable :${VERSION} tag cannot be ruled out. Re-run once the registry/tooling is reachable, or set FORCE=true to overwrite deliberately." >&2
  exit 1
fi

PROBE_N="$(printf '%s\n' "$PROBE_OUT" | grep -c . || true)"
EXISTING=""
UNKNOWN=""
if [ "$PROBE_N" -ne "$IMAGES_N" ]; then
  UNKNOWN="  the registry existence probe classified ${PROBE_N} of ${IMAGES_N} images; treating as inconclusive"$'\n'
else
  while IFS=$'\t' read -r status ref detail; do
    case "$status" in
      exists)  EXISTING="${EXISTING}  ${ref}"$'\n' ;;
      absent)  : ;; # 404 — the immutable tag is free
      unknown) UNKNOWN="${UNKNOWN}  ${ref}: ${detail}"$'\n' ;;
      # FAIL CLOSED on any unrecognised/empty status (bonnyr-f5 #193 r3 minor). The
      # shipped probe only ever emits exists/absent/unknown, but a malformed or
      # empty status field is the MOST inconclusive state there is — without this
      # arm it fell through to neither bucket and the guard printed "safe to
      # publish", overwriting an immutable tag on garbage. Treat it as inconclusive.
      *)       UNKNOWN="${UNKNOWN}  ${ref:-<no ref>}: unrecognised probe status '${status:-<empty>}' — treating as inconclusive"$'\n' ;;
    esac
  done <<< "$PROBE_OUT"
fi

if [ -n "$UNKNOWN" ]; then
  printf 'Registry existence probe was inconclusive (a non-not-found error) for:\n%s' "$UNKNOWN" >&2
  if [ "$FORCE" = "true" ]; then
    echo "::warning::force=true — publishing despite an inconclusive existence probe; if any of these tags were in fact already published this overwrites the immutable :${VERSION} tag and orphans its attestations." >&2
  else
    echo "::error::Could not confirm whether the :${VERSION} tag is free (auth / network / rate-limit / bad ref). Refusing to publish because an existing immutable tag cannot be ruled out. Re-run once the registry is reachable, or set FORCE=true only if you intend to overwrite." >&2
    exit 1
  fi
fi

if [ -z "$EXISTING" ]; then
  echo "No existing :${VERSION} manifests found — safe to publish."
  exit 0
fi

printf 'Images already published for this tag:\n%s' "$EXISTING" >&2
if [ "$FORCE" = "true" ]; then
  echo "::warning::force=true — overwriting the existing :${VERSION} manifests; the cosign/SBOM/SLSA attestations bound to the previous digests are now orphaned." >&2
  exit 0
fi
echo "::error::Images for v${VERSION} already exist in ${REGISTRY}. Republishing would move the immutable :${VERSION} tag and orphan its attestations. Set FORCE=true only if you intend to overwrite them." >&2
exit 1
