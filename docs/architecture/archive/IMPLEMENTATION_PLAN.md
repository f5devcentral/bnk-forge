# Implementation Plan — Design Doc to Code

This is the concrete execution plan for evolving BNK-Forge from the current architecture to the target state. Each phase builds on the previous one — no phase requires throwing away work from an earlier phase.

---

## How to Read This

Each item has:
- **What:** The concrete change
- **Where:** Exact files to create/modify
- **Depends on:** What must be done first
- **Validates with:** How to know it works

---

## Phase 1: Foundation (Weeks 1-2)

> Goal: Fix bugs, standardize patterns, unlock blocked features.
> Risk: Low. All changes are backward-compatible.

Everything in [QUICK_WINS.md](./QUICK_WINS.md) — see that document for the full list.

**Additionally:**

### P1-A: Celery Task Session Context Manager
**What:** Extract repeated `db = SessionLocal(); try/except/finally/close` into a reusable context manager.
**Where:** Create `backend/core/task_utils.py`
```python
from contextlib import contextmanager
from database import SessionLocal

@contextmanager
def task_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
```
**Then:** Refactor `backend/tasks/opentofu_tasks.py` to use `with task_session() as db:` in every task.
**Depends on:** Nothing.
**Validates with:** Existing tests pass. Tasks still create/close sessions properly.

### P1-B: Version from Single Source
**What:** Read `VERSION` file at startup, propagate everywhere.
**Where:**
- `backend/core/config.py` — read `VERSION` file, set `APP_VERSION`
- `backend/routes/api.py` line 49 — use `APP_VERSION` instead of hardcoded string
- `backend/main.py` line 277 — use `APP_VERSION`
- `frontend-v2/vite.config.ts` — inject `__APP_VERSION__` from `VERSION` file at build time
**Depends on:** Nothing.
**Validates with:** `GET /` returns correct version. Frontend shows correct version.

---

## Phase 2: Structural Refactoring (Weeks 3-6)

> Goal: Split monoliths, centralize patterns, prepare the codebase for the new engine.
> Risk: Medium. Changes are internal — API contracts don't change.

### P2-A: Split `models.py` into Domain Modules
**What:** Break 1,198-line file into focused modules.
**Where:** Create `backend/models/` directory:
```
backend/models/
  __init__.py              # Re-exports everything (backward compat)
  base.py                  # Base, TimestampMixin
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
**Key:** `__init__.py` re-exports all models so existing `from models import Project` still works.
**Depends on:** Nothing.
**Validates with:** All imports still resolve. `alembic` still detects models. App starts.

### P2-B: Centralize Pydantic Schemas
**What:** Move inline Pydantic models from route files to `schemas/`.
**Where:** Expand `backend/schemas/`:
```
backend/schemas/
  __init__.py
  project.py               # ProjectCreate, ProjectUpdate, ProjectResponse
  module.py                 # ModuleCreate, ModuleUpdate, InitRequest, PlanRequest, ApplyRequest...
  deployment.py             # DeploymentResponse, TaskResponse, TaskCreate
  stack.py                  # StackDeployRequest, StackPreviewResponse
  kubernetes.py             # ClusterCreate, ResourceResponse
  common.py                 # PaginatedResponse, ErrorResponse, SuccessResponse
```
**Primary source:** `routes/project_modules.py` lines 44-155 has 8 inline models to extract.
**Depends on:** Nothing.
**Validates with:** Same API contracts. OpenAPI schema unchanged.

### P2-C: Define the `DeploymentEngine` Interface
**What:** Create the ABC that both engines will implement.
**Where:** Create `backend/services/execution/engine_interface.py`
```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Callable, Dict, Any

@dataclass
class OperationResult:
    success: bool
    outputs: Dict[str, Any]
    stdout: str
    resources_created: int
    resources_modified: int
    resources_destroyed: int
    duration_seconds: float
    error_message: Optional[str] = None
    error_suggestion: Optional[str] = None

@dataclass
class PlanResult:
    has_changes: bool
    adds: int
    changes: int
    destroys: int
    details: str
    plan_id: Optional[str] = None

