# BNK Forge + Operator Architecture

## The Problem with "BNK Manager on K8s"

The previous proposal (CUSTOMER_PRODUCT_VISION.md) suggested running BNK Manager on the same K8s cluster it manages. This has three fatal flaws:

1. **Chicken and egg** — The "Build It All" persona doesn't have a cluster yet. They need BNK Forge running BEFORE any cluster exists to create the infrastructure.

2. **Can't fix what you're standing on** — If the K8s cluster has an issue (node failure, networking broken, etcd corruption, botched upgrade), the management tool that's supposed to help you fix it is also down.

3. **Infra management requires independence** — OpenTofu operations that create/modify cloud infrastructure (VPCs, EKS clusters, node groups) must run from OUTSIDE the target cluster. You can't resize the node group you're sitting on.

## The Right Architecture: Control Plane + Lightweight Operator

```
┌─────────────────────────────────────────────────────────────────┐
│                                                                  │
│  BNK-FORGE (Control Plane)                                       │
│  Runs INDEPENDENTLY — laptop, VM, Docker, or separate K8s       │
│                                                                  │
│  ┌─────────────┐ ┌─────────────┐ ┌───────────────────────┐     │
│  │ FastAPI      │ │ React UI    │ │ Engines               │     │
│  │ (API + WS)   │ │ (Dashboard)  │ │ ├─ OpenTofu (infra)   │     │
│  │              │ │              │ │ └─ K8s (via operators) │     │
│  └──────┬───────┘ └──────────────┘ └───────────┬───────────┘     │
│         │                                       │                │
│         │  WebSocket (persistent)               │  gRPC / HTTPS  │
│         │  ◄──── operator phones home ────►     │  commands out   │
│         │                                       │                │
└─────────┼───────────────────────────────────────┼────────────────┘
          │                                       │
          │           ┌───────────────────────────┘
          │           │
    ┌─────▼───────────▼──────────────────────────────────────────┐
    │  CUSTOMER K8S CLUSTER A                                     │
    │                                                             │
    │  bnk-operator/ (lightweight — single pod, ~50MB)            │
    │  ├── Connects OUTBOUND to BNK Forge (no inbound ports)     │
    │  ├── Receives commands: "apply this manifest", "install     │
    │  │   this chart", "scan this cluster", "get health"        │
    │  ├── Executes locally: kr8s, helm, kubectl                 │
    │  ├── Streams results back: status, logs, outputs            │
    │  ├── Watches BNK resources: reports health continuously     │
    │  └── Heartbeat: "I'm alive, here's my status"              │
    │                                                             │
    │  f5-bnk/ (the actual BNK deployment)                        │
    │  ├── flo, tmm, cwc, dssm, ...                              │
    │  └── Managed BY the operator on behalf of BNK Forge         │
    │                                                             │
    └─────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────────────────┐
    │  CUSTOMER K8S CLUSTER B (another cluster)                    │
    │                                                             │
    │  bnk-operator/ (same lightweight operator)                   │
    │  └── Also phones home to the same BNK Forge instance        │
    │                                                             │
    │  f5-bnk/                                                     │
    │  └── Different BNK config, managed from same dashboard      │
    │                                                             │
    └─────────────────────────────────────────────────────────────┘
```

## Why This Is Better

### 1. Independence from the clusters it manages
BNK Forge runs on YOUR machine (or a VM/container). If every customer cluster burns to the ground, BNK Forge is still running, still has the state, and can rebuild everything.

### 2. No inbound firewall rules on customer clusters
The operator makes an **outbound** WebSocket connection to BNK Forge. This works through NAT, firewalls, and VPNs without the customer opening any inbound ports. This is critical for enterprise customers with strict network security policies.

### 3. Solves the "Build It All" persona cleanly
```
Step 1: BNK Forge creates AWS infra (VPC, EKS) via OpenTofu
        → Runs locally, no cluster needed
Step 2: EKS cluster comes up
Step 3: BNK Forge installs the operator into the new cluster
        → One helm install / kubectl apply
Step 4: Operator phones home to BNK Forge
Step 5: BNK Forge tells operator: "Deploy BNK"
Step 6: Operator deploys BNK components locally (fast, native K8s API)
Step 7: Operator continuously reports BNK health back to BNK Forge
```

