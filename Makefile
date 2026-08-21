# === BNK Forge — Commands ===
# Usage: make deploy       — build + deploy (auto-detects Linux vs macOS networking)
#        make install      — first-time server setup (builds from scratch)
#        make update       — pull latest + rebuild + restart (keeps all data)
#        make test         — run all tests locally
#        make help         — see all commands
#
# Requires:
#   - Docker with BuildKit (default since Docker 23+)
#   - backend/.venv with dependencies installed (for local tests)
#   - frontend-v2/node_modules installed (for local tests)

SHELL := /bin/bash

# OPT-001: Ensure BuildKit is enabled for cache mount support
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1

# ─── Platform-aware compose command ──────────────────────────────────────────
# Auto-detects Linux vs macOS and picks the right networking:
#   Linux  → network_mode: host (services bind directly to host ports)
#   macOS  → bridge networking with port mappings (docker-compose.local.yml overlay)
#
# SAFETY: Running bridge networking on Linux creates iptables rules that can
# kill SSH connections permanently when net.bridge.bridge-nf-call-iptables = 1
# (the default on most distros). Requires BMC/IPMI reset to recover.
UNAME_S := $(shell uname -s)
# WSL2 reports "Linux" but runs Docker Desktop, which behaves like macOS for
# networking — host-mode containers bind inside the docker-desktop WSL2 distro,
# not the user's distro, so the local overlay (bridge + published ports) is
# what makes a Vite dev server reach the backend.
IS_WSL := $(shell grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null && echo 1)
ifeq ($(UNAME_S),Darwin)
  COMPOSE := docker compose -f docker-compose.yml -f docker-compose.local.yml
else ifeq ($(IS_WSL),1)
  COMPOSE := docker compose -f docker-compose.yml -f docker-compose.local.yml
else
  COMPOSE := docker compose
endif

# ─── CI / Local differentiation ─────────────────────────────────────────────
# GitHub Actions sets CI=true automatically. These variables control output
# format differences. The actual commands are identical in both contexts.

ifdef CI
  # CI: XML artifacts for GitHub Actions upload. SUITE is set per-target below.
  PYTEST_COV_REPORT  = --cov-report=xml:coverage-$(SUITE).xml
  PYTEST_JUNIT       = --junitxml=junit-$(SUITE).xml
  BACKEND_VENV       :=
  BACKEND_VENV_ROOT  :=
  OPERATOR_VENV      :=
  BACKEND_PREREQ     :=
  OPERATOR_PREREQ    :=
  FRONTEND_PREREQ    :=
else
  # Local: terminal output, venv activation
  PYTEST_COV_REPORT  := --cov-report=term-missing
  PYTEST_JUNIT       :=
  BACKEND_VENV       := source .venv/bin/activate &&
  BACKEND_VENV_ROOT  := source backend/.venv/bin/activate &&
  OPERATOR_VENV      := source .venv/bin/activate &&
  BACKEND_PREREQ     := | backend/.venv/bin/activate
  OPERATOR_PREREQ    := | bnk-operator/.venv/bin/activate
  FRONTEND_PREREQ    := | frontend-v2/node_modules
endif

PYTEST_BASE = python -m pytest
# --cov enables coverage; --cov-fail-under=0 overrides pyproject.toml's fail_under
# so individual suite runs don't fail on partial coverage. The full suite
# coverage threshold is enforced by `make coverage-backend`.
PYTEST_COV  = --cov --cov-fail-under=0

# ─── awsbnkctl CLI binary (cli-bnkctl engine) ────────────────────────────────
# The celery worker shells out to a pinned awsbnkctl release binary that is
# bind-mounted at ./bin/awsbnkctl (see docker-compose.yml x-worker-volumes).
# `make fetch-awsbnkctl` downloads + checksum-verifies the pinned linux/amd64
# release instead of requiring a hand-run `go build` on the host.
AWSBNKCTL_VERSION ?= 0.9.0-rc1
AWSBNKCTL_REPO    ?= JLCode-tech/awsbnkctl
AWSBNKCTL_BIN     := bin/awsbnkctl
AWSBNKCTL_STAMP   := bin/.awsbnkctl-$(AWSBNKCTL_VERSION).stamp

.PHONY: install update status logs \
        test test-backend test-backend-unit test-backend-component test-backend-legacy test-frontend \
        test-proxy test-operator test-db test-contracts test-e2e test-e2e-tier1 test-e2e-tier2 \
        test-integration test-integration-full build-frontend-check smoke-mcp-live mcp-readiness mcp-recreate \
        lint lint-backend lint-frontend shellcheck coverage quick-check pre-push push install-hooks setup-hooks \
        dev-setup security-audit docker-check docker-verify docker-validate \
        openapi openapi-types openapi-check openapi-types-check typecheck-backend typecheck-frontend \
        build build-retry build-backend build-frontend build-worker build-agent build-all \
        fetch-awsbnkctl \
        up down restart deploy deploy-backend deploy-frontend upgrade-safe \
        clean clean-docker check-disk setup-cleanup-cron check-migrations \
        test-upgrade dist push-images push-customer-build buildx-setup publish-signed help

# ─── Quick Start Commands ────────────────────────────────────────────────────
#
# These are the main entry points for users:
#   make install  — first-time setup (builds everything from scratch)
#   make update   — pull latest + rebuild + restart (keeps all data)
#   make status   — show container health and versions
#

# First-time installation: clean slate, build, configure, start
# This is the only entry point for new installations. No separate scripts.
install: _install-clean _install-override _install-build _install-start _install-info

# --- Install sub-targets (not meant to be called directly) ---

_install-clean:
	@echo ""
	@echo "========================================="
	@echo "  BNK Forge — First-Time Installation"
	@echo "========================================="
	@echo ""
	@echo "Stopping and removing existing containers..."
	@docker compose down 2>/dev/null || true
	@echo "Removing existing volumes..."
	@docker volume ls -q | grep bnk-forge | xargs docker volume rm 2>/dev/null || echo "  No volumes to remove"
	@echo "Removing existing images..."
	@docker images | grep bnk-forge | awk '{print $$1":"$$2}' | xargs docker rmi 2>/dev/null || echo "  No images to remove"

_install-override:
	@echo ""
	@echo "Configuring GUI Upgrade..."
	@printf '%s\n' \
		'# Auto-generated by make install — enables GUI Upgrade feature' \
		'# SEC-004: Docker socket grants full Docker daemon access (root-equivalent).' \
		'# Remove this file if you do not need the GUI "Upgrade Now" button.' \
		'' \
		'services:' \
		'  backend:' \
		'    environment:' \
		'      HOST_REPO_PATH: $(CURDIR)' \
		'    volumes:' \
		'      - /var/run/docker.sock:/var/run/docker.sock' \
		> docker-compose.override.yml
	@echo "  HOST_REPO_PATH=$(CURDIR)"
	@echo "  ✓ Created docker-compose.override.yml"

_install-build:
	@echo ""
	@echo "Building all images (first build: ~3 min, cached: ~30s)..."
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose build

_install-start: ensure-artifact-network
	@echo ""
	@echo "Starting infrastructure (postgres, redis)..."
	@docker compose up -d postgres redis
	@echo "  Waiting for database..."
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do \
	  if docker compose exec -T postgres pg_isready -U bnkforge > /dev/null 2>&1; then \
	    echo "  ✓ Database ready"; \
	    break; \
	  fi; \
	  sleep 1; \
	done
	@echo ""
	@echo "Fixing volume permissions..."
	@PROJECT=$$(basename "$(CURDIR)"); \
	for v in bnk-forge-data bnk-forge-keys bnk-forge-agent-token state_data helm_cache helm_config helm_charts workspace_data; do \
	  docker volume create \
	    --label com.docker.compose.project=$${PROJECT} \
	    --label com.docker.compose.volume=$${v} \
	    "$${PROJECT}_$${v}" >/dev/null; \
	done; \
	docker run --rm \
	  -v "$${PROJECT}_bnk-forge-data:/app/projects" \
	  -v "$${PROJECT}_bnk-forge-keys:/app/keys" \
	  -v "$${PROJECT}_bnk-forge-agent-token:/app/agent-token" \
	  -v "$${PROJECT}_state_data:/app/state" \
	  -v "$${PROJECT}_helm_cache:/home/bnkforge/.cache/helm" \
	  -v "$${PROJECT}_helm_config:/home/bnkforge/.config/helm" \
	  -v "$${PROJECT}_helm_charts:/app/helm_charts" \
	  -v "$${PROJECT}_workspace_data:/app/workspaces" \
	  alpine:latest sh -c " \
	    mkdir -p /app/projects /app/keys /app/agent-token /app/state /app/helm_charts /app/workspaces \
	      /home/bnkforge/.cache/helm /home/bnkforge/.config/helm && \
	    chown -R 1000:1000 /app/projects /app/keys /app/agent-token /app/state /app/helm_charts \
	      /app/workspaces /home/bnkforge" \
	  2>/dev/null && echo "  ✓ Volume permissions configured" \
	  || echo "  ⚠  Could not pre-configure permissions"
	@echo ""
	@echo "Starting all services..."
	@docker compose up -d
	@echo ""
	$(call wait-healthy,12,5)

