# BNK-Forge v2 — Codebase Review & Improvement Suggestions

**Date:** 2026-02-21
**Scope:** Full codebase review (backend, frontend, operator, infra)

---

## Executive Summary

BNK-Forge is a well-structured infrastructure management platform built with FastAPI (Python) + React/TypeScript + Celery + PostgreSQL + Redis. The codebase shows strong fundamentals: good separation of concerns, thoughtful error handling, security-conscious patterns (CLI injection prevention, RBAC, encryption), and a mature CI pipeline. Below are prioritized improvements grouped by category.

---

## 1. Security Improvements

### 1.1 — Deprecated `declarative_base` import (Low effort, High value)
**File:** `backend/database.py:7`

```python
# Current (deprecated in SQLAlchemy 2.0+):
from sqlalchemy.ext.declarative import declarative_base

# Fix:
from sqlalchemy.orm import declarative_base
```

The old import path is deprecated since SQLAlchemy 2.0 and will be removed. This should be a one-line fix.

### 1.2 — Auth middleware swallows all token errors (Medium effort, High value)
**File:** `backend/core/auth_middleware.py:106`

The `except Exception` block when validating tokens catches *every* exception type, including programming bugs (AttributeError, ImportError, etc.) and returns a 401. This masks real errors during development and operations.

```python
# Current:
except Exception:
    return JSONResponse(status_code=401, ...)

# Suggested:
from jose import JWTError, ExpiredSignatureError
from core.errors import UnauthorizedError

except (JWTError, ExpiredSignatureError, UnauthorizedError):
    return JSONResponse(status_code=401, ...)
except Exception:
    logger.exception(f"Unexpected error validating token for {path}")
    return JSONResponse(status_code=500, ...)
```

### 1.3 — Default admin credentials logged at startup (Low effort, Medium value)
**File:** `backend/main.py:165`

The plaintext default password (`admin/changeme`) is logged at INFO level. In production-adjacent environments where log aggregation collects INFO logs, this is a risk.

**Suggestion:** Log that a default admin was created but omit the credentials. Reference a docs page for first-login instructions instead.

### 1.4 — Docker socket mounted in production compose (Medium effort, High value)
**File:** `docker-compose.yml:122`

```yaml
- /var/run/docker.sock:/var/run/docker.sock
```

There's already a good comment about this (`SEC-004`), but the mount is active by default. Consider moving it to a `docker-compose.override.yml` or `docker-compose.dev.yml` so production deployments don't accidentally include it.

### 1.5 — CORS wildcard is the default (Low effort, Medium value)
**File:** `backend/main.py:345`

`ALLOWED_ORIGINS` defaults to `"*"` when the env var is not set. The `Settings` class at `core/config.py:74` has a safer default (`localhost:2650,localhost:5173`), but `main.py:345` reads directly from `os.getenv("ALLOWED_ORIGINS", "*")` — bypassing the Settings object entirely.

**Fix:** Use `settings.cors_origins` instead of re-reading the env var:
```python
allowed_origins = settings.cors_origins
```

This is a real inconsistency — the two code paths can diverge.

---

## 2. Architecture & Design Improvements

### 2.1 — Massive `lifespan()` function (Medium effort, High value)
**File:** `backend/main.py:89-313` (224 lines)

The lifespan function handles ~10 distinct initialization steps in one monolithic block. Each step has its own try/except that logs and continues, meaning startup can silently half-fail.

**Suggestion:** Extract each initialization step into its own function and create a startup registry:

```python
STARTUP_STEPS = [
    ("Database", init_database_step),
    ("System defaults", seed_defaults_step),
    ("Settings", seed_settings_step),
    # ...
]

async def lifespan(app):
    for name, step in STARTUP_STEPS:
        try:
            step()
            logger.info(f"✓ {name}")
        except Exception as e:
            logger.error(f"✗ {name}: {e}")
    yield
    # shutdown...
```

### 2.2 — Route response serialization is manual (Medium effort, High value)
**File:** `backend/routes/projects.py:70-116`

Project list items are manually assembled as dicts with ~30 fields. This pattern is repeated across many routes. When a model field is added, every route that returns that model must be updated.

**Suggestion:** Create Pydantic response serializers that can auto-serialize from SQLAlchemy models. You already have `schemas/` — lean into them more:

