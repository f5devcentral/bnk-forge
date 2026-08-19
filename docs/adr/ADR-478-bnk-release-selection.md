# ADR-478 — Per-deploy BNK release selection (unlock SSH bare-metal from 2.2)

- **ID:** `ADR-478` (GitHub epic [#478](https://github.com/f5devcentral/bnk-forge/issues/478)). ADR id derives from the tracking issue number.
- **Status:** Proposed (design accepted 2026-07-20 via grill-with-docs; implementation not started).
- **Date:** 2026-07-20
- **Builds on:** ADR-204 (SSH BNK-layer modules, merged via PR #420).
- **Design context:** `BNK-RELEASE-SELECTION-DESIGN.md` (worktree root, untracked) — full grill glossary + blast-radius analysis.

## Context

The SSH bare-metal blueprint is effectively locked to BNK **2.2** while current BNK is **2.3(.1)** (2.4 imminent). Three causes:

1. **No 2.3 deploy profile.** Deploy reads `BareMetalHost.version_profile` (`BnkVersionProfile`) via `blueprint_context.resolve_project_context`. Only 2.1/2.2 are seeded; 2.2 is `is_default`; no 2.3 row.
2. **Nothing selects a version.** `version_profile_id` is nullable, never auto-assigned, and there is no deploy-time field. NULL → the SSH modules fall back to hardcoded 2.2.
3. **Hardcoded 2.2 fallbacks** in the SSH modules: `_DEFAULT_MANIFEST_VERSION = "2.2.1-3.2226.0-0.0.511"` (`bnk_prerequisites.py:42`), cert-manager `v1.16.1` (`bnk_cert_manager.py`, `blueprint_context.py:134`), `kind: CNEInstance` (`bnk_cneinstance.py`, ignores `bnk_cr_kind`).

There are also **two unlinked version tables**: `BnkVersionProfile` (the deploy matrix) and `bnk_releases` (a detection-only registry that maps an installed FLO version to a GA label; consumed by `ReleaseRegistryService.resolve_ga`/`list_releases`/`sync_from_oci`, `bnk_upgrade_service`, and the `BNKReleaseRegistry` UI). No MCP consumer.

BNK provides **no cross-version interworking guarantee**, so the release must be a **per-deploy** choice, not a per-host default. Many target sites are **disconnected** (no repo.f5.com at deploy).

## Decision

**Introduce a per-deploy BNK release selection backed by a new admin-managed deployable-release catalog, reconciling `BnkVersionProfile` away without touching `bnk_releases`.**

1. **New `bnk_deployable_release` catalog** (the exact deploy matrix: manifest chart string, FLO chart version, cert-manager, doca, k8s, containerd/runc, calico/multus/sriov, storage, `bnk_cr_kind`, `is_default`, `is_active`, `source_type`) with a **nullable FK → `bnk_releases`** for GA-label display only. `bnk_releases` is **NOT modified** — detection/`resolve_ga`/registry UI untouched. `BnkVersionProfile` rows migrate into the catalog and the model is retired; `blueprint_context` repoints. Rationale: a GA *line* (fuzzy, detection) and a deployable *pin* (exact, deploy) are genuinely different entities.

2. **Per-deploy selection.** `create_deployment(deployable_release_id=…)` (deploy dialog + API). Persist on `BareMetalDeployment` as a `deployable_release_id` **FK** *and* freeze the full matrix into `version_profile_snapshot` (reproducibility). Default = catalog `is_default`.

   **Resolution seam (impl decision 2026-07-20, Option A).** The 25 BNK-layer SSH modules resolve versions through `resolve_project_context(host_id)` (`ssh_tasks → build_variables_for_ssh → assemble_variables`) — a path that never sees the orchestrator's `BareMetalDeployment` (the two paths connect only through host+project). So `create_deployment` **stamps the chosen release onto `bare_metal_hosts.version_profile_id`** (repointed FK → `bnk_deployable_release`) *in addition to* the deployment FK+snapshot, and `resolve_project_context` keeps reading `host` — now from the catalog table. `host.version_profile_id` is the **UI pre-fill hint** (pre-fills the picker) that the deploy action overwrites with the operator's actual per-deploy choice as the resolution anchor; the authoritative per-deploy record is the deployment's frozen `version_profile_snapshot`. (Option B — threading `deployment_id`/snapshot through the engine-agnostic module chain — was rejected as too wide a change to a path shared with non-baremetal flows.)

   **Placement amendment (2026-07-21, after live test).** The `create_deployment`/orchestrator path is **disabled/unreachable in the UI**; the operative deploy path is the **blueprint/stack deploy (Stacks tab)** → `resolve_project_context(project_id, host_id)`. So the picker moves to the **stack deploy dialog** (gated to BNK/bare-metal blueprints); on submit it stamps the chosen release onto the same `host.version_profile_id` carrier (per-deploy override of the pre-fill), keeping the Option-A resolution seam. **Per-deploy is confirmed, not per-project:** a project owns *many* clusters (`Project.k8s_clusters`), so a release is a property of the **cluster**, not the project. As no `KubernetesCluster` row exists at bring-up (linked post-Phase-2 at `ssh_tasks.py:320`), the deploy invocation is the correct per-cluster selection point — Decision 2 ("per-deploy, not per-host") stands. **Added in P1:** a `deployable_release_id` FK on `KubernetesCluster`, stamped at the post-Phase-2 link seam, so the cluster durably records the release it was built with (the long-term home for upgrade/parity/lifecycle). Multi-host (ADR-424, parked) records one release once on the cluster row — no per-host divergence. The dead bare-metal-panel picker is removed.

3. **De-hardcode = fail-fast, catalog-driven.** Delete the 2.2 version fallbacks; parameterize `kind`/cert-manager from the release. A missing required version errors loudly (kills the silent-2.2 failure class). CNEInstance CR shape (`spec.dataPlane`?) and FLO 2.21.x Helm values schema are *plumbed* now but *pinned* during live validation.

4. **Disconnected-first sourcing.** Catalog rows carry the **full explicit matrix, seeded at build/install time** (migration) → deploys need **no repo.f5.com pull** (reuses ADR-204's skip-if-provided). An **online refresh** action (connected) pulls+parses repo.f5.com manifests to add/update rows (extends the `sync_from_oci` pattern). Build-time task: pull the 2.3.1 manifest once at dev time and bake resolved versions into the seed.

5. **Default flip.** Seed 2.3 `is_active=True, is_default=False`; flip `is_default`→2.3 only after live validation (D) passes (**done 2026-07-24**, see P2 outcome). Same mechanism for 2.4.

6. **Per-release licensing contract → new `bare-metal/bnk-license` module (found in P2).** BNK **2.3.1 changed how CWC is licensed**. In **2.2**, CWC is licensed entirely through **FLO helm `license.*` values** (no `License` CR exists anywhere in the forge path). In **2.3.1**, CWC (`spk-cwc`) is licensed by a **`License` CR** (`apiVersion: k8s.f5net.com/v1`, Namespaced in `f5-operator`; only `jwt` required — JWKS auto-derives from the JWT type) and **ignores** FLO's helm `license.*` block. Without the CR, CWC logs "waiting for a License CR", TMM stays **STANDBY**, and F5SPKVlans never reach `Programmed` (bnk-vlans times out). Fix = a new **release-gated** module `bare-metal/bnk-license`, wired between `bnk-cneinstance` and `bnk-vlans` (blueprint is now **26 modules**): parses `manifest_version`, and for **`>= 2.3`** applies the License CR (CRD gate `licenses.k8s.f5net.com` + CWC-deployment gate `f5-spk-cwc` → apply → wait `condition=LicenseActive`), while **`< 2.3`** is a **clean no-op** (2.2 has no such CRD; an ungated CRD wait would hang forever). The now-inert 2.2 `license.*` block is left in `bnk_flo.py` (harmless on 2.3.1). This is the concrete resolution of Decision 3's "CNEInstance/FLO shape *pinned* during live validation" for the licensing dimension.

### Phases

- **P1 (mergeable, CI-testable):** catalog model + migration + build-time seed (2.1/2.2/2.3.1) · reconcile `BnkVersionProfile` → catalog + repoint `blueprint_context` · per-deploy selection (FK + snapshot + dialog/API + openapi types) · de-hardcode fail-fast · online refresh-from-repo.f5.com · minimal admin surface (list/activate/set-default) · tests.
- **P2 (bench-gated):** live 2.3.1 end-to-end on dpu-server-2 — modules green + BNK Active; pin CNEInstance shape + FLO 2.21.x values vs the live 2.3 CRD/charts; ADR-204 6-invariant parity re-run; clean destroy; **then flip `is_default`→2.3.**

  **P2 outcome (2026-07-24) — COMPLETE.** Full from-scratch 2.3.1 e2e on dpu-server-2: **26/26 modules deployed, no hand-fix.** Forge's `bnk-license` module created the License CR itself (`Registering`→`Active`, connected mode) → both F5SPKVlans `Programmed=True` → GatewayClass `bnk-gatewayclass` Accepted → TMM active (1/0). Prior-session fixes validated live from scratch: flash MAC-enum (`NET_RSHIM_MAC=00:1a:ca:ff:ff:10`, no `tmfifo_net1 down` workaround), setup-dpu-networking retry-verify, BFB stall/resume. **ADR-204 6-invariant live parity passed** (f5-bnk ns + workloads, SF NADs, TMM SF data-plane, GatewayClass controllerName, `dpu` taint, F5SPKVlan Programmed). **CNEInstance 2.3.1 shape pinned:** `pseudoCNI.enabled` + `networkAttachments:[sf-external,sf-internal]` + TMM env (`TMM_DEFAULT_MTU`, `TMM_IGNORE_GATEWAYS`), **no `spec.dataPlane`** (resolves Decision 3's carve-out). `is_default` **flipped 2.2→2.3.1**. Follow-up found+fixed (Decision 6 tail): `bnk-license`'s first apply can lose a transient 2.3.1 `ResourceQuota` admission race (`f5-single-license-quota`, "status unknown for quota") — extended the shared `_apply_manifests` retry to cover it.

  **P2 closeout dispositions (2026-07-24).**
  - **Static render-parity (SSH-vs-catalog):** the byte-parity gate `tests/unit/test_adr204_ssh_parity.py` (11 assertions, green in pre-push) is **2.2-scoped** — its DPU context pins `bnk_manifest_version=2.2.1` and diffs against the 2.2 catalog snapshot. **N/A for 2.3.1**: 2.3.1 is SSH-only (no catalog-path renderer) and intentionally diverges (networkAttachments vs `spec.dataPlane`, License CR). The 2.3.1 equivalence evidence is the **live 6-invariant applied-resource parity** above, not a static diff.
  - **k8s version pinning — REAL de-hardcode follow-up (does NOT block the flip):** the selected release's `k8s_version` (2.3.1 → `1.30.14`) does **not** reach `install-k8s-prereqs`/`kubeadm-init`. `probe-dpu` emits a `k8s_version` output that auto-wire precedence prefers over the release-derived value; on a fresh host that output is `None`, so the modules fall back to default/host apt state — the bench installed **1.29.15**. Functionally tolerated (26/26, BNK Active), but the release does not actually pin k8s. Fix: release-pinned versions must win over `probe-dpu`'s detected outputs (or `probe-dpu` must not emit a shadowing `None`). Likely affects other versions probe-dpu emits. **Decision (2026-07-24): document + defer** — enforcing the pin has no behavior-neutral form; it changes deploys to an untested k8s version (the bench validated `1.29.15`, and 2.2's seeded `1.30.4` is itself unverified, same provenance as the chart/cr_kind fields fixed below), so real enforcement must ride with a re-validated live deploy or the release-management branch.
  - **Live 2.2 no-op path — VALIDATED (2026-07-24, part 7).** Full from-scratch 2.2 (BNK 2.2 GA) deploy on dpu-server-2 via the UI release picker: **26/26 deployed.** `bnk-license` was a clean no-op (0.0s, emitted `license_active=True`, **no License CRD and no License CR on the cluster**) — 2.2 licensing is carried by the FLO helm chart → both F5SPKVlans `Programmed=True`, CWC + CNEInstance `Available=True`, all 6 live-parity invariants green. This mirrors the 2.3.1 License-CR path, validating the 2.2↔2.3.1 licensing-contract split in both directions. Two never-live-validated 2.2 seed fields surfaced and were fixed: (a) chart versions (cert-manager `v`-prefix, real FLO tag `v2.9.27-0.3.4`, pullable manifest `2.2.1-3.2226.0-0.0.511`); (b) `bnk_cr_kind` `BNKGatewayClass`→`CNEInstance` — FLO 2.2 installs `cneinstances.k8s.f5.com` and no `bnkgatewayclass` CRD exists, so the module renders a uniform `CNEInstance` for every release.
  - **`bnk-license` requires `jwt_token` even on the pre-2.3 no-op path** (`InputSpec(required=True)`), enforced at `validate_inputs()` *before* the version gate. Benign by design: 2.2 already requires the JWT for the FLO helm `license.*` block, so a 2.2 deploy always supplies it. Recorded so the requirement isn't later mistaken for a bug.
  - **Clean destroy:** N/A as a forge operation — the SSH bare-metal modules are imperative host mutations with no meaningful reverse op; teardown is `force-delete` (DB) + manual host reset (the validated workflow). Not a P2 gap.

## Consequences

- One authoritative deploy catalog; the silent-NULL→2.2 failure class is removed (fail-fast).
- `bnk_releases` and its consumers are untouched — detection/upgrade/registry UI keep working.
- `BnkVersionProfile` is retired (data migrated) — one fewer overlapping table.
- Disconnected sites deploy any seeded release with no repo.f5.com dependency for version metadata.
- New runtime concept (deployable-release catalog) needs a minimal admin UI + API + generated types.
- **Out of scope:** air-gapped fetch of the actual charts/images at deploy (local registry mirror — separate concern); cloud/non-SSH blueprint version selection.

## References

- Design context: `BNK-RELEASE-SELECTION-DESIGN.md` (worktree root).
- Investigation: `blueprint_context.py`, `backend/modules/bare_metal/bnk_*.py`, `services/bare_metal/orchestrator.py:350`, `services/release_registry_service.py`, `models/bnk_release.py`, `models/bare_metal.py`.
- Provenance caveat: the 2.3.1 manifest string `2.3.1-3.2598.3-0.0.304` was seen **only** in the separate `dpubnkctl` repo — **verify against repo.f5.com** before seeding. FLO 2.21.13 + k8s 1.30–1.31 are corroborated in forge (`bnk_upgrade_service.py`, `bnk_releases`).
- Related: ADR-204 (SSH BNK-layer).