class DeploymentEngine(ABC):
    @abstractmethod
    async def plan(self, module_config: dict, variables: dict, 
                   credentials_env: dict) -> PlanResult: ...
    
    @abstractmethod
    async def apply(self, module_config: dict, variables: dict,
                    credentials_env: dict,
                    on_output: Optional[Callable[[str], None]] = None) -> OperationResult: ...
    
    @abstractmethod
    async def destroy(self, module_config: dict, variables: dict,
                      credentials_env: dict) -> OperationResult: ...
    
    @abstractmethod
    async def get_outputs(self, module_config: dict) -> Dict[str, Any]: ...
    
    @abstractmethod
    async def check_drift(self, module_config: dict, 
                          desired_state: dict) -> Optional[PlanResult]: ...
```
**Depends on:** Nothing (interface only, no implementation yet).
**Validates with:** Passes type checking. No runtime impact.

### P2-D: Wrap Existing Engine as `OpenTofuEngine`
**What:** Refactor current `ExecutionEngine` to implement the `DeploymentEngine` ABC.
**Where:** Create `backend/services/execution/opentofu_engine.py`
- Extract from `execution_engine.py`: keep all existing logic
- Implement `plan()`, `apply()`, `destroy()`, `get_outputs()`, `check_drift()` methods
- Each method wraps existing `run_plan()`, `run_apply()`, etc.
- The current `ExecutionEngine` class becomes a thin adapter
**Key:** This is a WRAP, not a rewrite. All existing logic stays. We're just adding the interface on top.
**Depends on:** P2-C.
**Validates with:** All existing deployments still work. Run existing tests.

### P2-E: Add `execution_engine` to Module Metadata
**What:** Add optional `execution_engine` field to `module.json` schema.
**Where:**
- `backend/services/module_metadata.py` — add to schema validation (accept `"opentofu"` or `"kubernetes"`, default `"opentofu"`)
- `backend/models/module.py` (or current `models.py`) — no model change needed (stored in `dependencies_metadata` JSON)
**Depends on:** Nothing.
**Validates with:** Existing modules parse fine (field is optional with default).

### P2-F: Decompose ExecutionEngine Internals
**What:** Split the 1,709-line god object into focused modules.
**Where:** Create `backend/services/execution/` package:
```
backend/services/execution/
  __init__.py
  engine_interface.py       # ABC (from P2-C)
  opentofu_engine.py        # OpenTofu implementation (from P2-D)
  variable_assembler.py     # 7-layer variable chain (extract from execution_engine.py)
  hcl_generator.py          # write_tfvars, write_backend, write_encryption, write_provider
  tofu_runner.py            # subprocess.run wrappers with timeout
  output_collector.py       # tofu output -json parsing
  error_analyzer.py         # Destroy error regex parsing + guidance
  workspace_preparer.py     # Git clone, workspace setup
