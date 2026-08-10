# Hybrid Engine Design — kr8s + OpenTofu

Technical design document for the dual-engine architecture.

---

## Overview

```
                    ┌─────────────────────────┐
                    │    Unified Orchestrator   │
                    │                           │
                    │  Dependency Graph          │
                    │  Variable Assembler        │
                    │  Parallel Execution        │
                    │  Progress/WebSocket        │
                    └─────────┬─────────────────┘
                              │
                    ┌─────────▼─────────────┐
                    │    Engine Router        │
                    │    (module.json →       │
                    │     engine selection)   │
                    └────┬──────────────┬────┘
                         │              │
           ┌─────────────▼──┐    ┌──────▼───────────┐
           │  OpenTofu       │    │  Kubernetes       │
           │  Engine         │    │  Engine           │
           │                 │    │                   │
           │  infra/aws/*    │    │  k8s/*            │
           │  infra/azure/*  │    │  bnk/*            │
           │  infra/gcp/*    │    │                   │
           │                 │    │  kr8s (manifests)  │
           │  tofu CLI       │    │  helm CLI (charts) │
           │  State files    │    │  K8s watch (wait)  │
           │  Workspaces     │    │  No state files    │
           └─────────────────┘    └───────────────────┘
```

## What Stays the Same

These components are engine-agnostic and shared:

| Component | File(s) | Role |
|---|---|---|
| Dependency Graph | `services/dependency_graph_service.py`, `utils/dependency_graph.py` | Layer-based parallel execution ordering |
| Variable Assembler | `services/execution/variable_assembler.py` (extracted from execution_engine.py) | 7-layer variable precedence chain |
| Parallel Execution | `services/parallel_execution_service.py`, `tasks/parallel_tasks.py` | Layer dispatch, progress tracking |
| WebSocket Progress | `services/websocket_service.py` | Real-time status/output push |
| Credential Service | `services/credentials_service.py` | Cloud credential resolution |
| Module Catalog | `services/module_catalog_service.py` | Module discovery, sync, versioning |

## The Engine Interface

```python
# backend/services/execution/engine_interface.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any, List

@dataclass
class OperationResult:
    """Result of an apply/destroy operation."""
    success: bool
    outputs: Dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    resources_created: int = 0
    resources_modified: int = 0
    resources_destroyed: int = 0
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    error_suggestion: Optional[str] = None

@dataclass
class PlanResult:
    """Result of a plan/preview operation."""
    has_changes: bool
    adds: int = 0
    changes: int = 0
    destroys: int = 0
    details: str = ""
    plan_id: Optional[str] = None  # OpenTofu: saved plan file reference

@dataclass
class ModuleConfig:
    """Engine-agnostic module configuration."""
    module_id: int
    path: str                          # e.g., "bnk/flo" or "infra/aws/vpc"
    category: str                      # "infra", "k8s", "bnk"
    source_url: Optional[str] = None   # Git URL for OpenTofu modules
    source_ref: Optional[str] = None   # Git branch/tag
    execution_engine: str = "opentofu" # "opentofu" or "kubernetes"

class DeploymentEngine(ABC):
    """Interface both engines implement."""
    
    @abstractmethod
    async def apply(
        self,
        module: ModuleConfig,
        variables: Dict[str, Any],
        credentials_env: Dict[str, str],
        on_output: Optional[Callable[[str], None]] = None,
    ) -> OperationResult:
        """Deploy the module."""
        ...
    
    @abstractmethod
    async def destroy(
        self,
        module: ModuleConfig,
        variables: Dict[str, Any],
        credentials_env: Dict[str, str],
        on_output: Optional[Callable[[str], None]] = None,
    ) -> OperationResult:
        """Remove all resources created by this module."""
        ...
    
    @abstractmethod
    async def plan(
        self,
        module: ModuleConfig,
        variables: Dict[str, Any],
        credentials_env: Dict[str, str],
    ) -> PlanResult:
        """Preview what would change."""
        ...
    
    @abstractmethod
    async def get_outputs(
        self,
        module: ModuleConfig,
    ) -> Dict[str, Any]:
        """Read current outputs for dependency wiring."""
        ...
    
    @abstractmethod
    async def check_drift(
        self,
        module: ModuleConfig,
        desired_variables: Dict[str, Any],
    ) -> Optional[PlanResult]:
        """Check if actual state differs from desired."""
        ...
```

