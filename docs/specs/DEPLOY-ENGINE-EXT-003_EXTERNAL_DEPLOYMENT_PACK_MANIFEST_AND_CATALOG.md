# DEPLOY-ENGINE-EXT-003 — External Deployment-Pack Manifest and Catalog Schema

> Promoted from accepted `.agent` planning artifacts after implementation completion.

## Objective

Define the metadata/schema contract for external Git-backed deployment packs so BNK Forge
can catalog them as modules while preserving engine-aware lifecycle and dependency semantics.

## Decision summary

### Adopt

1. **New manifest file:** `bnkforge.pack.json`
2. **User-facing term remains:** `module`
3. **Execution contract term:** `deployment_pack`
4. **Closed engine enum:** `opentofu | kubernetes | ansible | script` — extended by
   Amendment A (below) with `container` and `ssh`
5. **New catalog fields:**
   - `engine_type`
   - `pack_manifest`
6. **Preserve existing catalog metadata fields for compatibility:**
   - `dependencies_metadata`
   - `inputs_metadata`
   - `outputs_metadata`
7. **Manifest-first for new external packs; legacy support for existing OpenTofu imports**

---

## Why this shape fits the existing repo

This proposal is intentionally anchored to what the current code already does well.

### Evidence

1. **Execution already has an engine-agnostic contract**
   - `engine_interface.py` already defines `ModuleContext`, `OperationResult`, `PlanResult`, and a shared engine lifecycle.

2. **Catalog behavior is already metadata-driven**
   - `ModuleLibrary` persists `dependencies_metadata`, `inputs_metadata`, and `outputs_metadata`.
   - `module_catalog_service.py` populates those from `module.json`.
   - `builtin_module_seeder.py` populates those for built-in Python-backed modules.

3. **Project/stack orchestration already depends on those metadata blobs**
   - `project_module_service.py`, `variable_assembler.py`, and `stack_deployment_service.py` all consume them.

4. **A big-bang metadata replacement would be unnecessarily risky**
   - existing flows already expect those specific metadata structures.

So the best schema move is not to replace the current model, but to add a generalized pack contract while preserving current metadata compatibility.

---

## Canonical manifest

### Filename

`bnkforge.pack.json`

### Required location rule

- Pack manifest lives at the root of each deployment-pack directory.
- A repository may contain multiple packs, each with its own manifest in its pack root.
- Source sync/import discovers these files directly.

### Why a new file instead of extending `module.json`

`module.json` is already parsed with assumptions tied to the old OpenTofu-centric module library shape. It does not clearly represent:

- engine class
- lifecycle capability truthfulness
- runner profile
- entrypoint model for non-OpenTofu packs

Using a new file avoids overloading legacy semantics while the generalized contract stabilizes.

---

## Proposed top-level schema

```json
{
  "schema_version": 1,
  "module": {
    "name": "string",
    "path": "string",
    "version": "string",
    "category": "infra|k8s|bnk|app|other",
    "description": "string",
    "provider": "string|null",
    "supported_platforms": ["aws", "azure", "gcp", "on-prem", "any"],
    "tags": ["string"]
  },
  "deployment_pack": {
    "engine": "opentofu|kubernetes|ansible|script",
    "runner_profile": "string",
    "working_directory": ".",
    "entrypoints": {},
    "lifecycle": {
      "supports_init": true,
      "supports_plan": true,
      "supports_apply": true,
      "supports_destroy": false,
      "supports_refresh": true,
      "supports_drift": false
    }
  },
  "dependencies": {
    "required": [],
    "optional": []
  },
  "inputs": {
    "required": [],
    "optional": []
  },
  "outputs": {
    "key_outputs": []
  },
  "credentials": {
    "required": [],
    "optional": []
  }
}
```

### Required top-level keys

- `schema_version`
- `module`
- `deployment_pack`
- `inputs`
- `outputs`

### Optional top-level keys

- `dependencies`
- `credentials`

---

## Field decisions

### `schema_version`

- Add `schema_version: 1` from day one for forward compatibility.

### `module`

