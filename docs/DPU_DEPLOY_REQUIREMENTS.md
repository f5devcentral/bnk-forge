# DPU Bare-Metal Deployment — Requirements

> Formalized requirements for bringing bare-metal DPU deployment capabilities into BNK Forge.
>
> Date: 2026-04-13
> Branch: `feat/dpu-bare-metal-deploy`
> Reference: [DPU_DEPLOY_ANALYSIS.md](DPU_DEPLOY_ANALYSIS.md)

---

## R1. Deployment Topologies

### R1.1 Topology as First-Class Concept

Forge MUST model deployment topologies as a first-class concept. When creating a bare-metal
DPU project, the user selects (or Forge auto-detects) the topology, which determines the
deployment phases and connectivity strategy.

### R1.2 Supported Topologies

| ID | Topology | Description |
|----|----------|-------------|
| R1.2a | **regular** | Host has an independent NIC. DPU is a worker node only. Simplest flow. |
| R1.2b | **bf3** | Host's network goes through the BF3 NIC. Mode change via BFB flash. |
| R1.2c | **bf3-ipmi** | Same as bf3, but NIC mode change needed before flash. Uses `mlxconfig` + IPMI power cycle. |
| R1.2d | **bmc** | Same as bf3-ipmi, but mode change done via Redfish BIOS attributes + Redfish power cycle. |

### R1.3 Topology Auto-Detection

Forge MUST be able to auto-detect the appropriate topology during discovery by probing:
- Whether the host has an independent (non-BF3) NIC with connectivity
- The current NIC mode (`INTERNAL_CPU_MODEL` via `mlxconfig`)
- Whether VFs are present (indicating DPU mode is active)
- BMC/IPMI reachability

---

## R2. Discovery as an Explicit Tool

### R2.1 Standalone Discovery

Forge MUST provide discovery as an explicit, user-invocable tool — not just an implicit
deployment prerequisite. Users invoke discovery to validate their environment before
committing to a deployment.

### R2.2 Discovery Capabilities

| ID | Probe | Description |
|----|-------|-------------|
| R2.2a | Host connectivity | SSH reachability, OS version, architecture |
| R2.2b | NIC mode | `INTERNAL_CPU_MODEL` via `mlxconfig` (SuperNIC vs DPU) |
| R2.2c | PF/VF interfaces | Auto-detect PF names, VF presence, link state |
| R2.2d | DPU state | SSH to DPU (rshim or management IP), installed software versions |
| R2.2e | Hugepages | Verify 16GB hugepages configured on DPU (required for TMM) |
| R2.2f | K8s state | Whether K8s is running on host, DPU joined, BNK deployed |
| R2.2g | BMC/IPMI | Redfish reachability, vendor detection, BIOS attributes; IPMI reachability |
| R2.2h | VLAN validation | Probe VLAN path by creating temporary sub-interface and pinging gateway |
| R2.2i | OVS bridge state | On DPU: OVS available, bridge ports, VF representors |
| R2.2j | DPU software versions | DOCA, containerd, runc, K8s versions on DPU vs target BNK profile |
| R2.2k | Topology recommendation | Based on probes, recommend the appropriate topology |

### R2.3 Shared Implementation

Discovery logic MUST be shared between the standalone discovery tool and the deployment
workflow's pre-flight validation. One implementation, two entry points.

### R2.4 Discovery Output

Discovery MUST produce structured output (JSON/API response) with:
- Raw probe results
- Assessment (pass/warn/fail per check)
- Recommended topology
- Recommended next actions
- Version drift details (if DPU exists but has wrong versions)

---

## R3. Deployment Phases

### R3.1 Four-Phase Architecture

Deployment follows four sequential phases. Each phase has pre-flight checks, execution
steps, and completion verification.

| Phase | Name | Scope |
|-------|------|-------|
| 1 | DPU Provisioning | Flash DPU with BFB, install K8s prereqs (containerd, runc, kubeadm) |
| 2 | Host K8s Setup | kubeadm init, Calico CNI, cert-manager, SR-IOV, Multus, storage |
| 3 | DPU Join | kubeadm join, label (`app=f5-tmm`), taint (`dpu=true:NoSchedule`) |
| 4 | App Platform | FLO, BNK CRs, GatewayClass, certificates, observability |

### R3.2 Phase 4 Integration with Existing Forge Modules

Phase 4 SHOULD reuse Forge's existing module system (`backend/modules/`) where possible:
- `modules/bnk/flo.py` — FLO installation
- `modules/bnk/cneinstance.py` — CNEInstance CR
- `modules/bnk/bnk_gatewayclass.py` — GatewayClass
- `modules/k8s/cert_manager.py` — cert-manager

Where the PoC deployer's Phase 4 steps overlap with existing Forge modules, Forge modules
take precedence. New steps (CWC certificates, OTEL certificates, observability stack) are
added as new modules or services.

---

## R4. Connectivity & Safety

### R4.1 Connectivity Preservation

Every deployment step that modifies networking MUST preserve or restore host connectivity.
This is enforced through:

| ID | Mechanism | Requirement |
|----|-----------|-------------|
| R4.1a | Pre-staged netplan | Write VF netplan config before any mode change; activates when VF appears |
| R4.1b | Fallback timer | Systemd timer that reverts netplan if no SSH login within configurable timeout |
| R4.1c | Phase C bootstrap | Systemd one-shot deposited before reboot; configures DPU OVS via rshim on boot |
| R4.1d | Post-flash recovery watcher | Background script that re-runs Phase C after BFB flash drops OVS |
| R4.1e | VLAN validation | Test VLAN path before any destructive network changes |