_install-info:
	@echo ""
	@docker compose ps
	@echo ""
	@HOST_IP=$$(ip route get 1 2>/dev/null | awk '{print $$7; exit}' \
	  || hostname -I 2>/dev/null | awk '{print $$1}' \
	  || echo "localhost"); \
	if [ -z "$$HOST_IP" ] || [ "$$HOST_IP" = "127.0.0.1" ]; then HOST_IP="localhost"; fi; \
	echo "========================================="; \
	echo "  ✅ Installation complete!"; \
	echo ""; \
	echo "  Version: $$(cat VERSION 2>/dev/null || echo 'unknown')"; \
	echo ""; \
	if [ "$$HOST_IP" = "localhost" ]; then \
	  echo "  Open: https://localhost:8443"; \
	else \
	  echo "  Open: https://$$HOST_IP:8443"; \
	  echo "        (accept the self-signed certificate warning)"; \
	fi; \
	echo ""; \
	echo "  Login: admin  (initial password: DEFAULT_ADMIN_PASSWORD, default 'changeme' — change on first login)"; \
	echo ""; \
	echo "  Next steps:"; \
	echo "    1. Change your password on first login"; \
	echo "    2. Browse deployable blueprints in Build → Catalog"; \
	echo "    3. Create your first project in Build → Projects"; \
	echo "========================================="

# Pull latest code, rebuild, and restart (non-destructive — keeps all data)
update:
	@echo ""
	@echo "========================================="
	@echo "  BNK Forge — Update"
	@echo "========================================="
	@echo ""
	@./upgrade.sh

# Show container health, versions, and service status
status:
	@echo ""
	@echo "========================================="
	@echo "  BNK Forge — Status"
	@echo "========================================="
	@echo ""
	@echo "Version: $$(cat VERSION 2>/dev/null || echo 'unknown')"
	@echo "Commit:  $$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
	@echo ""
	@$(COMPOSE) ps 2>/dev/null || echo "Docker Compose not running"
	@echo ""
	@echo "Health check:"
	@curl -sf http://localhost:8000/api/system/health 2>/dev/null | python3 -m json.tool 2>/dev/null \
		|| curl -sf https://localhost:8443/api/system/health -k 2>/dev/null | python3 -m json.tool 2>/dev/null \
		|| echo "  Backend not reachable (containers may not be running)"

# ─── Docker Build (OPT-001: cached builds) ──────────────────────────────────
#
# IMPORTANT: Do NOT use --no-cache for normal code changes!
# BuildKit caches pip/npm downloads across builds. Layer caching handles code.
# Only use `make build-clean` when requirements.txt or tool versions change.
#

# Build all app images — backend API, workers, beat, frontend
# OPT-002: Single docker compose build parallelizes independent stages via BuildKit
build:
	@echo ""
	@echo "=== Building all app images (parallel) ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose build backend celery-worker celery-beat frontend forge-agent
	@echo ""
	@echo "========================================="
	@echo "  Build complete (cached)"
	@echo "========================================="

# Same as `build`, but retries on transient download failures. Use this for
# from-scratch builds on DLP-managed workstations: the github.com tool
# downloads use Docker `ADD` (see docs/adr/D-035), which has no built-in retry,
# so a transient CDN/TLS blip aborts the whole build. BuildKit's layer cache
# makes each retry cheap (only the failed layer re-runs). Tune with
# RETRY_ATTEMPTS / RETRY_DELAY, e.g. `make build-retry RETRY_ATTEMPTS=5`.
build-retry:
	@echo ""
	@echo "=== Building all app images (parallel, with retry) ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 ./scripts/retry.sh -- docker compose build backend celery-worker celery-beat frontend forge-agent
	@echo ""
	@echo "========================================="
	@echo "  Build complete (cached)"
	@echo "========================================="

# Build just the API image (backend code changes)
build-backend:
	@echo ""
	@echo "=== Building backend (API) ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 $(COMPOSE) build backend
	@echo "  ✓ Backend image built"

# Build just the frontend image
build-frontend:
	@echo ""
	@echo "=== Building frontend ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 $(COMPOSE) build frontend
	@echo "  ✓ Frontend image built"

# Fetch the pinned awsbnkctl release binary (linux/amd64) for the worker mount.
# Idempotent: a version stamp file short-circuits re-download. Override the
# version with `make fetch-awsbnkctl AWSBNKCTL_VERSION=x.y.z`.
fetch-awsbnkctl: $(AWSBNKCTL_STAMP)

$(AWSBNKCTL_STAMP):
	@echo ""
	@echo "=== Fetching awsbnkctl $(AWSBNKCTL_VERSION) (linux/amd64) ==="
	@command -v gh >/dev/null 2>&1 || { echo "  ✗ GitHub CLI (gh) is required to download the release"; exit 1; }
	@mkdir -p bin
	@tmp="$$(mktemp -d)"; \
	asset="awsbnkctl_$(AWSBNKCTL_VERSION)_linux_amd64.tar.gz"; \
	echo "  Downloading $$asset + checksums.txt"; \
	gh release download "v$(AWSBNKCTL_VERSION)" --repo "$(AWSBNKCTL_REPO)" \
	  --pattern "$$asset" --pattern "checksums.txt" --dir "$$tmp" --clobber || { rm -rf "$$tmp"; exit 1; }; \
	echo "  Verifying checksum"; \
	( cd "$$tmp" && grep " $$asset$$" checksums.txt | shasum -a 256 -c - ) || { echo "  ✗ checksum mismatch"; rm -rf "$$tmp"; exit 1; }; \
	echo "  Extracting awsbnkctl -> $(AWSBNKCTL_BIN)"; \
	tar -xzf "$$tmp/$$asset" -C "$$tmp" awsbnkctl || { rm -rf "$$tmp"; exit 1; }; \
	install -m 0755 "$$tmp/awsbnkctl" "$(AWSBNKCTL_BIN)"; \
	rm -rf "$$tmp"
	@rm -f bin/.awsbnkctl-*.stamp
	@touch "$(AWSBNKCTL_STAMP)"
	@echo "  ✓ awsbnkctl $(AWSBNKCTL_VERSION) ready at $(AWSBNKCTL_BIN)"

# Build worker image (REQUIRED for any backend code change — workers run tasks/services)
# fetch-awsbnkctl is opt-in, not a hard prerequisite: it needs `gh` authenticated against
# AWSBNKCTL_REPO (a personal namespace today), so it must not block backend builds/deploys
# for contributors not touching the cli-bnkctl engine. We still attempt it automatically
# (non-fatal) so it "just works" for anyone who does have access; on failure we warn and
# continue the worker build without the binary — the cli-bnkctl engine will fail-soft at
# deploy time (see cli_bnkctl_module_seeder binary-presence gate) rather than blocking here.
build-worker:
	@$(MAKE) fetch-awsbnkctl || echo "  ⚠ Skipping awsbnkctl fetch ($(AWSBNKCTL_REPO) unavailable — install/auth 'gh' to enable the cli-bnkctl engine). Run 'make fetch-awsbnkctl' manually once you have access."
	@echo ""
	@echo "=== Building worker (includes CLI tools — slower) ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose build celery-worker
	@echo "  ✓ Worker image built"

# Build the built-in benchmark agent image
build-agent:
	@echo ""
	@echo "=== Building forge-agent (built-in benchmark agent) ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose build forge-agent
	@echo "  ✓ forge-agent image built"

# Build MCP server image
build-mcp:
	@echo ""
	@echo "=== Building MCP server ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose build mcp
	@echo "  ✓ MCP server image built"

# Build ALL images (backend API + worker + beat + frontend + proxy + mcp + agent)
build-all:
	@echo ""
	@echo "=== Building ALL images ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose build
	@echo "  ✓ All images built"

# Force rebuild without cache (only for requirements.txt / tool version changes)
build-clean:
	@echo ""
	@echo "=== Clean rebuild (no cache) — this will take several minutes ==="
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose build --no-cache
	@echo "  ✓ Clean rebuild complete"

# ─── Docker Deploy (build + restart + verify) ───────────────────────────────
#
# All deploy/runtime targets use $(COMPOSE) which auto-detects:
#   Linux  → host networking (docker compose)
#   macOS  → bridge networking (docker compose -f ... -f docker-compose.local.yml)

# Reusable health-check: polls backend /api/system/health.
# Usage: $(call wait-healthy,ATTEMPTS,SLEEP_SECONDS)
#   e.g. $(call wait-healthy,12,5)  → 12 attempts, 5s apart (60s max)
define wait-healthy
	@echo "=== Waiting for health checks... ==="
	@sleep $(2)
	@for i in $$(seq 1 $(1)); do \
	  if curl -sf http://localhost:8000/api/system/health > /dev/null 2>&1; then \
	    echo "  ✓ Backend healthy"; \
	    break; \
	  fi; \
	  if [ "$$i" = "$(1)" ]; then \
	    echo "  ⚠  Backend did not become healthy within $$(( $(1) * $(2) ))s"; \
	    echo "  Check logs: docker logs bnk-forge-backend"; \
	  else \
	    echo "  Waiting... ($$i/$(1))"; \
	  fi; \
	  sleep $(2); \
	done
