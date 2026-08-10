# Benchmark Target Clusters & Proxy Deployment — Design Document

**Date**: 2026-03-12
**Status**: Proposed
**Author**: Agent (reviewed by J. Lucia)
**Relates to**: ai-perf DESIGN.md Phase 4, bnk-forge-v2 Benchmark Dashboard

### Related Repos

| Repo | Path | Role |
|------|------|------|
| **ai-perf** | `/Users/j.lucia/Code/ai-perf/` | Client CLI + agent daemon + collector |
| **bnk-forge-v2** | `/Users/j.lucia/Code/github/bnk-forge-v2/` | Server dashboard + API + proxy orchestration |

### Key ai-perf Source Files (the client contract)

| File | Purpose |
|------|---------|
| `src/aiperf/cli.py` | CLI entry point — `aiperf run`, `aiperf agent`, `aiperf compare`, `aiperf profiles` |
| `src/aiperf/schemas/config.py` | `RunConfig` + `BurstPhaseConfig` — what the agent expects to receive |
| `src/aiperf/schemas/results.py` | `BenchmarkResult` — what the agent pushes to Forge via `POST /api/benchmarks/results` |
| `src/aiperf/collectors/forge.py` | `ForgeCollector` — HTTP client that calls Forge API (`push_result`, `pull_config`, `stream_progress`) |
| `src/aiperf/agent/client.py` | `AgentClient` — WebSocket agent that registers via `POST /api/benchmarks/agents`, connects via `WS /api/benchmarks/agents/{id}` |
| `src/aiperf/benchmarks/profiles.py` | Built-in profiles: `quick` (500 req), `standard` (25k), `stress` (50k), `soak` (100k) |
| `configs/proxy-compare.json` | Pre-built burst config for proxy comparison (7 phases, run 4x with different `--proxy`) |

---

## 1. Problem Statement

The Forge benchmark dashboard can **receive and display** results, but has no knowledge of
**what is being tested** or **how the test infrastructure is configured**. Today:

- Agents push results that say "proxy=envoy, model=gpt-oss-120b" but Forge doesn't know
  which cluster the proxy is on, whether it's deployed correctly, or what the endpoint URL is.
- Users must manually deploy proxies (Envoy, Nginx, HAProxy, F5) to K8s clusters, then
  manually configure agents to test through them. There's no orchestration.
- The "All proxies" dropdown on the Runs tab filters by reported proxy names, but Forge has
  no concept of which proxies are actually available on which clusters.

### What users want

> "I have a K8s cluster with vLLM on GPU nodes. I want to compare Envoy vs Nginx vs direct
> for LLM inference traffic. Forge should deploy the proxies, tell me when they're ready,
> and let me trigger the test from the dashboard."

---

## 2. Design Goals

1. **Benchmark Targets** — Forge knows which K8s clusters are being used for performance
   testing, and can reference existing `KubernetesCluster` records.
2. **Proxy Deployment** — Forge deploys proxy configurations (Envoy, Nginx, HAProxy, F5
   BIG-IP) to target clusters via Helm, then signals readiness.
3. **Self-contained workflow** — The entire test lifecycle is manageable from the Forge UI:
   configure target → deploy proxy → signal ready → agent runs test → results flow back.
4. **Agent autonomy preserved** — Agents still run independently and report which proxy they
   tested through. The same agent tests multiple proxies on the same cluster.

### Non-goals (for now)

