# BNK Forge — Testing Guide

Complete guide to the test infrastructure, patterns, and how to run tests.

> **Last verified: 2026-03-31.** Test counts and coverage evolve frequently; prefer Make targets and CI over hard-coded totals.

---

## Table of Contents

- [Quick Reference](#quick-reference)
- [Test Suite Overview](#test-suite-overview)
- [Running Tests](#running-tests)
- [Backend Tests](#backend-tests)
- [Frontend Tests](#frontend-tests)
- [E2E Tests](#e2e-tests)
- [BNK Operator Tests](#bnk-operator-tests)
- [CI Pipeline](#ci-pipeline)
- [Writing New Tests](#writing-new-tests)
- [Contract Testing (CT-012)](#contract-testing-ct-012)
- [Common Gotchas](#common-gotchas)
- [Keeping This Document Current](#keeping-this-document-current)

---

## Quick Reference

```bash
# Run everything (~90s)
make pre-push

# Quick check before commits (~15s)
make quick-check

# Individual suites
make test-backend-unit       # Backend unit tests only
make test-backend-component  # Backend component tests (with DB)
make test-frontend           # Frontend vitest
make test                    # All suites sequentially

# Coverage
make coverage                # Backend + frontend coverage reports
```

---

## Test Suite Overview

| Suite | Tool | Scope | What It Tests |
|-------|------|-------|---------------|
| Backend Unit | pytest | `backend/tests/unit/` | Pure logic: schemas, services, parsers, validators |
| Backend Component | pytest | `backend/tests/component/` | Services with real SQLite DB, mocked externals |
| Backend Integration | pytest | `backend/tests/integration/` | Full HTTP via FastAPI TestClient |
| Backend Legacy | pytest | flat `backend/tests/test_*.py` | Older tests retained outside the newer split layout |
| Backend Contract | pytest | `backend/tests/contract/` | Golden response-shape verification for Tier-1 APIs |
| Frontend | vitest | `frontend-v2/src/**/*.{test,spec}.{ts,tsx}` | Hooks, components, pages with MSW |
| E2E | Playwright | `tests/e2e/` | Full stack browser tests |
| BNK Operator | pytest | `bnk-operator/tests/` | Legacy/secondary operator command handlers and health |

---

## Running Tests

### Makefile Targets

| Target | Duration | What It Does |
|--------|----------|--------------|
| `make quick-check` | ~15s | Lint (ruff + eslint) + mypy + OpenAPI types freshness |
| `make pre-push` | ~90s | quick-check + 9 parallel test suites + tsc + frontend build |
| `make push` | ~90s + push | pre-push + git push + watch CI |
| `make test` | ~3m | All suites sequentially (lint + all backend + frontend + proxy + operator + db) |
| `make test-backend` | ~60s | All backend pytest tests |
| `make test-backend-unit` | ~15s | `pytest backend/tests/unit/` |
| `make test-backend-component` | ~30s | `pytest backend/tests/component/` |
| `make test-backend-legacy` | ~15s | Legacy tests at `backend/tests/` root |
| `make test-frontend` | ~30s | `npm test -- --run` (vitest) |
| `make test-proxy` | ~5s | `pytest tests/test_proxy_config.py` |
| `make test-operator` | ~5s | `pytest` in `bnk-operator/` (skips if no venv) |
| `make test-db` | ~5s | `pytest tests/test_migrations.py` |
| `make test-integration-full` | ~5m | Full-stack integration (requires running Docker) |
| `make test-e2e` | ~5m | Playwright E2E tier 1 |
| `make test-e2e-tier2` | ~60m | Playwright E2E tier 2 (requires AWS) |
| `make coverage` | ~2m | Coverage reports for backend + frontend |
| `make lint` | ~10s | Lint backend (ruff) + frontend (eslint) |
| `make typecheck-backend` | ~15s | mypy on `core/` and `schemas/` |
| `make typecheck-frontend` | ~15s | tsc --noEmit |

### Git Hooks

Install with `make install-hooks`:

- **pre-commit** (~5-10s) — Runs ruff/eslint only on changed files
- **pre-push** (~2-3m) — Delegates to `make pre-push`

### Mandatory Workflow

```
Before every commit:   make quick-check     (~15s)
Before every push:     make pre-push        (~90s)
```

**CI is for validation, not discovery.** If it fails in CI, it should have been caught locally first.

---

## Backend Tests

### Directory Structure

```
backend/tests/
├── conftest.py              # Root fixtures: DB engine, client, auth, factories
├── factories.py             # Model factories (User, Project, Module, Task, etc.)
├── mocks/                   # Mock modules
│   ├── aws_mock.py
│   ├── kubernetes_mock.py
│   ├── redis_mock.py
│   └── subprocess_mock.py
├── unit/                    # 31 files — pure logic, no DB
│   ├── conftest.py          # temp_encryption_key, mock_settings
│   ├── test_schemas_*.py    # Pydantic schema validation
│   ├── test_core_*.py       # Encryption, config, errors
│   ├── test_bnk_*.py        # BNK health, topology, backends
│   └── test_scanner_*.py    # Cluster scanner components
├── component/               # 65 files — services + real SQLite DB
│   ├── conftest.py          # mock_celery, project_with_modules
│   └── test_*_service.py    # Service-level tests
├── integration/             # 46 files — full HTTP via TestClient
│   ├── conftest.py          # mock_celery_dispatch, sample entities
│   ├── conftest_full.py     # Live Docker stack fixtures
│   └── test_routes_*.py     # Route-level HTTP tests
└── (32 legacy test files)   # Flat test_*.py at root level
```

### Configuration

In `backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "backend/tests"]
pythonpath = ["backend"]
markers = ["slow", "integration", "unit", "component", "security", "fast", "full"]
addopts = "-v --tb=short -m 'not full'"
```

- Tests marked `full` are excluded by default (require running Docker stack)
- Coverage threshold: 50% (`fail_under = 50`)

### Fixture Architecture

**Root `conftest.py`** — shared across all test categories:

| Fixture | Scope | Description |
|---------|-------|-------------|
| `engine` | session | SQLite in-memory with `StaticPool`, foreign keys ON |
| `db` | function | Transactional session (rolls back after each test) |
| `client` | function | FastAPI `TestClient` with DB override + mocked Redis |
| `admin_headers` | function | JWT auth headers for admin user |
| `operator_headers` | function | JWT auth headers for operator user |
| `viewer_headers` | function | JWT auth headers for viewer user |
| `sample_user` | function | Pre-created admin user |
| `sample_project` | function | Pre-created project |
| `make_user` | function | Factory: create user with custom attrs |
| `make_project` | function | Factory: create project |
| `make_module_library` | function | Factory: create module in library |
| `make_project_module` | function | Factory: add module to project |
| `make_task` | function | Factory: create task |
| `make_stack_template` | function | Factory: create stack template |
| `make_k8s_cluster` | function | Factory: create K8s cluster |
| `mock_cache` | function | `MockCacheService` replacing Redis |
| `mock_tofu` | function | Patched subprocess.run for OpenTofu |

**Unit conftest** adds: `temp_encryption_key`, `mock_settings`

**Component conftest** adds: `mock_celery`, `module_library`, `project_with_modules`

**Integration conftest** adds: `mock_celery_dispatch`, `sample_module`, `sample_task`

### Test Patterns

**Unit tests** — No DB, no mocks (or minimal):
```python
class TestProjectSchema:
    def test_valid_project_create(self):
        data = ProjectCreate(name="test", project_type="aws", region="us-west-2")
        assert data.name == "test"
    
    def test_invalid_name_raises(self):
        with pytest.raises(ValidationError):
            ProjectCreate(name="", project_type="aws")
```

**Component tests** — Real DB, mocked externals:
```python
class TestProjectService:
    def test_create_project(self, db, sample_user):
        project = ProjectService.create(db, name="test", owner_id=sample_user.id)
        assert project.name == "test"
        assert project.user_id == sample_user.id
```

**Integration tests** — Full HTTP:
```python
class TestProjectRoutes:
    def test_list_projects(self, client, admin_headers, sample_project):
        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["projects"]) >= 1
```

---

## Frontend Tests

### Directory Structure

```
frontend-v2/src/
├── test/                          # Test infrastructure
│   ├── setup.ts                   # MSW server, jest-dom, browser mocks
│   ├── test-utils.tsx             # Custom render with providers
│   ├── test-fixtures.ts           # Shared mock data
│   └── mocks/
│       ├── server.ts              # MSW setupServer
│       └── handlers.ts            # 962-line default API handlers
├── hooks/__tests__/               # 35 hook test files
├── hooks/k8s/__tests__/           # 6 K8s hook tests
├── lib/__tests__/                 # 15 utility tests
├── lib/api/__tests__/             # 17 API client tests
├── schemas/__tests__/             # Schema validation tests
├── stores/__tests__/              # Store tests
├── components/**/__tests__/       # ~132 component tests
└── pages/**/__tests__/            # ~25 page tests
```

### Configuration

In `frontend-v2/vitest.config.ts`:

```typescript
export default defineConfig({
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      thresholds: { statements: 25, branches: 15, functions: 15, lines: 25 }
    }
  }
});
```

### Test Infrastructure

**`setup.ts`** — Runs before all tests:
- Imports `@testing-library/jest-dom` matchers
- Starts MSW `server.listen()` before all
- Resets handlers + cleanup after each
- Mocks: `matchMedia`, `ResizeObserver`, `IntersectionObserver`

**`test-utils.tsx`** — Custom `render()`:
```typescript
// Wraps component with QueryClientProvider + MemoryRouter + ThemeProvider
// Intentionally omits NotificationProvider/WebSocketProvider
render(<Component />, { initialRoute: '/some-path' });
```

**`handlers.ts`** — 962 lines of MSW default handlers covering all major API endpoints.

**`test-fixtures.ts`** — Single source of truth for mock data objects.

### Test Patterns

**Hook tests** (`.test.ts`):
```typescript
describe('useProjects', () => {
  it('should fetch projects', async () => {
    const { result } = renderHook(() => useProjects(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
  });
});
```

**Component tests** (`.test.tsx`):
```typescript
describe('ProjectCard', () => {
  it('renders project name and status', () => {
    render(<ProjectCard project={mockProject} />);
    expect(screen.getByText('My Project')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
  });

  it('calls onDelete when delete button clicked', async () => {
    const onDelete = vi.fn();
    render(<ProjectCard project={mockProject} onDelete={onDelete} />);
    await userEvent.click(screen.getByRole('button', { name: /delete/i }));
    expect(onDelete).toHaveBeenCalledWith(mockProject.id);
  });
});
```

**Page integration tests** — Full page renders with routing and MSW.

---

## E2E Tests

### Structure

```
tests/e2e/
├── playwright.config.ts           # Config (base URL, retries, browser)
├── global-setup.ts                # Login + save auth state
├── tests/
│   ├── 00-smoke.spec.ts           # Basic health + login
│   ├── 01-project-lifecycle.spec.ts
│   ├── 02-module-management.spec.ts
│   ├── 03-stack-operations.spec.ts
│   ├── 04-deployment-workflow.spec.ts
│   ├── 05-kubernetes-explorer.spec.ts
│   ├── 06-helm-management.spec.ts
│   ├── 07-system-admin.spec.ts
│   ├── 08-rbac.spec.ts
│   ├── 09-error-handling.spec.ts
│   └── tier2/
│       └── 10-full-deployment.spec.ts
├── fixtures/                      # Test fixtures
├── pages/                         # Page object models
└── utils/                         # Helpers
```

### Tiers

| Tier | Tests | Requires | Duration |
|------|-------|----------|----------|
| Tier 1 | 00-09 | Running Docker stack | ~5 min |
| Tier 2 | 10 | Docker stack + AWS creds | ~30-60 min |

### Running

```bash
# Start the stack first
docker compose up -d

# Tier 1
make test-e2e

# Tier 2 (requires AWS credentials)
make test-e2e-tier2
```

See `tests/e2e/README.md` for detailed setup instructions.

---

## BNK Operator Tests

```
bnk-operator/tests/
├── conftest.py
├── test_command_handlers.py
├── test_control_plane_client.py
├── test_health_server.py
├── test_health_watcher.py
└── test_polling_client.py
```

`bnk-operator/` is still retained and CI-covered as a legacy/secondary-supported connectivity path. It is not safe to remove blindly while Makefile, CI, backend install-command support, and docs still reference it.

For local execution it uses a separate venv (`bnk-operator/.venv`). Config: `pytest.ini` with `asyncio_mode = auto`.

```bash
make test-operator   # Skips if venv doesn't exist
```

---

## CI Pipeline

`.github/workflows/ci.yml` — 4-phase pipeline with path-based filtering:

| Phase | Jobs | Duration | Runs When |
|-------|------|----------|-----------|
| **P1** | Lint backend, Lint frontend, Type check, Unit tests (BE+FE), Operator tests | ~60s | Backend/frontend/operator changes |
| **P2** | Component tests, Legacy tests, Frontend build | ~90s | After P1 |
| **P3** | Integration tests | ~2m | After P2 |
| **P4** | Security audit, Docker build | ~3m | After P3 |

Path filtering via `dorny/paths-filter` — jobs for unchanged areas are skipped.

**Skipped entirely** when only these paths change: `docs/`, `.agent/`, `*.md`, `.gitignore`, `Makefile`.

---

## Writing New Tests

### Backend: Adding a New Route Test

1. Identify the route file and its auth dependency
2. Write the test in the appropriate directory:
   - `unit/` for schema validation
   - `component/` for service logic
   - `integration/` for HTTP endpoint behaviour
3. Use existing fixtures (`admin_headers`, `make_project`, etc.)
4. Follow the naming convention: `test_{unit}_{condition}_{expected}`

```python
# backend/tests/integration/test_routes_new_feature.py
class TestNewFeatureRoutes:
    def test_create_requires_auth(self, client):
        response = client.post("/api/new-feature", json={"name": "test"})
        assert response.status_code == 401

    def test_create_returns_201(self, client, admin_headers):
        response = client.post(
            "/api/new-feature",
            json={"name": "test"},
            headers=admin_headers
        )
        assert response.status_code == 201
        assert response.json()["name"] == "test"
```

### Frontend: Adding a Hook Test

1. **Read the backend route first** — understand the request/response shape
2. Create `__tests__/useNewFeature.test.ts` next to the hook
3. Set up MSW handler with the real response shape
4. Use the CT-012 contract testing pattern (see below)

```typescript
// src/hooks/__tests__/useNewFeature.test.ts
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { createWrapper } from '@/test/test-utils';

describe('useNewFeature', () => {
  it('should fetch features', async () => {
    server.use(
      http.get('*/api/new-feature', () =>
        HttpResponse.json({ features: [{ id: 1, name: 'test' }] })
      )
    );
    const { result } = renderHook(() => useNewFeature(), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data.features).toHaveLength(1);
  });
});
```

### Frontend: Adding a Component Test

```typescript
// src/components/feature/__tests__/FeatureCard.test.tsx
import { render, screen } from '@/test/test-utils';
import { FeatureCard } from '../FeatureCard';

describe('FeatureCard', () => {
  it('renders feature name', () => {
    render(<FeatureCard feature={{ id: 1, name: 'Test' }} />);
    expect(screen.getByText('Test')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    render(<FeatureCard feature={null} isLoading />);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
  });
});
```

---

## Contract Testing (CT-012)

**The most important testing pattern in this project.**

Every frontend hook test that calls an API mutation **MUST** verify the request payload shape matches the backend Pydantic schema. This prevents the most common class of bugs: frontend sends wrong data, MSW returns canned success, test passes, production breaks.

### The Pattern

```typescript
describe('useCreateProject', () => {
  it('sends correct payload shape', async () => {
    // 1. Capture the request body
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/projects', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          // 2. Return the REAL backend response shape
          project: { id: 1, name: capturedBody?.name, status: 'active' },
          message: 'Project created',
        });
      })
    );

    // 3. Call the mutation
    const { result } = renderHook(() => useCreateProject(), { wrapper });
    await act(async () => {
      result.current.mutate({ name: 'Test', project_type: 'aws', region: 'us-west-2' });
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // 4. Assert payload matches backend Pydantic schema
    expect(capturedBody).toMatchObject({
      name: 'Test',
      project_type: 'aws',
      region: 'us-west-2',
    });
    // 5. Assert no accidental wrapping
    expect(capturedBody).not.toHaveProperty('request');
    expect(capturedBody).not.toHaveProperty('data');
  });
});
```

### Before Writing ANY Hook Test

1. Find the backend route file + line number
2. Read the Pydantic request model (check both `backend/schemas/` and inline in routes)
3. Read the actual return dict shape from the route handler
4. Write MSW handlers that return the REAL response shape
5. Capture `request.json()` in MSW and assert payload matches backend schema

---

## Common Gotchas

### Backend

- **SQLite vs PostgreSQL differences**: Tests use SQLite in-memory. Some PostgreSQL features (JSON operators, `ILIKE`, array columns) need special handling. The `conftest.py` engine uses `StaticPool` for performance.

- **ServiceRegistry resets between tests**: The root `conftest.py` resets `ServiceRegistry` after each test to prevent state leakage between tests.

- **`full` marker tests require Docker**: Tests marked `@pytest.mark.full` need the full Docker Compose stack running. They use `requests.Session` (not `TestClient`).

- **Celery is always mocked**: All test categories mock Celery task dispatch. Real Celery is never used in tests.

- **Factory sequence counters**: `factories.py` uses `_counters` dict for unique names. The counter persists across tests in the same session.

### Frontend

- **MSW handlers reset after each test**: `setup.ts` calls `server.resetHandlers()` in `afterEach`. Per-test overrides via `server.use()` are temporary.

- **Radix focus-scope flaky in CI**: `dispatchEvent` TypeError from `@radix-ui/react-focus-scope` when dialogs unmount. Fix: `vi.useFakeTimers({ shouldAdvanceTime: true })` + `vi.runOnlyPendingTimers()` in `afterEach`.

- **Custom render omits side-effect providers**: `test-utils.tsx` intentionally omits `NotificationProvider` and `WebSocketProvider` to avoid side effects in tests.

- **`waitFor` timeout**: Default timeout is 1000ms. For slow async operations, increase: `waitFor(() => ..., { timeout: 5000 })`.

### General

- **Dev environment is a liar**: 4,200 tests pass in dev but the production container might not even start. Dev compose mounts tools (git, tofu, helm) that the slim production image doesn't have. Tests never catch import errors or container startup failures. The ONLY proof is production site loading.

- **Lazy imports for CLI deps**: The slim API Docker target has no git/tofu. Services that import these at module level crash the API on startup. Use lazy imports inside functions.

---

## Keeping This Document Current

This document should be updated when:

- A new test directory or category is added
- Test configuration (pyproject.toml, vitest.config.ts) changes materially
- The test count crosses a major milestone (e.g., 5,000 tests)
- New testing patterns or conventions are established
- CI pipeline structure changes

> **Agent rule:** When adding new test infrastructure, patterns, or significantly changing test counts, update this file in the same commit.

### Verifying Test Counts

```bash
# Backend test files
find backend/tests -name "test_*.py" | wc -l

# Frontend test files
find frontend-v2/src -name "*.test.ts" -o -name "*.test.tsx" | wc -l

# Run counts
make test-backend 2>&1 | tail -1     # "X passed" line
npm test -- --run 2>&1 | tail -5     # vitest summary
```