endef

# Deploy everything: build, restart, verify health
# NOTE: No explicit service list — starts ALL services in the compose file.
# On macOS the proxy's nginx.local.conf references upstreams by Docker DNS name
# (e.g. "mcp"), and nginx crashes at startup if the upstream container doesn't
# exist on the network. Starting everything avoids this.
# The dedicated bridge network the container-image engine attaches artifact
# steps to. It must be created explicitly: no bnk-forge service joins it (they
# can't — under host networking a service cannot also join a bridge network),
# and `docker compose up` does NOT create a declared network that no service
# references. Without this, every artifact step dies with "network not found".
ARTIFACT_NETWORK ?= bnk-forge-artifacts
# Docker's auto-assigned pool (172.17.0.0/16+, typically landing on
# 172.18.0.0/16) can collide with a host's VPN/management routes, cutting off
# connectivity mid-deploy. No single hardcoded subnet is safe everywhere (a
# fixed 10.200.0.0/24 default also collided on a field site), so the subnet
# is resolved by scripts/artifact_network.sh: explicit ARTIFACT_NETWORK_SUBNET
# (or "auto" to defer to Docker's default-address-pools) wins; otherwise it
# auto-detects a free subnet from ARTIFACT_NETWORK_SUBNET_CANDIDATES against
# the host's routes and existing docker networks. Both vars may also be set
# in a .env file in the repo root. See issue #422.
ARTIFACT_NETWORK_SUBNET ?=
ARTIFACT_NETWORK_SUBNET_CANDIDATES ?=

ensure-artifact-network:
	@export ARTIFACT_NETWORK="$(ARTIFACT_NETWORK)"; \
	if [ -n "$(ARTIFACT_NETWORK_SUBNET)" ]; then export ARTIFACT_NETWORK_SUBNET="$(ARTIFACT_NETWORK_SUBNET)"; fi; \
	if [ -n "$(ARTIFACT_NETWORK_SUBNET_CANDIDATES)" ]; then export ARTIFACT_NETWORK_SUBNET_CANDIDATES="$(ARTIFACT_NETWORK_SUBNET_CANDIDATES)"; fi; \
	bash scripts/artifact_network.sh ensure

deploy: build ensure-artifact-network
	@echo ""
	@echo "=== Restarting containers ==="
	$(COMPOSE) up -d --force-recreate
	@echo ""
	$(call wait-healthy,12,5)
	@echo ""
	@$(COMPOSE) ps
	@echo ""
	date
	@echo ""
	@echo "========================================="
	@echo "  Deploy complete"
	@echo ""
ifeq ($(UNAME_S),Darwin)
	@echo "  Open:  https://localhost"
	@echo "  Login: admin  (initial password: DEFAULT_ADMIN_PASSWORD, default 'changeme'; change on first login)"
endif
	@echo "  Recommended next step: make mcp-readiness"
	@echo "========================================="

# Deploy just backend (API + workers + beat) — most common for backend code changes
deploy-backend: build-backend build-worker ensure-artifact-network
	@echo ""
	@echo "=== Restarting backend containers ==="
	$(COMPOSE) up -d --force-recreate backend celery-worker celery-worker-2 celery-beat
	@echo ""
	date
	@echo ""
	$(call wait-healthy,6,5)

# Deploy just frontend
deploy-frontend: build-frontend
	@echo ""
	@echo "=== Restarting frontend ==="
	$(COMPOSE) up -d --force-recreate frontend

# Run strict safe upgrade flow (preflight + upgrade + verification)
upgrade-safe:
	@echo ""
	@echo "=== Running safe upgrade flow (upgrade.sh --local) ==="
	@echo "  Includes git/disk preflight and strict verification gates."
	@./upgrade.sh --local

# Test upgrade policy guards (runs in isolated temp repo)
test-upgrade:
	@echo ""
	@echo "=== Testing upgrade.sh policy guards ==="
	@./scripts/test-upgrade-policy.sh

# ShellCheck lint for all shell scripts
shellcheck:
	@echo ""
	@echo "=== ShellCheck: linting shell scripts ==="
	@# bonnyr-f5 #182: drive from git ls-files so the WHOLE corpus is gated
	@# (the hardcoded globs missed 14 tracked scripts incl. dist/install.sh).
	@# bonnyr-f5 #182 r2: include the (extensionless) git hooks, and fail on an
	@# EMPTY list -- `xargs shellcheck` with no files exits 0 on BSD (blind).
	@files="$$(git ls-files '*.sh' .githooks/pre-commit .githooks/pre-push 2>/dev/null)"; \
	  n=$$(printf '%s\n' "$$files" | grep -c .); \
	  [ "$$n" -ge 1 ] || { echo "::error::shellcheck found no files to lint"; exit 1; }; \
	  printf '%s\n' "$$files" | xargs shellcheck --severity=warning

# ── CI-parity gates (bonnyr-f5 #182 r3, Major) ──────────────────────────────
# The four gates ci.yml added were not runnable locally: `make pre-push` ran
# none of them and `make shellcheck` had no dependents, yet ci.yml's header
# claims `make pre-push` == CI. #166: "a local gate that does not run the CI
# command is not a gate." These targets ARE the CI command (ci.yml calls the
# same `make` targets / same scripts), and `pre-push` now depends on `ci-gates`.
.PHONY: ci-gates version-check secret-scan commit-lint script-selftests

# Helm chart tag/appVersion + frontend package.json must equal VERSION.
version-check:
	@echo ""
	@echo "=== Version artifacts consistency (sync-version-artifacts.sh --check) ==="
	@bash scripts/sync-version-artifacts.sh --check

# gitleaks range-aware secret scan + assertion backstop (single source of truth,
# shared with ci.yml's secret-scan job and the scheduled baseline workflow).
# Honours RANGE from the environment; unset => scan since the upstream merge-base.
secret-scan:
	@echo ""
	@echo "=== Secret scan (gitleaks) ==="
	@bash scripts/secret-scan.sh

# Commit-message marker enforcement (shared with ci.yml's commit-lint job and
# .githooks/pre-push). Honours RANGE; unset => @{upstream}..HEAD.
commit-lint:
	@echo ""
	@echo "=== Commit message marker lint ==="
	@bash scripts/lint-commit-markers.sh

# The paired self-test harnesses ci.yml's script-selftests job runs.
# ci.yml's compute step has FOUR anti-vacuity assertions and this target must
# mirror ALL of them, or a broken harness passes locally while CI goes red
# (bonnyr-f5 #182 r4/r5, Major-3: a local gate that diverges from the CI command
# is not a gate). The four (in ci.yml order):
#   1. non-zero exit               -> the harness itself errored
#   2. a "FAIL:" line (rc still 0) -> an assertion failed but exit was swallowed
#   3. NO "PASS:" line             -> the guard was silenced / renamed: green with
#                                     zero assertions actually run
#   4. NO "=== END SELF-TEST ==="  -> the harness exited early (deleted END marker
#                                     or an early `exit 0`) with assertions unrun
# r5 landed 3+4 here; r4 had only 1+2, so the "silenced guard" and "early exit"
# harness-break modes were CI-red but `make`-GREEN.
script-selftests:
	@echo ""
	@echo "=== Script self-tests ==="
	@set +e; out="$$(SELF_TEST=1 bash scripts/compute_version_bump.sh 2>&1)"; rc=$$?; \
	  echo "$$out"; \
	  if [ "$$rc" -ne 0 ]; then echo "::error::compute_version_bump self-test exited $$rc"; exit "$$rc"; fi; \
	  if printf '%s\n' "$$out" | grep -qE '(^|[[:space:]])FAIL:'; then \
	    echo "::error::compute_version_bump self-test reported FAIL: but exited 0"; exit 1; \
	  fi; \
	  if ! printf '%s\n' "$$out" | grep -qE '(^|[[:space:]])PASS:'; then \
	    echo "::error::self-test produced no PASS lines -- the harness did not run"; exit 1; \
	  fi; \
	  if ! printf '%s\n' "$$out" | grep -qE '=== END SELF-TEST ==='; then \
	    echo "::error::self-test did not reach its END marker -- it exited early with assertions unrun"; exit 1; \
	  fi
	@if grep -q -- '--self-test' scripts/extract-breaking-changes.sh; then \
	  bash scripts/extract-breaking-changes.sh --self-test; \
	else \
	  echo "  (extract-breaking-changes.sh has no --self-test yet; it lands with #179)"; \
	fi

# Aggregate: every CI gate that is not already covered by quick-check/tests.
ci-gates: shellcheck version-check commit-lint script-selftests secret-scan
	@echo ""
	@echo "=== CI-parity gates passed ==="

# Convenience: start/stop/restart all (platform-aware)
up: ensure-artifact-network
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) restart

logs:
	$(COMPOSE) logs -f --tail 50

# ─── Default: run all tests ─────────────────────────────────────────────────

test: lint test-backend test-frontend test-proxy test-operator test-db
	@echo ""
	@echo "========================================="
	@echo "  All tests passed"
	@echo "========================================="

