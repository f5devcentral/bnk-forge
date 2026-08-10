# BNK-Forge v2 — Refactoring Plan

> Created: 2026-02-17
> Status: P0 + P1 + P2 + P3 Complete — all backend AND frontend refactoring done
> Branch: `agent/p0-engine-unification` (all agents work here)
> Codebase: v2.10.0, ~134k lines (68k backend, 62k frontend, 2k operator)

---

## Codebase by the Numbers

| Segment | Lines | Files |
|---------|------:|------:|
| Backend Python | 68,050 | ~120 |
| Frontend TS/TSX | 61,855 | ~100 |
| Operator Python | 1,922 | 6 |
| Docs/Config | ~12,000 | ~50 |
| **Total** | **~134k** | **~276** |
| Tests | 5,121 | 12 |

---

## 🔴 P0 — Two Parallel Execution Engines (4,036 lines of confusion)

### The Problem

`execution_engine.py` (1,739 lines) is the original monolith — still handles ALL OpenTofu work. The `execution/` directory (2,297 lines) is the "new" engine with `kubernetes_engine.py`, `operator_engine.py`, `engine_router.py`, etc. `opentofu_engine.py` (79 lines) was a dead stub — every method returned a hardcoded "delegated to existing handler" string. The migration was never finished.

### The Fix — Complete the migration (Option A)

> **⚠️ PHASE ORDER MATTERS.** Do not skip or reorder phases.
> Phase 1b is the critical gate — without it, `ExecutionEngine` still has two callers
> and cannot be safely refactored or deleted.

**Phase 1a** — Make `OpenTofuEngine` a real adapter ✅ DONE
- `opentofu_engine.py`: dead stub → 500-line adapter delegating to `ExecutionEngine`
- `engine_router.py`: `get_engine()` returns `OpenTofuEngine` for OT modules (never `None`)
- Commit: `3049a18` | 357 tests pass, 0 regressions

**Phase 1b** — Reduce direct ExecutionEngine callers ✅ DONE
- Removed `ExecutionEngine` from 5 files that only needed extracted functions
- `state_decryption_service.py` → `config_writer.write_encryption_config`
- `kubernetes_tasks.py` → `variable_assembler.can_execute`
- `stack_deployment_service.py` → dead import removed
- `opentofu_tasks.py` → `can_execute` calls replaced with `check_dependencies`
- Extracted `can_execute()` as standalone function in `variable_assembler.py`
- `execution/__init__.py` re-exports `can_execute` and `build_variables`
- Remaining 3 importers are appropriate: adapter, OT tasks (execution primitives), drift tasks (temp workspace)

**Phase 2** — Extract and slim `execution_engine.py` ✅ DONE
- `config_writer.py` (412 lines) extracted — pure file-generation functions, no DB access
- `variable_assembler.py` expanded (222→466 lines) — variable building + can_execute consolidated
- `execution_engine.py` shrunk from 1,739 → ~990 lines (build_variables, can_execute, config methods all delegate)
- Remaining in monolith: workspace prep, subprocess execution, output capture, destroy retry logic
- Commit: `e0cabec`

**Phase 3** — Rename and consolidate ✅ DONE
- `execution_engine.py` → `execution/opentofu_runtime.py`
- Class renamed: `ExecutionEngine` → `OpenTofuRuntime`
- Backward-compat alias `ExecutionEngine = OpenTofuRuntime` in runtime file
- Backward-compat shim at `services/execution_engine.py` (re-exports from new path)
- All importers updated to use new path
- All comments/docstrings updated to reference `OpenTofuRuntime`

### Final Architecture