## Kubernetes Engine — kr8s Implementation

```python
# backend/services/execution/kubernetes_engine.py

import kr8s
import asyncio
import subprocess
import json
import time
import tempfile
from typing import Optional, Callable, Dict, Any

from .engine_interface import DeploymentEngine, OperationResult, PlanResult, ModuleConfig


class KubernetesEngine(DeploymentEngine):
    """Native K8s execution engine using kr8s for manifests and helm CLI for charts."""
    
    def __init__(self, kubeconfig_path: str):
        self.kubeconfig_path = kubeconfig_path
        self._api: Optional[kr8s.asyncio.Api] = None
    
    async def _get_api(self) -> kr8s.asyncio.Api:
        if self._api is None:
            self._api = await kr8s.asyncio.Api(kubeconfig=self.kubeconfig_path)
        return self._api
    
    async def apply(self, module, variables, credentials_env, on_output=None):
        start = time.monotonic()
        module_def = self._load_module_definition(module.path)
        
        try:
            if module_def.module_type == "helm_chart":
                result = await self._apply_helm(module_def, variables, on_output)
            else:
                result = await self._apply_manifests(module_def, variables, on_output)
            
            result.duration_seconds = time.monotonic() - start
            return result
            
        except Exception as e:
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - start,
                error_message=str(e),
                error_suggestion=self._suggest_fix(e),
            )
    
    async def _apply_manifests(self, module_def, variables, on_output):
        api = await self._get_api()
        manifests = module_def.render_manifests(variables)
        created = 0
        modified = 0
        output_lines = []
        
        for manifest in manifests:
            kind = manifest["kind"]
            name = manifest["metadata"]["name"]
            ns = manifest["metadata"].get("namespace", "default")
            
            if on_output:
                on_output(f"Applying {kind}/{name} in {ns}...")
            
            # Server-side apply — handles create AND update
            try:
                existing = await api.get(kind, name, namespace=ns)
                await api.apply(manifest, force=True)
                modified += 1
                msg = f"  Updated {kind}/{name}"
            except kr8s.NotFoundError:
                await api.apply(manifest, force=True)
                created += 1
                msg = f"  Created {kind}/{name}"
            
            if on_output:
                on_output(msg)
            output_lines.append(msg)
        
        # Wait for readiness on resources that support it
        for manifest in manifests:
            condition = module_def.get_readiness_condition(manifest)
            if condition:
                kind = manifest["kind"]
                name = manifest["metadata"]["name"]
                ns = manifest["metadata"].get("namespace", "default")
                
                if on_output:
                    on_output(f"Waiting for {kind}/{name} to be ready...")
                
                obj = await api.get(kind, name, namespace=ns)
                try:
                    await obj.wait(condition, timeout=module_def.timeout)
                    if on_output:
                        on_output(f"  {kind}/{name} is ready")
                except TimeoutError:
                    return OperationResult(
                        success=False,
                        error_message=f"{kind}/{name} did not become ready within {module_def.timeout}s",
                        error_suggestion="Check pod events and logs for the resource.",
                        stdout="\n".join(output_lines),
                        resources_created=created,
                        resources_modified=modified,
                    )
        
        # Collect outputs
        outputs = await self._collect_outputs(module_def)
        
        return OperationResult(
            success=True,
            outputs=outputs,
            stdout="\n".join(output_lines),
            resources_created=created,
            resources_modified=modified,
        )
    
    async def _apply_helm(self, module_def, variables, on_output):
        """Install or upgrade a Helm release."""
        values = module_def.render_helm_values(variables)
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(values, f)
            values_path = f.name
        
        cmd = [
            "helm", "upgrade", "--install",
            module_def.release_name,
            module_def.chart_ref,
            "--namespace", module_def.namespace,
            "--create-namespace",
            "--values", values_path,
            "--wait",
            "--timeout", f"{module_def.timeout}s",
            "--kubeconfig", self.kubeconfig_path,
        ]
        
        if module_def.chart_version:
            cmd.extend(["--version", module_def.chart_version])
        
        if on_output:
            on_output(f"Installing Helm chart: {module_def.chart_ref}")
            on_output(f"  Release: {module_def.release_name}")
            on_output(f"  Namespace: {module_def.namespace}")
        
        result = await asyncio.to_thread(
            subprocess.run, cmd,
            capture_output=True, text=True, timeout=module_def.timeout + 30,
        )
        
        if result.returncode != 0:
            return OperationResult(
                success=False,
                stdout=result.stdout,
                stderr=result.stderr,
                error_message=f"Helm install failed: {result.stderr}",
                error_suggestion=self._suggest_helm_fix(result.stderr),
            )
        
        if on_output:
            on_output(f"  Helm chart installed successfully")
        
        outputs = await self._collect_outputs(module_def)
        
        return OperationResult(
            success=True,
            outputs=outputs,
            stdout=result.stdout,
            resources_created=1,  # Helm release
        )
    
    async def destroy(self, module, variables, credentials_env, on_output=None):
        module_def = self._load_module_definition(module.path)
        start = time.monotonic()
        
        try:
            if module_def.module_type == "helm_chart":
                return await self._destroy_helm(module_def, on_output)
            else:
                return await self._destroy_manifests(module_def, variables, on_output)
        except Exception as e:
            return OperationResult(
                success=False,
                duration_seconds=time.monotonic() - start,
                error_message=str(e),
            )
    
    async def _destroy_manifests(self, module_def, variables, on_output):
        api = await self._get_api()
        manifests = module_def.render_manifests(variables)
        destroyed = 0
        
        # Delete in reverse order
        for manifest in reversed(manifests):
            kind = manifest["kind"]
            name = manifest["metadata"]["name"]
            ns = manifest["metadata"].get("namespace", "default")
            
            if on_output:
                on_output(f"Deleting {kind}/{name}...")
            
            try:
                obj = await api.get(kind, name, namespace=ns)
                await obj.delete()
                destroyed += 1
                if on_output:
                    on_output(f"  Deleted {kind}/{name}")
            except kr8s.NotFoundError:
                if on_output:
                    on_output(f"  {kind}/{name} already absent")
        
        return OperationResult(success=True, resources_destroyed=destroyed)
    
    async def plan(self, module, variables, credentials_env):
        """Check what would change by comparing desired vs actual."""
        module_def = self._load_module_definition(module.path)
        manifests = module_def.render_manifests(variables)
        api = await self._get_api()
        
        adds = changes = 0
        details = []
        
        for manifest in manifests:
            kind = manifest["kind"]
            name = manifest["metadata"]["name"]
            ns = manifest["metadata"].get("namespace", "default")
            
            try:
                actual = await api.get(kind, name, namespace=ns)
                diff = self._deep_diff(manifest.get("spec", {}), actual.raw.get("spec", {}))
                if diff:
                    changes += 1
                    details.append(f"~ {kind}/{name}: {json.dumps(diff, indent=2)}")
            except kr8s.NotFoundError:
                adds += 1
                details.append(f"+ {kind}/{name} (will be created)")
        
        return PlanResult(
            has_changes=(adds + changes) > 0,
            adds=adds,
            changes=changes,
            details="\n".join(details) if details else "No changes detected.",
        )
    
    async def get_outputs(self, module):
        module_def = self._load_module_definition(module.path)
        return await self._collect_outputs(module_def)
    
    async def check_drift(self, module, desired_variables):
        return await self.plan(module, desired_variables, {})
    
    # --- Internal helpers ---
    
    async def _collect_outputs(self, module_def) -> dict:
        """Read outputs from live cluster resources."""
        api = await self._get_api()
        outputs = {}
        for name, spec in module_def.outputs.items():
            try:
                obj = await api.get(spec.resource_kind, spec.resource_name,
                                    namespace=spec.namespace)
                outputs[name] = spec.extract_from(obj.raw)
            except (kr8s.NotFoundError, KeyError):
                outputs[name] = None
        return outputs
    
    def _load_module_definition(self, path: str):
        """Load Python module definition by path."""
        # Registry of known modules — populated at startup
        from modules import MODULE_REGISTRY
        if path not in MODULE_REGISTRY:
            raise ValueError(f"No Python module definition for: {path}")
        return MODULE_REGISTRY[path]
    
    def _suggest_fix(self, error: Exception) -> Optional[str]:
        """Map common K8s errors to actionable suggestions."""
        msg = str(error).lower()
        if "forbidden" in msg:
            return "The kubeconfig may not have sufficient RBAC permissions."
        if "not found" in msg and "crd" in msg:
            return "A required CRD is not installed. Ensure FLO operator is deployed first."
        if "connection refused" in msg:
            return "Cannot reach the Kubernetes API server. Check kubeconfig and cluster status."
        return None
```

