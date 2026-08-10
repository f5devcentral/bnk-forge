# DPU Bare-Metal Deployment — Architecture Brief

> Input document for the architect agent. Contains the problem statement, constraints,
> reference architecture, and questions to resolve during decomposition.
>
> Date: 2026-04-13
> Branch: `feat/dpu-bare-metal-deploy`
> References:
> - [DPU_DEPLOY_ANALYSIS.md](DPU_DEPLOY_ANALYSIS.md)
> - [DPU_DEPLOY_REQUIREMENTS.md](DPU_DEPLOY_REQUIREMENTS.md)

---

## 1. Problem Statement

BNK Forge needs to manage the full lifecycle of BNK deployments on DPU-equipped bare-metal
servers — from an empty x86 host with NVIDIA BlueField DPUs through to a fully operational
BNK environment with Day-2 management. Currently Forge assumes an existing Kubernetes
cluster. This work extends Forge downward to cover:

1. Hardware discovery and readiness assessment
2. DPU provisioning (BFB flash, K8s prerequisite installation)
3. Host Kubernetes cluster bootstrap
4. DPU node join to cluster
5. BNK platform deployment (reusing existing Forge modules where possible)

The reference implementation lives in `../bnk-poc-deployer` (branch `feat/native-python`).

---

## 2. Constraints

### 2.1 Execution Model

- Forge runs on a workstation/server (Docker Compose). It SSHes into target hosts.
- Target hosts may be behind jumphosts (SSH ProxyJump chains).
- All remote execution happens via SSH (subprocess-based, inheriting ssh-agent).
- Long-running operations (BFB flash: 15-40min) must survive SSH drops.
- Real-time streaming of remote command output to the Forge UI.

### 2.2 Connectivity Preservation

This is the hardest constraint. When the host's only network path goes through the BF3:
- Flashing the DPU drops the OVS bridge → SSH disconnects
- NIC mode changes require cold boot → SSH disconnects
- Every step that risks connectivity MUST have a recovery mechanism
- Mechanisms: pre-staged netplan, fallback timers, Phase C bootstrap, post-flash watchers

### 2.3 Idempotency & Resumability

- Every step is idempotent (probe-based state detection, not state files)
- Every step can execute independently
- Deployment can resume from any step after failure
- Full deployment runs 0→100 in one sequence when everything succeeds

### 2.4 Topology Awareness

Four connectivity tiers determine the deployment flow:

| Tier | Mode Change Method | Power Cycle Method | SSH Path |
|------|-------------------|-------------------|----------|
| regular | N/A (already DPU or irrelevant) | N/A | Independent NIC |
| bf3 | BFB `bfb_post_install` | DPU auto-reboot | Via BF3 PF (SuperNIC) |
| bf3-ipmi | `mlxconfig` via SSH | `ipmitool` from Forge | Via BF3 PF → VF after reboot |
| bmc | Redfish BIOS PATCH | Redfish cold boot | Via BF3 PF → VF after reboot |

### 2.5 Integration with Existing Forge

- Backend follows Forge patterns: routes (thin HTTP), services (business logic), schemas (Pydantic)
- Phase 4 (BNK platform) SHOULD reuse existing Forge modules where overlapping
- New project type in Forge: "Bare Metal DPU Deployment"
- Discovery extends Forge's existing cluster scanner concept
- Must follow Forge's test conventions (unit/component/contract/frontend)
- Must integrate with Forge's audit, auth, and multi-user ownership systems

### 2.6 Testing

- Unit tests for all new services (mocked SSH/Redfish)
- Integration tests for service interactions
- E2E tests runnable against a real target host (details TBD)
- Follow existing test patterns (`@pytest.mark.unit`, `@pytest.mark.component`)

---

## 3. Reference Architecture from bnk-poc-deployer

### 3.1 Python Implementation Structure (branch `feat/native-python`)

```
cli/
├── config/          # 6-layer config merge with typed dataclasses
│   ├── model.py     # BnkConfig, HostConfig, BmcConfig, DpuConfig, NetworkConfig, AppConfig
│   ├── loader.py    # defaults → profile → file → creds → env → CLI
│   ├── profiles.py  # Version profile loader
│   └── creds.py     # Credentials loader
├── commands/        # CLI command dispatch
│   ├── deploy_regular.py   # Regular topology (4 phases on host)
│   ├── deploy_bf3.py       # BF3 topology (4 phases with host prep)
│   ├── deploy_bmc.py       # BMC topology (13-step workstation flow)
│   └── discover.py         # Hardware discovery
├── execution/       # Remote execution layer
│   └── ssh.py       # SSHSession (subprocess ssh, supports ProxyJump)
├── discovery/       # Hardware probes
│   ├── state.py     # DiscoveryState dataclass hierarchy
│   ├── host_probe.py
│   ├── bmc_probe.py
│   └── cluster_probe.py
├── phases/          # Deployment phase implementations
│   ├── phase1/      # DPU provisioning (BFB config, flash)
│   ├── phase2/      # Host K8s (prereqs, kubeadm, addons)
│   ├── phase3/      # DPU join (kubeadm join, label, taint)
│   └── phase4/      # App platform (FLO, BNK, certs, observability)
├── redfish/         # BMC client layer
│   ├── client.py    # RedfishClient (stdlib urllib)
│   └── vendors/     # VendorPlugin strategy pattern
└── util/            # Shared utilities
    ├── errors.py    # Exception hierarchy
    ├── connectivity.py  # Connectivity risk assessment
    └── k8s.py       # kubectl/helm wrappers
```

