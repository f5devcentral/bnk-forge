# syntax=docker/dockerfile:1
# =============================================================================
# BNK-Forge Backend — Multi-Target Dockerfile
# =============================================================================
#
# Builds role-specific images from a shared base:
#
#   docker build --target api    -t bnk-forge-api    backend/   (~300-400MB)
#   docker build --target worker -t bnk-forge-worker backend/   (~700-900MB)
#   docker build --target beat   -t bnk-forge-beat   backend/   (~300-400MB)
#
# Build args:
#   BUILD_DEV=true          Include pytest/factory-boy (for docker-compose.dev.yml)
#   INSTALL_INFRACOST=true  Include Infracost binary (deprecated in v2)
#
# Layer hierarchy:
#   python:3.11-slim → base (system deps + user)
#     → dependencies (pip install, cached)
#     → tooling-deps (base + ALL CLI tools — independent of app code)
#     → app-base (base + deps + app code)
#        ├── api    (uvicorn + helm/kubectl only)
#        ├── beat   (celery beat — no CLI tools)
#        └── worker (app-base + COPY --from=tooling-deps binaries)
#
# PERFORMANCE NOTES (OPT-001, OPT-002):
#   - BuildKit cache mounts for pip: survives across builds, ~60s → ~5s on cache hit
#   - API target downloads helm+kubectl directly (no tooling-deps stage dependency)
#   - tooling-deps stage consolidates downloads into fewer layers
#   - OPT-002: tooling-deps stage is independent of app code — code changes don't bust tool cache
#   - Use `docker compose build` (not --no-cache) for normal code changes
#   - Only use --no-cache when requirements.txt or tool versions change


# =============================================================================
# Stage 1: BASE — Python runtime + system deps (shared by ALL targets)
# =============================================================================
# python:3.11-slim — digest-pinned (Slice B / #300); re-pin when bumping the tag.
FROM python:3.11-slim@sha256:f9fa7f851e38bfb19c9de3afbc4b86ae7176ea7aaf94535c31df5458d5849457 AS base
ARG TARGETARCH=amd64

# Create non-root user for running the application
# UID/GID 1000 is standard for first non-root user
ARG UID=1000
ARG GID=1000
# Create docker group with GID 999 (common on Linux hosts) for socket access
RUN groupadd -g 999 docker || true && \
    groupadd -g ${GID} bnkforge && \
    useradd -m -u ${UID} -g bnkforge -G docker -s /bin/bash bnkforge

# Minimal system deps for the base image (curl for health checks, ca-certs for TLS)
# postgresql-client: pg_dump + psql for backup/restore operations (BR-002).
# Use the distro metapackage so the image stays buildable as Debian advances majors.
# --only-upgrade: the python:3.11-slim base lags Debian point releases and can
# ship base packages flagged CRITICAL — libgnutls30t64 (CVE-2026-42010 RSA-PSK
# auth bypass, CVE-2026-33845) and libssh2-1t64 (CVE-2026-55200 out-of-bounds
# write, fixed in 1.11.1-1+deb13u1). Pull the trixie-security builds explicitly;
# drop each once the base image tracks the fixed point release itself.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    openssh-client \
    postgresql-client \
    && apt-get install -y --only-upgrade libgnutls30t64 libssh2-1t64 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app


# =============================================================================
# Stage 2: DEPENDENCIES — Python packages (cached across builds)
# =============================================================================
# BuildKit cache mount keeps pip's download cache across builds.
# First build: downloads everything (~60s). Subsequent: near-instant.
#
FROM base AS dependencies
ARG BUILD_DEV=false
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$BUILD_DEV" = "true" ]; then \
      pip install --prefix=/install -r requirements-dev.txt; \
    else \
      pip install --prefix=/install -r requirements.txt; \
    fi


# =============================================================================
# Stage 3: APP-BASE — Base + dependencies + application code
# =============================================================================
# This is the shared foundation for all targets. It has the full Python app
# but NO infrastructure CLI tools. Rebuilds only on code changes (fast).
#
FROM base AS app-base
# Copy pre-built Python packages from dependencies stage
COPY --from=dependencies /install /usr/local