## Python Module Definition Pattern

```python
# backend/modules/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any


@dataclass
class InputSpec:
    """Declares a module input variable."""
    type: type = str
    required: bool = True
    default: Any = None
    source: str = "user"           # "user", "module", "profile", "auto"
    from_module: Optional[str] = None  # e.g., "bnk/flo"
    from_output: Optional[str] = None  # e.g., "flo_namespace"
    description: str = ""


@dataclass
class OutputSpec:
    """Declares how to read an output from the cluster."""
    resource_kind: str
    resource_name: str              # Can use {variable} placeholders
    namespace: str = "default"
    field_path: str = ""            # JSONPath-like: "metadata.name", "status.addresses[0].value"
    
    def extract_from(self, resource_dict: dict) -> Any:
        """Extract value from a K8s resource dict using field_path."""
        parts = self.field_path.split(".")
        current = resource_dict
        for part in parts:
            if "[" in part:
                key, idx = part.rstrip("]").split("[")
                current = current[key][int(idx)]
            else:
                current = current.get(part)
                if current is None:
                    return None
        return current


class BaseModule(ABC):
    """Base class for all Python-defined K8s modules."""
    
    name: str = ""
    path: str = ""
    module_type: str = "manifest"   # "manifest" or "helm_chart"
    timeout: int = 300
    
    inputs: Dict[str, InputSpec] = {}
    outputs: Dict[str, OutputSpec] = {}
    dependencies: List[str] = []
    
    @abstractmethod
    def render_manifests(self, variables: Dict[str, Any]) -> List[dict]:
        """Return list of K8s manifest dicts to apply."""
        ...
    
    def get_readiness_condition(self, manifest: dict) -> Optional[str]:
        """Return the condition to wait for, or None to skip waiting."""
        return None
    
    def get_required_user_inputs(self) -> Dict[str, InputSpec]:
        """Return only the inputs the user needs to provide."""
        return {k: v for k, v in self.inputs.items() if v.source == "user" and v.required}


class HelmModule(BaseModule):
    """Base for modules that install a Helm chart."""
    
    module_type = "helm_chart"
    release_name: str = ""
    chart_ref: str = ""
    chart_version: Optional[str] = None
    namespace: str = "default"
    
    def render_manifests(self, variables):
        """Helm modules don't render manifests — they use render_helm_values."""
        return []
    
    @abstractmethod
    def render_helm_values(self, variables: Dict[str, Any]) -> dict:
        """Return Helm values dict."""
        ...


class ManifestModule(BaseModule):
    """Base for modules that apply K8s manifests directly."""
    module_type = "manifest"
```

