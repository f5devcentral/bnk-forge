# Sprint: Test Coverage & Build Performance

**Version:** 2.10.45  
**Created:** 2026-02-23  
**Status:** In Progress — Phase 1 Complete  

---

## Executive Summary

| Metric | Current | Target |
|--------|---------|--------|
| Backend test files | 19 | 45+ |
| Backend coverage | ~30% | 80%+ |
| Frontend test files | 14 | 50+ |
| Frontend coverage | ~15% | 75%+ |
| E2E test specs | 1 | 10+ |
| Build time (cold) | 10+ min | <3 min |
| Build time (hot/dev) | 10+ min | <10 sec |
| Test run time | N/A | <3 min (unit), <10 min (E2E) |

---

## CRITICAL: Build Performance Problem

### Current State (BROKEN)

The `upgrade.sh` script uses `docker compose build --no-cache` which takes **10+ minutes** for a single code change. This is unacceptable for development.

**Root Cause:** The Dockerfile downloads and installs ~500MB of tooling on every build:
- AWS CLI v2 (~200MB)
- OpenTofu (~80MB)
- Helm (~50MB)
- kubectl (~50MB)
- Docker CLI (~50MB)
- Python dependencies (~100MB)

**Current Dockerfile Issues:**
1. Base image does too much work (lines 1-89)
2. Tool downloads happen every build with `--no-cache`
3. No separation between "tooling" and "code" layers
4. pip-builder stage helps but base-image rebuild kills it

### Solution: Multi-Stage Build with Cached Tooling

```dockerfile
# Stage 1: Tooling image (rebuild rarely - only when tool versions change)
FROM python:3.11-slim AS tooling
# Install ALL tools here: aws, tofu, helm, kubectl, docker-cli
# This layer is cached and reused across builds

# Stage 2: Dependencies (rebuild when requirements.txt changes)
FROM tooling AS deps
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

# Stage 3: Application (rebuild on every code change - FAST)
FROM deps AS app
COPY . ./
# This is the only layer that rebuilds on code changes
```

### Implementation Tasks

| Task | Priority | Time Est. |
|------|----------|-----------|
| Create `tooling` base image, publish to registry | P0 | 2h |
| Refactor Dockerfile to 3-stage build | P0 | 1h |
| Remove `--no-cache` from upgrade.sh | P0 | 5m |
| Add dev mode with volume mounts (no rebuild) | P1 | 1h |
| Create `docker-compose.dev.yml` for development | P1 | 30m |
| Document build process in README | P1 | 30m |

### Dev Mode (No Rebuild)

For development, mount code as volumes so changes are instant:

```yaml
# docker-compose.dev.yml
services:
  backend:
    volumes:
      - ./backend:/app:ro  # Mount code read-only
      - ./backend/alembic:/app/alembic  # Migrations writable
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Run with: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up`

---

## Phase 1: Build & Test Infrastructure (Days 1-2)

### 1.1 Docker Build Optimization

| Task | File | Changes |
|------|------|---------|
| Create tooling base image | `Dockerfile.tooling` | All tool installs |
| Refactor main Dockerfile | `backend/Dockerfile` | 3-stage build |
| Fix upgrade.sh | `upgrade.sh` | Remove `--no-cache` |
| Dev compose file | `docker-compose.dev.yml` | Volume mounts, hot reload |
| Frontend dev mode | `docker-compose.dev.yml` | Vite dev server |

### 1.2 Test Infrastructure

| Task | File | Description |
|------|------|-------------|
| Install factory_boy | `requirements-dev.txt` | Test data factories |
| Create factories | `backend/tests/factories.py` | User, Project, Module, Task factories |
| Shared fixtures | `backend/tests/conftest.py` | Extend existing fixtures |
| Coverage enforcement | `pyproject.toml` | `--cov-fail-under=70` |
| Parallel tests | `pyproject.toml` | pytest-xdist config |
| Mock services | `backend/tests/mocks/` | AWS, K8s, Helm CLI mocks |

### 1.3 Factories Pattern

