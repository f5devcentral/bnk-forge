# DPU Bare-Metal Deployment — Implementation Plan

> Records how the architecture spec is decomposed into builder tasks, sequencing
> decisions, and rationale for the chosen approach.
>
> Date: 2026-04-13
> Branch: `feat/dpu-bare-metal-deploy`
> References:
> - [DPU_DEPLOY_SPEC.md](DPU_DEPLOY_SPEC.md) — Architecture specification
> - [DPU_DEPLOY_REQUIREMENTS.md](DPU_DEPLOY_REQUIREMENTS.md) — Requirements

---

## 1. Decomposition Approach

### Principles

1. **Vertical slices where possible** — each task delivers a testable increment
2. **Foundation first** — data models and schemas before any business logic
3. **Execution layer before business logic** — SSH/IPMI/Redfish must work before phases use them
4. **Discovery before deployment** — users should be able to discover and validate before deploying
5. **Innermost loop first** — unit tests with every task, integration tests when services connect
6. **One concern per task** — each builder task touches one service or one test file

### Dependency Graph

```
Phase 0: Foundation (enums → models → migration → schemas → version profiles)
    │
    ▼
Phase 1: SSH Execution Layer (ssh_session → ssh_pool → tests)
    │
    ├──────────────────────────────────────┐
    ▼                                      ▼
Phase 2: Discovery                   Phase 2b: BMC/IPMI Clients
(host_probe → dpu_probe →           (redfish_client → vendor_plugins
 topology_detector → vlan_probe →     → ipmi_client → tests)
 discovery_service → routes → tests)
    │                                      │
    ├──────────────────────────────────────┘
    ▼
Phase 3: Orchestration (step_registry → orchestrator → celery_task → routes → tests)
    │
    ▼
Phase 4: DPU Provisioning (bfb_config → connectivity_services → phase1_service → tests)
    │
    ▼
Phase 5: K8s Bootstrap + Join (phase2_service → phase3_service → tests)
    │
    ▼
Phase 6: Platform + Integration (phase4_service → k8s_cluster_linking → e2e_tests)
    │
    ▼
Phase 7: Polish (version_profile_routes → mcp_tools → audit → openapi)
```

---

## 2. Task Registry

Each task is a bounded unit of work for the builder agent. Tasks are numbered
for reference and sequenced by dependency.

### Phase 0: Foundation

| Task ID | Description | Files | Test Files | Depends On |
|---------|-------------|-------|-----------|-----------|
| BM-000 | Add bare-metal enums to `models/enums.py` | `models/enums.py` | `tests/unit/test_schemas_bare_metal.py` (partial) | — |
| BM-001 | Create `BareMetalHost`, `BareMetalDeployment`, `DeploymentStep`, `BnkVersionProfile` models | `models/bare_metal.py`, `models/__init__.py` | — | BM-000 |
| BM-002 | Create Alembic migration for new tables | `alembic/versions/xxx_add_bare_metal_tables.py` | — | BM-001 |
| BM-003 | Create Pydantic request/response schemas | `schemas/bare_metal.py` | `tests/unit/test_schemas_bare_metal.py` | BM-000 |
| BM-004 | Create `BnkVersionProfileService` + seed BNK 2.1/2.2 profiles | `services/bare_metal/__init__.py`, `services/bare_metal/version_profiles.py` | `tests/unit/test_version_profiles.py` | BM-001, BM-003 |

### Phase 1: SSH Execution Layer

| Task ID | Description | Files | Test Files | Depends On |
|---------|-------------|-------|-----------|-----------|
| BM-010 | Implement `SSHSession` (execute, scp, rsync, wait_for_ssh) | `services/bare_metal/ssh_session.py` | `tests/unit/test_ssh_session.py` | — |
| BM-011 | Implement `SSHConnectionPool` (ControlMaster management) | `services/bare_metal/ssh_pool.py` | `tests/unit/test_ssh_pool.py` | — |
| BM-012 | SSH integration test (real host connection, E2E marker) | — | `tests/e2e/test_ssh_connection.py` | BM-010 |

