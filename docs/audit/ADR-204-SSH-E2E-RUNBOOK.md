# ADR-204 — Live E2E Runbook (`bnk-bare-metal-full-poc-ssh` on dpu-server-2)

Status of code: **complete & green** (S1–S6 code). Parity gate: 10/10 byte-identical
to the pinned catalog snapshot. Destroy/revert: wired + tested. 3009 unit tests pass.

This runbook is the live validation step. It is **destructive** (~30–45 min: DPU flash,
kubeadm, full BNK layer) and reconfigures **dpu-server-2** (registered host id=1,
`172.28.13.16`).

## Preconditions

- [x] dpu-server-2 registered as a bare-metal host (id=1, `172.28.13.16`) — backend can reach it.
- [x] forge stack running locally (`docker ps` shows `bnk-forge-*`).
- [ ] **This branch deployed to the running stack** — the running backend is `main`; it does
      NOT yet have the SSH modules or the `bnk-bare-metal-full-poc-ssh` blueprint.
- [ ] cne_pull_secret (FAR) + jwt_token configured (System Default or project secret).
- [ ] dpu-server-2 in a flashable/deployable starting state.

## Steps

1. **Deploy this branch to the running stack** (rebuilds backend + workers — replaces `main`):
   ```bash
   cd /Users/b.rais/dev/sp-pm/bnk/bnk-forge-worktrees/adr-204-ssh-bnk-layer
   make deploy-backend          # build-worker is included automatically
   ```

2. **Load the new blueprint** (stack_templates.json is seeded on startup; re-seed templates):
   ```bash
   # via the template seeder/endpoint used in this repo, or restart backend which re-seeds.
   # Verify it loaded:
   docker exec bnk-forge-postgres sh -lc \
     'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
      "SELECT slug FROM stack_templates WHERE slug = '"'"'bnk-bare-metal-full-poc-ssh'"'"';"'
   ```

3. **Create a project** from `bnk-bare-metal-full-poc-ssh`, bind `bare_metal_host_id=1`
   (dpu-server-2), set DPU VLAN/self-IP settings, and ensure FAR + JWT secrets resolve.

4. **Deploy** and watch. Modules 1–17 run as today; **18–25 now run over SSH**:
   - 18 `bare-metal/bnk-prerequisites` — namespaces (f5-operator/utils/bnk-gw + **f5-bnk**) + far-secret
   - 19 `bare-metal/network-setup` — SF NADs `sf-external`/`sf-internal` in `f5-bnk`
   - 20 `bare-metal/cert-manager` — `helm upgrade --install` Jetstack v1.16.1
   - 21 `bare-metal/bnk-cert-issuer` — issuer chain + OTEL certs
   - 22 `bare-metal/bnk-flo` — `helm registry login repo.f5.com` + FLO chart
   - 23 `bare-metal/bnk-cneinstance` — CNEInstance CR (sriov), wait Accepted
   - 24 `bare-metal/bnk-vlans` — F5SPKVlan external/internal, wait Programmed
   - 25 `bare-metal/bnk-gatewayclass` — GatewayClass `controllerName=f5.com/f5-bnk-f5-cne-controller`, wait Accepted

## Validation (parity against REAL applied resources)

On dpu-server-2 (`sudo kubectl`):
```bash
kubectl get ns f5-bnk f5-operator
kubectl -n f5-bnk get nad sf-external sf-internal
kubectl -n f5-bnk get cneinstance bnk-instance -o jsonpath='{.spec.dataPlane.mode}'   # sriov
kubectl get clusterissuer bnk-ca-cluster-issuer
kubectl -n f5-bnk get f5-spk-vlans.k8s.f5net.com external internal
kubectl get gatewayclass bnk-gatewayclass -o jsonpath='{.spec.controllerName}'         # f5.com/f5-bnk-f5-cne-controller
kubectl get gatewayclass bnk-gatewayclass -o jsonpath='{.status.conditions[?(@.type=="Accepted")].status}'  # True
```
These must match the catalog-path result for the DPU case — the same invariants the
offline parity tests assert against the pinned snapshot.

## Teardown

Destroy the project (or the BNK-layer modules). Each SSH module's `destroy()` reverses its
apply (helm uninstall / kubectl delete in reverse); `bare-metal/bnk-prerequisites` additionally
strips F5 webhooks/finalizers + deletes F5 CRDs so namespace deletion doesn't hang.

## Notes / sensitive material

- FAR secret + CA key handled per spec: written `0600` to `/tmp`, used, `shred`-ed; never logged.
- The credential heredocs are piped to `helm`/`kubectl` over the (encrypted) SSH channel,
  not echoed to logs.