```python
# backend/tests/factories.py
import factory
from factory.alchemy import SQLAlchemyModelFactory
from models import User, Project, ProjectModule

class UserFactory(SQLAlchemyModelFactory):
    class Meta:
        model = User
        sqlalchemy_session_persistence = "commit"
    
    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@test.com")
    role = "operator"
    is_active = True
    hashed_password = "$2b$12$test..."  # Pre-hashed "password"

class ProjectFactory(SQLAlchemyModelFactory):
    class Meta:
        model = Project
    
    name = factory.Sequence(lambda n: f"project-{n}")
    cloud_provider = "aws"
    region = "us-east-1"

class ProjectModuleFactory(SQLAlchemyModelFactory):
    class Meta:
        model = ProjectModule
    
    project = factory.SubFactory(ProjectFactory)
    status = "pending"
```

---

## Phase 2: Backend Unit Tests (Days 3-6)

### 2.1 Critical Services

| Service | Test File | Tests Needed |
|---------|-----------|--------------|
| `variable_assembler.py` | `test_variable_assembler.py` | 7-layer chain, transforms, JWT guard, destroy mode |
| `opentofu_runtime.py` | `test_opentofu_runtime.py` | workspace prep, init, plan, apply, destroy |
| `secrets_service.py` | `test_secrets_service.py` | encrypt, decrypt, file upload, validation |
| `aws_auth_service.py` | `test_aws_auth_service.py` | SSO flow, token refresh, credential storage |
| `kubernetes_engine.py` | `test_kubernetes_engine.py` | apply, destroy, placeholder injection |
| `workspace_lock_service.py` | `test_workspace_lock.py` | acquire, release, timeout, concurrent |
| `helm_service.py` | `test_helm_service.py` | install, upgrade, rollback, uninstall |

### 2.2 Mock Requirements

```python
# backend/tests/mocks/aws_mock.py
from unittest.mock import MagicMock, patch

def mock_boto3_client():
    """Mock boto3 for AWS operations."""
    mock_sso = MagicMock()
    mock_sso.start_device_authorization.return_value = {
        "deviceCode": "test-device-code",
        "userCode": "TEST-CODE",
        "verificationUri": "https://device.sso.us-east-1.amazonaws.com/",
        "expiresIn": 600,
    }
    return mock_sso

# backend/tests/mocks/subprocess_mock.py
def mock_tofu_subprocess(returncode=0, stdout="", stderr=""):
    """Mock subprocess for tofu CLI calls."""
    mock_result = MagicMock()
    mock_result.returncode = returncode
    mock_result.stdout = stdout
    mock_result.stderr = stderr
    return mock_result
```

### 2.3 Celery Task Tests

| Task File | Test File | Key Scenarios |
|-----------|-----------|---------------|
| `opentofu_tasks.py` | `test_opentofu_tasks.py` | Success, failure, retry, timeout |
| `kubernetes_tasks.py` | `test_kubernetes_tasks.py` | Apply, destroy, placeholder fallback |
| `parallel_tasks.py` | `test_parallel_tasks.py` | Layer execution, failure propagation, skip |
| `stack_tasks.py` | `test_stack_tasks.py` | Deploy, destroy, partial failure |

### 2.4 Route Coverage Expansion

| Route File | Current | Target | Priority |
|------------|---------|--------|----------|
| `project_execution.py` | 0 | 15 | P0 |
| `project_secrets.py` | 0 | 10 | P0 |
| `cloud_auth.py` | 0 | 12 | P0 |
| `project_modules.py` | 0 | 12 | P1 |
| `helm.py` | 0 | 15 | P1 |
| `k8s/resources.py` | 0 | 20 | P1 |
| `stacks.py` | 8 | 20 | P1 |

---

## Phase 3: Frontend Unit Tests (Days 7-9)

### 3.1 Hook Tests

| Hook | Test File | Scenarios |
|------|-----------|-----------|
| `useDeployments` | `useDeployments.test.ts` | fetch, trigger, poll status |
| `useHelm` | `useHelm.test.ts` | releases, install, upgrade |
| `useModules` | `useModules.test.ts` | CRUD, dependencies |
| `useStacks` | `useStacks.test.ts` | templates, instances |
| `useCredentials` | `useCredentials.test.ts` | CRUD, validation |
| `useSecrets` | `useSecrets.test.ts` | CRUD, file upload |
| `useWebSocket` | `useWebSocket.test.ts` | connect, reconnect, messages |

### 3.2 Component Tests

| Component | Test File | Scenarios |
|-----------|-----------|-----------|
| `CreateProjectDialog` | `CreateProjectDialog.test.tsx` | validation, submit, error |
| `DeployConfirmationDialog` | `DeployConfirmationDialog.test.tsx` | plan display, confirm |
| `LogViewerModal` | `LogViewerModal.test.tsx` | streaming, search, filter |
| `K8sResourceViewer` | `K8sResourceViewer.test.tsx` | display, actions |
| `HelmReleaseCard` | `HelmReleaseCard.test.tsx` | status, actions |
| `ModuleCard` | `ModuleCard.test.tsx` | status, deploy, destroy |