# Copy application code (ordered by change frequency — least-changing first)
# The repo-root VERSION file. core/config.py._read_version_file() has always
# looked for /app/VERSION; nothing copied it, so settings.VERSION fell back to
# "0.0.0" in every backend image — visible on /api, in the OpenAPI title and in
# the X-BNK-Forge-Version header. This is why the build context is the repo root
# rather than ./backend: VERSION lives above backend/ and cannot be reached from
# a context rooted inside it. The frontend image already builds this way.
COPY VERSION ./VERSION
COPY backend/alembic.ini ./
COPY backend/alembic/ ./alembic/
COPY backend/data/ ./data/
COPY backend/core/ ./core/
COPY backend/models/ ./models/
COPY backend/schemas/ ./schemas/
COPY backend/utils/ ./utils/
COPY backend/modules/ ./modules/
COPY backend/services/ ./services/
COPY backend/routes/ ./routes/
COPY backend/tasks/ ./tasks/
# Python entry points change most often — copy last for best cache hits
COPY backend/*.py ./

# Create required directories and set ownership to bnkforge user
# /app/bfb-cache is pre-created so the named Docker volume inherits
# correct ownership on first mount (otherwise it initializes as root).
RUN mkdir -p /tmp/bnk-forge-logs /tmp/bnk-forge-modules /app/state /app/helm_charts /app/projects /app/keys /app/agent-token /app/workspaces /app/bfb-cache /app/provider-cache && \
    chown -R bnkforge:bnkforge /app /tmp/bnk-forge-logs /tmp/bnk-forge-modules

# Shared OpenTofu provider cache path (mounted from compose volume at runtime)
ENV TF_PLUGIN_CACHE_DIR=/app/provider-cache

# Copy entrypoint script (handles DB migrations for API, skips for celery)
COPY backend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]


# =============================================================================
# Stage 4: TOOLING-DEPS — Infrastructure CLI tools (independent of app code)
# =============================================================================
# Heavy stage (~500MB+) with all the CLI tools celery workers need to execute
# infrastructure operations: OpenTofu, Helm, kubectl, AWS CLI, Docker CLI, git.
#
# OPT-002: Extends base (NOT app-base) so Python code changes never invalidate
# this stage. Binaries are COPYed into the worker stage at build time.
#
# OPT-001: Consolidated tool downloads into fewer RUN layers for better caching.
# Each tool version is pinned with SHA256 checksums for reproducibility.
#
FROM base AS tooling-deps
ARG TARGETARCH=amd64

# --- Tool versions (change these to trigger re-download) ---
ENV OPENTOFU_VERSION=1.11.5
ENV OPENTOFU_SHA256_amd64=901121681e751574d739de5208cad059eddf9bd739b575745cf9e3c961b28a13
ENV OPENTOFU_SHA256_arm64=1b79f8fe3cbf7ac0540507bb6bd0ecab1675c78393bbf492867eaabb79840eec
ENV HELM_VERSION=3.20.0
ENV HELM_SHA256_amd64=dbb4c8fc8e19d159d1a63dda8db655f9ffa4aac1b9a6b188b34a40957119b286
ENV HELM_SHA256_arm64=bfb14953295d5324d47ab55f3dfba6da28d46c848978c8fbf412d4271bdc29f1
ENV KUBECTL_VERSION=1.32.12
ENV KUBECTL_SHA256_amd64=adac5674d19f47ed3f0620bfeb1932d3d5f4557d49f311f6f65b6b75e0721ee1
ENV KUBECTL_SHA256_arm64=86fa465134c54de6202d89d41eb89504810236cc968e1402a31f3674f66ffdbd
# llmtop — vLLM/SGLang/NIM/LMCache scraper. Subprocessed by the celery worker
# from the backend-health task to build per-pod inference metrics for one
# F5BigAnalyzer's monitored apps. Worker is the right place: it has aws CLI +
# kubectl, so EKS kubeconfigs with `exec: aws eks get-token` auth correctly.
ENV LLMTOP_VERSION=0.2.0
ENV LLMTOP_SHA256_amd64=2f2e043d5aa33a923ab7ce2921a92e9780dc1ec2241efcc20462cf5c673e8ec7
ENV LLMTOP_SHA256_arm64=6c2560827e9a1a810d472ae21b5a41b450e93e20ba46d2657403da873090c4ab
# oras — OCI registry client used by ReleaseSource live-fetch (ADR-494).
# Needed in both api (synchronous uvicorn endpoint) and worker stages.
# SHA256 values verified against upstream oras_1.2.2_checksums.txt (confirmed match).
ENV ORAS_VERSION=1.2.2
ENV ORAS_SHA256_amd64=bff970346470e5ef888e9f2c0bf7f8ee47283f5a45207d6e7a037da1fb0eae0d
ENV ORAS_SHA256_arm64=edd7195cbb8ba56c29ede413eefa10c8026201d63326017cd315841b4063aa56

# System packages needed for tool downloads
# IMPORTANT: Terraform local-exec provisioners default to /bin/sh.
# Debian uses dash for /bin/sh which breaks $() command substitution in
# heredoc provisioners. Linking /bin/sh -> bash ensures compatibility with
# all existing module provisioner scripts.
RUN apt-get update && apt-get install -y --no-install-recommends \
    unzip jq gnupg lsb-release \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /bin/bash /bin/sh

# Docker CLI (for container management via Docker socket)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl --retry 3 --retry-delay 2 --retry-connrefused -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && apt-get purge -y --auto-remove gnupg lsb-release \
    && rm -rf /var/lib/apt/lists/*

# AWS CLI v2 (required for EKS kubeconfig authentication)
RUN curl --retry 3 --retry-delay 2 --retry-connrefused -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "awscliv2.zip" \
    && unzip awscliv2.zip \
    && ./aws/install \
    && rm -rf aws awscliv2.zip

# OPT-001: Download infrastructure CLI tools.
# GitHub URLs (tofu, llmtop, oras) use ADD — Docker-daemon fetch via host cert store,
# DLP-safe per F5 KB57735. get.helm.sh and dl.k8s.io are not TLS-intercepted;
# curl is fine for those.

# tofu — github.com: ADD (DLP-safe)
ADD "https://github.com/opentofu/opentofu/releases/download/v${OPENTOFU_VERSION}/tofu_${OPENTOFU_VERSION}_linux_${TARGETARCH}.zip" /tmp/tofu.zip
RUN EXPECTED=$(eval echo \$OPENTOFU_SHA256_${TARGETARCH}) \
    && echo "${EXPECTED}  /tmp/tofu.zip" | sha256sum -c - \
    && unzip /tmp/tofu.zip tofu -d /tmp \
    && mv /tmp/tofu /usr/local/bin/ && chmod +x /usr/local/bin/tofu \
    && rm /tmp/tofu.zip

# helm — get.helm.sh + kubectl — dl.k8s.io: curl (not TLS-intercepted)
RUN curl --retry 3 --retry-delay 2 --retry-connrefused -fsSL "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" -o /tmp/helm.tar.gz \
    && EXPECTED=$(eval echo \$HELM_SHA256_${TARGETARCH}) \
    && echo "${EXPECTED}  /tmp/helm.tar.gz" | sha256sum -c - \
    && tar -zxvf /tmp/helm.tar.gz -C /tmp && mv /tmp/linux-${TARGETARCH}/helm /usr/local/bin/ && chmod +x /usr/local/bin/helm && rm -rf /tmp/linux-${TARGETARCH} /tmp/helm.tar.gz \
    && curl --retry 3 --retry-delay 2 --retry-connrefused -fsSL "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" -o /usr/local/bin/kubectl \
    && EXPECTED=$(eval echo \$KUBECTL_SHA256_${TARGETARCH}) \
    && echo "${EXPECTED}  /usr/local/bin/kubectl" | sha256sum -c - \
    && chmod +x /usr/local/bin/kubectl

# llmtop — github.com: ADD (DLP-safe)
ADD "https://github.com/InfraWhisperer/llmtop/releases/download/v${LLMTOP_VERSION}/llmtop_${LLMTOP_VERSION}_linux_${TARGETARCH}.tar.gz" /tmp/llmtop.tar.gz
RUN EXPECTED=$(eval echo \$LLMTOP_SHA256_${TARGETARCH}) \
    && echo "${EXPECTED}  /tmp/llmtop.tar.gz" | sha256sum -c - \
    && tar -zxf /tmp/llmtop.tar.gz -C /tmp llmtop \
    && mv /tmp/llmtop /usr/local/bin/ && chmod +x /usr/local/bin/llmtop \
    && rm /tmp/llmtop.tar.gz

# oras — github.com: ADD (DLP-safe)
ADD "https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_linux_${TARGETARCH}.tar.gz" /tmp/oras.tar.gz
RUN EXPECTED=$(eval echo \$ORAS_SHA256_${TARGETARCH}) \
    && echo "${EXPECTED}  /tmp/oras.tar.gz" | sha256sum -c - \
    && tar -zxf /tmp/oras.tar.gz -C /tmp oras \
    && mv /tmp/oras /usr/local/bin/ && chmod +x /usr/local/bin/oras \
    && rm /tmp/oras.tar.gz

# Infracost (OPTIONAL — deprecated in v2)
ARG INSTALL_INFRACOST=false
ENV INFRACOST_VERSION=0.10.43
ENV INFRACOST_SHA256_amd64=ea21bbc939530243a526b22e2f63ae3842b8ba6141ab0c0995bc49a0ad9ef2f4
ENV INFRACOST_SHA256_arm64=c79e4e822b6e241aa2548e9b860b77ce86e99acbc79d8954e2b14f174c55bd62
RUN if [ "$INSTALL_INFRACOST" = "true" ]; then \
      curl --retry 3 --retry-delay 2 --retry-connrefused -fsSL -o /tmp/infracost.tar.gz \
        "https://github.com/infracost/infracost/releases/download/v${INFRACOST_VERSION}/infracost-linux-${TARGETARCH}.tar.gz" \
      && EXPECTED=$(eval echo \$INFRACOST_SHA256_${TARGETARCH}) \
      && echo "${EXPECTED}  /tmp/infracost.tar.gz" | sha256sum -c - \
      && tar -xzf /tmp/infracost.tar.gz -C /tmp \
      && mv /tmp/infracost-linux-${TARGETARCH} /usr/local/bin/infracost \
      && chmod +x /usr/local/bin/infracost \
      && rm /tmp/infracost.tar.gz \
      || echo "Infracost installation failed - cost estimation will be disabled"; \
    else \
      echo "Skipping Infracost (opt-in via --build-arg INSTALL_INFRACOST=true)"; \
    fi

# Clean up build-only packages
RUN apt-get purge -y --auto-remove unzip && rm -rf /var/lib/apt/lists/*


# =============================================================================
# Target: API — FastAPI + Uvicorn (slim, no CLI tools)
# =============================================================================
# For: backend service (uvicorn). Runs the HTTP API server.
# Needs: helm, kubectl (for synchronous K8s/Helm API operations)
# Does NOT need: tofu, aws, docker, git (those are worker-only)
#
# OPT-001 (revised): COPY helm+kubectl from tooling-deps instead of downloading.
# tooling-deps extends base (not app-base), so it's code-change-independent.
# The first build downloads tools once; subsequent builds reuse the cached
# tooling-deps layer regardless of Python code changes.
#
FROM app-base AS api

# OPT-001: Copy helm + kubectl + oras from tooling-deps (code-change-independent cache)
# oras is needed here: GET /{id}/tags and POST /{id}/tags:pull run in uvicorn, not Celery.
COPY --from=tooling-deps /usr/local/bin/helm    /usr/local/bin/helm
COPY --from=tooling-deps /usr/local/bin/kubectl /usr/local/bin/kubectl
COPY --from=tooling-deps /usr/local/bin/oras    /usr/local/bin/oras

# Create helm cache/config dirs for bnkforge user
RUN mkdir -p /home/bnkforge/.cache/helm /home/bnkforge/.config/helm && \
    chown -R bnkforge:bnkforge /home/bnkforge/.cache /home/bnkforge/.config
EXPOSE 8000
USER bnkforge
# --proxy-headers: Trust X-Forwarded-* headers from reverse proxy
# --forwarded-allow-ips=*: Allow forwarded headers from any IP (proxy is trusted)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]


# =============================================================================
# Target: WORKER — Celery Worker (full tooling for infrastructure operations)
# =============================================================================
# For: celery-worker, celery-worker-2. Executes OpenTofu, Helm, K8s operations.
# Needs: tofu, helm, kubectl, aws, docker (from tooling-deps), git (from base), jq (apt)
# Size: ~700-900MB
#
# OPT-002: Extends app-base and COPYs binaries from tooling-deps so that Python
# code changes only rebuild app-base → worker layers, not the heavy tool downloads.
#
FROM app-base AS worker

# Copy CLI binaries from tooling-deps (git + openssh-client already in base)
COPY --from=tooling-deps /usr/local/bin/tofu       /usr/local/bin/tofu
COPY --from=tooling-deps /usr/local/bin/helm       /usr/local/bin/helm
COPY --from=tooling-deps /usr/local/bin/kubectl    /usr/local/bin/kubectl
COPY --from=tooling-deps /usr/local/bin/llmtop     /usr/local/bin/llmtop
COPY --from=tooling-deps /usr/local/bin/oras       /usr/local/bin/oras
# AWS CLI v2: copy the full install tree then recreate the symlinks.
# The installer places the runtime at /usr/local/aws-cli/v2/current/ and
# creates symlinks at /usr/local/bin/{aws,aws_completer}. COPY --from
# dereferences symlinks, which breaks the PyInstaller bundle's internal
# library resolution. Recreating the symlinks preserves the expected layout.
COPY --from=tooling-deps /usr/local/aws-cli/       /usr/local/aws-cli/
RUN ln -sf /usr/local/aws-cli/v2/current/bin/aws /usr/local/bin/aws && \
    ln -sf /usr/local/aws-cli/v2/current/bin/aws_completer /usr/local/bin/aws_completer
# Docker CLI: statically linked — safe to COPY without shared libs
COPY --from=tooling-deps /usr/bin/docker           /usr/bin/docker

# jq: install via apt (not COPY) because it needs shared libs (libjq.so.1, libonig.so)
RUN apt-get update && apt-get install -y --no-install-recommends jq && rm -rf /var/lib/apt/lists/*

# Restore bash as /bin/sh (tooling-deps sets this; base uses dash)
# IMPORTANT: Terraform local-exec provisioners rely on bash-compatible /bin/sh
RUN ln -sf /bin/bash /bin/sh

# Configure git — no credential.helper (creds are injected per-operation via GIT_ASKPASS)
RUN git config --global --add safe.directory '*'
ENV GIT_TERMINAL_PROMPT=0

# Create directories for bnkforge user
RUN mkdir -p /home/bnkforge/.cache/helm /home/bnkforge/.config/helm && \
    chown -R bnkforge:bnkforge /home/bnkforge

# Configure git for bnkforge user — no credential.helper (creds are injected per-operation via GIT_ASKPASS)
USER bnkforge
RUN git config --global --add safe.directory '*'
CMD ["celery", "-A", "celery_app", "worker", "--loglevel=info", "--concurrency=4", "--queues=default,opentofu,orchestrator,cli"]


# =============================================================================
# Target: BEAT — Celery Beat Scheduler (slim, no CLI tools)
# =============================================================================
# For: celery-beat. Just fires cron tasks into Redis queue, never runs CLI tools.
# Does NOT need: tofu, helm, kubectl, aws, docker, git
# Size: ~300-400MB
#
FROM app-base AS beat
USER bnkforge
CMD ["celery", "-A", "celery_app", "beat", "--loglevel=info"]


# =============================================================================
# Stage: TEST — Dev dependencies (pytest, ruff, mypy) for local test runs
# =============================================================================
# Source code is NOT baked in; mount the repo at /app at runtime so iterative
# edits don't trigger rebuilds. Used by `make test-backend-docker` etc.
# Size: ~400MB (base + dev deps).
#
FROM base AS test
COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements-dev.txt
WORKDIR /app
ENTRYPOINT []
CMD ["python", "-m", "pytest", "tests/", "--tb=short", "-q"]
