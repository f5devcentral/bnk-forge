# D-033 — Multi-version module catalog (immutable module versions; pin-resolution end to end)

- **Status:** Accepted
- **Tracking:** #433 (epic) · implementation PR #436 (combined, supersedes stacked #434/#435) · follow-ups #446 (cross-source disambiguation), #447 (deactivation guard)
- **Date:** 2026-07-17
- **Source:** Operator/owner request — "ctl binaries are built for specific releases … we need to support multiple versions for each ctl tool." Surfaced while updating the roksbnkctl catalog entry from v1.11.4 to v1.20.0, where the CLI surface had drifted enough that the old manifest's steps no longer existed in the new binary.
- **Sibling ADRs:** D-028 (unified blueprint catalog), D-019 (dynamic-by-default). Related issues: #404 (dual-content catalog repos), #428/#430 (blueprint sync fixes that established the canonical-hash + immutability patterns this ADR reuses).
- **Class of problem it governs:** catalog entries whose identity omits their version, so "update" means "mutate in place" — silently changing what pinned consumers (blueprints) and live consumers (deployed projects) run.

## Context

All findings code-verified on `staging` (2026-07-17).

### 1. The module library holds exactly one version per tool, overwritten on sync

`ModuleLibrary.version` is a plain data column with no identity role and no uniqueness (`backend/models/module.py:22`). Pack-module sync upserts by `(module_source_id, source_path)` (`backend/services/module_sync_service.py:997-1007` — "Uses (module_source_id, source_path) as stable identity for upsert behavior") and **updates the row in place**: a re-sync of a catalog repo that bumped a tool's version replaces the manifest the previous version's consumers still reference.

### 2. Blueprint version pins are enforced at validation but ignored at resolution

The blueprint manifest contract **mandates explicit pinned module versions** — `latest`/floating refs are rejected (`backend/schemas/blueprints.py:96-109`, `_validate_pinned_module_version`). But every resolution site matches by **path only**: `imported_blueprint_service.py:202-203, 491-492, 546-547, 577-578` all filter `ModuleLibrary.path == module_ref, ModuleLibrary.is_active` and take `.first()`. The pin the validator insists on is displayed, then ignored. An imported (immutable) blueprint release pinning `tools/roksbnkctl@1.11.4` deploys whatever version the library row currently holds.

### 3. Deployed projects drift silently — including on destroy

`ProjectModule` holds a live FK (`module_library_id`, `backend/models/project.py:144`) with **no manifest snapshot**; engines read `ctx.pack_manifest` resolved from the library row at execution time (`backend/services/execution/container_engine.py:207,426,444…`). When sync mutates the row, every existing project runs the *new* manifest on its next apply — and, most dangerously, on **destroy**. Concrete case from the motivating session: roksbnkctl 1.11.4's steps used `--home/--region/--cluster-name` flags that v1.20.0 removed (replaced by `init --non-interactive` + `ROKSBNKCTL_*` env). A project deployed under the old manifest would attempt teardown with steps the recorded state was never created by.

### 4. The correct model already exists in this codebase