### 3.3 MSW Handler Expansion

```typescript
// frontend-v2/src/test/mocks/handlers.ts - additions needed

// Deployment handlers
http.post('/api/project-modules/:id/plan', () => {
  return HttpResponse.json({ task_id: 'task-123', status: 'pending' })
}),
http.post('/api/project-modules/:id/apply', () => {
  return HttpResponse.json({ task_id: 'task-456', status: 'pending' })
}),

// Helm handlers
http.get('/api/k8s/:clusterId/helm/releases', () => {
  return HttpResponse.json(mockHelmReleases)
}),
http.post('/api/k8s/:clusterId/helm/install', () => {
  return HttpResponse.json({ success: true, release: 'nginx-1' })
}),

// Secrets handlers
http.get('/api/projects/:id/secrets', () => {
  return HttpResponse.json(mockSecrets)
}),
http.post('/api/projects/:id/secrets/value', () => {
  return HttpResponse.json({ id: 1, name: 'new-secret' })
}),
```

---

## Phase 4: E2E Tests (Days 10-12)

### 4.1 Playwright Structure

```
tests/e2e/
├── playwright.config.ts     # Update for faster runs
├── fixtures/
│   ├── auth.fixture.ts      # Login/logout helpers
│   ├── project.fixture.ts   # Project CRUD helpers
│   ├── api-mock.fixture.ts  # API mocking for fast tests
│   └── db.fixture.ts        # Database seeding
├── pages/
│   ├── login.page.ts
│   ├── projects.page.ts
│   ├── project-detail.page.ts
│   ├── deployments.page.ts
│   ├── kubernetes.page.ts
│   └── helm.page.ts
└── tests/
    ├── auth.spec.ts         # 5 tests
    ├── projects.spec.ts     # 8 tests
    ├── deployments.spec.ts  # 10 tests
    ├── kubernetes.spec.ts   # 8 tests
    ├── helm.spec.ts         # 6 tests
    ├── stacks.spec.ts       # 5 tests
    └── smoke.spec.ts        # 3 critical path tests
```

### 4.2 E2E Test Scenarios

**auth.spec.ts**
- Login with valid credentials
- Login with invalid credentials
- Session persistence
- Logout
- Password change

**projects.spec.ts**
- List projects
- Create project
- Edit project
- Delete project
- Project navigation
- Environment switching
- Search/filter

**deployments.spec.ts**
- View module status
- Trigger plan
- View plan output
- Confirm apply
- View apply logs
- Cancel operation
- Handle failure
- Retry failed deployment

### 4.3 API Mocking Strategy

For fast, reliable E2E tests, mock the API:

```typescript
// fixtures/api-mock.fixture.ts
import { test as base, expect } from '@playwright/test';

export const test = base.extend({
  apiMock: async ({ page }, use) => {
    // Mock all API calls
    await page.route('**/api/**', async (route) => {
      const url = route.request().url();
      
      if (url.includes('/api/auth/login')) {
        return route.fulfill({
          status: 200,
          json: { token: 'mock-jwt-token', user: mockUser },
        });
      }
      
      if (url.includes('/api/projects')) {
        return route.fulfill({ status: 200, json: mockProjects });
      }
      
      // ... more mocks
    });
    
    await use();
  },
});
```

---

## Phase 5: CI/CD Updates (Day 13-14)

### 5.1 Updated CI Workflow

```yaml
# .github/workflows/ci.yml
name: CI

on: [push, pull_request]

jobs:
  backend-tests:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
      redis:
        image: redis:7
        options: >-
          --health-cmd "redis-cli ping"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - run: pip install -r backend/requirements-dev.txt
      - run: |
          pytest backend/tests/ -v \
            --cov=backend \
            --cov-report=xml \
            --cov-fail-under=70 \
            -n auto
      - uses: codecov/codecov-action@v4

  frontend-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend-v2/package-lock.json
      - run: cd frontend-v2 && npm ci
      - run: cd frontend-v2 && npm run test:coverage
      - run: cd frontend-v2 && npm run build

  e2e-tests:
    runs-on: ubuntu-latest
    needs: [backend-tests, frontend-tests]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: cd tests/e2e && npm ci
      - run: npx playwright install --with-deps chromium
      - run: cd tests/e2e && npx playwright test
      - uses: actions/upload-artifact@v4
        if: failure()
        with:
          name: playwright-report
          path: tests/e2e/playwright-report/

  docker-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose build
```

