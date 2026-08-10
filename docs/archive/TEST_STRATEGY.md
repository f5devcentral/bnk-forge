# BNK-Forge v2 — Test Strategy & Coverage Plan

**Version:** 2.10.47  
**Created:** 2026-02-23  
**Status:** Plan — Ready for Implementation  
**Replaces:** `docs/SPRINT-TEST-COVERAGE.md` (Phase 1 only)

---

## 1. Executive Summary

BNK-Forge v2 has a solid but incomplete test foundation. The goal is **trustworthy test coverage**: every area of the system has automated tests, they run locally before every push, they run again in CI, and they are always green. If a test is red, work stops until it's fixed.

### Current State (2026-02-23)

| Area | Tests | Pass Rate | Coverage | Verdict |
|------|-------|-----------|----------|---------|
| **Backend unit/integration** | 849 pass, 17 skip | 98% | ~30% (est.) | Good foundation, large gaps |
| **Frontend unit** | 154 pass | 100% | ~15% | Narrow — only 14 of 75+ files tested |
| **Proxy (nginx)** | 0 | N/A | 0% | Nothing |
| **Operator (bnk-operator)** | 0 | N/A | 0% | Nothing |
| **Database (migrations)** | 0 | N/A | 0% | Nothing |
| **E2E (Playwright)** | 1 spec, broken | 0% | Near 0% | Infrastructure exists, tests broken |
| **CI pipeline** | Runs backend + frontend | Passing | Partial | Missing proxy, operator, DB |
| **Local automation** | None | N/A | N/A | No Makefile, no git hooks |

### Target State

| Area | Tests | Coverage | Enforcement |
|------|-------|----------|-------------|
| **Backend** | 1200+ | 60%+ | `--cov-fail-under=60` |
| **Frontend** | 300+ | 30%+ | Vitest thresholds |
| **Proxy** | 20+ | Config + integration | CI job |
| **Operator** | 80+ | 70%+ | CI job |
| **Database** | 10+ | Migration validation | CI job |
| **E2E** | Local only | Critical paths | Pre-push optional |
| **CI** | All areas | Gated | All jobs must pass |
| **Local** | `make test` | Pre-commit lint, pre-push full | Git hooks |

---

## 2. Architecture — What Tests Where