```
**Key:** `variable_assembler.py` is shared between both engines. Everything else is OpenTofu-specific.
**Depends on:** P2-C, P2-D.
**Validates with:** All existing deployments still work. Each file is independently testable.

---

## Phase 3: Kubernetes Engine (Weeks 7-12)

> Goal: Build the native K8s execution path. BNK modules run 5-10x faster.
> Risk: Medium-high. New code path, but old path remains as fallback.

### P3-A: Add kr8s Dependency
**What:** Add `kr8s` to requirements.
**Where:** `backend/requirements.txt` — add `kr8s>=0.20.0`
**Depends on:** Nothing.
**Validates with:** `pip install kr8s` succeeds. `import kr8s` works.

### P3-B: Build `KubernetesEngine`
**What:** Implement `DeploymentEngine` using kr8s + Helm subprocess.
**Where:** Create `backend/services/execution/kubernetes_engine.py`

Core methods:
```python
class KubernetesEngine(DeploymentEngine):
    def __init__(self, kubeconfig: str):
        self.api = kr8s.asyncio.Api(kubeconfig=kubeconfig)
    
    async def apply(self, module_config, variables, credentials_env, on_output=None):
        module_def = load_module_definition(module_config["path"])
        
        if module_def.type == "helm_chart":
            return await self._install_helm(module_def, variables, on_output)
        else:
            return await self._apply_manifests(module_def, variables, on_output)
    
    async def _apply_manifests(self, module_def, variables, on_output):
        manifests = module_def.render_manifests(variables)
        for manifest in manifests:
            if on_output:
                on_output(f"Applying {manifest['kind']}/{manifest['metadata']['name']}...")
            await self.api.apply(manifest, force=True)
            if on_output:
                on_output(f"  Applied {manifest['kind']}/{manifest['metadata']['name']}")
        
        # Wait for readiness
        for manifest in manifests:
            if module_def.has_readiness_check(manifest):
                obj = await self.api.get(manifest["kind"], 
                                         manifest["metadata"]["name"],
                                         namespace=manifest["metadata"].get("namespace"))
                await obj.wait(module_def.readiness_condition(manifest), timeout=300)
        
        return OperationResult(
            success=True,
            outputs=await self._collect_outputs(module_def),
            ...
        )
    
    async def _install_helm(self, module_def, variables, on_output):
        """Use subprocess helm (reuse existing helm_service.py patterns)."""
        values = module_def.render_helm_values(variables)
        result = await asyncio.to_thread(
            subprocess.run,
            ["helm", "install", module_def.release_name, module_def.chart_ref,
             "--namespace", module_def.namespace,
             "--values", write_temp_values(values),
             "--wait", "--timeout", f"{module_def.timeout}s",
             "--kubeconfig", self.kubeconfig_path],
            capture_output=True, text=True
        )
        ...
    
    async def get_outputs(self, module_config):
        """Read outputs from live cluster — no state file."""
        module_def = load_module_definition(module_config["path"])
        outputs = {}
        for output_name, output_spec in module_def.outputs.items():
            resource = await self.api.get(
                output_spec.resource_kind,
                output_spec.resource_name,
                namespace=output_spec.namespace
            )
            outputs[output_name] = output_spec.extract(resource.raw)
        return outputs
    
    async def check_drift(self, module_config, desired_state):
        """Compare desired manifests vs actual cluster state."""
        module_def = load_module_definition(module_config["path"])
        manifests = module_def.render_manifests(desired_state["variables"])
        diffs = []
        for manifest in manifests:
            try:
                actual = await self.api.get(manifest["kind"],
                                           manifest["metadata"]["name"],
                                           namespace=manifest["metadata"].get("namespace"))
                diff = deep_diff(manifest.get("spec", {}), actual.raw.get("spec", {}))
                if diff:
                    diffs.append(diff)
            except kr8s.NotFoundError:
                diffs.append({"missing": manifest["kind"] + "/" + manifest["metadata"]["name"]})
        ...
```

**Depends on:** P2-C (engine interface), P3-A (kr8s).
**Validates with:** Unit tests with mocked kr8s API. Integration test against a local k3s/kind cluster.

### P3-C: Build Python Module Definitions for BNK Modules
**What:** Convert each BNK Terraform module into a Python class that renders K8s manifests.
**Where:** Create `backend/modules/` directory:
```
backend/modules/
  __init__.py
  base.py                      # BaseModule, HelmModule, ManifestModule ABCs
  k8s/
    cert_manager.py             # HelmModule — cert-manager chart
    network_setup.py            # ManifestModule — Multus NADs
  bnk/
    far_setup.py                # ManifestModule — namespaces, secrets, manifest download
    flo.py                      # HelmModule — FLO Helm chart
    bnk_gatewayclass.py         # ManifestModule — GatewayClass + BNKGatewayClassConfig
    gateway.py                  # ManifestModule — Gateway with listeners
    routes.py                   # ManifestModule — HTTPRoute/GRPCRoute/L4Route
    bnk_secpolicy.py            # ManifestModule — BNKSecPolicy CR
    bnk_netpolicy.py            # ManifestModule — BNKNetPolicy CR
```

Each module class defines:
- `required_inputs` — what variables it needs (with sources: user, module, profile)
- `render_manifests(variables)` — returns list of K8s manifest dicts
- `render_helm_values(variables)` — returns Helm values dict (for HelmModule)
- `outputs` — what to read from the cluster after apply
- `readiness_condition` — what to wait for
- `dependencies` — what modules must be applied first

**Key:** These replace both the Terraform `.tf` files AND the variable schema parsing. The module IS the schema.

**Depends on:** P3-B.
**Validates with:** `module.render_manifests(test_vars)` produces valid K8s YAML. Compare output with existing Terraform module's `kubernetes_manifest` blocks.

### P3-D: Build Engine Router
**What:** Route module execution to the correct engine based on metadata.
**Where:** Create `backend/services/execution/engine_router.py`
```python
class EngineRouter:
    def __init__(self, opentofu_engine, k8s_engine_factory):
        self.opentofu = opentofu_engine
        self.k8s_factory = k8s_engine_factory  # Creates per-cluster engines
    
    def get_engine(self, project, module) -> DeploymentEngine:
        engine_type = self._determine_engine(module)
        if engine_type == "kubernetes":
            kubeconfig = self._resolve_kubeconfig(project)
            return self.k8s_factory(kubeconfig)
        return self.opentofu
    
    def _determine_engine(self, module) -> str:
        # 1. Explicit metadata
        if module.library_module and module.library_module.metadata:
            engine = module.library_module.metadata.get("execution_engine")
            if engine:
                return engine
        # 2. Category-based default
        if module.category in ("k8s", "bnk"):
            return "kubernetes"
        return "opentofu"
