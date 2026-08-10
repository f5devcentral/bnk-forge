# D-032 — Dependency-Chain-Aware Variable Lifecycle & Day-2 Tofu-Style Updates

- Status: Proposed
- Date: 2026-06-14
- Supersedes: none
- Related: D-008 (variable context), D-019 (dynamic-by-default), D-028 (unified blueprint catalog), D-029 (EKS+BNK blueprint e2e reliability)

## Context

forge is a UI + engine over the real `tofu` binary. Module variables flow into
`terraform.tfvars.json` through a 7-layer assembler
(`services/execution/variable_assembler.py:326-603`). Each variable declares a
`source` in `ModuleLibrary.inputs_metadata`: `user` (real input), `module`
(wired from a dependency's output via `from_module`/`from_output`, e.g.
`eks.vpc_id ← infra/aws/vpc.vpc_id`), or project/credential context
(`aws_region`, `project_name`, …). The assembler already classifies and
resolves these at plan/apply (Layer 3 wiring `:508-568`, Layer 5 user overrides
`:582-587`), and hard-fails only on a genuinely missing *required* dependency
output (`:533-539`).

Two defects break the "UI over tofu" contract:

1. **Over-demanding create-time gate.** `get_required_inputs`
   (`services/imported_blueprint_service.py:166-198`) and the `variables_schema`
   branches of the validators (`project_module_service.py:244-259`,
   `:1142-1150`) check `required` from `variables_schema` *without* consulting
   `inputs_metadata.source`. For `aws-k8s-foundation` (release 25, modules
   `infra/aws/{vpc,security,eks,storage,high-performance-nodes}`) this treats all
   43 required vars as user-required — including dependency outputs
   (`vpc_id`, subnet ids, role ARNs, `security_group_id`) and context vars —
   blocking one-shot deploy on values the chain supplies at apply time. A
   correct, source-aware resolver **already exists** but is private to the
   deploy gate (`parallel_execution_service._check_missing_variables:615-669`)
   and a source-aware validator exists unused on the edit paths
   (`variable_parser.validate_user_variables:544-607`).

2. **No day-2, chain-aware re-apply.** tofu lets you change a var and
   `apply -var` idempotently, cascading to dependents. forge can edit
   `variable_overrides` (`PUT /api/project-modules/{id}`), detects the change
   (`workspace_manager.vars_changed`/`vars_hash:647-710` — which includes
   dependency outputs because it calls `build_variables`), and invalidates the
   saved plan — but nothing re-plans/re-applies automatically, and **nothing
   propagates downstream**: the deploy continuation `_trigger_next_stack_module`
   is **stack-scoped** (`tasks/_tofu_helpers.py:89-90`) and imported-blueprint
   modules have **no `stack_instance_id`** (`imported_blueprint_service.py:289-297`).

## Decision

### 1. Single source of truth for "what must the user still provide?"
Extract the existing `_check_missing_variables` logic into
`services/execution/variable_resolution.py` (`unsatisfied_user_inputs`,
`classify_input_source`). The deploy gate, the create-time required-inputs
aggregator, and the edit-time validators all consult this one resolver. The
**resolution rule**:

> A variable is user-required at create/edit time iff its `inputs_metadata`
> source is `user`, it has no `default`, it is not wired by a blueprint literal,
> and no upstream layer (project/credential context, dependency wiring) supplies
> it. Vars with source `module`/`project`/context are **deferred** to the
> assembler and validated at plan/apply, never at create. The gate is, by
> construction, never stricter than the assembler — it cannot demand what the
> chain will supply.

`get_required_inputs` is driven off `inputs_metadata` (source-bearing) rather
than `variables_schema`; `variables_schema` remains a fallback only for legacy
modules with no `inputs_metadata`.

### 2. No hardcoded values where they are adjustable
- Blueprint manifests must **never** carry `inputs` literals for `source:module`
  vars (they would shadow dependency wiring at Layer 5). A manifest validator
  rejects this on import.
- For `source:user` vars, blueprint literals are permitted but are
  default-class: the Slice-1 user-override overlay already ensures user input
  wins. A hygiene warning flags dead literals equal to the module default.
- Optional modules that are **present but not opted-in** are added with
  `enabled=False`; disabled modules are already excluded from layering and
  dispatch (`dependency_graph_service.py:170-173`,
  `parallel_execution_service._dispatch_first_wave`), so their inputs are
  neither required nor deployed unless the user enables them. This fixes
  `high-performance-nodes` (optional=True) force-requiring `ecr_registry`.

### 3. Day-2 variable change → idempotent, chain-aware re-apply
New endpoint `POST /api/projects/{project_id}/reapply`
`{ module_id?, variable_overrides?, propagate=true }`:
1. Optionally write `variable_overrides` (source-aware validated) — the `-var`
   equivalent.
2. Seed the **changed root** and compute its **downstream transitive closure**
   via `DependencyGraphService.get_reverse_dependencies` (new recursive helper).
