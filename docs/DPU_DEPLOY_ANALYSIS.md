# DPU Bare-Metal Deployment — Analysis

> Analysis document capturing the investigation into bringing bare-metal DPU deployment
> capabilities from `bnk-poc-deployer` into BNK Forge.
>
> Date: 2026-04-13
> Branch: `feat/dpu-bare-metal-deploy`

---

## 1. Context

BNK Forge currently manages the lifecycle of F5 BNK (Big-IP Next Kubernetes) deployments
on **existing Kubernetes clusters**. It has a rich module system (`backend/modules/`) with
ManifestModule, HelmModule, and mixed-module types, three execution engines (OpenTofu,
Kubernetes direct, Operator WebSocket), stack templates, blueprints, and comprehensive
Day-2 operations (topology visualization, health dashboards, fleet management, diagnostics).

However, Forge assumes a Kubernetes cluster already exists. For DPU-based BNK deployments,
the journey starts much earlier — from bare-metal x86 servers with NVIDIA BlueField DPUs.

A separate project, `bnk-poc-deployer`, was built to automate this bare-metal-to-BNK flow
for PoC environments. The goal of this work is to bring that capability into Forge as a
first-class feature.

---

## 2. bnk-poc-deployer: What It Does

### Overview

`bnk-poc-deployer` is a bare-metal-to-BNK orchestrator. It takes a raw x86 server with
NVIDIA BlueField DPUs and deploys a complete BNK environment end-to-end through four
sequential phases:

| Phase | Name | Duration | What It Does |
|-------|------|----------|-------------|
| 1 | DPU Provisioning | 15-40 min | Flash DPU with BFB, install K8s prereqs on DPU |
| 2 | Host K8s Setup | 5-10 min | kubeadm init, CNI, cert-manager, SR-IOV, storage |
| 3 | DPU Join | 2-5 min | kubeadm join, label, taint DPU node |
| 4 | App Platform | 10-15 min | FLO, BNK CRs, GatewayClass, observability, use cases |

### Two Implementations

The project has two implementations:

1. **Shell scripts** (main branch) — battle-tested on real hardware across all topologies
2. **Python CLI** (`feat/native-python` branch) — complete ground-up rewrite with ~90+ source
   files, 45 test files, typed dataclasses, zero external dependencies (pure stdlib)

The Python implementation is the chosen reference for Forge integration because:
- Typed data models (`BnkConfig`, `DiscoveryState`, etc.) map to Forge's Pydantic schemas
- Clean service abstractions (`SSHSession`, `RedfishClient`, `VendorPlugin`) map to Forge services
- 6-layer config merge (defaults → profile → file → creds → env → CLI) is more sophisticated
- Exception hierarchy (`BnkError` → 5 subclasses) matches Forge's error patterns
- Phase 4 modules have `deploy()` + `revert()` pattern — nearly 1:1 with Forge services
- 45 test files provide a specification of expected behavior
- Zero external dependencies — pure stdlib Python, distributable as zipapp

### Three Deployment Topologies

| Topology | When To Use | Orchestration Model |
|----------|-------------|-------------------|
| **regular** | Host has its own independent NIC (non-BF3) | Runs ON the host |
| **bf3** | Host's only network is through the BF3 (already in DPU mode or SuperNIC→DPU via BFB) | Runs ON the host |
| **bmc** | Same as bf3, but NIC mode change needed before flash via BIOS attributes | Runs from WORKSTATION via BMC |

### Version Profile System

Coordinated component versions live in `versions/<bnk_version>/versions.env`:

- BNK 2.1: FLO v1.198.4, K8s 1.29, local-path storage, BNKGatewayClass CR
- BNK 2.2: FLO v2.9.27, K8s 1.30, NFS storage, CNEInstance CR

Each profile pins: BNK manifest version, FLO version, K8s version, DOCA version,
containerd/runc versions, ecosystem tool versions (Calico, cert-manager, Gateway API,
Multus, SR-IOV), storage class type, and feature flags.

### Hardware Discovery (`discover.sh` / `discovery/` package)

Read-only probe from workstation:
- SSH into host → detect NIC mode, PF interfaces, VFs, OVS bridges, hugepages, K8s state
- Redfish to BMC → discover vendor, BIOS attributes, NIC mode settings, power state
- Auto-detect topology from hardware state
- Output structured JSON assessment with recommendations

---

## 3. Technical Deep Dive: NIC Mode Change & Connectivity

### BlueField NIC Modes

| Mode | `INTERNAL_CPU_MODEL` | Host PF Behavior | DPU ARM Cores | VFs |
|------|---------------------|------------------|---------------|-----|
| **SuperNIC** | 0 | Direct NIC (PF → switch) | Minimal/dormant | None |
| **DPU** | 1 | eSwitch port | Full OS, OVS bridge | Present |

The mode is stored in NIC NVM and requires a power cycle to take effect.

