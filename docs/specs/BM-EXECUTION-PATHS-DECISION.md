# Bare-Metal DPU Deployment — Execution Path Decision Record

> **Status:** OPEN FOR REVIEW — No path chosen yet  
> **Date:** 2026-04-16  
> **Branch:** `feat/dpu-bare-metal-deploy`  
> **Context:** Phase 0-3 (discovery, probes, UI, actionable assessment) complete.  
> Pre-Phase-4 consolidation complete (SSH dedup, discovery persistence, step composability).  
> Decision needed on how to implement Phase 4-7 step executors.

---

## Current State (What Exists Today)

### Infrastructure Already Built

| Component | Status | Where |
|-----------|--------|-------|
| `BareMetalHost` model (project-scoped, persisted) | ✅ Done | `models/bare_metal.py` |
| Discovery probes (host, BMC, DPU, topology) | ✅ Done | `services/bare_metal/discovery/` |
| Discovery state persistence (10 typed columns + full result cache) | ✅ Done | CON-010..013 |
| Actionable assessment (NIC mode, BFB, K8s, Phase C, BMC) | ✅ Done | BM-ASSESS |
| Step composability (phase/step selection, plan preview, prerequisites) | ✅ Done | CON-020..022 |
| Shared SSH primitives (`paramiko_utils`) | ✅ Done | CON-001..007 |
| `BareMetalDeploymentService` orchestrator with step registries | ✅ Done | `services/bare_metal/orchestrator.py` |
| Frontend discovery UI + actionable assessment | ✅ Done | `components/bare-metal/BareMetalPanel.tsx` |
| `DeploymentEngine` ABC (`init/plan/apply/destroy`) | ✅ Production | `services/execution/engine_interface.py` |
| Four engine implementations (OpenTofu, Kubernetes, Operator, Ansible) | ✅ Production | `services/execution/*_engine.py` |
| `StackTemplate` blueprint system | ✅ Production | `models/stack.py`, `data/stack_templates.json` |
| Python module registry (ManifestModule, HelmModule) | ✅ Production | `modules/base.py`, `modules/` |
| Dependency graph + parallel execution service | ✅ Production | `services/parallel_execution_service.py` |
| Module task dispatch (routes to correct engine) | ✅ Production | `services/execution/task_dispatch.py` |

### What's NOT Built (Phase 4-7)

- Step executor code (the actual SSH commands for flash_dpu, kubeadm_init, install_flo, etc.)
- `SSHEngine` (a `DeploymentEngine` implementation for SSH-based steps)
- `SSHModule` base class (module definitions for SSH-based steps)
- BNK bare-metal `StackTemplate` (the blueprint)
- K8s cluster auto-linking after Phase 2 creates a cluster
- Frontend deployment wizard / step selection UI
- Frontend deployment progress / live output UI

---

## The Three Execution Paths

### Path A: Standalone Bare-Metal Orchestrator (Original Plan)

**Description:** Implement step executors as Python service methods within the existing `BareMetalDeploymentService` orchestrator. Each phase gets its own service file. The orchestrator dispatches to phase services sequentially. This is a self-contained system parallel to the blueprint/engine system.

**Architecture:**
```
BareMetalDeploymentService (orchestrator)
  ├── Phase1DPUService.flash_dpu(ssh, host, vp)
  ├── Phase1DPUService.install_dpu_prereqs(ssh, host, vp)
  ├── Phase2K8sService.kubeadm_init(ssh, host, vp)
  ├── Phase2K8sService.install_cni(ssh, host, vp)
  ├── Phase3JoinService.kubeadm_join(ssh, host, vp)
  ├── Phase4PlatformService.install_flo(ssh, host, vp)
  └── Phase4PlatformService.deploy_bnk_cr(ssh, host, vp)
```

**Strengths:**
- Simplest to implement — just write the SSH commands as Python methods
- No changes to the existing engine/blueprint infrastructure
- Step composability already built (CON-020..022) — users can select phases/steps
- Fastest time to working end-to-end deployment
- Connectivity-risk handling (wait-for-reconnect, netplan staging) is natural in sequential service methods
- Clean `(ssh_session, version_profile) → result` interface per step