### R4.2 Idempotency

Every step MUST be idempotent. Running the same step twice MUST produce the same result
as running it once. State is detected by probing (not state files):

- Phase 1: Check DPU SSH + installed packages
- Phase 2: Check if K8s cluster is running (`kubectl get nodes`)
- Phase 3: Check if DPU node exists and is Ready
- Phase 4: Check if Helm releases exist, CRs exist, namespaces exist

### R4.3 Independent Execution

Each phase and each step within a phase MUST be executable independently. The deployment
workflow runs them in sequence, but a user (or retry mechanism) can invoke any step
directly.

### R4.4 Resume Capability

If a deployment fails at step N, the user MUST be able to resume from step N (or any
step) without re-running completed steps. Completion is detected via probes, not persisted
state.

---

## R5. Remote Execution

### R5.1 Workstation-Driven Model

Forge drives deployment from its own host (the "workstation"). It SSHes into target
hosts, deposits scripts/configurations, and executes them remotely.

### R5.2 SSH Execution Service

Forge MUST provide an SSH execution service that:

| ID | Capability |
|----|-----------|
| R5.2a | Connects to remote hosts via SSH (key auth, password auth) |
| R5.2b | Supports jumphost chains (ProxyJump / `-J`) |
| R5.2c | Transfers files via scp/rsync |
| R5.2d | Executes commands with streaming output |
| R5.2e | Handles connection drops gracefully (flash/reboot scenarios) |
| R5.2f | Waits for SSH to become available (post-reboot polling) |
| R5.2g | Masks sensitive data in logs (passwords, JWT tokens) |

### R5.3 IPMI Execution

Forge MUST support IPMI-over-LAN for power management:
- `chassis power status` — read power state
- `chassis power cycle` — cold boot
- `chassis power soft` — graceful shutdown
- `chassis power on` / `chassis power off`

### R5.4 Redfish Client

Forge MUST support Redfish for BMC operations:
- Service discovery
- BIOS attribute read/write (NIC mode)
- Power management (cold boot sequence)
- Vendor detection and vendor-specific handling (Supermicro, Lenovo XClarity, Dell iDRAC, HPE iLO)

---

## R6. Version Profiles

### R6.1 BNK Version Matrix

Forge MUST maintain a registry of BNK version profiles, each pinning coordinated versions
of all components:

- BNK manifest version and CR kind
- FLO Helm chart version
- Kubernetes version
- DOCA version
- containerd, runc versions
- Ecosystem versions (Calico, cert-manager, Gateway API, Multus, SR-IOV)
- Storage class type and provisioner
- Feature flags (IPv6, TMM node labels, etc.)

### R6.2 DPU Software Version Detection

Discovery MUST detect installed DPU software versions and compare against the target BNK
profile. If versions don't match, the deployment plan MUST include a DPU reflash step.

---

## R7. VLAN & Network Configuration

### R7.1 User-Provided Network Config

For bf3/bmc topologies, the user MUST provide:
- Network mode: VLAN or flat
- VLAN ID (VLAN mode)
- Gateway IP on management subnet
- Host management IP (CIDR notation)
- DPU management IP (optional, CIDR notation)

### R7.2 Probe-Based Validation

Forge MUST validate VLAN configuration by probing on the actual host:
- Create temporary VLAN sub-interface on PF
- Assign test IP
- Ping gateway through the VLAN
- Tear down temporary config
- Report pass/fail before any destructive action

---

## R8. Status & Audit

### R8.1 Real-Time Status

Deployment progress MUST be visible in the Forge UI in real-time:
- Current phase and step
- Per-step status (pending/running/completed/failed/skipped)
- Streaming output from remote commands
- Duration per step

### R8.2 Audit Trail

Every deployment action MUST be recorded in Forge's audit system:
- Who initiated the deployment
- Target host details
- Topology used
- Each step executed with outcome
- Timestamps

---

## R9. Testing

### R9.1 Unit Tests

All new services, schemas, and utility code MUST have unit tests:
- SSH session mocking (no real SSH needed)
- Redfish client mocking
- Discovery state serialization
- BFB config generation
- Phase logic (with mocked SSH)

### R9.2 Integration Tests

Integration tests MUST verify service interactions:
- Discovery → deployment plan generation
- Phase sequencing based on topology
- Resume from failed step

### R9.3 E2E Tests

E2E tests MUST be runnable against a real target host:
- Full discovery probe
- VLAN validation
- SSH execution chain (with jumphosts if configured)
- DPU software version detection

Target host details will be provided for E2E test configuration.

---

## R10. Scope Boundaries

### R10.1 In Scope (Phase 1)

- Single host deployment (one x86 server + 1-N DPUs)
- All four topologies (regular, bf3, bf3-ipmi, bmc)
- Discovery as standalone tool and deployment prerequisite
- Backend services, API routes, schemas
- Frontend pages for project creation, discovery, deployment monitoring
- MCP tools for AI-driven deployment

### R10.2 Out of Scope (Future)

- Multi-host cluster orchestration (multiple control plane nodes)
- Automated BFB image management (download, versioning, caching)
- GPU/accelerator setup on DPU nodes
- Custom use-case deployment beyond the BNK platform
- Bare-metal provisioning without DPUs (standard K8s on bare metal)
