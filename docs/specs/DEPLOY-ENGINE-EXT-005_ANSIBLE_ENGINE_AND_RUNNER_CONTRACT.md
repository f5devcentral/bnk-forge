# DEPLOY-ENGINE-EXT-005 — Ansible Engine and Governed Runner Contract

> Promoted from accepted `.agent` planning artifacts after implementation completion.

## Objective

Add `ansible` as the first new external deployment-pack engine using a fixed BNK-governed runner contract, while fitting into the existing engine/task/orchestration architecture.

## Decision summary

### Adopt

1. **Ansible is the first non-OpenTofu external deployment engine.**
2. **Implement it through the existing `DeploymentEngine` abstraction**, not a separate runtime framework.
3. **Use a fixed runner profile (`ansible-default`)**, not arbitrary runtime selection.
4. **Support a truthful capability matrix**, not fake OpenTofu-style parity.
5. **Destroy is optional and pack-declared** via optional destroy playbook entrypoint.
6. **Outputs must come from a structured file/artifact contract**, not scraped stdout.

---

## Why Ansible is the right first engine

Evidence from the current architecture:

1. the engine abstraction already exists
2. task orchestration already routes by engine family
3. the system already handles long-running infra operations and credential injection
4. Ansible maps well to infrastructure deployment semantics without opening arbitrary CLI execution

Conclusion:
- Ansible exercises generalized engine behavior, credential handling, outputs, and lifecycle truthfulness without expanding into a generic script runner

---

## Engine integration model

Implement `AnsibleEngine` under `backend/services/execution/` conforming to `DeploymentEngine`.

Recommended files:
- `backend/services/execution/ansible_engine.py`
- `backend/services/execution/ansible_runner.py` (if separation is useful)
- updates in:
  - `engine_router.py`
  - `task_dispatch.py`
  - engine-aware task execution layer

Why:
- fits current execution architecture and preserves existing task lifecycle and observability patterns

---

## Task execution model

Reuse the current Celery-backed task lifecycle model, adding ansible-aware routing rather than building a separate async system.

Current behavior already includes:
- task DB records
- queued / in_progress / completed / failed lifecycle
- task logs
- deployment records
- module status updates

Recommendation:
1. best fit: add dedicated ansible task functions mirroring k8s/opentofu task signatures
2. acceptable alternative: a more general engine-task wrapper if introduced cleanly

---

## Runner contract

Use a fixed BNK-governed runner profile named:
- `ansible-default`

Runner responsibilities:
- execute `ansible-playbook` with controlled flags
- materialize needed inventory and credentials into bounded workspace locations
- capture stdout/stderr for task logs
- enforce timeout and exit-code handling
- capture structured outputs artifact if declared

Non-goals for first phase (scoped to the **ansible engine only** — the later
`container` engine deliberately inverts the first item for vendor `*ctl` tools;
see DEPLOY-ENGINE-EXT-003 Amendment A):
- no custom container image per pack
- no runtime package installation declared by pack
- no arbitrary shell pre/post hooks in manifest

---

## ModuleContext / execution inputs

Use the existing `ModuleContext` and extend it only if necessary in a backward-compatible way.

Helpful current fields already available:
- `module_id`
- `project_id`
- `path`
- `category`
- `variables`
- `credentials_env`
- `workspace_path`

Recommendation:
- avoid adding many ansible-specific fields unless broadly reusable
- derive runner inputs from manifest + current module/project data at runtime assembly time

---

## Lifecycle capability model for Ansible

Represent Ansible behavior truthfully using the accepted capability-matrix model.

### Semantics

#### init
- lightweight and optional in meaning
- may validate workspace/runner readiness and manifest entrypoints

#### plan
- supported where feasible via:
  - `--syntax-check`
  - `--check`
  - optional diff output if enabled
- should not promise full OpenTofu-style resource-change precision

#### apply
- required
- standard `ansible-playbook` run against declared playbook/inventory

#### destroy
- supported only when manifest declares a destroy playbook
- otherwise action is unavailable

#### outputs
- required through structured output artifact contract

#### drift/refresh
- limited; do not overclaim

---

## Manifest-driven entrypoint requirements

Required for ansible packs:
- `deployment_pack.engine = ansible`
- `deployment_pack.runner_profile = ansible-default`
- `deployment_pack.entrypoints.playbook`

Optional ansible entrypoints:
- `inventory_source`
- `destroy_playbook`
- `outputs_file`

Recommended first-phase rule:
- require `outputs_file` for ansible packs

Reason:
- output normalization is too important to leave implicit in the first engine slice

---

## Inventory and credential model

Use platform-managed file/env injection, not repo-embedded secrets.

Inventory source modes:
- `generated`
- `secret_file`
- `pack_file`

Credential support:
- env-based cloud credentials
- SSH key file materialization from managed secrets
- kubeconfig file materialization when needed
- vault password or secret files only through managed secret references

Rule:
- the manifest may declare what is needed, but secrets are sourced from platform-managed data only

---

## Output contract

Ansible outputs must be read from a structured JSON artifact written by the run, not inferred from human-readable logs.

Contract:
- manifest declares `outputs_file`
- runner expects a JSON object at that path after apply
- file contents are normalized into module outputs

Runtime behavior:
- if file exists and parses → outputs imported successfully
- if file missing but outputs declared → operation may succeed, but output status must be marked partial/unavailable
- if outputs are declared empty → no artifact required

---

## Workspace model

Use the existing persistent workspace approach where practical.

Recommended behavior:
- source/pack contents materialized into module workspace
- generated inventory / secret files / output artifacts live under the workspace
- destroy/cleanup removes ephemeral ansible-generated artifacts but respects broader workspace retention policies

---

## Logging, observability, and safety

Ansible execution must follow the same task-log and deployment-record discipline as current engines, with secret-safe redaction.

Required behaviors:
- stdout/stderr captured into task logs
- task status updates emitted consistently
- deployment record created for apply/destroy/init where appropriate
- secret material never logged
- command line should avoid exposing secret values

Redaction policy should explicitly cover:
- ansible extra vars containing secrets
- vault/password material
- SSH private key content

---

## Dispatch/routing recommendation

Replace the current binary routing assumption (`kubernetes` vs `opentofu`) with engine-type-aware dispatch.

Dispatch should prefer explicit `library_module.engine_type` when available:
- `kubernetes` → k8s tasks
- `opentofu` → opentofu tasks
- `ansible` → ansible tasks
- `script` → deferred/not implemented yet

Fallback path:
- preserve current legacy inference only where explicit engine metadata is absent

---

## Failure semantics

Distinguish clearly among:
1. manifest/config errors
2. runner/environment errors
3. playbook execution failures
4. output artifact failures

Suggested user-visible behavior:
- manifest/config errors: fail fast before playbook run
- runner errors: fail with environment/setup guidance
- playbook failure: fail with task logs and last actionable stderr
- missing output artifact: operation may succeed, but outputs truthfulness must reflect the gap

---

## Testing expectations

Required test areas:
1. `AnsibleEngine` contract tests against `DeploymentEngine`
2. dispatch routing chooses ansible path when `engine_type=ansible`
3. apply path captures logs/status/deployment records correctly
4. destroy only available when declared
5. outputs artifact contract works
6. secret-safe logging behavior
7. inventory/credential materialization behavior

---

## Rejected alternatives

- generic shell engine first
- infer outputs from stdout
- direct arbitrary `ansible-playbook` flags from manifest

Reasons:
- broader safety surface
- brittle and not dependency-safe
- too much control leakage into ungoverned execution shape