- Automated vLLM deployment (users bring their own LLM endpoint)
- Multi-cluster distributed load testing (one agent → one cluster per run)
- Full GitOps proxy management (deploy + teardown, not ongoing lifecycle)

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Forge Dashboard                                                     │
│                                                                      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────────────┐ │
│  │  Benchmarks   │   │   Targets    │   │    Proxy Deployments     │ │
│  │  (existing)   │   │   (new)      │   │    (new)                 │ │
│  │              │   │              │   │                          │ │
│  │  Runs        │   │  Cluster +   │   │  envoy → cluster-1      │ │
│  │  Compare     │   │  namespace + │   │  nginx → cluster-1      │ │
│  │  Configs     │   │  LLM endpoint│   │  haproxy → cluster-1    │ │
│  │  Agents      │   │  selection   │   │  Status: ready/deploying │ │
│  └──────────────┘   └──────┬───────┘   └────────────┬─────────────┘ │
│                            │                        │                │
│                            ▼                        ▼                │
│  ┌──────────────────────────────────────────────────────────────────┐│
│  │  Backend Services                                                ││
│  │  BenchmarkTargetService  — CRUD targets, validate connectivity   ││
│  │  ProxyDeployService      — Helm install/uninstall proxies        ││
│  │  BenchmarkService        — existing run/result/agent management  ││
│  └──────────────────────────────────────────────────────────────────┘│
└──────────────────────┬──────────────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
┌─────────────────┐    ┌─────────────────────────────────────────────┐
│  Test Client     │    │  K8s Cluster (target under test)             │
│  (aiperf agent)  │    │                                              │
│                  │    │  ┌─────────────────┐  ┌──────────────────┐  │
│  Targets proxy   │───►│  │ Proxy (deployed  │  │ vLLM / LLM      │  │
│  endpoint URL    │    │  │ by Forge via Helm)│──│ endpoint (user-  │  │
│  from Forge      │    │  │ Envoy/Nginx/etc  │  │ managed)         │  │
│                  │    │  └─────────────────┘  └──────────────────┘  │
└─────────────────┘    └─────────────────────────────────────────────┘
```

---

## 4. Data Model

### 4.1 BenchmarkTarget (new table)

A **target** is a K8s cluster + LLM endpoint that benchmarks are run against.
References the existing `KubernetesCluster` model.

```python
class BenchmarkTarget(Base):
    __tablename__ = "benchmark_targets"

    id              = Column(Integer, primary_key=True)
    name            = Column(String(255), unique=True, nullable=False)
    description     = Column(Text, nullable=True)

    # Link to existing K8s cluster
    cluster_id      = Column(Integer, ForeignKey("kubernetes_clusters.id"), nullable=False)

    # LLM endpoint details (what the proxy will forward to)
    llm_base_url    = Column(String(500), nullable=False)  # e.g. http://vllm-svc.default:8000
    llm_model       = Column(String(255), nullable=False)  # e.g. openai/gpt-oss-120b
    llm_namespace   = Column(String(255), default="default")
    llm_endpoint    = Column(String(255), default="/v1/chat/completions")

    # Proxy routing
    proxy_namespace = Column(String(255), default="perf-proxies")  # NS for proxy deployments

    # Status
    status          = Column(String(50), default="active")  # active|inactive|error
    last_validated  = Column(DateTime(timezone=True))
    validation_msg  = Column(Text, nullable=True)

    # Metadata
    tags            = Column(JSON, default={})
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    cluster         = relationship("KubernetesCluster")
    proxy_deploys   = relationship("ProxyDeployment", back_populates="target")
```

### 4.2 ProxyDeployment (new table)

A **proxy deployment** is a specific proxy (envoy, nginx, etc.) deployed to a target
cluster, ready to receive traffic.

```python
class ProxyDeploymentStatus(str, enum.Enum):
    pending     = "pending"
    deploying   = "deploying"
    ready       = "ready"
    failed      = "failed"
    uninstalling = "uninstalling"
    uninstalled = "uninstalled"

class ProxyDeployment(Base):
    __tablename__ = "proxy_deployments"

    id              = Column(Integer, primary_key=True)
    target_id       = Column(Integer, ForeignKey("benchmark_targets.id", ondelete="CASCADE"))
    proxy_type      = Column(String(50), nullable=False)  # envoy|nginx|haproxy|f5-bnk
    
    # Helm release info
    helm_release    = Column(String(255))          # e.g. perf-envoy-cluster1
    helm_chart      = Column(String(255))          # e.g. envoy-gateway/gateway-helm
    helm_version    = Column(String(50))           # Chart version
    helm_values     = Column(JSON, default={})     # Custom values.yaml overrides
    
    # Deployed endpoint (populated after successful deploy)
    proxy_url       = Column(String(500))          # e.g. http://envoy-proxy.perf-proxies:10080
    external_url    = Column(String(500))          # NodePort/LB URL for external agents
    
    # Status
    status          = Column(String(50), default=ProxyDeploymentStatus.pending)
    status_message  = Column(Text)
    deployed_at     = Column(DateTime(timezone=True))
    
    # Metadata
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    target          = relationship("BenchmarkTarget", back_populates="proxy_deploys")
```

### 4.3 Updated BenchmarkRun (add target_id FK)

```python
# Add to existing BenchmarkRun model:
target_id = Column(Integer, ForeignKey("benchmark_targets.id"), nullable=True)
proxy_deployment_id = Column(Integer, ForeignKey("proxy_deployments.id"), nullable=True)
```

This links a run to the specific target and proxy deployment it tested, while remaining
optional (runs can still be pushed without a target, for backward compatibility).

---

## 5. API Endpoints

### 5.1 Benchmark Targets

```
GET    /api/benchmarks/targets              — list all targets
POST   /api/benchmarks/targets              — create target (cluster_id + LLM details)
GET    /api/benchmarks/targets/{id}         — target detail + proxy deployments
PUT    /api/benchmarks/targets/{id}         — update target
DELETE /api/benchmarks/targets/{id}         — delete target (cascades proxy deploys)
POST   /api/benchmarks/targets/{id}/validate — test connectivity to LLM endpoint
```

### 5.2 Proxy Deployments

```
POST   /api/benchmarks/targets/{id}/proxies           — deploy a proxy to target
GET    /api/benchmarks/targets/{id}/proxies            — list proxy deployments for target
GET    /api/benchmarks/targets/{id}/proxies/{proxy_id} — deployment detail + status
DELETE /api/benchmarks/targets/{id}/proxies/{proxy_id} — uninstall proxy (Helm uninstall)
POST   /api/benchmarks/targets/{id}/proxies/{proxy_id}/redeploy — redeploy after config change
```

### 5.3 Proxy Deploy Request Schema

```python
class ProxyDeployRequest(BaseModel):
    proxy_type: Literal["envoy", "nginx", "haproxy", "f5-bnk"]
    helm_values: dict | None = None   # Custom overrides (optional)
    
    # Optional: override chart defaults
    helm_chart: str | None = None
    helm_version: str | None = None