## Example Module: BNK GatewayClass

```python
# backend/modules/bnk/bnk_gatewayclass.py

from modules.base import ManifestModule, InputSpec, OutputSpec


class BNKGatewayClassModule(ManifestModule):
    name = "BNK GatewayClass"
    path = "bnk/bnk-gatewayclass"
    timeout = 300
    dependencies = ["bnk/flo"]
    
    inputs = {
        "gatewayclass_name": InputSpec(
            default="bnk-gatewayclass",
            description="Name of the GatewayClass resource",
        ),
        "controller_name": InputSpec(
            default="f5.com/gateway-controller",
            source="auto",
            description="BNK controller name (do not change)",
        ),
        "flo_namespace": InputSpec(
            source="module",
            from_module="bnk/flo",
            from_output="flo_namespace",
            description="Namespace where FLO is installed",
        ),
        "tmm_cpu": InputSpec(
            source="profile",
            default="4",
            description="CPU allocation for TMM",
        ),
        "tmm_memory": InputSpec(
            source="profile",
            default="8Gi",
            description="Memory allocation for TMM",
        ),
        "tmm_hugepages": InputSpec(
            source="profile",
            default="4Gi",
            description="HugePages allocation for TMM",
        ),
        "ha_enabled": InputSpec(
            type=bool,
            source="profile",
            default=False,
            description="Enable TMM high availability",
        ),
    }
    
    outputs = {
        "gatewayclass_name": OutputSpec(
            resource_kind="GatewayClass",
            resource_name="{gatewayclass_name}",
            field_path="metadata.name",
        ),
        "gatewayclass_ready": OutputSpec(
            resource_kind="GatewayClass",
            resource_name="{gatewayclass_name}",
            field_path="status.conditions",  # Check for Accepted condition
        ),
    }
    
    def render_manifests(self, variables):
        gc_name = variables.get("gatewayclass_name", "bnk-gatewayclass")
        flo_ns = variables.get("flo_namespace", "f5-bnk")
        
        return [
            # GatewayClass
            {
                "apiVersion": "gateway.networking.k8s.io/v1",
                "kind": "GatewayClass",
                "metadata": {"name": gc_name},
                "spec": {
                    "controllerName": variables.get("controller_name", "f5.com/gateway-controller"),
                    "parametersRef": {
                        "group": "bnk.f5.com",
                        "kind": "BNKGatewayClassConfig",
                        "name": f"{gc_name}-config",
                        "namespace": flo_ns,
                    },
                },
            },
            # BNKGatewayClassConfig
            {
                "apiVersion": "bnk.f5.com/v1",
                "kind": "BNKGatewayClassConfig",
                "metadata": {
                    "name": f"{gc_name}-config",
                    "namespace": flo_ns,
                },
                "spec": {
                    "controller": {
                        "resources": {
                            "requests": {"cpu": "100m", "memory": "256Mi"},
                            "limits": {"cpu": "500m", "memory": "512Mi"},
                        },
                    },
                    "tmm": {
                        "replicas": 2 if variables.get("ha_enabled") else 1,
                        "resources": {
                            "requests": {
                                "cpu": variables.get("tmm_cpu", "4"),
                                "memory": variables.get("tmm_memory", "8Gi"),
                            },
                            "limits": {
                                "cpu": variables.get("tmm_cpu", "4"),
                                "memory": variables.get("tmm_memory", "8Gi"),
                                "hugepages-2Mi": variables.get("tmm_hugepages", "4Gi"),
                            },
                        },
                    },
                },
            },
        ]
    
    def get_readiness_condition(self, manifest):
        if manifest["kind"] == "GatewayClass":
            return "condition=Accepted"
        return None
```