### 4. Cluster problems are visible from outside
If the cluster is unhealthy, the operator's heartbeat stops or reports errors. BNK Forge shows "Cluster A: last heartbeat 5 minutes ago ⚠️" — the admin can investigate from outside the cluster.

If the operator itself dies (cluster really broken), BNK Forge shows "Cluster A: operator disconnected 🔴" — the admin knows to look at the cluster infrastructure, not BNK.

### 5. Multi-cluster is natural
Each cluster gets its own operator. They all phone home to the same BNK Forge. One dashboard, many clusters. No credential sprawl — each operator has local RBAC via its ServiceAccount.

### 6. Air-gapped/restricted networks
The operator only needs outbound HTTPS/WSS to BNK Forge. If BNK Forge runs on the customer's internal network, this works in air-gapped environments too. No cloud dependencies.

---

## The Operator: What It Is

The operator is a **single lightweight pod** that runs on each managed K8s cluster. It is NOT a Kubernetes controller in the traditional CRD-reconciliation sense. It's an **agent** that:

1. **Connects outbound** to BNK Forge via WebSocket (persistent connection with auto-reconnect)
2. **Receives commands** from BNK Forge (apply manifest, install Helm chart, scan cluster, get logs)
3. **Executes locally** using kr8s + Helm CLI (the same tools we designed for the K8s engine)
4. **Streams results** back in real-time (apply output, pod logs, events)
5. **Watches resources** continuously (BNK health, certificate status, pod readiness) and reports changes
6. **Sends heartbeat** every 30 seconds with cluster summary

### Why "operator" and not "agent"

Calling it an "operator" is natural in the K8s ecosystem. But it's really more like an agent. The key distinction:
- Traditional K8s operator: watches CRDs, runs reconciliation loops autonomously
- BNK operator: receives commands from BNK Forge, executes them, reports back

We could do both — have CRDs for declarative config AND accept imperative commands from BNK Forge. But start with the agent pattern (command-driven) because:
- Simpler to build
- Easier to debug (command → response, not eventual consistency)
- BNK Forge stays in control
- Can add CRD reconciliation later if customers want GitOps

### Size and footprint

```yaml
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 128Mi
```

The operator bundles:
- Python runtime (~30 MB)
- kr8s library (~2 MB)
- Helm CLI (~50 MB) — or call the cluster's existing Helm if available
- WebSocket client

**Total image: ~100-150 MB.** Compare to the current backend image at ~800 MB-1 GB (which includes OpenTofu, AWS CLI, Docker CLI, Infracost — none of which the operator needs).

---

## The Communication Protocol

### Connection establishment

```
1. Customer installs operator:
   helm install f5/bnk-operator \
     --set controlPlane.url=wss://bnk-forge.example.com/ws/operator \
     --set controlPlane.token=<registration-token>

2. Operator connects outbound:
   WebSocket → wss://bnk-forge.example.com/ws/operator
   Auth: Bearer <registration-token>

3. BNK Forge validates token, registers operator:
   {cluster_id: "auto-assigned", cluster_name: "from-operator-label"}

4. Operator appears in BNK Forge dashboard:
   "New cluster connected: prod-us-east-1 ● Online"
```

### Registration token

BNK Forge generates a one-time or reusable registration token:
```
POST /api/operators/registration-tokens
{
  "name": "prod-cluster-token",
  "expires_at": "2026-03-15T00:00:00Z",  // optional
  "max_uses": 1,                           // optional
  "labels": {"environment": "production", "region": "us-east-1"}
}
→ { "token": "bnk_reg_abc123..." }
```

This is similar to how Teleport, Rancher, and other multi-cluster tools handle agent registration. No need for the customer to create credentials or kubeconfigs — the operator registers itself.

### Message protocol (over WebSocket)