**Weaknesses:**
- Two parallel deployment systems (bare-metal orchestrator vs stack/engine system)
- Cannot reuse existing K8s modules (FLO, CNEInstance, cert-manager) — must rewrite as SSH commands
- No blueprint support — users can't compose bare-metal steps with cloud modules in a single blueprint
- Bare-metal deployments don't appear in the same UI as stack deployments
- Step selection is bare-metal-specific (CON-020 `selected_phases`), not the standard module toggle pattern

**Estimated effort:** ~4-5 builder sessions for Phase 4-7
**Overhead if abandoned:** Step executor code (Python methods) is reusable as `SSHEngine.apply()` bodies. The SSH commands themselves transfer to any path. Loss: ~1 session of orchestrator wiring that wouldn't be needed under Path B/C.

---

### Path B: SSHEngine + Module Definitions + Blueprint (Convergence Path)

**Description:** Build an `SSHEngine` implementing the existing `DeploymentEngine` ABC. Each bare-metal step becomes a module with a Python class definition (like `ManifestModule` but with SSH commands instead of K8s manifests). Register them in `ModuleLibrary` with `engine_type: "ssh"`. Compose into a `StackTemplate` blueprint. The existing stack deployment service, dependency graph, task dispatch, and UI handle everything.

**Architecture:**
```
StackTemplate ("BNK DPU Bare-Metal")
  ├── bare-metal/probe-dpu     → SSHEngine.plan()   → probe current state
  ├── bare-metal/flash-dpu     → SSHEngine.apply()   → BFB flash via rshim
  ├── bare-metal/kubeadm-init  → SSHEngine.apply()   → kubeadm init on host
  ├── k8s/bnk-prerequisites    → KubernetesEngine     → (EXISTING MODULE, reused as-is)
  ├── bnk/flo                  → KubernetesEngine     → (EXISTING MODULE, reused as-is)
  └── bnk/cneinstance          → KubernetesEngine     → (EXISTING MODULE, reused as-is)
```

**New components needed:**
1. `SSHEngine` (~350 lines) — `DeploymentEngine` implementation using paramiko
2. `SSHModule` base class (~50 lines) — `BaseModule` variant for SSH steps
3. SSH fields on `ModuleContext` (~6 fields, additive)
4. `"ssh"` in `_EXPLICIT_ENGINE_TYPES` in `task_dispatch.py` (1 line)
5. `"ssh"` routing in `engine_router.py` (~10 lines)
6. Module definitions per step (~50-100 lines each, ~15 modules)
7. `ModuleLibrary` seed entries (JSON data)
8. `StackTemplate` entry in `stack_templates.json` (JSON data)

**How `SSHEngine` maps the lifecycle:**
| Method | Bare-metal meaning | Example: `flash_dpu` |
|--------|-------------------|---------------------|
| `init` | Validate SSH reachable, check prerequisites | Can reach host? rshim device present? BFB image URL valid? |
| `plan` | Probe whether step is already done | Check current DOCA version vs target — flash needed? |
| `apply` | Execute SSH commands | Pre-stage netplan → flash BFB → wait for reconnect → validate |
| `destroy` | Reverse where possible | Not applicable for hardware flash (no-op) |
| `get_outputs` | Return state for downstream modules | `{dpu_ip: "192.168.100.2", doca_version: "2.9.1"}` |

**How `ModuleContext` extends for SSH:**
```python
@dataclass
class ModuleContext:
    # ... existing fields (module_id, project_id, path, variables, etc.) ...
    
    # SSH-specific (ignored by OpenTofu/K8s engines)
    ssh_host: str | None = None
    ssh_port: int = 22
    ssh_credential_id: int | None = None
    jumphost_chain: list[dict] | None = None
    dpu_relay: bool = False                    # Target is DPU via host relay
    dpu_host: str | None = None
    dpu_credential_id: int | None = None
    connectivity_risk: bool = False            # Step may drop SSH
    connectivity_mechanism: str | None = None  # How to recover
```