```

---

## 6. Proxy Helm Charts

Each proxy type has a default Helm chart and values template that configures it as an
LLM inference reverse proxy (forwards to the target's `llm_base_url`).

### 6.1 Chart Registry (built-in defaults)

| Proxy | Default Chart | Notes |
|-------|--------------|-------|
| Envoy | `envoy/gateway-helm` or custom chart | Gateway API + HTTPRoute to LLM svc |
| Nginx | `ingress-nginx/ingress-nginx` or custom | Ingress + backend pointing to LLM svc |
| HAProxy | `haproxytech/haproxy` | ConfigMap with LLM backend |
| F5 BIG-IP | F5 CIS chart | AS3 declaration for LLM pool |

### 6.2 Values Template Pattern

Each proxy deployment generates a `values.yaml` that:
1. Routes traffic from a known port to the target's `llm_base_url`
2. Deploys into the target's `proxy_namespace`
3. Exposes a NodePort or LoadBalancer for external agent access
4. Includes sensible defaults for LLM traffic (large body sizes, long timeouts)

```yaml
# Example: generated envoy values for target "cluster-1"
gateway:
  listeners:
    - port: 10080
      protocol: HTTP
routes:
  - match:
      prefix: /v1/
    route:
      cluster: llm-backend
clusters:
  - name: llm-backend
    endpoints:
      - address: vllm-svc.default
        port: 8000
    timeout: 600s
    maxRequestBodyBytes: 10485760  # 10MB for large prompts
```

### 6.3 Deployment Flow

```
1. User clicks "Deploy Envoy" on target
2. Backend creates ProxyDeployment record (status=pending)
3. Celery task:
   a. Generate values.yaml from template + target config
   b. helm install {release} {chart} -n {proxy_namespace} -f values.yaml
   c. Wait for pods ready (kubectl rollout status)
   d. Discover service URL (NodePort/LB)
   e. Update ProxyDeployment (status=ready, proxy_url=..., external_url=...)
4. Frontend polls/WS until status=ready
5. User sees "Envoy ready at http://10.176.11.91:31080" → can trigger agent test
```

---

## 7. Frontend Design

### 7.1 New "Targets" Tab in Benchmarks Page

Added alongside existing tabs: Runs | Detail | Compare | Configs | **Targets** | Agents

**Target list view:**
- Table: Name, Cluster, LLM Model, LLM URL, # Proxies Deployed, Status
- "Add Target" button → modal/drawer

**Target detail view** (click into a target):
- Header: target name, cluster info, LLM endpoint
- Proxy deployment cards (one per proxy type):
  - Status badge (pending/deploying/ready/failed)
  - Proxy URL (copyable)
  - Deploy/Undeploy buttons
  - "Test Now" button (triggers agent run against this proxy)
- "Deploy All Common Proxies" quick action (envoy + nginx + haproxy + nodeport)
- Validation section: "Test LLM Connectivity" button

### 7.2 Integration with Existing Tabs

- **Runs tab**: Add "Target" column showing which target a run was against
- **Compare tab**: Filter by target to compare all proxies on the same cluster
- **Agents tab**: Show which target(s) an agent has been used with

### 7.3 Remove DPU Infrastructure from K8s Page

The DPU Infrastructure button in `KubernetesV2.tsx` is a redirect to Fleet and shows
nothing K8s-specific. Remove it to reduce noise. DPF lives in Fleet only.

---

## 8. Agent Integration

### 8.1 ai-perf Client Architecture (reference)

The ai-perf client has three modes of operation, all defined in `src/aiperf/cli.py`:

**Manual CLI run** (`aiperf run`):
```bash
# Run benchmark directly, push results to Forge
aiperf run \
    --base-url http://10.176.11.91:31080 \
    --model openai/gpt-oss-120b \
    --proxy envoy \
    --profile standard \
    --collector https://10.176.11.91