```
// BNK Forge → Operator (commands)
{
  "type": "command",
  "id": "cmd-123",
  "action": "apply_manifests",
  "payload": {
    "manifests": [...],
    "namespace": "f5-bnk",
    "wait_for_ready": true,
    "timeout": 300
  }
}

// Operator → BNK Forge (streaming output)
{
  "type": "output",
  "command_id": "cmd-123",
  "line": "Applying GatewayClass/bnk-gatewayclass..."
}

// Operator → BNK Forge (command result)
{
  "type": "result",
  "command_id": "cmd-123",
  "success": true,
  "outputs": {
    "gatewayclass_name": "bnk-gatewayclass",
    "gatewayclass_ready": true
  },
  "resources_created": 2,
  "duration_seconds": 3.5
}

// Operator → BNK Forge (continuous health)
{
  "type": "health",
  "timestamp": "2026-02-15T10:30:00Z",
  "cluster": {
    "kubernetes_version": "1.30.2",
    "node_count": 3,
    "nodes_ready": 3
  },
  "bnk": {
    "installed": true,
    "flo_version": "2.2.0",
    "flo_ready": true,
    "tmm_instances": 2,
    "tmm_ready": 2,
    "tmm_containers": "7/7",
    "gateway_programmed": true,
    "routes_active": 6,
    "license_status": "active",
    "license_expiry": "2027-03-15",
    "certificates": [
      {"name": "bnk-tls", "valid": true, "expires_in_days": 58}
    ]
  }
}

// Operator → BNK Forge (heartbeat)
{
  "type": "heartbeat",
  "timestamp": "2026-02-15T10:30:30Z",
  "operator_version": "1.0.0",
  "uptime_seconds": 86400,
  "commands_executed": 47,
  "last_error": null
}
```

### Reconnection and resilience

```python
class OperatorClient:
    """Runs on the customer's cluster. Connects to BNK Forge."""
    
    async def run(self):
        while True:
            try:
                async with websockets.connect(
                    self.control_plane_url,
                    extra_headers={"Authorization": f"Bearer {self.token}"},
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    self.connected = True
                    await asyncio.gather(
                        self._send_heartbeats(ws),
                        self._send_health_updates(ws),
                        self._receive_commands(ws),
                    )
            except (ConnectionClosed, ConnectionRefusedError, OSError) as e:
                self.connected = False
                logger.warning(f"Disconnected from control plane: {e}")
                await asyncio.sleep(self._backoff())  # Exponential backoff
    
    def _backoff(self):
        """1s, 2s, 4s, 8s, ... up to 60s"""
        delay = min(60, 2 ** self._reconnect_attempts)
        self._reconnect_attempts += 1
        return delay
```

---

## How This Changes the Engine Architecture

### Before (from HYBRID_ENGINE_DESIGN.md)

```
BNK Forge Backend
  └── Engine Router
       ├── OpenTofuEngine → subprocess tofu (for infra)
       └── KubernetesEngine → kr8s (direct K8s API calls)
                              ↑
                              Problem: needs kubeconfig,
                              network access to cluster
```

### After (operator architecture)

```
BNK Forge Backend
  └── Engine Router
       ├── OpenTofuEngine → subprocess tofu (for infra)
       │                     Runs locally in BNK Forge
       │
       └── OperatorEngine → sends commands via WebSocket
                             to the operator on the cluster
                             ↓
                             Operator executes kr8s/helm
                             locally inside the cluster
```

The `OperatorEngine` implements the same `DeploymentEngine` interface:

```python
class OperatorEngine(DeploymentEngine):
    """Executes K8s operations via a remote operator agent."""
    
    def __init__(self, operator_connection: OperatorWebSocket):
        self.operator = operator_connection
    
    async def apply(self, module, variables, credentials_env, on_output=None):
        """Send apply command to operator, stream results back."""
        module_def = load_module_definition(module.path)
        
        if module_def.module_type == "helm_chart":
            command = {
                "action": "install_helm",
                "chart_ref": module_def.chart_ref,
                "release_name": module_def.release_name,
                "namespace": module_def.namespace,
                "values": module_def.render_helm_values(variables),
                "timeout": module_def.timeout,
            }
        else:
            command = {
                "action": "apply_manifests",
                "manifests": module_def.render_manifests(variables),
                "wait_for_ready": True,
                "timeout": module_def.timeout,
            }
        
        # Send command, stream output
        result = await self.operator.send_command(command, on_output=on_output)
        
        return OperationResult(
            success=result["success"],
            outputs=result.get("outputs", {}),
            stdout=result.get("stdout", ""),
            resources_created=result.get("resources_created", 0),
            duration_seconds=result.get("duration_seconds", 0),
            error_message=result.get("error_message"),
        )
    
    async def get_outputs(self, module):
        """Ask operator to read outputs from cluster."""
        module_def = load_module_definition(module.path)
        result = await self.operator.send_command({
            "action": "get_outputs",
            "output_specs": {
                name: {"kind": spec.resource_kind, "name": spec.resource_name, 
                       "namespace": spec.namespace, "field_path": spec.field_path}
                for name, spec in module_def.outputs.items()
            }
        })
        return result.get("outputs", {})
    
    async def check_drift(self, module, desired_variables):
        """Ask operator to compare desired vs actual state."""
        module_def = load_module_definition(module.path)
        result = await self.operator.send_command({
            "action": "check_drift",
            "manifests": module_def.render_manifests(desired_variables),
        })
        return PlanResult(
            has_changes=result.get("has_changes", False),
            adds=result.get("adds", 0),
            changes=result.get("changes", 0),
            details=result.get("details", ""),
        )
```