**How the BNK blueprint would look in `stack_templates.json`:**
```json
{
  "name": "BNK DPU Bare-Metal Deployment",
  "slug": "bnk-bare-metal-dpu",
  "category": "bare-metal",
  "cloud_provider": null,
  "modules": [
    {"path": "bare-metal/probe-dpu", "required": true, "variables": {}},
    {"path": "bare-metal/set-nic-mode", "required": false, "variables": {}},
    {"path": "bare-metal/flash-dpu", "required": true, "variables": {"bfb_url": "..."}},
    {"path": "bare-metal/install-dpu-prereqs", "required": true, "variables": {}},
    {"path": "bare-metal/kubeadm-init", "required": true, "variables": {"k8s_version": "1.30.2"}},
    {"path": "k8s/bnk-prerequisites", "required": true, "variables": {}},
    {"path": "bnk/flo", "required": true, "variables": {}},
    {"path": "bnk/cneinstance", "required": true, "variables": {}},
    {"path": "bnk/bnk-vlans", "required": true, "variables": {}},
    {"path": "bnk/bnk-gatewayclass", "required": true, "variables": {}}
  ],
  "variable_templates": {
    "host_ip": {"type": "string", "required": true},
    "ssh_credential_id": {"type": "number", "required": true},
    "bfb_url": {"type": "string", "required": false}
  },
  "prerequisites": [
    {"type": "bare_metal_host", "description": "Registered bare-metal host with SSH access"}
  ]
}
```

**Users can fork and customize:**
- Drop `flash-dpu` if DPU is already provisioned
- Drop `set-nic-mode` if already in DPU mode
- Add `bare-metal/install-cert-manager` or swap for Helm-based cert-manager
- Mix SSH modules with K8s modules in a single blueprint
- Create "K8s only" blueprint (just Phase 2-3 steps) for hosts with pre-flashed DPUs

**Strengths:**
- One deployment system for everything (cloud, on-prem, bare-metal)
- Existing K8s modules (FLO, CNEInstance, cert-manager, prerequisites) reused directly — no rewrite
- Blueprint composability — users can mix SSH and K8s modules in one template
- Existing UI works (stack deployment progress, module grid, variable editor)
- Existing dependency graph handles ordering
- Existing task dispatch, Celery workers, WebSocket log streaming all work
- Users can fork blueprints for different hardware configurations
- `plan()` gives free drift detection (probe whether host state matches desired)

