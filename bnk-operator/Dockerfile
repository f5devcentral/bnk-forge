# BNK Operator — lightweight K8s agent (~150 MB image)
# Connects outbound to BNK-Forge via WebSocket (wss://) or HTTP polling (https://).
# Supports TLS with custom CA certificates for self-signed BNK-Forge instances.


# =============================================================================
# Stage: TEST — Dev dependencies for `make test-operator-docker`
# =============================================================================
# Source code is NOT baked in; mount the repo at /app at runtime so iterative
# edits don't trigger rebuilds.
#
# python:3.12-slim — digest-pinned (Slice B / #300); re-pin when bumping the tag.
FROM python:3.12-slim@sha256:a39549e211a16149edf74e5fdc9ef03a6767e46cd987c5048b6659b6c9904c94 AS test
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
CMD ["python", "-m", "pytest", "tests/", "--tb=short", "-q"]


# =============================================================================
# Stage: RUNTIME (default) — Deployable operator image
# =============================================================================
# python:3.12-slim — digest-pinned (Slice B / #300); re-pin when bumping the tag.
FROM python:3.12-slim@sha256:a39549e211a16149edf74e5fdc9ef03a6767e46cd987c5048b6659b6c9904c94 AS runtime

# Helm version + per-arch SHA256s — matches backend/Dockerfile exactly.
# Slice E / #300: replaces `| bash` pipe with verified download.
# Re-pin when bumping HELM_VERSION.
ARG TARGETARCH=amd64
ENV HELM_VERSION=3.20.0
ENV HELM_SHA256_amd64=dbb4c8fc8e19d159d1a63dda8db655f9ffa4aac1b9a6b188b34a40957119b286
ENV HELM_SHA256_arm64=bfb14953295d5324d47ab55f3dfba6da28d46c848978c8fbf412d4271bdc29f1

# Install Helm CLI + ca-certificates for TLS.
# Downloads tarball, verifies SHA256, installs, then removes curl.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    curl --retry 3 --retry-delay 2 --retry-connrefused -fsSL \
      "https://get.helm.sh/helm-v${HELM_VERSION}-linux-${TARGETARCH}.tar.gz" -o helm.tar.gz && \
    EXPECTED=$(eval echo \$HELM_SHA256_${TARGETARCH}) && \
    echo "${EXPECTED}  helm.tar.gz" | sha256sum -c - && \
    tar -zxf helm.tar.gz "linux-${TARGETARCH}/helm" && \
    mv "linux-${TARGETARCH}/helm" /usr/local/bin/helm && \
    chmod +x /usr/local/bin/helm && \
    rm -rf helm.tar.gz "linux-${TARGETARCH}" && \
    apt-get remove -y curl && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -f operator && useradd -r -g operator -d /app -s /sbin/nologin operator

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy operator code
COPY *.py ./

# Switch to non-root user
USER operator

# Health server port
EXPOSE 8080

# Container-level health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')" || exit 1

# Entrypoint
CMD ["python", "-u", "main.py"]