```python
class ProjectListItemSchema(BaseModel):
    class Config:
        from_attributes = True  # SQLAlchemy 2.0 compatible
```

Then: `return [ProjectListItemSchema.from_orm(p) for p in projects]`

### 2.3 — `execution_engine.py` is a backward-compat shim (Low effort, Low value)
**File:** `backend/services/execution_engine.py`

This file only re-exports `OpenTofuRuntime` as `ExecutionEngine`. Grep the codebase for remaining `ExecutionEngine` imports and migrate them, then remove the shim.

### 2.4 — Service instantiation inconsistencies
Some services are singletons (`ServiceRegistry.get()`), some are instantiated per-request (`SecretsService(db)`), and some are module-level instances (`cache = CacheService()`).

**Suggestion:** Document and standardize the lifecycle patterns:
- **Singletons**: stateless services, registries
- **Per-request**: services that take a `db` session
- **Module-level**: infrastructure (cache, Redis)

Consider using FastAPI's dependency injection more consistently instead of manual instantiation in route handlers.

### 2.5 — Large files that should be split
Several files exceed 800+ lines and mix concerns:

| File | Lines | Suggestion |
|------|-------|------------|
| `services/cluster_scanner.py` | 1199 | Split by provider (AWS/Azure/GCP scanners) |
| `services/project_module_service.py` | 1112 | Separate CRUD from business logic |
| `services/runbook_service.py` | 1051 | Extract individual runbook implementations |
| `routes/module_library.py` | 884 | Split into library CRUD vs sync operations |
| `services/adaptive_module_selector.py` | 819 | Extract `TEMPLATE_MODULES` dict (~200 lines of data) to JSON/YAML |
| `frontend-v2/src/pages/HelmPackagesV2.tsx` | 940 | Extract table, forms, and dialogs to components |
| `frontend-v2/src/pages/Modules.tsx` | 918 | Same as above |

---

## 3. Error Handling Improvements

### 3.1 — 415 `except Exception` occurrences across 97 files
While the `@handle_route_errors` decorator exists and properly maps exceptions, many services and tasks still use bare `except Exception` with inconsistent recovery patterns.

**Priority files** (highest occurrence counts):
- `services/execution/kubernetes_engine.py` — 14
- `services/user_module_service.py` — 13
- `services/system_service.py` — 12
- `services/cluster_scanner.py` — 11

**Suggestion:** Audit these files and:
1. Replace bare `except Exception` with specific exception types where possible
2. Use the `@handle_route_errors` decorator for route-level code
3. For service code, create a similar `@handle_service_errors` that logs and re-raises typed errors

### 3.2 — Database init failure is non-fatal
**File:** `backend/main.py:102-103`

```python
except Exception as e:
    logger.error(f"✗ Database initialization failed: {e}")
```

If the database fails to initialize, the app continues starting up. Every subsequent request will fail with confusing errors. This should be fatal (re-raise or `sys.exit(1)`).

---

## 4. Performance Improvements

### 4.1 — Cache key generation in decorator is fragile
**File:** `backend/core/cache.py:147-157`

The `@cached` decorator builds cache keys by string-converting args and filtering out objects with `__dict__`. This is brittle — two different objects could produce the same cache key, or the same logical request could produce different keys.

**Suggestion:** Use explicit cache key parameters:
```python
@cached("modules", key=lambda project_id, **kw: f"project:{project_id}")
def get_modules(project_id: int, db: Session):
    ...
```

### 4.2 — N+1 query patterns in list endpoints
The `list_projects` endpoint (`routes/projects.py:63`) correctly uses `joinedload`/`subqueryload`, but other list endpoints should be audited for similar patterns. Specifically check:
- Module listing endpoints
- Task listing endpoints
- Deployment history endpoints

### 4.3 — `activate_project` does a full table UPDATE
**File:** `backend/routes/projects.py:468`

```python
db.query(Project).update({"is_active": False})
```

This updates every project row on every activation. With many projects, this generates unnecessary write I/O. Consider tracking the active project ID in a separate settings table or only deactivating the currently active project.

---

## 5. Testing Improvements

### 5.1 — Good test foundation but gaps in coverage
The test suite includes:
- Security tests (CLI injection, Helm zip-slip, RBAC, namespace security) — excellent
- Route integration tests (auth, projects, stacks, system, K8s)
- E2E tests with Playwright

