# D-028 — Unified Blueprint Catalog (one tab governs all blueprints; modules behind Advanced; retire External Charts)

- **Status:** Proposed
- **Date:** 2026-06-05
- **Source:** Operator/owner feedback — "the Blueprint tab should manage all the blueprints including the internal ones in code (whether a user sees them or not). By default do we need to see all the modules? It just becomes busy. Helm repos and external charts seem to be doing the same thing — or one works and one doesn't."
- **Sibling principle ADRs:** D-012 (blueprint-category-resolver), D-013 (blueprint-catalog-filter-model), D-014 (deploy-dialog-orchestrator), D-019 (dynamic-by-default)
- **Class of problem it governs:** two parallel systems modelling the same concept, where one is unmanaged and invisible to its own admin surface; plus catalog-surface clutter and a half-built orphan feature.

## Context

The Catalog page (`frontend-v2/src/pages/Catalog.tsx`) has six tabs: **Modules, Blueprints, Helm Repos, DOCA Releases, bf.conf Templates, External Charts**. Three findings, all code-verified this session, drive this ADR.

### 1. There are two parallel blueprint systems; the Blueprint tab manages only one of them

| | In-code "stack templates" | Git blueprint catalog |
|---|---|---|
| Storage | `backend/data/stack_templates.json` — **12 hardcoded** (AWS EKS, BNK 2.2 Platform, DPU PoC, GKE/AKS/OCP, …) | `BlueprintRelease` rows synced from git (`models/blueprint_catalog.py`) |
| Source of truth | A bespoke JSON shape (`slug`/`modules[].path`/`variables`) | `BlueprintManifest` (`schemas/blueprints.py` — `forge-blueprint.json`, strict) |
| API | `GET /api/stacks/templates`, `GET /api/stacks/templates/{slug}` | `GET /api/blueprint-catalog/releases` |
| Deploy path | `stack_service.create_instance` (by slug) | `create_project_from_imported_blueprint_release` (by release id, `imported-blueprint/{id}/` module prefix) |
| Managed in Catalog? | **No — invisible and unmanaged** | Yes (sources, sync, import/unimport, lifecycle) |
| Visibility control | **None — always shown** on `/stacks` | `release_state=imported` + `validation_state=valid` + `is_active=true` |

The user-facing **Blueprints page is `/stacks`** (`pages/Stacks.tsx`, nav label "Blueprints"). It **merges** both lists: built-in templates from `/api/stacks/templates` **plus** imported releases from `/api/blueprint-catalog/releases?release_state=imported&validation_state=valid&is_active=true` (`Stacks.tsx:54-88`). So the Blueprint tab in the Catalog governs only *half* of what an end-user sees, and the more important half — the 12 production templates we actually ship — is unmanaged, has no visibility toggle, and is editable only by hand-editing a JSON file in the image.

The `"builtin"` source type already exists in the catalog API surface (`services/blueprint_catalog_service.py`) but is **unimplemented** — there is no `sync_builtin_source()`, no bundled manifests, and `blueprint_library.git_url` defaults to `""` (`services/defaults_service.py:51`). So today a fresh install has **zero** catalog blueprints and only the 12 invisible-to-admin in-code ones.

### 2. The Modules tab is clutter for the common case

`pages/Modules.tsx` is a read-only library, paginated at 25 with a "show more" (`INITIAL_MODULES_LIMIT = 25`), expected to hold 100s of modules. Blueprints are the intended primary consumption surface; raw modules are power-user territory. There is already a proven `showAdvanced` toggle pattern in the codebase (`components/k8s/RecoveryPanel.tsx:127`).

### 3. Helm Repos vs External Charts — External Charts is a half-built orphan

- **Helm Repos** (`/api/helm/repositories`, `services/helm_repository_service.py`) is a thin `helm repo add/update/remove` CLI wrapper — functional, ephemeral per worker, the real need.
- **External Charts** (`/api/external-charts`, `models/external_chart.py`, `components/settings/ExternalCharts.tsx`) was meant to be a curated registry: `chart_key → repo_url/chart_name/chart_version`. But **the deploy flow never resolves `chart_key`** — `services/execution/k8s_catalog_payload.py` builds the helm payload from the module's own `module.json` `entrypoints.chart_ref`/`helm_repos`. The table's `repo_url`/`chart_name` are **never read at deploy time**. Its only live reads are cosmetic: a version-label dropdown (`stack_service.py:400-422,485-506`) and a single cert-manager version fallback (`execution/blueprint_context.py:129-139`). It is dead surface that invites admins to configure something with no effect.

