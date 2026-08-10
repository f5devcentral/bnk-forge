# Tier 1 Response Model Coverage Plan — API-CONTRACT-002

> Concrete, endpoint-by-endpoint plan to add explicit `response_model` to every Tier 1 endpoint. Builds on the tiering from API-CONTRACT-001.

Status: **Accepted** | Created: 2026-03-27

---

## Problem

From the [Endpoint Contract Tiering](ENDPOINT_CONTRACT_TIERING.md), Tier 1 coverage is **52%** — the routes that matter most have the worst contract coverage. Of 54 Tier 1 endpoints, 26 lack `response_model`. In most cases, the Pydantic schemas already exist in `backend/schemas/` but are not wired to routes.

---

## Current State Summary

| Domain | Tier 1 Endpoints | Covered | Gap | Schema Status |
|--------|:----------------:|:-------:|:---:|:-------------|
| Auth | 7 | 7 | 0 | ✅ Complete |
| Cluster Management | 10 | 0 | 10 | ⚠️ Schemas exist, not wired |
| Connectivity | 2 | 0 | 2 | ⚠️ Schemas exist, not wired |
| BNK Data/Health | 5 | 0 | 5 | ❌ No schemas yet |
| Fleet Health | 2 | 2 | 0 | ✅ Complete |
| System Health | 3 | 0 | 3 | ⚠️ Partial schemas exist |
| K8s Resources | 7 | 0 | 7 | ⚠️ Schemas exist, not wired |
| Projects | 6 | 6 | 0 | ✅ Complete |
| Helm | 5 | 0 | 5 | ⚠️ Schemas exist, not wired |
| Licensing | 4 | 4 | 0 | ✅ Complete |
| Recovery | 3 | 3 | 0 | ✅ Complete |
| **Total** | **54** | **28** | **26** | — |

---

## Hardening Plan

### Batch 1 — Wire Existing Schemas (Low Risk, High Confidence)

These endpoints already have matching Pydantic response models in `backend/schemas/`. The work is to add `response_model=X` to the route decorator and verify the service return dict matches the schema. No schema authoring needed.

**Estimated effort: ~2 hours. Extremely low risk.**

#### 1a. Cluster Management Routes → `schemas/k8s.py`

| Route | Method | Current Return | Wire to Schema |
|-------|--------|----------------|---------------|
| `/api/k8s/clusters` | GET | `ClusterManagementService.list_all_clusters()` → list | `ClusterListResponse` |
| `/api/k8s/clusters/{id}` | GET | `ClusterManagementService.get_cluster_details()` → dict | `ClusterDetailResponse` |
| `/api/projects/{id}/k8s/clusters` | GET | `ClusterManagementService.list_project_clusters()` → list | `ClusterListResponse` |
| `/api/projects/{id}/k8s/clusters` | POST | `ClusterManagementService.create_cluster()` → dict | `ClusterOperationResponse` |
| `/api/k8s/clusters/{id}` | PUT | `ClusterManagementService.update_cluster()` → dict | `ClusterOperationResponse` |
| `/api/k8s/clusters/{id}` | DELETE | `ClusterManagementService.delete_cluster()` → dict | `ClusterOperationResponse` |
| `/api/k8s/clusters/{id}/test` | POST | `KubernetesService.test_connection()` → dict | `ClusterConnectionTestResponse` |
| `/api/k8s/clusters/{id}/namespaces` | GET | inline dict building | `NamespaceListResponse` ★ |
| `/api/k8s/clusters/{id}/nodes/count` | GET | inline dict | `NodeCountResponse` |
| `/api/k8s/clusters/{id}/refresh-kubeconfig` | POST | `ClusterManagementService.refresh_kubeconfig()` → dict | `ClusterOperationResponse` |

★ Namespaces route builds the dict inline in the handler — needs minor shape check.

**Verification approach:**
1. For each route, read the service method return dict.
2. Compare field names/types to the Pydantic model.
3. Add `response_model=X` to decorator.
4. If service returns extra fields the model doesn't declare, Pydantic will silently drop them — this is the main risk. Check for this.
5. Run existing backend tests to verify no breakage.