**Gaps to address:**
- **No service-layer unit tests** — The `services/` directory has 60+ files but zero dedicated test files. Services contain the core business logic and should have the most tests.
- **No tests for Celery tasks** — The `tasks/` directory has critical code paths (OpenTofu execution, drift detection, upgrades) with no test coverage.
- **Missing edge cases in route tests** — e.g., concurrent project activation, cache invalidation races

### 5.2 — CI pipeline improvements
**File:** `.github/workflows/ci.yml`

Current CI runs: backend tests, frontend lint/test/build, Docker build, security audit, Trivy scan. This is solid.

**Missing:**
- **No coverage reporting** — Add `pytest --cov` and enforce a minimum threshold
- **No migration testing** — Alembic migrations should be tested (upgrade/downgrade round-trip)
- **Security audit is `continue-on-error: true`** — This means vulnerabilities never block merges. Consider at least failing on CRITICAL severity.
- **No Python linting** — Frontend has ESLint, but backend has no linter configured (no ruff, flake8, or mypy)

---

## 6. Code Quality Improvements

### 6.1 — Add a Python linter (ruff or flake8)
The backend has no automated linting. Consider adding `ruff` (fast, comprehensive):

```toml
# pyproject.toml or ruff.toml
[tool.ruff]
target-version = "py311"
select = ["E", "F", "I", "N", "W", "UP"]
```

Add to CI:
```yaml
- name: Lint Python
  run: ruff check backend/
```

### 6.2 — Add type checking (mypy or pyright)
The codebase uses type hints inconsistently. Some functions have full annotations, others have none. Adding gradual type checking would catch bugs:

```ini
# mypy.ini
[mypy]
python_version = 3.11
warn_return_any = True
warn_unused_configs = True
ignore_missing_imports = True
```

### 6.3 — `secrets_service.py` has duplicated soft-delete reactivation logic
**File:** `backend/services/secrets_service.py`

The `create_file_secret` (lines 89-169) and `create_value_secret` (lines 171-245) both contain nearly identical soft-delete reactivation logic (~30 lines each). Extract this into a shared `_upsert_secret()` method.

### 6.4 — TODOs for GCP and Azure credential refresh
**File:** `backend/services/credential_refresh_service.py:405,421`

```python
# TODO: Implement GCP credential refresh
# TODO: Implement Azure credential refresh
```

These are either planned features or dead code paths. If not planned for near-term, remove the empty implementations to avoid confusing operators who configure GCP/Azure templates and expect refresh to work.

---

## 7. Infrastructure & DevOps Improvements

### 7.1 — Backup scheduling uses `sleep` loop
**File:** `docker-compose.yml:354-367`

The backup container uses a shell `while true; sleep` loop for scheduling. This is fragile — if the sleep calculation is wrong, backups may run at unexpected times.

**Suggestion:** Use `cron` inside the container or a dedicated backup scheduler image like `prodrigestivill/postgres-backup-local`.

### 7.2 — No database migration testing
Alembic migrations exist but aren't validated in CI. A broken migration can take down production.

**Suggestion:** Add a CI step:
```yaml
- name: Test migrations
  run: |
    cd backend
    alembic upgrade head
    alembic downgrade base
    alembic upgrade head
```

### 7.3 — Consider health check for the operator
**File:** `bnk-operator/`

The operator has a `health_server.py` but it's not referenced in any Docker Compose health check configuration. If operators are deployed via Docker Compose (not just K8s), add health checks.

---

## 8. Frontend Improvements

### 8.1 — Large page components need decomposition
Several page files exceed 700+ lines:

- `HelmPackagesV2.tsx` (940 lines)
- `Modules.tsx` (918 lines)
- `Fleet.tsx` (786 lines)
- `ProjectDetailV2.tsx` (784 lines)

These should extract reusable sub-components (tables, forms, dialogs, detail panels) into a `components/` subdirectory per domain.

### 8.2 — Good patterns already in place
- Lazy loading via `React.lazy` in the router — good for bundle splitting
- Custom hooks (`useK8s`, `useHelm`, `useProjects`, etc.) — good separation of data fetching
- TypeScript types in `types/` directory
- Tests for hooks (`hooks/__tests__/`)
- Accessibility hook (`useAccessibility`)

### 8.3 — No global API error handling layer visible in router
The router has `<ErrorBoundary />` for render errors, but API error handling (401 redirect to login, 403 toast, 500 retry) should be centralized in an API client interceptor if not already present.