## Decision

**The blueprint catalog is the single model and the Blueprint tab is the single control surface for every blueprint, in-code or git-sourced. Modules move behind an Advanced toggle. External Charts is removed.**

1. **One model.** The 12 in-code stack templates become **builtin blueprint releases** under a system-seeded `builtin` source. `stack_templates.json` is converted to `forge-blueprint.json` manifests bundled in the image and loaded by a real `sync_builtin_source()`. End state: `BlueprintRelease` is the only blueprint representation.
2. **One control surface.** The Blueprint tab lists **all** blueprints (builtin + git), and a per-release **"Visible on Blueprints page"** toggle decides whether each reaches `/stacks` — including builtins. Admins can ship a blueprint in-image yet hide it from users, or hide a noisy git one, without editing files.
3. **One deploy path.** `/stacks` deploys everything through the imported-release path; the legacy `stack_service` template deploy path and `/api/stacks/templates` are retired (or reduced to a back-compat shim that derives from builtin releases).
4. **Modules behind Advanced.** The Modules tab is hidden by default behind an "Advanced" tickbox on the Catalog page; the preference persists. Blueprints are the default surface.
5. **External Charts removed.** Tab, component, routes, model and table are deleted. Its two real consumers (version-label dropdown, cert-manager version fallback) are relocated to a durable home (`BnkVersionProfile` / config defaults) before deletion — no behavior regression.

### Visibility model

The catalog already encodes user-visibility as `release_state=imported ∧ validation_state=valid ∧ is_active=true` (the exact `/stacks` filter). We reuse it rather than invent a flag:

| Admin intent | Mechanism |
|---|---|
| Ship a builtin, visible to users | seed as `imported` + `valid` + `is_active=true` |
| Ship a builtin, hidden from users | same, but `is_active=false` (the "Visible on Blueprints page" toggle off) |
| Promote/demote a git blueprint | existing import/unimport |

The Blueprint tab exposes this as a plain **"Visible on Blueprints page"** switch per release so the operator never has to reason about the three underlying fields. `is_featured` (today a stack-template-only flag, `Stacks.tsx`) is carried onto the release model so the featured ordering survives the merge.

## The schema gap (the real work, and the main risk)

The stack-template shape and `BlueprintManifest` differ in three ways that the migration must resolve. These are the load-bearing design decisions — flagged here, recommendation given, open for the owner.

| Concern | stack_templates.json | BlueprintManifest (strict) | Proposed bridge |
|---|---|---|---|
| Module reference | `modules[].path` (e.g. `infra/aws/vpc`), **no version** | `modules[].module` + **mandatory pinned `version`** (`v1.2.3`; `latest`/floating rejected, `schemas/blueprints.py:80-93`) | At authoring time, pin each builtin module to its current module-library version. Builtin manifests are generated, not hand-written, so pinning is mechanical. **Open Q1.** |
| Optional modules | `modules[].required: false` (e.g. storage, high-perf nodes) | no per-module required/optional concept | Extend the manifest with an explicit `modules[].optional: bool` (default false) + deploy-dialog module toggles. **Open Q2 — schema extension.** |
| Inputs vs variables | free-form `variables` dict per module + `prerequisites: string[]` | `inputs` (typed `required`/`optional`) + `prerequisites: object[]` | Map `variables → modules[].inputs`; wrap prerequisite strings as `{type:"note", description}`. Mechanical. |

**Recommended approach:** keep `BlueprintManifest` strict (it is a *governed* artifact contract by design, D-019) and treat the 12 builtins as first-class governed manifests — generate them once via a one-shot converter, pin versions, add the small `optional` extension. The alternative (a relaxed schema variant for builtins) reintroduces two shapes and is rejected.

## Phased delivery (tracer-bullet vertical slices)

Each phase is independently shippable to `staging`. P0 and P5 are independent of the blueprint spine and can land first as quick wins.