# ─── Individual test suites ──────────────────────────────────────────────────

test-backend: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Backend Tests ==="
	@cd backend && $(BACKEND_VENV) \
	  python -m pytest tests/ --tb=short -q

test-frontend: $(FRONTEND_PREREQ)
	@echo ""
	@echo "=== Frontend Tests ==="
	@cd frontend-v2 && npm test -- --run

test-proxy: SUITE = proxy
test-proxy: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Proxy Config Tests ==="
	@cd backend && $(BACKEND_VENV) \
	  $(PYTEST_BASE) tests/test_proxy_config.py --tb=short -q $(PYTEST_COV) $(PYTEST_COV_REPORT) $(PYTEST_JUNIT)

test-operator: $(OPERATOR_PREREQ)
	@echo ""
	@echo "=== Operator Tests ==="
	@cd bnk-operator && $(OPERATOR_VENV) \
	  python -m pytest tests/ --tb=short -q

test-mcp:
	@echo ""
	@echo "=== MCP Server Tests ==="
	@cd mcp-server && \
	  if [ -d ".venv" ]; then source .venv/bin/activate; fi && \
	  python -m pytest tests/ --tb=short -q

smoke-mcp-live:
	@echo ""
	@echo "=== MCP Live Smoke Validation ==="
	@echo "  NOTE: ping/tools-list reachability != runtime readiness; tool calls require valid MCP backend credentials."
	@echo "  Configure MCP_USERNAME/MCP_PASSWORD if backend admin password was rotated."
	@python3 scripts/mcp_live_smoke.py --mcp-url "$${MCP_SMOKE_URL:-http://localhost:8081/mcp}" $${MCP_SMOKE_INSECURE_TLS:+--insecure-tls}

mcp-readiness:
	@echo ""
	@echo "=== MCP Readiness Check (Liveness + Runtime) ==="
	@echo "  Layer 1: container liveness (protocol ping healthcheck)"
	@state=$$(docker inspect -f '{{.State.Status}}' bnk-forge-mcp 2>/dev/null) || (echo "  ERROR: bnk-forge-mcp container not found. Start stack first (make deploy)." && exit 1); \
	health=$$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' bnk-forge-mcp 2>/dev/null); \
	echo "  bnk-forge-mcp: state=$$state health=$$health"; \
	if [ "$$state" != "running" ]; then \
		echo "  ERROR: MCP container is not running."; \
		exit 1; \
	fi; \
	if [ "$$health" != "none" ] && [ "$$health" != "healthy" ]; then \
		echo "  ERROR: MCP protocol liveness healthcheck is not healthy."; \
		exit 1; \
	fi
	@echo "  Layer 2: runtime readiness (governed tool calls via smoke-mcp-live)"
	@$(MAKE) --no-print-directory smoke-mcp-live

mcp-recreate:
	@echo ""
	@echo "=== Recreate MCP service ==="
	@echo "  Use after MCP_USERNAME/MCP_PASSWORD changes so MCP picks up new credentials."
	@$(COMPOSE) up -d --force-recreate --no-deps mcp
	@$(COMPOSE) ps mcp

test-db: SUITE = db-migration
test-db: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== DB Migration Tests ==="
	@cd backend && $(BACKEND_VENV) \
	  $(PYTEST_BASE) tests/test_migrations.py --tb=short -q $(PYTEST_COV) $(PYTEST_COV_REPORT) $(PYTEST_JUNIT)

test-backend-unit: SUITE = unit
test-backend-unit: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Backend Unit Tests ==="
	@cd backend && $(BACKEND_VENV) \
	  $(PYTEST_BASE) tests/unit/ --tb=short -q $(PYTEST_COV) $(PYTEST_COV_REPORT) $(PYTEST_JUNIT)

test-backend-component: SUITE = component
test-backend-component: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Backend Component Tests ==="
	@cd backend && $(BACKEND_VENV) \
	  $(PYTEST_BASE) tests/component/ --tb=short -q $(PYTEST_COV) $(PYTEST_COV_REPORT) $(PYTEST_JUNIT)

test-contracts: SUITE = contract
test-contracts: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Golden Contract Tests ==="
	@cd backend && $(BACKEND_VENV) \
	  $(PYTEST_BASE) tests/contract/ -v --tb=short $(PYTEST_COV) $(PYTEST_COV_REPORT) $(PYTEST_JUNIT)

# Exact complement of test-integration-full: together they cover every test in
# tests/integration/. The selector is spelled out rather than inherited from
# pyproject's addopts (-m 'not full') on purpose -- that implicit coupling is
# what let the two targets stop being complements without anyone noticing
# (#130). Change one selector, change the other.
test-integration: SUITE = integration
test-integration: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Integration Tests (non-full marker set) ==="
	@cd backend && $(BACKEND_VENV) \
	  $(PYTEST_BASE) tests/integration/ -m 'not full' --tb=short -q $(PYTEST_COV) $(PYTEST_COV_REPORT) $(PYTEST_JUNIT)

# SUITE is integration-full, not integration: the artifact filenames are derived
# from it, and CI now runs both targets in one job.
test-integration-full: SUITE = integration-full
test-integration-full: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Full-Mode Integration Tests (requires running Docker stack) ==="
	@cd backend && $(BACKEND_VENV) \
	  $(PYTEST_BASE) tests/integration/ -m full --tb=short -q $(PYTEST_COV) $(PYTEST_COV_REPORT) $(PYTEST_JUNIT)

test-e2e: test-e2e-tier1

test-e2e-tier1:
	@echo ""
	@echo "=== E2E Tier 1 Tests (requires running stack, ~5 min) ==="
	@cd tests/e2e && npx playwright test

test-e2e-tier2:
	@echo ""
	@echo "=== E2E Tier 2 Tests (requires running stack + AWS creds, ~30-60 min) ==="
	@cd tests/e2e && E2E_TIER=2 npx playwright test tests/tier2/

# ─── Linting ─────────────────────────────────────────────────────────────────

lint: lint-backend lint-frontend
	@echo ""
	@echo "=== All linting passed ==="

lint-backend: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Backend Lint (ruff) ==="
	@cd backend && $(BACKEND_VENV) \
	  python -m ruff check . --config pyproject.toml

lint-frontend: $(FRONTEND_PREREQ)
	@echo ""
	@echo "=== Frontend Lint (eslint) ==="
	@cd frontend-v2 && set -o pipefail && npm run lint 2>&1 | tail -30

# ─── Coverage ────────────────────────────────────────────────────────────────

coverage: coverage-backend coverage-frontend
	@echo ""
	@echo "=== Coverage reports complete ==="

coverage-backend: | backend/.venv/bin/activate
	@echo ""
	@echo "=== Backend Coverage ==="
	@cd backend && source .venv/bin/activate && \
	  python -m pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=40 --tb=short -q

coverage-frontend: | frontend-v2/node_modules
	@echo ""
	@echo "=== Frontend Coverage ==="
	@cd frontend-v2 && npm run test:coverage

# ─── Pre-Push & Push (local checks → push → verify CI) ──────────────────────

typecheck-backend: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Backend Type Check (mypy core/ schemas/) ==="
	@cd backend && $(BACKEND_VENV) \
	  python -m mypy core/ schemas/ --config-file pyproject.toml

typecheck-frontend: $(FRONTEND_PREREQ)
	@echo ""
	@echo "=== TypeScript Type Check (tsc --noEmit) ==="
	@cd frontend-v2 && npx tsc --noEmit

test-backend-legacy: SUITE = legacy
test-backend-legacy: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Backend Legacy Tests (flat test_*.py files) ==="
	@cd backend && $(BACKEND_VENV) \
	  $(PYTEST_BASE) tests/ \
	    --ignore=tests/unit \
	    --ignore=tests/component \
	    --ignore=tests/integration \
	    --ignore=tests/contract \
	    --ignore=tests/migrations \
	    --tb=short -q $(PYTEST_COV) $(PYTEST_COV_REPORT) $(PYTEST_JUNIT)

build-frontend-check: $(FRONTEND_PREREQ)
	@echo ""
	@echo "=== Frontend Build Check (npm run build) ==="
	@cd frontend-v2 && npm run build > /dev/null 2>&1 && echo "  Build succeeded ✓"

# ── Migration chain validator ────────────────────────────────────────────────
# Pure AST parsing — no DB, no venv, no alembic import. Runs in <1s.
check-migrations:
	@echo "=== Migration Chain Validator ==="
	@python3 scripts/check-migrations.py

# ── Quick check (~15s): lint + types + contracts ────────────────────────────
# Run before every commit. Catches most CI failures instantly.
quick-check: lint typecheck-backend openapi-types-check check-migrations
	@echo ""
	@echo "========================================="
	@echo "  Quick check passed (~15s)"
	@echo "========================================="

