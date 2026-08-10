# Engineering Improvements

> Technical refinements, debt reduction, and reliability improvements for BNK Forge v2.

Last updated: 2026-03-31 | Product-state note: this document tracks workstreams, not shipped release numbers.

---

## Overview

The engineering foundations of BNK Forge are strong. The three-engine architecture, structured error handling, security testing, and operator model are well-designed. This document covers refinements that improve reliability, maintainability, and developer experience — the kind of work that prevents future bugs rather than fixing current ones.

---

## Completed

All major engineering improvements have been implemented. Here's a summary:

| # | Section | Status | Completed In |
|---|---------|--------|-------------|
| 1 | Engine Router Resilience | ✅ Done | ENG-005 — health checks, circuit breaker, fallback logic |
| 2 | Mutable Class Attributes | ✅ Done | ENG-003 — `__init_subclass__` deep-copy fix |
| 3 | Explicit Transaction Boundaries | ✅ Done | ENG-006 — convention enforced, 101 commits refactored |
| 4 | Service Registry (DI) | ✅ Done | ENG-004 — ServiceRegistry singleton, 18 lazy imports replaced |
| 5 | Backend Route Integration Tests | ✅ Done | ENG-001 — conftest.py + 15 route tests |
| 6 | Frontend Test Foundation | ✅ Done | ENG-002 — Vitest + RTL + MSW, 105 tests |
| 7 | Shared Test Fixtures | ✅ Done | ENG-001 — part of conftest.py |
| 8 | Input Validation Gaps | ✅ Done | ENG-007 — CIDR, region, Helm timeout, project name uniqueness |
| 9 | CI/CD Pipeline | ✅ Done | ENG-003 — `.github/workflows/ci.yml` |
| 10 | Backward Compatibility Shims | 📝 Documented | Kept for now, documented |
| 11 | Development Artifacts | ✅ Done | ENG-003 — configs cleaned, update.sh deprecated |
| 12 | Merge update.sh / upgrade.sh | ✅ Done | ENG-003 — update.sh redirects to upgrade.sh |
| 13 | Unified BNK Data Fetching | ✅ Done | Separate agent work |
| 14 | Frontend Bundle Optimization | ✅ Done | ENG-005 — lazy-load DeploymentPipeline, vendor-ui chunks, sideEffects |

---

## Architecture Refinements

### 1. Engine Router Resilience ✅

**Problem:** The `EngineRouter` decides which engine to use at dispatch time, but conditions can change during execution. An operator can disconnect mid-apply. A kubeconfig can expire mid-plan. Currently, failures are abrupt.

**Improvement:** Add engine health checks and mid-execution fallback.

```python
class EngineRouter:
    def get_engine(self, module_path, category=""):
        engine = self._resolve_engine(module_path, category)

        # Verify the engine can actually execute right now
        if not engine.health_check():
            fallback = self._get_fallback_engine(module_path)
            if fallback and fallback.health_check():
                logger.warning(f"Primary engine unhealthy, falling back: {engine} -> {fallback}")
                return fallback
            raise EngineUnavailableError(...)

        return engine
```

**Specifically:**
- Add `health_check()` to `DeploymentEngine` ABC — returns True/False
- `OperatorEngine.health_check()` — verify WebSocket connection is alive
- `KubernetesEngine.health_check()` — verify kubeconfig is valid (quick API call)
- `OpenTofuEngine.health_check()` — verify `tofu` binary exists
- Circuit-breaker pattern: after N failures, stop trying that engine for M seconds

**Status:** ✅ Completed in ENG-005

---

### 2. Mutable Class Attributes on BaseModule ✅

**Problem:** Python's classic mutable default gotcha:

```python
class BaseModule(ABC):
    inputs: Dict[str, InputSpec] = {}   # Shared across ALL instances
    outputs: Dict[str, OutputSpec] = {}  # Same reference for everyone
    dependencies: List[str] = []         # Mutation affects all subclasses
```

If any code does `self.inputs["foo"] = bar` without copying first, it silently corrupts all instances. This works today because `render_manifests` is stateless, but it's fragile.