**Known shape mismatches to resolve:**
- `ClusterManagementService.list_all_clusters()` returns a flat list, but `ClusterListResponse` wraps it in `{"clusters": [...]}`. The route handler will need to wrap the return value.
- `NamespaceListResponse` expects `count` field — verify the inline dict includes this.

#### 1b. Connectivity Routes → `schemas/k8s.py`

| Route | Method | Wire to Schema |
|-------|--------|---------------|
| `/api/k8s/clusters/{id}/connectivity` | GET | `ClusterConnectivityResponse` |
| `/api/k8s/clusters/connectivity` | GET | `BatchConnectivityResponse` |

These schemas were updated during PLAT-REL-001 with canonical `ConnectivityStatus` enum. The service already returns matching shapes.

#### 1c. Helm Routes → `schemas/helm.py`

| Route | Method | Wire to Schema |
|-------|--------|---------------|
| `/api/k8s/{id}/helm/releases` | GET | Needs shape adaptation ★ |
| `/api/k8s/{id}/helm/releases/{name}` | GET | Needs shape adaptation ★ |
| `/api/k8s/{id}/helm/install` | POST | `HelmOperationResponse` |
| `/api/helm/repositories` | GET | `HelmRepositoryListResponse` |
| `/api/helm/charts/search` | GET | `HelmChartSearchResponse` |

★ **Shape mismatch:** Helm routes return `{"success": True, "releases": [...], "count": N}` but `HelmReleaseListResponse` schema expects `{"releases": [...], "cluster_id": N}`. Two options:
1. **Adapt schema** to include `success` and `count` (preferred — matches actual return).
2. **Adapt route** to match schema.

**Decision: Adapt schemas.** Add `success: bool = True` and `count: int` to `HelmReleaseListResponse`. This matches the actual contract consumers see.

#### 1d. System Health Route → `schemas/system.py`

| Route | Method | Wire to Schema |
|-------|--------|---------------|
| `/api/system/health` | GET | `SystemHealthResponse` |

The health route is a `@public_router` (no auth), which uses a different router instance. The schema exists and the service shape should match. Verify.

---

### Batch 2 — Wire with Minor Adaptations (Low Risk)

These endpoints have schemas that are close but need small adjustments to match actual return shapes.

**Estimated effort: ~2 hours. Low risk.**

#### 2a. K8s Resource Routes → `schemas/k8s.py`

| Route | Method | Wire to Schema | Notes |
|-------|--------|---------------|-------|
| `/api/k8s/clusters/{id}/resources/{type}` | GET | `K8sResourceListResponse` | ★ |
| `/api/k8s/clusters/{id}/resources/{type}/{name}/describe` | GET | New `K8sResourceDescribeResponse` | Needs creation |
| `/api/k8s/resource-types` | GET | `ResourceTypeListResponse` | ★ |

★ K8s resources are intrinsically dynamic — the `spec`, `status`, and other nested fields vary by resource type. The existing `K8sResourceItem` model uses `dict[str, Any]` for dynamic fields, which is correct for envelope typing.

**New schema needed:**
```python
class K8sResourceDescribeResponse(BaseModel):
    """Response for describe/detail endpoint."""
    resource: dict[str, Any]  # Full K8s resource object
    cluster_id: int
    resource_type: str
    name: str
    namespace: str | None = None
```

#### 2b. System Version/Settings Routes

| Route | Method | Notes |
|-------|--------|-------|
| `/api/version` | GET | Returns `{"version": str, "build_time": str, ...}` — create `VersionResponse` |
| `/api/settings` | GET | Returns `list[dict]` — create `SettingsListResponse` |

**New schemas needed:**
```python
class VersionResponse(BaseModel):
    version: str
    git_commit: str | None = None
    build_time: str | None = None

class SettingItem(BaseModel):
    key: str
    value: str | None = None
    description: str | None = None

class SettingsListResponse(BaseModel):
    settings: list[SettingItem]
```

#### 2c. Remaining K8s Resource Endpoints