# ── Pre-push (~90s parallel): mirrors ALL CI jobs ───────────────────────────
# Run once before git push. Runs test suites in parallel for speed.
# Prerequisite: quick-check runs first (sequential), then the CI-parity gates
# (shellcheck / version-check / commit-lint / script-selftests / secret-scan --
# bonnyr-f5 #182 r3, so `make pre-push` genuinely == CI), then tests fan out.
pre-push: quick-check ci-gates
	@echo ""
	@echo "=== Running all test suites in parallel... ==="
	@failed=""; \
	( set -o pipefail; make test-backend-unit 2>&1 | tail -3 ) & pid1=$$!; \
	( set -o pipefail; make test-backend-component 2>&1 | tail -3 ) & pid2=$$!; \
	( set -o pipefail; make test-backend-legacy 2>&1 | tail -3 ) & pid3=$$!; \
	( set -o pipefail; make test-frontend 2>&1 | tail -3 ) & pid4=$$!; \
	( set -o pipefail; make test-operator 2>&1 | tail -3 ) & pid5=$$!; \
	( set -o pipefail; make test-proxy 2>&1 | tail -3 ) & pid6=$$!; \
	( set -o pipefail; make test-db 2>&1 | tail -3 ) & pid7=$$!; \
	( set -o pipefail; make typecheck-frontend 2>&1 | tail -3 ) & pid8=$$!; \
	( set -o pipefail; make test-contracts 2>&1 | tail -3 ) & pid9=$$!; \
	wait $$pid1 || failed="$$failed backend-unit"; \
	wait $$pid2 || failed="$$failed backend-component"; \
	wait $$pid3 || failed="$$failed backend-legacy"; \
	wait $$pid4 || failed="$$failed frontend"; \
	wait $$pid5 || failed="$$failed operator"; \
	wait $$pid6 || failed="$$failed proxy"; \
	wait $$pid7 || failed="$$failed db-migrations"; \
	wait $$pid8 || failed="$$failed tsc"; \
	wait $$pid9 || failed="$$failed contracts"; \
	if [ -n "$$failed" ]; then \
	  echo ""; \
	  echo "========================================="; \
	  echo "  FAILED:$$failed"; \
	  echo "========================================="; \
	  exit 1; \
	fi
	@echo ""
	@echo "========================================="
	@echo "  Pre-push checks passed — safe to push"
	@echo "========================================="

push: pre-push
	@echo ""
	@echo "=== Pushing to origin ==="
	@git push
	@echo ""
	@echo "=== Waiting for CI to start... ==="
	@sleep 10
	@echo "=== Watching CI (this may take a few minutes)... ==="
	@gh run watch --exit-status && \
	  echo "" && \
	  echo "=========================================" && \
	  echo "  CI GREEN — all checks passed" && \
	  echo "=========================================" || \
	  (echo "" && \
	   echo "=========================================" && \
	   echo "  CI RED — check failures with: gh run view" && \
	   echo "=========================================" && \
	   exit 1)

# ─── Containerized Tests (no local venv / Node needed) ──────────────────────
# Run tests and linting inside Docker images — matches CI Python 3.11 / Node 20
# exactly, sidesteps local venv drift (e.g. Python 3.14 on macOS).
#
# Usage:
#   make test-docker              — run all containerized tests
#   make test-backend-docker      — backend pytest in python:3.11 container
#   make test-frontend-docker     — frontend vitest in node:20 container
#   make test-operator-docker     — operator pytest in python:3.12 container
#   make lint-backend-docker      — ruff in container
#
# Source is bind-mounted so edits take effect immediately (no rebuild).

BACKEND_TEST_IMAGE  := bnk-forge-backend-test
OPERATOR_TEST_IMAGE := bnk-forge-operator-test
FRONTEND_RUN_IMAGE  := node:20-slim
DOCKER_RUN_FLAGS    := --rm -v $(CURDIR):/repo -w /repo

.PHONY: test-docker test-backend-docker test-frontend-docker test-operator-docker \
        lint-backend-docker build-test-images

test-docker: lint-backend-docker test-backend-docker test-frontend-docker test-operator-docker
	@echo ""
	@echo "========================================="
	@echo "  All containerized tests passed"
	@echo "========================================="

build-test-images: .stamp/backend-test-image .stamp/operator-test-image

# VERSION is a prerequisite because the image now COPYs it — without this a
# version bump leaves a stale test image reporting the old number.
.stamp/backend-test-image: backend/Dockerfile backend/requirements.txt backend/requirements-dev.txt VERSION
	@mkdir -p .stamp
	@echo ""
	@echo "=== Building backend test image ($(BACKEND_TEST_IMAGE)) ==="
	docker build --target test -f backend/Dockerfile -t $(BACKEND_TEST_IMAGE) .
	@touch $@

.stamp/operator-test-image: bnk-operator/Dockerfile bnk-operator/requirements.txt bnk-operator/requirements-dev.txt
	@mkdir -p .stamp
	@echo ""
	@echo "=== Building operator test image ($(OPERATOR_TEST_IMAGE)) ==="
	docker build --target test -t $(OPERATOR_TEST_IMAGE) bnk-operator/
	@touch $@

test-backend-docker: .stamp/backend-test-image
	@echo ""
	@echo "=== Backend Tests (docker) ==="
	docker run $(DOCKER_RUN_FLAGS) -w /repo/backend $(BACKEND_TEST_IMAGE) \
	  python -m pytest tests/ --tb=short -q

lint-backend-docker: .stamp/backend-test-image
	@echo ""
	@echo "=== Backend Lint (ruff, docker) ==="
	docker run $(DOCKER_RUN_FLAGS) -w /repo/backend $(BACKEND_TEST_IMAGE) \
	  python -m ruff check . --config pyproject.toml

test-operator-docker: .stamp/operator-test-image
	@echo ""
	@echo "=== Operator Tests (docker) ==="
	docker run $(DOCKER_RUN_FLAGS) -w /repo/bnk-operator $(OPERATOR_TEST_IMAGE) \
	  python -m pytest tests/ --tb=short -q

# Frontend: use stock node image + named volume for node_modules cache.
# First run does npm ci (~30s), subsequent runs reuse the volume (~0s).
test-frontend-docker:
	@echo ""
	@echo "=== Frontend Tests (docker) ==="
	docker run $(DOCKER_RUN_FLAGS) \
	  -v bnk-forge-fe-node_modules:/repo/frontend-v2/node_modules \
	  -w /repo/frontend-v2 $(FRONTEND_RUN_IMAGE) \
	  sh -c '[ -f node_modules/.package-lock.json ] || npm ci; npm test -- --run'

# ─── Dev Environment Setup ───────────────────────────────────────────────────
# Creates local Python virtualenvs and installs frontend deps needed by the
# lint/test targets. File targets are idempotent — re-running is a no-op once
# the stamps exist. Delete the target path to force a rebuild.

dev-setup: backend/.venv/bin/activate bnk-operator/.venv/bin/activate frontend-v2/node_modules
	@echo ""
	@echo "=== Dev environment ready ==="
	@echo "  backend/.venv        ✓"
	@echo "  bnk-operator/.venv   ✓"
	@echo "  frontend-v2/node_modules ✓"

backend/.venv/bin/activate: backend/requirements.txt backend/requirements-dev.txt
	@echo ""
	@echo "=== Creating backend/.venv ==="
	@cd backend && (python3.11 -m venv .venv || python3 -m venv .venv) && \
	  .venv/bin/pip install --upgrade pip && \
	  .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
	@touch $@

bnk-operator/.venv/bin/activate: bnk-operator/requirements.txt bnk-operator/requirements-dev.txt
	@echo ""
	@echo "=== Creating bnk-operator/.venv ==="
	@cd bnk-operator && python3 -m venv .venv && \
	  source .venv/bin/activate && \
	  pip install --upgrade pip && \
	  pip install -r requirements.txt -r requirements-dev.txt
	@touch $@

frontend-v2/node_modules: frontend-v2/package.json frontend-v2/package-lock.json
	@echo ""
	@echo "=== Installing frontend-v2 dependencies ==="
	@cd frontend-v2 && npm ci
	@touch $@

# ─── Git Hooks ───────────────────────────────────────────────────────────────

install-hooks:
	@echo "Installing git hooks..."
	@mkdir -p .git/hooks
	@cp .githooks/pre-commit .git/hooks/pre-commit
	@cp .githooks/pre-push .git/hooks/pre-push
	@chmod +x .git/hooks/pre-commit .git/hooks/pre-push
	@echo "Git hooks installed:"
	@echo "  pre-commit: lint + migration chain check on every commit (~5-10s)"
	@echo "  pre-push:   migration chain check + lint + unit + component + frontend tests (~2-3m)"

setup-hooks: ## Configure git to use project hooks (.githooks/)
	git config core.hooksPath .githooks
	@echo "Git hooks installed — pre-commit and pre-push migration checks active"

# ─── OpenAPI & Type Generation ───────────────────────────────────────────────

openapi: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Generating OpenAPI spec ==="
	@$(BACKEND_VENV_ROOT) python scripts/generate-openapi.py

openapi-types: openapi $(FRONTEND_PREREQ)
	@echo ""
	@echo "=== Generating TypeScript types from OpenAPI spec ==="
	@cd frontend-v2 && npx openapi-typescript ../backend/openapi.json -o src/types/api-generated.ts

