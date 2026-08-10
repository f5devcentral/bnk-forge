# Quick Wins — ✅ ALL COMPLETED

> All 17 quick wins were completed during Weeks 1-2. This file is kept for historical reference.
> See commits: `df6f50c`, `6ebc70d`, `8a25b6d` for implementation details.

---

## Bugs (Fix Today)

### QW-001: `validate_module_for_operation()` Dead Code
**File:** `backend/routes/project_modules.py` lines 236-240
**Issue:** `db` is never in scope — not a parameter. Dependency check is dead code.
**Fix:** Add `db: Session` parameter, pass it from callers.
**Risk:** Low — currently does nothing.
**Time:** 15 minutes

### QW-002: `is_active` Semantic Collision
**File:** `backend/services/project_service.py` line 204
**Issue:** `project.is_active = project.deployed_count > 0` overwrites user's explicit "active project" toggle.
**Fix:** Add `has_deployments` computed column. Keep `is_active` as user-controlled.
**Risk:** Low — needs migration + frontend check.
**Time:** 1 hour

### QW-003: Version Inconsistency
**Files:** `backend/routes/api.py` line 49 (`"1.0.0-mvp"`), `backend/core/config.py` (`"2.6.2"`), `VERSION` file (`2.7.18`)
**Fix:** Read version from `VERSION` file at startup, use everywhere.
**Risk:** None.
**Time:** 30 minutes

### QW-004: Auto-Wire Non-Determinism
**File:** `backend/services/execution_engine.py` lines 556-562
**Issue:** If two modules produce same-named output, first found wins (query order not guaranteed).
**Fix:** Prefer declared dependencies over auto-wire. Add warning log on conflicts.
**Risk:** Low — adds safety, doesn't change happy path.
**Time:** 30 minutes

---

## Consistency (This Week)

### QW-005: Standardize Error Handling in `projects.py`
**File:** `backend/routes/projects.py` (~20 instances)
**Issue:** Uses `return JSONResponse(status_code=500, content={"error": str(e)})` — leaks raw exceptions, inconsistent format.
**Fix:** Replace with `raise InternalError(...)` / `raise BadRequestError(...)` from `core.errors`.
**Risk:** Low — error responses change format (frontend may need minor updates).
**Time:** 2 hours

### QW-006: Standardize Error Handling in `project_modules.py`
**File:** `backend/routes/project_modules.py`
**Issue:** Uses `raise HTTPException(...)` throughout instead of `core.errors`.
**Fix:** Replace with `raise NotFoundError(...)` / `raise BadRequestError(...)`.
**Risk:** Low — same as QW-005.
**Time:** 2 hours

### QW-007: Use `get_or_404()` Everywhere
**File:** All route files
**Issue:** `project = db.query(Project).filter(...).first(); if not project: raise HTTPException(404)` repeated dozens of times.
**Fix:** Replace with `project = get_or_404(db, Project, project_id, "Project")`.
**Risk:** None — helper already exists in `core/errors.py` line 202.
**Time:** 1 hour

### QW-008: Replace `print()` with Logger
**Files:** `backend/main.py`, `backend/celery_app.py`
**Issue:** `print()` in production code bypasses logging config.
**Fix:** Replace with `logger.info()`.
**Risk:** None.
**Time:** 30 minutes

### QW-009: Fix Configuration Disconnect
**File:** `backend/database.py`
**Issue:** Reads `DATABASE_URL` from `os.getenv()` bypassing Pydantic Settings class.
**Fix:** Import from `core.config import settings`, use `settings.DATABASE_URL`.
**Risk:** Low — verify `.env` loading order.
**Time:** 15 minutes

### QW-010: Fix `useSettings.ts` Using Raw `fetch()`
**File:** `frontend-v2/src/hooks/useSettings.ts` lines 55-77
**Issue:** Uses raw `fetch()` instead of `apiClient`, bypassing auth interceptors and error handling.
**Fix:** Replace with `apiClient.get()/post()`.
**Risk:** Low.
**Time:** 30 minutes

---

## Cleanup (This Week)