```
┌─────────────────────────────────────────────────────────────────┐
│                        Test Pyramid                             │
│                                                                 │
│                          /\                                     │
│                         /  \        E2E (Playwright)            │
│                        / 10 \       Local only, optional        │
│                       /──────\                                  │
│                      /        \     Integration                 │
│                     /   150    \    Routes + DB + mocks          │
│                    /────────────\                                │
│                   /              \   Unit                        │
│                  /    1500+       \  Services, hooks, components │
│                 /──────────────────\                             │
│                                                                 │
│  ┌──────────┐ ┌──────────┐ ┌───────┐ ┌──────────┐ ┌─────────┐ │
│  │ Backend  │ │ Frontend │ │ Proxy │ │ Operator │ │   DB    │ │
│  │ pytest   │ │ vitest   │ │ nginx │ │ pytest   │ │ alembic │ │
│  │ 849+skip │ │ 154 pass │ │ 0     │ │ 0        │ │ 0       │ │
│  └──────────┘ └──────────┘ └───────┘ └──────────┘ └─────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.1 Test Types

| Type | Speed | What It Validates | Runs When |
|------|-------|-------------------|-----------|
| **Unit** | <1s each | Single function/class in isolation | Every commit (pre-commit lint, pre-push full) |
| **Integration** | <3s each | Route + DB + mocked externals | Pre-push + CI |
| **Proxy validation** | <5s total | nginx.conf syntax, security headers, routing rules | Pre-push + CI |
| **DB migration** | <10s total | Alembic up/down without errors | Pre-push + CI |
| **E2E** | 30s-5min each | Full browser workflow through running stack | Local only, manual trigger |

---

## 3. Current Coverage Audit — Complete Gap Analysis

### 3.1 Backend: Routes (7 of 36 tested = 19%)

**Tested (7):**

| Route File | Test File | Tests |
|-----------|-----------|-------|
| `routes/auth.py` | `test_routes_auth.py` | Login success/failure, RBAC |
| `routes/project_execution.py` | `test_routes_execution.py` | init/plan/apply/destroy/cancel/deploy/retry/logs |
| `routes/kubernetes.py` | `test_routes_k8s.py` | Cluster list + register |
| `routes/projects.py` | `test_routes_projects.py` | CRUD + unauthorized |
| `routes/audit.py` | `test_routes_rbac.py` | Admin access, viewer denied |
| `routes/stacks.py` | `test_routes_stacks.py` | Template list + detail |
| `routes/system.py` | `test_routes_system.py` | Health (no auth), version (auth) |

**NOT Tested (29) — Ordered by Priority:**

| Priority | Route File | Risk if Untested |
|----------|-----------|------------------|
| **P0** | `routes/project_secrets.py` | Secrets could leak or fail silently |
| **P0** | `routes/project_variables.py` | Variable injection is core to deployments |
| **P0** | `routes/project_modules.py` | Module CRUD is the primary user flow |
| **P0** | `routes/helm.py` | Helm operations affect live clusters |
| **P0** | `routes/project_orchestration.py` | Orchestration coordinates multi-module deploys |
| **P1** | `routes/cloud_auth.py` | AWS credential management |
| **P1** | `routes/drift.py` | Drift detection affects operational safety |
| **P1** | `routes/config_promotion.py` | Config promotion between environments |
| **P1** | `routes/snapshots.py` | State snapshot/restore |
| **P1** | `routes/k8s/resources.py` | K8s resource CRUD |
| **P1** | `routes/k8s/f5bnk.py` | F5 BNK-specific operations |
| **P1** | `routes/operators/crud.py` | Operator management |
| **P1** | `routes/operators/commands.py` | Remote command execution |
| **P1** | `routes/operators/fleet.py` | Fleet management |
| **P1** | `routes/operators/tokens.py` | Token management (security) |
| **P2** | `routes/alert_channels.py` | Alert routing |
| **P2** | `routes/bnk_upgrade.py` | BNK upgrade orchestration |
| **P2** | `routes/config_export.py` | Config export |
| **P2** | `routes/cost.py` | Cost estimation |
| **P2** | `routes/credential_templates.py` | Credential templates |
| **P2** | `routes/k8s_websocket.py` | WebSocket for K8s exec |
| **P2** | `routes/k8s/tunnels.py` | SSH tunnel management |
| **P2** | `routes/module_library.py` | Module library browsing |
| **P2** | `routes/module_sources.py` | Module source management |
| **P2** | `routes/notifications.py` | Notification preferences |
| **P2** | `routes/operator_polling.py` | Operator HTTP polling |
| **P2** | `routes/operator_ws.py` | Operator WebSocket |
| **P2** | `routes/project_deployments.py` | Deployment history |
| **P2** | `routes/project_variable_mappings.py` | Variable wiring between modules |
| **P2** | `routes/registry.py` | Container registry |
| **P2** | `routes/runbooks.py` | Runbook operations |
| **P2** | `routes/state_viewer.py` | State file viewer |
| **P2** | `routes/tasks.py` | Task history/logs |

### 3.2 Backend: Services (12 of 62 tested = 19%)

**Tested (12):**

| Service | Test Coverage | Notes |
|---------|-------------|-------|
| `adaptive_module_selector.py` | Unit | Module selection logic |
| `bnk_upgrade_service.py` | Unit | Version parsing, plan generation |
| `builtin_module_seeder.py` | Unit | Metadata export + catalog seeder |
| `dependency_graph_service.py` | Unit | Kahn's algorithm, cycle detection |
| `helm_service.py` | Unit + 4 Security | install/upgrade/rollback/uninstall + injection prevention |
| `k8s_drift_service.py` | Unit | Manifest diff and normalization |
| `operator_registry.py` | Unit (11 skipped) | Cleanup, auth, connection fan-out — async tests broken |
| `secrets_service.py` | Unit (1 skipped) | CRUD, encryption, execution prep |
| `variable_parser.py` | Security | Git clone injection |
| `execution/kubernetes_engine.py` | Unit | apply/destroy flow, health check |
| `execution/opentofu_runtime.py` | Unit | Workspace prep, subprocess, retry |
| `execution/variable_assembler.py` | Unit + Security | 7-layer chain, transforms, JWT guard |

**NOT Tested — Critical Services (P0):**

| Service | Why Critical |
|---------|-------------|
| `auth_service.py` | JWT creation, password hashing, token validation — security-critical |
| `credentials_service.py` | AWS/cloud credential management — security-critical |
| `kubernetes_service.py` | Direct K8s API calls — affects live clusters |
| `project_crud_service.py` | Core project lifecycle |
| `project_module_service.py` | Module lifecycle management |
| `execution/engine_router.py` | Routes to correct execution engine |
| `execution/opentofu_engine.py` | Full OpenTofu lifecycle orchestration |
| `execution/operator_engine.py` | Remote operator execution |
| `execution/task_dispatch.py` | Celery task dispatch — the entry point for all async work |
| `workspace_manager.py` | Workspace creation/cleanup |
| `workspace_lock_service.py` | Prevents concurrent deployments |

**NOT Tested — Important Services (P1):**

| Service | Why Important |
|---------|-------------|
| `aws_auth_service.py` | AWS SSO flow |
| `cluster_management_service.py` | Multi-cluster management |
| `drift_service.py` | OpenTofu drift detection |
| `helm_chart_store_service.py` | Helm chart storage |
| `helm_repository_service.py` | Helm repo management |
| `input_wiring_service.py` | Variable wiring between modules |
| `module_catalog_service.py` | Module catalog operations |
| `parallel_execution_service.py` | Parallel deploy/destroy |
| `snapshot_service.py` | State snapshot CRUD |
| `stack_service.py` | Stack template operations |
| `stack_deployment_service.py` | Stack deployment orchestration |

**NOT Tested — Kubernetes Sub-Package (0 of 7 files):**

| File | What It Does |
|------|-------------|
| `kubernetes/_base.py` | Base K8s client setup |
| `kubernetes/_describe.py` | Resource describe operations |
| `kubernetes/_metrics.py` | K8s metrics collection |
| `kubernetes/_operations.py` | Apply/delete/scale operations |
| `kubernetes/_pods.py` | Pod management, exec, logs |
| `kubernetes/_resources.py` | Resource listing/filtering |
| `kubernetes/_rollouts.py` | Deployment rollout management |

### 3.3 Backend: Core (0 of 10 tested = 0%)

| File | What It Does | Priority |
|------|-------------|----------|
| `core/auth_middleware.py` | JWT validation, role extraction | **P0** |
| `core/encryption.py` | Fernet encryption for secrets | **P0** |
| `core/errors.py` | Error classes + `@handle_route_errors` | **P0** |
| `core/config.py` | App configuration / Settings class | **P1** |
| `core/cache.py` | Redis caching layer | **P1** |
| `core/audit_middleware.py` | Audit log middleware | **P1** |
| `core/k8s_resource_registry.py` | K8s resource type registry | **P2** |
| `core/k8s_types.py` | K8s type definitions | **P2** |
| `core/defaults.py` | Default values | **P2** |
| `core/logging_config.py` | Logging setup | **P3** |
| `core/worker_heartbeat.py` | Celery worker heartbeat | **P3** |

### 3.4 Backend: Tasks (0 of 10 tested = 0%)

| File | What It Does | Priority |
|------|-------------|----------|
| `tasks/opentofu_tasks.py` | Celery tasks for init/plan/apply/destroy | **P0** |
| `tasks/kubernetes_tasks.py` | Celery tasks for K8s operations | **P0** |
| `tasks/parallel_tasks.py` | Multi-module parallel execution | **P0** |
| `tasks/stack_tasks.py` | Stack deploy/destroy orchestration | **P1** |
| `tasks/bnk_upgrade_tasks.py` | BNK upgrade execution | **P1** |
| `tasks/drift_tasks.py` | Periodic drift detection | **P1** |
| `tasks/_tofu_helpers.py` | Shared OpenTofu helpers | **P1** |
| `tasks/health_monitor_task.py` | Periodic health checks | **P2** |
| `tasks/heartbeat_task.py` | Worker heartbeat | **P2** |
| `tasks/operator_cleanup_task.py` | Stale operator cleanup | **P2** |

### 3.5 Backend: 17 Skipped Tests

| Test File | Skipped | Reason | Fix |
|-----------|---------|--------|-----|
| `test_operator_production.py` | 11 tests | `@pytest.mark.asyncio` but `pytest-asyncio` not properly configured | Install `pytest-asyncio` in venv, verify `asyncio_mode = "auto"` |
| `test_secrets_service.py` | 1 test | Same async issue | Same fix |
| `test_cli_arg_security.py` | 5 tests | Tests for operator validation functions, import issues | Fix imports or skip conditions |

**Recommendation:** Fix all 17 skipped tests. They represent real code paths that should be validated. The async tests need `pytest-asyncio` properly installed and configured.

### 3.6 Frontend: Hooks (8 of 32 tested = 25%)

**Tested:** `useAuth`, `useDrift`, `useFleet`, `useK8s`, `useProjects`, `useRunbooks`, `useSnapshots`, `useTasks`

**NOT Tested — By Priority:**

| Priority | Hook | Why |
|----------|------|-----|
| **P0** | `useModules` | Core deployment flow |
| **P0** | `useHelm` | Helm operations affect live clusters |
| **P0** | `useDeployments` | Deployment management |
| **P0** | `useProjectSecrets` | Secrets management |
| **P1** | `useK8sClusters` | Cluster management |
| **P1** | `useK8sResources` | Resource management |
| **P1** | `useK8sOperations` | K8s operations |
| **P1** | `useOperators` | Operator management |
| **P1** | `useStacks` | Stack templates |
| **P1** | `useParallelExecution` | Parallel deploy/destroy |
| **P1** | `useConfigPromotion` | Config promotion |
| **P1** | `useModuleSources` | Module sources |
| **P2** | `useAllHelmReleases` | Helm aggregation |
| **P2** | `useK8sBnk` | BNK-specific |
| **P2** | `useCost` | Cost analysis |
| **P2** | `useSettings` | Settings |
| **P2** | `useSystem` | System status |
| **P2** | `useNotifications` | Notifications |
| **P2** | `useRegistry` | Container registry |
| **P2** | `useRole` | Role checks |
| **P3** | `useDebounce` | Utility |
| **P3** | `useAccessibility` | Utility |
| **P3** | `useFocusTrap` | Utility |
| **P3** | `useKeyboardShortcuts` | Utility |
| **P3** | `useTaskWebSocket` | WebSocket connection |

### 3.7 Frontend: Components (5 of 29 tested = 17%)

**Tested:** `ErrorBoundary`, `ErrorHandler`, `Login`, `Notifications`, `ProjectCreate`, `RoleGuard`

**NOT Tested:** 24+ component directories. Every component under `components/` except the 5 above: `aws/`, `bnk/`, `CommandPalette`, `cost/`, `dashboard/`, `deployments/`, `drift/`, `execution/`, `fleet/`, `health/`, `helm/`, `k8s/`, `layout/`, `modules/`, `operators/`, `presets/`, `projects/` (partial), `providers/`, `runbooks/`, `secrets/`, `settings/`, `shared/`, `snapshots/`, `stacks/`, `system/`, `ui/`.

### 3.8 Frontend: Pages (0 of 14 tested = 0%)

No page-level tests exist. All 14 pages (`Dashboard`, `Projects`, `ProjectDetailV2`, `KubernetesV2`, `HelmPackagesV2`, `Fleet`, `Operators`, `Stacks`, `Modules`, `System`, `TaskHistory`, `F5BNK`, `AuthTemplates`, `Login`) are untested.

### 3.9 Frontend: API Modules (0 of 19 tested = 0%)

No API module has dedicated tests. The API layer (`frontend-v2/src/lib/api/`) is only tested indirectly through hook tests that happen to call these modules.

### 3.10 Frontend: Lib Utilities (1 of 15 tested = 7%)

Only `error-handler.ts` has a test (via `ErrorHandler.test.ts`). The remaining 14 utility files (`validators.ts`, `time-utils.ts`, `health-utils.ts`, `helm-grouping.ts`, `queryKeys.ts`, `constants.ts`, `logger.ts`, `notify.ts`, `presets.ts`, `status-colors.ts`, `storage-keys.ts`, `utils.ts`, `aws-regions.ts`, `categoryColors.ts`) have no tests.

### 3.11 Proxy (0 tests)

The nginx reverse proxy (`proxy/nginx.conf`) has:
- Rate limiting (`5r/m` login, `30r/s` API)
- Security headers (X-Frame-Options, X-Content-Type-Options, X-XSS-Protection, Referrer-Policy, Permissions-Policy)
- WebSocket upgrade support
- Gzip compression
- DNS re-resolution for container IP changes
- 300s timeouts for long-running operations

**None of this is tested.**

### 3.12 Operator — bnk-operator/ (0 tests)

6 Python modules, 0 tests:

| Module | Lines | Key Functionality |
|--------|-------|-------------------|
| `main.py` | 208 | Entry point, env config, signal handling, concurrent task startup |
| `command_handlers.py` | 769 | K8s apply/destroy, Helm install/uninstall, cluster scan, health, resource listing, pod logs, CLI injection guards |
| `control_plane_client.py` | 408 | WebSocket client, TLS, registration, heartbeat, command dispatch, reconnect with backoff |
| `polling_client.py` | 303 | HTTP polling alternative, registration, poll+execute loop, retry |
| `health_server.py` | 139 | HTTP liveness/readiness/metrics endpoints |
| `health_watcher.py` | 136 | Periodic health check, delta detection, change reporting |

### 3.13 Database Migrations (0 tests)

Alembic migrations (`backend/alembic/versions/`) are never validated. A bad migration could break production with no test catching it before deploy.

### 3.14 E2E (broken)

- 1 spec file with 18 steps; fails at step 2 (dialog scroll issue)
- Page objects exist for Projects, ProjectDetail, Tasks
- AWS helper exists but requires live AWS access
- No auth flow tests, no error handling tests, no independent specs

---

## 4. Local Automation — The `make test` Experience

### 4.1 Makefile

A single `Makefile` at repo root provides all test commands:

```makefile
# === BNK-Forge v2 Test Commands ===

