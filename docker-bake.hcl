variable "REGISTRY" {}
variable "VERSION" {}
variable "PLATFORMS" {
  default = "linux/amd64,linux/arm64"
}
# Floating tag pushed alongside the immutable :VERSION tag.
#   "latest"        -> conventional floating tag (default)
#   "customer-build"-> rolling tag for the customer-build line
#   ""              -> push ONLY the versioned tag (e.g. registries that forbid overwriting an existing floating manifest)
variable "ROLLING_TAG" {
  default = "latest"
}
# OCI image labels — injectable at build time.
#   GIT_REVISION  -> git commit SHA (set by make push-images / CI)
#   SOURCE_URL    -> canonical repo URL (default: upstream)
variable "GIT_REVISION" {
  default = "unknown"
}
variable "SOURCE_URL" {
  default = "https://github.com/f5devcentral/bnk-forge"
}
# Build timestamp for org.opencontainers.image.created (RFC 3339). Empty by
# default so a plain `docker buildx bake` does not stamp a wall-clock time into
# the image config.
#   A timestamp() default stamped a FRESH time into every build, guaranteeing a
#   different config digest on every rebuild. CI instead sets CREATED to the
#   release commit's committer date (fixed for a given tag) and also exports
#   SOURCE_DATE_EPOCH. That removes the two most obvious sources of variance.
#
#   It does NOT make a rebuild byte-reproducible, and a republish CAN move the
#   digest. The Dockerfiles run `apt-get update` / `apk upgrade` / `pip` / `npm`
#   against live package indexes, and there is no buildkit `rewrite-timestamp`
#   pass normalizing layer mtimes to SOURCE_DATE_EPOCH — so two builds of the
#   same tag can produce different layer diff_ids and a different image digest
#   (bonnyr-f5 #181 round 4, verified: two SOURCE_DATE_EPOCH-pinned builds gave
#   divergent digests). Because a republish may not resolve to the original
#   digest, the immutable :VERSION tag is protected the honest way — an existence
#   probe REFUSES a republish by default and requires an explicit force to
#   overwrite — rather than by relying on determinism we do not have. That probe
#   guards BOTH paths that bake --push this file: the release workflow (see
#   release.yml "Refuse to overwrite an already-published tag") and the operator
#   `make push-images` command (Makefile, FORCE_LATEST=1 to override). Both call
#   scripts/registry-tag-probe.sh, so the protection is not fixed at one call
#   site only (bonnyr-f5 #181 round 5, F3).
variable "CREATED" {
  default = ""
}

group "default" {
  targets = ["api", "worker", "beat", "frontend", "proxy", "mcp", "operator"]
}

target "_common" {
  platforms = split(",", PLATFORMS)
  # Omit org.opencontainers.image.created entirely when CREATED is empty instead
  # of stamping an empty-string label: an empty value is spec-invalid and
  # falsifies the label table in docs/DOCKER.md. A plain `docker buildx bake`
  # (e.g. `make push-images`, which does not set CREATED) must not emit the key
  # at all; CI sets CREATED to the release commit's committer date. This is the
  # same conditional shape the ROLLING_TAG tags use below (bonnyr-f5 #181 round
  # 5, F7).
  labels = merge(
    {
      "org.opencontainers.image.source"   = SOURCE_URL
      "org.opencontainers.image.revision" = GIT_REVISION
      "org.opencontainers.image.version"  = VERSION
    },
    CREATED != "" ? { "org.opencontainers.image.created" = CREATED } : {},
  )
}

target "_backend" {
  inherits   = ["_common"]
  // Repo root, not ./backend — the VERSION file lives above backend/ and the
  // image needs it (see backend/Dockerfile). Matches the frontend target.
  context    = "."
  dockerfile = "backend/Dockerfile"
}

target "api" {
  inherits = ["_backend"]
  target   = "api"
  tags = concat(
    ["${REGISTRY}/bnk-forge-api:${VERSION}"],
    ROLLING_TAG != "" ? ["${REGISTRY}/bnk-forge-api:${ROLLING_TAG}"] : [],
  )
}

target "worker" {
  inherits = ["_backend"]
  target   = "worker"
  tags = concat(
    ["${REGISTRY}/bnk-forge-worker:${VERSION}"],
    ROLLING_TAG != "" ? ["${REGISTRY}/bnk-forge-worker:${ROLLING_TAG}"] : [],
  )
}

target "beat" {
  inherits = ["_backend"]
  target   = "beat"
  tags = concat(
    ["${REGISTRY}/bnk-forge-beat:${VERSION}"],
    ROLLING_TAG != "" ? ["${REGISTRY}/bnk-forge-beat:${ROLLING_TAG}"] : [],
  )
}

target "frontend" {
  inherits   = ["_common"]
  context    = "."
  dockerfile = "frontend-v2/Dockerfile"
  tags = concat(
    ["${REGISTRY}/bnk-forge-frontend:${VERSION}"],
    ROLLING_TAG != "" ? ["${REGISTRY}/bnk-forge-frontend:${ROLLING_TAG}"] : [],
  )
}

target "proxy" {
  inherits = ["_common"]
  context  = "./proxy"
  tags = concat(
    ["${REGISTRY}/bnk-forge-proxy:${VERSION}"],
    ROLLING_TAG != "" ? ["${REGISTRY}/bnk-forge-proxy:${ROLLING_TAG}"] : [],
  )
}

target "mcp" {
  inherits = ["_common"]
  context  = "./mcp-server"
  tags = concat(
    ["${REGISTRY}/bnk-forge-mcp:${VERSION}"],
    ROLLING_TAG != "" ? ["${REGISTRY}/bnk-forge-mcp:${ROLLING_TAG}"] : [],
  )
}

target "operator" {
  inherits = ["_common"]
  context  = "./bnk-operator"
  target   = "runtime"
  tags = concat(
    ["${REGISTRY}/bnk-forge-operator:${VERSION}"],
    ROLLING_TAG != "" ? ["${REGISTRY}/bnk-forge-operator:${ROLLING_TAG}"] : [],
  )
}