```
- Builds a `RunConfig` (from `schemas/config.py`)
- Executes via `BurstBenchmark.run()` (from `benchmarks/burst.py`)
- Saves result locally via `FileCollector` (from `collectors/file.py`)
- Pushes to Forge via `ForgeCollector.push_result()` → `POST /api/benchmarks/results`

**Agent daemon** (`aiperf agent`):
```bash
# Register with Forge, await remote triggers
aiperf agent --forge-url https://10.176.11.91 --name loadgen-01
```
- `AgentClient._register()` → `POST /api/benchmarks/agents` (registers machine)
- `AgentClient._connect_and_serve()` → `WS /api/benchmarks/agents/{id}` (outbound WebSocket)
- Heartbeats every 30s, exponential backoff reconnect
- Receives `{"type": "run", "config": {...RunConfig...}}` commands from Forge
- Executes same `BurstBenchmark.run()` as CLI mode
- Streams progress over WS, pushes final result via `ForgeCollector.push_result()`

**Config pull from Forge** (`aiperf run --from-forge`):
```bash
# Pull a saved RunConfig from Forge, then run it
aiperf run --from-forge https://10.176.11.91/api/benchmarks/configs/3 \
    --base-url http://proxy-endpoint:8080
```
- `ForgeCollector.pull_config()` → `GET /api/benchmarks/configs/{id}`
- Returns `RunConfig` from the saved config's `config_json` field
- CLI args override pulled config values (base_url, model, proxy)

### 8.2 How Agents Use Targets (new with this design)

When a user triggers a run from the Forge UI against a target:
1. User selects: target + proxy deployment + agent
2. Forge constructs a `RunConfig` (matching `ai-perf/src/aiperf/schemas/config.py`) with:
   - `base_url` = proxy deployment's `external_url`
   - `proxy` = proxy deployment's `proxy_type`
   - `model` = target's `llm_model`
   - `endpoint` = target's `llm_endpoint`
3. Forge sends `{"type": "run", "config": {...}, "run_id": "..."}` to agent via WebSocket
4. Agent's `_handle_run()` validates the RunConfig, then `_execute_run()` runs the benchmark
5. Agent pushes result via `ForgeCollector.push_result()` → `POST /api/benchmarks/results`
6. Results are linked to the target and proxy deployment via `target_id` and `proxy_deployment_id`

### 8.3 Manual Agent Runs (backward compatible)

Agents can still push results without a target. The `ForgeCollector` only calls
`POST /api/benchmarks/results` with the `BenchmarkResult` JSON — it doesn't know
or care about targets. The Forge backend extracts `proxy`, `model`, and `base_url`
from the result and creates/updates the `BenchmarkRun` record.

```bash
# Manual run — no target, no agent daemon, just push results
aiperf run --base-url http://10.176.11.91:31080 --model gpt-oss-120b --proxy envoy \
    --collector https://10.176.11.91