.PHONY: test test-backend test-frontend test-proxy test-operator test-db \
        test-all lint lint-backend lint-frontend coverage install-hooks help

# Default: run everything
test: lint test-backend test-frontend test-proxy test-operator test-db
	@echo "\n=== All tests passed ==="

# --- Individual test suites ---

test-backend:
	@echo "\n=== Backend Tests ==="
	cd backend && source .venv/bin/activate && \
	  python -m pytest tests/ -v --tb=short -q

test-frontend:
	@echo "\n=== Frontend Tests ==="
	cd frontend-v2 && npm test -- --run

test-proxy:
	@echo "\n=== Proxy Tests ==="
	cd backend && source .venv/bin/activate && \
	  python -m pytest tests/test_proxy_config.py -v --tb=short

test-operator:
	@echo "\n=== Operator Tests ==="
	cd bnk-operator && source .venv/bin/activate && \
	  python -m pytest tests/ -v --tb=short

test-db:
	@echo "\n=== DB Migration Tests ==="
	cd backend && source .venv/bin/activate && \
	  python -m pytest tests/test_migrations.py -v --tb=short

test-e2e:
	@echo "\n=== E2E Tests (requires running stack) ==="
	cd tests/e2e && npx playwright test

# --- Linting ---

lint: lint-backend lint-frontend
	@echo "\n=== All linting passed ==="