---

## 9. Quick Wins (Can do immediately)

| # | Fix | File | Impact |
|---|-----|------|--------|
| 1 | Fix deprecated `declarative_base` import | `database.py:7` | Prevents future breakage |
| 2 | Use `settings.cors_origins` in `main.py` | `main.py:345` | Eliminates CORS config inconsistency |
| 3 | Make DB init failure fatal | `main.py:102` | Prevents confusing cascading errors |
| 4 | Catch specific JWT exceptions in auth middleware | `auth_middleware.py:106` | Stops masking real bugs |
| 5 | Remove default password from log message | `main.py:165` | Security hygiene |
| 6 | Add `ruff` to CI | `ci.yml` | Catches bugs automatically |
| 7 | Move Docker socket to override file | `docker-compose.yml` | Better production defaults |

---

## 10. Additional Findings (from deep-dive analysis)

### 10.1 — SQL string interpolation for table names
**File:** `backend/services/system_service.py:284`

```python
f"SELECT pg_total_relation_size('{model.__tablename__}') as size"
```

While safe here (table names come from model classes, not user input), this pattern sets a bad precedent. Use SQLAlchemy's `func.pg_total_relation_size()` or `text()` with bound parameters.

### 10.2 — Auth tokens stored in `localStorage` (XSS risk)
**File:** `frontend-v2/src/lib/api/client.ts:21-24`

`localStorage` is accessible to any JavaScript on the page, making tokens vulnerable to XSS. Consider migrating to `httpOnly` cookies for token storage (requires backend CSRF protection).

### 10.3 — Additional large frontend components needing decomposition
| File | Lines |
|------|-------|
| `components/onboarding/FirstDeploymentWizard.tsx` | 1132 |
| `components/k8s/ClusterScanResults.tsx` | 1117 |

These should be split into step/section sub-components.

### 10.4 — Inconsistent polling intervals across hooks
**File:** `frontend-v2/src/hooks/useK8s.ts`

Different queries use different polling intervals (20s, 30s) with no shared constants. Extract to a `POLL_INTERVALS` config object.

### 10.5 — `eslint-disable` hiding real dependency bugs
**File:** `frontend-v2/src/components/projects/CreateProjectDialog.tsx:85`

```typescript
// eslint-disable-next-line react-hooks/exhaustive-deps
}, [open, defaults]);  // Missing: handleProjectTypeChange
```

The suppressed warning is a real bug — if `handleProjectTypeChange` changes identity, the effect won't re-run.

### 10.6 — Missing `React.memo` on expensive child components
Dashboard and detail pages re-render all children on every state change. Wrap expensive visualization components (health rings, charts, resource tables) in `React.memo()`.

### 10.7 — No frontend retry/circuit-breaker logic
The axios interceptor handles 401 redirects but doesn't retry on 5xx errors or implement circuit-breaking for cascading failures. Consider adding exponential backoff for transient server errors.

### 10.8 — Unvalidated string lengths on API inputs
**File:** `backend/routes/cloud_auth.py:59-65`

Fields like `access_token: str` and `account_id: str` accept arbitrary-length strings with no `max_length` constraint. Add Pydantic `Field(max_length=...)` validators to prevent abuse.

---

## 11. Summary of Priorities

### P0 — Fix now
- CORS config inconsistency (`main.py` vs `settings`)
- Auth middleware exception handling
- DB init should be fatal on failure
- Add `max_length` validators to sensitive string fields (cloud_auth)

### P1 — Next sprint
- Add Python linter (ruff) to CI
- Add service-layer unit tests (start with `execution_engine`, `helm_service`, `secrets_service`)
- Split largest files (>1000 lines backend, >1000 lines frontend)
- Fix `eslint-disable` dependency suppressions
- Replace SQL f-string interpolation with SQLAlchemy functions

### P2 — Roadmap
- Add type checking (mypy gradual adoption)
- Standardize service lifecycle patterns
- Add coverage reporting and migration testing to CI
- Decompose large frontend pages/components into sub-components
- Implement GCP/Azure credential refresh or remove stubs
- Evaluate `httpOnly` cookie auth to replace `localStorage` tokens
- Add frontend retry/circuit-breaker logic for API calls
- Extract polling intervals into shared config constants