**Fix:** `__init_subclass__` deep-copy applied.

**Status:** ✅ Completed in ENG-003

---

### 3. Explicit Transaction Boundaries (Unit of Work) ✅

**Problem:** Services reach directly into `db.query()` and `db.commit()` at various depths. Some routes commit, some services commit, some tasks commit. Transaction boundaries are implicit and spread across call chains.

**Convention (now enforced):**

```
Rule: Only the outermost caller (route handler or Celery task) commits.
      Services raise exceptions on error — they never swallow or commit.
      Helpers use flush() for audit logging; route-level db.commit() on both success and error paths.
```

**Status:** ✅ Completed in ENG-006 (101 commits). Config promotion routes refactored in v2.10.32 — `_log_promotion` now flushes instead of committing, route-level `db.commit()` added to both success and error paths.

---

### 4. Dependency Injection for Services (Reduce Lazy Imports) ✅

**Problem:** ~20+ lazy imports inside methods to avoid circular imports.

**Solution:** Lightweight `ServiceRegistry` singleton populated at startup, replacing 18 lazy imports across 9 files.

**Status:** ✅ Completed in ENG-004

---

## Testing Improvements

### 5. Backend Route Integration Tests ✅

**Problem:** Zero route-level HTTP tests.

**Solution:** `conftest.py` with SQLite in-memory DB, TestClient, JWT auth fixtures. 15 route tests across 6 files.

**Status:** ✅ Completed in ENG-001

---

### 6. Frontend Test Foundation ✅

**Problem:** 268 TypeScript/TSX files with zero tests.

**Solution:** Vitest + React Testing Library + MSW. 105 tests across 10 files covering 5 hooks and 5 components.

**Status:** ✅ Completed in ENG-002

---

### 7. Shared Test Fixtures (conftest.py) ✅

**Problem:** 16 backend test files with no shared fixtures.

**Solution:** Shared `conftest.py` with DB, client, and auth fixtures.

**Status:** ✅ Completed as part of ENG-001

---

## Reliability Improvements

### 8. Input Validation Gaps ✅

Several validation gaps have been closed:

| Gap | Fix | Status |
|---|---|---|
| No CIDR validation on network inputs | `validate_cidr()` / `validate_cidr_fields()` in `utils/validators.py` — applied to module create/update, stack variables, project variable defaults | ✅ Done |
| No project name uniqueness error message | Explicit duplicate check on create + update routes, clean 409 ConflictError message, IntegrityError safety net | ✅ Done |
| No region validation | `validate_aws_region()` in `utils/validators.py` — validates against 32 known AWS regions. Applied to project create/update, cloud-auth models, credential templates, K8s clusters | ✅ Done |
| Missing `docs/DEPLOYMENT.md` referenced by `config.py` | Created comprehensive deployment guide with prerequisites, env vars, backup, troubleshooting | ✅ Done |
| Helm timeout not validated at API boundary | Added `field_validator` to InstallChartRequest, UpgradeReleaseRequest, RollbackReleaseRequest | ✅ Done |
| `UserCreateRequest.email` typed as `str` | Deferred — single admin user, not needed | ⏭️ Deferred |
| No password complexity beyond 8 chars | Deferred — single admin user | ⏭️ Deferred |
| No rate limiting on login endpoint | Deferred — single admin user | ⏭️ Deferred |

**Status:** ✅ Core items completed in ENG-007. Remaining items deferred (single admin user, minimal risk).

---

### 9. CI/CD Pipeline ✅

**Solution:** `.github/workflows/ci.yml` with 3 jobs: backend-tests, frontend-lint, docker-build.

**Status:** ✅ Completed in ENG-003

---

## Code Hygiene

### 10. Backward Compatibility Shims

Multiple places have shims for old import paths that add complexity:

- `backend/services/execution_engine.py` — re-exports from `execution/opentofu_runtime.py`
- `backend/models/__init__.py` — barrel re-export for old `from models import X` imports
- `frontend-v2/src/lib/api.ts` — re-exports from `api/` subdirectory

