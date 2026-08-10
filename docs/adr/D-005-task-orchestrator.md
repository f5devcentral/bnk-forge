# D-005 — TaskOrchestrator Module

- **Status:** Deferred (Accepted in spirit; parked behind D-001 Phase 3 Celery Canvas)
- **Date proposed:** 2026-05-13
- **Date deferred:** 2026-05-13
- **Backlog id:** `architecture-task-orchestrator`
- **Source memo:** `architecture_deepening_2026-05-13_six_candidates.md` (#1)
- **Depends on:** D-001 Phase 3 (Celery Canvas)
- **Resume trigger:** D-001 Phase 3 lands, OR Phase 1.6 `EntityLockService` shape is stable and we want to validate the orchestrator's lock parameter against it before Canvas.

## Context

Each engine's Celery task family duplicates orchestration:

- `backend/app/tasks/opentofu_tasks.py` (1389 lines)
- `backend/app/tasks/ansible_tasks.py` (462)
- `backend/app/tasks/kubernetes_tasks.py` (806)
- `backend/app/tasks/ssh_tasks.py` (598)
- `backend/app/tasks/proxy_deploy_tasks.py` (145)
- `backend/app/tasks/bnk_upgrade_tasks.py` (180)
- `backend/services/execution/parallel_tasks.py`, `backend/services/execution/task_dispatch.py`

Each task acquires `module_lock`, refreshes heartbeat, publishes task-started/completed events, validates phase transitions (`not_initialized → planned → applying → applied`), and marks `applied` / `*_failed` on failure. `_publish_task_completion` is re-implemented locally in `kubernetes_tasks.py:140-152`; ProxyDeploy invents `_publish_proxy_event`; BnkUpgrade uses an `on_output` lambda.

**Deletion test:** removing per-engine wrappers concentrates complexity 4× across engines into *the same* complexity — the seam is at the wrong layer. Tests must mock `module_lock` + `_notify_task_started` + `_publish_task_completion` per task.

## Decision (deeper shape)

A `TaskOrchestrator` Module that owns lock lifecycle, phase state machine, and event publishing. Interface (sketch — finalize in grilling loop):

```
TaskOrchestrator.run(engine_adapter, module_id, operation, variables) -> OperationResult
```

Engines collapse to `execute(variables, credentials) -> EngineResult` — pure work, no orchestration. Two adapters become real with Phase 1.6: entity-lock context (proxy/upgrade) and module-lock context (everything else).

**Strategy:** Strangler Fig. Phases A–E:

- **Phase A** — Define `EngineAdapter` Protocol in `backend/services/execution/engine_adapter.py`. Zero callers. Typecheck-only.
- **Phase B** — `TaskOrchestrator.run(...)` extracted from `opentofu_tasks.run_opentofu_init` lines 94-127 wrapper. Add `OpenTofuInitAdapter`. Zero Celery tasks call it.
- **Phase C** — Migrate `opentofu_tasks` Celery tasks one at a time (init → plan → apply → destroy). Each task body becomes `return orchestrator.run(...)`. Celery task `name=...` unchanged.
- **Phase D** — Migrate other engines (ansible → kubernetes → ssh), one engine per PR.
- **Phase E** — After D-001 Phase 1.6 lands EntityLockService, opt in proxy/upgrade by passing the entity-lock context manager to orchestrator. No orchestrator change needed.

## Consequences

**Leverage:** new engines implement one adapter instead of duplicating the lock + heartbeat + event skeleton.

**Locality:** "engine forgot to set `apply_failed` on raise" becomes impossible — the orchestrator owns terminal state.

**Test win:** ~50 task tests stop mocking past the interface. Orchestration gets one integration test against the seam.

**Compatibility:** strangler approach preserves Celery task names, return-dict shape, DB schema, WebSocket payloads, HTTP API.

## Why deferred

Slice A is a 6-PR refactor sequence with no user-visible value. D-001 Phase 3 (Canvas) reshapes the dispatch layer; doing TaskOrchestrator first risks touching the same code twice if Canvas surfaces constraints (idempotency, result shape, retry semantics) that ripple inward to the adapter Protocol.

## Open verifications before resuming

- Confirm same skeleton shape in `kubernetes_tasks.run_kubernetes_apply` + `ansible_tasks.run_ansible_apply`.
- Check `parallel_tasks.py` retry/timeout semantics — orchestrator must not change them.
- Architect verdict from 2026-05-13 is preserved in `.agent/tasks/active/task-orchestrator-slice-a/` — re-validate against Canvas design before extraction.

## References

- Source memo: `architecture_deepening_2026-05-13_six_candidates.md`
- Related: D-001 (module locking redesign — Phase 3 Canvas is the unblock)
- Code: `backend/services/module_lock.py:325-359` (already deep `module_lock` context manager)
- Task workspace: `.agent/tasks/active/task-orchestrator-slice-a/`