### The engine router now has three engines

The operator is **one of two** first-class connectivity models. The other is **Direct mode** — BNK Forge connects to the cluster's K8s API using kubeconfig, cloud auth (EKS/AKS/GKE), or SSH tunnels. Direct mode is what we run in production today and remains fully supported for environments where the operator can't be installed (strict pod policies, air-gapped without outbound, managed K8s restrictions, compliance rules against custom agents).

```python
class EngineRouter:
    def get_engine(self, project, module) -> DeploymentEngine:
        engine_type = self._determine_engine(module)
        
        if engine_type == "opentofu":
            return self.opentofu_engine  # Runs locally
        
        if engine_type == "kubernetes":
            cluster = project.cluster
            
            # Model A: Operator — preferred when available
            operator = self._get_operator_for_project(project)
            if operator and operator.connected:
                return OperatorEngine(operator)
            
            # Model B: Direct — first-class alternative for restricted environments
            # Uses kubeconfig, cloud auth (EKS/AKS/GKE), or SSH tunnel
            kubeconfig = self._resolve_kubeconfig(project)
            if kubeconfig:
                return KubernetesEngine(kubeconfig)
            
            raise EngineError(
                "No connectivity to cluster. Either install the BNK operator "
                "or configure credentials (kubeconfig, cloud auth, SSH tunnel)."
            )
```

### Three execution modes

| Mode | Engine | When Used |
|---|---|---|
| **Local OpenTofu** | `OpenTofuEngine` | Creating cloud infrastructure (VPC, EKS, AKS, GKE) |
| **Remote Operator** | `OperatorEngine` | K8s/BNK operations on clusters with operator installed (Model A) |
| **Direct K8s** | `KubernetesEngine` | K8s/BNK operations via kubeconfig, cloud auth, or SSH tunnel (Model B) |

Both Model A and Model B are **production-grade**. The operator is preferred when the environment allows it (no credential management, continuous health, outbound-only networking). Direct mode is the right choice when the operator can't be installed or when quick setup matters (SE demos, POCs, air-gapped on-prem).

---

## The Operator Pod

### What it runs

```python
# bnk-operator/main.py

import asyncio
import kr8s
from helm_executor import HelmExecutor
from cluster_scanner import ClusterScanner
from health_watcher import HealthWatcher
from control_plane_client import ControlPlaneClient

async def main():
    # Connect to local cluster (ServiceAccount auth — no kubeconfig needed)
    k8s_api = await kr8s.asyncio.Api()
    
    # Initialize components
    helm = HelmExecutor()
    scanner = ClusterScanner(k8s_api)
    health = HealthWatcher(k8s_api)
    
    # Connect to BNK Forge control plane
    client = ControlPlaneClient(
        url=os.environ["CONTROL_PLANE_URL"],
        token=os.environ["REGISTRATION_TOKEN"],
    )
    
    # Register command handlers
    client.on("apply_manifests", lambda cmd: apply_manifests(k8s_api, cmd))
    client.on("install_helm", lambda cmd: helm.install(cmd))
    client.on("uninstall_helm", lambda cmd: helm.uninstall(cmd))
    client.on("scan_cluster", lambda cmd: scanner.scan())
    client.on("get_outputs", lambda cmd: get_outputs(k8s_api, cmd))
    client.on("get_pod_logs", lambda cmd: get_pod_logs(k8s_api, cmd))
    client.on("check_drift", lambda cmd: check_drift(k8s_api, cmd))
    client.on("get_resources", lambda cmd: get_resources(k8s_api, cmd))
    
    # Run everything concurrently
    await asyncio.gather(
        client.connect(),                    # WebSocket to control plane
        health.watch_and_report(client),     # Continuous health reporting
    )

async def apply_manifests(api, command):
    """Apply manifests using kr8s — same logic as KubernetesEngine."""
    results = []
    for manifest in command["manifests"]:
        kind = manifest["kind"]
        name = manifest["metadata"]["name"]
        ns = manifest["metadata"].get("namespace", "default")
        
        yield {"type": "output", "line": f"Applying {kind}/{name}..."}
        
        await api.apply(manifest, force=True)
        results.append({"kind": kind, "name": name, "action": "applied"})
        
        yield {"type": "output", "line": f"  ✓ {kind}/{name} applied"}
    
    # Wait for readiness if requested
    if command.get("wait_for_ready"):
        for manifest in command["manifests"]:
            kind = manifest["kind"]
            name = manifest["metadata"]["name"]
            ns = manifest["metadata"].get("namespace", "default")
            
            yield {"type": "output", "line": f"Waiting for {kind}/{name}..."}
            try:
                obj = await api.get(kind, name, namespace=ns)
                await obj.wait("condition=Ready", timeout=command.get("timeout", 300))
                yield {"type": "output", "line": f"  ✓ {kind}/{name} ready"}
            except Exception as e:
                yield {"type": "output", "line": f"  ✗ {kind}/{name}: {e}"}
                return {"success": False, "error_message": str(e)}
    
    return {"success": True, "resources_created": len(results)}
```

