# =============================================================================
# Generic Full-Stack Dev Container — Python + Node.js
# =============================================================================
# A reusable devcontainer base for Python/FastAPI + React/Node projects.
# Includes:
#   - Python 3.11
#   - Node.js 20 (via NodeSource)
#   - Claude Code CLI (Anthropic's in-terminal coding agent)
#   - Common dev tooling (git, curl, jq, ssh, procps, sudo)
#   - Unprivileged `vscode` user with passwordless sudo
#
# Customize:
#   - Bump PYTHON_VERSION / NODE_MAJOR via build args below
#   - Add project-specific system packages to the `apt-get install` line
#   - Add extra global CLIs (e.g. Terraform, kubectl, helm) in the bottom section

ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

ARG NODE_MAJOR=20
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

# System dependencies — keep this list minimal; add project-specific packages
# to your own override if you need them (e.g. build-essential, libpq-dev).
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl \
      ca-certificates \
      git \
      openssh-client \
      gnupg \
      jq \
      procps \
      iproute2 \
      sudo \
      vim-tiny \
    && rm -rf /var/lib/apt/lists/*

# Node.js via NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI — run `claude` inside the container terminal for an AI pair
# that can read your repo, logs, and running services directly.
RUN npm install -g @anthropic-ai/claude-code

# ---------------------------------------------------------------------------
# OPTIONAL: add common devops CLIs here. Uncomment what you need.
# ---------------------------------------------------------------------------
# # kubectl
# RUN curl -fsSLo /usr/local/bin/kubectl \
#       "https://dl.k8s.io/release/$(curl -Ls https://dl.k8s.io/release/stable.txt)/bin/linux/$(dpkg --print-architecture)/kubectl" \
#     && chmod +x /usr/local/bin/kubectl
#
# # helm
# RUN curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
#
# # Terraform (via apt)
# RUN curl -fsSL https://apt.releases.hashicorp.com/gpg | gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg \
#     && echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" > /etc/apt/sources.list.d/hashicorp.list \
#     && apt-get update && apt-get install -y terraform && rm -rf /var/lib/apt/lists/*

# Create dev user (matches typical devcontainer UID mapping)
RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} -m ${USERNAME} -s /bin/bash \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

WORKDIR /workspace

# Keep container alive so VS Code can attach via docker-compose.
CMD ["sleep", "infinity"]