openapi-check: $(BACKEND_PREREQ)
	@echo ""
	@echo "=== Checking OpenAPI spec freshness ==="
	@$(BACKEND_VENV_ROOT) python scripts/generate-openapi.py --check

openapi-types-check: openapi-check $(FRONTEND_PREREQ)
	@echo ""
	@echo "=== Checking TypeScript generated types freshness ==="
	@cd frontend-v2 && npx openapi-typescript ../backend/openapi.json -o src/types/api-generated-check.ts 2>/dev/null
	@if ! diff -q frontend-v2/src/types/api-generated.ts frontend-v2/src/types/api-generated-check.ts > /dev/null 2>&1; then \
	  rm frontend-v2/src/types/api-generated-check.ts; \
	  echo ""; \
	  echo "ERROR: Generated TypeScript types are stale."; \
	  echo "Run: make openapi-types"; \
	  echo ""; \
	  exit 1; \
	fi
	@rm frontend-v2/src/types/api-generated-check.ts
	@echo "  Generated types are up to date ✓"

# ─── Help ────────────────────────────────────────────────────────────────────

help:
	@echo "BNK Forge — Commands"
	@echo ""
	@echo "  All deploy/runtime targets auto-detect your platform:"
	@echo "    Linux  → host networking (services bind directly to host ports)"
	@echo "    macOS  → bridge networking via Docker Desktop (port mappings)"
	@echo ""
	@echo "Getting Started"
	@echo "  make dev-setup             Create backend/bnk-operator venvs + install frontend deps (for local tests/lint)"
	@echo "  make install               First-time server setup (builds everything from scratch)"
	@echo "  make update                Pull latest + rebuild + restart (keeps all data)"
	@echo "  make status                Show container health and versions"
	@echo ""
	@echo "Docker Build (cached builds)"
	@echo "  make build                 Build all app images in parallel (~30s cached, ~3min first time)"
	@echo "  make build-backend         Build backend API image only"
	@echo "  make build-frontend        Build frontend image only"
	@echo "  make build-worker          Build worker image (includes CLI tools — slower)"
	@echo "  make build-all             Build ALL images (api + worker + beat + frontend + proxy)"
	@echo "  make build-clean           Force rebuild without cache (only for deps/tool changes)"
	@echo "  make docker-verify         Verify worker CLI tools + MCP module in built images"
	@echo "  make docker-validate       Full Docker validation (build + size + tool verification)"
	@echo ""
	@echo "Deploy (platform-aware)"
	@echo "  make deploy                Build + restart all containers + verify health"
	@echo "  make deploy-backend        Build + restart backend containers only"
	@echo "  make deploy-frontend       Build + restart frontend only"
	@echo "  make up                    Start all containers"
	@echo "  make down                  Stop all containers"
	@echo "  make restart               Restart all containers"
	@echo "  make logs                  Tail all container logs"
	@echo "  make upgrade-safe          Run strict preflight + upgrade + verification gates"
	@echo "  make test-upgrade          Test upgrade.sh policy guards (isolated temp repo)"
	@echo "  make shellcheck            Lint all shell scripts with ShellCheck"
	@echo ""
	@echo "Testing"
	@echo "  make test                  Run all tests (lint + backend + frontend + proxy + operator + db)"
	@echo "  make test-docker           Run all tests inside containers (no local venv/node needed)"
	@echo "  make test-backend-docker   Backend pytest in python:3.11 container"
	@echo "  make test-frontend-docker  Frontend vitest in node:20 container"
	@echo "  make test-operator-docker  Operator pytest in python:3.12 container"
	@echo "  make lint-backend-docker   Ruff in python:3.11 container"
	@echo "  make test-backend          Run backend tests only (pytest)"
	@echo "  make test-backend-unit     Run backend unit tests only (tests/unit/)"
	@echo "  make test-backend-component Run backend component tests only (tests/component/)"
	@echo "  make test-backend-legacy   Run backend legacy tests (flat test_*.py files)"
	@echo "  make test-frontend         Run frontend tests only (vitest)"
	@echo "  make build-frontend-check  Verify frontend builds successfully"
	@echo "  make test-proxy            Run proxy config validation tests"
	@echo "  make test-operator         Run operator tests only (pytest)"
	@echo "  make test-contracts        Run golden contract tests (response shape verification)"
	@echo "  make test-db               Run DB migration validation tests"
	@echo "  make test-integration    Run integration tests (default marker set)"
	@echo "  make test-integration-full Run full-mode integration tests (requires Docker stack)"
	@echo "  make test-e2e              Run Tier 1 E2E tests (requires running stack)"
	@echo "  make test-e2e-tier2        Run Tier 2 E2E tests (requires stack + AWS creds)"
	@echo ""
	@echo "Linting & Type Checking"
	@echo "  make lint                  Run all linters (ruff + eslint)"
	@echo "  make lint-backend          Run backend linter only (ruff)"
	@echo "  make lint-frontend         Run frontend linter only (eslint)"
	@echo "  make coverage              Run tests with coverage reporting"
	@echo "  make typecheck-backend     Run mypy on core/ schemas/"
	@echo "  make typecheck-frontend    Run tsc --noEmit"
	@echo ""
	@echo "CI & Push"
	@echo "  make quick-check           Fast check (~15s): lint + mypy + openapi types"
	@echo "  make pre-push              Full CI mirror (~90s parallel): quick-check + all tests"
	@echo "  make push                  Run checks, push, then verify CI is green"
	@echo ""
	@echo "Code Generation"
	@echo "  make openapi               Generate OpenAPI spec (backend/openapi.json)"
	@echo "  make openapi-types         Generate OpenAPI spec + TypeScript types"
	@echo "  make openapi-check         Check if committed OpenAPI spec is stale"
	@echo "  make install-hooks         Install pre-commit and pre-push git hooks"
	@echo ""
	@echo "MCP Server"
	@echo "  make build-mcp             Build MCP server Docker image"
	@echo "  make test-mcp              Run MCP server tests (pytest)"
	@echo "  make smoke-mcp-live        Run bounded live MCP runtime smoke checks"
	@echo "  make mcp-readiness         Run MCP liveness + runtime readiness checks"
	@echo "  make mcp-recreate          Recreate MCP service after credential changes"
	@echo ""
	@echo "Distribution & Registry (Multi-Arch)"
	@echo "  make buildx-setup          Set up multi-arch builder with QEMU (one-time)"
	@echo "  make push-images           Build + push multi-arch images (amd64 + arm64)"
	@echo "  make push-customer-build   Build+push arm64 customer-build images (SHA tag + rolling 'customer-build' tag)"
	@echo "  make dist                  Build distributable tarball (dist/bnk-forge-VERSION.tar.gz)"
	@echo ""
	@echo "Disk & Cleanup"
	@echo "  make check-disk            Check disk space (warns if > 70%, fails if > 85%)"
	@echo "  make clean-docker          Remove dangling images + build cache > 7 days"
	@echo "  make setup-cleanup-cron    Install weekly cleanup cron job (Sunday 2 AM)"
	@echo ""
	@echo "  make help                  Show this help"

# ─── Distribution & Registry ─────────────────────────────────────────────────
#
# Build a distributable tarball and/or push images to a container registry.
# End users install from the tarball without needing source code.
#
#   make dist          — build dist/bnk-forge-VERSION.tar.gz
#   make push-images   — tag + push all images to BNK_FORGE_REGISTRY
#

# Files that `make dist` generates into dist/ but git does NOT track. The
# tarball manifest is `git ls-files dist/` plus this list, so packaging inherits
# the tracked set by definition -- dist/.gitignore stays the single source of
# truth -- while still shipping generated artifacts. Anything else sitting in a
# builder's dist/ (real secrets, .env, logs) is never copied. See #133.
#
# Note: git supplies the file NAMES only; contents are copied from the working
# tree, so the tracked files this target regenerates in place (VERSION, the two
# nginx confs) ship with their fresh content, not their committed content.
DIST_GENERATED := install-guide.html