```
                    ┌─────────────────────────────────┐
                    │        EngineRouter              │
                    │   get_engine(module_path)        │
                    └──────┬──────────┬───────────┬────┘
                           │          │           │
                    ┌──────▼──┐ ┌─────▼─────┐ ┌──▼──────────────┐
                    │ K8s     │ │ Operator  │ │ OpenTofuEngine  │
                    │ Engine  │ │ Engine    │ │ (adapter)       │
                    └─────────┘ └───────────┘ └────────┬────────┘
                                                       │ delegates to
                                              ┌────────▼────────┐
                                              │ OpenTofuRuntime  │
                                              │ (~990 lines)    │
                                              │ subprocess mgmt │
                                              └────────▲────────┘
                                                       │ also called by
                                              ┌────────┴────────┐
                                              │ opentofu_tasks   │  (run_init/plan/apply/destroy)
                                              │ drift_tasks      │  (temp workspace + plan_detailed)
                                              └─────────────────┘
                                              
  execution/ package public API:
    OpenTofuRuntime        ── core subprocess management
    variable_assembler.py  ── can_execute(), build_variables()
    config_writer.py       ── write_tfvars(), write_backend/encryption/provider_config()
```

### Key Files

| File | Lines | Status |
|------|------:|--------|
| `services/execution/opentofu_runtime.py` | ~990 | Core OT runtime (moved from execution_engine.py) |
| `services/execution/opentofu_engine.py` | 501 | Adapter: DeploymentEngine interface |
| `services/execution/config_writer.py` | 412 | Extracted config generation |
| `services/execution/variable_assembler.py` | ~466 | Variable building + can_execute |
| `services/execution/engine_interface.py` | 174 | ABC — the contract |
| `services/execution/engine_router.py` | 307 | Returns engine for ALL modules |
| `services/execution/kubernetes_engine.py` | 896 | K8s engine |
| `services/execution/operator_engine.py` | 468 | Operator engine |
| `services/execution_engine.py` | 12 | Backward-compat shim (re-exports) |
| `tasks/opentofu_tasks.py` | 1,559 | Uses OpenTofuRuntime for execution primitives |

### Risks
- `execution_engine.py` is battle-tested on real AWS deployments. Don't rewrite, wrap.
- OpenTofu tasks have complex workspace/state management. Don't simplify prematurely.
- Tests: 487 passing. Run full suite after each phase.

---

## 🔴 P0 — God Route Files

### The Problem

Routes contain business logic instead of being thin HTTP handlers:

| Route File | Lines | `db.query()` | Assessment |
|------------|------:|:------------:|-----------|
| `kubernetes.py` | 2,925 | 21 | God file: clusters, resources, namespaces, gateways, firewall, egress, SNAT, topology, scanning, tunnels |
| `project_modules.py` | 2,486 | 54 | God file: module CRUD + execution (plan/apply/destroy) + variables |
| `api.py` | 821 | 24 | Catch-all: overlaps kubernetes + projects routes |

**Total: 318 raw `db.query()` calls across route files.** These should be service-layer methods.

### The Fix

**P1a — Split the god routes (2-3 days):** ✅ DONE
- 471 tests pass, 0 regressions (34 pre-existing helm security failures unrelated)
- `kubernetes.py` backward-compat shim retained (51 lines) for any external references

```
routes/kubernetes.py (2,925 lines) → split into:
  routes/k8s/__init__.py         — package init (23 lines)
  routes/k8s/_shared.py          — shared helpers/deps (159 lines)
  routes/k8s/clusters.py         — Cluster CRUD, scanning, adaptive modules (679 lines)
  routes/k8s/f5bnk.py            — F5 BNK topology, gateways, firewall, egress, SNAT (1,292 lines)
  routes/k8s/resources.py        — Generic K8s resource CRUD (758 lines)
  routes/k8s/tunnels.py          — SSH tunnel management (144 lines)

routes/project_modules.py (2,486 lines) → split into:
  routes/project_modules.py           — Module CRUD (731 lines)
  routes/project_execution.py         — Plan, apply, destroy, deploy-all, destroy-all (752 lines)
  routes/project_deployments.py       — Deployment history (438 lines)
  routes/project_variable_mappings.py — Variable mapping CRUD (338 lines)
```

> Note: `api.py` (821 lines) split deferred — it has distinct endpoints that don't cleanly map to
> the k8s/ or project routes. Can be addressed in P1b when service layer is extracted.

**P1b — Extract service layer (2-3 days):** ✅ COMPLETE (Phase 1 + Phase 2)
- Created 9 service classes covering ALL significant route files
- 476 tests pass, 0 new regressions

