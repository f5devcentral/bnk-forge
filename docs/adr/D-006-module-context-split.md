# D-006 — Engine-specific ModuleContext

- **Status:** Proposed
- **Date proposed:** 2026-05-13
- **Backlog id:** `architecture-module-context-split`
- **Source memo:** `architecture_deepening_2026-05-13_six_candidates.md` (#2)
- **Depends on:** none (independent of D-005, but cleaner after D-005 lands)
- **Resume trigger:** any time after D-005 Phase A defines the EngineAdapter Protocol — natural next step on the same surface.

## Context

- `backend/services/execution/engine_interface.py` defines `ModuleContext`
- All engine implementations under `backend/app/tasks/` consume it

One `ModuleContext` carries `pack_manifest`, `deploy_model`, `workspace_path`, `module_source_kind`, etc. Fields are populated conditionally by engine. Kubernetes tests must know what Terraform expects in `workspace_path`; `kubernetes_tasks` tests set up `pack_manifest` even for Helm-only paths.

**Deletion test:** removing the grab-bag shape doesn't eliminate the fields, but it eliminates the false promise that every consumer needs every field. The Interface advertises depth it doesn't have.

## Decision (deeper shape)

Split into a `BaseModuleContext` (fields every engine needs) plus per-engine `K8sModuleContext`, `TerraformModuleContext`, `AnsibleModuleContext`, `SSHModuleContext`. Each engine's entry point takes its own type.

## Consequences

**Locality:** adding a Kubernetes-only field stops requiring updates to TerraformModuleContext fixtures.

**Leverage:** compile-time guarantees replace runtime `if ctx.deploy_model == "helm"` branches.

**Test win:** fixtures shrink to what the engine actually consumes; a new engine doesn't require existing engines' tests to change.

## References

- Source memo: `architecture_deepening_2026-05-13_six_candidates.md`
- Related: D-005 (EngineAdapter Protocol gives this a natural home)
- Code: `backend/services/execution/engine_interface.py`
