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

group "default" {
  targets = ["api", "worker", "beat", "frontend", "proxy", "mcp"]
}

target "_common" {
  platforms = split(",", PLATFORMS)
  labels = {
    "org.opencontainers.image.source"   = SOURCE_URL
    "org.opencontainers.image.revision" = GIT_REVISION
    "org.opencontainers.image.version"  = VERSION
    "org.opencontainers.image.created"  = timestamp()
  }
}

target "_backend" {
  inherits = ["_common"]
  context  = "./backend"
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