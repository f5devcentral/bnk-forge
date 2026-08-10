# Golden Contract Test Matrix — API-CONTRACT-004

> The initial set of exact response-shape tests for the most important BNK Forge APIs. Defines which endpoints get "golden" contract verification, what assertions to make, and how fixtures work.

Status: **Accepted** | Created: 2026-03-27

---

## Problem

BNK Forge has 941+ backend unit tests, but most test business logic, not response shape. Frontend MSW mocks return canned data that may not match actual backend responses. MCP tools trust the backend's return shape without verification. When a service function changes its return dict, nothing catches the contract drift until a user sees broken UI or an AI assistant gets unexpected data.

Golden contract tests prove that **the actual response shape matches the documented Pydantic schema** for the endpoints that matter most.

---

## What is a Golden Contract Test?

A golden contract test:

1. Calls the actual route handler (via FastAPI TestClient).
2. Receives the real JSON response.
3. Validates the response **parses cleanly** through the declared `response_model` Pydantic schema.
4. Asserts **required fields are present** and have expected types.
5. Optionally asserts **field values are within expected ranges** (e.g., severity is one of the canonical values).

It does NOT test business logic, database state, or external system behavior — those are covered by existing unit/integration tests.

---

## Golden Contract Test Matrix

### Selection Criteria

Endpoints are selected based on:
- **Tier 1** from API-CONTRACT-001
- **Multi-consumer** — consumed by both frontend and MCP
- **Operator decision path** — response directly informs operator action
- **Shape complexity** — complex nested shapes are most likely to drift

### The Matrix (20 endpoints)

| # | Route | Method | Consumer | Response Schema | Fixture Needed | Priority |
|---|-------|--------|----------|----------------|---------------|----------|
| **Auth** |
| 1 | `/api/auth/login` | POST | FE | `LoginResponse` | DB user | P0 |
| 2 | `/api/auth/me` | GET | FE | `MeResponse` | Auth token | P0 |
| 3 | `/api/auth/users` | GET | FE+MCP | `UserListWithCountsResponse` | DB users | P0 |
| **Cluster Management** |
| 4 | `/api/k8s/clusters` | GET | FE+MCP | `ClusterListResponse` | DB clusters | P0 |
| 5 | `/api/k8s/clusters/{id}` | GET | FE+MCP | `ClusterDetailResponse` | DB cluster | P0 |
| 6 | `/api/k8s/clusters/{id}/test` | POST | FE+MCP | `ClusterConnectionTestResponse` | Mock K8s | P0 |
| 7 | `/api/k8s/clusters/{id}/scan` | POST | FE+MCP | `ClusterScanResponse` | Mock K8s | P1 |
| **Connectivity** |
| 8 | `/api/k8s/clusters/{id}/connectivity` | GET | FE+MCP | `ClusterConnectivityResponse` | Mock probe | P0 |
| 9 | `/api/k8s/clusters/connectivity` | GET | FE+MCP | `BatchConnectivityResponse` | Mock probe | P0 |
| **System** |
| 10 | `/api/system/health` | GET | FE+MCP | `SystemHealthResponse` | Running services | P0 |
| **Fleet** |
| 11 | `/api/operators/fleet-health` | GET | FE+MCP | `FleetHealthResponse` | Mock operators | P0 |
| **BNK** |
| 12 | `/api/k8s/clusters/{id}/f5bnk/health` | GET | FE+MCP | `BNKHealthResponse` ★ | Mock K8s CRDs | P1 |
| 13 | `/api/k8s/clusters/{id}/f5bnk/gateway-topology` | GET | FE+MCP | `BNKTopologyResponse` ★ | Mock K8s CRDs | P1 |
| **Helm** |
| 14 | `/api/k8s/{id}/helm/releases` | GET | FE+MCP | `HelmReleaseListResponse` | Mock Helm | P1 |
| 15 | `/api/helm/repositories` | GET | FE+MCP | `HelmRepositoryListResponse` | Mock Helm | P1 |
| **Projects** |
| 16 | `/api/projects` | GET | FE+MCP | `ProjectListResponse` | DB projects | P0 |
| 17 | `/api/projects/{id}` | GET | FE+MCP | `ProjectDetailResponse` | DB project | P0 |
| **Licensing** |
| 18 | `/api/licensing/{id}/status` | GET | FE+MCP | `LicenseStatusResponse` | Mock CWC | P1 |
| **Recovery** |
| 19 | `/api/k8s/clusters/{id}/recovery/status` | GET | FE+MCP | `RecoveryStatusResponse` | Mock K8s | P1 |
| **K8s Resources** |
| 20 | `/api/k8s/clusters/{id}/resources/{type}` | GET | FE+MCP | `K8sResourceListResponse` | Mock K8s | P1 |

