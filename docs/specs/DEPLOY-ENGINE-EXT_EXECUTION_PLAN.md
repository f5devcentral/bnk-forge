# DEPLOY-ENGINE-EXT — Execution Plan

> Promoted from accepted `.agent` planning artifacts after implementation completion.

## Purpose

Convert the design/proposal work for external deployment-pack support into a practical execution sequence that another human or agent can follow without relying on `.agent` state.

## Parent items

- `DEPLOY-ENGINE-EXT-001` — epic
- `DEPLOY-ENGINE-EXT-002` — design pass
- proposal artifacts:
  - `DEPLOY-ENGINE-EXT-003`
  - `DEPLOY-ENGINE-EXT-004`
  - `DEPLOY-ENGINE-EXT-005`
  - `DEPLOY-ENGINE-EXT-006`

## Resolved decisions that MUST be preserved

1. **Trust boundary:** only admin-added / allowlisted `ModuleSource` records are eligible for first-phase external deployment-pack import/execution.
2. **Main UX terminology:** keep `module` as the primary user-facing concept.
3. **Script support:** narrowly governed only, not broad arbitrary CLI execution.
4. **Destroy semantics:** optional per engine/pack and explicitly declared.
5. **First non-OpenTofu engine:** `ansible`.

---

## Execution order

1. `DEPLOY-ENGINE-EXT-003a` — DB/model/schema foundation
2. `DEPLOY-ENGINE-EXT-003b` — manifest parser/validator + catalog normalization
3. `DEPLOY-ENGINE-EXT-003c` — module-library API serialization for new metadata
4. `DEPLOY-ENGINE-EXT-004a` — manifest-driven Git-source discovery/import
5. `DEPLOY-ENGINE-EXT-004b` — sync result/reporting + stale-pack reconciliation
6. `DEPLOY-ENGINE-EXT-005a` — ansible engine adapter + runner contract
7. `DEPLOY-ENGINE-EXT-005b` — ansible task dispatch/integration + outputs contract
8. `DEPLOY-ENGINE-EXT-006a` — backend capability exposure for project/stack/module APIs
9. `DEPLOY-ENGINE-EXT-006b` — frontend types + action gating + engine badges

---

## Work packages

### DEPLOY-ENGINE-EXT-003a — DB/model/schema foundation

Objective:
- add the minimum persistent schema needed for generalized deployment-pack metadata

Scope:
- `ModuleLibrary.engine_type`
- `ModuleLibrary.pack_manifest`
- migration strategy for existing rows
- keep all current metadata fields intact

Acceptance:
- `ModuleLibrary` can persist explicit engine type and full normalized pack manifest
- existing rows remain compatible
- no existing module-library/project-module flows are broken

### DEPLOY-ENGINE-EXT-003b — Manifest parser/validator + catalog normalization

Objective:
- implement `bnkforge.pack.json` parsing/validation and normalization into catalog fields

Scope:
- parse `bnkforge.pack.json`
- validate closed engine enum and lifecycle contract
- validate engine-specific required entrypoints
- normalize into:
  - `pack_manifest`
  - `engine_type`
  - `inputs_metadata`
  - `outputs_metadata`
  - `dependencies_metadata`

Acceptance:
- valid pack manifest parses cleanly
- invalid manifest fails with actionable validation error
- compatibility metadata is populated for downstream consumers

### DEPLOY-ENGINE-EXT-003c — Module-library API serialization for new metadata

Objective:
- expose new catalog metadata safely and truthfully from backend APIs

Scope:
- prefer stored `engine_type` over heuristic inference when available
- expose lifecycle capability metadata from pack manifest/normalized contract
- preserve legacy fallback when explicit metadata is absent

Acceptance:
- catalog API can return explicit engine type/capability data for pack-aware modules
- legacy OpenTofu and built-in K8s modules still serialize correctly

### DEPLOY-ENGINE-EXT-004a — Manifest-driven Git-source discovery/import

Objective:
- add the new Git-source sync path that discovers and imports deployment packs by manifest

Scope:
- clone approved Git `ModuleSource`
- discover `bnkforge.pack.json`
- validate each discovered pack
- create/update catalog rows keyed by source + path
- preserve fallback to legacy Terraform discovery only when no manifests are present

Acceptance:
- approved Git source with valid pack manifests imports modules into catalog
- no reliance on `.tf` heuristics for new manifest-driven sources
- legacy sources still work when no manifests are present

### DEPLOY-ENGINE-EXT-004b — Sync result/reporting + stale-pack reconciliation

Objective:
- make sync/import operationally truthful

Scope:
- structured pack-level sync errors
- source-level vs pack-level failure semantics
- mark removed packs inactive when no longer present in source
- richer sync result payloads

Acceptance:
- one bad pack does not poison entire source sync
- removed packs are no longer shown as active
- sync response includes useful reporting detail

### DEPLOY-ENGINE-EXT-005a — Ansible engine adapter + runner contract

Objective:
- implement the runtime engine for `engine_type=ansible`

Scope:
- `AnsibleEngine` implementing `DeploymentEngine`
- fixed runner profile `ansible-default`
- manifest-driven playbook/inventory/output artifact handling
- secret-safe logging/redaction

Acceptance:
- ansible module can perform validate/check/apply via engine contract
- output artifact contract is enforced
- no arbitrary command execution contract is introduced

### DEPLOY-ENGINE-EXT-005b — Ansible task dispatch/integration + outputs contract

Objective:
- wire Ansible execution into the current task/orchestration system

Scope:
- task dispatch recognizes explicit `engine_type=ansible`
- ansible task lifecycle updates module/task/deployment records consistently
- optional destroy only when pack declares it
- module outputs are normalized from structured artifact

Acceptance:
- ansible modules can be queued/executed like current engines
- task logs and statuses behave consistently with existing product expectations
- missing destroy support is handled truthfully

### DEPLOY-ENGINE-EXT-006a — Backend capability exposure for project/stack/module APIs

Objective:
- expose engine and lifecycle capability metadata across relevant backend surfaces

Scope:
- module library responses
- project module responses
- stack/blueprint detail responses where relevant
- backend remains source of truth for capabilities

Acceptance:
- APIs expose capability metadata sufficiently for frontend action gating
- fallback behavior remains correct for legacy modules

### DEPLOY-ENGINE-EXT-006b — Frontend types + action gating + engine badges

Objective:
- make the frontend mixed-engine-aware without changing the main module-centric UX

Scope:
- expand TS types for `ansible` / `script`
- add lifecycle capability typing
- show engine badges/details in module library and relevant project/blueprint views
- hide/disable unsupported actions

Acceptance:
- users can see engine/capability information
- unsupported actions are not shown as if available
- UI still primarily speaks in terms of modules

---

## Handoff notes

Do not change without explicit re-decision:
- allowlisted source-only trust boundary
- module-centric UI terminology
- optional destroy semantics
- Ansible-first engine priority

Suggested execution checkpoints:
1. schema/model change merged
2. manifest parsing/validation tests passing
3. manifest-driven source sync working on sample repo
4. ansible engine task path working end-to-end on a controlled sample pack
5. UI action gating verified for mixed capability modules