### QW-011: Remove Dead/Backup Files
```bash
rm backend/models.py.bak
rm backend/routes/projects.py.backup
rm frontend-v2/src/components/helm/HelmPackagesV2.tsx.bak
rm frontend-v2/src/components/helm/HelmPackages.tsx.backup
rm bnk-forge-v2backendentrypoint.sh
rm configs/test_integration.db
```
**Risk:** None.
**Time:** 5 minutes

### QW-012: DRY Docker Compose with YAML Anchors
**File:** `docker-compose.yml`
**Issue:** Environment variables and volumes copy-pasted across 4 services.
**Fix:** Add `x-common-env` and `x-common-volumes` anchors.
**Risk:** Low — test with `docker compose config` to validate.
**Time:** 30 minutes

### QW-013: Add Migrations to `update.sh`
**File:** `update.sh`
**Issue:** Pulls code and rebuilds but doesn't run `alembic upgrade head`. Schema goes out of sync.
**Fix:** Add migration step after container restart.
**Risk:** Low.
**Time:** 10 minutes

### QW-014: Add Log Rotation
**File:** `docker-compose.yml`
**Fix:** Add `logging: { driver: json-file, options: { max-size: "10m", max-file: "3" } }` to all services.
**Risk:** None.
**Time:** 15 minutes

---

## High-Value Connections (This Week)

### QW-015: Connect Helm UI to Backend
**File:** `frontend-v2/src/components/k8s/HelmReleasesViewer.tsx` line 59
**Issue:** `// TODO: Implement API hook when backend is ready` — backend has full Helm support, frontend is disconnected.
**Fix:** Wire `useHelm` hook to the existing `HelmReleasesViewer` component.
**Impact:** Unlocks Day 2 Helm operations (upgrade FLO, rollback, view history).
**Time:** 2-3 hours

### QW-016: Fix Stack Template Dependency Serialization
**File:** `backend/services/stack_deployment_service.py` lines 140-147
**Issue:** Every module depends on ALL previous modules (forces serial execution).
**Fix:** Use `dependencies_metadata.required` from module.json instead.
**Impact:** BNK stack goes from 7 serial layers to ~4 parallel layers. Deploy time drops significantly.
**Time:** 2 hours

### QW-017: Merge Duplicate Keyboard Shortcut Hooks
**Files:** `frontend-v2/src/hooks/useKeyboardShortcuts.ts`, `useKeyboardShortcut.ts`
**Issue:** Two hooks with different interfaces doing the same thing.
**Fix:** Merge into one with input-awareness from the better implementation.
**Time:** 1 hour

---

## Scorecard

| ID | Risk | Time | Impact | Category |
|---|---|---|---|---|
| QW-001 | None | 15m | Fix dead code | Bug |
| QW-002 | Low | 1h | Fix data corruption | Bug |
| QW-003 | None | 30m | Consistency | Bug |
| QW-004 | Low | 30m | Prevent subtle wiring bugs | Bug |
| QW-005 | Low | 2h | API consistency | Consistency |
| QW-006 | Low | 2h | API consistency | Consistency |
| QW-007 | None | 1h | DRY, readability | Consistency |
| QW-008 | None | 30m | Observability | Consistency |
| QW-009 | Low | 15m | Config correctness | Consistency |
| QW-010 | Low | 30m | Auth correctness | Consistency |
| QW-011 | None | 5m | Repo hygiene | Cleanup |
| QW-012 | Low | 30m | Maintainability | Cleanup |
| QW-013 | Low | 10m | Operational correctness | Cleanup |
| QW-014 | None | 15m | Operational hygiene | Cleanup |
| QW-015 | Low | 3h | Unlocks Day 2 Helm | Connection |
| QW-016 | Medium | 2h | Deploy speed 2-3x faster | Performance |
| QW-017 | Low | 1h | DRY, consistency | Cleanup |

**Total estimated time: ~15 hours across 17 items.**
All can be done independently. Suggested order: Bugs first, then QW-015/016 (highest impact), then consistency, then cleanup.