### How Each Topology Handles the Mode Transition

#### BF3 Topology (SuperNIC → DPU via BFB flash)

1. Host is reachable via PF (direct NIC in SuperNIC mode)
2. `prepare-host-bf3.sh` pre-stages VF netplan config + installs fallback timer
3. `bfb-install` flashes DPU via rshim (PCIe tunnel) — **host keeps connectivity** (PF still works)
4. BFB `bfb_post_install` runs `mlxconfig ... INTERNAL_CPU_MODEL=1` on DPU
5. DPU reboots → firmware reads new mode → eSwitch enters switchdev → VFs appear on host
6. Pre-staged netplan activates on VF → connectivity restores via VF→OVS→physical port
7. Fallback timer auto-reverts if connectivity not restored within 600s

**Key insight**: No host reboot needed. The DPU's own reboot at end of BFB install commits
the mode change (ARM cores and NIC firmware share the same power domain on BF3 SoC).

#### BMC Topology (SuperNIC → DPU via BIOS + cold boot)

1. Workstation connects to BMC via Redfish + host via SSH
2. Deposit Phase C bootstrap service on host (systemd one-shot)
3. Set NIC mode via Redfish BIOS attribute PATCH
4. Cold boot via Redfish (GracefulShutdown → Off → On)
5. Host reboots → NIC now in DPU mode → Phase C runs:
   - Wait for rshim device
   - SSH to DPU via rshim (tmfifo_net0 at 192.168.100.2)
   - Configure OVS bridge on DPU (add VF representor)
   - Wait for VF on host, assign management IP
   - Write persistent netplan
6. Connectivity restores → workstation SSHes back in
7. BFB flash with post-flash recovery watcher (handles OVS drop during flash)

#### mlxconfig + IPMI Topology (SuperNIC → DPU without Redfish)

When the BMC is reachable via IPMI LAN but Redfish is unavailable:
1. SSH to host → run `mlxconfig` to set `INTERNAL_CPU_MODEL=1`
2. Deposit Phase C bootstrap service (same as BMC flow)
3. `ipmitool chassis power cycle` from workstation (UDP port 623 to BMC)
4. Same recovery as BMC flow (Phase C → connectivity restore)

This avoids Redfish entirely for environments where IPMI works but Redfish doesn't.

#### Reflash in DPU Mode (wrong software versions)