Required:
- `name`
- `path`
- `version`
- `category`
- `description`

Optional:
- `provider`
- `supported_platforms`
- `tags`

Mapping to current catalog:
- `name` → `ModuleLibrary.name`
- `path` → `ModuleLibrary.path`
- `version` → `ModuleLibrary.version`
- `category` → `ModuleLibrary.category`
- `provider` → `ModuleLibrary.provider`
- `description` → `ModuleLibrary.description`
- `tags` → `ModuleLibrary.tags`

### `deployment_pack.engine`

Closed enum:
- `opentofu`
- `kubernetes`
- `ansible`
- `script`

Catalog persistence:
- `ModuleLibrary.engine_type`

### `deployment_pack.runner_profile`

Initial allowed values:
- `opentofu-default`
- `kubernetes-default`
- `ansible-default`
- `script-restricted`

### `deployment_pack.working_directory`

- Required string, defaulting to `.`

### `deployment_pack.lifecycle`

Required fields:
- `supports_init`
- `supports_plan`
- `supports_apply`
- `supports_destroy`
- `supports_refresh`
- `supports_drift`

Rules:
- `supports_apply` must be `true`
- `supports_destroy` may be `false`
- omitted values are invalid; capability truth must be explicit

Persistence strategy:
- persist full manifest in `pack_manifest`
- expose derived API field later for `lifecycle_capabilities`
- do not add six dedicated DB columns in this slice

### `deployment_pack.entrypoints`

Use engine-specific declarative keys, validated by engine type.

#### OpenTofu
Required:
- `module_root`

Optional:
- `state_subpath`

#### Kubernetes
Require at least one of:
- `manifest_path`
- `chart_path`

#### Ansible
Required:
- `playbook`

Optional:
- `inventory_source`
- `destroy_playbook`
- `outputs_file`

#### Script
Required:
- `apply_script`
- `outputs_file`

Optional:
- `destroy_script`

Rule:
- values are relative paths or bounded identifiers inside the pack, not arbitrary runtime commands

### `inputs`

Required per input:
- `name`
- `type`
- `description`
- `source`

Optional per input:
- `default`
- `example`
- `sensitive`
- `from_module`
- `from_output`

Allowed `source` values:
- `user`
- `module`
- `auto`

### `outputs`

Keep current `key_outputs` list shape, but require:
- `name`
- `type`
- `description`

Optional:
- `sensitive`

### `dependencies`

Retain current `required` / `optional` grouping.

Required dependency fields:
- `module`
- `reason` for required dependencies

### `credentials`

Required per credential entry:
- `name`
- `type`
- `description`

Optional:
- `delivery_mode`
- `required_for`

Initial allowed credential types:
- `cloud`
- `ssh_key`
- `kubeconfig`
- `inventory_secret`
- `env_secret`
- `api_token`

Initial allowed delivery modes:
- `env`
- `file`
- `platform_reference`

Rule:
- manifest declares what is needed, not the secret value

---

## Concrete catalog schema changes

Add two new fields to `ModuleLibrary`:

1. `engine_type = Column(String(50), nullable=True, index=True)`
2. `pack_manifest = deferred(Column(JSON))`

Keep existing fields unchanged:
- `variables_schema`
- `dependencies`
- `dependencies_metadata`
- `inputs_metadata`
- `outputs_metadata`
- `workflow_compatibility`
- source linkage fields

Why:
- `engine_type` should be queryable, filterable, and easy to expose
- `pack_manifest` stores the normalized authoritative manifest without forcing all consumers to reassemble it
- existing metadata fields remain so current services continue working without broad refactors

---

## Normalization rules during import

Populate directly:
- `name`
- `path`
- `version`
- `category`
- `provider`
- `description`
- `tags`
- `engine_type`
- `pack_manifest`

Populate compatibility fields from manifest:
- `inputs_metadata` ← `inputs`
- `outputs_metadata` ← `outputs.key_outputs`
- `dependencies_metadata` ← `dependencies`

Derive backward-compatible fields where useful:
- `dependencies` ← flattened required dependency module names