**Plan:** These are fine for now. Remove them only when a major version bump justifies breaking the old paths. Document them so new contributors don't add more.

### 11. Development Artifacts ✅

| File | Action | Status |
|---|---|---|
| `configs/README.md` | Removed | ✅ |
| `configs/example/` | Removed | ✅ |
| Root `TROUBLESHOOTING.md` | Removed | ✅ |
| `backend/seed_stack_templates.py` | Removed | ✅ |
| `update.sh` | Deprecated → points to upgrade.sh | ✅ |
| `.DS_Store` files | In `.gitignore`, none tracked | ✅ |

### 12. Merge update.sh into upgrade.sh ✅

`update.sh` now shows a deprecation message pointing users to `upgrade.sh`.

**Status:** ✅ Completed in ENG-003

---

## Performance Improvements

### 13. Unified BNK Data Fetching ✅

Single `/f5bnk/data` endpoint fetches all CRDs + pods once. Frontend views share data via `useBnkData()` hook. Eliminates ~60% of K8s API calls.

**Status:** ✅ Completed

### 14. Frontend Bundle Optimization ✅

- DeploymentPipeline lazy-loaded (separate 5KB gzip chunk)
- 6 Radix packages consolidated into vendor-ui chunk
- `sideEffects: ["*.css"]` enables tree-shaking
- Initial load ~208KB gzipped

**Status:** ✅ Completed in ENG-005

---

## Summary: Priority Order

| # | Improvement | Status | Category |
|---|---|---|---|
| 1 | Engine router resilience | ✅ Done | Architecture |
| 2 | Mutable class attributes fix | ✅ Done | Architecture |
| 3 | Explicit transaction boundaries | ✅ Done | Architecture |
| 4 | Service registry (DI) | ✅ Done | Architecture |
| 5 | Backend route integration tests | ✅ Done | Testing |
| 6 | Frontend test foundation | ✅ Done | Testing |
| 7 | Shared test fixtures | ✅ Done | Testing |
| 8 | Input validation gaps | ✅ Done | Reliability |
| 9 | CI/CD pipeline | ✅ Done | DevOps |
| 10 | Backward compatibility shims | 📝 Documented | Hygiene |
| 11 | Development artifacts cleanup | ✅ Done | Hygiene |
| 12 | Merge update.sh / upgrade.sh | ✅ Done | Hygiene |
| 13 | Unified BNK data fetching | ✅ Done | Performance |
| 14 | Frontend bundle optimization | ✅ Done | Performance |
| 15 | Celery task registration guard | ✅ Done | Reliability |

**14 of 15 items complete.** Remaining: backward compat shims (documented, low priority — will remove on next major version bump).

### 15. Celery Task Registration Guard ✅

**Problem:** The `celery_app.py` `include=` list is a single long line containing all task modules. Editing it to add a new module can silently drop an existing one — the worker discards unregistered tasks with no backend-visible error. Regression `eb32caf2` dropped `tasks.ssh_tasks`, breaking all bare-metal deployments for 3 days.

**Improvement:** AST-based regression guard test (`tests/unit/test_celery_task_registration.py`) that parses `task_dispatch.py` to extract every `tasks.*` import and asserts all are present in `celery_app.conf.include`. Self-maintaining — new engines are caught automatically.

**See also:** [PM-001 in POST_MORTEMS.md](POST_MORTEMS.md#pm-001-bare-metal-deployments-silently-fail--celery-task-registration-regression) for full root cause analysis.

---

## Related Documents

- [Product Vision](PRODUCT_VISION.md) — Where BNK Forge is heading
- [UX Roadmap](UX_ROADMAP.md) — Making the user experience effortless
- [Deployment Guide](DEPLOYMENT.md) — How to deploy and operate BNK Forge
- [Architecture Index](architecture/README.md) — Current and archived architecture documents
- [Architecture archive](architecture/archive/) — Historical refactoring and planning documents
- [Post-Mortems](POST_MORTEMS.md) — Root cause analyses for production-impact bugs