### Phase 2: Discovery + BMC/IPMI

| Task ID | Description | Files | Test Files | Depends On |
|---------|-------------|-------|-----------|-----------|
| BM-020 | Implement `host_probe.py` (OS, NIC mode, PF/VF, hugepages, K8s) | `services/bare_metal/discovery/host_probe.py` | `tests/unit/test_host_probe.py` | BM-010 |
| BM-021 | Implement `dpu_probe.py` (DPU SSH, software versions, OVS state) | `services/bare_metal/discovery/dpu_probe.py` | `tests/unit/test_dpu_probe.py` | BM-010 |
| BM-022 | Implement `RedfishClient` + `VendorPlugin` base + generic plugin | `services/bare_metal/redfish/client.py`, `vendor_base.py`, `generic.py` | `tests/unit/test_redfish_client.py` | — |
| BM-023 | Implement vendor plugins (Supermicro, Lenovo, Dell, HPE) | `services/bare_metal/redfish/supermicro.py`, `lenovo.py`, `dell.py`, `hpe.py` | `tests/unit/test_redfish_vendors.py` | BM-022 |
| BM-024 | Implement `bmc_probe.py` (Redfish probes for BMC) | `services/bare_metal/discovery/bmc_probe.py` | `tests/unit/test_bmc_probe.py` | BM-022 |
| BM-025 | Implement `IPMIClient` (ipmitool subprocess wrapper) | `services/bare_metal/ipmi_client.py` | `tests/unit/test_ipmi_client.py` | — |
| BM-026 | Implement `vlan_probe.py` (VLAN path validation) | `services/bare_metal/discovery/vlan_probe.py` | `tests/unit/test_vlan_probe.py` | BM-010 |
| BM-027 | Implement `topology_detector.py` (pure function, auto-detect topology) | `services/bare_metal/discovery/topology_detector.py` | `tests/unit/test_topology_detector.py` | — |
| BM-028 | Implement `BareMetalDiscoveryService` (orchestrates all probes) | `services/bare_metal/discovery/__init__.py` | `tests/component/test_bare_metal_discovery.py` | BM-020 through BM-027 |
| BM-029 | Implement bare-metal host CRUD + discovery routes | `routes/bare_metal_hosts.py` | `tests/component/test_bare_metal_host_routes.py` | BM-003, BM-004, BM-028 |
| BM-02E | Discovery E2E test (real host) | — | `tests/e2e/test_bare_metal_discovery.py` | BM-029 |

### Phase 3: Deployment Orchestration

| Task ID | Description | Files | Test Files | Depends On |
|---------|-------------|-------|-----------|-----------|
| BM-030 | Implement step registries (REGULAR, BF3, BF3_IPMI, BMC step lists) | `services/bare_metal/orchestrator.py` (step definitions only) | `tests/unit/test_step_registry.py` | BM-000 |
| BM-031 | Implement `BareMetalDeploymentService` (state machine, execute loop, resume) | `services/bare_metal/orchestrator.py` (full service) | `tests/component/test_bare_metal_orchestrator.py` | BM-030, BM-010 |
| BM-032 | Implement Celery tasks (`execute_bare_metal_deployment`, `run_bare_metal_discovery`) | `tasks/bare_metal_tasks.py` | `tests/component/test_bare_metal_tasks.py` | BM-031, BM-028 |
| BM-033 | Implement deployment routes (create, get, resume, cancel, step logs) | `routes/bare_metal_deployments.py` | `tests/component/test_bare_metal_deployment_routes.py` | BM-003, BM-032 |

### Phase 4: DPU Provisioning (Phase 1 Service)