Do not overload `variables_schema` as the primary pack metadata location.

---

## Validation rules

1. Manifest file must parse as valid JSON.
2. `schema_version` must be `1`.
3. `module.name`, `module.path`, `module.version`, `module.category`, `module.description` are required.
4. `deployment_pack.engine` must be in the closed enum.
5. `deployment_pack.runner_profile` must be in the allowed profile set for the declared engine.
6. `deployment_pack.working_directory` is required.
7. `deployment_pack.lifecycle.supports_apply` must be `true`.
8. Engine-specific required entrypoints must be present.
9. No manifest field may include raw secret values.
10. `inputs.required/optional` and `outputs.key_outputs` must use structured entry objects, not plain strings.

---

## Migration plan

### Phase 1 — additive introduction

- add `engine_type` and `pack_manifest` to catalog model
- support `bnkforge.pack.json` for new generalized external packs
- keep current OpenTofu `module.json` and Terraform-oriented sync paths functioning

### Phase 2 — normalized catalog exposure

- for newly imported packs, always fill compatibility metadata fields from manifest
- for existing OpenTofu modules, derive `engine_type = opentofu` where not explicitly present

### Phase 3 — optional convergence

- evaluate whether `module.json` should be translated to the new manifest format automatically or remain legacy-only

Recommendation:
- do not force immediate migration of all current OpenTofu modules

---

## Rejected alternatives

- single giant JSON blob only, no explicit `engine_type` column
- fully replacing existing metadata fields immediately
- arbitrary command templates in manifest

Reasons:
- weak routing/filtering/API use
- too disruptive to current project/stack/module flows
- violates governed-runner and trust-boundary decisions

---

## Amendment A (2026-07-18) — `container` and `ssh` engines; the artifact manifest

The engine enum in this spec was closed at `opentofu | kubernetes | ansible | script`.
Two engines have shipped since and are hereby admitted to the contract:

- **`ssh`** — governed runner profile, same ownership model as `ansible` (no amendment
  to the runner-ownership rules; entrypoints are declarative, the runner is in-product).
- **`container`** — a deliberate **inversion of the governed-runner model** for vendor
  `*ctl` CLI tools. The pack directory pairs `bnkforge.pack.json` with a second file,
  **`bnkforge.artifact.json`**, which *is* the runner contract:
  - `kind: container_image` with a **digest-pinned** image (`registry_host` /
    `repository` / `sha256:` digest; floating tags are rejected) that the **content
    author's own repo builds and publishes** — BNK Forge never builds runner images.
  - argv-only `steps` (`init`/`plan`/`apply`/`destroy`) invoking the artifact's own
    image; shell/command/image-override keys are denylisted.
  - optional runtime blocks merged into the stored `pack_manifest` at sync:
    `state`, `execution`, `credentials`, `secret_files`, `cluster`, `references`,
    `actions` (ADR D-034), `reports`.

  What remains in-product for `container` packs is the **execution substrate and
  admission control**: manifest validation (`services/module_metadata.py:
  validate_artifact_manifest`), digest-pinning and registry-host allowlist
  enforcement, non-root image refusal, runtime sandboxing and resource limits,
  workspace/secrets materialization, and outputs/cluster/reports readback
  (`services/execution/container_engine.py`, `container_runner.py`,
  `kubernetes_runner.py`). The trust boundary therefore moves from "Forge builds
  the runner" to "Forge admits and confines an author-built runner".

Validation rules for the base pack manifest are unchanged; for `container` packs the
`deployment_pack.entrypoints` map is typically empty and the artifact manifest is
authoritative for execution. Versioning follows ADR D-033: a new runner image digest
requires a new artifact/module version; hashed catalog rows are immutable.

References: `docs/How to write CI container runner modules and blueprints for BNK
Forge.md` (authoring guide), ADR D-033 (immutable multi-version catalog), ADR D-034
(module test actions), `docs/RUNNER_MODULE_UPDATE.md` (update runbook). Reference
content: the `bnkctl-index` repo (`tools/ocibnkctl`, `tools/roksbnkctl`).