```
Phase 1 services (top 3 by DB calls):
  services/project_module_service.py  — 1,097 lines (from project_modules + project_execution)
  services/drift_service.py           — 542 lines (from drift.py)
  services/stack_service.py           — 605 lines (from stacks.py)

Phase 2 services (remaining 6):
  services/api_service.py                 — 491 lines (from api.py)
  services/credential_template_service.py — 543 lines (from credential_templates.py)
  services/system_service.py              — 488 lines (from system.py)
  services/cluster_management_service.py  — 309 lines (from k8s/clusters.py)
  services/cost_service.py                — 307 lines (from cost.py)
  services/module_source_service.py       — 246 lines (from module_sources.py)

Route reductions (Phase 1):
  project_modules.py     731 → 222 lines (70% thinner)
  project_execution.py   753 → 134 lines (82% thinner)
  drift.py               666 → 206 lines (69% thinner)
  stacks.py            1,017 → 293 lines (71% thinner)

Route reductions (Phase 2):
  api.py                  822 → 194 lines (76% thinner)
  credential_templates.py 896 → 209 lines (77% thinner)
  system.py             1,370 → 393 lines (71% thinner)
  k8s/clusters.py         680 → 247 lines (64% thinner)
  cost.py                 431 → 132 lines (69% thinner)
  module_sources.py       368 → 120 lines (67% thinner)
```

Total: ~291 DB calls moved from routes to services across all 10 route files.

**P1c — Standardize error patterns (1 day):** ✅ DONE
- Replaced all `HTTPException` with `core.errors` classes across 12 route files
- 0 HTTPException references remaining in routes
- 5 legitimate JSONResponse uses retained (410 Gone, 202 Accepted, 200 OK)
- Commit: `fb789e8` | 487 tests pass

---

## 🟡 P1 — Worth Fixing for Maintainability

| # | Item | Lines | Status |
|---|------|------:|--------|
| 5 | `models.py` — 36 models in one file | 1,573 | ✅ P1-5 — split into 11 domain files |
| 6 | `kubernetes_service.py` — god service | 2,505 | ✅ P1-6 — split into 7 mixin modules |
| 7 | Frontend `api.ts` — ~369 methods in one object | 1,313 | ✅ P2a — split into 13 modules |
| 8 | Frontend `types/index.ts` — 145 interfaces | 1,709 | ✅ P2b — split into 17 type files |
| 9 | Frontend god pages (ProjectDetailV2, F5BNK, KubernetesV2) | 4,225 | ✅ P2c — split into focused sub-modules |
| 10 | `SESSION_STATE.md` — bloated state file | 2,148→59 | ✅ Trimmed |

**P1-5 — Split `models.py` (1,580 lines, 36 models → 11 domain files)** ✅ DONE
```
models.py (monolith) → models/ package:
  models/__init__.py         — barrel re-export (backward compat, zero breaking changes)
  models/kubernetes.py       — KubernetesCluster, K8sGateway, FirewallPolicy, EgressConfiguration, SnatPool
  models/project.py          — Project, ProjectModule, ProjectSecret, Environment, Deployment, DeploymentLog
  models/module.py           — ModuleLibrary, ModuleSource, ModuleSnapshot
  models/variable.py         — VariableMapping, VariableMappingTemplate
  models/stack.py            — StackTemplate, StackInstance
  models/task.py             — Task, ParallelExecution
  models/drift.py            — DriftCheck, DriftSettings, CostEstimate
  models/system.py           — ApplicationSetting, SyncJob, CloudCredentialTemplate, User, AuditLog, Notification, HelmChart
  models/operator.py         — OperatorRegistrationToken, ConnectedOperator, OperatorCommandQueue
  models/alert.py            — AlertChannel, AlertHistory
  models/bnk_upgrade.py      — BnkUpgrade
```

**P1-6 — Split `kubernetes_service.py` (2,506 lines → 7 mixin modules)** ✅ DONE
```
services/kubernetes_service.py (monolith) → services/kubernetes/ package:
  services/kubernetes/__init__.py    — KubernetesService (assembled from mixins)
  services/kubernetes/_base.py       — cluster access, kubeconfig, connection testing (152 lines)
  services/kubernetes/_resources.py  — generic CRUD: get, create, update, delete (418 lines)
  services/kubernetes/_describe.py   — describe resource, events (333 lines)
  services/kubernetes/_pods.py       — pod logs, restart, container listing (137 lines)
  services/kubernetes/_metrics.py    — pod/node metrics, CPU/memory parsing (187 lines)
  services/kubernetes/_rollouts.py   — rollout history, status, undo, restart (194 lines)
  services/kubernetes/_operations.py — patch, label, annotate, scale, cordon, drain (244 lines)
  services/kubernetes_service.py     — backward-compat shim (11 lines)
```