| Task ID | Description | Files | Test Files | Depends On |
|---------|-------------|-------|-----------|-----------|
| BM-040 | Implement `BfbConfigGenerator` (render BFB config from version profile + host config) | `services/bare_metal/provisioning/bfb_config.py` | `tests/unit/test_bfb_config.py` | BM-004 |
| BM-041 | Implement connectivity services (NetplanStager, FallbackTimer, PhaseCBootstrap, PostFlashWatcher) | `services/bare_metal/connectivity/` (all 4 files + __init__.py) | `tests/unit/test_connectivity_services.py` | BM-010 |
| BM-042 | Implement `Phase1DpuService` (orchestrates flash, connectivity, DPU prereqs) | `services/bare_metal/provisioning/phase1_dpu.py` | `tests/component/test_phase1_dpu.py` | BM-040, BM-041, BM-010 |

### Phase 5: K8s Bootstrap + DPU Join

| Task ID | Description | Files | Test Files | Depends On |
|---------|-------------|-------|-----------|-----------|
| BM-050 | Implement `Phase2K8sService` (kubeadm init, CNI, addons, storage) | `services/bare_metal/provisioning/phase2_k8s.py` | `tests/component/test_phase2_k8s.py` | BM-010, BM-004 |
| BM-051 | Implement `Phase3JoinService` (kubeadm join, label, taint) | `services/bare_metal/provisioning/phase3_join.py` | `tests/component/test_phase3_join.py` | BM-010 |

### Phase 6: Platform + Integration

| Task ID | Description | Files | Test Files | Depends On |
|---------|-------------|-------|-----------|-----------|
| BM-060 | Implement `Phase4PlatformService` (bridge to Forge modules + CWC/OTEL certs) | `services/bare_metal/provisioning/phase4_platform.py` | `tests/component/test_phase4_platform.py` | BM-010, Forge modules |
| BM-061 | K8s cluster linking (register cluster in Forge after Phase 2, link kubeconfig) | `services/bare_metal/orchestrator.py` additions | `tests/component/test_cluster_linking.py` | BM-050 |
| BM-062 | E2E deployment test (full 4-phase deployment against real host) | — | `tests/e2e/test_bare_metal_deployment.py` | All previous |

### Phase 7: Polish

| Task ID | Description | Files | Test Files | Depends On |
|---------|-------------|-------|-----------|-----------|
| BM-070 | Version profiles route (list, get, create) | `routes/bare_metal_version_profiles.py` | `tests/component/test_version_profile_routes.py` | BM-004 |
| BM-071 | Audit integration (bare-metal events in audit trail) | Service additions | — | All services |
| BM-072 | OpenAPI types regeneration | `make openapi-types` | — | All routes |
| BM-073 | Contract tests (response shape verification for all bare-metal routes) | — | `tests/contract/test_bare_metal_contracts.py` | All routes |

---

## 3. Sequencing Decisions

### Decision S1: Foundation before execution

**Choice:** Build data models and schemas (Phase 0) before any service code.

**Rationale:** Every service and route depends on the models. Getting the data model
right first prevents refactoring cascades. The migration can be tested immediately
(create tables, verify columns, test rollback).

### Decision S2: SSH layer is the critical path

**Choice:** Build SSH execution (Phase 1) immediately after foundation, before discovery or deployment.

**Rationale:** Every probe, every deployment step, and every connectivity service depends
on being able to execute commands on remote hosts. SSH is the foundation upon which
everything else is built. Getting it right early (with integration tests against a real
host) de-risks the entire project.

### Decision S3: Discovery before deployment

**Choice:** Build the full discovery pipeline (Phase 2) before any deployment orchestration.

**Rationale:** Discovery is both a standalone user tool (R2.1) and a deployment prerequisite.
It's also lower-risk than deployment — discovery is read-only, can't brick a server.
Building it first lets users validate their environment while we build the deployment flow.

### Decision S4: Orchestrator skeleton before phase services

**Choice:** Build the step registry and state machine (Phase 3) before implementing
individual phase services (Phases 4-6).

**Rationale:** The orchestrator defines the contract that phase services must fulfill.
With mocked phase services, we can test the entire state machine (start, progress,
fail, resume, cancel) without any real SSH execution. Phase services are then plugged
in one at a time.

