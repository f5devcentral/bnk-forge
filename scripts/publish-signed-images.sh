#!/usr/bin/env bash
# publish-signed-images.sh — Keyless cosign signing + SBOM + provenance for all BNK Forge images.
#
# Workflow:
#   1. Caller MUST have already pushed images via `make push-images` (or push-customer-build).
#   2. This script resolves the digest for each image, then:
#      a. Signs it with keyless cosign (Sigstore/Fulcio/Rekor — OIDC identity of the caller).
#      b. Generates a CycloneDX SBOM via syft and attaches it via cosign attest.
#      c. Attaches SLSA provenance via cosign attest.
#   3. In dry-run mode (default) it only prints the commands — nothing is executed.
#
# Usage:
#   # Print the plan (safe, no network I/O beyond digest resolve):
#   BNK_FORGE_REGISTRY=ghcr.io/your-org ./scripts/publish-signed-images.sh
#
#   # Execute signing (opens a browser for OIDC login on first run):
#   BNK_FORGE_REGISTRY=ghcr.io/your-org ./scripts/publish-signed-images.sh --execute
#
#   # Override version:
#   BNK_FORGE_REGISTRY=ghcr.io/your-org BNK_FORGE_VERSION=3.1.6 ./scripts/publish-signed-images.sh --execute
#
# Environment variables:
#   BNK_FORGE_REGISTRY  — required; e.g. ghcr.io/f5devcentral
#   BNK_FORGE_VERSION   — optional; defaults to contents of ./VERSION
#   DRY_RUN             — set to 0 to execute (equivalent to --execute)
#
# Prerequisites:
#   cosign  — https://docs.sigstore.dev/cosign/system_config/installation/
#              brew install cosign
#   syft    — https://github.com/anchore/syft#installation
#              brew install syft
#
# Consumer verification (see docs/DOCKER.md for full details):
#   cosign verify <image>@<digest> \
#     --certificate-identity-regexp 'https://github.com/f5devcentral/bnk-forge/\.github/workflows/release\.yml@.*' \
#     --certificate-oidc-issuer https://token.actions.githubusercontent.com

set -euo pipefail

# ─── Parse flags ─────────────────────────────────────────────────────────────

DRY_RUN="${DRY_RUN:-1}"

for arg in "$@"; do
  case "$arg" in
    --execute)   DRY_RUN=0 ;;
    --dry-run)   DRY_RUN=1 ;;
    --help|-h)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $arg" >&2
      echo "Usage: $0 [--execute|--dry-run|--help]" >&2
      exit 1
      ;;
  esac
done

# ─── Validate environment ─────────────────────────────────────────────────────

if [[ -z "${BNK_FORGE_REGISTRY:-}" ]]; then
  echo "ERROR: BNK_FORGE_REGISTRY is not set." >&2
  echo "" >&2
  echo "Usage: BNK_FORGE_REGISTRY=ghcr.io/your-org $0 [--execute]" >&2
  exit 1
fi

REGISTRY="${BNK_FORGE_REGISTRY}"
VERSION="${BNK_FORGE_VERSION:-$(cat "$(dirname "$0")/../VERSION")}"

# ─── Check required tools ────────────────────────────────────────────────────

check_tool() {
  local tool="$1"
  local hint="$2"
  if ! command -v "$tool" &>/dev/null; then
    echo "ERROR: '$tool' is not installed." >&2
    echo "  Install: $hint" >&2
    exit 1
  fi
}

if [[ "$DRY_RUN" == "0" ]]; then
  check_tool cosign "brew install cosign  OR  https://docs.sigstore.dev/cosign/system_config/installation/"
  check_tool syft   "brew install syft    OR  https://github.com/anchore/syft#installation"
fi

# ─── Image list ───────────────────────────────────────────────────────────────
# 7 images from docker-bake.hcl (pushed via `make push-images`).
#
# bnk-forge-operator joined the docker-bake.hcl "default" group (target
# "operator", context ./bnk-operator, Dockerfile target "runtime") so it now
# publishes to BNK_FORGE_REGISTRY at the release VERSION like every other
# image, alongside the separate bnk-operator/build.sh path. This image is
# published and signed; chart deployments can override image.repository and
# image.tag in values.yaml to point to this GHCR release image as needed.