lint-backend:
	@echo "\n=== Backend Lint (ruff) ==="
	cd backend && source .venv/bin/activate && \
	  python -m ruff check . --config pyproject.toml

lint-frontend:
	@echo "\n=== Frontend Lint (eslint) ==="
	cd frontend-v2 && npm run lint

# --- Coverage ---

coverage:
	@echo "\n=== Backend Coverage ==="
	cd backend && source .venv/bin/activate && \
	  python -m pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=60
	@echo "\n=== Frontend Coverage ==="
	cd frontend-v2 && npm run test:coverage

# --- Git Hooks ---

install-hooks:
	@echo "Installing git hooks..."
	cp .githooks/pre-commit .git/hooks/pre-commit
	cp .githooks/pre-push .git/hooks/pre-push
	chmod +x .git/hooks/pre-commit .git/hooks/pre-push
	@echo "Hooks installed."

# --- Help ---

help:
	@echo "BNK-Forge v2 Test Commands"
	@echo ""
	@echo "  make test          Run all tests (lint + unit + integration)"
	@echo "  make test-backend  Run backend tests only"
	@echo "  make test-frontend Run frontend tests only"
	@echo "  make test-proxy    Run proxy config tests only"
	@echo "  make test-operator Run operator tests only"
	@echo "  make test-db       Run DB migration tests only"
	@echo "  make test-e2e      Run E2E tests (requires running stack)"
	@echo "  make lint          Run all linters"
	@echo "  make coverage      Run tests with coverage reporting"
	@echo "  make install-hooks Install pre-commit and pre-push hooks"
```

### 4.2 Git Hooks

Two git hooks enforce quality gates:

**`.githooks/pre-commit`** — Fast checks on every commit (~5-10s):
```bash
#!/bin/bash
# Pre-commit: lint only (fast)
set -e

echo "=== Pre-commit: Linting ==="

# Backend lint (ruff) — only if backend files changed
if git diff --cached --name-only | grep -q "^backend/"; then
  echo "  Backend files changed — running ruff..."
  cd backend && source .venv/bin/activate && python -m ruff check . --config pyproject.toml
  cd ..
fi

# Frontend lint (eslint) — only if frontend files changed
if git diff --cached --name-only | grep -q "^frontend-v2/"; then
  echo "  Frontend files changed — running eslint..."
  cd frontend-v2 && npm run lint
  cd ..
fi

echo "=== Lint passed ==="
```

**`.githooks/pre-push`** — Full test suite before push (~60-90s):
```bash
#!/bin/bash
# Pre-push: full test suite
set -e

echo "=== Pre-push: Full test suite ==="

# Backend tests
echo "  Running backend tests..."
cd backend && source .venv/bin/activate && python -m pytest tests/ -q --tb=short
cd ..

# Frontend tests
echo "  Running frontend tests..."
cd frontend-v2 && npm test -- --run
cd ..

# Proxy tests
echo "  Running proxy tests..."
cd backend && source .venv/bin/activate && python -m pytest tests/test_proxy_config.py -q --tb=short
cd ..

# Operator tests
echo "  Running operator tests..."
cd bnk-operator && source .venv/bin/activate && python -m pytest tests/ -q --tb=short
cd ..

# DB migration tests
echo "  Running migration tests..."
cd backend && source .venv/bin/activate && python -m pytest tests/test_migrations.py -q --tb=short
cd ..

echo "=== All tests passed — pushing ==="
```

**Installation:** `make install-hooks`

### 4.3 Flow

```
Developer writes code
        │
        ▼
   git commit ──────► pre-commit hook ──► lint (ruff + eslint)
        │                                    │
        │                              FAIL? ─► Fix and retry
        │                                    │
        │                              PASS ─► Commit created
        │
        ▼
   git push ────────► pre-push hook ──► full test suite
        │                                    │
        │                              FAIL? ─► Fix, commit, retry
        │                                    │
        │                              PASS ─► Push to origin
        │
        ▼
   GitHub CI ───────► backend tests
                      frontend lint + tests + build
                      proxy validation
                      operator tests
                      DB migration check
                      docker build
                      security audit
                      trivy scan
                           │
                     ALL GREEN? ──► Merge allowed
                     ANY RED?  ──► Block merge, fix required