**Weaknesses:**
- More upfront work than Path A (~2 extra sessions for SSHEngine + module definitions)
- Connectivity-risk compound steps push the boundary of `apply()` — a single `apply()` call may run for 10+ minutes with SSH drops mid-execution
- Need to bridge `BareMetalHost` model into `ModuleContext` (host IP, credentials, discovery state)
- The existing `StackDeploymentService` creates `ProjectModule` records — need to verify this works for SSH modules (they don't have git sources or workspaces)
- `ParallelExecutionService` may try to parallelize independent SSH steps — need to verify sequential constraint for connectivity-risk steps

**Estimated effort:** ~6-7 builder sessions for Phase 4-7
**Overhead if abandoned:** SSHEngine + module definitions (~2 sessions) are throwaway if we revert to Path A. But the step executor logic (the actual SSH commands) transfers directly to Path A service methods.

---

### Path C: Ansible Playbook Modules (Use Existing Engine)

**Description:** Write each bare-metal step as an Ansible playbook. Register in `ModuleLibrary` with `engine_type: "ansible"` and a `pack_manifest`. The existing `AnsibleEngine` runs them. Compose into blueprints same as Path B.

**Architecture:**
```
StackTemplate ("BNK DPU Bare-Metal")
  ├── bare-metal/flash-dpu     → AnsibleEngine  → playbook: flash_dpu.yml
  ├── bare-metal/kubeadm-init  → AnsibleEngine  → playbook: kubeadm_init.yml
  ├── k8s/bnk-prerequisites    → KubernetesEngine → (existing module)
  ├── bnk/flo                  → KubernetesEngine → (existing module)
  └── bnk/cneinstance          → KubernetesEngine → (existing module)
```

**How Ansible handles SSH:**
- Ansible natively SSHes to remote hosts — that's its core function
- Multi-hop / jumphost: `ansible_ssh_common_args: "-J jumphost1,jumphost2"`
- DPU relay: Ansible's `ProxyJump` config or a two-play playbook (play 1 on host, play 2 on DPU)
- Wait-for-reconnect: `delegate_to: localhost` + `wait_for_connection` module

**Strengths:**
- No new engine to build — `AnsibleEngine` already exists and works
- Full blueprint composability (same as Path B)
- Ansible playbooks are self-documenting and runnable outside Forge
- Ansible's `--check` mode provides free `plan()` semantics
- Ansible's idempotency model (modules report "changed" vs "ok") maps to probe-before-act
- Rich ecosystem of Ansible modules (k8s, helm, apt, systemd, etc.)
- Connectivity-risk: `wait_for_connection` + `delegate_to: localhost` handles SSH drops

**Weaknesses:**
- Ansible must be installed in the Forge container (it already is — `AnsibleRunner.health_check()` confirms)
- Ansible adds overhead vs direct paramiko SSH (~2-3s per task for SSH setup)
- The governed runner contract (`AnsibleManifestContractError`) requires a specific `pack_manifest` structure — every module needs this metadata
- Need to write YAML playbooks instead of Python — different skillset, harder to unit test
- Ansible's connection model reconnects per task, not per session — slower for multi-command steps
- The `AnsibleEngine` runs playbooks on the Forge container (local subprocess) — Ansible then SSHes OUT to the target. This means SSH credentials need to be passed as Ansible variables or inventory, not through the `SSHCredential` model directly
- No existing integration between `SSHCredential` model and Ansible inventory — need to build this bridge
- `pack_manifest` contract is strict: no hooks, no custom commands, governed profile only. Compound steps that need custom logic (BFB flash + wait + validate) are awkward in pure Ansible
- Debugging is harder — Ansible output is verbose and not structured for the Forge log UI

**Estimated effort:** ~6-7 builder sessions (similar to Path B — writing playbooks takes similar time to writing Python methods, plus the Ansible-specific wiring)
**Overhead if abandoned:** Ansible playbooks are NOT reusable as Python methods. The SSH commands within them transfer, but the YAML structure and Ansible inventory setup do not.

---

## Hybrid Variations

### Path B+: SSHEngine Now, Existing K8s Modules for Phase 4

Build `SSHEngine` for Phase 1-3 (hardware steps). After Phase 2 creates K8s cluster, register it in Forge. Phase 4 steps (FLO, CNEInstance, certs) use the EXISTING `KubernetesEngine` modules directly. This is the most natural split:

- **Phase 1 (DPU):** `SSHEngine` — flash_dpu, set_nic_mode, install_prereqs
- **Phase 2 (K8s):** `SSHEngine` — kubeadm_init, install_cni (SSH-based since no kubeconfig in Forge yet)
- **Phase 2 bridge:** Copy kubeconfig from host → register `KubernetesCluster` in Forge
- **Phase 3 (Join):** `SSHEngine` — kubeadm_join, label/taint nodes
- **Phase 4 (Platform):** `KubernetesEngine` — reuse existing FLO, CNEInstance, BNKPrerequisites, BNKVlans, BNKGatewayClass modules AS-IS

**Advantages over pure Path B:** Phase 4 modules already exist and are production-tested. Zero new code for BNK platform deployment.

### Path A→B: Ship Path A, Migrate to Path B Later

Implement Phase 4-7 as Python service methods (Path A). After they're battle-tested, wrap them in `SSHEngine.apply()` bodies and create module/blueprint definitions (Path B). The SSH command logic is identical — it's just a wrapper change.

**Advantages:** Fastest to working deployment. Migration is mechanical, not creative.
**Risk:** "Later" may never happen if other priorities take over.

---

## Comparison Matrix

| Criterion | Path A (Standalone) | Path B (SSHEngine) | Path C (Ansible) | Path B+ (Hybrid) |
|-----------|--------------------|--------------------|-------------------|-------------------|
| **Time to first working deployment** | ~4 sessions | ~6 sessions | ~6 sessions | ~6 sessions |
| **Blueprint composability** | ❌ No | ✅ Yes | ✅ Yes | ✅ Yes |
| **Reuse existing K8s modules** | ❌ Rewrite as SSH | ✅ In blueprint | ✅ In blueprint | ✅ Direct reuse |
| **Unified deployment UI** | ❌ Separate | ✅ Same UI | ✅ Same UI | ✅ Same UI |
| **Connectivity-risk handling** | ✅ Natural | ⚠️ Long apply() | ⚠️ delegate_to | ⚠️ Long apply() |
| **Overhead if abandoned** | ~1 session wasted | ~2 sessions wasted | ~4 sessions wasted | ~2 sessions wasted |
| **New infrastructure to build** | None | SSHEngine + modules | Ansible bridge | SSHEngine + modules |
| **User can customize deployment** | Phase/step select only | Fork blueprint, add/remove modules | Fork blueprint | Fork blueprint |
| **Step logic is testable** | ✅ Python unit tests | ✅ Python unit tests | ⚠️ Ansible test frameworks | ✅ Python unit tests |
| **Dependencies** | None | Paramiko (already present) | Ansible (already present) | Paramiko (already present) |
| **Skillset required** | Python | Python | Python + YAML/Ansible | Python |

---

## Risk Assessment: What If the Chosen Path Fails?

### Path A → fallback
**What "fails" means:** We discover that two parallel deployment systems create too much UX confusion, or we need blueprint composability sooner than expected.
**Fallback:** Migrate to Path B. The step executor Python methods become `SSHEngine.apply()` bodies with minimal wrapping. Estimated migration: ~2 sessions.
**Code lost:** Orchestrator wiring in `_execute_step()` (~200 lines). All SSH command logic reuses.

### Path B → fallback
**What "fails" means:** `SSHEngine.apply()` can't handle connectivity-risk compound steps (SSH drops during 10-minute BFB flash), or `StackDeploymentService` can't handle SSH-specific concerns (sequential constraints, host-scoped variables, credential injection).
**Fallback:** Revert to Path A. Extract step executor bodies from `SSHEngine.apply()` into standalone service methods. The SSH command logic transfers directly.
**Code lost:** SSHEngine class (~350 lines), module definitions (~15 × ~75 lines = ~1100 lines), ModuleContext SSH fields. Total: ~2 sessions of work. The SSH command logic within each module's execute body reuses as-is.

### Path C → fallback
**What "fails" means:** Ansible's overhead is too high, playbook debugging is too painful, or the governed runner contract is too restrictive for compound steps.
**Fallback:** Revert to Path A or B. Ansible playbook YAML does NOT transfer — must rewrite as Python methods.
**Code lost:** All playbook YAML (~15 playbooks × ~50-100 lines = ~1000 lines), Ansible inventory bridge, pack_manifest definitions. Total: ~4 sessions of work.

### Path B+ → fallback
Same as Path B, but with the added benefit that Phase 4 modules are already production-tested and don't fail. Risk is concentrated in Phase 1-3 SSHEngine handling.

---

## UI Interaction Considerations

Any execution path must support these human workflows through the UI:

### 1. Discovery & Assessment Flow
**Already built.** User registers a host → runs discovery → sees probe results + actionable assessment → follows the recommended next-steps (NIC mode change commands, BFB flash guidance, etc.)

### 2. Deployment Planning Flow
User needs to:
- See what steps will run for their host's topology
- Select which phases/steps to include (or use a blueprint as starting point)
- Preview estimated duration and prerequisites
- Confirm before starting

**Path A:** Custom UI in `BareMetalPanel.tsx` using the `/preview` endpoint (CON-022). Step selection is checkboxes. This is bare-metal-specific UI.
**Path B/B+/C:** Standard stack deployment UI. User selects a blueprint → sees modules as toggleable cards (same as AWS EKS Foundation today) → each module shows estimated time, required/optional, variables. This UI already exists.

### 3. Deployment Progress Flow
User needs to:
- See real-time progress per step/module
- See live log output (SSH command output streamed)
- See which step is currently running, which are done, which are pending/skipped
- Intervene (pause, cancel, retry failed step)

**Path A:** Custom progress UI reading from `DeploymentStep` records. Need to build: step progress cards, live log panel, retry/cancel buttons. The WebSocket log streaming infrastructure exists (used by Celery tasks) but needs wiring for the bare-metal orchestrator.
**Path B/B+/C:** Standard module deployment progress UI. The existing module grid already shows per-module status (pending → deploying → deployed → failed). WebSocket log streaming already works for all engines. Retry/cancel already wired. **This UI exists today and needs zero changes.**

### 4. Post-Deployment Flow
User needs to:
- See the resulting K8s cluster in Forge's cluster management
- Navigate from bare-metal host → its cluster → deployed BNK modules
- Run existing cluster scanning, drift detection, etc.

**All paths:** After Phase 2 creates K8s, the cluster must be registered in Forge's `KubernetesCluster` model. This is the bridge. Path B/B+ does this naturally (the kubeadm-init module's `get_outputs()` returns the kubeconfig, which triggers `cluster_auto_registration_service`). Path A needs explicit wiring.