### 5.2 Coverage Thresholds

| Component | Current | Target | Enforcement |
|-----------|---------|--------|-------------|
| Backend | ~30% | 70% | `--cov-fail-under=70` |
| Frontend | ~15% | 60% | vitest coverage threshold |
| E2E | 1 spec | 7 specs | N/A (functional) |

---

## File Checklist

### New Files to Create

```
backend/
├── tests/
│   ├── factories.py                    # factory_boy factories
│   ├── mocks/
│   │   ├── __init__.py
│   │   ├── aws_mock.py                 # AWS/boto3 mocks
│   │   ├── subprocess_mock.py          # CLI subprocess mocks
│   │   ├── kubernetes_mock.py          # K8s API mocks
│   │   └── redis_mock.py               # Redis mocks
│   ├── test_variable_assembler.py
│   ├── test_opentofu_runtime.py
│   ├── test_secrets_service.py
│   ├── test_aws_auth_service.py
│   ├── test_kubernetes_engine.py
│   ├── test_workspace_lock.py
│   ├── test_helm_service.py
│   ├── test_opentofu_tasks.py
│   ├── test_kubernetes_tasks.py
│   ├── test_parallel_tasks.py
│   └── test_routes_execution.py

frontend-v2/src/
├── hooks/__tests__/
│   ├── useDeployments.test.ts
│   ├── useHelm.test.ts
│   ├── useModules.test.ts
│   └── useSecrets.test.ts
├── components/__tests__/
│   ├── CreateProjectDialog.test.tsx
│   ├── DeployConfirmationDialog.test.tsx
│   └── LogViewerModal.test.tsx
└── stores/__tests__/
    ├── authStore.test.ts
    └── uiStore.test.ts

tests/e2e/
├── fixtures/
│   ├── auth.fixture.ts
│   ├── project.fixture.ts
│   └── api-mock.fixture.ts
├── pages/
│   ├── login.page.ts
│   └── deployments.page.ts
└── tests/
    ├── auth.spec.ts
    ├── projects.spec.ts
    ├── deployments.spec.ts
    ├── kubernetes.spec.ts
    └── helm.spec.ts

docker-compose.dev.yml                   # Dev mode with hot reload
Dockerfile.tooling                       # Base image with tools
docs/TESTING.md                          # Test documentation
```

### Files to Modify

```
backend/Dockerfile                       # 3-stage build
backend/pyproject.toml                   # Coverage thresholds, pytest-xdist
backend/requirements-dev.txt             # Add factory_boy, pytest-xdist
upgrade.sh                               # Remove --no-cache
docker-compose.yml                       # Reference tooling image
.github/workflows/ci.yml                 # E2E job, coverage
frontend-v2/vitest.config.ts             # Coverage thresholds
frontend-v2/src/test/mocks/handlers.ts   # More API mocks
```

---

## Success Criteria

### Build Performance
- [ ] Cold build < 3 minutes
- [ ] Hot build (code change only) < 30 seconds
- [ ] Dev mode (volume mount) < 10 seconds to see changes

### Test Coverage
- [ ] Backend coverage ≥ 70%
- [ ] Frontend coverage ≥ 60%
- [ ] All critical paths have tests
- [ ] E2E covers main user flows

### CI/CD
- [ ] All tests pass in CI
- [ ] Coverage reports uploaded
- [ ] E2E runs on every PR
- [ ] Build artifacts cached

---

## Notes for Future Agents

1. **Build is SLOW** - The `--no-cache` was added because Docker wasn't picking up code changes. The real fix is proper layer separation, not disabling cache.

2. **PostgreSQL vs SQLite** - Backend tests use SQLite in-memory for speed. Some PostgreSQL-specific features may not be tested. Consider Docker PostgreSQL for integration tests.

3. **Celery tests** - Use `celery.contrib.testing` for task tests. Don't actually run Redis/Celery in unit tests.

4. **E2E strategy** - Mock the API for fast, reliable tests. Only run against real backend for integration/smoke tests.

5. **Factory pattern** - Use `factory_boy` for consistent test data. Avoid hardcoded fixtures that drift from reality.
