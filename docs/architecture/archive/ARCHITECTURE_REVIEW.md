# BNK-Forge v2 — Architecture Review & Refactoring Plan

**Date:** February 2026
**Current Version:** 2.7.19

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [The Fundamental Question: Should This Be IaC?](#2-the-fundamental-question-should-this-be-iac)
3. [Architectural Recommendations](#3-architectural-recommendations)
4. [Code-Level Refactors](#4-code-level-refactors)
5. [Frontend Improvements](#5-frontend-improvements)
6. [Infrastructure & DevOps](#6-infrastructure--devops)
7. [Implementation Phases](#7-implementation-phases)
8. [Kubernetes SDK Ecosystem Analysis](#8-kubernetes-sdk-ecosystem-analysis)
9. [Three Deployment Personas](#9-three-deployment-personas)
10. [Day 2 Operations Gap Analysis](#10-day-2-operations-gap-analysis)
11. [The Hybrid Engine: Detailed Design](#11-the-hybrid-engine-detailed-design)
12. [Revised Implementation Roadmap](#12-revised-implementation-roadmap)

---

## 1. Executive Summary

BNK-Forge v2 is a well-engineered deployment platform for F5 BNK on Kubernetes. The core execution engine, dependency graph system, real-time WebSocket feedback, and parallel deployment orchestration are solid. The module catalog with `module.json` metadata is a smart abstraction.

However, a deep analysis reveals that **the most fundamental architectural decision — using OpenTofu/Terraform as the universal execution engine — creates significant unnecessary complexity for the BNK deployment use case.** The platform spends ~2,900 lines of Python wrapping OpenTofu subprocess calls, managing workspaces, and generating HCL configuration files — to ultimately execute what amounts to `kubectl apply` and `helm install` for 9 out of 14 modules.

This document covers three levels of improvement:
1. **Architectural** — What would we do differently if starting over?
2. **Structural** — What can we refactor within the current architecture?
3. **Tactical** — What bugs and inconsistencies should we fix now?

---

## 2. The Fundamental Question: Should This Be IaC?

### 2.1 What the BNK Modules Actually Do

We analyzed every Terraform resource created by the BNK/K8s modules:

| Resource Type | Count Across All BNK/K8s Modules | What It Actually Is |
|---|---|---|
| `kubernetes_manifest` | 13 | `kubectl apply -f <yaml>` |
| `helm_release` | 2 (cert-manager, flo) | `helm install` |
| `kubernetes_namespace` | 4 | `kubectl create namespace` |
| `kubernetes_secret` | 1 | `kubectl create secret` |
| `null_resource` + `local-exec` | 7 | `kubectl get` / `kubectl wait` |
| `time_sleep` | 7 | `sleep N` |
| `data.external` (bash scripts) | 2 | Shell script execution |
| **Actual cloud resources (EC2, VPC, etc.)** | **0** | — |

**The BNK layer creates zero cloud resources.** It is 100% Kubernetes-plane operations. The Terraform modules are ~74% YAML-expressed-as-HCL (`kubernetes_manifest` blocks), which is arguably harder to read and maintain than native YAML/Helm values.

### 2.2 The Cost of OpenTofu for Kubernetes Operations

The Python platform code dedicated to wrapping OpenTofu:

| Component | Lines | Purpose |
|---|---|---|
| `execution_engine.py` — HCL generation | ~530 | Writing tfvars, backend, encryption, provider configs |
| `execution_engine.py` — subprocess calls | ~390 | Running `tofu init/plan/apply/destroy/refresh` |
| `workspace_manager.py` (entire file) | ~640 | Managing `.terraform/` dirs, init versions, plan files, vars hashes |
| `provider_config.py` (entire file) | ~390 | Generating K8s/Helm provider HCL for multi-cloud auth |
| `opentofu_tasks.py` — subprocess orchestration | ~880 | Init-before-plan, plan-before-apply, saved plan validation |
| **Total OpenTofu ceremony** | **~2,830 lines** | |

Compare this to the actual deployment logic (variable assembly, dependency ordering, error handling): **~1,600 lines**.

**The ceremony-to-logic ratio is 1.8:1.** For every line of code that decides *what* to deploy, there are 1.8 lines managing *how OpenTofu works*.

### 2.3 Where OpenTofu IS the Right Choice

For the **infrastructure layer** (`infra/aws/vpc`, `infra/aws/eks`, etc.), OpenTofu is absolutely the correct tool:

- These modules create **real cloud resources** (VPCs, subnets, EKS clusters, IAM roles, security groups)
- **State management** is essential — you need to know what exists to modify or destroy it
- **Plan-before-apply** prevents costly mistakes (deleting a production VPC)
- **Dependency-aware destroy** is critical (delete subnets before VPC)
- **Drift detection** catches manual changes
- These are the operations that take 15-20 minutes and cost real money

### 2.4 The Hybrid Engine Architecture

If we were starting from scratch, the ideal architecture would be a **two-engine approach**:

```
┌─────────────────────────────────────────────────────────┐
│                    BNK-Forge Platform                     │
│                                                           │
│  ┌─────────────────────┐    ┌──────────────────────────┐ │
│  │  Infrastructure      │    │  Kubernetes              │ │
│  │  Engine              │    │  Engine                  │ │
│  │                      │    │                          │ │
│  │  OpenTofu/Terraform  │    │  Native K8s Client       │ │
│  │  ─ VPC, Subnets      │    │  ─ Helm SDK              │ │
│  │  ─ EKS, AKS, GKE     │    │  ─ kubectl apply         │ │
│  │  ─ IAM, Security     │    │  ─ K8s Python client     │ │
│  │  ─ Storage, DNS      │    │  ─ CRD management        │ │
│  │                      │    │                          │ │
│  │  WHY: State mgmt,    │    │  WHY: K8s IS the state,  │ │
│  │  plan preview,        │    │  no init/plan ceremony,  │ │
│  │  drift detection,     │    │  native readiness checks,│ │
│  │  cloud API wrappers   │    │  streaming output,       │ │
│  │                      │    │  sub-second operations    │ │
│  └──────────┬───────────┘    └────────────┬─────────────┘ │
│             │                             │               │
│             └──────────┬──────────────────┘               │
│                        │                                  │
│              ┌─────────▼──────────┐                       │
│              │  Unified            │                       │
│              │  Orchestrator       │                       │
│              │                     │                       │
│              │  ─ Dependency graph  │                       │
│              │  ─ Variable wiring   │                       │
│              │  ─ Parallel layers   │                       │
│              │  ─ Progress/status   │                       │
│              │  ─ Rollback/retry    │                       │
│              └─────────────────────┘                       │
└─────────────────────────────────────────────────────────┘
```

#### What a Kubernetes-Native Engine Would Look Like

For the 9 BNK/K8s modules, the execution would be:

```python
# Instead of: clone git repo → write tfvars → write backend.tf → write provider.tf 
#             → tofu init → tofu plan → tofu apply → parse outputs
#
# It would be:
class KubernetesEngine:
    def __init__(self, kubeconfig: str):
        self.k8s_client = kubernetes.client.ApiClient(config)
        self.helm_client = HelmClient(kubeconfig)
    
    async def apply_manifest(self, manifest: dict, namespace: str):
        """Replace 'kubernetes_manifest' Terraform resources."""
        api = kubernetes.client.CustomObjectsApi(self.k8s_client)
        return await api.create_namespaced_custom_object(...)
    
    async def install_helm_chart(self, release: str, chart: str, values: dict):
        """Replace 'helm_release' Terraform resources."""
        return await self.helm_client.install(release, chart, values=values)
    
    async def wait_for_ready(self, resource, timeout: int):
        """Replace 'time_sleep' + 'null_resource' kubectl wait."""
        w = kubernetes.watch.Watch()
        async for event in w.stream(api.list_..., timeout_seconds=timeout):
            if is_ready(event['object']):
                return True
    
    def get_outputs(self, resource) -> dict:
        """Replace 'tofu output -json' — just read from the cluster."""
        return self.k8s_client.read_namespaced_custom_object(...)
```

**Estimated code reduction:** ~2,400 lines of OpenTofu ceremony replaced by ~400 lines of direct Kubernetes API calls.

#### What We'd Gain

| Benefit | Description |
|---|---|
| **No init ceremony** | Kubernetes client connects instantly. No 30-60 second `tofu init` downloading providers. |
| **No plan ceremony** | For K8s, you can `kubectl diff` or just apply — the cluster is the state store. |
| **Streaming output** | Watch events in real-time instead of buffering entire subprocess output. |
| **Native readiness** | Replace `time_sleep` + `null_resource kubectl wait` with proper K8s watch streams. |
| **Sub-second feedback** | Creating a namespace takes <1 second. Currently takes 30+ seconds (init + plan + apply). |
| **No workspace management** | No `.terraform/` dirs, no `.bnk_init_version`, no `vars_hash` — the cluster IS the state. |
| **No state file encryption** | No encryption.tf needed — K8s secrets handle this natively. |
| **No provider config generation** | No `bnk_forge_providers.tf` — authentication is a one-time kubeconfig setup. |
| **Better error messages** | Kubernetes API errors are structured JSON, not subprocess stderr parsing. |

#### What We'd Lose (And How to Compensate)

| Loss | Compensation |
|---|---|
| `tofu plan` preview | `kubectl diff` + `helm diff` plugin — actually better for CRDs |
| Unified state file | Kubernetes IS the state — query the cluster directly |
| Automatic rollback | Implement compensating transactions (delete what was created on failure) |
| Output wiring between modules | Still needed — but pass Python dicts directly instead of tfvars files |
| Drift detection | `kubectl get` vs desired state comparison — simpler than `tofu plan -detailed-exitcode` |

### 2.5 Migration Strategy: Don't Rewrite — Dual-Engine

The critical insight is that **we don't need to throw away OpenTofu** for the infrastructure layer. The migration path is:

1. **Phase 1:** Extract a `KubernetesEngine` alongside the existing `ExecutionEngine`
2. **Phase 2:** Tag modules with `execution_engine: "kubernetes"` vs `"opentofu"` in `module.json`
3. **Phase 3:** Route execution through the appropriate engine based on module metadata
4. **Phase 4:** Convert BNK/K8s Terraform modules to Python-native Kubernetes manifests
5. **Phase 5:** Both engines share the same dependency graph, variable wiring, and parallel execution orchestrator

The unified orchestrator (dependency graph, variable assembly, parallel execution) stays exactly as-is. Only the execution backend changes.

### 2.6 The Alternative: Pure Helm/Kustomize Approach

An even more radical option: package the entire BNK deployment as a **single meta Helm chart** with subcharts:

```
bnk-deployment/
  Chart.yaml
  values.yaml          # Single file with ALL user inputs
  charts/
    far-setup/
    cert-manager/
    flo/
    bnk-gatewayclass/
    gateway/
    routes/
  templates/
    _helpers.tpl       # Variable derivation logic
```

**Pros:** One `helm install` deploys everything. Helm handles dependency ordering within a release. Values files replace the 7-layer variable assembly. Rollback is `helm rollback`. No custom platform needed for the BNK layer.

**Cons:** Loses the per-module visibility and control that makes BNK-Forge valuable. Can't inspect/modify individual modules. Helm's dependency ordering is less sophisticated than BNK-Forge's layer-based parallelism. Doesn't help with the infrastructure layer.

**Verdict:** The meta-Helm-chart approach works for production deployments where you "set and forget." BNK-Forge's value is in the **development/debug/iterate** workflow where per-module control matters. The hybrid engine approach is the better path.

### 2.7 The "Ridiculously Easy" Target State

For the goal of making F5 BNK deployment ridiculously easy on any cloud or on-prem, the ideal experience would be:

```
Step 1: Choose your target
         ┌──────────────────────────┐
         │  Where is your cluster?   │
         │                           │
         │  ○ AWS (I need a cluster) │  → infra/aws stack (OpenTofu)
         │  ○ Azure (need a cluster) │  → infra/azure stack (OpenTofu)
         │  ○ GCP (need a cluster)   │  → infra/gcp stack (OpenTofu)
         │  ● I have a cluster       │  → skip infra, go to BNK
         │  ○ On-prem / bare metal   │  → kubeconfig upload
         └──────────────────────────┘

Step 2: Provide credentials (2-3 inputs)
         - F5 license JWT
         - F5 FAR service account key
         - (Cloud creds if building infra)

Step 3: Choose deployment size
         ┌──────────────────────────┐
         │  Deployment Profile       │
         │                           │
         │  ○ Dev (2 nodes, 4GB)     │
         │  ● Standard (3 nodes, 8GB)│
         │  ○ Production (5 nodes,   │
         │    16GB, HA, DPU)         │
         └──────────────────────────┘

Step 4: Deploy (one click)
         → Platform resolves all dependencies
         → Auto-generates all derived variables (CIDRs, names, tags)
         → Runs infra (if needed) via OpenTofu
         → Runs BNK stack via Kubernetes-native engine
         → Real-time streaming progress for each component
         → ~20 min for greenfield, ~10 min for existing cluster
```

**What's needed to get there:**

| Gap | Current State | Target State |
|---|---|---|
| Cloud support | AWS only (5 infra modules) | AWS + Azure + GCP + on-prem |
| Credential flow | 4-tier chain, AWS-specific | Cloud-agnostic credential interface |
| Variable complexity | 6 CIDR blocks, repeated names, manual wiring | Auto-derived from project name + cloud + size profile |
| Execution speed (BNK layer) | 30-60s per module (init/plan/apply) | 1-5s per module (native K8s API) |
| User inputs for BNK stack | ~15 variables across 7 modules | 3 inputs (JWT, FAR key, size) + auto-derive the rest |
| Streaming output | Buffered (nothing until done) | Line-by-line real-time via WebSocket |
| Rollback on failure | Manual per-module destroy | Automatic compensating transactions |
| Module execution | Serial within stack | True parallel (where dependencies allow) |

---

## 3. Architectural Recommendations

### 3.1 Decompose the Execution Engine (God Object)

**Current:** `execution_engine.py` is 1,709 lines doing everything — variable assembly, workspace management, git cloning, HCL generation, subprocess execution, output parsing, error analysis.

**Proposed decomposition:**

```
services/
  execution/
    __init__.py
    variable_assembler.py     # 7-layer variable precedence chain (~200 lines)
    workspace_preparer.py     # Git clone, config file generation (~300 lines)
    tofu_runner.py            # subprocess.run calls with timeout/streaming (~250 lines)
    output_collector.py       # Output capture and parsing (~100 lines)
    provider_injector.py      # Multi-cloud provider config generation (~400 lines)
    error_analyzer.py         # Destroy error parsing with actionable guidance (~100 lines)
    kubernetes_runner.py      # NEW: Native K8s API execution (~400 lines)
    engine_factory.py         # Routes to tofu_runner or kubernetes_runner (~50 lines)
```

### 3.2 Fix Stack Template Dependency Serialization

**Current:** `stack_deployment_service.py` line 140-147 makes every module depend on ALL previous modules, creating a fully serial chain that defeats the parallel execution engine.

**Fix:** Use the template's actual dependency declarations from `module.json`:

```python
# BEFORE (forces serial):
for idx, module in enumerate(created_modules):
    if idx > 0:
        module.dependencies = [m.id for m in created_modules[:idx]]

# AFTER (respects actual dependencies):
for module in created_modules:
    lib_module = module_lookup[module.path_in_project]
    if lib_module.dependencies_metadata:
        required_deps = lib_module.dependencies_metadata.get('required', [])
        module.dependencies = [
            m.id for m in created_modules
            if m.path_in_project in [d['module'] for d in required_deps]
        ]
```

This would enable layers like:
- Layer 1: `far-setup`, `network-setup` (parallel — independent)
- Layer 2: `cert-manager` (depends on far-setup)
- Layer 3: `flo` (depends on far-setup, cert-manager)
- Layer 4: `bnk-gatewayclass` (depends on flo)

Instead of the current:
- Layer 1: `far-setup`
- Layer 2: `network-setup` (waits for far-setup unnecessarily)
- Layer 3: `cert-manager` (waits for network-setup unnecessarily)
- ...7 serial layers

### 3.3 Deployment Profiles (Size-Based Defaults)

Add a `deployment_profile` concept that collapses dozens of variables into a single choice:

```json
{
  "profiles": {
    "dev": {
      "description": "Development — minimal resources",
      "infra": {
        "instance_type": "m5.xlarge",
        "node_count": 2,
        "kubernetes_version": "1.30",
        "vpc_cidr": "10.0.0.0/16"
      },
      "bnk": {
        "deployment_size": "Small",
        "tmm_cpu": "2",
        "tmm_memory": "4Gi",
        "tmm_hugepages": "2Gi",
        "ha_enabled": false
      }
    },
    "standard": {
      "description": "Standard — balanced for most workloads",
      "infra": {
        "instance_type": "c5n.2xlarge",
        "node_count": 3,
        "kubernetes_version": "1.30",
        "vpc_cidr": "10.0.0.0/16"
      },
      "bnk": {
        "deployment_size": "Medium",
        "tmm_cpu": "4",
        "tmm_memory": "8Gi",
        "tmm_hugepages": "4Gi",
        "ha_enabled": true
      }
    },
    "production": {
      "description": "Production — high performance, HA, DPU nodes",
      "infra": {
        "instance_type": "c5n.4xlarge",
        "node_count": 5,
        "kubernetes_version": "1.30",
        "enable_high_performance_nodes": true,
        "vpc_cidr": "10.0.0.0/16"
      },
      "bnk": {
        "deployment_size": "Large",
        "tmm_cpu": "8",
        "tmm_memory": "16Gi",
        "tmm_hugepages": "8Gi",
        "ha_enabled": true,
        "dpdk_enabled": true
      }
    }
  }
}
```

### 3.4 Auto-Derive CIDR Blocks

The VPC module currently requires 6 individual CIDR blocks. This should be auto-calculated:

```python
def auto_slice_vpc_cidr(vpc_cidr: str = "10.0.0.0/16", az_count: int = 2) -> dict:
    """From one VPC CIDR, derive all 5 subnet CIDRs."""
    network = ipaddress.ip_network(vpc_cidr)
    # Split /16 into /20 subnets (16 available)
    subnets = list(network.subnets(new_prefix=20))
    return {
        "vpc_cidr": vpc_cidr,
        "public_subnet_cidr": str(subnets[0]),       # 10.0.0.0/20
        "private_external_subnet_cidrs": [
            str(subnets[1]), str(subnets[2])           # per AZ
        ],
        "private_internal_subnet_cidrs": [
            str(subnets[3]), str(subnets[4])           # per AZ
        ],
        "pod_subnet_cidr": str(subnets[5]),            # 10.0.80.0/20
    }
```

### 3.5 Multi-Cloud Credential Abstraction

**Current:** `credentials_service.py` is entirely AWS-specific. No Azure Service Principal, GCP Service Account, or on-prem kubeconfig support.

**Proposed:**

```python
class CloudCredentialProvider(ABC):
    @abstractmethod
    def get_env_vars(self) -> Dict[str, str]: ...
    
    @abstractmethod
    def get_kubeconfig(self) -> Optional[str]: ...
    
    @abstractmethod
    def validate(self) -> bool: ...

class AWSCredentialProvider(CloudCredentialProvider):
    """Existing logic: access keys, profile, SSO."""
    ...

class AzureCredentialProvider(CloudCredentialProvider):
    """ARM_CLIENT_ID, ARM_CLIENT_SECRET, ARM_TENANT_ID, ARM_SUBSCRIPTION_ID."""
    ...

class GCPCredentialProvider(CloudCredentialProvider):
    """GOOGLE_APPLICATION_CREDENTIALS (service account JSON)."""
    ...

class OnPremCredentialProvider(CloudCredentialProvider):
    """User-provided kubeconfig file."""
    ...

def get_provider(project: Project) -> CloudCredentialProvider:
    providers = {
        "aws": AWSCredentialProvider,
        "azure": AzureCredentialProvider,
        "gcp": GCPCredentialProvider,
        "on-prem": OnPremCredentialProvider,
    }
    return providers[project.cloud_provider](project)
```

### 3.6 Streaming Subprocess Output

**Current:** `subprocess.run(capture_output=True)` buffers everything. A 20-minute EKS deploy shows nothing until completion.

**Proposed:**

```python
async def run_tofu_streaming(cmd: list, env: dict, task_id: str):
    """Stream output line-by-line through WebSocket."""
    process = await asyncio.create_subprocess_exec(
        *cmd, env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    async for line in process.stdout:
        decoded = line.decode().strip()
        # Publish each line to WebSocket in real-time
        await publish_task_output(task_id, decoded)
        output_buffer.append(decoded)
    
    await process.wait()
    return ProcessResult(
        returncode=process.returncode,
        stdout='\n'.join(output_buffer),
        stderr=await process.stderr.read(),
    )
```

### 3.7 Unified Dependency Resolution

**Current:** Two independent topological sort implementations:
- `deployment_wizard_service.py` lines 265-398 (catalog-level, operates on `ModuleLibrary` names)
- `dependency_graph_service.py` lines 30-118 (instance-level, operates on `ProjectModule` IDs)

**Proposed:** One `DependencyGraph` class that works at both levels:

```python
class DependencyGraph:
    """Single dependency resolution engine for both catalog and instance levels."""
    
    def resolve_from_catalog(self, module_paths: List[str]) -> List[List[str]]:
        """Given desired module paths, resolve full dependency tree and return layers."""
        ...
    
    def resolve_from_project(self, project_id: int) -> List[List[int]]:
        """Given a project, build execution layers from instantiated modules."""
        ...
    
    def detect_cycles(self, edges: Dict) -> Optional[List]:
        """Shared cycle detection for both levels."""
        ...
    
    def topological_sort(self, nodes: Set, edges: Dict) -> List[List]:
        """Shared Kahn's algorithm for both levels."""
        ...
```

### 3.8 Stack Preview / Dry-Run

**Current:** `deploy_stack()` immediately creates `ProjectModule` records. No way to preview.

**Add:** A `preview_stack()` method that returns what WOULD happen without creating records:

```python
def preview_stack(self, template, project, user_variables) -> StackPreview:
    """Preview stack deployment without creating any records."""
    return StackPreview(
        modules=[...],           # What modules would be created
        execution_layers=[...],  # Parallel execution plan
        resolved_variables={...},# All variables after resolution
        missing_inputs=[...],    # What the user still needs to provide
        estimated_time_minutes=N,
        estimated_cost=None,     # If Infracost data available
    )
```

### 3.9 Automated Rollback on Stack Failure

**Current:** If module 3 of 7 fails during stack deploy, modules 1-2 are left in "applied" state. Manual cleanup required.

**Proposed:** Offer three options when a stack module fails:

```
Stack Deploy Failed: cert-manager failed to install
  
  [Continue] Skip failed module, deploy remaining
  [Rollback] Destroy far-setup, network-setup (reverse order)
  [Pause]    Leave everything as-is for debugging
```

The destroy infrastructure for rollback already exists in `orchestrate_stack_destroy`.

---

## 4. Code-Level Refactors

### 4.1 Critical Bugs to Fix

#### Bug: `validate_module_for_operation()` Dead Code
**File:** `routes/project_modules.py` lines 236-240
**Issue:** The function attempts `db.query(PM).filter(PM.id == dep_id).first() if hasattr(locals(), 'db') else None` but `db` is never in scope — it's not a parameter. The dependency check is dead code.
**Fix:** Pass `db: Session` as a parameter.

#### Bug: `is_active` Semantic Collision  
**File:** `services/project_service.py` line 204
**Issue:** `project.is_active = project.deployed_count > 0` overwrites the user's explicit "active project" toggle. `activate_project()` in `projects.py` sets `is_active = True`, but the next `update_project_counts` resets it.
**Fix:** Separate into `is_active` (user toggle) and `has_deployments` (computed).

#### Bug: Auto-Wire Non-Determinism
**File:** `execution_engine.py` lines 556-562
**Issue:** If two modules produce outputs with the same name, the first one found wins. Query order is not guaranteed.
**Fix:** Add explicit priority (prefer declared dependencies over auto-wire) and log warnings on conflicts.

#### Bug: Version Inconsistency
**Files:** `routes/api.py` line 49 reports `"1.0.0-mvp"`, while `core/config.py` says `"2.6.2"`, and `VERSION` file says `"2.7.18"`.
**Fix:** Read from the `VERSION` file at startup, single source of truth.

### 4.2 Standardize Error Handling

**Current state:** Three conflicting patterns:

| Pattern | Where | Format |
|---|---|---|
| `raise NotFoundError(...)` from `core.errors` | `routes/api.py` | `{"error": {"code": "NOT_FOUND", "message": "..."}}` |
| `raise HTTPException(status_code=404)` | `routes/project_modules.py`, `routes/kubernetes.py` | `{"detail": "..."}` |
| `return JSONResponse(status_code=500, content={"error": str(e)})` | `routes/projects.py` (~20 instances) | `{"error": "raw exception string"}` |

**Fix:** Migrate everything to `core.errors`:

```python
# BEFORE (routes/projects.py):
except Exception as e:
    return JSONResponse(status_code=500, content={"error": str(e)})

# AFTER:
except Exception as e:
    raise InternalError(f"Failed to create project: {e}")
```

**Scope:** ~20 `JSONResponse` instances in `projects.py`, ~100 `HTTPException` instances in `kubernetes.py`, scattered across other routes.

### 4.3 Split `models.py` (1,198 lines, 22 classes)

```
models/
  __init__.py              # Re-exports all models
  base.py                  # Base, mixins, common columns
  project.py               # Project, ProjectModule
  credentials.py           # CloudCredentialTemplate, ProjectSecret
  deployment.py            # Deployment, Task, AuditLog
  module.py                # ModuleLibrary, ModuleSource, VariableMapping
  kubernetes.py            # KubernetesCluster, HelmRelease
  stack.py                 # StackTemplate, StackInstance
  execution.py             # ParallelExecution
  drift.py                 # DriftCheck, DriftSettings
  system.py                # ApplicationSetting, Notification
```

### 4.4 Centralize Pydantic Schemas

**Current:** `routes/project_modules.py` defines 8 inline Pydantic models (lines 44-155). `schemas/models.py` has only 4 models. Every route file has its own inline definitions.

**Fix:** Move all schemas to `schemas/` organized by domain:

```
schemas/
  __init__.py
  project.py               # ProjectCreate, ProjectUpdate, ProjectResponse
  module.py                 # ModuleCreate, ModuleUpdate, ModuleResponse, ...
  deployment.py             # DeploymentResponse, TaskResponse
  stack.py                  # StackDeployRequest, StackPreview
  kubernetes.py             # ClusterResponse, ResourceResponse
  common.py                 # PaginatedResponse, ErrorResponse
```

### 4.5 Extract `get_or_404()` Pattern

**Current:** `project = db.query(Project).filter(Project.id == project_id).first(); if not project: raise HTTPException(404)` is repeated dozens of times.

**Fix:** The `get_or_404()` helper already exists in `core/errors.py` line 202 — just use it:

```python
# BEFORE (repeated everywhere):
project = db.query(Project).filter(Project.id == project_id).first()
if not project:
    raise HTTPException(status_code=404, detail="Project not found")

# AFTER:
project = get_or_404(db, Project, project_id, "Project")
```

### 4.6 Celery Task Session Management

**Current:** Every task creates `db = SessionLocal()` with manual try/except/finally/close. Repeated in every task function.

**Fix:** Context manager or base task class:

```python
@contextmanager
def task_session():
    """Managed database session for Celery tasks."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# Usage:
@celery_app.task
def run_opentofu_apply(module_id: int, ...):
    with task_session() as db:
        module = get_or_404(db, ProjectModule, module_id, "Module")
        engine = ExecutionEngine(db)
        ...
```

### 4.7 Break Up Giant Route Handlers

| Handler | File | Lines | Should Become |
|---|---|---|---|
| `set_aws_auth_method()` | `routes/api.py` | 280 lines (437-717) | `services/aws_auth_service.py` |
| `get_project_variables()` | `routes/projects.py` | 191 lines (725-916) | `services/variable_discovery_service.py` |
| `run_stack_deployment()` | `routes/stacks.py` | 148 lines (500-648) | Already has `stack_deployment_service.py` — move the orchestration there |

### 4.8 Replace `print()` with Logger

**Files:** `main.py`, `celery_app.py`

```python
# BEFORE:
print(f"  Database initialized successfully")

# AFTER:
logger.info("Database initialized successfully")
```

### 4.9 Fix Configuration Disconnect

**Current:** `core/config.py` has a Pydantic `Settings` class. `database.py` reads `DATABASE_URL` from `os.getenv()` directly, bypassing Settings.

**Fix:** All config reads should go through `Settings`:

```python
# database.py
from core.config import settings
engine = create_async_engine(settings.DATABASE_URL)
```

### 4.10 Remove Dead/Backup Files

```
rm backend/models.py.bak
rm backend/routes/projects.py.backup
rm frontend-v2/src/components/helm/HelmPackagesV2.tsx.bak
rm frontend-v2/src/components/helm/HelmPackages.tsx.backup
rm bnk-forge-v2backendentrypoint.sh
rm configs/test_integration.db
```

---

## 5. Frontend Improvements

### 5.1 Consolidate Duplicate Systems

| Duplication | Files | Fix |
|---|---|---|
| Two keyboard shortcut hooks | `useKeyboardShortcuts.ts`, `useKeyboardShortcut.ts` | Merge into one with input-awareness |
| Two status color systems | `status-colors.ts`, `ui-constants.ts:60-80` | Keep `status-colors.ts`, remove duplicate from `ui-constants.ts` |
| Two notification methods | `notify.*()` vs `toast.*()` | All hooks should use `notify.*()` only |
| `useSettings.ts` uses `fetch()` | `useSettings.ts:55-77` | Switch to `apiClient` (Axios) |

### 5.2 Standardize Query Keys

**Current:** `queryKeys.ts` provides a centralized factory, but most hooks define ad-hoc keys.

**Fix:** Migrate all hooks to use `queryKeys.*`:

```typescript
// BEFORE (useK8s.ts):
queryKey: ['k8s', 'clusters', projectId]

// AFTER:
queryKey: queryKeys.k8s.clusters(projectId)
```

Hooks to update: `useK8s`, `useHelm`, `useDrift`, `useCost`, `useStacks`, `useModuleSources`, `useProjectSecrets`.

### 5.3 Split Monolithic Files

| File | Lines | Split Into |
|---|---|---|
| `api.ts` | 1,104 | `api/projects.ts`, `api/modules.ts`, `api/k8s.ts`, `api/helm.ts`, `api/stacks.ts`, `api/index.ts` (re-exports) |
| `types/index.ts` | 1,402 | `types/project.ts`, `types/module.ts`, `types/k8s.ts`, `types/helm.ts`, etc. |

### 5.4 Add Missing Safety Nets

```typescript
// router.tsx — add 404 catch-all:
{ path: "*", element: <NotFoundPage /> }

// App.tsx — add error boundary:
<ErrorBoundary fallback={<ErrorFallback />}>
  <RouterProvider router={router} />
</ErrorBoundary>
```

Fix navigation shortcuts referencing non-existent routes (`/deployments`, `/settings` in `useKeyboardShortcuts.ts`).

### 5.5 Remove Hardcoded User

`useNotifications.ts` and `NotificationProvider.tsx` hardcode `user: 'admin'`. Should be dynamic from auth store (even if auth isn't fully implemented yet).

---

## 6. Infrastructure & DevOps

### 6.1 Security Fixes (Critical)

| Issue | File | Fix |
|---|---|---|
| Hardcoded DB credentials (6 copies) | `docker-compose.yml` | Use `${POSTGRES_PASSWORD:-default}` pattern + `.env` |
| Redis password in healthcheck args | `docker-compose.yml:42` | Use `REDISCLI_AUTH` env var instead |
| Docker socket `chmod 666` | `fresh-start.sh:97-104` | Use Docker group membership |
| Docker socket mounted into backend | `docker-compose.yml:95` | Add `tecnativa/docker-socket-proxy` service |
| `ALLOWED_ORIGINS=*` default | `docker-compose.yml:66` | Default to specific origins |
| Backend port 2651 exposed directly | `docker-compose.yml:107` | Remove — all traffic through proxy |

### 6.2 DRY Docker Compose

```yaml
# Use YAML anchors:
x-common-env: &common-env
  DATABASE_URL: postgresql://bnkforge:${POSTGRES_PASSWORD:-bnkforge_dev_password}@postgres:5432/bnkforge
  REDIS_URL: redis://:${REDIS_PASSWORD:-bnkforge_redis_dev}@redis:6379/0
  CELERY_BROKER_URL: redis://:${REDIS_PASSWORD:-bnkforge_redis_dev}@redis:6379/1
  CELERY_RESULT_BACKEND: redis://:${REDIS_PASSWORD:-bnkforge_redis_dev}@redis:6379/2

x-common-volumes: &common-volumes
  - module_catalog:/app/configs/.module_catalog
  - workspace_data:/app/workspaces
  - bnk-forge-data:/app/data
  - bnk-forge-keys:/app/keys
  - state_data:/app/state_data
  - ./secrets:/app/secrets:ro

services:
  backend:
    environment:
      <<: *common-env
    volumes:
      <<: *common-volumes
  celery-worker:
    environment:
      <<: *common-env
    volumes:
      <<: *common-volumes
```

### 6.3 Add `update.sh` Migrations

```bash
# update.sh — add after container restart:
echo "Running database migrations..."
docker exec bnk-forge-backend alembic upgrade head
```

### 6.4 Remove Redundant Frontend Proxy Config

Delete `/api/` and `/ws/` location blocks from `frontend-v2/nginx.conf`. All API routing should go through the proxy service only.

### 6.5 Add Log Rotation

```yaml
# docker-compose.yml — add to every service:
logging:
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"
```

### 6.6 Add CI/CD Pipeline

Minimum viable pipeline:

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]
jobs:
  lint-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install ruff
      - run: ruff check backend/
  
  lint-frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v2
      - run: pnpm install && pnpm lint
    working-directory: frontend-v2
  
  test-backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install -r backend/requirements.txt pytest
      - run: pytest backend/tests/
  
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker compose build
```

### 6.7 Version Propagation

```python
# backend/core/config.py — read from VERSION file at startup:
import pathlib

VERSION_FILE = pathlib.Path(__file__).parent.parent.parent / "VERSION"
APP_VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else "0.0.0"
```

```typescript
// frontend-v2/vite.config.ts — inject at build time:
define: {
  __APP_VERSION__: JSON.stringify(fs.readFileSync('../VERSION', 'utf-8').trim())
}
```

---

## 7. Implementation Phases

### Phase 1: Quick Wins & Bug Fixes (1-2 weeks)
_Low risk, high impact, no architecture changes_

- [ ] Fix `validate_module_for_operation()` dead code — pass `db` parameter
- [ ] Fix `is_active` semantic collision — add `has_deployments` column
- [ ] Fix version inconsistency — single `VERSION` file source of truth
- [ ] Standardize error handling — migrate `projects.py` to `core.errors`
- [ ] Remove dead/backup files
- [ ] Replace `print()` with logger
- [ ] Fix configuration disconnect (`database.py` → use `Settings`)
- [ ] Add 404 route to frontend router
- [ ] Fix `useSettings.ts` to use `apiClient`
- [ ] Merge duplicate keyboard shortcut hooks
- [ ] Add log rotation to docker-compose
- [ ] Fix `update.sh` missing migrations
- [ ] DRY docker-compose.yml with YAML anchors

### Phase 2: Structural Refactoring (2-4 weeks)
_Medium risk, improves maintainability and developer experience_

- [ ] Split `models.py` into domain modules
- [ ] Centralize Pydantic schemas in `schemas/`
- [ ] Extract `get_or_404()` pattern across all routes
- [ ] Celery task session context manager
- [ ] Break up giant route handlers into services
- [ ] Standardize query keys in all frontend hooks
- [ ] Consolidate notification methods
- [ ] Split `api.ts` and `types/index.ts` by domain
- [ ] Add React Error Boundary
- [ ] Security fixes in docker-compose.yml
- [ ] Add basic CI/CD pipeline
- [ ] Add business logic tests for core services

### Phase 3: Architectural Improvements (4-8 weeks)
_Higher risk, significant capability gains_

- [ ] Fix stack template dependency serialization (enable true parallelism)
- [ ] Decompose `ExecutionEngine` into focused services
- [ ] Implement streaming subprocess output via WebSocket
- [ ] Add deployment profiles (dev/standard/production)
- [ ] Add auto-CIDR derivation
- [ ] Add stack preview/dry-run endpoint
- [ ] Multi-cloud credential abstraction interface
- [ ] Unified dependency resolution (single graph service)
- [ ] Automated rollback on stack failure

### Phase 4: Dual-Engine Architecture (8-12 weeks)
_Highest impact, enables "ridiculously easy" target_

- [ ] Build `KubernetesEngine` with native K8s client
- [ ] Add `execution_engine` field to `module.json` metadata schema
- [ ] Route execution through appropriate engine via `engine_factory`
- [ ] Convert BNK/K8s Terraform modules to Python-native K8s manifests
- [ ] Build Azure infrastructure modules (`infra/azure/`)
- [ ] Build GCP infrastructure modules (`infra/gcp/`)
- [ ] On-prem kubeconfig-based flow
- [ ] Simplified "3-input" BNK deployment wizard
- [ ] Real-time K8s watch streams for progress

---

## Appendix A: Module Dependency Graph

```
LAYER 1: INFRASTRUCTURE (Cloud-Specific — OpenTofu)
  vpc ──→ security ──→ eks ──→ storage
                         └──→ high-performance-nodes

LAYER 2: KUBERNETES PREREQUISITES (Cloud-Agnostic)
  far-setup ──→ cert-manager
  network-setup (independent)

LAYER 3: BNK PLATFORM
  flo (depends on: far-setup, cert-manager)

LAYER 4: BNK GATEWAY
  bnk-gatewayclass ──→ gateway ──→ routes

LAYER 5: BNK POLICY (independent of gateway chain)
  bnk-secpolicy (depends on: flo)
  bnk-netpolicy (depends on: flo)
```

## Appendix B: Variable Flow (Simplified)

```
User provides:       project_name, environment, cloud_region
                     f5_jwt_token, f5_far_key, deployment_size

Auto-derived:        vpc_cidr → 5 subnet CIDRs
                     cluster_name = {project_name}-cluster
                     common_tags = {project: ..., environment: ...}
                     node_count, instance_type (from size profile)
                     tmm_cpu, tmm_memory, hugepages (from size profile)
                     all namespace names (f5-spk, cert-manager)

Module-wired:        vpc_id → security, eks
                     subnet_ids → eks, high-perf-nodes
                     cluster_name → all k8s/bnk modules
                     role_arns → eks
                     far_secret_name → cert-manager, flo
                     flo_ready → gatewayclass, gateway, policies
                     gatewayclass_name → gateway
                     gateway_name → routes
```

## Appendix C: Current vs Target OpenTofu Ceremony Reduction

| Metric | Current | After Phase 3 | After Phase 4 |
|---|---|---|---|
| Lines of OpenTofu ceremony (Python) | ~2,830 | ~2,200 (streaming) | ~800 (infra only) |
| Lines of K8s execution code | 0 | 0 | ~400 |
| BNK module deploy time (7 modules) | ~15 min | ~12 min (parallel) | ~3 min (native API) |
| `tofu init` calls per BNK deploy | 7 | 7 | 0 |
| Workspace dirs managed | 14 per project | 14 | 5 (infra only) |

---

## 8. Kubernetes SDK Ecosystem Analysis

Before building a Kubernetes-native engine, we surveyed the entire ecosystem to avoid reinventing the wheel.

### 8.1 What We Evaluated

| Tool | Type | Stars | Maintained | CRDs | Helm | Async | Embeddable |
|---|---|---|---|---|---|---|---|
| **kubernetes-client/python** | Official K8s client | 7.5k | Yes (v35, Jan 2026) | Yes (CustomObjectsApi) | No | No | Yes |
| **kr8s** | Modern K8s client | ~900 | Yes (v0.20, Jan 2026) | Yes (custom classes) | No | **Yes** (native asyncio) | Yes |
| **pykube-ng** | Lightweight K8s client | ~400 | Yes | Yes | No | No | Yes |
| **kopf** | Operator framework | 2.5k | Yes (v1.43, Feb 2026) | Yes (core use case) | No | Yes | Partial (operator pattern) |
| **Pulumi Python SDK** | Full IaC platform | 22k+ | Yes | Yes | **Yes** | Via Automation API | Yes |
| **CDK8s** | Manifest generator | 4.5k | Yes | Yes (import CRDs) | Partial | N/A | Partial (generates YAML only) |
| **CDKTF** | CDK for Terraform | 4.8k | **SUNSET Dec 2025** | — | — | — | **Dead** |
| **Argo CD** | GitOps server | 22k | Yes | Yes | Yes | N/A | No (standalone server) |
| **Flux CD** | GitOps controllers | 7.9k | Yes | Yes | Yes | N/A | No (K8s controllers) |
| **Helmfile** | Helm orchestrator | 5k | Yes | No | Yes | No | No (Go CLI) |
| **pyhelm** | Python Helm bindings | ~100 | **Abandoned** | No | Helm v2 only | No | **Dead** |
| **Crossplane** | K8s-based control plane | 9.5k+ | Yes | Yes | Via provider | N/A | No (K8s operator) |

### 8.2 Why Most Options Don't Fit

| Tool | Why Not |
|---|---|
| **kubernetes-client/python** | Too verbose for CRDs (untyped dicts, no convenience methods). No Helm. No async. Would need ~800 lines of wrapper code. |
| **pykube-ng** | kr8s is the modern evolution of the same idea, with async and better DX. |
| **kopf** | Wrong paradigm entirely. Designed for long-running reconciliation loops (operator pattern), not imperative "deploy now" Celery tasks. Would require rearchitecting from request/response to controller pattern. |
| **CDK8s** | Only generates YAML — doesn't apply it. You still need another tool to actually deploy. Adds complexity without solving the problem. |
| **CDKTF** | Dead. Sunset December 2025. |
| **Argo CD / Flux CD** | Require a full GitOps architecture change. Your backend would need to commit manifests to Git, then the GitOps tool syncs them. Adds latency, complexity, and a completely different mental model. Excellent tools — just not for this use case. |
| **Helmfile** | Go CLI binary. We'd be back to subprocess calls, which is what we're trying to get away from. |
| **Crossplane** | Massive infrastructure requirement (management cluster + Crossplane controllers + Compositions). Overkill for deploying a known set of BNK modules. Makes sense at much larger scale. |

### 8.3 The Two Viable Options

#### Option A: kr8s + subprocess helm (Recommended)

**kr8s** is a modern, human-written (not auto-generated) Kubernetes Python client with first-class asyncio support. It was built because the official client is too verbose and too low-level for application code.

```python
import kr8s

async def deploy_bnk_gateway(kubeconfig: str, config: dict):
    api = kr8s.asyncio.Api(kubeconfig=kubeconfig)
    
    # Create namespace (replaces kubernetes_namespace Terraform resource)
    ns = await api.get("namespaces", "f5-bnk", allow_empty=True)
    if not ns:
        await kr8s.asyncio.Namespace({"metadata": {"name": "f5-bnk"}}).create()
    
    # Apply CRD instance (replaces kubernetes_manifest Terraform resource)
    gateway = {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "Gateway",
        "metadata": {"name": config["gateway_name"], "namespace": "f5-bnk"},
        "spec": {
            "gatewayClassName": config["gatewayclass_name"],
            "listeners": config["listeners"],
        }
    }
    await api.apply(gateway, force=True)  # Server-side apply
    
    # Wait for readiness (replaces time_sleep + null_resource kubectl wait)
    gw = await api.get("gateways", config["gateway_name"], namespace="f5-bnk")
    await gw.wait("condition=Programmed", timeout=300)
    
    # Return outputs (replaces tofu output -json)
    return {
        "gateway_name": gw.metadata.name,
        "gateway_addresses": [a["value"] for a in gw.status.get("addresses", [])],
    }
```

For Helm charts (cert-manager, FLO), use subprocess:

```python
async def install_helm_chart(release: str, chart: str, values: dict, kubeconfig: str):
    values_file = write_temp_values(values)
    result = await asyncio.to_thread(
        subprocess.run,
        ["helm", "install", release, chart,
         "--kubeconfig", kubeconfig,
         "--namespace", namespace,
         "--values", values_file,
         "--wait", "--timeout", "300s"],
        capture_output=True, text=True
    )
    return result
```

**Why kr8s over the official client:**
- `api.apply()` does server-side apply — the official client has no SSA convenience method
- `obj.wait("condition=Ready")` — built in. Official client requires manual Watch loops.
- Native asyncio — perfect for FastAPI. Official client is sync-only.
- ~5 lines to apply a CRD vs ~25 with the official client
- Already used in production by NVIDIA, Dask community

**Why subprocess helm instead of a Helm library:**
- There is **no maintained Python Helm library** (pyhelm is dead, Helm v2 era)
- Helm is a single Go binary, subprocess is reliable and well-understood
- BNK-Forge already has a full `helm_service.py` (1,400 lines) that wraps `helm` CLI — we'd reuse this
- The Helm operations are only 2 out of ~20 K8s operations — not worth a complex integration

#### Option B: Pulumi Automation API

Pulumi offers an **Automation API** designed exactly for embedding IaC in application code:

```python
from pulumi import automation as auto
import pulumi_kubernetes as k8s

def bnk_program():
    """Pulumi program that deploys BNK."""
    ns = k8s.core.v1.Namespace("f5-bnk", metadata={"name": "f5-bnk"})
    
    cert_manager = k8s.helm.v4.Chart("cert-manager", 
        chart="cert-manager",
        namespace="cert-manager",
        values={...},
    )
    
    gateway = k8s.apiextensions.CustomResource("gateway",
        api_version="gateway.networking.k8s.io/v1",
        kind="Gateway",
        metadata={"name": "bnk-gateway", "namespace": "f5-bnk"},
        spec={...},
    )

# Programmatic deployment from FastAPI/Celery:
stack = auto.create_or_select_stack(
    stack_name="bnk-production",
    project_name="bnk-forge",
    program=bnk_program,
)
result = stack.up()  # Deploy
# result.outputs gives you all module outputs
```

**Pros over kr8s:**
- Single tool handles K8s resources AND Helm charts (no subprocess helm)
- Built-in state management and drift detection
- Plan/preview capability (`stack.preview()`)
- Automatic readiness waiting
- Can also handle infrastructure (replacing OpenTofu entirely)

**Cons:**
- Requires Pulumi engine binary distributed with your Docker image
- Needs a state backend (S3, local filesystem, or Pulumi Cloud)
- Heavier dependency footprint
- Slower than direct API calls (diff engine overhead)
- Learning curve for Pulumi concepts (stacks, resources, outputs, serialization)
- Commercial entity — Pulumi Cloud is paid (but self-hosted state is free)

### 8.4 Recommendation: kr8s + Helm CLI (Phase 1), Consider Pulumi (Phase 2)

**Start with kr8s + Helm subprocess** because:
1. Minimal new dependencies — kr8s is pure Python, Helm is already in the Docker image
2. Fastest execution — direct K8s API calls with no diff engine overhead
3. Native asyncio — integrates perfectly with FastAPI
4. We already have `helm_service.py` — reuse it
5. Simplest mental model — "apply this manifest, wait for it, read the result"
6. No state backend needed — Kubernetes IS the state

**Consider Pulumi later** if:
- We want to replace OpenTofu for infra too (single tool for everything)
- We need plan/preview for K8s changes
- We want cross-resource drift detection
- Customers need Pulumi Cloud integration

---

## 9. Three Deployment Personas

### 9.1 The Three Users

The entire architecture should be organized around **three deployment personas**, not around tools or module categories:

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  PERSONA 1: "BUILD IT ALL"                                       │
│  ──────────────────────────                                      │
│  "I have an AWS/Azure/GCP account. Give me everything."          │
│                                                                  │
│  Needs: Cloud infrastructure + Kubernetes + F5 BNK               │
│  Engine: OpenTofu (infra) → Kubernetes-native (BNK)              │
│  Time: ~45 minutes                                               │
│  Inputs: Cloud creds, F5 license, deployment size                │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  PERSONA 2: "I HAVE A CLUSTER"                                   │
│  ──────────────────────────────                                  │
│  "I have a K8s cluster. Install BNK on it."                      │
│                                                                  │
│  Needs: BNK prerequisites + F5 BNK                               │
│  Engine: Kubernetes-native only (no OpenTofu)                    │
│  Time: ~10 minutes                                               │
│  Inputs: Kubeconfig, F5 license, deployment size                 │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  PERSONA 3: "JUST BNK"                                           │
│  ──────────────────────────                                      │
│  "I have K8s with cert-manager, Multus, everything.              │
│   Just deploy the F5 BNK components."                            │
│                                                                  │
│  Needs: F5 BNK only (FLO, GatewayClass, Gateway, Routes)        │
│  Engine: Kubernetes-native only (no OpenTofu)                    │
│  Time: ~3 minutes                                                │
│  Inputs: Kubeconfig, F5 license                                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 9.2 Current vs Target Experience

#### Persona 1: "Build It All" (Currently Best Supported)

| Step | Current | Target |
|---|---|---|
| 1 | Go to Stacks → deploy "AWS K8s Foundation" | Single unified wizard: "Where is your cluster?" → "I need one" |
| 2 | Create new project, pick region, creds | Same, but with deployment profile (dev/std/prod) |
| 3 | Fill in 6 CIDR blocks manually | Auto-derived from one VPC CIDR |
| 4 | Deploy infra stack, wait | Same, but with streaming output |
| 5 | **Go back** to Stacks → deploy "F5 BNK 2.2" separately | **Automatic continuation** — infra completes, BNK starts |
| 6 | Choose "Existing Project", fill BNK inputs | BNK inputs auto-populated from infra outputs |
| 7 | **Go to K8s page** → "Detect EKS" to connect cluster | **Automatic** — cluster registered on EKS apply |
| 8 | Wait 15 min for BNK (7 serial modules) | Wait 5 min (parallel modules via native K8s API) |
| **Total steps** | **8 manual steps** | **3 manual steps** |
| **Total time** | **~45 min + manual effort** | **~35 min, mostly automated** |

#### Persona 2: "I Have a Cluster" (Currently Awkward)

| Step | Current | Target |
|---|---|---|
| 1 | Figure out which stack template to use | Wizard: "Where is your cluster?" → "I have one" |
| 2 | Go to Stacks → "F5 BNK 2.2" → "Existing Project" | Upload kubeconfig (or paste cluster endpoint) |
| 3 | Know that cert-manager/Multus will be installed | **Cluster scan**: "We detected cert-manager v1.16 ✓, Multus ✓, HugePages ✓, SR-IOV ✗" |
| 4 | Manually enter cluster_endpoint, CA data, subnet IDs | **Auto-discovered** from kubeconfig + cluster scan |
| 5 | Create project secrets for jwt_token, cne_pull_secret | Same — these are truly user-specific |
| 6 | Hope the cluster meets prerequisites | **Pre-flight validation**: "Missing: SR-IOV device plugin. [Install guide →]" |
| 7 | Deploy, get errors if prerequisites missing | Deploy with confidence — prerequisites verified |
| **Total inputs** | **~15 variables** | **3 inputs** (kubeconfig, JWT, FAR key) |

#### Persona 3: "Just BNK" (Currently Not Differentiated)

| Step | Current | Target |
|---|---|---|
| 1 | Same flow as Persona 2 | Wizard: "Where is your cluster?" → "I have one with prerequisites" |
| 2 | BNK template deploys cert-manager again (conflict!) | **Cluster scan** detects existing components, skips them |
| 3 | BNK template deploys NADs again (conflict!) | Only deploys FLO → GatewayClass → Gateway → Routes |
| 4 | 7 modules, many unnecessary | **4 modules** (skip prerequisites) |
| **Time** | ~15 min (7 serial modules through OpenTofu) | **~2 min** (4 parallel modules via native K8s API) |

### 9.3 The Cluster-First Architecture

The key architectural shift: **start with the cluster, not the stack template.**

```
┌─────────────────────────────────────────────────────────────────┐
│  STEP 1: Connect or Create Your Cluster                          │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ Create New   │  │ Connect      │  │ Connect                 │ │
│  │ ○ AWS EKS    │  │ ○ Kubeconfig │  │ ○ Cluster Endpoint      │ │
│  │ ○ Azure AKS  │  │   upload     │  │   + token / cert        │ │
│  │ ○ GCP GKE    │  │              │  │   (for managed K8s)     │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬──────────────┘ │
│         │                  │                      │               │
│         ▼                  ▼                      ▼               │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ OpenTofu Engine       │  Kubernetes Engine                   │ │
│  │ (create infra)        │  (connect + scan)                    │ │
│  └──────────┬────────────┴──────────┬───────────────────────────┘ │
│             │                       │                             │
│             ▼                       ▼                             │
├─────────────────────────────────────────────────────────────────┤
│  STEP 2: Cluster Scan (automatic)                                │
│                                                                  │
│  ✓ Kubernetes 1.30.2                                             │
│  ✓ cert-manager v1.16.1 (installed)                              │
│  ✓ Multus CNI (installed)                                        │
│  ✓ HugePages: 2Gi per node (3 nodes)                            │
│  ✗ SR-IOV Device Plugin (not detected)  [Install →]             │
│  ✗ F5 FAR Pull Secret (not configured)  [Configure →]           │
│                                                                  │
│  BNK Readiness: 4/6 prerequisites met                            │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  STEP 3: Configure BNK                                           │
│                                                                  │
│  Deployment Profile: ○ Dev  ● Standard  ○ Production             │
│                                                                  │
│  F5 License JWT:     [••••••••••••••••]  ✓ Valid                  │
│  F5 FAR Key:         [Upload file]       ✓ Uploaded              │
│                                                                  │
│  Modules to deploy (auto-determined from scan):                  │
│  ☐ cert-manager (already installed — skipping)                   │
│  ☐ network-setup (Multus NADs already exist — skipping)          │
│  ☑ FAR Setup (pull secrets, manifest download)                   │
│  ☑ FLO (Lifecycle Operator)                                      │
│  ☑ BNK GatewayClass                                              │
│  ☑ Gateway (HTTP + HTTPS listeners)                              │
│  ☑ Routes (default route)                                        │
│                                                                  │
│  [Preview Plan]  [Deploy BNK →]                                  │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│  STEP 4: Deploy (streaming progress)                             │
│                                                                  │
│  ████████████░░░░ 65% — Installing FLO operator...               │
│                                                                  │
│  ✓ FAR Setup ............. 12s                                    │
│  ✓ FLO Helm install ...... 45s (waiting for CRDs)                │
│  ► BNK GatewayClass ...... deploying (TMM pods starting)         │
│  ○ Gateway ............... pending                                │
│  ○ Routes ................ pending                                │
│                                                                  │
│  Live: TMM pod f5-bnk/tmm-0: 5/7 containers ready               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 9.4 The Cluster Scan: Key New Capability

The most impactful new feature would be a **cluster prerequisite scanner** that auto-detects what's already installed:

```python
class ClusterScanner:
    """Scan a K8s cluster to detect BNK prerequisites."""
    
    async def scan(self, api: kr8s.asyncio.Api) -> ClusterScanResult:
        return ClusterScanResult(
            kubernetes_version=await self._get_k8s_version(api),
            cert_manager=await self._detect_cert_manager(api),
            multus=await self._detect_multus(api),
            sriov=await self._detect_sriov(api),
            hugepages=await self._detect_hugepages(api),
            storage_classes=await self._get_storage_classes(api),
            existing_bnk=await self._detect_existing_bnk(api),
            node_capabilities=await self._scan_node_capabilities(api),
        )
    
    async def _detect_cert_manager(self, api) -> ComponentStatus:
        """Check if cert-manager is installed and what version."""
        try:
            deploy = await api.get("deployments", "cert-manager", 
                                   namespace="cert-manager")
            version = deploy.spec.template.spec.containers[0].image.split(":")[-1]
            return ComponentStatus(installed=True, version=version)
        except kr8s.NotFoundError:
            return ComponentStatus(installed=False)
    
    async def _detect_multus(self, api) -> ComponentStatus:
        """Check for Multus CRDs and DaemonSet."""
        try:
            await api.get("customresourcedefinitions", 
                         "network-attachment-definitions.k8s.cni.cncf.io")
            return ComponentStatus(installed=True)
        except kr8s.NotFoundError:
            return ComponentStatus(installed=False)
    
    async def _detect_hugepages(self, api) -> HugePagesStatus:
        """Check node allocatable HugePages."""
        nodes = await api.get("nodes")
        return HugePagesStatus(
            available=any(
                int(n.status.allocatable.get("hugepages-2Mi", "0")) > 0 
                for n in nodes
            ),
            per_node={
                n.metadata.name: n.status.allocatable.get("hugepages-2Mi", "0")
                for n in nodes
            }
        )
    
    async def _detect_existing_bnk(self, api) -> Optional[BNKStatus]:
        """Check for existing F5 BNK installation."""
        try:
            # Look for FLO deployment
            flo = await api.get("deployments", namespace="f5-bnk", 
                               label_selector="app=f5-lifecycle-operator")
            # Look for CNEInstance
            cne = await api.get("cneinstances", namespace="f5-bnk")
            return BNKStatus(
                installed=True,
                flo_version=flo[0].metadata.labels.get("version"),
                cne_ready=cne[0].status.get("ready", False) if cne else False,
            )
        except (kr8s.NotFoundError, IndexError):
            return None
```

This scanner drives the **adaptive module selection** — instead of a fixed template, the system determines which modules are needed based on what's already there.

### 9.5 Adaptive Module Selection

```python
def determine_required_modules(scan: ClusterScanResult, 
                                profile: DeploymentProfile) -> List[Module]:
    """Given a cluster scan, determine exactly which modules to deploy."""
    modules = []
    
    # Always needed: FAR setup (pull secrets, manifest download)
    modules.append(Module("bnk/far-setup"))
    
    # Conditional: cert-manager
    if not scan.cert_manager.installed:
        modules.append(Module("k8s/cert-manager"))
    elif scan.cert_manager.version < MINIMUM_CERT_MANAGER_VERSION:
        modules.append(Module("k8s/cert-manager", action="upgrade"))
    
    # Conditional: network attachments
    if not scan.multus.installed:
        raise PrerequisiteError("Multus CNI is required but not installed")
    if not scan.multus.nads_exist:
        modules.append(Module("k8s/network-setup"))
    
    # Conditional: SR-IOV (for high-performance profiles)
    if profile.requires_sriov and not scan.sriov.installed:
        raise PrerequisiteError("SR-IOV is required for production profile")
    
    # Always needed: BNK core
    modules.append(Module("bnk/flo"))
    modules.append(Module("bnk/bnk-gatewayclass"))
    
    # Always needed: Gateway + Routes (user configures these)
    modules.append(Module("bnk/gateway"))
    modules.append(Module("bnk/routes"))
    
    # Optional policies
    if profile.include_security_policy:
        modules.append(Module("bnk/bnk-secpolicy"))
    if profile.include_network_policy:
        modules.append(Module("bnk/bnk-netpolicy"))
    
    return modules
```

---

## 10. Day 2 Operations Gap Analysis

"Ridiculously easy" isn't just about deployment — it's about the entire lifecycle. Here's what exists, what's missing, and what matters most.

### 10.1 What Already Works Well

| Operation | Backend | Frontend | Quality |
|---|---|---|---|
| View K8s resources (pods, deployments, services) | Full | Full | Excellent |
| Scale deployments | Full | Full | Good |
| Pod logs / exec terminal | Full | Full | Good |
| View BNK CRDs (F5BigFwPolicy, BNKSecPolicy, etc.) | Full | Full | Good |
| Edit/Create K8s resources (YAML) | Full | Full | Good |
| Deployment rollout/restart/undo | Full | Full | Good |
| Drift detection (Terraform state) | Full | Full | Good |
| AWS SSO credential refresh | Full | Full | Good |
| Infracost estimation | Full | Full | Good |
| Helm operations (install/upgrade/rollback) | **Full backend** | **NOT connected** | Backend only |

### 10.2 Critical Day 2 Gaps

#### Gap 1: BNK Version Upgrade (HIGH)
**Current:** No mechanism to upgrade BNK from one version to another. The manifest version is hardcoded in the stack template. Upgrading means manually editing the CNEInstance CR or redeploying with a new template version.

**Target:** A "BNK Upgrade" workflow that:
1. Detects current BNK version from CNEInstance CR
2. Shows available versions from F5 manifest repository
3. Downloads new manifest, updates FAR secrets
4. Upgrades FLO Helm chart to matching version
5. Updates CNEInstance CR with new manifest version
6. Monitors rolling restart of TMM pods
7. Validates post-upgrade health (all containers ready, traffic flowing)

#### Gap 2: BNK Health Dashboard (HIGH)
**Current:** Individual K8s resources can be viewed. No unified BNK health view.

**Target:** A single "BNK Health" panel showing:
```
┌─────────────────────────────────────────────────────┐
│  F5 BNK Health                              ● HEALTHY│
│                                                      │
│  Platform                                            │
│  ├─ FLO Operator ........ v2.2.0  ● Running          │
│  ├─ CWC ................. v2.2.0  ● Running          │
│  ├─ DSSM ................ v2.2.0  ● Running          │
│  └─ Observer ............. v2.2.0  ● Running          │
│                                                      │
│  Data Plane                                          │
│  ├─ TMM Pods ............ 2/2     ● All Ready (7/7)  │
│  ├─ IPAM Controller ..... v2.2.0  ● Running          │
│  └─ OTEL Collector ...... v0.88   ● Running          │
│                                                      │
│  Networking                                          │
│  ├─ GatewayClass ........ bnk-gc  ● Accepted         │
│  ├─ Gateway ............. bnk-gw  ● Programmed       │
│  │  ├─ :80 HTTP ......... ● Attached (3 routes)      │
│  │  └─ :443 HTTPS ....... ● Attached (3 routes)      │
│  └─ Routes .............. 6 total  5 active           │
│                                                      │
│  Security                                            │
│  ├─ Certificates ........ 4 valid (renew in 58 days) │
│  ├─ Firewall Policy ..... ● Active (12 rules)        │
│  └─ License ............. ● Connected (expires 2027)  │
│                                                      │
│  [Upgrade BNK]  [View Logs]  [Export Config]         │
└─────────────────────────────────────────────────────┘
```

#### Gap 3: Route & Policy Builders (MEDIUM)
**Current:** Routes and policies can only be managed via raw YAML editing in the generic K8s resource editor.

**Target:** Purpose-built UI components. The frontend already has `ListenerBuilder.tsx` and `RoutingRulesBuilder.tsx` for the initial deploy — these need to be usable for Day 2 editing as well, not just the deploy wizard.

#### Gap 4: Connect Helm UI to Backend (MEDIUM)
**Current:** `HelmReleasesViewer.tsx` line 59 says `// TODO: Implement API hook when backend is ready`. The backend has full Helm upgrade/rollback support in `helm_service.py`. The wiring just needs to be completed.

**Impact:** Without this, the user can't upgrade FLO (Helm chart) from the UI, can't rollback a bad FLO upgrade, and can't view Helm release history — all critical for BNK lifecycle.

#### Gap 5: Cluster-Level Drift Detection (MEDIUM)
**Current:** Drift detection runs `tofu plan` against Terraform state. If someone `kubectl edit`s a BNK CR directly, the system doesn't detect it.

**Target:** For the Kubernetes engine modules, drift detection should compare desired state (from the last successful deploy) against actual state (from the cluster). kr8s makes this straightforward:

```python
async def check_k8s_drift(api, module, desired_state):
    actual = await api.get(desired_state["kind"], desired_state["metadata"]["name"],
                          namespace=desired_state["metadata"].get("namespace"))
    return deep_diff(desired_state["spec"], actual.raw["spec"])
```

#### Gap 6: BNK Scale / Resize (LOW)
**Current:** No workflow to change deployment size (Small → Medium → Large).

**Target:** Update CNEInstance CR with new resource allocations, triggering FLO to rolling-restart TMM with new resource limits.

#### Gap 7: Certificate Monitoring (LOW)
**Current:** cert-manager CRDs are viewable in the K8s resource browser but there's no proactive alerting on upcoming certificate expiry.

### 10.3 Prioritized Day 2 Roadmap

| Priority | Operation | Effort | Blocked By |
|---|---|---|---|
| 1 | Connect Helm UI to backend | 2-3 days | Nothing — backend exists |
| 2 | BNK Health Dashboard | 1-2 weeks | Kubernetes engine (for live cluster queries) |
| 3 | BNK Version Upgrade workflow | 2-3 weeks | Kubernetes engine, Helm UI |
| 4 | Route/Policy builders for Day 2 | 1-2 weeks | Nothing — components exist |
| 5 | Cluster-level drift detection | 1-2 weeks | Kubernetes engine |
| 6 | BNK Scale workflow | 1 week | Kubernetes engine |
| 7 | Certificate monitoring | 1 week | Nothing |

---

## 11. The Hybrid Engine: Detailed Design

### 11.1 Architecture Overview

```
┌───────────────────────────────────────────────────────────────┐
│                         API Layer                              │
│  FastAPI Routes → Pydantic Schemas → Dependency Injection      │
└────────────────────────────┬──────────────────────────────────┘
                             │
┌────────────────────────────▼──────────────────────────────────┐
│                    Orchestration Layer                          │
│                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Dependency    │  │ Variable     │  │ Parallel Execution   │ │
│  │ Graph         │  │ Assembler    │  │ Service              │ │
│  │ (shared)      │  │ (shared)     │  │ (shared)             │ │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘ │
│         │                  │                      │             │
│         └──────────┬───────┘──────────────────────┘             │
│                    │                                            │
│         ┌──────────▼──────────┐                                │
│         │  Engine Router      │                                │
│         │  (module.json →     │                                │
│         │   engine selection) │                                │
│         └─────┬─────────┬────┘                                │
│               │         │                                      │
└───────────────┼─────────┼──────────────────────────────────────┘
                │         │
  ┌─────────────▼──┐   ┌──▼───────────────┐
  │  OpenTofu       │   │  Kubernetes       │
  │  Engine         │   │  Engine           │
  │                 │   │                   │
  │  For:           │   │  For:             │
  │  infra/aws/*    │   │  k8s/*            │
  │  infra/azure/*  │   │  bnk/*            │
  │  infra/gcp/*    │   │                   │
  │                 │   │  Uses:            │
  │  Uses:          │   │  kr8s (CRDs)      │
  │  tofu CLI       │   │  helm CLI (charts)│
  │  State files    │   │  K8s watch (wait) │
  │  Workspaces     │   │                   │
  └─────────────────┘   └───────────────────┘
```

### 11.2 The Engine Interface

Both engines implement the same interface so the orchestrator doesn't care which one runs:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

@dataclass
class OperationResult:
    success: bool
    outputs: dict          # Module outputs for dependency wiring
    stdout: str            # Human-readable log
    resources_created: int
    resources_modified: int
    resources_destroyed: int
    duration_seconds: float
    error_message: Optional[str] = None
    error_suggestion: Optional[str] = None  # Actionable guidance

@dataclass  
class PlanResult:
    has_changes: bool
    adds: int
    changes: int
    destroys: int
    details: str           # Human-readable plan output
    plan_id: Optional[str] # For apply-saved-plan (OpenTofu only)

class DeploymentEngine(ABC):
    """Common interface for both execution engines."""
    
    @abstractmethod
    async def plan(self, module: ModuleConfig) -> PlanResult:
        """Preview what would change."""
        ...
    
    @abstractmethod
    async def apply(self, module: ModuleConfig, 
                    on_output: Callable[[str], None] = None) -> OperationResult:
        """Deploy the module. on_output streams lines for real-time UI."""
        ...
    
    @abstractmethod
    async def destroy(self, module: ModuleConfig) -> OperationResult:
        """Remove all resources created by this module."""
        ...
    
    @abstractmethod
    async def get_outputs(self, module: ModuleConfig) -> dict:
        """Read current outputs (for dependency wiring)."""
        ...
    
    @abstractmethod
    async def check_drift(self, module: ModuleConfig) -> Optional[PlanResult]:
        """Check if actual state differs from desired state."""
        ...

class OpenTofuEngine(DeploymentEngine):
    """Existing execution engine, refactored to this interface.
    Used for infra/* modules that create cloud resources."""
    
    async def apply(self, module, on_output=None):
        # Existing logic: prepare workspace, write configs, 
        # subprocess.run tofu apply, parse outputs
        ...

class KubernetesEngine(DeploymentEngine):
    """New engine for k8s/* and bnk/* modules.
    Uses kr8s for CRDs and subprocess helm for charts."""
    
    def __init__(self, kubeconfig: str):
        self.api = kr8s.asyncio.Api(kubeconfig=kubeconfig)
    
    async def apply(self, module, on_output=None):
        if module.type == "helm_chart":
            return await self._install_helm(module, on_output)
        else:
            return await self._apply_manifests(module, on_output)
    
    async def _apply_manifests(self, module, on_output):
        """Apply K8s manifests with real-time progress."""
        manifests = module.render_manifests()  # Python dicts, not HCL
        results = []
        
        for manifest in manifests:
            if on_output:
                on_output(f"Applying {manifest['kind']}/{manifest['metadata']['name']}...")
            
            result = await self.api.apply(manifest, force=True)
            results.append(result)
            
            if on_output:
                on_output(f"  ✓ {manifest['kind']}/{manifest['metadata']['name']} applied")
        
        # Wait for readiness
        for result in results:
            if hasattr(result, 'wait'):
                if on_output:
                    on_output(f"Waiting for {result.kind}/{result.name} to be ready...")
                await result.wait("condition=Ready", timeout=300)
        
        return OperationResult(success=True, outputs=self._collect_outputs(results), ...)
    
    async def get_outputs(self, module):
        """Read outputs directly from the cluster — no state file needed."""
        # e.g., for gateway module: read Gateway status for addresses
        resource = await self.api.get(module.output_resource_kind, 
                                      module.output_resource_name,
                                      namespace=module.namespace)
        return module.extract_outputs(resource.raw)
    
    async def check_drift(self, module):
        """Compare desired state vs actual state in the cluster."""
        desired = module.render_manifests()
        for manifest in desired:
            try:
                actual = await self.api.get(manifest["kind"], 
                                           manifest["metadata"]["name"],
                                           namespace=manifest["metadata"].get("namespace"))
                diff = deep_diff(manifest.get("spec", {}), actual.raw.get("spec", {}))
                if diff:
                    return PlanResult(has_changes=True, details=format_diff(diff), ...)
            except kr8s.NotFoundError:
                return PlanResult(has_changes=True, adds=1, details="Resource missing from cluster")
        return PlanResult(has_changes=False)
```

### 11.3 Engine Selection

The `module.json` metadata schema gets a new field:

```json
{
  "module": {
    "name": "F5 BNK GatewayClass",
    "layer": "bnk-gateway",
    "execution_engine": "kubernetes",
    "supported_platforms": ["any"]
  }
}
```

The engine router:

```python
class EngineRouter:
    def __init__(self, tofu_engine: OpenTofuEngine, k8s_engine: KubernetesEngine):
        self.engines = {
            "opentofu": tofu_engine,
            "kubernetes": k8s_engine,
        }
    
    def get_engine(self, module: ProjectModule) -> DeploymentEngine:
        engine_type = module.library_module.metadata.get("execution_engine", "opentofu")
        return self.engines[engine_type]
```

### 11.4 What Changes, What Stays the Same

| Component | Changes? | Details |
|---|---|---|
| Dependency Graph | **No change** | Works on ProjectModule IDs regardless of engine |
| Variable Assembler | **Minor change** | Same 7-layer chain; K8s engine receives Python dicts instead of writing tfvars |
| Parallel Execution | **No change** | Layer-based execution; each layer can mix engine types |
| Celery Tasks | **Refactored** | Replace init/plan/apply subprocess chain with engine.apply() call |
| Workspace Manager | **Not needed** for K8s engine | Only used by OpenTofu engine |
| WebSocket Progress | **Enhanced** | K8s engine streams real-time; OpenTofu engine unchanged |
| Credential Service | **Enhanced** | kubeconfig resolution for K8s engine; cloud creds for OpenTofu |
| Module Catalog | **Minor change** | New `execution_engine` field in module.json |

### 11.5 Module Conversion: Terraform → Python

Each BNK module converts from Terraform HCL to a Python module definition:

```python
# Example: bnk/bnk-gatewayclass/module.py

class BNKGatewayClassModule(KubernetesModule):
    """F5 BNK GatewayClass — triggers FLO to deploy all BNK components."""
    
    name = "bnk-gatewayclass"
    layer = "bnk-gateway"
    
    required_inputs = {
        "gatewayclass_name": Input(type=str, default="bnk-gatewayclass"),
        "controller_name": Input(type=str, default="f5.com/gateway-controller"),
        "flo_namespace": Input(type=str, source="module", from_module="bnk/flo"),
        "tmm_cpu": Input(type=str, source="profile"),  # From deployment profile
        "tmm_memory": Input(type=str, source="profile"),
    }
    
    outputs = {
        "gatewayclass_name": OutputFrom(resource="GatewayClass", field="metadata.name"),
        "gatewayclass_ready": OutputFrom(resource="GatewayClass", 
                                         field="status.conditions", 
                                         condition="Accepted"),
    }
    
    def render_manifests(self, variables: dict) -> List[dict]:
        return [
            {
                "apiVersion": "gateway.networking.k8s.io/v1",
                "kind": "GatewayClass",
                "metadata": {"name": variables["gatewayclass_name"]},
                "spec": {
                    "controllerName": variables["controller_name"],
                    "parametersRef": {
                        "group": "bnk.f5.com",
                        "kind": "BNKGatewayClassConfig",
                        "name": f"{variables['gatewayclass_name']}-config",
                    }
                }
            },
            {
                "apiVersion": "bnk.f5.com/v1",
                "kind": "BNKGatewayClassConfig",
                "metadata": {
                    "name": f"{variables['gatewayclass_name']}-config",
                    "namespace": variables["flo_namespace"],
                },
                "spec": {
                    "tmm": {
                        "resources": {
                            "requests": {
                                "cpu": variables["tmm_cpu"],
                                "memory": variables["tmm_memory"],
                            }
                        }
                    }
                }
            }
        ]
```

---

## 12. Revised Implementation Roadmap

Given the three-persona focus and Day 2 operations analysis, here's the updated phased plan:

### Phase 1: Foundation & Quick Wins (Weeks 1-2)
_Same as before — bug fixes, consistency, cleanup_

- [ ] All items from original Phase 1 (Section 7)
- [ ] **NEW:** Connect Helm UI to backend (2-3 days — unblock Day 2 Helm operations)
- [ ] **NEW:** Fix stack template dependency serialization (enable parallelism)

### Phase 2: Structural Refactoring (Weeks 3-6)
_Split models, centralize schemas, DRY infrastructure_

- [ ] All items from original Phase 2 (Section 7)
- [ ] **NEW:** Define `DeploymentEngine` interface (ABC)
- [ ] **NEW:** Refactor existing `ExecutionEngine` to implement the interface as `OpenTofuEngine`
- [ ] **NEW:** Add `execution_engine` field to `module.json` metadata schema
- [ ] **NEW:** Wire up Route/Policy builder components for Day 2 editing

### Phase 3: Kubernetes Engine (Weeks 7-12)
_The big capability jump — native K8s execution_

- [ ] Build `KubernetesEngine` using kr8s + subprocess helm
- [ ] Build `ClusterScanner` for prerequisite detection
- [ ] Build `EngineRouter` that selects engine based on module metadata
- [ ] Convert BNK modules from Terraform → Python module definitions
- [ ] Implement streaming output via WebSocket for both engines
- [ ] Build BNK Health Dashboard (powered by cluster scanner)
- [ ] Implement adaptive module selection (scan → determine modules)
- [ ] Implement cluster-level drift detection for K8s engine modules
- [ ] Deployment profiles (dev/standard/production)
- [ ] Auto-CIDR derivation for VPC modules

### Phase 4: Persona-Driven UX (Weeks 13-16)
_The "ridiculously easy" experience_

- [ ] Unified deploy wizard ("Where is your cluster?" flow)
- [ ] Kubeconfig-first flow for Persona 2 & 3
- [ ] Cluster scan → adaptive template → one-click deploy
- [ ] Stack preview/dry-run
- [ ] Automated rollback on stack failure
- [ ] BNK upgrade workflow
- [ ] BNK scale/resize workflow
- [ ] Certificate monitoring and alerting

### Phase 5: Multi-Cloud (Weeks 17-24)
_True "any cloud or on-prem"_

- [ ] Azure infrastructure modules (`infra/azure/vnet`, `infra/azure/aks`)
- [ ] GCP infrastructure modules (`infra/gcp/vpc`, `infra/gcp/gke`)
- [ ] Multi-cloud credential abstraction
- [ ] On-prem kubeconfig-only flow (skip all infra)
- [ ] Multi-cluster BNK management ("deploy BNK to cluster B like cluster A")
- [ ] Cross-cluster BNK health dashboard
