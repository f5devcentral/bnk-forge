# Endpoint Contract Tiering — API-CONTRACT-001

> Classification of BNK Forge API endpoints by contract criticality, to sequence hardening work where breakage hurts most.

Status: **Accepted** | Created: 2026-03-27

---

## Problem

BNK Forge has ~200 API endpoints across 40+ route files. Not all endpoints carry the same risk when contracts drift. Today, some critical operator-facing and AI-consumed routes have no `response_model`, while lower-priority admin routes do. The team needs a shared rubric to focus contract hardening where it matters.

---

## Tiering Rubric

### Tier 1 — Operator-Critical and AI-Consumed

**Criteria (any one qualifies):**

1. **Operator decision path** — The endpoint directly informs operator action (health checks, cluster status, connectivity probes, fleet health).
2. **MCP tool backing** — The endpoint is called by one or more MCP tools consumed by AI assistants.
3. **Frontend primary surface** — The endpoint populates the default/landing view of a major UI page.
4. **Auth gate** — The endpoint controls access to all other endpoints (login, token, user identity).
5. **Cross-system contract** — The endpoint is consumed by more than one client type (frontend + MCP, frontend + operator CLI).

**Contract expectation:** Explicit `response_model`, shape-matching golden tests, OpenAPI diff reviewed.

### Tier 2 — Important but Lower Blast Radius

**Criteria:**

1. **Secondary operational surface** — Used in drill-down, detail, or configuration views (not landing pages).
2. **Single-consumer** — Consumed by frontend only, or MCP only, not both.
3. **Mutations with confirmation** — Endpoints that change state but have explicit user confirmation flows.

**Contract expectation:** Explicit `response_model` recommended, shape tests encouraged, drift review on best-effort.

### Tier 3 — Internal, Admin, or Low-Traffic

**Criteria:**

1. **System admin only** — Database stats, workspace cleanup, container restart.
2. **Background/polling** — Operator polling, heartbeat, task status.
3. **Rarely consumed by AI** — No MCP tool backing, no primary frontend surface.

**Contract expectation:** Typed return recommended long-term, but not a priority for hardening.

---

## Endpoint Classification

### Tier 1 — Operator-Critical and AI-Consumed (54 endpoints)

#### Auth (7 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| POST | `/api/auth/login` | ✅ LoginResponse | ❌ | ✅ |
| GET | `/api/auth/me` | ✅ MeResponse | ❌ | ✅ |
| POST | `/api/auth/change-password` | ✅ SuccessResponse | ❌ | ✅ |
| POST | `/api/auth/users` | ✅ UserCreateResponse | ✅ | ✅ |
| GET | `/api/auth/users` | ✅ UserListWithCountsResponse | ✅ | ✅ |
| PUT | `/api/auth/users/{user_id}` | ✅ UserUpdateResponse | ❌ | ✅ |
| DELETE | `/api/auth/users/{user_id}` | ✅ UserDeleteResponse | ❌ | ✅ |

**Status: ✅ Fully covered.** All auth routes have explicit response models.

#### Cluster Management (10 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/k8s/clusters` | ✅ ClusterListResponse | ✅ | ✅ |
| GET | `/api/k8s/clusters/{cluster_id}` | ✅ ClusterDetailResponse | ✅ | ✅ |
| POST | `/api/projects/{project_id}/k8s/clusters` | ✅ ClusterCreateResponse | ❌ | ✅ |
| PUT | `/api/k8s/clusters/{cluster_id}` | ✅ ClusterSummary | ❌ | ✅ |
| DELETE | `/api/k8s/clusters/{cluster_id}` | ✅ ClusterOperationResponse | ❌ | ✅ |
| POST | `/api/k8s/clusters/{cluster_id}/test` | ✅ ClusterConnectionTestResponse | ✅ | ✅ |
| GET | `/api/k8s/clusters/{cluster_id}/namespaces` | ✅ NamespaceListResponse | ✅ | ✅ |
| POST | `/api/k8s/clusters/{cluster_id}/scan` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/resource-types` | ❌ | ❌ | ✅ |
| POST | `/api/k8s/clusters/{cluster_id}/refresh-kubeconfig` | ✅ ClusterRefreshResponse | ❌ | ✅ |

**Status: ✅ Fully covered (10/10).** Wired in CT-B01 + CT-B16/CT-B17 (scan + resource-types).

#### Connectivity (2 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/k8s/clusters/{cluster_id}/connectivity` | ✅ ClusterConnectivityResponse | ✅ | ✅ |
| GET | `/api/k8s/clusters/connectivity` | ✅ BatchConnectivityResponse | ✅ | ✅ |