```

---

## 5. Implementation Plan — Phased Approach

### Phase 1: Foundation (Est. 2-3 hours)

**Goal:** Single-command test runner + git hooks. After this, every push is gated.

| Task | Files | Description |
|------|-------|-------------|
| Create Makefile | `Makefile` | All test/lint/coverage commands |
| Create pre-commit hook | `.githooks/pre-commit` | Lint on commit |
| Create pre-push hook | `.githooks/pre-push` | Full suite on push |
| Add `install-hooks` target | `Makefile` | Copy hooks + chmod |

### Phase 2: Fix Broken Tests (Est. 1-2 hours)

**Goal:** 866 tests collected, 866 passing, 0 skipped.

| Task | Fix |
|------|-----|
| 11 skipped `test_operator_production.py` | Ensure `pytest-asyncio` is installed in backend venv; verify `asyncio_mode = "auto"` in pyproject.toml works |
| 1 skipped `test_secrets_service.py` | Same async fix |
| 5 skipped `test_cli_arg_security.py` | Fix conditional import for operator validation functions |

### Phase 3: Proxy Tests (Est. 2-3 hours)

**Goal:** Validate nginx config correctness without running Docker.

| Test | What It Validates |
|------|-------------------|
| **Security headers present** | Parse nginx.conf, verify all 5 security headers |
| **Rate limit zones defined** | `login:10m rate=5r/m`, `api:10m rate=30r/s` |
| **Server tokens off** | `server_tokens off;` present |
| **Gzip enabled** | `gzip on;` with correct types |
| **Frontend routing** | `/` proxies to `bnk-forge-frontend:80` |
| **API routing** | `/api/` proxies to `bnk-forge-backend:8000` |
| **WebSocket routing** | `/ws/` proxies with Upgrade headers |
| **Login rate limit** | `/api/auth/login` uses `login` zone |
| **Timeout values** | API: 300s, WebSocket: 600s |
| **Client max body size** | `100M` |
| **DNS resolver** | `127.0.0.11 valid=30s` |
| **No hardcoded IPs** | All upstreams use `$variable_upstream` pattern |

**Implementation approach:** Python tests that parse `proxy/nginx.conf` as text and validate rules. Plus, when Docker is running, curl-based integration tests through the proxy.

**Files:** `backend/tests/test_proxy_config.py` (config parsing) + `tests/test_proxy_integration.py` (curl-based, skipped if no Docker)

### Phase 4: Operator Tests (Est. 4-6 hours)

**Goal:** 80+ tests covering all 6 operator modules.

**Structure:**
```
bnk-operator/
├── tests/
│   ├── __init__.py
│   ├── conftest.py                   # Shared fixtures, mock K8s client
│   ├── test_command_handlers.py      # ~35 tests
│   ├── test_control_plane_client.py  # ~15 tests
│   ├── test_polling_client.py        # ~12 tests
│   ├── test_health_server.py         # ~8 tests
│   ├── test_health_watcher.py        # ~8 tests
│   └── test_main.py                  # ~5 tests
├── requirements-dev.txt              # pytest, pytest-asyncio, aioresponses
```

**Test breakdown by module:**

| Module | Tests | Key Scenarios |
|--------|-------|---------------|
| `command_handlers.py` | ~35 | `_validate_cli_arg` (reject flags, allow normal), `_validate_helm_timeout` (valid/invalid), `apply_manifests` (empty, success counting, namespace default, wait timeout), `destroy_manifests` (reverse order, ignore_not_found), `install_helm` (missing params, OCI login, values cleanup), `uninstall_helm` ("not found" = success), `scan_cluster` (CRD detection), `get_health` (FLO/TMM), `get_resources` (namespace/label), `get_pod_logs` (missing pod), helpers (`_node_ready`, `_pod_ready`, `_deployment_ready`) |
| `control_plane_client.py` | ~15 | `_build_ssl_context` (no-wss, insecure, custom CA, missing CA), `_backoff_with_jitter` (exponential cap 60s, jitter range), `on_command` (register/lookup), `_handle_command` (dispatch, unknown action, duration tracking), registration handshake, heartbeat missed detection, `send_health` (no-op when disconnected) |
| `polling_client.py` | ~12 | `_register` (success/failure), `_poll_and_execute` (empty, 401, poll interval hints), `_execute_command` (dispatch, unknown action), `_submit_result` (retry 3x), `send_health` (no-op), `_build_ssl_context`, `_auth_headers` |
| `health_server.py` | ~8 | `/healthz` (always 200), `/readyz` (200 connected, 503 disconnected), `/metrics` (Prometheus format, correct gauges/counters), shutdown |
| `health_watcher.py` | ~8 | `_has_changed` (first=True, same=False, diff=True, serialization error), `_check_and_report` (first sends, unchanged skips, periodic force-send), `watch_and_report` (waits for connection, shutdown, error counting) |
| `main.py` | ~5 | `_create_client` (websocket/polling/invalid), missing env vars = error, signal handler sets shutdown |

### Phase 5: Database Migration Tests (Est. 1-2 hours)

**Goal:** Validate Alembic migrations don't break.

| Test | What It Validates |
|------|-------------------|
| **Head is current** | `alembic heads` returns single head (no forks) |
| **Upgrade to head** | `alembic upgrade head` on empty SQLite succeeds |
| **Downgrade all** | `alembic downgrade base` succeeds |
| **Round-trip** | upgrade head -> downgrade base -> upgrade head without errors |
| **No offline SQL errors** | `alembic upgrade --sql head` produces valid SQL |

**File:** `backend/tests/test_migrations.py`

### Phase 6: Backend Coverage Expansion (Est. 8-12 hours)

**Goal:** Backend from ~30% to 60%+ coverage.

**Priority order — test what matters most first:**

**Batch 1 — Core (P0, ~2 hours):**

| Target | Test File | Tests |
|--------|-----------|-------|
| `core/auth_middleware.py` | `test_core_auth.py` | JWT validation, role extraction, expired token, malformed token |
| `core/encryption.py` | `test_core_encryption.py` | Encrypt/decrypt roundtrip, key rotation, invalid key |
| `core/errors.py` | `test_core_errors.py` | Each error class, `@handle_route_errors` decorator |

**Batch 2 — Critical Services (P0, ~3 hours):**

| Target | Test File | Tests |
|--------|-----------|-------|
| `auth_service.py` | `test_auth_service.py` | Create token, hash/verify password, token decode |
| `project_crud_service.py` | `test_project_crud.py` | Create, update, delete, list, not-found |
| `project_module_service.py` | `test_project_module.py` | Add, remove, reorder, dependency validation |
| `execution/engine_router.py` | `test_engine_router.py` | Route to tofu/k8s/operator based on module type |
| `execution/task_dispatch.py` | `test_task_dispatch.py` | Dispatch to correct Celery queue |
| `workspace_manager.py` | `test_workspace_manager.py` | Create, cleanup, lock acquisition |

**Batch 3 — Tasks (P0, ~2 hours):**

| Target | Test File | Tests |
|--------|-----------|-------|
| `tasks/opentofu_tasks.py` | `test_opentofu_tasks.py` | Success, failure, retry, timeout, cancel |
| `tasks/kubernetes_tasks.py` | `test_kubernetes_tasks.py` | Apply, destroy, fallback |
| `tasks/parallel_tasks.py` | `test_parallel_tasks.py` | Layer execution, failure propagation, skip |

**Batch 4 — P0 Routes (~2 hours):**

| Target | Test File | Tests |
|--------|-----------|-------|
| `routes/project_secrets.py` | `test_routes_secrets.py` | CRUD + RBAC |
| `routes/project_variables.py` | `test_routes_variables.py` | CRUD + RBAC |
| `routes/project_modules.py` | `test_routes_modules.py` | CRUD + RBAC |
| `routes/helm.py` | `test_routes_helm.py` | All Helm endpoints + RBAC |
| `routes/project_orchestration.py` | `test_routes_orchestration.py` | Deploy/destroy orchestration |

**Batch 5 — P1 Services + Routes (~3 hours):**

Remaining services from the P1 list above. Focus on services that affect production safety.

### Phase 7: Frontend Coverage Expansion (Est. 4-6 hours)

**Goal:** Frontend from ~15% to 30%+ coverage.

**Batch 1 — P0 Hooks (~2 hours):**

| Hook | New MSW Handlers Needed |
|------|------------------------|
| `useModules` | Module CRUD endpoints |
| `useHelm` | Already have handlers |
| `useDeployments` | Deployment list/trigger |
| `useProjectSecrets` | Already have handlers |

**Batch 2 — Key Lib Utilities (~1 hour):**

| Utility | Why |
|---------|-----|
| `validators.ts` | Input validation affects security |
| `time-utils.ts` | Date formatting used everywhere |
| `health-utils.ts` | Health status interpretation |
| `queryKeys.ts` | Query key correctness prevents stale cache |

**Batch 3 — P1 Hooks (~2 hours):**

`useK8sClusters`, `useK8sResources`, `useOperators`, `useStacks`, `useParallelExecution`, `useConfigPromotion`

**Batch 4 — Components (~1 hour):**

Focus on components with logic (not just UI):
- `CommandPalette` — keyboard navigation, search
- `layout/` — route rendering, auth redirects
- `providers/` — context wiring

### Phase 8: CI Hardening (Est. 2-3 hours)

**Goal:** CI runs everything the pre-push hook runs, plus Docker build and security checks.

**Updated `.github/workflows/ci.yml` — new jobs to add:**

```yaml
  # --- NEW: Proxy validation ---
  proxy-validation:
    name: Proxy Config Validation
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install test deps
        run: pip install pytest
      - name: Validate proxy config
        run: pytest backend/tests/test_proxy_config.py -v

  # --- NEW: Operator tests ---
  operator-tests:
    name: Operator Tests
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install dependencies
        run: |
          pip install -r bnk-operator/requirements.txt
          pip install -r bnk-operator/requirements-dev.txt
      - name: Run operator tests
        run: pytest bnk-operator/tests/ -v --tb=short

  # --- NEW: DB migration check ---
  db-migration-check:
    name: DB Migration Validation
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install dependencies
        run: pip install -r backend/requirements.txt
      - name: Validate migrations
        run: pytest backend/tests/test_migrations.py -v
        env:
          DATABASE_URL: "sqlite:///./test.db"
          SECRET_KEY: "ci-test-secret-key"