3. Re-apply the closure in dependency order using the existing idempotent
   `_dispatch_first_wave` machinery with `force_new_plan=True` for any module
   whose `vars_changed()` is true; unchanged modules are no-ops (empty tofu
   plan). `build_layers_for_modules` orders the closure.
4. **Project-scoped continuation** (the one new orchestration piece): on a
   successful apply under a reapply `run_handle`, a project-scoped
   `_trigger_next_reapply_module` (mirroring `_trigger_next_stack_module`)
   re-checks each downstream dependent's `vars_changed()` *after* the upstream's
   new outputs are persisted, and queues only the dependents whose wired outputs
   actually changed. Fail-closed: a failed upstream stops the cascade.

**Idempotency** rests on existing primitives: `_dispatch_first_wave` skips
`applied && !vars_changed` and live-task modules; tofu apply against unchanged
state is a no-op; `vars_hash` already factors dependency outputs. One fix:
persist `vars_hash` on apply success (today only on plan,
`opentofu_tasks.py:454`) to close the drift-detection window.

**Locking & failure:** per-module applies use the existing heartbeat+fence lock
and pre-apply snapshot; the route reuses the `deploy-all` in-flight-task
conflict guard. Rollback is not automatic (tofu has no transactional
multi-module rollback) — partial upstream apply is the correct tofu semantic;
the user re-runs `reapply` idempotently after fixing a failure. Progress is read
from the existing `GET …/orchestration/{run_handle}` endpoint.

## Consequences

- One-shot deploy of `aws-k8s-foundation` and similar dependency-chained
  blueprints stops being blocked on wired/context vars.
- Editing a deployed variable and re-applying — including downstream
  propagation — works through forge as it does on the CLI.
- Single classification authority eliminates the validator/assembler drift class
  of bug.
- New project-scoped continuation must be `run_handle`-gated and fail-closed to
  avoid runaway cascades; it doubles as a fix for the latent project-scoped
  first-deploy continuation gap (imported blueprints beyond layer 0).
- Manifest validator may flag previously-imported manifests; `source:module`
  literal is a hard error only for new imports, a warning for existing.

## Open decision (Slice 6 approach)
Two ways to get a project-scoped re-apply continuation for imported blueprints:
- **(a) New project-scoped trigger** — `_trigger_next_reapply_module` mirroring
  the stack one, `run_handle`-gated. Keeps imported-blueprint projects free of
  stack coupling; also fixes the latent first-deploy continuation gap.
- **(b) Give imported-blueprint projects a `stack_instance_id`** — reuse the
  existing stack continuation as-is. Less new code, but couples blueprint
  projects to the stack abstraction and may carry stack semantics they don't
  want.

**Decision (2026-06-14): option (b) — converge imported-blueprint projects onto the stack_instance model.** `create_project_from_release` now creates a `StackInstance` row and sets `stack_instance_id` on every module it creates, reusing the existing, proven stack continuation (`_update_stack_status_if_needed` → `_trigger_next_stack_module`) instead of writing a parallel project-scoped trigger. This fixes BOTH defects with one change: deploy-all auto-chains every layer (vpc→security→eks) on a single click (the trigger no longer early-returns on a null `stack_instance_id`), and the project-details UI renders the blueprint header with nested modules (it groups by `stack_instance_id` and reads `stack_instances`). Because blueprint projects are created from `blueprint_catalog` **releases**, not legacy `stack_templates`, `stack_instances.template_id` is made nullable and a nullable `blueprint_release_id` FK is added — the release remains the single source of truth and the stack_instance is a thin provenance link (no synthetic StackTemplate row, no manifest duplication). Destroy is covered by the same change (destroy completion already fires the project/stack-aware `_trigger_next_destroy_module`; modules now route through the stack-scoped destroy branch). Existing pre-fix imported-blueprint projects keep NULL linkage and must be re-created; a one-time backfill is an optional follow-up. This restores the originally-intended "stacks = blueprints" model that fell out when the imported-blueprint path was built without the stack_instance linkage (ref: `aws-syd-test`, which carried it via an ad-hoc agent fix). Option (a) (a separate `_trigger_next_reapply_module`) is dropped for the keystone path; a project-scoped trigger may still be revisited for the `module_id=null` drift mode (Slice 7).

## Slice plan
1. (DONE) User-override overlay + module-schema aggregation.
2. Source-aware required-inputs + edit validators via shared resolver.
3. Optional-module non-blocking + enable-on-opt-in.
4. Manifest hygiene validator (no `source:module` literals).
5. Day-2 single-module `reapply` (no propagation).
6. Downstream propagation chain (project-scoped continuation).
7. (Optional) `reapply` drift mode (`module_id=null`).

Slices 2-4 are unit/component/localhost-provable. Slices 5-6 are
component-provable with a stubbed engine; full output-change cascade validates
on AWS `aws-k8s-foundation` (ties to D-029).