- **P0 — Modules behind Advanced (quick win).** Catalog page gains an "Advanced" tickbox (persisted to localStorage / user prefs); Modules tab hidden unless ticked. No backend change. *Files:* `Catalog.tsx`. ~1 builder pass.
- **P1 — Builtin source plumbing.** Implement `sync_builtin_source()` loading bundled `backend/data/blueprints/<id>/forge-blueprint.json` into `BlueprintRelease` rows under a seeded `builtin` source; seed at startup (`startup_steps.py`). Validates the strict-manifest path end-to-end with **one** converted builtin. *Risk-reducer: proves the gap-bridge before converting all 12.*
- **P2 — Convert the 12 + manifest `optional` extension.** One-shot converter `stack_templates.json → 12 manifests`; add `modules[].optional` to `BlueprintManifest` + validator; bundle the manifests. Resolves Open Q1/Q2.
- **P3 — Visibility control in the Blueprint tab.** Surface builtins in `BlueprintCatalogPanel`; add the "Visible on Blueprints page" switch (maps to import/unimport + `is_active`); carry `is_featured` onto the release model (alembic). `/stacks` now shows builtins via the existing release filter.
- **P4 — Deploy-path unification + retire stack-template path.** Route builtin deploys through `create_project_from_imported_blueprint_release` with parity (optional-module selection, prerequisites, `requires_existing_cluster`). Reduce `/api/stacks/templates` to a shim over builtin releases (or remove + redirect `/stacks` to release-only). Remove `stack_templates.json` and the divergent `StackDetailDialog` path once parity is verified live.
- **P5 — Remove External Charts (quick win, independent).** Relocate the version-label dropdown source + cert-manager version fallback to `BnkVersionProfile`/config defaults; then delete tab, `ExternalCharts.tsx`, `routes/external_charts.py`, `models/external_chart.py`, and drop the table (alembic). Verify deploys + version dropdowns unchanged.

Final tab set after this ADR: **Blueprints** (all blueprints + visibility), **Helm Repos**, **DOCA Releases**, **bf.conf Templates**, and **Modules** (revealed by Advanced). External Charts gone.

## Consequences

- **Positive:** one mental model and one admin surface for blueprints; in-image blueprints become governable (visibility, lifecycle) without editing files; a fresh install can ship visible blueprints out of the box; less catalog clutter; one dead feature removed; one deploy path to maintain.
- **Negative / cost:** P1–P4 is a real migration touching the strict manifest schema, a DB migration (`is_featured`, drop `external_charts`), and the `/stacks` deploy path — the highest-risk part is deploy-path parity (P4). Mitigated by converting one builtin first (P1) and keeping each slice shippable.
- **Back-compat:** `/api/stacks/templates` consumers (the `/stacks` page) are migrated in-repo; any external/MCP consumer of that endpoint must be checked before P4 removes it.

## Open questions (owner decisions before P2/P4)

- **Q1 — version pinning for builtin modules:** pin each builtin module to its current module-library version at conversion time (recommended), or relax the manifest validator for the builtin source? (Recommend: pin.)
- **Q2 — optional modules:** extend `BlueprintManifest` with `modules[].optional` (recommended), or drop per-module optionality and model storage/high-perf-nodes as separate blueprints? (Recommend: extend.)
- **Q3 — `/api/stacks/templates` removal:** hard-remove + redirect, or keep a thin back-compat shim deriving from builtin releases? (Recommend: shim for one release, then remove.)

## Deferred

**Full `ImportedBlueprintDeployDialog` parity for the deploy path** is deferred as a follow-up (not part of P4). Builtin blueprints currently deploy via the legacy template path by slug — when a user deploys a builtin-sourced release, `Stacks.tsx` opens `StackDetailDialog` using `release.blueprint_id` as the slug, which resolves to the matching `stack_templates.json` entry. This entry still exists in the image and backs the deploy.

Full retire of the template deploy path requires giving `ImportedBlueprintDeployDialog` feature parity with `StackDetailDialog`:
- Optional-module selection (toggle modules whose `optional: true` in the manifest)
- Per-module variable inputs (with types, validation, sensitive masking)
- Prerequisites check (secrets, credential templates)
- Full-cloud region picker (AWS/Azure/GCP/IBM region query)
- Bare-metal host selection (DPU/bare-metal category blueprints)
- Deploy-mode selection (apply vs plan-only)

Until that parity work lands, `stack_templates.json`, `StackDetailDialog`, and `/api/stacks/templates` remain in the image and are **not removed**. MCP and Dashboard consumers of `/api/stacks/templates` are unaffected.