```

**Changes to existing jobs:**
- `backend-tests`: Change `--cov-fail-under=0` to `--cov-fail-under=60`
- `frontend-checks`: Change `npm test -- --run` to `npm run test:coverage`

---

## 6. Coverage Thresholds — Ratchet Strategy

We never lower thresholds. We only raise them as tests are added.

| Phase | Backend `fail_under` | Frontend Statements | Frontend Branches |
|-------|---------------------|--------------------|--------------------|
| **Current** | 40% | 15% | 10% |
| **After Phase 6 Batch 1-2** | 50% | 15% | 10% |
| **After Phase 6 Batch 3-5** | 60% | 15% | 10% |
| **After Phase 7 Batch 1-2** | 60% | 25% | 15% |
| **After Phase 7 Batch 3-4** | 60% | 30% | 20% |
| **Future (3 months)** | 70% | 40% | 30% |
| **Future (6 months)** | 80% | 50% | 40% |

**Enforcement files:**
- Backend: `backend/pyproject.toml` -> `[tool.coverage.report]` -> `fail_under`
- Frontend: `frontend-v2/vitest.config.ts` -> `test.coverage.thresholds`
- CI: `--cov-fail-under=X` in GitHub Actions

---

## 7. Test Writing Guidelines

### 7.1 Backend Test Conventions

```python
# File naming: test_<module_name>.py
# Class naming: Test<FeatureName>
# Method naming: test_<action>_<scenario>

class TestProjectCRUD:
    """Tests for project CRUD operations."""

    def test_create_project_returns_201(self, client, admin_headers):
        """POST /api/projects with valid data returns 201."""
        response = client.post("/api/projects", json={...}, headers=admin_headers)
        assert response.status_code == 201

    def test_create_project_viewer_returns_403(self, client, viewer_headers):
        """Viewers cannot create projects."""
        response = client.post("/api/projects", json={...}, headers=viewer_headers)
        assert response.status_code == 403

    def test_create_project_missing_name_returns_422(self, client, admin_headers):
        """Missing required field returns 422."""
        response = client.post("/api/projects", json={}, headers=admin_headers)
        assert response.status_code == 422
```

**Rules:**
- Use the `client`, `admin_headers`, `db`, and factory fixtures from `conftest.py`
- Mock external services (K8s, AWS, subprocess) — never call real infrastructure
- Each test is independent — no shared state between tests
- Test the happy path AND error paths (401, 403, 404, 422, 500)
- RBAC: test admin, operator, and viewer for every write endpoint

### 7.2 Frontend Test Conventions

```typescript
// File: useModules.test.ts
import { renderHook, waitFor } from '@testing-library/react';
import { createWrapper } from '../../test/test-utils';
import { useModules } from '../useModules';