```
**Depends on:** P2-C, P2-E, P3-B.
**Validates with:** Routes infra modules to OpenTofu, BNK modules to K8s engine.

### P3-E: Integrate Engine Router into Celery Tasks
**What:** Update task functions to use the engine router instead of direct `ExecutionEngine`.
**Where:** `backend/tasks/opentofu_tasks.py` (or new `backend/tasks/deployment_tasks.py`)
```python
@celery_app.task
def run_module_apply(module_id: int, ...):
    with task_session() as db:
        module = get_or_404(db, ProjectModule, module_id)
        project = module.project
        
        router = EngineRouter(
            opentofu_engine=OpenTofuEngine(db),
            k8s_engine_factory=lambda kc: KubernetesEngine(kc),
        )
        engine = router.get_engine(project, module)
        
        variables = VariableAssembler(db).build_variables(project, module)
        credentials = CredentialsService(db).get_credentials(project)
        
        result = asyncio.run(engine.apply(
            module_config={"path": module.path_in_project, ...},
            variables=variables,
            credentials_env=credentials,
            on_output=lambda line: publish_task_output(task_id, line),
        ))
        
        # Update module status, save outputs — same as current logic
        module.status = "applied" if result.success else "apply_failed"
        module.outputs = result.outputs
        ...
```
**Key:** The orchestration logic (status updates, output saving, dependency triggering) stays the same. Only the execution backend changes.
**Depends on:** P3-B, P3-C, P3-D, P1-A (task_session).
**Validates with:** Deploy a BNK stack. Infra modules use OpenTofu. BNK modules use K8s engine. Same end result, faster.

### P3-F: Build Cluster Scanner
**What:** Detect installed prerequisites on a K8s cluster.
**Where:** Create `backend/services/cluster_scanner.py`
```python
class ClusterScanner:
    async def scan(self, api: kr8s.asyncio.Api) -> ClusterScanResult:
        return ClusterScanResult(
            kubernetes_version=await self._get_version(api),
            cert_manager=await self._detect_cert_manager(api),
            multus=await self._detect_multus(api),
            sriov=await self._detect_sriov(api),
            hugepages=await self._detect_hugepages(api),
            storage_classes=await self._get_storage_classes(api),
            existing_bnk=await self._detect_existing_bnk(api),
        )