### RBAC — What the operator needs

```yaml
# The operator's ServiceAccount permissions
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: bnk-operator
rules:
  # BNK management
  - apiGroups: ["bnk.f5.com", "f5.com"]
    resources: ["*"]
    verbs: ["*"]
  # Gateway API
  - apiGroups: ["gateway.networking.k8s.io"]
    resources: ["gateways", "gatewayclasses", "httproutes", "grpcroutes"]
    verbs: ["*"]
  # Core resources (namespaces, secrets, configmaps)
  - apiGroups: [""]
    resources: ["namespaces", "secrets", "configmaps", "services", "pods", "pods/log", "events", "nodes"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  # Workloads (for health monitoring)
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "daemonsets", "replicasets"]
    verbs: ["get", "list", "watch", "update", "patch"]
  # cert-manager
  - apiGroups: ["cert-manager.io"]
    resources: ["certificates", "issuers", "clusterissuers"]
    verbs: ["get", "list", "watch", "create", "update"]
  # Helm (needs secrets for release tracking)
  - apiGroups: [""]
    resources: ["secrets"]
    verbs: ["get", "list", "watch", "create", "update", "delete"]
    # Helm stores releases as secrets in the release namespace
  # Storage
  - apiGroups: ["storage.k8s.io"]
    resources: ["storageclasses"]
    verbs: ["get", "list", "watch", "create"]
  # CRDs (for Multus NADs, SR-IOV)
  - apiGroups: ["k8s.cni.cncf.io"]
    resources: ["network-attachment-definitions"]
    verbs: ["get", "list", "watch", "create", "update"]
  # Cluster info
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apiextensions.k8s.io"]
    resources: ["customresourcedefinitions"]
    verbs: ["get", "list", "watch"]
```

### Health Watcher — Continuous monitoring