### Decision S5: Phase 1 (DPU) is the highest-risk phase

**Choice:** Build DPU provisioning (Phase 4 of implementation) before K8s bootstrap
(Phase 5), even though K8s is Phase 2 in the deployment flow.

**Rationale:** DPU provisioning involves connectivity-risking operations (BFB flash,
mode changes, reboots). The connectivity preservation services are the most complex
and highest-risk code in the project. Building them early maximizes the time available
for testing and iteration. K8s bootstrap (Phase 2) is well-understood SSH + kubeadm —
lower risk.

### Decision S6: Redfish vendor plugins are incremental

**Choice:** Implement generic + Supermicro vendor plugin first, add others incrementally.

**Rationale:** Our test hardware is Supermicro. The generic plugin handles 90% of cases
(the NIC mode attribute comes from Mellanox firmware, not the BMC vendor). Other vendors
(Lenovo, Dell, HPE) are thin overrides added when hardware becomes available for testing.

### Decision S7: Frontend deferred

**Choice:** Build all backend (API, services, tests) before any frontend work.

**Rationale:** The API is the contract. Frontend pages are follow-on work that can proceed
in parallel (or after) once routes are stable. This keeps the initial scope focused and
allows E2E testing via API before UI is available.

### Decision S8: MCP tools deferred

**Choice:** Add MCP tools after all API routes are finalized.

**Rationale:** MCP tools are thin wrappers around API routes. They depend on stable route
paths and response shapes. Adding them too early means they break with every route change.

### Decision S9: Single builder task per service file

**Choice:** Each builder task creates or modifies one primary service file and its
corresponding test file.

**Rationale:** Keeps tasks small and reviewable. Builder-reviewer loop stays tight.
No massive PRs that are hard to review. Each task can be committed independently.

---

## 4. Acceptance Criteria per Phase

### Phase 0: Foundation
- [ ] All new enums importable from `models.enums`
- [ ] All new models importable from `models`
- [ ] Alembic migration runs and rolls back cleanly
- [ ] All Pydantic schemas validate with example data
- [ ] BNK 2.1 and 2.2 version profiles seeded
- [ ] `make test-backend-unit` passes with new tests

### Phase 1: SSH Execution
- [ ] `SSHSession.execute()` runs a command on a local subprocess (mocked test)
- [ ] `SSHSession.scp_to()` transfers a file (mocked test)
- [ ] `SSHSession.wait_for_ssh()` polls for connection (mocked test)
- [ ] Jumphost chain support tested with mock
- [ ] E2E: SSH to a real host and execute `uname -a` (requires test host)

### Phase 2: Discovery
- [ ] Each probe function returns structured results (typed dicts)
- [ ] `TopologyDetector` correctly classifies all four topologies from probe results
- [ ] `BareMetalDiscoveryService` orchestrates all probes and produces `BareMetalDiscoveryResponse`
- [ ] Host CRUD routes work (create, read, update, delete)
- [ ] `POST /discover` triggers async discovery via Celery
- [ ] `GET /discovery` returns latest results
- [ ] E2E: Full discovery against a real host produces accurate results

### Phase 3: Orchestration
- [ ] Step registries define correct steps for all four topologies
- [ ] Orchestrator state machine transitions correctly (PENDING → IN_PROGRESS → COMPLETED)
- [ ] Resume from failed step works (sets `resume_from_step`, re-executes from that point)
- [ ] Cancel sets status to CANCELLED and revokes Celery task
- [ ] Concurrent deployment prevention (reject if active deployment exists for same host)
- [ ] Deployment routes work (create, get, resume, cancel, step logs)

### Phase 4: DPU Provisioning
- [ ] `BfbConfigGenerator` produces valid BFB config for BNK 2.1 and 2.2
- [ ] All four connectivity services deposit correct scripts/configs (verified by content assertion)
- [ ] `Phase1DpuService` executes the correct step sequence for each topology