---

## 🟢 P2 — Frontend Consolidation + Minor / Cosmetic

**P2a — Split `api.ts` (1,330 lines → 13 domain modules)** ✅ DONE (commit `6951897`)
```
frontend-v2/src/lib/api.ts (1,330 lines) → split into:
  lib/api/index.ts              — barrel re-export (backward compat, zero breaking changes)
  lib/api/client.ts             — shared axios instance + helpers
  lib/api/projects.ts           — project CRUD + deploy/destroy
  lib/api/modules.ts            — module CRUD + execution
  lib/api/stacks.ts             — stack CRUD + deployment
  lib/api/kubernetes.ts         — K8s cluster + resource management
  lib/api/f5bnk.ts              — F5 BNK topology, gateways, firewall
  lib/api/helm.ts               — Helm repos + releases
  lib/api/credentials.ts        — credential templates
  lib/api/secrets.ts            — project secrets
  lib/api/system.ts             — system info + upgrade
  lib/api/cost.ts               — cost estimates
  lib/api/module-sources.ts     — module source sync
```

**P2b — Split `types/index.ts` (1,724 lines, 147 interfaces → 17 type files)** ✅ DONE (commit `60fd1db`)
```
frontend-v2/src/types/index.ts (1,724 lines) → split into:
  types/index.ts                — barrel re-export (backward compat)
  types/project.ts              — Project, ProjectModule, Deployment
  types/stack.ts                — StackTemplate, StackInstance, StackLayer
  types/kubernetes.ts           — KubernetesCluster, K8sResource, Namespace
  types/f5bnk.ts                — F5BNK topology, gateway, firewall types
  types/helm.ts                 — HelmRelease, HelmRepository, HelmChart
  types/credential.ts           — CloudCredentialTemplate
  types/secret.ts               — ProjectSecret
  types/module.ts               — ModuleLibrary, ModuleSource
  types/system.ts               — SystemInfo, VersionInfo, UpgradeReadiness
  types/cost.ts                 — CostEstimate, CostComponent
  types/task.ts                 — Task, TaskLog, ParallelExecution
  types/drift.ts                — DriftCheck, DriftDetail
  types/execution.ts            — ExecutionResult, PlanResult
  types/variable.ts             — VariableMapping, VariableSchema
  types/common.ts               — shared enums, pagination, API response wrappers
  types/websocket.ts            — WebSocket message types
```

**P2c — Split 3 god pages (4,229 lines → focused sub-modules)** ✅ DONE (commit `6f25d5f`)
```
pages/ProjectDetailV2.tsx (1,828 lines) → split into:
  pages/project-detail/index.tsx        — main page shell + routing
  pages/project-detail/ModulesTab.tsx   — module list + actions
  pages/project-detail/StacksTab.tsx    — stack management
  pages/project-detail/SecretsTab.tsx   — project secrets
  pages/project-detail/hooks.ts         — shared queries + mutations

pages/F5BNK.tsx (1,349 lines) → split into:
  pages/f5bnk/index.tsx                 — main page + sidebar
  pages/f5bnk/TopologyView.tsx          — topology visualization
  pages/f5bnk/ResourceView.tsx          — resource CRUD
  pages/f5bnk/PolicyView.tsx            — firewall + security policies

pages/KubernetesV2.tsx (1,052 lines) → split into:
  pages/kubernetes/index.tsx            — main page + cluster selector
  pages/kubernetes/ResourceExplorer.tsx — resource browsing + CRUD
  pages/kubernetes/ClusterManagement.tsx— cluster connection management
```