**Status: ✅ Fully covered.** Wired in CT-B02.

#### BNK Data & Health (5 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/k8s/clusters/{id}/f5bnk/data` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/f5bnk/health` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/f5bnk/gateway-topology` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/f5bnk/policy-gateway-associations` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/f5bnk/a2a/agents` | ❌ | ✅ | ✅ |

**Status: ❌ 0/5 have response models.** These are structurally complex (nested dicts from service analysis). Response model creation requires careful shape documentation.

#### Fleet Health (2 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/operators/fleet-health` | ✅ FleetHealthResponse | ✅ | ✅ |
| POST | `/api/operators/fleet-compare` | ✅ FleetCompareResponse | ❌ | ✅ |

**Status: ✅ Fully covered.**

#### System Health (3 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/system/health` | ✅ SystemHealthResponse | ✅ | ✅ |
| GET | `/api/version` | ❌ | ✅ | ✅ |
| GET | `/api/settings` | ❌ | ✅ | ✅ |

**Status: ✅ Fully covered (3/3).** Health wired in CT-B04; version/settings typed in CT-B15.

#### K8s Resources (7 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/k8s/clusters/{id}/resources/{type}` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/resources/{type}/{name}/describe` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/pods/{name}/logs` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/events` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/top/pods` | ❌ | ✅ | ✅ |
| GET | `/api/k8s/clusters/{id}/top/nodes` | ❌ | ✅ | ✅ |
| POST | `/api/k8s/clusters/{id}/pods/{name}/restart` | ❌ | ✅ | ✅ |

**Status: ✅ Fully covered (7/7).** Envelope-level response models wired in CT-B14 to type stable contract fields while preserving dynamic nested payloads.

#### Projects (6 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/projects` | ✅ ProjectListResponse | ✅ | ✅ |
| GET | `/api/projects/{project_id}` | ✅ ProjectDetailResponse | ✅ | ✅ |
| POST | `/api/projects` | ✅ ProjectMutationResponse | ❌ | ✅ |
| PUT | `/api/projects/{project_id}` | ✅ ProjectMutationResponse | ❌ | ✅ |
| DELETE | `/api/projects/{project_id}` | ✅ SuccessResponse | ❌ | ✅ |
| GET | `/api/projects/active` | ✅ ActiveProjectResponse | ❌ | ✅ |

**Status: ✅ Fully covered.**

#### Helm (5 key endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/k8s/{id}/helm/releases` | ✅ HelmReleaseListResponse | ✅ | ✅ |
| GET | `/api/k8s/{id}/helm/releases/{name}` | ✅ HelmReleaseDetailResponse | ✅ | ✅ |
| POST | `/api/k8s/{id}/helm/install` | ✅ HelmOperationResponse | ✅ | ✅ |
| GET | `/api/helm/repositories` | ✅ HelmRepositoryListResponse | ✅ | ✅ |
| GET | `/api/helm/charts/search` | ✅ HelmChartSearchResponse | ✅ | ✅ |

**Status: ✅ Fully covered.** Schemas adapted and wired in CT-B03.

#### Licensing (4 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/licensing/{id}/status` | ✅ LicenseStatusResponse | ✅ | ✅ |
| GET | `/api/licensing/{id}/report` | ✅ LicenseReportResponse | ✅ | ✅ |
| POST | `/api/licensing/{id}/renew` | ✅ LicenseActionResponse | ✅ | ✅ |
| GET | `/api/licensing/{id}/cwc-status` | ✅ CWCStatusResponse | ❌ | ✅ |

**Status: ✅ Fully covered.**

#### Recovery (3 endpoints)
| Method | Path | response_model | MCP | Frontend |
|--------|------|:---:|:---:|:---:|
| GET | `/api/k8s/clusters/{id}/recovery/status` | ✅ RecoveryStatusResponse | ✅ | ✅ |
| POST | `/api/k8s/clusters/{id}/recovery/cwc-certs` | ✅ CWCCertResyncResponse | ✅ | ✅ |
| POST | `/api/k8s/clusters/{id}/recovery/platform-restart` | ✅ PlatformRestartResponse | ✅ | ✅ |

**Status: ✅ Fully covered.**

---

### Tier 2 — Important but Lower Blast Radius (67 endpoints)