`BlueprintRelease` is immutable and keyed `(blueprint_source_id, blueprint_id, blueprint_version)` (`backend/models/blueprint_catalog.py:96,131` — unique constraint + structural-immutability `before_update` listener). Sync creates new rows per version, no-ops on identical content, and reports conflicts on same-version-different-content (canonical-normalized hashing per #430). Blueprints got versioning right; modules did not.

### 5. External catalog repos (bnkctl-index et al.) inherit the limitation

A catalog repo tracks one manifest per tool directory (`module.path` must equal the directory — `module_sync_service.py:653-660`), so today the ecosystem convention is "one current version per tool"; history exists only in the repo's git log, not in the catalog.

## Decision

**Module identity becomes `(module_source_id, path, version)` and each version row is immutable. Blueprints resolve their pinned version exactly; the existing `ProjectModule` FK becomes a true pin because the row it points at can no longer change.**

Deeper shape, in dependency order:

1. **Model + migration.** Unique constraint `(module_source_id, path, version)`; add `content_sha256` and `is_latest` to `ModuleLibrary`; structural-immutability `before_update` guard mirroring `BlueprintRelease` (lifecycle fields stay mutable: `is_active`, timestamps, health/smoke-test fields). Alembic migration backfills existing rows as the single version of their path. Hashing uses the canonical-normalized representation on both write and compare (the #430 lesson). Precision notes: the identity column is **`path`** (the directory-derived identity `sync` enforces); `source_path` remains provenance-only — pre-`source_path`-era drifted rows are deduped defensively by the migration rather than given a second identity. `version` stays nullable for legacy rows; Postgres treats NULLs as distinct in unique constraints, so NULL-version rows are policed by the sync upsert's `IS NULL` matching, not the constraint. The migration is **forward-only**: collapsing multi-row-per-path back to one row is lossy (non-nullable `ProjectModule` FKs may reference any version row), so downgrade drops only schema, not data.
2. **Sync.** Upsert keyed by version: new version in the manifest → new row (prior rows untouched); same version + same hash → no-op; same version + different hash → conflict recorded in sync results, never an overwrite. `is_latest` recomputed per `(source, path)` in the same transaction as the sync that changed the version set (two fully concurrent syncs of one source could still race to two/zero `is_latest` rows — self-healing on the next sync; a partial unique index `WHERE is_latest` is the escalation if it ever bites). Ordering is semver with sync-time fallback; real versions include shapes like `2.3.0-ehf-2-3.2598.3-0.0.17`, and `PINNED_MODULE_VERSION_PATTERN` (`backend/schemas/blueprints.py`) must accept every concrete shape the catalog can hold — pack versions are free-form, so the pin contract rejects only floating refs (`latest`), not unusual concrete versions. **No catalog-repo layout change**: repos keep one directory per tool tracking the newest release; the catalog accumulates history across syncs.
3. **Resolution.** Blueprint instantiation resolves `(path, version == pinned)`; a missing version is a hard, actionable error (`BLUEPRINT_MODULE_VERSION_MISSING`, listing available versions) — never a silent fallback. Direct (non-blueprint) module deploys default to `is_latest`. Known gap: identity is `(source, path, version)` but blueprint pins carry no source, so two sources cataloging the same `(path, version)` resolve nondeterministically — a disambiguation rule (or at minimum tie-detection warning) is tracked in #446.
4. **Project pinning.** With immutable rows, `ProjectModule.module_library_id` *is* the pin — no snapshot column. Add an explicit "upgrade module version" action (swap FK → re-plan) replacing implicit drift with an operator decision. Deactivating a version still referenced by project modules is blocked (or requires force with a loud warning) — not yet implemented; sync currently inactivates stale versions unconditionally, tracked in #447.
5. **API/UI/MCP.** Catalog UI grows a version picker per module (default latest, "latest" badge); blueprint deploy dialog shows pin + availability; `make openapi-types` regen; MCP `list_modules` exposes versions.
6. **Docs.** Authoring guide gains a versioning section; catalog-repo contract docs updated: per-tool update procedure stays "bump version+digest in place" — the catalog now retains the old version.

### Scope boundaries

- Phases 1–4 target **pack modules** (`bnkforge.pack.json` path). Legacy terraform-directory modules keep current single-version behavior initially. Builtin seeders (`cli_bnkctl_module_seeder`, k8s builtins) must set version identity consistently but remain single-version.
- Suggested slicing: **PR-1** model + migration + sync immutability; **PR-2** resolution + project pinning + APIs (carries the regression-test weight — it closes findings 2 and 3); **PR-3** UI + MCP + docs.

## Consequences

- Imported blueprint releases become truly reproducible: the pin they were validated with is the manifest they deploy.
- For deployments created after the migration, destroy always runs the manifest the deployment was created with — the highest-severity hazard here disappears without adding a snapshot column. Projects that already drifted pre-migration (the motivating roksbnkctl case) are **not** repaired by the backfill; see "Transition for existing data".
- Catalog storage grows by one manifest-sized JSON row per version — negligible.
- "Latest" becomes a computed property; anything currently assuming path-uniqueness of `ModuleLibrary` rows (queries, seeders, stack templates) must be audited in PR-1 — `.first()` on a path filter is the smell to grep for.
- Version pruning becomes a real admin operation (deactivate old versions), gated by project references.

## Transition for existing data

The backfill records each existing row as *the single version of its path* — only the currently-synced version survives into the multi-version world. Two consequences for data that predates the migration:

- **Blueprint releases pinning an overwritten version go from "deploys fine via path-only match" to a hard `BLUEPRINT_MODULE_VERSION_MISSING` error.** This is strictly safer (the path-only match was silently deploying the *wrong* content), but it is a behavior change, and since `BlueprintRelease` is immutable the release cannot be re-imported at the same version. Remediation, in order of preference: (a) register the old catalog-repo tag as an additional source (the interim workaround below), which re-materializes the missing version row and makes the pin resolve exactly; or (b) publish a bumped blueprint version pinning a version the catalog holds.
- **Already-drifted projects stay drifted.** A project whose module row was overwritten before this shipped keeps pointing at the newer content; the backfill cannot reconstruct the manifest the deployment was actually created with. Operators should verify such projects' next plan/destroy manually — post-migration, the problem class cannot recur.

## Interim workaround (zero code)

Sources are keyed by `(url, ref)` and distinct refs already create distinct sources (test-covered: `test_sync_git_source_ref_mismatch_creates_distinct_module_source`). Registering one source per git tag holds an old version in the catalog alongside the new — coarse, but available today.

## References

- `backend/models/module.py:11-40`, `backend/models/project.py:138-163`, `backend/models/blueprint_catalog.py:58-140`
- `backend/services/module_sync_service.py:622-700, 997-1007`
- `backend/services/imported_blueprint_service.py:202, 491, 546, 577`
- `backend/schemas/blueprints.py:96-109`
- Issues #404, #428, #430; index repo https://github.com/mwiget/bnkctl-index