## Engine Router

```python
# backend/services/execution/engine_router.py

from .engine_interface import DeploymentEngine, ModuleConfig
from .opentofu_engine import OpenTofuEngine
from .kubernetes_engine import KubernetesEngine


class EngineRouter:
    """Routes module execution to the correct engine."""
    
    def __init__(self, db, opentofu_engine: OpenTofuEngine):
        self.db = db
        self.opentofu = opentofu_engine
        self._k8s_engines = {}  # Cache per kubeconfig
    
    def get_engine(self, project, module) -> DeploymentEngine:
        engine_type = self._determine_engine(module)
        
        if engine_type == "kubernetes":
            kubeconfig = self._resolve_kubeconfig(project)
            if kubeconfig not in self._k8s_engines:
                self._k8s_engines[kubeconfig] = KubernetesEngine(kubeconfig)
            return self._k8s_engines[kubeconfig]
        
        return self.opentofu
    
    def _determine_engine(self, module) -> str:
        """Determine which engine should execute this module."""
        # 1. Explicit module.json metadata
        if hasattr(module, 'library_module') and module.library_module:
            metadata = module.library_module.dependencies_metadata or {}
            engine = metadata.get("execution_engine")
            if engine:
                return engine
        
        # 2. Python module registry
        from modules import MODULE_REGISTRY
        if module.path_in_project in MODULE_REGISTRY:
            return "kubernetes"
        
        # 3. Category-based default
        category = getattr(module, 'category', '') or ''
        if category in ("k8s", "bnk"):
            return "kubernetes"
        
        # 4. Default to OpenTofu
        return "opentofu"
    
    def _resolve_kubeconfig(self, project) -> str:
        """Get kubeconfig path for the project's cluster."""
        # For projects with EKS: generate kubeconfig from EKS credentials
        # For projects with uploaded kubeconfig: use the stored file
        # For on-prem: use the SSH tunnel + kubeconfig
        cluster = (
            self.db.query(KubernetesCluster)
            .filter(KubernetesCluster.project_id == project.id)
            .first()
        )
        if cluster and cluster.kubeconfig:
            return self._write_temp_kubeconfig(cluster.kubeconfig)
        
        # Fallback: try to get from EKS module outputs
        return self._generate_eks_kubeconfig(project)
```