| Domain | Endpoints | response_model coverage |
|--------|:---------:|:-----------------------:|
| Benchmarks | 32 | ✅ 32/32 (fully typed) |
| Drift | 12 | ✅ 10/12 (2 missing: check-now, stats) |
| QKView | 11 | ✅ 10/11 (download is file stream) |
| Stacks | 12 | ✅ 10/12 |
| BNK Upgrade | 8 | ⚠️ 4/8 (version/history typed, plan/execute untyped) |
| DPF | 3 | ❌ 0/3 |
| SSH Credentials | 6 | ✅ 5/6 |
| Notifications | 6 | ✅ 6/6 |

**Tier 2 is in relatively good shape.** Benchmarks and QKView are fully covered from GAP-010 work. Main gaps are BNK Upgrade mutations and DPF endpoints.

---

### Tier 3 — Internal, Admin, or Low-Traffic (~80 endpoints)

| Domain | Endpoints | Notes |
|--------|:---------:|-------|
| System admin (cleanup, vacuum, restart, workspace mgmt) | ~15 | Low priority |
| Cloud auth (AWS SSO, SSH, credentials) | ~16 | Complex but admin-only |
| Operator polling/heartbeat | 5 | Internal protocol |
| Config export/promotion | 4 | Infrequent use |
| Module library/registry | ~15 | Developer-facing |
| Project execution/orchestration | ~15 | Complex IaC flows |
| State viewer | 8 | Developer inspection |
| Tasks | 4 | Background job status |
| Alert channels | 9 | Low traffic |
| Audit | 3 | Admin-only reporting |

---

## Summary

| Tier | Endpoints | With response_model | Coverage |
|------|:---------:|:-------------------:|:--------:|
| **Tier 1** | 54 | 54 | **100%** |
| **Tier 2** | 67 | ~57 | **~85%** |
| **Tier 3** | ~80 | ~15 | **~19%** |
| **Total** | ~201 | ~116 | **~58%** |

### Key Insight (Updated 2026-03-27)

Tier 1 coverage improved from 81% → 100% via CT-B14/CT-B15/CT-B16/CT-B17 (Batch 2 hardening).

- **K8s resources** (7 endpoints): now covered using envelope-level models
- **System version/settings** (2 endpoints): now explicitly typed
- **Cluster scan + resource-types** (2 endpoints): now typed with truthful wrappers/schemas

Tier 1 now has no remaining response_model gaps.

**Golden contract tests:** 22 P0 tests in `backend/tests/contract/` verify response shapes match schemas (CT-B10/B11). Run via `make test-contracts`.

---

## Follow-On Implementation Tickets

From this tiering, the following concrete work emerges:

| Ticket | Description | Priority |
|--------|-------------|----------|
| **CT-B01** | Wire existing `schemas/k8s.py` response models to cluster management routes | P0 |
| **CT-B02** | Wire existing `schemas/k8s.py` connectivity response models to connectivity routes | P0 |
| **CT-B03** | Wire existing `schemas/system.py` SystemHealthResponse to system routes | P0 |
| **CT-B04** | Wire existing `schemas/helm.py` response models to Helm routes | P0 |
| **CT-B05** | Create and wire response models for BNK data/health/topology endpoints | P1 |
| **CT-B06** | Create envelope response models for K8s resource list/describe endpoints | P1 |
| **CT-B07** | Wire response models to BNK upgrade plan/execute endpoints | P1 |
| **CT-B08** | Verify no consumer depends on fields stripped by response_model | P0 |
| **CT-B09** | Create and wire response models for DPF endpoints | P2 |

---

## Relationship to Work Package A

This tiering builds on the canonical status semantics from PLAT-REL-001. The `HealthSeverity` and `ConnectivityStatus` enums established there are used by several Tier 1 endpoints (connectivity, BNK health, fleet health). Contract hardening should use these canonical types, not legacy string literals.

---

## Related Documents

- [Status Semantics — PLAT-REL-001](STATUS_SEMANTICS.md)
- [Tier 1 Response Model Coverage Plan — API-CONTRACT-002](TIER1_RESPONSE_MODEL_PLAN.md)
- [Golden Contract Test Matrix — API-CONTRACT-004](GOLDEN_CONTRACT_TEST_MATRIX.md)
- [OpenAPI Diff Review Workflow — API-CONTRACT-005](OPENAPI_DIFF_REVIEW_WORKFLOW.md)