### 3.2 Key Patterns to Preserve

1. **Probe-based idempotency**: State detected from live probes, not persisted state files
2. **Connectivity preservation layers**: Pre-staged configs, fallback timers, Phase C, watchers
3. **Topology dispatch**: Topology determines phase sequence and connectivity strategy
4. **Version profiles**: Coordinated component versions per BNK release
5. **Dual-mode execution**: Same phase code works locally (subprocess) or remotely (SSHSession)

---

## 4. Questions for the Architect

### 4.1 Service Decomposition

How should the new capability be organized within Forge's backend?

Options to evaluate:
- **A**: Flat services (`services/ssh_service.py`, `services/redfish_service.py`, `services/dpu_provisioning_service.py`)
- **B**: Package per domain (`services/bare_metal/ssh.py`, `services/bare_metal/redfish.py`, ...)
- **C**: Package per phase (`services/bare_metal/phase1/`, `services/bare_metal/phase2/`, ...)

### 4.2 Workflow Orchestration

How should the multi-phase deployment workflow be modeled?

Forge currently has `StackDeploymentService` for multi-module deployment and Celery tasks
for async execution. Options:
- **A**: Extend `StackDeploymentService` with bare-metal phases
- **B**: New `BareMetalDeploymentService` with its own task model
- **C**: Generic workflow engine that both stack deployment and bare-metal use

### 4.3 Discovery Integration

How should discovery relate to the existing cluster scanner?

- **A**: Extend `services/scanner/` with bare-metal probes
- **B**: Separate `services/discovery/` package (bare-metal specific)
- **C**: Discovery as a workflow step that feeds into the scanner model

### 4.4 SSH Session Lifecycle

How should long-lived SSH sessions be managed?

- Connection pooling (reuse connections across steps)?
- ControlMaster/ControlPath for SSH multiplexing?
- Session cleanup on deployment cancel/abort?
- Credential storage (SSH keys, passwords) — in Forge's existing encrypted secrets?

### 4.5 Data Model

What new database models are needed?

Candidates:
- `BareMetalHost` — host registration with SSH/IPMI/BMC access details
- `DpuDevice` — DPU hardware info, software versions, BFB history
- `DeploymentTopology` — topology config (VLAN, network mode, etc.)
- `BareMetalDeployment` — deployment instance with phase/step state
- `DiscoveryResult` — structured probe results (JSON column or normalized)
- `BnkVersionProfile` — version matrix (may extend existing models)

### 4.6 Phase 4 Overlap

How to handle the overlap between bnk-poc-deployer Phase 4 and existing Forge modules?

Specific overlaps:
- FLO installation → `modules/bnk/flo.py` (Forge has this)
- CNEInstance CR → `modules/bnk/cneinstance.py` (Forge has this)
- cert-manager → `modules/k8s/cert_manager.py` (Forge has this)
- CWC certificates → Not in Forge modules (PoC deployer only)
- OTEL certificates → Not in Forge modules (PoC deployer only)
- Observability stack → Not in Forge modules (PoC deployer only)

### 4.7 Frontend Scope

What UI pages/components are needed?

Candidates:
- Project creation wizard with topology selection
- Discovery results page (probe results + recommendations)
- Deployment monitoring page (phase/step progress + streaming output)
- Host registration form (SSH, IPMI, BMC credentials)
- VLAN/network configuration form with validation results

### 4.8 API Routes

What new API routes are needed?

Candidates:
- `POST /api/bare-metal/hosts` — register a host
- `POST /api/bare-metal/hosts/{id}/discover` — run discovery
- `GET /api/bare-metal/hosts/{id}/discovery` — get discovery results
- `POST /api/bare-metal/deployments` — start deployment
- `GET /api/bare-metal/deployments/{id}` — get deployment state
- `POST /api/bare-metal/deployments/{id}/resume` — resume from step
- `GET /api/bare-metal/version-profiles` — list BNK version profiles

### 4.9 MCP Tools

What MCP tools should be added?

Candidates:
- `discover_host` — run discovery probes
- `deploy_bare_metal` — start deployment
- `get_deployment_status` — check deployment progress
- `list_version_profiles` — list available BNK versions

---

## 5. Deliverables Expected from Architect

1. **Service decomposition** with clear boundaries and responsibilities
2. **Data model design** (SQLAlchemy models, Pydantic schemas)
3. **API route design** (endpoints, request/response models)
4. **Workflow design** (how phases/steps are orchestrated, state managed)
5. **Test strategy** (what to test at each layer, how to mock remote execution)
6. **Implementation phasing** (what to build first, dependency order)
7. **Risk assessment** (hardest parts, potential blockers)