```
These runs appear in the Runs tab without a target link.

### 8.4 Built-in Profiles (reference from ai-perf)

The agent and CLI support these built-in profiles (`src/aiperf/benchmarks/profiles.py`):

| Profile | Requests | Phases | Use Case |
|---------|----------|--------|----------|
| `quick` | 500 | 3 | Smoke testing — validate endpoint is reachable |
| `standard` | 25,000 | 11 | Production burst pattern (default) |
| `stress` | 50,000 | 7 | High-load with extended bursts and large prompts |
| `soak` | 100,000 | 5 | Long-running sustained load with mild bursts |

Additionally, `configs/proxy-compare.json` is a pre-built 7-phase config designed
specifically for proxy comparison: run it 4 times with `--proxy envoy`, `--proxy nginx`,
`--proxy haproxy`, `--proxy nodeport`, then compare results in Forge.

---

## 9. Agents Tab UX Improvements (Commit 1 — immediate)

Independent of the target/proxy work, the Agents tab needs these fixes.
All content references real code from `ai-perf/src/aiperf/`.

### 9.1 Better Code Block Styling
- Replace inline `<code>` with proper styled code blocks (dark bg, monospace, high contrast)
- Click-to-copy button on each command
- Dynamic `--forge-url` using `window.location.origin` (shows actual IP user is on)

### 9.2 How-To Section (always visible, not just empty state)

**Section 1: aiperf — One-Shot Run (push results to Forge)**
- Syntax from `cli.py` `run` command with real flags
- `--collector FORGE_URL` pushes BenchmarkResult to `POST /api/benchmarks/results`
- Show all key flags: `--base-url`, `--model`, `--proxy`, `--profile`, `--collector`
- List available profiles: quick (500), standard (25k), stress (50k), soak (100k)

**Section 2: aiperf agent — Persistent Daemon**
- Syntax from `cli.py` `agent` command
- Registers via `POST /api/benchmarks/agents`, connects via WebSocket
- Awaits remote triggers from Forge, auto-reconnects on disconnect
- Show flags: `--forge-url`, `--name`, `--tags`, `--api-key`, `--dataset`

**Section 3: aiperf compare — Local Comparison**
- `aiperf compare result1.json result2.json` for terminal-based comparison
- Supports both new BenchmarkResult format and legacy burst_driver.py output

**Section 4: Result JSON Format**
- Key fields from `schemas/results.py` BenchmarkResult:
  result_id, labels (proxy, model, base_url), latency (LatencyStats),
  throughput (ThroughputStats), phases (per-phase breakdown), timeline (optional)
- This is what `POST /api/benchmarks/results` expects

**Section 5: llm-bench (future)**
- Placeholder — second engine, same result format, differentiated by `tool` field

### 9.3 Dynamic Forge IP
- All example commands use `window.location.origin` for `--forge-url` and `--collector`
- No hardcoded hostnames or DNS — always the IP/hostname the user is currently on
- If user is at `https://10.176.11.91`, commands show `--forge-url https://10.176.11.91`

### 9.4 Downloadable Config Templates
- **"Download RunConfig Template"** — JSON matching `ai-perf/schemas/config.py` RunConfig
  with base_url, model, proxy, phases, total_requests, etc.
- **"Download Proxy Compare Config"** — The `configs/proxy-compare.json` 7-phase burst
  config designed for running the same test across multiple proxies
- **"Download Result JSON Schema"** — Documents the BenchmarkResult format so users
  building custom tools know what to push to `POST /api/benchmarks/results`
- All downloads are generated in-browser (Blob URLs), no server round-trip needed

---

## 10. Implementation Phases

### Phase 4a: Agents Tab UX (this commit)
1. Rewrite `AgentsTab` component with better styling
2. Add how-to sections with tool docs
3. Dynamic Forge IP via `window.location`
4. Config template download buttons
5. Remove DPU Infrastructure from K8s page

### Phase 4b: Benchmark Targets (next commit)
1. New DB models: `BenchmarkTarget`, `ProxyDeployment`
2. Alembic migration
3. New schemas: target CRUD, proxy deploy request/response
4. New service: `BenchmarkTargetService`
5. New routes: `/api/benchmarks/targets/...`
6. Frontend: Targets tab

### Phase 4c: Proxy Deployment (following commit)
1. Proxy Helm chart templates (envoy, nginx, haproxy)
2. `ProxyDeployService` with Helm install/uninstall
3. Celery tasks for async deployment + status polling
4. Frontend: deploy buttons, status indicators, proxy URLs
5. Integration: "Test Now" button triggers agent run

### Phase 4d: Orchestration Polish
1. "Quick Test" wizard: pick cluster → deploy proxies → run all → compare
2. Teardown: uninstall all proxies after comparison
3. Scheduled comparison runs
4. Export comparison reports

---

## 11. Open Questions

1. **F5 BIG-IP proxy**: Is this deployed via CIS (F5 Container Ingress Services) Helm chart,
   or is the BIG-IP external and we just point the agent at it?
2. **LLM endpoint validation**: Should we actually send a test prompt to validate, or just
   check HTTP connectivity to the base URL?
3. **Proxy health checks**: After Helm install, how long do we wait? Should we actively
   probe the proxy endpoint before marking as "ready"?
4. **Agent-to-proxy networking**: If the agent is external to the cluster, we need NodePort
   or LoadBalancer. Should Forge auto-detect which to use based on cluster type?
5. **Chart customization UI**: Do we need a form for custom Helm values, or is the JSON
   editor sufficient?
