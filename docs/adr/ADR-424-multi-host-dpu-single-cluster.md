# ADR-424 — Multi-Host / Multi-DPU Single Cluster over rshim (tmfifo IPAM + Hub-and-Spoke Routing)

- **ID:** `ADR-424` (GitHub epic [#424](https://github.com/f5devcentral/bnk-forge/issues/424)).
- **Status:** Accepted — Design Locked (Grill complete 2026-07-23).
- **Date proposed:** 2026-07-17
- **Date accepted:** 2026-07-23
- **Depends on:** ADR-204 (SSH BNK-layer modules — PR #420 merged to `staging`).

---

## Context & Problem Statement

Today, a bare-metal BNK deployment targets **one host** (one `BareMetalDeployment` → one host = control plane, its single DPU joins as a worker over tmfifo/rshim). A project often contains **multiple bare-metal hosts**, each carrying one or more DPUs, and requires a **single Kubernetes cluster** spanning all hosts and DPUs.

The rshim/tmfifo join transport (where DPUs join k8s over point-to-point tmfifo interfaces rather than the data VLAN) creates two challenges when scaling to multi-host:
1. **Host-local IPAM collision**: The existing tmfifo allocator computes `/30` addresses host-locally (`192.168.100.0/30`), causing collisions when DPUs on different hosts try to join the same cluster.
2. **Multi-host reachability**: DPUs on remote hosts cannot reach the control plane apiserver or each other without cluster-wide addressing and cross-host routing.

---

## Decision & Design Architecture

### 1. Membership Model (B-select with B-all Default)
- **Host members**: Join the Kubernetes cluster over their routable management IP via standard `kubeadm join`. Schedulable — carry BNK control-plane workloads and host-level workloads (e.g. GPUs).
- **DPU members**: Join as worker nodes over their host's tmfifo link (`--node-ip` = tmfifo IP). Carry BNK data-plane workloads (TMM/CNE).
- **Default behavior**: Every host registered in a project is automatically included in the cluster by default (**B-all**). Operators can deselect specific hosts or DPUs (**B-select**).

### 2. Control Plane Placement
- **Single Control-Plane Host**: One designated host runs `apiserver`, `etcd`, and core k8s control-plane components. All other hosts and all DPUs join as workers. Addressing/routing design allows future HA extension without breaking changes.

### 3. Cluster-Scoped tmfifo IPAM Allocator
- **Unit**: Remains `/30` per host↔DPU link (`host_tmfifo_ip` `.1`, `dpu_tmfifo_ip` `.2`).
- **Cluster Pool**: Allocates unique `/30` subnets from a cluster-level pool CIDR (e.g. `192.168.100.0/22`).
- **Persistence**: Allocation occurs automatically upon cluster membership assignment and is persisted in `Dpu` records for idempotent redeployment.
- *Note*: Multi-DPU-per-host MAC uniqueness is an orthogonal concern handled in ADR-478 (`poc-deployer` MAC assignment).

### 4. Hub-and-Spoke Routing
- **DPU → Apiserver**: Apiserver is advertised on the **CP host's management IP** + cert SANs (mgmt + tmfifo). Every DPU reaches the CP apiserver via its local host's NAT/MASQUERADE.
- **Apiserver → Remote DPU Kubelet**: CP host installs static routes `dpuX_tmfifo/32 via ownerHost_mgmtIP` for remote DPUs, and owner hosts enable `ip_forward`.

### 5. Data Model Shape (Q6)
- **Aggregate**: Reuses `KubernetesCluster` table.
- **Side-Table**: Adds 1:1 `BnkClusterConfig` (`cluster_id`, `tmfifo_pool_cidr`, `join_transport`, `control_plane_host_id`).
- **Entities**:
  - `BareMetalHost`: `kubernetes_cluster_id` (FK), `is_control_plane` (bool).
  - `Dpu`: `kubernetes_cluster_id` (FK), persisted `host_tmfifo_ip` & `dpu_tmfifo_ip`.

### 6. Deployment Orchestration (Q7)
- **Parent DAG Orchestrator**: Celery workflow coordinates execution across hosts:
  1. CP Host Init (`kubeadm init` on CP host over mgmt IP with SANs).
  2. Parallel Worker Host Joins (`kubeadm join` over mgmt IP) & DPU Joins (tmfifo IPAM + rshim join).
  3. BNK Layer Deployment (Modules 18–25 applied via CP apiserver).

### 7. Blueprint & UI UX (Q8)
- **Deploy Modal**: Auto-populates all project hosts (B-all default), provides radio selection for CP host, checkboxes for worker hosts/DPUs, and configures cluster tmfifo CIDR pool.

### 8. Teardown / Destroy Ordering (Q9)
- **Reverse-Dependency DAG**:
  1. Revert BNK layer modules (18–25) via CP apiserver.
  2. Reset DPU worker nodes & clean up tmfifo static routes/NAT on hosts.
  3. Drain and reset Worker Hosts in parallel (`kubeadm reset`).
  4. Drain and reset CP Host (`kubeadm reset`).
  5. Release tmfifo pool allocations and delete `BnkClusterConfig`.

---

## Phasing & Rollout Plan

- **Phase 0 (Design & Spec)**: Grill complete; ADR-424 written; sub-issues filed.
- **Phase 1 (Core Model, Allocator & Member Join — CI-testable)**:
  - Database schema & Alembic migration (`BnkClusterConfig`, `BareMetalHost`, `Dpu` additions).
  - Cluster-scoped tmfifo pool allocator.
  - Apiserver mgmt IP advertisement & SAN additions.
  - Worker host join over mgmt IP.
- **Phase 2 (Hub-and-Spoke Routing & Validation — Bench-gated)**:
  - CP host static route programming & host `ip_forward`.
  - Live validation on 2 physical bare-metal hosts (hard gate before Phase 2 merge).