### 5. Re-Run / Maintenance Flow
User needs to:
- Re-run a specific step (e.g., re-flash DPU with updated BFB)
- Re-run discovery after manual changes
- See deployment history per host

**Path A:** Uses `selected_steps` from CON-021. User picks specific steps to re-run. Custom UI needed.
**Path B/B+/C:** Standard stack "redeploy module" action. User clicks on a specific module → redeploy. Existing UI handles this.

### UI Effort Summary

| UI Component | Path A (Build new) | Path B/B+/C (Already exists) |
|-------------|-------------------|------------------------------|
| Blueprint selection / module toggle | ❌ Must build | ✅ Exists (stack template selector) |
| Deployment progress per module | ❌ Must build | ✅ Exists (module status grid) |
| Live log streaming | ⚠️ Wire to BM orchestrator | ✅ Exists (Celery → WebSocket) |
| Variable editor per module | ❌ Must build | ✅ Exists (stack variable form) |
| Retry/cancel deployment | ⚠️ Wire to BM orchestrator | ✅ Exists (module action buttons) |
| Post-deploy cluster view | ⚠️ Wire cluster registration | ✅ Exists (cluster management) |
| Deployment history | ❌ Must build | ✅ Exists (stack instance history) |
| Discovery + assessment | ✅ Already built | ✅ Already built (keep as-is) |