★ Schema needs to be created first (Batch 3 from API-CONTRACT-002).

---

## Fixture Approach

### Fixture Categories

| Category | What it provides | Example |
|----------|-----------------|---------|
| **DB fixtures** | Pre-seeded database records via factory-boy | Users, projects, clusters |
| **Mock K8s** | Mocked `kubernetes.client` responses | Namespace list, pod list, CRD list |
| **Mock Helm** | Mocked Helm CLI subprocess output | Release list, repo list |
| **Mock probe** | Mocked network probe results (ICMP, TCP, K8s API) | Connected, partial, unreachable |
| **Mock CWC** | Mocked CWC REST API responses | License status, QKView status |
| **Auth token** | Valid JWT for test user | Bearer token fixture |

### Existing Fixtures to Reuse

The backend test suite already has:
- `conftest.py` with `test_client`, `db_session`, `test_user` fixtures
- Factory-boy factories for `User`, `Project`, `KubernetesCluster`, `ProjectModule`
- Mock K8s client patterns in `test_kubernetes_service.py`, `test_connectivity_probe.py`

**New fixtures needed:**
- `mock_bnk_data` — pre-built BNK CRD responses for health/topology testing
- `mock_helm_releases` — pre-built Helm release list/detail
- `mock_connectivity_results` — pre-built probe results for each connectivity status

---

## Test Structure

### File Organization

```
backend/tests/
  contract/                    # New directory for golden contract tests
    __init__.py
    conftest.py               # Contract test fixtures
    test_auth_contracts.py    # Auth shape tests
    test_cluster_contracts.py # Cluster shape tests
    test_connectivity_contracts.py
    test_bnk_contracts.py
    test_fleet_contracts.py
    test_helm_contracts.py
    test_project_contracts.py
    test_system_contracts.py
```

### Test Pattern

```python
"""
Golden contract test — verifies response shape matches declared schema.

Pattern:
1. Set up minimal fixtures (DB + mocks)
2. Call route via TestClient
3. Assert 200 status
4. Parse response through Pydantic schema
5. Assert required fields are present with expected types
6. Assert canonical vocabulary where applicable (e.g., HealthSeverity values)
"""

def test_cluster_list_contract(test_client, auth_headers, seed_clusters):
    """GET /api/k8s/clusters returns shape matching ClusterListResponse."""
    response = test_client.get("/api/k8s/clusters", headers=auth_headers)
    assert response.status_code == 200

    data = response.json()

    # Parse through Pydantic — this IS the contract assertion
    parsed = ClusterListResponse.model_validate(data)

    # Required fields present
    assert isinstance(parsed.clusters, list)
    assert len(parsed.clusters) > 0

    # Each cluster has required fields
    for cluster in parsed.clusters:
        assert cluster.id is not None
        assert isinstance(cluster.name, str)
        assert cluster.name != ""


def test_connectivity_contract_canonical_vocabulary(
    test_client, auth_headers, mock_connectivity_probe
):
    """GET /api/k8s/clusters/{id}/connectivity uses canonical ConnectivityStatus."""
    response = test_client.get(
        "/api/k8s/clusters/1/connectivity", headers=auth_headers
    )
    assert response.status_code == 200

    data = response.json()
    parsed = ClusterConnectivityResponse.model_validate(data)

    # Status must be a canonical ConnectivityStatus value
    assert parsed.status in {"connected", "reachable", "partial", "unreachable", "unknown"}
```

### Assertion Levels

Each golden test covers three assertion levels:

| Level | What it checks | Example |
|-------|---------------|---------|
| **L1: Parseable** | Response parses through Pydantic schema without error | `ClusterListResponse.model_validate(data)` |
| **L2: Required fields** | Non-optional fields are present and non-null | `assert cluster.id is not None` |
| **L3: Vocabulary** | Enum/status fields use canonical values | `assert status in ConnectivityStatus.__members__` |