| Route | Method | Notes |
|-------|--------|-------|
| `/api/k8s/clusters/{id}/pods/{name}/logs` | GET | Returns `{"logs": str, ...}` — create `PodLogsResponse` |
| `/api/k8s/clusters/{id}/events` | GET | Returns list of event dicts — create `ClusterEventsResponse` |
| `/api/k8s/clusters/{id}/top/pods` | GET | Returns metrics list — create `PodMetricsResponse` |
| `/api/k8s/clusters/{id}/top/nodes` | GET | Returns metrics list — create `NodeMetricsResponse` |
| `/api/k8s/clusters/{id}/pods/{name}/restart` | POST | Returns success/message — `K8sResourceOperationResponse` |

**New schemas needed (add to `schemas/k8s.py`):**
```python
class PodLogsResponse(BaseModel):
    pod_name: str
    container: str | None = None
    logs: str
    cluster_id: int

class ClusterEventItem(BaseModel):
    name: str
    namespace: str | None = None
    type: str | None = None  # Normal, Warning
    reason: str | None = None
    message: str | None = None
    source: str | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    count: int | None = None

class ClusterEventsResponse(BaseModel):
    events: list[ClusterEventItem]
    cluster_id: int

class PodMetricItem(BaseModel):
    name: str
    namespace: str | None = None
    cpu: str | None = None
    memory: str | None = None

class PodMetricsResponse(BaseModel):
    pods: list[PodMetricItem]
    cluster_id: int

class NodeMetricItem(BaseModel):
    name: str
    cpu: str | None = None
    cpu_percent: str | None = None
    memory: str | None = None
    memory_percent: str | None = None

class NodeMetricsResponse(BaseModel):
    nodes: list[NodeMetricItem]
    cluster_id: int
```

---

### Batch 3 — BNK Data/Health Models (Medium Risk, Requires Design)

These endpoints return deeply nested analysis results. Response models need careful design to capture the contract without over-constraining the evolving shape.

**Estimated effort: ~4 hours. Medium risk due to complex nested shapes.**

#### Target Endpoints

| Route | Method | Current Shape |
|-------|--------|--------------|
| `/api/k8s/clusters/{id}/f5bnk/data` | GET | 11-field dict with nested health, topology, backends, palette objects |
| `/api/k8s/clusters/{id}/f5bnk/health` | GET | Health analysis with severity, components, issues lists |
| `/api/k8s/clusters/{id}/f5bnk/gateway-topology` | GET | Topology graph with nodes, links, counts |
| `/api/k8s/clusters/{id}/f5bnk/policy-gateway-associations` | GET | Policy association list with count |
| `/api/k8s/clusters/{id}/f5bnk/a2a/agents` | GET | Agent list with probe results |

#### Approach

**Strategy: Progressive typing** — Start with loose `dict[str, Any]` for deeply nested fields, then tighten as shapes stabilize.

```python
# Example for BNK health
class BNKHealthResponse(BaseModel):
    severity: str  # Uses HealthSeverity canonical values
    components: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    summary: str | None = None
    cluster_id: int

class BNKDataResponse(BaseModel):
    health: dict[str, Any]  # BNKHealthResponse shape
    topology: list[dict[str, Any]]
    dataPlane: dict[str, Any]
    referenceGrants: list[dict[str, Any]]
    topologyCounts: dict[str, int]
    policyAssociations: list[dict[str, Any]]
    policyCount: int
    backends: list[dict[str, Any]]
    palette: dict[str, Any]
    cluster_id: int
    namespace: str | None = None
```

**Risk note:** BNK analysis shapes are the output of complex service functions (`analyze_health`, `analyze_topology`, `analyze_backends`). Tightening these models requires reading each analysis function to map exact output fields. This is the most work-intensive part of the hardening plan.

#### Prerequisite

Before creating BNK response models, read and document the exact return shapes of:
- `services/bnk_data_service.py` → `analyze_health()`, `analyze_topology()`, `analyze_backends()`, `analyze_policy_associations()`, `extract_palette_data()`
- `services/bnk/a2a_discovery.py` → `discover_a2a_agents()`

---

## Adoption Order