**Bottom line:** Path B/B+/C save approximately **3-4 sessions of frontend work** by reusing the existing stack deployment UI. Path A requires building parallel UI components for the bare-metal deployment experience.

---

## Open Questions for Reviewers

1. **Connectivity-risk steps in SSHEngine:** Is a 10-minute `apply()` call (BFB flash with wait-for-reconnect) acceptable in the engine model? Or should compound steps be split into separate modules with the orchestrator managing the wait?

2. **StackDeploymentService sequential constraint:** The existing stack deployment resolves dependency layers and can parallelize independent modules. Bare-metal steps MUST be sequential on the same host. Is `dependencies` sufficient to force sequential execution, or do we need an explicit sequential mode?

3. **SSHCredential → ModuleContext bridge:** How should the host's SSH credentials flow into `ModuleContext.variables`? Options: (a) special variable resolution in `variable_assembler.py`, (b) host-scoped variables injected by the stack deployment service, (c) a new `ssh_context` field on ModuleContext.

4. **Blueprint per topology:** Should there be one blueprint per topology (BF3, BF3-IPMI, BMC, Regular) or one blueprint with conditional modules? The existing blueprint system doesn't have conditional logic — modules are required or optional, not conditional on discovered hardware state.

5. **Module variables vs host model:** SSH modules need `host_ip`, `mst_device`, `nic_mode` etc. Should these come from the host model (auto-injected) or from user-supplied variables (manual input)? Auto-inject from discovery is more ergonomic but requires a new variable source type.