```
Expose via API: `POST /api/kubernetes/clusters/{id}/scan`
**Depends on:** P3-A (kr8s).
**Validates with:** Point at a cluster with cert-manager installed. Scanner detects it.

### P3-G: Adaptive Module Selection
**What:** Use scan results to determine which modules to deploy (skip what's already installed).
**Where:** Create `backend/services/adaptive_module_service.py`
- Takes `ClusterScanResult` + desired stack + deployment profile
- Returns filtered module list with reasons (deploy/skip/upgrade)
**Depends on:** P3-F.
**Validates with:** Cluster with cert-manager → module list skips cert-manager.

### P3-H: Streaming Output via WebSocket
**What:** Replace buffered subprocess output with real-time streaming.
**Where:**
- `backend/services/execution/kubernetes_engine.py` — already streams via `on_output` callback
- `backend/services/execution/opentofu_engine.py` — replace `subprocess.run` with `subprocess.Popen` + line-by-line read
- `backend/services/websocket_service.py` — add `publish_task_output(task_id, line)` method
- `frontend-v2/src/hooks/useTaskWebSocket.ts` — handle streaming output events
**Depends on:** P3-B.
**Validates with:** Deploy a module. See output appear line by line in the UI.

---

## Phase 4: Persona-Driven UX (Weeks 13-16)

> Goal: The "ridiculously easy" experience for all three personas.
> Risk: Medium. Frontend-heavy, but backend is solid from Phase 3.

### P4-A: Unified Deploy Wizard
**What:** Replace "pick a stack template" with "where is your cluster?"
**Where:** New frontend component `frontend-v2/src/components/deploy/DeployWizard.tsx`
- Step 1: "Where is your cluster?" → Create new (AWS/Azure/GCP) | I have one (kubeconfig upload) | On-prem
- Step 2: Connect/create → scan prerequisites
- Step 3: Configure BNK (deployment profile + F5 license)
- Step 4: Review (adaptive module list) → Deploy
**Depends on:** P3-F, P3-G.

### P4-B: Deployment Profiles
**What:** Collapse 15+ variables into "Dev / Standard / Production" choice.
**Where:**
- Create `backend/data/deployment_profiles.json` — profile definitions
- `backend/services/execution/variable_assembler.py` — apply profile defaults before user overrides
- Frontend — radio group in deploy wizard
**Depends on:** P2-F (variable assembler extraction).

### P4-C: Auto-Derive CIDRs
**What:** From one VPC CIDR, auto-calculate all 5 subnet CIDRs.
**Where:** `backend/services/execution/variable_assembler.py` or `backend/lib/cidr_calculator.py`
**Depends on:** Nothing.

### P4-D: BNK Health Dashboard
**What:** Unified view of BNK component health on a cluster.
**Where:**
- `backend/routes/bnk.py` — new `GET /api/bnk/{cluster_id}/health` endpoint using cluster scanner
- `frontend-v2/src/components/bnk/BNKHealthDashboard.tsx` — visual health panel
**Depends on:** P3-F (cluster scanner).

### P4-E: BNK Upgrade Workflow
**What:** Upgrade BNK version on a running cluster.
**Where:**
- `backend/services/bnk_upgrade_service.py` — download new manifest, upgrade FLO Helm, update CNEInstance
- `frontend-v2/src/components/bnk/BNKUpgradeWizard.tsx`
**Depends on:** P3-B (K8s engine), P3-F (scanner), QW-015 (Helm UI connected).

### P4-F: Stack Preview / Dry-Run
**What:** Show what a deployment will do before creating any resources.
**Where:**
- `backend/routes/stacks.py` — new `POST /api/stacks/templates/{slug}/preview` endpoint
- `backend/services/stack_deployment_service.py` — `preview_stack()` method
**Depends on:** P3-G (adaptive module selection).

---

## Phase 5: Multi-Cloud (Weeks 17-24)

### P5-A: Multi-Cloud Credential Abstraction
**Where:** Refactor `backend/services/credentials_service.py` to `CloudCredentialProvider` ABC with AWS/Azure/GCP/OnPrem implementations.

### P5-B: Azure Infrastructure Modules
**Where:** `backend/modules/infra/azure/` — VNet, AKS, NSGs (OpenTofu modules)

### P5-C: GCP Infrastructure Modules
**Where:** `backend/modules/infra/gcp/` — VPC, GKE, IAM (OpenTofu modules)

### P5-D: On-Prem Kubeconfig Flow
**What:** "I have a cluster" flow that only needs a kubeconfig file — no cloud provider at all.

---

## Dependency Graph

```
Phase 1 (Quick Wins)
  ├── QW-001..017 (independent, parallel)
  └── P1-A (task session ctx mgr)
      P1-B (version propagation)

Phase 2 (Structural)
  ├── P2-A (split models) — independent
  ├── P2-B (centralize schemas) — independent  
  ├── P2-C (engine interface) — independent
  │   └── P2-D (wrap as OpenTofuEngine)
  │       └── P2-F (decompose internals)
  └── P2-E (module.json metadata) — independent

Phase 3 (K8s Engine)
  P3-A (add kr8s) → P3-B (build KubernetesEngine) → P3-C (module definitions)
                                                    → P3-E (task integration)
                   → P3-F (cluster scanner) → P3-G (adaptive selection)
  P3-D (engine router) — depends on P2-C, P3-B
  P3-H (streaming) — depends on P3-B

Phase 4 (UX)
  P4-A (deploy wizard) — depends on P3-F, P3-G
  P4-B (profiles) — depends on P2-F
  P4-C (auto-CIDRs) — independent
  P4-D (BNK health) — depends on P3-F
  P4-E (BNK upgrade) — depends on P3-B, P3-F, QW-015
  P4-F (stack preview) — depends on P3-G

Phase 5 (Multi-Cloud)
  All depend on Phase 3 engine router being solid
```