### Phase 5: K8s Bootstrap + Join
- [ ] `Phase2K8sService` runs kubeadm init with correct version from profile
- [ ] `Phase3JoinService` joins DPU, labels, and taints correctly

### Phase 6: Platform + Integration
- [ ] `Phase4PlatformService` invokes Forge FLO module
- [ ] New K8s cluster registered in Forge with kubeconfig after Phase 2
- [ ] E2E: Full deployment from bare metal to running BNK

### Phase 7: Polish
- [ ] Version profile routes serve correct data
- [ ] All routes produce responses matching Pydantic schemas (contract tests)
- [ ] OpenAPI types freshness check passes (`make openapi-types`)

---

## 5. File Count Summary

| Phase | New Files | Test Files | Total |
|-------|----------|-----------|-------|
| 0: Foundation | 4 | 2 | 6 |
| 1: SSH | 2 | 3 | 5 |
| 2: Discovery | 12 | 10 | 22 |
| 3: Orchestration | 3 | 4 | 7 |
| 4: DPU Provisioning | 6 | 3 | 9 |
| 5: K8s Bootstrap | 2 | 2 | 4 |
| 6: Platform | 1 | 3 | 4 |
| 7: Polish | 1 | 1 | 2 |
| **Total** | **31** | **28** | **59** |

---

## 6. Parallelization Strategy (Worktrees)

The dependency graph allows two rounds of parallel work via git worktrees:

```
Phase 0: Foundation ◄── SEQUENTIAL (everything depends on this)
    │
    ▼
Phase 1: SSH ◄── SEQUENTIAL (everything depends on this)
    │
    ├──────────────────────────────────┐
    ▼                                  ▼
Phase 2a: Discovery Probes        Phase 2b: Redfish + IPMI Clients
(BM-020..BM-021, BM-026..BM-027) (BM-022..BM-025)
 ◄── WORKTREE A                    ◄── WORKTREE B
    │                                  │
    ├──────────────────────────────────┘
    ▼
Phase 2c: Discovery Service + Routes ◄── SEQUENTIAL (merges A+B)
(BM-028, BM-029, BM-02E)
    │
    ▼
Phase 3: Orchestration ◄── SEQUENTIAL
    │
    ├──────────────────────┐
    ▼                      ▼
Phase 4: DPU Provisioning Phase 5: K8s Bootstrap + Join
(BM-040..BM-042)          (BM-050..BM-051)
 ◄── WORKTREE A            ◄── WORKTREE B
    │                      │
    ├──────────────────────┘
    ▼
Phase 6: Platform + Integration ◄── SEQUENTIAL (merges A+B)
    │
    ▼
Phase 7: Polish ◄── SEQUENTIAL
```

### Worktree Plan

| Round | Worktree A | Worktree B | Merge Point |
|-------|-----------|-----------|-------------|
| **Round 1** | Phase 2a: SSH-based probes (host, DPU, VLAN, topology detector) | Phase 2b: HTTP/subprocess clients (Redfish, IPMI, BMC probe) | Merge into feature branch before Phase 2c |
| **Round 2** | Phase 4: DPU provisioning (BFB config, connectivity, Phase 1 service) | Phase 5: K8s bootstrap + DPU join (Phase 2-3 services) | Merge into feature branch before Phase 6 |

### Estimated Timeline Savings

| Approach | Duration |
|----------|----------|
| Fully sequential | ~6 weeks |
| With 2 parallelization rounds | ~4 weeks |

### Worktree Naming Convention

```
feat/dpu-bare-metal-deploy              # Main feature branch
feat/dpu-bare-metal-deploy--discovery   # Round 1, Worktree A
feat/dpu-bare-metal-deploy--bmc-clients # Round 1, Worktree B
feat/dpu-bare-metal-deploy--phase1-dpu  # Round 2, Worktree A
feat/dpu-bare-metal-deploy--phase2-k8s  # Round 2, Worktree B
```
