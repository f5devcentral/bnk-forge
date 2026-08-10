# DEPLOY-ENGINE-EXT-004 — Manifest-Driven Source Sync and Import

> Promoted from accepted `.agent` planning artifacts after implementation completion.

## Objective

Replace the Terraform-biased Git sync/import flow with a manifest-driven import path for approved external deployment-pack sources, using the accepted `DEPLOY-ENGINE-EXT-003` contract.

## Decision summary

### Adopt

1. **Use `ModuleSource` as the trust boundary** for external deployment-pack import.
2. **Keep `source_type='git'`** for approved Git-backed pack sources in this phase.
3. **Discover packs by `bnkforge.pack.json`**, not `.tf` file heuristics, for the new flow.
4. **Keep legacy Terraform discovery as a transitional compatibility path**, not the future default.
5. **Validate manifests during sync/import**, not deferred until execution time.
6. **Record pack-level import failures in sync results** without failing the entire source sync unless clone/source-level failure occurs.

---

## Why this change is needed

Evidence from the current implementation:

1. Git sync was explicitly Terraform-specific:
   - clone repo
   - find directories containing `.tf` files
   - parse Terraform metadata heuristically
   - import into `ModuleLibrary`

2. User validation was Terraform-only.

3. Source orchestration was already centralized via `module_source_service.py` and `routes/module_sources.py`.

4. Approved source management already existed through `ModuleSource`.

Conclusion:
- do not bolt Ansible/script detection into Terraform heuristics
- keep source management as-is, but change Git sync/import to a manifest-first discovery model

---

## Source trust model

Only **admin-added / allowlisted `ModuleSource` Git sources** are eligible for deployment-pack import in first phase.

Implications:
- no project-owner arbitrary Git URL execution
- no sync from ad hoc blueprint/module references
- all importable packs come from managed source records

---

## Sync mode model

Within `source_type='git'`, support two sync modes during transition:

1. **manifest-driven pack sync** — preferred for new generalized external deployment packs
2. **legacy Terraform discovery sync** — compatibility path for current OpenTofu-oriented sources

Recommendation:
- avoid creating a new `source_type` just for pack sync in this slice
- content-discovery method is separate from source trust

---

## Proposed sync flow for approved Git sources

Primary flow:
1. clone source
2. discover all `bnkforge.pack.json` files
3. validate each manifest
4. normalize manifest into catalog fields
5. create/update `ModuleLibrary` rows
6. mark missing/invalid packs in sync results
7. update `ModuleSource` sync metadata

Discovery rule:
- recurse repository
- ignore `.git` and other known non-content directories
- treat each directory containing `bnkforge.pack.json` as one deployment pack root

Pack identity rule:
- stable upsert identity is `module_source_id + module.path`

---

## Manifest validation behavior

Validation happens during sync/import.

### Source-level failures

Examples:
- clone failure
- auth failure
- bad git ref

Result:
- source sync fails
- `ModuleSource.sync_status = failed`

### Pack-level failures

Examples:
- invalid JSON
- missing required manifest keys
- unsupported engine
- invalid lifecycle contract
- missing engine-specific entrypoints

Result:
- source sync can still succeed overall with errors recorded
- invalid pack is skipped from import/update
- sync results enumerate pack-level errors clearly

Why this split matters:
- one bad pack in a large approved source should not prevent valid packs from syncing

---

## Suggested sync result shape

```json
{
  "modules_found": 12,
  "modules_created": 4,
  "modules_updated": 6,
  "modules_skipped": 2,
  "legacy_terraform_modules_found": 0,
  "pack_manifests_found": 12,
  "errors": [
    {
      "path": "infra/aws/broken-pack",
      "stage": "manifest_validation",
      "message": "deployment_pack.engine is invalid"
    }
  ]
}
```

Rationale:
- simple flat error strings are not rich enough once multiple pack types and validation stages exist

---

## Catalog import normalization rules

For each valid discovered pack, populate:
- `ModuleLibrary.name`
- `ModuleLibrary.path`
- `ModuleLibrary.version`
- `ModuleLibrary.category`
- `ModuleLibrary.provider`
- `ModuleLibrary.description`
- `ModuleLibrary.tags`
- `ModuleLibrary.module_source_id`
- `ModuleLibrary.source_path`
- `ModuleLibrary.source_version`
- `ModuleLibrary.git_source`
- `ModuleLibrary.engine_type`
- `ModuleLibrary.pack_manifest`
- `ModuleLibrary.inputs_metadata`
- `ModuleLibrary.outputs_metadata`
- `ModuleLibrary.dependencies_metadata`

Derived compatibility field:
- `dependencies` ← flatten required dependency module names where appropriate

Git source formatting rule for multi-pack repos:
- continue reflecting repo + path identity, e.g. `https://example/repo.git//infra/aws/vpc-foundation`

---

## Legacy compatibility model

Keep existing Terraform discovery logic temporarily, but make it an explicit compatibility branch.

Behavior:
1. if any `bnkforge.pack.json` files are found:
   - use manifest-driven pack import as primary mode
2. if none are found:
   - optionally fall back to legacy Terraform discovery for existing sources

Guardrail:
- do not try to merge both modes within the same directory blindly
- manifest presence wins for that pack root

---

## Handling stale catalog entries

After a successful manifest-driven sync of a source:
- any previously imported manifest-driven catalog module from that source not present in the current sync set should be marked inactive, not hard deleted immediately

Why:
- safer for backward compatibility and auditability
- avoids unexpected breakage if projects still reference a formerly synced pack

---

## Proposed service changes

### `module_sync_service.py`

Add:
- manifest discovery helper
- manifest parser/validator integration
- pack import/upsert helper
- structured sync-result error reporting
- stale-pack reconciliation for source-linked manifest imports

Keep:
- clone/auth/ref handling
- registry sync path as-is
- legacy Terraform compatibility path for now

### `module_source_service.py`

- no major contract change required initially
- may later expose richer sync result summary transparently

### `user_module_service.py`

Recommendation:
- do not expand the Terraform-specific “user module” flow into generalized pack sync in this slice

Reason:
- stakeholder trust model says approved sources only in first phase

---

## API behavior recommendation

Keep `POST /api/module-sources/{source_id}/sync` endpoint shape, but improve returned result detail.

Return:
- source summary
- modules created/updated/skipped
- manifest count
- structured errors

---

## Validation and test expectations

Required test areas:

1. clone + manifest discovery success
2. pack validation failure does not poison entire source sync
3. legacy fallback compatibility
4. upsert behavior
5. removed-pack truthfulness
6. trust-boundary behavior

---

## Rejected alternatives

- keep `.tf` heuristics and just add ansible/script heuristics too
- new source type for every engine family immediately
- manifest validation only at runtime

Reasons:
- fragile, ambiguous detection rules
- source trust and source content-discovery are different concerns
- invalid packs would pollute catalog and mislead users before execution
