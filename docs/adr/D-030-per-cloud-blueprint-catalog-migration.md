# D-030 — Per-Cloud Blueprint Catalog Migration (EKS → AKS → GKE), off the combined modules repo

- **Status:** Proposed
- **Date:** 2026-06-13
- **Related ADRs:** D-028 (unified blueprint catalog — the consuming *system* this builds on), D-029 (EKS+BNK blueprint e2e reliability — surfaced the gap and is the EKS reference implementation), D-021 (proxy migration), D-019 (dynamic-by-default)
- **Tracking issue:** _to be filed_ (pending sign-off on the D-030 direction)

## Context

Forge has **two parallel catalog systems**, and BNK cloud deploys are mid-transition between them:

1. **Legacy — the combined module library.** `backend/data/stack_templates.json` (`bnk-on-k8s`) references flat module paths (`bnk/flo`, `bnk/cneinstance`, `k8s/cert-manager`, …) resolved from the **combined** repo `bnk-forge-modules` (DB setting `module_library.git_url` = `https://github.com/JLCode-tech/bnk-forge-modules.git`, ref `release/2.2`, seeded in `backend/services/defaults_service.py`). Deploys run through `StackDeploymentService.create_project_modules_from_template`.

2. **Current direction — per-cloud blueprint catalogs.** The actual EKS+BNK deploy is **blueprint-driven**: `BlueprintSource` rows point forge at per-cloud catalog repos (e.g. `bnk-forge-aws-eks-cluster`), whose `blueprints/*/forge-blueprint.json` compose the `eks-cluster-*` modules with their inputs. Forge is a *consumer* — `BlueprintSyncService.sync_git_source()` imports blueprint releases; `ImportedBlueprintService.create_project_from_release()` resolves `${…}` references and creates `ProjectModule` rows. Cloud-agnostic primitives live in **`bnk-forge-catalog-shared`** and are **vendored** into each per-cloud catalog via `scripts/vendor-refresh.sh`.

**The blueprint — not the module — is the deploy unit.** Modules exist only to support a blueprint. D-029 made this concrete: the honest-readiness gates were correctly built as cloud-agnostic shared modules and vendored/wired into `eks-cluster-cneinstall`, but they do not take effect in a deploy until the **blueprint** is updated (a `BlueprintRelease` version bump — releases are structurally immutable — plus correct input wiring). The legacy `bnk-on-k8s` / combined-repo path was a red herring for the live EKS deploy.

The team wants to **standardize on per-cloud blueprint catalogs** — `bnk-forge-aws-eks-cluster` (EKS, exists), and new catalogs for **Azure (AKS)** and **GKE** — all sharing `bnk-forge-catalog-shared` via vendoring, and to **migrate away from the combined `bnk-forge-modules` repo** for these cloud BNK deploys. The combined repo's "one repo, all clouds, flat modules" shape does not isolate per-cloud datapath/networking differences, makes the honest-gate vendoring story awkward, and (per D-029 P0) the legacy stack-template resolution silently skips missing modules.

## Decision

Adopt **per-cloud blueprint catalogs as the single source of truth for cloud BNK deploys**, layered on the D-028 blueprint-catalog system:

1. **One per-cloud catalog repo per cloud** — `bnk-forge-aws-eks-cluster` (EKS, reference), `bnk-forge-azure-*` (AKS), `bnk-forge-gke-*` (GKE). Each owns its cloud-specific modules (cluster create/register, CNI/datapath, node pools) and its `blueprints/*/forge-blueprint.json`.
2. **Shared primitives live once** in `bnk-forge-catalog-shared` (cert-manager, multus, the D-029 `cneinstance-ready-gate` + `license-activation-gate`, …) and are **vendored** into each per-cloud catalog via `vendor-refresh.sh` (`VENDORED.pin` records the source SHA; re-running yields zero drift). New clouds adopt a shared module with a 2-line vendor map + one `module` block — the explicit D-029/leverage rationale.
3. **Forge points at the per-cloud catalogs via `BlueprintSource` rows.** No forge *code* change is required to consume them; the migration is configuration (BlueprintSource registration + the default `blueprint_library` settings) plus catalog content + the integrity guard below.
4. **Catalog-integrity guard moves to the blueprint import path** (re-aimed D-029 P0). `ImportedBlueprintService` / `BlueprintSyncService` must **fail closed** when a blueprint references a module absent from the catalog release it pins — never silently skip. The aggregated fail-closed preflight already built for the legacy stack-template path (`StackDeploymentService.verify_template_modules_present`) is ported to the blueprint path; the legacy guard is retained only as long as `bnk-on-k8s` / the combined repo remains in use, then retired with it.
5. **Version-bump discipline is part of the contract.** `BlueprintRelease` rows are immutable (`backend/models/blueprint_catalog.py`); any content change to a blueprint requires a `forge-blueprint.json` `version` bump or forge will not re-import it. Document this in the catalog contract so honest-gate / datapath changes actually reach deploys.
6. **Deprecate the combined `bnk-forge-modules` repo for cloud BNK deploys** and **move the default catalog off the personal fork** (`JLCode-tech/*` → the org). The generic `bnk-on-k8s` stack template may remain for non-cloud / bring-your-own-cluster installs, but is explicitly out of the cloud-deploy path.