# Build distributable install package (no source code needed by end users)
# No 'build' prerequisite: the tarball bundles no images (recipients pull from
# the registry), so rebuilding images here would be wasted work.
dist:
	@echo ""
	@echo "========================================="
	@echo "  BNK Forge — Building Distribution Package"
	@echo "========================================="
	@VERSION=$$(cat VERSION); \
	echo "  Version: $$VERSION"; \
	echo ""; \
	echo "=== Updating dist/VERSION ==="; \
	cp VERSION dist/VERSION; \
	echo "=== Updating dist/nginx configs ==="; \
	: "Adding a conf here? dist/.gitignore lists the tracked ones by name;"; \
	: "a file not listed there stays untracked and misses a fresh clone."; \
	cp proxy/nginx.local.conf dist/nginx/proxy.local.conf; \
	cp frontend-v2/nginx.local.conf dist/nginx/frontend.local.conf; \
	echo "=== Bundling install guide ==="; \
	cp user-pack/install-guide.html dist/install-guide.html; \
	echo "=== Creating tarball (tracked files + generated artifacts only) ==="; \
	TMPDIR=$$(mktemp -d); \
	STAGE="$$TMPDIR/bnk-forge-$${VERSION}"; \
	mkdir -p "$$STAGE"; \
	{ git ls-files -z -- dist/; \
	  for g in $(DIST_GENERATED); do printf 'dist/%s\0' "$$g"; done; } \
	  | sort -zu \
	  | while IFS= read -r -d '' f; do \
	      if [ ! -f "$$f" ]; then \
	        echo "  ✗ manifest lists a file that is not in the working tree: $$f" >&2; \
	        exit 1; \
	      fi; \
	      rel="$${f#dist/}"; \
	      mkdir -p "$$STAGE/$$(dirname "$$rel")"; \
	      cp "$$f" "$$STAGE/$$rel"; \
	    done || { rm -rf "$$TMPDIR"; exit 1; }; \
	tar -czf "dist/bnk-forge-$${VERSION}.tar.gz" -C "$$TMPDIR" "bnk-forge-$${VERSION}"; \
	rm -rf "$$TMPDIR"; \
	DIST_GENERATED="$(DIST_GENERATED)" scripts/check-dist-contents.sh \
	  "dist/bnk-forge-$${VERSION}.tar.gz" || exit 1; \
	echo ""; \
	echo "  ✓ Created: dist/bnk-forge-$${VERSION}.tar.gz"; \
	echo ""; \
	ls -lh "dist/bnk-forge-$${VERSION}.tar.gz"; \
	echo ""; \
	echo "  To publish:"; \
	echo "    1. Push images:  make push-images BNK_FORGE_REGISTRY=ghcr.io/your-org"; \
	echo "    2. Upload:       gh release create v$${VERSION} dist/bnk-forge-$${VERSION}.tar.gz"

# ── Multi-arch image build + push ────────────────────────────────────────────
#
# Builds linux/amd64 + linux/arm64 images and pushes multi-arch manifests.
# Uses docker buildx to cross-compile. Each registry tag is a manifest list
# that Docker automatically resolves to the correct platform on pull.
#
# Usage:
#   make push-images BNK_FORGE_REGISTRY=ghcr.io/your-org
#   make push-images BNK_FORGE_REGISTRY=ghcr.io/your-org PLATFORMS=linux/amd64
#
# Prerequisites:
#   - docker buildx (included in Docker Desktop; on Linux: docker buildx install)
#   - QEMU for cross-platform builds: make buildx-setup
#   - Authenticated to the target registry: docker login ghcr.io
#

# Platforms to build for (override with: make push-images PLATFORMS=linux/amd64)
PLATFORMS ?= linux/amd64,linux/arm64

# Platforms for the customer-build test loop (override for a multi-arch release: CB_PLATFORMS="linux/amd64,linux/arm64")
CB_PLATFORMS ?= linux/arm64

# Buildx builder name
BUILDX_BUILDER ?= bnk-forge-multiarch

# Builder for the arm64 customer-build test loop. desktop-linux is Docker Desktop's
# built-in builder (native arm64, healthy emulation) — avoids the QEMU reset footgun.
CB_BUILDER ?= desktop-linux

# Create/bootstrap the buildx builder with QEMU support
buildx-setup:
	@echo ""
	@echo "========================================="
	@echo "  Setting up multi-arch buildx builder"
	@echo "========================================="
	@echo ""
	@echo "=== Registering QEMU emulators ==="
	@if docker info --format '{{.OperatingSystem}}' 2>/dev/null | grep -q "Docker Desktop"; then \
	  echo "  Docker Desktop detected — using its built-in emulation; skipping QEMU --reset (it corrupts binfmt)"; \
	else \
	  docker run --rm --privileged multiarch/qemu-user-static --reset -p yes 2>/dev/null \
	    || docker run --rm --privileged tonistiigi/binfmt --install all 2>/dev/null \
	    || echo "  ⚠  QEMU setup skipped (may already be configured)"; \
	fi
	@echo ""
	@echo "=== Creating buildx builder: $(BUILDX_BUILDER) ==="
	@if docker buildx inspect $(BUILDX_BUILDER) > /dev/null 2>&1; then \
	  echo "  Builder '$(BUILDX_BUILDER)' already exists — reusing"; \
	else \
	  docker buildx create --name $(BUILDX_BUILDER) \
	    --driver docker-container \
	    --platform linux/amd64,linux/arm64 \
	    --bootstrap; \
	  echo "  ✓ Builder '$(BUILDX_BUILDER)' created"; \
	fi
	@echo ""
	@docker buildx inspect $(BUILDX_BUILDER) | head -10
	@echo ""
	@echo "  ✓ Multi-arch builder ready"
	@echo "  Supported platforms:"
	@docker buildx inspect $(BUILDX_BUILDER) --bootstrap 2>/dev/null | grep -oP 'linux/\w+' | sort -u | sed 's/^/    /'

# Push multi-arch images to a container registry
# Usage: make push-images BNK_FORGE_REGISTRY=ghcr.io/your-org
push-images:
	@echo ""
	@echo "========================================="
	@echo "  BNK Forge — Multi-Arch Push to Registry"
	@echo "========================================="
	@if [ -z "$${BNK_FORGE_REGISTRY:-}" ]; then \
	  echo "ERROR: BNK_FORGE_REGISTRY is not set."; \
	  echo ""; \
	  echo "Usage: make push-images BNK_FORGE_REGISTRY=ghcr.io/your-org"; \
	  echo "       make push-images BNK_FORGE_REGISTRY=ghcr.io/your-org PLATFORMS=linux/amd64"; \
	  exit 1; \
	fi
	@if ! docker buildx inspect $(BUILDX_BUILDER) > /dev/null 2>&1; then \
	  echo ""; \
	  echo "ERROR: Buildx builder '$(BUILDX_BUILDER)' not found."; \
	  echo "Run first: make buildx-setup"; \
	  exit 1; \
	fi
	@VERSION=$$(cat VERSION); \
	REGISTRY=$${BNK_FORGE_REGISTRY}; \
	echo "  Registry:  $$REGISTRY"; \
	echo "  Version:   $$VERSION"; \
	echo "  Platforms: $(PLATFORMS)"; \
	echo "  Builder:   $(BUILDX_BUILDER)"; \
	echo ""; \
	echo "=== Building + pushing all images in parallel (docker buildx bake) ==="; \
	GIT_REVISION=$$(git rev-parse HEAD 2>/dev/null || echo unknown); \
	REGISTRY=$$REGISTRY VERSION=$$VERSION PLATFORMS=$(PLATFORMS) GIT_REVISION=$$GIT_REVISION \
	  docker buildx bake --builder $(BUILDX_BUILDER) --push && \
	echo ""; \
	echo "========================================="; \
	echo "  ✅ All images pushed to $$REGISTRY"; \
	echo "  Tags:      $$VERSION, latest"; \
	echo "  Platforms: $(PLATFORMS)"; \
	echo ""; \
	echo "  Verify manifests:"; \
	echo "    docker manifest inspect $${REGISTRY}/bnk-forge-api:$${VERSION}"; \
	echo "========================================="

# Push customer-build images: SHA-pinned immutable tag + rolling 'customer-build' tag
# Usage: make push-customer-build BNK_FORGE_REGISTRY=ghcr.io/your-org
push-customer-build:
	@echo ""
	@echo "========================================="
	@echo "  BNK Forge — Customer-Build Publish"
	@echo "========================================="
	@if [ -z "$${BNK_FORGE_REGISTRY:-}" ]; then \
	  echo "ERROR: BNK_FORGE_REGISTRY is not set."; \
	  echo ""; \
	  echo "Usage: make push-customer-build BNK_FORGE_REGISTRY=ghcr.io/your-org"; \
	  exit 1; \
	fi
	@if ! docker buildx inspect $(CB_BUILDER) > /dev/null 2>&1; then \
	  echo ""; \
	  echo "ERROR: Buildx builder '$(CB_BUILDER)' not found."; \
	  echo "Run first: make buildx-setup"; \
	  exit 1; \
	fi
	@BASE=$$(cat VERSION); \
	SHA=$$(git rev-parse --short HEAD); \
	FULLTAG=$${BASE}-cb.$${SHA}; \
	REGISTRY=$${BNK_FORGE_REGISTRY}; \
	echo "  Registry:      $$REGISTRY"; \
	echo "  Base version:  $$BASE"; \
	echo "  Commit:        $$SHA"; \
	echo "  Immutable tag: $$FULLTAG"; \
	echo "  Rolling tag:   customer-build"; \
	echo "  Platforms:     $(CB_PLATFORMS)"; \
	echo ""; \
	echo "=== Building + pushing customer-build images (docker buildx bake) ==="; \
	REGISTRY=$$REGISTRY VERSION=$$FULLTAG ROLLING_TAG=customer-build PLATFORMS=$(CB_PLATFORMS) \
	  docker buildx bake --builder $(CB_BUILDER) --push && \
	echo ""; \
	echo "========================================="; \
	echo "  ✅ Pushed to $$REGISTRY"; \
	echo "    Immutable: $${REGISTRY}/bnk-forge-api:$${FULLTAG}   (+ worker/beat/frontend/proxy/mcp/operator)"; \
	echo "    Rolling:   $${REGISTRY}/bnk-forge-api:customer-build"; \
	echo "    Platforms: $(CB_PLATFORMS)"; \
	echo ""; \
	echo "    To deploy newest in dist .env:"; \
	echo "      BNK_FORGE_REGISTRY=$$REGISTRY"; \
	echo "      BNK_FORGE_VERSION=customer-build"; \
	echo "    Or pin this exact build:"; \
	echo "      BNK_FORGE_VERSION=$${FULLTAG}"; \
	echo "========================================="