| # | Item | Status |
|---|------|--------|
| 11 | Dead file: `F5BNKResourceList.tsx` (344 lines, never imported) | ✅ Deleted |
| 12 | Stale architecture docs (ARCHITECTURE_REVIEW, IMPLEMENTATION_PLAN, etc.) | ✅ Archived to `docs/architecture/archive/` |
| 13 | 32 Alembic migrations — not a problem, don't squash | No action needed |

---

## Progress Tracker

| Task | Est. Effort | Status | Commit |
|------|------------|--------|--------|
| **P0: Engine unification** | | | |
| ├─ Phase 1a: OpenTofuEngine adapter | 1 day | ✅ Done | `3049a18` |
| ├─ Phase 1b: Reduce direct EE callers (8→3) | 1 day | ✅ Done | `42407ee` |
| ├─ Phase 2: Extract & slim monolith | 1-2 days | ✅ Done | `e0cabec` |
| └─ Phase 3: Rename & consolidate | 0.5 day | ✅ Done | `5409736` |
| **P1a: Split god routes** | 2-3 days | ✅ Done | `c692fc9` |
| **P1b: Extract service layer** | 2-3 days | ✅ Complete (9 services) | — |
| **P1c: Standardize errors** | 1 day | ✅ Done | `fb789e8` |
| **P1-5: Split models.py** | 0.5 day | ✅ Done | — |
| **P1-6: Split kubernetes_service.py** | 0.5 day | ✅ Done | — |
| **P2a: Split api.ts** | 0.5 day | ✅ Done | `6951897` |
| **P2b: Split types/index.ts** | 0.5 day | ✅ Done | `60fd1db` |
| **P2c: Split god pages** | 1 day | ✅ Done | `6f25d5f` |
| **P3a: Delete dead files** | 30 min | ✅ Done | `5f3f98f` |
| **P3b: Archive stale docs** | 30 min | ✅ Done | `5f3f98f` |
| **P3c: Slim SESSION_STATE** | 1 hour | ✅ Done | — |

**All refactoring complete.** Backend (P0, P1a-c, P1-5, P1-6) and frontend (P2a-c) consolidation all done. No remaining items.

---

## Post-Refactor Backlog (Good Ideas from Stale Branches)

> These were found during branch cleanup (2026-02-17). Each had value but the source
> branches diverged too far from main to merge. Re-implement fresh against the
> refactored codebase once P1b is done.

| # | Item | Source | Effort | Priority |
|---|------|--------|--------|----------|
| B1 | **Helm `_validate_arg` for timeout/username/password** — security gap, no validation on these CLI args today | PR #39 `sentinel-helm-arg-injection-fix` | 30 min | ✅ Done |
| B2 | **Centralized `utils/security.py`** — reusable `validate_cli_arg()` instead of private methods per service. Enables consistent validation in helm, k8s, aws services | PR #35 `sentinel/fix-argument-injection` | 1 hour | ✅ Done |
| B3 | **`load_only()` on joined loads** — `deferred()` + `load_only()` across 14 backend files | PR #37 `bolt/optimize-module-queries` | 30 min | ✅ Done (`8d07362`) |
| B4 | **Kahn's algorithm for dependency graph** — O(N+E) topological sort | PR #25 `bolt-dependency-resolution` | 1 hour | ✅ Done (`28047d3`) |
| B5 | **Accessibility aria-labels** — comprehensive a11y pass, 22 frontend files | PR #34, #38 `palette-*` | 30 min | ✅ Done (`b3bfef3`) |
| B6 | **E2E test suite** — 84 tests updated for new file structure | `agent/sprint-15-e2e-tests` | 2-3 hours | ✅ Done (`e09c233`) |

---

## What NOT to Refactor

- **Module system** (4,343 lines, 24 files) — Clean, well-tested (263 tests), single-purpose files
- **Operator** (1,922 lines) — Tight, focused, new code
- **Core** (2,076 lines) — Properly structured
- **Tests** (5,121 lines) — Keep all, add more during refactoring
- **Alembic migrations** (32 files, 1,813 lines) — Don't squash, they're the DB history

---

## Models.py Note (1,573 lines, 36 models) — ✅ DONE

Split into `models/` package with 11 domain-specific files + barrel `__init__.py`. All 118+ import sites unchanged (backward compat via re-exports). 36 tables verified registered with SQLAlchemy Base.