6. **Destroy semantics for hardware:** What does "destroy" mean for `flash_dpu`? Reset DPU to factory? Do nothing? The existing engine contract expects `destroy()` to undo `apply()`. For hardware operations this is either meaningless or dangerous.

---

## Recommendation

**Path B+ (SSHEngine for Phase 1-3, existing K8s modules for Phase 4)** offers the best balance:

- Blueprint composability from day one
- Reuses ALL existing Phase 4 modules (FLO, CNEInstance, etc.) without rewriting
- Reuses ALL existing UI (stack deployment, module grid, progress, logs, variables)
- SSHEngine is bounded and testable (~350 lines)
- If it fails, fallback to Path A costs ~2 sessions and SSH command logic fully transfers
- Phase 4-7 of the existing bare-metal spec maps directly: Phase 1-3 become SSH modules, Phase 4 becomes "use existing K8s modules after cluster registration"

The 2-session overhead vs Path A buys: blueprint support, unified UI, K8s module reuse, and user customization through blueprint forking. The risk is manageable because the SSHEngine is the only new infrastructure — everything else already exists.

---

## Appendix: File References

### Existing Engine Infrastructure
- `services/execution/engine_interface.py` — `DeploymentEngine` ABC, `ModuleContext`, `OperationResult`, `PlanResult`
- `services/execution/engine_router.py` — Engine selection logic
- `services/execution/task_dispatch.py` — Routes module ops to Celery tasks
- `services/execution/ansible_engine.py` — Ansible engine (closest precedent for SSH-based execution)
- `services/execution/ansible_runner.py` — Governed playbook runner contract

### Existing Module System
- `modules/base.py` — `BaseModule`, `ManifestModule`, `HelmModule`, `InputSpec`, `OutputSpec`
- `modules/__init__.py` — `MODULE_REGISTRY` with 19 registered Python modules
- `modules/bnk/flo.py` — FLO HelmModule (would be reused directly in Phase 4)
- `modules/bnk/cneinstance.py` — CNEInstance ManifestModule (would be reused directly in Phase 4)

### Existing Blueprint System
- `models/stack.py` — `StackTemplate`, `StackInstance`
- `data/stack_templates.json` — Blueprint definitions (6 templates today)
- `services/stack_service.py` — Stack CRUD + deployment trigger
- `services/stack_deployment_service.py` — Creates ProjectModules from template, orchestrates deployment

### Bare-Metal Infrastructure (Built)
- `models/bare_metal.py` — `BareMetalHost`, `BareMetalDeployment`, `DeploymentStep`
- `services/bare_metal/orchestrator.py` — Step registries, create_deployment, execute_deployment
- `services/bare_metal/discovery/__init__.py` — Discovery service + actionable assessment
- `services/bare_metal/ssh_session.py` — `SSHSession` with paramiko
- `services/ssh/paramiko_utils.py` — Shared SSH primitives (CON-001)
- `schemas/bare_metal.py` — All request/response schemas

### Specs & Documentation
- `docs/DPU_DEPLOY_SPEC.md` — Original architecture spec
- `docs/DPU_DEPLOY_ANALYSIS.md` — BlueField-3 analysis + NIC mode reference
- `docs/DPU_DEPLOY_IMPLEMENTATION_PLAN.md` — Task registry
- `docs/specs/BM-CONSOLIDATION-PRE-PHASE4.md` — Pre-Phase-4 consolidation spec (completed)