## Cluster Scanner

```python
# backend/services/cluster_scanner.py

import kr8s
from dataclasses import dataclass, field
from typing import Optional, Dict, List


@dataclass
class ComponentStatus:
    installed: bool
    version: Optional[str] = None
    healthy: bool = True
    details: Optional[str] = None

@dataclass
class NodeCapabilities:
    name: str
    hugepages_2mi: str = "0"
    sriov_devices: Dict[str, int] = field(default_factory=dict)
    labels: Dict[str, str] = field(default_factory=dict)
    allocatable_cpu: str = "0"
    allocatable_memory: str = "0"

@dataclass
class ClusterScanResult:
    kubernetes_version: str = ""
    node_count: int = 0
    cert_manager: ComponentStatus = field(default_factory=lambda: ComponentStatus(installed=False))
    multus: ComponentStatus = field(default_factory=lambda: ComponentStatus(installed=False))
    sriov: ComponentStatus = field(default_factory=lambda: ComponentStatus(installed=False))
    hugepages: ComponentStatus = field(default_factory=lambda: ComponentStatus(installed=False))
    existing_bnk: Optional[ComponentStatus] = None
    storage_classes: List[str] = field(default_factory=list)
    nodes: List[NodeCapabilities] = field(default_factory=list)
    
    @property
    def bnk_ready(self) -> bool:
        """Are all hard prerequisites met for BNK deployment?"""
        return (
            self.cert_manager.installed
            and self.multus.installed
            and self.hugepages.installed
        )
    
    @property
    def prerequisites_met(self) -> Dict[str, bool]:
        return {
            "kubernetes": bool(self.kubernetes_version),
            "cert_manager": self.cert_manager.installed,
            "multus_cni": self.multus.installed,
            "hugepages": self.hugepages.installed,
            "sriov": self.sriov.installed,
            "storage_class": len(self.storage_classes) > 0,
        }
    
    @property  
    def missing_prerequisites(self) -> List[str]:
        return [k for k, v in self.prerequisites_met.items() if not v]


class ClusterScanner:
    """Scan a K8s cluster to detect BNK prerequisites and existing installations."""
    
    async def scan(self, api: kr8s.asyncio.Api) -> ClusterScanResult:
        result = ClusterScanResult()
        
        # Run all scans concurrently
        import asyncio
        version, nodes, cert_manager, multus, sriov, storage, bnk = await asyncio.gather(
            self._get_version(api),
            self._scan_nodes(api),
            self._detect_cert_manager(api),
            self._detect_multus(api),
            self._detect_sriov(api),
            self._get_storage_classes(api),
            self._detect_existing_bnk(api),
            return_exceptions=True,
        )
        
        if not isinstance(version, Exception):
            result.kubernetes_version = version
        if not isinstance(nodes, Exception):
            result.nodes = nodes
            result.node_count = len(nodes)
            # Derive hugepages from node capabilities
            has_hugepages = any(
                int(n.hugepages_2mi.rstrip("Ki").rstrip("Mi").rstrip("Gi") or "0") > 0
                for n in nodes
            )
            result.hugepages = ComponentStatus(installed=has_hugepages)
        if not isinstance(cert_manager, Exception):
            result.cert_manager = cert_manager
        if not isinstance(multus, Exception):
            result.multus = multus
        if not isinstance(sriov, Exception):
            result.sriov = sriov
        if not isinstance(storage, Exception):
            result.storage_classes = storage
        if not isinstance(bnk, Exception):
            result.existing_bnk = bnk
        
        return result
    
    async def _get_version(self, api) -> str:
        version_info = await api.version()
        return f"{version_info.major}.{version_info.minor}"
    
    async def _scan_nodes(self, api) -> List[NodeCapabilities]:
        nodes = await api.get("nodes")
        return [
            NodeCapabilities(
                name=n.metadata.name,
                hugepages_2mi=n.status.get("allocatable", {}).get("hugepages-2Mi", "0"),
                sriov_devices={
                    k: int(v) for k, v in n.status.get("allocatable", {}).items()
                    if "sriov" in k.lower() or "netdevice" in k.lower()
                },
                labels=dict(n.metadata.get("labels", {})),
                allocatable_cpu=n.status.get("allocatable", {}).get("cpu", "0"),
                allocatable_memory=n.status.get("allocatable", {}).get("memory", "0"),
            )
            for n in nodes
        ]
    
    async def _detect_cert_manager(self, api) -> ComponentStatus:
        try:
            deploys = await api.get("deployments", namespace="cert-manager",
                                     label_selector="app.kubernetes.io/name=cert-manager")
            if deploys:
                image = deploys[0].spec.template.spec.containers[0].image
                version = image.split(":")[-1] if ":" in image else "unknown"
                return ComponentStatus(installed=True, version=version)
        except Exception:
            pass
        return ComponentStatus(installed=False)
    
    async def _detect_multus(self, api) -> ComponentStatus:
        try:
            await api.get("customresourcedefinitions",
                         "network-attachment-definitions.k8s.cni.cncf.io")
            return ComponentStatus(installed=True)
        except Exception:
            return ComponentStatus(installed=False)
    
    async def _detect_sriov(self, api) -> ComponentStatus:
        try:
            ds = await api.get("daemonsets", namespace="kube-system",
                               label_selector="app=sriov-device-plugin")
            if ds:
                return ComponentStatus(installed=True)
        except Exception:
            pass
        return ComponentStatus(installed=False)
    
    async def _get_storage_classes(self, api) -> List[str]:
        try:
            scs = await api.get("storageclasses")
            return [sc.metadata.name for sc in scs]
        except Exception:
            return []
    
    async def _detect_existing_bnk(self, api) -> Optional[ComponentStatus]:
        try:
            deploys = await api.get("deployments", namespace="f5-bnk",
                                     label_selector="app.kubernetes.io/name=f5-lifecycle-operator")
            if deploys:
                image = deploys[0].spec.template.spec.containers[0].image
                version = image.split(":")[-1] if ":" in image else "unknown"
                return ComponentStatus(installed=True, version=version)
        except Exception:
            pass
        return None
```

## Comparison: Before and After

### Deploying BNK GatewayClass

**Before (OpenTofu path):**
```
1. Clone git repo to /app/workspaces/{pid}/{mid}/           (~5s)
2. Write terraform.tfvars.json                               (~0.1s)
3. Write backend_override.tf (S3 or local)                   (~0.1s)
4. Write encryption.tf (state encryption config)             (~0.1s)
5. Write bnk_forge_providers.tf (K8s/Helm auth)              (~0.1s)
6. tofu init (download providers: kubernetes, null, time)    (~30s)
7. tofu plan (diff state vs desired)                         (~10s)
8. tofu apply plan.out                                       (~15s)
9. tofu output -json (read outputs)                          (~2s)
   TOTAL: ~62 seconds
```

**After (kr8s path):**
```
1. Render 2 manifests (GatewayClass + BNKGatewayClassConfig) (~0.01s)
2. api.apply(gatewayclass_manifest)                          (~0.5s)
3. api.apply(config_manifest)                                (~0.5s)
4. gateway.wait("condition=Accepted")                        (~2s)
5. Read outputs from cluster                                 (~0.5s)
   TOTAL: ~3.5 seconds
```

**Speedup: ~18x faster.**