# Multi-arch customer-build publish: identical to push-customer-build but emits a
# combined linux/amd64 + linux/arm64 manifest list. Routes through the docker-container
# builder ($(BUILDX_BUILDER)) because the default 'docker' driver cannot push manifest lists.
# Docker Desktop already ships amd64/arm64 binfmt emulation — do NOT run 'make buildx-setup'
# (its QEMU --reset wipes Docker Desktop's binfmt handlers). One-time builder create:
#   docker buildx create --name $(BUILDX_BUILDER) --driver docker-container --bootstrap
# Usage: make push-customer-build-multiarch BNK_FORGE_REGISTRY=ghcr.io/your-org
push-customer-build-multiarch:
	@$(MAKE) push-customer-build \
	  CB_PLATFORMS=linux/amd64,linux/arm64 \
	  CB_BUILDER=$(BUILDX_BUILDER)

# Keyless cosign signing + SBOM + provenance for all published images.
# Run AFTER `make push-images` (images must already be in the registry).
# Dry-run by default — prints what would be signed without touching the registry.
# Usage:
#   make publish-signed BNK_FORGE_REGISTRY=ghcr.io/your-org               # dry-run
#   make publish-signed BNK_FORGE_REGISTRY=ghcr.io/your-org SIGN_EXECUTE=1 # sign for real
publish-signed:
	@if [ -z "$${BNK_FORGE_REGISTRY:-}" ]; then \
	  echo "ERROR: BNK_FORGE_REGISTRY is not set."; \
	  echo "Usage: make publish-signed BNK_FORGE_REGISTRY=ghcr.io/your-org"; \
	  exit 1; \
	fi
	@if [ "$${SIGN_EXECUTE:-0}" = "1" ]; then \
	  BNK_FORGE_REGISTRY=$${BNK_FORGE_REGISTRY} DRY_RUN=0 bash scripts/publish-signed-images.sh --execute; \
	else \
	  BNK_FORGE_REGISTRY=$${BNK_FORGE_REGISTRY} bash scripts/publish-signed-images.sh --dry-run; \
	fi

# Verify Docker images have expected tools and modules (catches COPY/install regressions)
# Run after `make build` or `make build-clean` to validate images before deploy/push.
docker-verify:
	@echo ""
	@echo "=== Docker Image Verification ==="
	@echo ""
	@echo "--- Version baked into the image ---"
	@expected=$$(cat VERSION); \
	failed=0; \
	for img in bnk-forge-api bnk-forge-worker bnk-forge-beat; do \
	  actual=$$(docker run --rm --entrypoint "" $$img:latest cat /app/VERSION 2>/dev/null || echo ""); \
	  if [ "$$actual" = "$$expected" ]; then \
	    echo "  OK    $$img reports $$actual"; \
	  else \
	    echo "  FAIL  $$img reports '$$actual', expected '$$expected'"; \
	    echo "        settings.VERSION falls back to 0.0.0 when /app/VERSION is absent,"; \
	    echo "        which surfaces on /api, the OpenAPI title and X-BNK-Forge-Version."; \
	    failed=1; \
	  fi; \
	done; \
	[ $$failed -eq 0 ] || exit 1
	@echo ""
	@echo "--- Worker CLI tools ---"
	@failed=0; \
	check_tool() { \
	    local tool=$$1 cmd=$$2; \
	    if docker run --rm --entrypoint "" bnk-forge-worker:latest bash -c "$$cmd" > /dev/null 2>&1; then \
	      echo "  OK    $$tool"; \
	    else \
	      echo "  FAIL  $$tool"; \
	      failed=1; \
	    fi; \
	  }; \
	  check_tool "tofu"    "tofu version"; \
	  check_tool "helm"    "helm version"; \
	  check_tool "kubectl" "kubectl version --client"; \
	  check_tool "aws"     "aws --version"; \
	  check_tool "docker"  "docker --version"; \
	  check_tool "git"     "git --version"; \
	  check_tool "jq"      "jq --version"; \
	  check_tool "bash-sh" "[ -L /bin/sh ] && readlink /bin/sh | grep -q bash"; \
	  echo ""; \
	  echo "--- MCP server module ---"; \
	  if docker run --rm bnk-forge-mcp:latest python -c "import bnk_forge_mcp.server; print('OK')" > /dev/null 2>&1; then \
	    echo "  OK    bnk_forge_mcp.server"; \
	  else \
	    echo "  FAIL  bnk_forge_mcp.server"; \
	    failed=1; \
	  fi; \
	  echo ""; \
	  if [ "$$failed" = "1" ]; then \
	    echo "ERROR: Docker image verification failed"; \
	    exit 1; \
	  fi
	@echo "=== Docker verification passed ==="

# ─── Security Audit ──────────────────────────────────────────────────────────
# Slice A / #300: real recipe bodies replacing the prior no-op stubs.
# Gate: pip-audit exits non-zero on HIGH+CRITICAL; npm audit gates on all deps (prod+dev) — #303.
# Requires pip-audit to be installed (CI installs it; locally: pip install pip-audit).
#
# PYSEC-2026-1325: ecdsa is a transitive dep of python-jose[cryptography]==3.5.0 (not bumped in this PR); no fix version available yet.
# (fastapi/starlette/paramiko CVEs previously deferred have been fixed by this PR's bumps.)
PIP_AUDIT_DEFER = --ignore-vuln PYSEC-2026-1325
security-audit:
	@echo ""
	@echo "=== Security Audit ==="
	@echo ""
	@echo "--- Python: backend ---"
	@pip-audit -r backend/requirements.txt --progress-spinner=off $(PIP_AUDIT_DEFER)
	@echo ""
	@echo "--- Python: bnk-operator ---"
	@pip-audit -r bnk-operator/requirements.txt --progress-spinner=off
	@echo ""
	@echo "--- Python: mcp-server (pyproject.toml) ---"
	# PYSEC-2026-196 is the build-env pip itself (pip<26.1.2), not a project dependency.
	# PYSEC-2026-3447 is the build-env setuptools (<83.0.0), not a project dependency.
	@cd mcp-server && pip-audit --progress-spinner=off $(PIP_AUDIT_DEFER) --ignore-vuln PYSEC-2026-196 --ignore-vuln PYSEC-2026-3447
	@echo ""
	@echo "--- JavaScript: frontend-v2 (prod deps, HIGH+) ---"
	@cd frontend-v2 && npm audit --omit=dev --audit-level=high
	@echo "--- JavaScript: frontend-v2 (dev deps, CRITICAL only) ---"
	@cd frontend-v2 && npm audit --audit-level=critical
	@echo ""
	@echo "=== Security audit passed ==="

# Slice A / #300: real Dockerfile lint using BuildKit's built-in check call.
# Uses `docker buildx build --call=check` which is available in BuildKit >= 0.12
# (Docker Desktop >= 4.27). Exits non-zero on lint failures.
# Alternative: install hadolint and run over each Dockerfile.
docker-check:
	@echo ""
	@echo "=== Docker Check (BuildKit lint) ==="
	@failed=0; \
	  for target_spec in \
	    "backend/Dockerfile:." \
	    "frontend-v2/Dockerfile:." \
	    "proxy/Dockerfile:proxy" \
	    "mcp-server/Dockerfile:mcp-server" \
	    "bnk-operator/Dockerfile:bnk-operator"; do \
	    dockerfile=$${target_spec%%:*}; \
	    ctx=$${target_spec##*:}; \
	    echo "  Checking $$dockerfile ..."; \
	    if ! docker buildx build --call=check --file=$$dockerfile $$ctx 2>&1; then \
	      echo "  FAIL: $$dockerfile"; \
	      failed=1; \
	    fi; \
	  done; \
	  if [ "$$failed" = "1" ]; then \
	    echo ""; \
	    echo "ERROR: docker-check failed — fix Dockerfile warnings above"; \
	    exit 1; \
	  fi
	@echo "=== Docker check passed ==="

# Full Docker validation: build + size thresholds + tool/module verification
docker-validate: docker-check docker-verify

# ─── Disk Management & Cleanup ───────────────────────────────────────────────

# Check disk space before builds
check-disk:
	@./scripts/check-disk-space.sh

# Clean up Docker disk usage (safe — keeps running containers + recent cache)
clean-docker:
	@./scripts/docker-cleanup.sh

# Remove project-prefix volumes that compose has disowned (orphans).
# Detected by `make clean`; this target acts on them with a confirmation prompt.
clean-orphans:
	@./scripts/docker-clean-orphans.sh

# Alias for convenience
clean: clean-docker

# Install automated weekly cleanup
setup-cleanup-cron:
	@./scripts/setup-cleanup-cron.sh
