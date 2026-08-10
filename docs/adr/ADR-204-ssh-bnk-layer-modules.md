# ADR-204 — SSH Implementations of BNK-Layer Modules (18–25), Ported from bnk-poc-deployer

- **ID:** `ADR-204` (GitHub epic [#204](https://github.com/f5devcentral/bnk-forge/issues/204); sub-issues #205, #210). Renumbered from the original `D-020`, which collided with `D-020-f5-design-system-token-discipline.md`; ADR ids now derive from the tracking issue number to avoid sequential-counter collisions across branches.
- **Status:** Accepted — implemented (S1–S6 code complete & green; live e2e pending, see `docs/audit/ADR-204-SSH-E2E-RUNBOOK.md`)
- **Date proposed:** 2026-05-31
- **Implementation note (2026-05-31):** Parity resolved against a *concrete* pinned catalog
  snapshot (`backend/tests/fixtures/catalog_snapshot/`, SHA `97c722e`), not assumptions. Two
  ADR assumptions were corrected by the real catalog: **(1)** cert-manager is **Jetstack**
  `oci://quay.io/jetstack/charts/cert-manager` v1.16.1 — NOT an F5 chart; **(2)** module 18
  `k8s/bnk-prerequisites` is an OpenTofu module (no pack), so its parity is structural and its
  component versions come from the BnkVersionProfile (transforms), not an on-host helm-pull.
- **Backlog id:** `ssh-bnk-layer-modules`
- **Source memo:** `docs/audit/SSH_MODULE_MIGRATION_FEASIBILITY.md` (2026-05-31) + user direction
- **Depends on:** d019 DPU transforms (`blueprint_context.py`, merged on this branch) — the SSH ports must reproduce their output.
- **Resume trigger:** kubernetes-direct / operator / OpenTofu execution of the BNK layer fails in a constrained bare-metal environment (no routable cluster API from backend, no operator WS, flaky tunnel) — observed on dpu-server-2.

## Context

The `DPU + BNK 2.2 Full PoC (All-in-One)` blueprint (`bnk-bare-metal-full-poc`, 25 modules) runs
modules **1–17 over SSH** (`bare-metal/*`, Python registry, `SSHEngine`) and modules **18–25
through the kubernetes / operator / OpenTofu engines**:

| # | Module | Current engine/type |
|---|---|---|
| 18 | `k8s/bnk-prerequisites` | manifests (pack authored opentofu) |
| 19 | `k8s/network-setup` | manifests (Multus NADs) |
| 20 | `k8s/cert-manager` | helm |
| 21 | `k8s/bnk-cert-issuer` | manifests |
| 22 | `bnk/flo` | helm (F5 Lifecycle Operator) |
| 23 | `bnk/cneinstance` | manifest (CNEInstance CR) |
| 24 | `bnk/bnk-vlans` | manifests (F5SPKVlan CRs) |
| 25 | `bnk/bnk-gatewayclass` | manifest (pack authored opentofu) |

On bare-metal DPU servers in constrained environments (dpu-server-2), the K8s/operator/OpenTofu
paths are fragile: they require the backend to reach the cluster API (kubeconfig, operator
WebSocket, or SSH tunnel + kubeconfig rewrite). These keep breaking. Modules 1–17 do **not** have
this problem — they SSH to the host and run `sudo kubectl` against the host's local admin
kubeconfig (present after `kubeadm-init`, module 9).

**Two facts make an SSH path cheap rather than a rewrite:**

1. **Prior art exists and works.** `~/dev/sp-pm/bnk/bnk-poc-deployer` (the successor to this work)
   implements the entire BNK layer as on-host shell scripts that run `sudo kubectl` / `helm`
   locally on the control-plane node — the *same execution model* as forge's `bare-metal/*` SSH
   modules. Mapping:
   - 18 → `app/platform/00-namespaces.yaml` + `app/scripts/30-create-far-secret.sh`
   - 19 → `app/platform/02-network-attachments.yaml` (already `sf-external`/`sf-internal` in `f5-bnk`)
   - 20 → `infra/host/install-host-k8s.sh` (`helm install cert-manager …`)
   - 21 → `app/platform/01-clusterissuer.yaml` + `app/scripts/40/41-create-*-certificates.sh`
   - 22 → `app/scripts/50-generate-flo-values.sh` + `app/scripts/51-install-flo.sh`
     (`helm registry login repo.f5.com` → `helm install/upgrade flo oci://repo.f5.com/charts/f5-lifecycle-operator --version -f values -n` → verify)
   - 23 → `app/scripts/60-install-bnk-using-flo.sh` (`envsubst < CR | kubectl apply -f -` + verify)
   - 24 → **no direct equivalent** (poc-deployer "VLAN" = host/DPU netplan, not F5SPKVlan CRs) — author fresh
   - 25 → `app/scripts/61-install-gatewayclass.sh` + `app/platform/61/62-*.yaml`
   - revert: `app/scripts/revert/*` → maps to module `destroy()`
2. **kubectl-over-SSH is already proven in forge** — modules 10–14 (`install-cni`, `install-multus`,
   `install-gateway-api`, `install-sriov`, `install-storage`) install K8s resources this way today
   (`backend/modules/bare_metal/install_gateway_api.py`).

## Decision

**Add SSH implementations of the 8 BNK-layer steps, ported from bnk-poc-deployer, that coexist with
the existing catalog modules. Do not remove modules 18–25.**

- New Python `SSHModule` classes under `backend/modules/bare_metal/` with paths
  `bare-metal/bnk-*` (e.g. `bare-metal/bnk-prerequisites`, `bare-metal/bnk-flo`, …), registered in
  `backend/modules/__init__.py`, dispatched by the existing `SSHEngine`.
- A blueprint variant (e.g. `bnk-bare-metal-full-poc-ssh`, or a flag on the existing template) that
  swaps catalog modules 18–25 for their `bare-metal/bnk-*` SSH equivalents. The catalog path stays
  available for cloud/operator clusters.
- Extend the SSH base/engine with two reusable helpers (the only genuinely new mechanism):
  **(a) `helm registry login` + `helm upgrade --install` over SSH** (port `51-install-flo.sh`), and
  **(b) generic readiness + output collection** (`kubectl wait` / `verify_resource_exists` and
  `kubectl get -o jsonpath` mapped to pack `outputs.key_outputs`).
- Variable rendering reuses the d019 transforms (`MODULE_TRANSFORMS`) — they are engine-agnostic.
  poc-deployer's `envsubst` templating maps to forge's `${var}` substitution.

### Parity is a hard acceptance criterion

When the catalog modules work, they produce the **correct** result (incl. d019 DPU transforms:
`f5-bnk` namespace, SF NADs, `tmm_data_plane_mode=sriov`, GatewayClass
`controllerName=f5.com/f5-bnk-f5-cne-controller`, `dpu` taint key). The SSH ports **must produce
equivalent applied resources**. Each module ships a parity check that diffs the SSH-rendered
manifest/helm-values against the catalog path's `build_manifest_payload` / `build_helm_payload`
output for the DPU case. Known reconciliations to resolve, not assume:
- **cert-manager chart source**: poc-deployer uses `jetstack/cert-manager`; forge module 20 is the
  *F5* cert-manager chart. Use the forge/F5 source for parity.
- **bnk-vlans**: confirm whether FLO/CNEInstance materializes F5SPKVlan, or author manifests from
  the existing `_transform_bnk_vlans` values.

## Consequences

- **Operational simplification (the goal):** on the SSH blueprint, the BNK layer needs no backend
  kubeconfig, no operator WS, no tunnel rewrite — all 25 modules flow through one transport (SSH to
  host). Removes the dpu-server-2 failure class.
- **Two implementations of the BNK layer** (catalog + SSH). Mitigated by the parity check and by
  keeping templating/values driven from the same canonical context.
- **New runtime dependency**: `helm` (and `yq`/`envsubst` if used) must be present on the host. The
  bare-metal prereq modules should ensure/verify this.
- **Clean teardown regained**: SSH `destroy()` ports the poc-deployer revert scripts
  (`kubectl delete` / `helm uninstall`) instead of the default hardware no-op.
- **Sensitive material**: FAR secret / CA key written to host `/tmp` must be `0600` + shredded,
  never logged.

## References

- Feasibility memo: `docs/audit/SSH_MODULE_MIGRATION_FEASIBILITY.md`
- Prior art: `~/dev/sp-pm/bnk/bnk-poc-deployer` (`app/platform/`, `app/scripts/`, `infra/host/`)
- Forge SSH model: `backend/services/execution/ssh_engine.py`,
  `backend/modules/bare_metal/install_gateway_api.py`
- Render pipeline: `backend/services/execution/k8s_catalog_payload.py`
- DPU transforms: `backend/services/execution/blueprint_context.py`
- Routing: `backend/services/execution/engine_router.py`
- Related ADRs: D-007 (k8s payload builder), D-010 (engine registry)