---

## Ownership and Review

### Test Ownership

| Domain | Owner | Review by |
|--------|-------|-----------|
| Auth | Backend | Frontend (consumer) |
| Cluster/Connectivity | Backend | Frontend + MCP (consumers) |
| BNK/Fleet | Backend | Frontend + MCP (consumers) |
| Helm | Backend | Frontend + MCP (consumers) |
| Projects | Backend | Frontend (consumer) |
| System | Backend | MCP (consumer) |

### Review Process

When a golden contract test fails:
1. **Is the schema wrong?** If the service legitimately changed shape → update schema + test + document in OpenAPI diff.
2. **Is the service wrong?** If the service broke its contract → fix the service.
3. **Is the test wrong?** If the test is over-specified → relax assertion, document why.

A golden test failure is a **merge blocker** for Tier 1 endpoints.

---

## Integration with CI

### Proposed CI Stage

```yaml
# In .github/workflows/test.yml (or Makefile target)
contract-tests:
  name: Golden Contract Tests
  runs-on: ubuntu-latest
  steps:
    - run: pytest backend/tests/contract/ -v --tb=short
```

### Makefile Target

```makefile
test-contracts:
    cd backend && python -m pytest tests/contract/ -v --tb=short

# Include in pre-push checks
pre-push: quick-check test-backend test-contracts test-frontend
```

---

## Frontend MSW Alignment

Golden contract tests establish the **source of truth** for response shapes. Frontend MSW handlers should mirror these shapes.

### Proposed Workflow

1. Golden contract test defines expected shape via Pydantic schema.
2. Backend OpenAPI spec is regenerated (`scripts/generate-openapi.py`).
3. Frontend MSW handlers are verified against OpenAPI spec (or Pydantic schema directly).
4. Any MSW handler that returns a shape not matching the schema is a contract drift bug.

This creates a closed loop: **Backend schema → Golden test → OpenAPI → MSW mock → Frontend test**.

---

## MCP Alignment

MCP tools call backend routes and return the response to AI assistants. The golden contract tests ensure:

1. The shape MCP tools receive is stable and documented.
2. Tool descriptions can reference OpenAPI schemas for precise output specification.
3. AI assistants can trust field names and types in tool responses.

---

## Follow-On Implementation Tickets

| Ticket | Description | Priority |
|--------|-------------|----------|
| **CT-B10** | Create `backend/tests/contract/` directory and shared fixtures | P0 |
| **CT-B11** | Implement P0 golden tests: auth, clusters, connectivity, system, fleet, projects (12 tests) | P0 |
| **CT-B12** | Implement P1 golden tests: BNK, Helm, licensing, recovery, resources (8 tests) | P1 |
| **CT-B13** | Add `make test-contracts` target and CI stage | P0 |
| **CT-B14** | Audit frontend MSW handlers against golden schemas (top 10) | P1 |
| **CT-B15** | Document MCP tool output contracts referencing golden schemas | P2 |

---

## Relationship to Other Specs

- **API-CONTRACT-001** (Tiering): This matrix selects from Tier 1 endpoints.
- **API-CONTRACT-002** (Coverage Plan): Response models must be wired before golden tests can validate against them. Batch 1-2 from the coverage plan are prerequisites for P0 golden tests.
- **API-CONTRACT-005** (OpenAPI Diff): Golden tests + OpenAPI diffs form two complementary verification layers.
- **PLAT-REL-001** (Status Semantics): Golden tests enforce canonical vocabulary for status/severity fields.

---

## Related Documents

- [Endpoint Contract Tiering — API-CONTRACT-001](ENDPOINT_CONTRACT_TIERING.md)
- [Tier 1 Response Model Coverage Plan — API-CONTRACT-002](TIER1_RESPONSE_MODEL_PLAN.md)
- [OpenAPI Diff Review Workflow — API-CONTRACT-005](OPENAPI_DIFF_REVIEW_WORKFLOW.md)
- [Status Semantics — PLAT-REL-001](STATUS_SEMANTICS.md)
- [Diagnostic Payload — PLAT-REL-002](DIAGNOSTIC_PAYLOAD.md)
