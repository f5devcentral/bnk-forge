# Post-Mortems

> Root cause analyses for production-impact bugs. Each entry captures what broke, why, the fix, and what guard prevents recurrence.

---

## PM-001: Bare-metal deployments silently fail — Celery task registration regression

**Date discovered:** 2026-05-06
**Regression introduced:** 2026-05-03 (commit `eb32caf2`)
**Impact:** All bare-metal (SSH engine) deployments silently fail. Blueprints get stuck in `deploying` status permanently. No error visible in the UI or backend logs — only in worker logs.
**Duration of impact:** ~3 days (May 3-6)

### Symptom

User deploys a bare-metal blueprint (e.g., DPU Infrastructure All-in-One). The first module (`probe-dpu`) transitions to `initializing` and never progresses. The blueprint stays in `deploying` permanently. The three-dot menu hides the Deploy button, leaving no way to retry.

### Root cause

Commit `eb32caf2` (`feat(analyzer): real per-pod backend health + benchmark routing breakdown + section reorder`) rewrote the `include=` line in `celery_app.py` to add `tasks.backend_health_task`, but dropped `tasks.ssh_tasks` in the process. The entire include list is a single long line — the author added their module at the end and didn't notice `ssh_tasks` was removed.

**Before (working, from `bare-metal-deploy-v2` branch):**
```python
include=[..., "tasks.dpu_tasks", "tasks.ssh_tasks"]
```

**After (broken, `eb32caf2`):**
```python
include=[..., "tasks.dpu_tasks", "tasks.backend_health_task"]
```

The Celery worker never loads `tasks.ssh_tasks`, so when the backend dispatches `run_ssh_init.delay(...)`, the worker receives the message and immediately discards it:

```
ERROR/MainProcess: Received unregistered task of type 'tasks.ssh_tasks.run_ssh_init'.
The message has been ignored and discarded.
```

The backend side succeeds (`.delay()` returns an `AsyncResult`), so no error is raised there. The stale-execution janitor marks the task as `failed` 15 seconds later, but the module status is never updated (the callback never fires), and the stack status is never reconciled.

### Why it wasn't caught

1. **No test validated the `include` list.** Existing tests import `tasks.ssh_tasks` directly (Python `import` works fine — the file exists on disk). The Celery registration path is only exercised at worker boot, which no unit test covers.
2. **The error appears only in worker logs**, not in backend logs or the API response. The backend's `.delay()` call succeeds regardless of whether the worker knows about the task.
3. **No bare-metal deployment was attempted** between May 3 (regression) and May 6 (discovery).

### Fix

1. Added `"tasks.ssh_tasks"` back to `celery_app.py`'s `include=` list.
2. Rebuilt and redeployed backend + worker (`make deploy-backend`).

### Prevention: regression guard test

**File:** `tests/unit/test_celery_task_registration.py`

A self-maintaining unit test that:
1. AST-parses `services/execution/task_dispatch.py` to extract every `from tasks.X import ...` statement
2. Reads `celery_app.conf.include` at import time
3. Asserts every dispatched module is registered

This test requires no manual updates. When a new engine is added to `task_dispatch.py`, it automatically fails if the corresponding module isn't in the `include` list. Runs as part of `make pre-push`.

### Design considerations identified

**1. Single-line `include` list is high stomping risk**

The `include=` argument is a single long line with 15 modules. Any edit that rewrites the line risks dropping a module silently. Alternatives considered:
- Multi-line list (one module per line) — reduces merge conflict risk but doesn't prevent omission
- The regression guard test is the actual safety net — it fails at test time regardless of formatting

**2. Celery task dispatch is a fire-and-forget gap**

The dispatcher calls `.delay()` which puts a message on Redis and returns. If the worker discards the message, nothing in the system notices except:
- The stale-execution janitor (2-minute loop) — marks the task as `failed`
- But the janitor only touches the `tasks` table, not the module or stack status

The module stays in a transitional state (`initializing`, `applying`) because:
- The task body's exception handler deliberately avoids updating module status after lock release (to prevent stomping a lock held by another worker)
- The `CallbackTask.on_failure` callback only updates task status, not module/stack status
- `_update_stack_status_if_needed` is only called from within the task body on success/controlled failure, never from the exception handlers

The existing `update_stack_progress` / BUG-009 recovery handles this eventually when something calls it (e.g., `get_stack_status()` polling, or the next `run_deploy()` attempt), but there's no proactive reconciliation.

**3. Frontend hides the retry path**

The original `canDeployStack` condition excluded `deploying` from valid statuses. When combined with the above, users had no way to unstick a failed deployment without force-deleting and starting over. The frontend fix (branch `fix/stuck-deploying-retry`) adds `deploying` to the allowed statuses and shows "Retry Deploy" in the menu. The backend's stale-deploying guard (`stack_service.py:862-895`) then detects no active tasks, resets to `failed`, and proceeds.

### Files changed

| File | Change |
|------|--------|
| `backend/celery_app.py` | Added `"tasks.ssh_tasks"` back to `include=` list |
| `backend/tests/unit/test_celery_task_registration.py` | New regression guard test |
| `frontend-v2/src/pages/project-detail/ModuleGroupTable.tsx` | Allow retry when blueprint stuck in `deploying` |
| `docs/TROUBLESHOOTING.md` | Updated "Module Stuck in Deploying" section |
| `docs/POST_MORTEMS.md` | This document |