IMAGES=(
  "bnk-forge-api"
  "bnk-forge-worker"
  "bnk-forge-beat"
  "bnk-forge-frontend"
  "bnk-forge-proxy"
  "bnk-forge-mcp"
  "bnk-forge-operator"
)

# ─── Helpers ─────────────────────────────────────────────────────────────────

# Print a command in dry-run or execute it in live mode.
run_or_print() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [dry-run] $*"
  else
    "$@"
  fi
}

# Resolve image digest.  In dry-run mode, emit a placeholder instead of hitting
# the registry so the script is fully offline-safe.
resolve_digest() {
  local image_ref="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "<digest-of-${image_ref##*/}>"
  else
    # Resolve the TOP-LEVEL digest (the multi-arch image index for a multi-arch
    # push), NOT a per-platform manifest. This is the digest `cosign verify <tag>`
    # resolves a tag to, so the signature must attach to it.
    docker buildx imagetools inspect "${image_ref}" --format '{{.Manifest.Digest}}' 2>/dev/null \
    || crane digest "${image_ref}" 2>/dev/null \
    || { echo "ERROR: could not resolve digest for ${image_ref}" >&2; exit 1; }
  fi
}

# ─── Provenance predicate (minimal SLSA Build L1) ────────────────────────────
# Written to a temp file and attached via cosign attest --type slsaprovenance.
#
# metadata.buildStartedOn is OMITTED, not stamped from `date -u` here: this script
# runs at SIGNING time, AFTER the build (and a sign_only recovery re-signs an
# already-built digest), so a wall-clock time taken here is not the build start and
# would be a knowingly-wrong timestamp in the attestation. The field is optional in
# SLSA v0.2; omitting it is honest, stamping the wrong value is not (bonnyr-f5 #193
# minor). The build's real time is already carried by the OCI
# org.opencontainers.image.created label (docker-bake.hcl CREATED).

write_provenance() {
  local image_ref="$1"
  local digest="$2"
  local out_file="$3"

  local git_sha
  git_sha="$(git -C "$(dirname "$0")/.." rev-parse HEAD 2>/dev/null || echo "unknown")"
  # Provenance must name the remote this build actually came from. Prefer an
  # explicit SOURCE_URL (CI), fall back to the checkout's own origin.
  local repo_url="${SOURCE_URL:-}"
  if [[ -z "${repo_url}" ]]; then
    repo_url="$(git -C "$(dirname "$0")/.." remote get-url origin 2>/dev/null || true)"
    repo_url="${repo_url%.git}"
    repo_url="${repo_url/git@github.com:/https://github.com/}"
  fi
  repo_url="${repo_url:-https://github.com/f5devcentral/bnk-forge}"

  cat > "$out_file" <<PROVENANCE
{
  "_type": "https://slsa.dev/provenance/v0.2",
  "subject": [
    {
      "name": "${image_ref}",
      "digest": { "sha256": "${digest#sha256:}" }
    }
  ],
  "predicateType": "https://slsa.dev/provenance/v0.2",
  "predicate": {
    "builder": { "id": "manual-publish-script" },
    "buildType": "${repo_url}/blob/main/scripts/publish-signed-images.sh",
    "invocation": {
      "configSource": {
        "uri": "${repo_url}",
        "digest": { "sha1": "${git_sha}" },
        "entryPoint": "scripts/publish-signed-images.sh"
      }
    },
    "buildConfig": {
      "version": "${VERSION}"
    },
    "metadata": {
      "completeness": {
        "parameters": false,
        "environment": false,
        "materials": false
      },
      "reproducible": false
    }
  }
}
PROVENANCE
}

# ─── Main ─────────────────────────────────────────────────────────────────────

echo ""
echo "========================================================"
echo "  BNK Forge — Keyless Image Signing + SBOM + Provenance"
echo "========================================================"
echo "  Registry:  ${REGISTRY}"
echo "  Version:   ${VERSION}"
if [[ "$DRY_RUN" == "1" ]]; then
  echo "  Mode:      DRY-RUN  (pass --execute to sign for real)"
else
  echo "  Mode:      EXECUTE  (will contact Sigstore + registry)"
fi
echo "========================================================"
echo ""
echo "  Images:"
for name in "${IMAGES[@]}"; do
  echo "    ${REGISTRY}/${name}:${VERSION}"
done
echo ""

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "${TMPDIR_WORK}"' EXIT

for name in "${IMAGES[@]}"; do
  IMAGE_REF="${REGISTRY}/${name}:${VERSION}"
  echo "──────────────────────────────────────────────────────"
  echo "  Image: ${IMAGE_REF}"

  # 1. Resolve digest (must sign by digest for immutability).
  echo "  Resolving digest..."
  DIGEST="$(resolve_digest "${IMAGE_REF}")"
  IMAGE_WITH_DIGEST="${REGISTRY}/${name}@${DIGEST}"
  echo "  Digest: ${DIGEST}"
  echo ""

  # 2. Sign with keyless cosign (Fulcio cert + Rekor transparency log).
  #    --yes skips the interactive OIDC consent prompt in CI-like contexts;
  #    the browser popup still appears on the first invocation if no ambient
  #    OIDC token is present.
  echo "  [1/3] Signing (keyless cosign)..."
  run_or_print cosign sign \
    --yes \
    "${IMAGE_WITH_DIGEST}"

  echo ""

  # 3. Generate CycloneDX SBOM via syft and attach via cosign attest.
  SBOM_FILE="${TMPDIR_WORK}/${name}-sbom.cdx.json"
  echo "  [2/3] Generating SBOM (syft → CycloneDX) and attesting..."
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [dry-run] syft ${IMAGE_WITH_DIGEST} -o cyclonedx-json --file ${SBOM_FILE}"
    echo "  [dry-run] cosign attest \\"
    echo "    --yes \\"
    echo "    --type cyclonedx \\"
    echo "    --predicate ${SBOM_FILE} \\"
    echo "    ${IMAGE_WITH_DIGEST}"
  else
    syft "${IMAGE_WITH_DIGEST}" -o cyclonedx-json --file "${SBOM_FILE}"
    cosign attest \
      --yes \
      --type cyclonedx \
      --predicate "${SBOM_FILE}" \
      "${IMAGE_WITH_DIGEST}"
  fi

  echo ""

  # 4. Attach SLSA Build L1 provenance.
  PROV_FILE="${TMPDIR_WORK}/${name}-provenance.json"
  echo "  [3/3] Attaching SLSA provenance..."
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "  [dry-run] write_provenance ${IMAGE_WITH_DIGEST} ${DIGEST} ${PROV_FILE}"
    echo "  [dry-run] cosign attest \\"
    echo "    --yes \\"
    echo "    --type slsaprovenance \\"
    echo "    --predicate ${PROV_FILE} \\"
    echo "    ${IMAGE_WITH_DIGEST}"
  else
    write_provenance "${IMAGE_WITH_DIGEST}" "${DIGEST}" "${PROV_FILE}"
    cosign attest \
      --yes \
      --type slsaprovenance \
      --predicate "${PROV_FILE}" \
      "${IMAGE_WITH_DIGEST}"
  fi

  echo ""
done

echo "========================================================"
if [[ "$DRY_RUN" == "1" ]]; then
  echo "  DRY-RUN complete — no registry writes performed."
  echo ""
  echo "  To sign for real:"
  echo "    BNK_FORGE_REGISTRY=${REGISTRY} $0 --execute"
else
  echo "  All images signed + SBOM + provenance attached."
  echo ""
  echo "  Verify a signed image:"
  echo "    cosign verify \\"
  echo "      ${REGISTRY}/bnk-forge-api@<digest> \\"
  echo "      --certificate-identity-regexp 'https://github.com/f5devcentral/bnk-forge/\.github/workflows/release\.yml@.*' \\"
  echo "      --certificate-oidc-issuer https://token.actions.githubusercontent.com"
  echo ""
  echo "  Verify the SBOM attestation:"
  echo "    cosign verify-attestation \\"
  echo "      --type cyclonedx \\"
  echo "      --certificate-identity-regexp 'https://github.com/f5devcentral/bnk-forge/\.github/workflows/release\.yml@.*' \\"
  echo "      --certificate-oidc-issuer https://token.actions.githubusercontent.com \\"
  echo "      ${REGISTRY}/bnk-forge-api@<digest>"
fi
echo "========================================================"