## Slices (program)

- **M1 — EKS reference complete (in flight via D-029).** D-029 P1/P2 gate modules (shared + vendored) + the blueprint wiring (version bump + FLO-consistent `jwt_token`) on `bnk-forge-aws-eks-cluster`. Repoint/confirm forge's EKS `BlueprintSource` and validate an import picks up the new release. *Largely done; needs the catalog PRs merged + a forge re-sync + an e2e validation.*
- **M2 — Catalog integrity at the blueprint import path (re-aimed D-029 P0).** Fail-closed, aggregated missing-module guard in `ImportedBlueprintService`/`BlueprintSyncService`; ensure all modules a blueprint references resolve on the pinned release. *Agent-ready; no AWS.*
- **M3 — Azure (AKS) catalog.** New `bnk-forge-azure-*` catalog: vendor the shared gates (2-line map), author AKS cluster/datapath modules + blueprints, register the `BlueprintSource`. *Real work; AKS account to validate e2e.*
- **M4 — GKE catalog.** Same shape as M3 for GKE. *Real work; GCP account; note #301 GCP name bug is separate.*
- **M5 — Deprecate combined repo + off-fork move.** Move default catalog repos off `JLCode-tech/*` to the org; mark `bnk-forge-modules` / `bnk-on-k8s` as legacy/non-cloud; migrate or document existing projects. *Cross-cutting; ownership/permissions decision needed.*
- **M6 — Catalog contract + migration docs.** Update `CATALOG_REPO_CONTRACT.md` (vendoring, version-bump discipline, per-cloud layout) and write the migration guide.

Order: M1 → M2 are the trustworthy foundation; M3/M4 are independently shippable once the shared-vendor pattern is proven on EKS; M5/M6 close it out.

## Consequences

Per-cloud isolation (datapath/networking/node differences live in the cloud catalog, not a shared flat list); honest-readiness + licensing gates authored once in `bnk-forge-catalog-shared` and adopted per-cloud near-free; cleaner provenance (off the personal fork); deploy success becomes falsifiable per cloud (D-029). Forge stays a thin consumer — the migration is mostly configuration + catalog content, not forge code.

Costs / risks: N catalogs to maintain instead of one; vendor-refresh + `VENDORED.pin` discipline per cloud; **version-bump discipline** (a missed bump silently ships stale blueprints — M2's guard + docs mitigate); `BlueprintSource` reconfiguration and migration of in-flight projects off the legacy path; AKS/GKE need cloud accounts to validate e2e. The combined `bnk-forge-modules` repo cannot be deleted until the legacy `bnk-on-k8s` path is retired or repointed.

## Non-goals

Classic BIG-IP/TMOS estate (D-023); the generic non-cloud k8s install path (may keep `bnk-on-k8s`); the #301 GCP blueprint-name bug (separate); rewriting D-028's catalog *system* (this migrates its *sources*).

## References

- D-028 (unified blueprint catalog system), D-029 (EKS reference + the gap that surfaced this).
- Forge consume path: `backend/services/blueprint_sync_service.py`, `backend/services/imported_blueprint_service.py`, `backend/models/blueprint_catalog.py` (BlueprintRelease immutability), `backend/services/defaults_service.py` (`module_library.*` / `blueprint_library.*` settings).
- Catalogs: `bnk-forge-catalog-shared` (shared primitives + `CATALOG_REPO_CONTRACT.md`), `bnk-forge-aws-eks-cluster` (EKS reference: `blueprints/*/forge-blueprint.json`, `scripts/vendor-refresh.sh`, `VENDORED.pin`). Legacy: `bnk-forge-modules` + `backend/data/stack_templates.json` (`bnk-on-k8s`).