describe('useModules', () => {
  it('fetches project modules', async () => {
    const { result } = renderHook(
      () => useModules('project-1'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
  });

  it('returns empty array for project with no modules', async () => {
    // Override MSW handler for this test
    server.use(
      http.get('*/api/projects/:id/modules', () => {
        return HttpResponse.json([]);
      })
    );

    const { result } = renderHook(
      () => useModules('project-1'),
      { wrapper: createWrapper() }
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(0);
  });
});
```

**Rules:**
- Use `createWrapper()` from `test/test-utils.tsx` for React Query + Router context
- Use MSW handlers from `test/mocks/handlers.ts` — add new handlers as needed
- Override handlers per-test with `server.use()` for edge cases
- Test loading, success, and error states for every hook
- Component tests should test user interactions, not implementation details

### 7.3 Operator Test Conventions

```python
# Use pytest-asyncio for async tests
# Mock kr8s API client and subprocess for Helm CLI

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from command_handlers import CommandHandlers

@pytest.fixture
def handlers():
    """CommandHandlers with mocked K8s client."""
    with patch('command_handlers.kr8s') as mock_kr8s:
        mock_api = AsyncMock()
        mock_kr8s.asyncio.Api.return_value = mock_api
        h = CommandHandlers(cluster_name="test-cluster")
        h._api = mock_api
        yield h

class TestApplyManifests:
    @pytest.mark.asyncio
    async def test_empty_manifests_returns_early(self, handlers):
        result = await handlers.apply_manifests({"manifests": []})
        assert result["success"] is True
        assert result["applied"] == 0
```

### 7.4 Proxy Test Conventions

```python
# Parse nginx.conf as text — no nginx binary needed

import re
from pathlib import Path

NGINX_CONF = Path(__file__).parent.parent.parent / "proxy" / "nginx.conf"

class TestProxySecurityHeaders:
    def setup_method(self):
        self.conf = NGINX_CONF.read_text()

    def test_x_frame_options_present(self):
        assert 'X-Frame-Options "SAMEORIGIN"' in self.conf

    def test_content_type_nosniff(self):
        assert 'X-Content-Type-Options "nosniff"' in self.conf

class TestProxyRateLimiting:
    def setup_method(self):
        self.conf = NGINX_CONF.read_text()

    def test_login_rate_limit_5_per_minute(self):
        assert re.search(r'limit_req_zone.*zone=login.*rate=5r/m', self.conf)
```

---

## 8. What "Always Green" Means — The Contract

### 8.1 Definition

> **All tests pass at HEAD on main. Always. No exceptions.**

If a test is red:
1. **Stop other work** — the red test is the highest priority
2. **Diagnose** — is it a regression, a flaky test, or a test that needs updating?
3. **Fix** — either fix the code or fix the test
4. **Never skip** — `@pytest.mark.skip` is only for tests that are being actively worked on with a linked issue

### 8.2 When Tests Change

| Scenario | Action |
|----------|--------|
| **New feature** | Write tests first or alongside. Coverage must not decrease. |
| **Bug fix** | Write a test that reproduces the bug, then fix it. |
| **Refactor** | Existing tests must still pass. If behavior changes, update tests. |
| **Code deletion** | Delete corresponding tests. Coverage may decrease temporarily. |
| **Flaky test** | Fix immediately. If it can't be fixed quickly, quarantine with `@pytest.mark.flaky` and a linked issue. |

### 8.3 CI as the Source of Truth

- Local tests are a convenience — CI is what matters
- If it passes locally but fails in CI, the CI failure is the real bug
- If it passes in CI but fails locally, fix your local environment
- Every push must have ALL CI jobs green

---

## 9. New Files to Create

```
# Phase 1 — Foundation
Makefile                                 # Test runner entry point
.githooks/pre-commit                     # Lint on commit
.githooks/pre-push                       # Full suite on push

# Phase 3 — Proxy
backend/tests/test_proxy_config.py       # nginx.conf parsing tests (~20 tests)

# Phase 4 — Operator
bnk-operator/tests/__init__.py
bnk-operator/tests/conftest.py           # Shared fixtures, mock K8s
bnk-operator/tests/test_command_handlers.py   # ~35 tests
bnk-operator/tests/test_control_plane_client.py  # ~15 tests
bnk-operator/tests/test_polling_client.py     # ~12 tests
bnk-operator/tests/test_health_server.py      # ~8 tests
bnk-operator/tests/test_health_watcher.py     # ~8 tests
bnk-operator/tests/test_main.py               # ~5 tests
bnk-operator/requirements-dev.txt             # pytest, pytest-asyncio, aioresponses

# Phase 5 — DB
backend/tests/test_migrations.py         # Alembic roundtrip tests (~5 tests)

# Phase 6 — Backend expansion
backend/tests/test_core_auth.py          # Auth middleware
backend/tests/test_core_encryption.py    # Encryption
backend/tests/test_core_errors.py        # Error handling
backend/tests/test_auth_service.py       # Auth service
backend/tests/test_project_crud.py       # Project CRUD service
backend/tests/test_project_module.py     # Module service
backend/tests/test_engine_router.py      # Engine routing
backend/tests/test_task_dispatch.py      # Task dispatch
backend/tests/test_workspace_manager.py  # Workspace management
backend/tests/test_opentofu_tasks.py     # Celery tasks
backend/tests/test_kubernetes_tasks.py   # Celery tasks
backend/tests/test_parallel_tasks.py     # Celery tasks
backend/tests/test_routes_secrets.py     # Secrets route
backend/tests/test_routes_variables.py   # Variables route
backend/tests/test_routes_modules.py     # Modules route
backend/tests/test_routes_helm.py        # Helm route
backend/tests/test_routes_orchestration.py  # Orchestration route

# Phase 7 — Frontend expansion
frontend-v2/src/hooks/__tests__/useModules.test.ts
frontend-v2/src/hooks/__tests__/useHelm.test.ts
frontend-v2/src/hooks/__tests__/useDeployments.test.ts
frontend-v2/src/hooks/__tests__/useProjectSecrets.test.ts
frontend-v2/src/hooks/__tests__/useK8sClusters.test.ts
frontend-v2/src/hooks/__tests__/useK8sResources.test.ts
frontend-v2/src/hooks/__tests__/useOperators.test.ts
frontend-v2/src/hooks/__tests__/useStacks.test.ts
frontend-v2/src/hooks/__tests__/useParallelExecution.test.ts
frontend-v2/src/hooks/__tests__/useConfigPromotion.test.ts
frontend-v2/src/lib/__tests__/validators.test.ts
frontend-v2/src/lib/__tests__/time-utils.test.ts
frontend-v2/src/lib/__tests__/health-utils.test.ts
frontend-v2/src/lib/__tests__/queryKeys.test.ts
```

## 10. Files to Modify

```
# Phase 2 — Fix skipped tests
backend/tests/test_operator_production.py   # Fix async test config
backend/tests/test_secrets_service.py       # Fix async test config
backend/tests/test_cli_arg_security.py      # Fix conditional imports

# Phase 6
backend/pyproject.toml                      # Raise fail_under: 40 → 50 → 60

# Phase 7
frontend-v2/vitest.config.ts               # Raise thresholds: 15 → 25 → 30
frontend-v2/src/test/mocks/handlers.ts      # Add MSW handlers for new hooks

# Phase 8
.github/workflows/ci.yml                   # Add proxy, operator, DB jobs
```

---

## 11. Estimated Effort

| Phase | Description | Estimate | Dependencies |
|-------|-------------|----------|--------------|
| **Phase 1** | Makefile + Git hooks | 2-3h | None |
| **Phase 2** | Fix 17 skipped tests | 1-2h | None |
| **Phase 3** | Proxy tests | 2-3h | Phase 1 |
| **Phase 4** | Operator tests | 4-6h | Phase 1 |
| **Phase 5** | DB migration tests | 1-2h | Phase 1 |
| **Phase 6** | Backend coverage 60% | 8-12h | Phase 2 |
| **Phase 7** | Frontend coverage 30% | 4-6h | None |
| **Phase 8** | CI hardening | 2-3h | Phases 3-7 |
| **Total** | | **24-37 hours** | |

Phases 1-5 can be done in parallel (independent). Phases 6-7 are the bulk of the work. Phase 8 ties it all together.

---

## 12. Success Criteria

When this plan is fully implemented, the following must all be true:

- [ ] `make test` runs all tests locally in <90 seconds and passes
- [ ] `make lint` passes with 0 errors
- [ ] `make coverage` shows backend >=60%, frontend >=30%
- [ ] `git commit` triggers pre-commit hook (lint only, ~5-10s)
- [ ] `git push` triggers pre-push hook (full suite, ~60-90s)
- [ ] GitHub CI has 7+ jobs: backend, frontend, proxy, operator, db, docker-build, security
- [ ] All CI jobs are green on main
- [ ] 0 skipped tests in backend
- [ ] 0 skipped tests in operator
- [ ] Proxy config is validated (security headers, rate limits, routing)
- [ ] Alembic migrations round-trip without errors
- [ ] Coverage thresholds are enforced — build fails if coverage drops

---

## Appendix A: Current Test File Inventory

### Backend (32 test files, 866 collected, 849 pass, 17 skip)

| # | Test File | Type | Status |
|---|-----------|------|--------|
| 1 | `test_adaptive_module_selector.py` | Unit | Pass |
| 2 | `test_api_security.py` | Security | Pass |
| 3 | `test_bnk_upgrade_service.py` | Unit | Pass |
| 4 | `test_builtin_module_seeder.py` | Unit | Pass |
| 5 | `test_cli_arg_security.py` | Security | **5 skip** |
| 6 | `test_critical_path_e2e.py` | E2E | Pass |
| 7 | `test_dependency_graph.py` | Unit | Pass |
| 8 | `test_engine_modules.py` | Unit | Pass |
| 9 | `test_factories.py` | Infra | Pass |
| 10 | `test_git_injection.py` | Security | Pass |
| 11 | `test_helm_namespace_security.py` | Security | Pass |
| 12 | `test_helm_revisions_security.py` | Security | Pass |
| 13 | `test_helm_security.py` | Security | Pass |
| 14 | `test_helm_service.py` | Unit | Pass |
| 15 | `test_helm_zip_slip.py` | Security | Pass |
| 16 | `test_k8s_drift_service.py` | Unit | Pass |
| 17 | `test_kubernetes_engine.py` | Unit | Pass |
| 18 | `test_opentofu_runtime.py` | Unit | Pass |
| 19 | `test_operator_production.py` | Unit | **11 skip** |
| 20 | `test_routes_auth.py` | Integration | Pass |
| 21 | `test_routes_execution.py` | Integration | Pass |
| 22 | `test_routes_k8s.py` | Integration | Pass |
| 23 | `test_routes_projects.py` | Integration | Pass |
| 24 | `test_routes_rbac.py` | Integration | Pass |
| 25 | `test_routes_stacks.py` | Integration | Pass |
| 26 | `test_routes_system.py` | Integration | Pass |
| 27 | `test_secrets_service.py` | Unit | **1 skip** |
| 28 | `test_variable_assembler.py` | Unit | Pass |
| 29 | `test_variable_transform_security.py` | Security | Pass |

### Frontend (14 test files, 154 tests, 154 pass)

| # | Test File | Type | Tests |
|---|-----------|------|-------|
| 1 | `ErrorBoundary.test.tsx` | Component | 13 |
| 2 | `ErrorHandler.test.ts` | Utility | 23 |
| 3 | `Login.test.tsx` | Component | 7 |
| 4 | `Notifications.test.tsx` | Component | 14 |
| 5 | `ProjectCreate.test.tsx` | Component | 11 |
| 6 | `RoleGuard.test.tsx` | Component | 10 |
| 7 | `useAuth.test.ts` | Hook | 11 |
| 8 | `useDrift.test.ts` | Hook | ~8 |
| 9 | `useFleet.test.ts` | Hook | ~8 |
| 10 | `useK8s.test.ts` | Hook | 8 |
| 11 | `useProjects.test.ts` | Hook | ~10 |
| 12 | `useRunbooks.test.ts` | Hook | ~10 |
| 13 | `useSnapshots.test.ts` | Hook | 13 |
| 14 | `useTasks.test.ts` | Hook | ~8 |

### Other Areas

| Area | Test Files | Status |
|------|-----------|--------|
| Proxy | 0 | Nothing |
| Operator | 0 | Nothing |
| DB Migrations | 0 | Nothing |
| E2E | 1 spec (broken) | Step 2 fails (dialog scroll issue) |

---

## Appendix B: MSW Mock Handler Coverage

### Currently Mocked (27 handlers):

Projects (4), Auth (3), System (2), K8s (4), Tasks (2), Credentials (1), Notifications (1), Modules (4), Helm (4), Secrets (3), Stacks (2), Operators (1), Drift (2), Module Library (1), Parallel Exec (2)

### Need to Add for Phase 7:

- `*/api/projects/:id/variables` — Variable CRUD (GET/POST/PUT/DELETE)
- `*/api/projects/:id/variable-mappings` — Variable wiring
- `*/api/k8s/clusters/:id/resources` — K8s resource operations
- `*/api/k8s/clusters/:id/pods/:name/logs` — Pod log streaming
- `*/api/operators/:id/commands` — Operator commands
- `*/api/config-promotion/*` — Config promotion endpoints
- `*/api/snapshots/*` — Snapshot CRUD
- `*/api/cost/*` — Cost estimation
- `*/api/settings/*` — Settings endpoints