| Phase | Batch | Endpoints | Risk | Effort |
|-------|-------|:---------:|:----:|:------:|
| **Phase 1** | Batch 1a: Cluster management | 10 | Very low | 1h |
| **Phase 1** | Batch 1b: Connectivity | 2 | Very low | 15m |
| **Phase 1** | Batch 1c: Helm | 5 | Low | 45m |
| **Phase 1** | Batch 1d: System health | 1 | Very low | 15m |
| **Phase 2** | Batch 2a: K8s resources | 3 | Low | 1h |
| **Phase 2** | Batch 2b: System version/settings | 2 | Low | 30m |
| **Phase 2** | Batch 2c: K8s logs/events/metrics | 5 | Low | 1h |
| **Phase 3** | Batch 3: BNK data/health | 5 | Medium | 4h |

**Total estimated effort: ~9 hours across 3 phases.**

After Phase 1 alone, Tier 1 coverage goes from **52% → 85%** (46 of 54 endpoints covered).

---

## Blockers and Unknowns

| Issue | Impact | Resolution |
|-------|--------|-----------|
| **Helm route shape mismatch** | Schema says `cluster_id`, route returns `success` + `count` | Adapt schemas to match actual return shapes |
| **Cluster list return shape** | Service returns flat list, schema wraps in `{"clusters": [...]}` | Adjust route handler to wrap, or adjust schema |
| **BNK health nested shapes** | Analysis functions return deep dicts | Use `dict[str, Any]` initially, tighten later |
| **K8s resource dynamic fields** | `spec`, `status` vary by resource type | Envelope model with `dict[str, Any]` for dynamic portions |
| **`response_model` strips extra fields** | Pydantic silently drops unmodeled fields | Verify no consumer depends on stripped fields |

---

## Impact on Consumers

### Frontend
- **No immediate change.** The frontend already consumes these shapes via Axios. Adding `response_model` doesn't change the response — it just validates it server-side and documents it in OpenAPI.
- **Future benefit:** TypeScript types can be generated from OpenAPI once response models stabilize.

### MCP
- **No immediate change.** MCP tools consume the same JSON. Response models ensure the shape MCP expects is the shape the backend guarantees.
- **Future benefit:** MCP tool descriptions can reference OpenAPI schema for precise output contracts.

### Tests
- **Backend tests:** Existing tests should continue to pass. If `response_model` strips a field that tests assert on, that's a contract bug to fix.
- **Frontend tests:** MSW mocks should match the now-documented response shapes. Any mismatch is a contract drift bug that the golden test matrix will catch.

---

## Follow-On Implementation Tickets

| Ticket | Description | Batch | Priority |
|--------|-------------|-------|----------|
| **CT-B01** | Wire `schemas/k8s.py` response models to cluster management routes | 1a | P0 |
| **CT-B02** | Wire `schemas/k8s.py` connectivity response models to routes | 1b | P0 |
| **CT-B03** | Adapt + wire `schemas/helm.py` response models to Helm routes | 1c | P0 |
| **CT-B04** | Wire `schemas/system.py` SystemHealthResponse to health route | 1d | P0 |
| **CT-B05** | Create + wire K8s resource describe/logs/events/metrics response models | 2a-2c | P1 |
| **CT-B06** | Create system VersionResponse and SettingsListResponse | 2b | P1 |
| **CT-B07** | Design + wire BNK data/health/topology response models | 3 | P1 |
| **CT-B08** | Verify no consumer depends on fields stripped by response_model | all | P0 |

---

## Relationship to Work Package A

The connectivity response models (`ClusterConnectivityResponse`, `BatchConnectivityResponse`) already use the canonical `ConnectivityStatus` enum from PLAT-REL-001. BNK health response models should reference `HealthSeverity` from `models/enums.py`. This ensures the contract trust layer builds on the platform truthfulness layer.

---

## Related Documents

- [Endpoint Contract Tiering — API-CONTRACT-001](ENDPOINT_CONTRACT_TIERING.md)
- [Golden Contract Test Matrix — API-CONTRACT-004](GOLDEN_CONTRACT_TEST_MATRIX.md)
- [OpenAPI Diff Review Workflow — API-CONTRACT-005](OPENAPI_DIFF_REVIEW_WORKFLOW.md)
- [Status Semantics — PLAT-REL-001](STATUS_SEMANTICS.md)