When NIC is already in DPU mode but DPU software versions don't match the target BNK profile:
1. Host connectivity goes through DPU OVS (VF → pf0vf0 → sf_external → p0)
2. Deposit post-flash recovery watcher on host (`nohup`)
3. `bfb-install` via rshim → DPU ARM cores reset → **OVS goes down → connectivity lost**
4. Flash continues on host via rshim (PCIe, doesn't need network)
5. DPU reboots → `mlnx-ovs.conf` rebuilds bridges → watcher re-runs Phase C
6. Connectivity restores

### Connectivity Preservation Mechanisms

| Mechanism | Purpose | Used By |
|-----------|---------|---------|
| **Pre-staged netplan** | Activates on VF appearance after mode change | BF3, BMC |
| **Fallback systemd timer** | Auto-reverts netplan if no SSH login within timeout | BF3 |
| **Phase C bootstrap service** | Configures DPU OVS bridge via rshim after cold boot | BMC, IPMI |
| **Post-flash recovery watcher** | Re-runs Phase C after BFB flash drops OVS | BMC (reflash) |
| **Background `bfb-install`** | Survives SSH disconnect during flash | BMC |
| **VLAN validation** | Tests VLAN path before any destructive action | BF3, BMC |

---

## 4. IPMI vs Redfish Assessment

### Operations Required

| Operation | IPMI | Redfish | Verdict |
|-----------|------|---------|---------|
| Read power state | ✅ `chassis power status` | ✅ `GET {system}` | Both work |
| Power cycle (cold boot) | ✅ `chassis power cycle` | ✅ `POST Reset` | Both work |
| Graceful shutdown | ✅ `chassis power soft` | ✅ `GracefulShutdown` | Both work |
| Read BIOS attributes | ❌ Not in IPMI spec | ✅ `GET {system}/Bios` | Redfish only |
| Set BIOS attributes | ❌ Not in IPMI spec | ✅ `PATCH {bios}/Settings` | Redfish only |
| Hardware inventory | ⚠️ FRU data only | ✅ Full structured data | Redfish richer |
| NIC/adapter enumeration | ❌ | ✅ NetworkAdapters | Redfish only |

### Conclusion

IPMI **cannot replace** Redfish for BIOS attribute management (the NIC mode change via
BIOS settings). However, IPMI **can replace** Redfish for power management, and when
combined with `mlxconfig` (which sets the NIC mode at the firmware level, not BIOS level),
it creates a viable alternative path for environments where Redfish is unavailable.

### Recommended Tier Model

| Tier | Access | Mode Change | Power Cycle | Complexity |
|------|--------|-------------|-------------|------------|
| **1: OOB** | SSH via independent NIC | `mlxconfig` on host | `ipmitool` or `reboot` | Simplest |
| **2: BF3** | SSH via BF3 PF (SuperNIC) | BFB `bfb_post_install` | DPU auto-reboot | Medium |
| **2.5: mlxconfig+IPMI** | SSH via BF3 + IPMI LAN | `mlxconfig` via SSH | `ipmitool` from workstation | Medium |
| **3: Full Redfish** | SSH + Redfish | Redfish BIOS PATCH | Redfish cold boot | Most complex |

---

## 5. Gap Analysis: bnk-poc-deployer vs BNK Forge

| Capability | bnk-poc-deployer | BNK Forge | Integration Need |
|-----------|-----------------|-----------|-----------------|
| DPU provisioning (BFB flash) | ✅ Full | ❌ None | New services |
| BMC/Redfish operations | ✅ Multi-vendor | ❌ None | New services |
| IPMI power management | ❌ Not implemented | ❌ None | New service |
| Hardware discovery | ✅ Full | ❌ None | Extend scanner |
| Host K8s bootstrap | ✅ Full | ❌ None | New services |
| DPU cluster join | ✅ Full | ❌ None | New services |
| Topology detection | ✅ Auto-detect | ❌ None | New capability |
| VLAN validation | ✅ Probe-based | ❌ None | New capability |
| DPU software version probing | ⚠️ Partial | ❌ None | New capability |
| Jumphost support | ✅ Transparent (subprocess ssh) | ❌ None | New capability |
| BNK deployment (FLO, CRs) | ✅ Shell/Python | ✅ Python modules | Reuse Forge modules |
| Version profiles | ✅ env files | ⚠️ Hardcoded | Extend to profiles |
| Day-2 operations | ❌ None | ✅ Full | Existing Forge |
| Multi-cluster fleet | ❌ Single host | ✅ Full | Existing Forge |
| UI/API/MCP | ❌ CLI only | ✅ Full | Extend for DPU |
| Idempotent steps | ✅ Probe-based | ✅ Module-based | Align patterns |
| Connectivity preservation | ✅ Multi-layer | ❌ N/A | Port patterns |
| Resume capability | ✅ `--skip-to` / `--from` | ⚠️ Task-level | Extend workflow |

### Key Python Classes to Port/Adapt

| bnk-poc-deployer Class | Forge Target | Notes |
|----------------------|-------------|-------|
| `SSHSession` | `services/ssh/session.py` | Subprocess-based, inherits ssh-agent/ProxyJump |
| `RedfishClient` + `VendorPlugin` | `services/redfish/` | Strategy pattern, multi-vendor |
| `DiscoveryState` | Extend `services/scanner/` | Typed probe results |
| `BnkConfig` (6-layer) | Pydantic schemas + DB | Config becomes persistent |
| `BfbConfigGenerator` | `services/dpu/bfb_config.py` | BFB configuration rendering |
| `DeployStep` registry | Task/workflow model | Resumable step sequences |
| Phase 4 modules | Existing Forge modules | Near 1:1 mapping |

---

## 6. Abstraction Mapping: Python CLI → Forge Services

### Execution Layer

The Python CLI uses `SSHSession` (subprocess `ssh`) for all remote execution. In Forge,
this becomes a service that:
- Manages SSH connections with jumphost chains (`-J` / ProxyJump)
- Deposits scripts/configs on remote hosts via scp/rsync
- Executes commands with streaming output (for real-time status)
- Handles connection drops and reconnection (for flash/reboot scenarios)

### Discovery Layer

The Python CLI's `discovery/` package maps to an extension of Forge's cluster scanner:
- `host_probe.py` → bare-metal host probes (NIC mode, PF/VF, hugepages, K8s state)
- `bmc_probe.py` → BMC/Redfish probes (vendor, BIOS, power state)
- `cluster_probe.py` → already covered by Forge's scanner
- `state.py` → `DiscoveryState` becomes a Pydantic model in Forge schemas

Discovery is both a **standalone tool** (user invokes before deployment to validate
their environment) and a **deployment prerequisite** (Forge runs discovery automatically
as part of the deployment workflow).

### Deployment Orchestration

The Python CLI's command dispatch (`deploy_regular.py`, `deploy_bf3.py`, `deploy_bmc.py`)
becomes a Forge workflow service that:
- Selects the right phase sequence based on topology
- Manages step state (pending/running/completed/failed)
- Supports resume from any step
- Streams status updates to the Forge UI
- Handles the connectivity-preservation patterns as first-class concerns

### Version Profiles

The `versions/<ver>/versions.env` files become a Forge database-backed model:
- BNK version profiles with all coordinated component versions
- Queryable via API
- Used by deployment workflows to pin exact versions
- Extensible as new BNK versions are released