```python
class HealthWatcher:
    """Continuously watches BNK resources and reports status."""
    
    def __init__(self, api: kr8s.asyncio.Api):
        self.api = api
        self._last_health = None
    
    async def watch_and_report(self, client: ControlPlaneClient):
        """Run health checks on a loop, report changes."""
        while True:
            try:
                health = await self._check_health()
                
                # Only send if changed (reduce noise)
                if health != self._last_health:
                    await client.send({"type": "health", **health})
                    self._last_health = health
                else:
                    # Still send periodic full report every 5 minutes
                    pass
                
                await asyncio.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error(f"Health check failed: {e}")
                await asyncio.sleep(10)
    
    async def _check_health(self) -> dict:
        """Comprehensive BNK health check."""
        
        # FLO operator
        flo = await self._check_deployment("f5-bnk", "f5-lifecycle-operator")
        
        # TMM instances
        tmm_pods = await self.api.get("pods", namespace="f5-bnk",
                                       label_selector="app=f5-tmm")
        tmm_ready = sum(1 for p in tmm_pods if self._pod_ready(p))
        tmm_containers = self._container_summary(tmm_pods)
        
        # Gateway status
        gateways = await self._safe_get("gateways.gateway.networking.k8s.io",
                                         namespace="f5-bnk")
        gateway_status = [
            {
                "name": gw.metadata.name,
                "programmed": self._has_condition(gw, "Programmed"),
                "listeners": len(gw.spec.get("listeners", [])),
            }
            for gw in gateways
        ]
        
        # Routes
        routes = await self._safe_get("httproutes.gateway.networking.k8s.io",
                                       namespace="f5-bnk")
        
        # Certificates
        certs = await self._check_certificates()
        
        # License (check FLO annotations/status)
        license_status = await self._check_license()
        
        # Nodes
        nodes = await self.api.get("nodes")
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "cluster": {
                "kubernetes_version": (await self.api.version()).git_version,
                "node_count": len(nodes),
                "nodes_ready": sum(1 for n in nodes if self._node_ready(n)),
            },
            "bnk": {
                "installed": flo is not None,
                "flo_version": flo.get("version") if flo else None,
                "flo_ready": flo.get("ready", False) if flo else False,
                "tmm_total": len(tmm_pods),
                "tmm_ready": tmm_ready,
                "tmm_containers": tmm_containers,
                "gateways": gateway_status,
                "routes_count": len(routes),
                "certificates": certs,
                "license": license_status,
            }
        }
```

---

## Installation Flow

### From the customer's perspective

```
1. BNK Forge UI: Go to "Clusters" → "Connect Cluster"

2. BNK Forge generates a registration command:
   ┌──────────────────────────────────────────────────────────┐
   │  Connect Your Cluster                                     │
   │                                                           │
   │  Run this on your cluster:                                │
   │                                                           │
   │  ┌────────────────────────────────────────────────────┐  │
   │  │ helm install bnk-operator f5/bnk-operator \        │  │
   │  │   --namespace bnk-operator --create-namespace \    │  │
   │  │   --set controlPlane.url=wss://10.0.1.50:2650 \   │  │
   │  │   --set controlPlane.token=bnk_reg_abc123def456    │  │
   │  └────────────────────────────────────────────────────┘  │
   │                                                           │
   │  Or apply directly:                                       │
   │  ┌────────────────────────────────────────────────────┐  │
   │  │ kubectl apply -f https://bnk-forge:2650/api/       │  │
   │  │   operators/install/bnk_reg_abc123def456.yaml      │  │
   │  └────────────────────────────────────────────────────┘  │
   │                                                           │
   │  Waiting for operator to connect...  ◌ (spinning)        │
   │                                                           │
   └──────────────────────────────────────────────────────────┘

3. Customer runs the command on their cluster

4. Operator starts, connects outbound to BNK Forge

5. BNK Forge UI updates:
   ┌──────────────────────────────────────────────────────────┐
   │  ✓ Cluster Connected!                                     │
   │                                                           │
   │  Name: prod-us-east-1                                     │
   │  Kubernetes: v1.30.2                                      │
   │  Nodes: 5 (all ready)                                     │
   │  Operator: v1.0.0 ● Connected                             │
   │                                                           │
   │  Scanning prerequisites...                                │
   │                                                           │
   │  ✓ cert-manager v1.16.1                                   │
   │  ✓ Multus CNI                                             │
   │  ✓ HugePages: 4Gi per node                                │
   │  ✗ SR-IOV device plugin (not detected)                    │
   │                                                           │
   │  [Install SR-IOV →]  [Deploy BNK Anyway →]                │
   └──────────────────────────────────────────────────────────┘
```

### For the "Build It All" persona

```
1. BNK Forge creates infrastructure via OpenTofu (locally)
2. EKS cluster comes up
3. BNK Forge automatically installs operator into new cluster
   (using the EKS kubeconfig from OpenTofu outputs)
4. Operator connects back to BNK Forge
5. BNK Forge deploys BNK via operator
6. All fully automated — user clicks "Deploy" once
```

The operator install step (step 3) can use the existing `KubernetesEngine` (direct kr8s with kubeconfig from EKS) to bootstrap just the operator. Then all subsequent operations go through the operator.

---

## BNK Forge Stays What It Is

This is the key insight: **BNK Forge doesn't need to change its own deployment model.** It continues to run as:

| Environment | How to Run BNK Forge |
|---|---|
| **SE laptop/demo** | `docker compose up` (current, keeps working) |
| **Shared server** | `docker compose up` on a VM (current, keeps working) |
| **Single engineer** | `docker run f5/bnk-forge` (future slim image) |
| **Team/enterprise** | Docker Compose or K8s deployment for BNK Forge itself (optional) |

The difference is: BNK Forge no longer needs direct network access to every cluster it manages. The operator handles that. BNK Forge only needs:
- Outbound internet (for OpenTofu to talk to cloud APIs)
- The operator's inbound WebSocket connection

---

## How This Changes the Credential Story

### What credentials BNK Forge needs

| Credential | For What | Stored Where |
|---|---|---|
| **Cloud creds** (AWS/Azure/GCP) | OpenTofu infra creation | BNK Forge credential templates (existing) |
| **Registration tokens** | Operator registration | BNK Forge database (new) |

### What credentials the OPERATOR needs

| Credential | For What | Stored Where |
|---|---|---|
| **K8s ServiceAccount** | All K8s API operations | K8s RBAC (automatic — no storage needed) |
| **Helm** | Chart installs | Uses ServiceAccount auth (no separate creds) |
| **F5 FAR pull secret** | Pull BNK images | K8s Secret in cluster (deployed by operator) |
| **F5 JWT license** | BNK license activation | K8s Secret in cluster (deployed by operator) |

**The operator doesn't need kubeconfigs, cloud credentials, or external auth.** It runs inside the cluster with a ServiceAccount. BNK Forge sends the F5-specific secrets (FAR key, JWT) as part of the deploy command, and the operator creates K8s Secrets locally.

This eliminates the entire credential storage problem for K8s access. No kubeconfigs stored in BNK Forge's database. No credential refresh for K8s tokens. No EKS token expiry concerns. The operator just uses its ServiceAccount.

---

## What We Build and When

### Phase 1: Foundation (current plan — no change)
Quick wins, bug fixes, auth foundation.

### Phase 2: K8s Engine + Module Definitions (mostly no change)
Build kr8s-based engine and Python module definitions. These are used by BOTH the operator AND the direct engine (for dev/testing).

### Phase 3: Operator (NEW)
```
bnk-operator/
  Dockerfile                 # Slim Python + kr8s + helm
  main.py                    # Entry point — connect to control plane
  command_handlers.py         # Apply manifests, install Helm, scan, etc.
  health_watcher.py           # Continuous BNK health monitoring
  control_plane_client.py     # WebSocket client with auto-reconnect
  cluster_scanner.py          # Reuse from backend/services/cluster_scanner.py
  charts/
    bnk-operator/            # Helm chart for installing the operator
      Chart.yaml
      values.yaml
      templates/
        deployment.yaml
        serviceaccount.yaml
        clusterrole.yaml
        clusterrolebinding.yaml
```

Backend additions:
```
backend/
  services/
    execution/
      operator_engine.py      # NEW — DeploymentEngine via WebSocket
    operator_registry.py       # NEW — Track connected operators
  routes/
    operators.py               # NEW — Registration tokens, operator status
  websocket/
    operator_handler.py        # NEW — WebSocket endpoint for operators
```

### Phase 4: Product Polish
BNK Health Dashboard (powered by operator health reports), deploy wizard, alerting.

### Phase 5: Day 2 Operations
BNK upgrade, backup/restore, config export — all executed via operator commands.

---

## Comparison with Prior Architecture

| Aspect | "BNK Manager on K8s" (prior) | "BNK Forge + Operator" (this) |
|---|---|---|
| **Cluster independence** | ✗ Dies with cluster | ✓ Runs independently |
| **Build It All persona** | ✗ Chicken-and-egg | ✓ Creates infra first, installs operator after |
| **Network requirements** | Inbound access to K8s API | Operator connects outbound only |
| **Multi-cluster** | Complex (hub cluster needed) | Natural (each cluster gets an operator) |
| **Credential management** | Kubeconfigs for every cluster | No kubeconfigs — operator uses ServiceAccount |
| **Cluster troubleshooting** | ✗ Tool down when cluster down | ✓ Can see "operator disconnected" from outside |
| **BNK Forge deployment** | Must run on K8s | Runs anywhere (Docker, VM, laptop) |
| **Operator footprint** | N/A | ~128 MB RAM, ~100 MB image |
| **Air-gapped support** | Requires K8s ingress | Outbound WSS only |
