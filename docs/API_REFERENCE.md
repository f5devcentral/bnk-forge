# BNK Forge — API Reference

Complete reference for the BNK Forge REST API. All endpoints require JWT authentication unless marked **public**.

> **Auto-generated from code audit on 2026-03-19.** If this doc drifts from code, regenerate from route files.

---

## Table of Contents

- [Authentication](#authentication)
- [Roles & Authorization](#roles--authorization)
- [Auth](#auth)
- [Projects](#projects)
- [Project Modules](#project-modules)
- [Module Execution](#module-execution)
- [Parallel Orchestration](#parallel-orchestration)
- [Deployment History](#deployment-history)
- [Project Variables](#project-variables)
- [Variable Mappings](#variable-mappings)
- [Project Secrets](#project-secrets)
- [Kubernetes Clusters](#kubernetes-clusters)
- [Kubernetes Resources](#kubernetes-resources)
- [F5 BNK](#f5-bnk)
- [TMM Debug](#tmm-debug)
- [SSH Tunnels](#ssh-tunnels)
- [Recovery](#recovery)
- [Helm](#helm)
- [Fleet / Config Export](#fleet--config-export)
- [Config Promotion](#config-promotion)
- [BNK Upgrade](#bnk-upgrade)
- [Drift Detection](#drift-detection)
- [QKView Diagnostics](#qkview-diagnostics)
- [Licensing](#licensing)
- [Runbooks](#runbooks)
- [Performance Benchmarks](#performance-benchmarks)
- [Module Library](#module-library)
- [Module Sources](#module-sources)
- [Registry](#registry)
- [Blueprints (Stacks)](#blueprints-stacks)
- [Snapshots](#snapshots)
- [Tasks & Operations](#tasks--operations)
- [State Viewer](#state-viewer)
- [SSH Credentials](#ssh-credentials)
- [Credential Templates](#credential-templates)
- [Cloud Auth (AWS/SSH)](#cloud-auth-awsssh)
- [Alert Channels](#alert-channels)
- [Notifications](#notifications)
- [Audit Log](#audit-log)
- [System Administration](#system-administration)
- [Backup & Restore](#backup--restore)
- [Operator (Legacy)](#operator-legacy)
- [Misc / Root](#misc--root)
- [WebSocket Endpoints](#websocket-endpoints)

---

## Authentication

All API requests require a JWT bearer token in the `Authorization` header:

```
Authorization: Bearer <token>
```

Obtain a token via `POST /api/auth/login`. Tokens expire after the configured session duration.

### Public Endpoints (no auth required)

| Endpoint | Description |
|----------|-------------|
| `POST /api/auth/login` | Authenticate and get a JWT token |
| `GET /api/system/health` | System health check |
| `POST /api/benchmarks/results` | Push benchmark results from aiperf CLI |
| `POST /api/benchmarks/results/aiperf` | Push raw aiperf profile export |

---

## Roles & Authorization

| Role | Level | Description |
|------|-------|-------------|
| **admin** | Highest | Full access — system settings, user management, all projects |
| **operator** | Mid | Deploy, manage own projects, credentials, auth templates |
| **viewer** | Lowest | Read-only access to all data |

### Ownership-Based Auth

Some endpoints use ownership checks instead of role checks:

| Dependency | Meaning |
|-----------|---------|
| `require_project_owner` | Must own the project (or be admin) |
| `require_module_owner` | Must own the module's parent project (or be admin) |
| `require_cluster_owner` | Must own the cluster's parent project (or be admin) |

---

## Auth

`backend/routes/auth.py` — Prefix: `/api/auth`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| POST | `/api/auth/login` | public | `LoginRequest` | `LoginResponse` | Authenticate and return JWT token |
| GET | `/api/auth/me` | user | — | `MeResponse` | Get current user profile |
| POST | `/api/auth/change-password` | user | `ChangePasswordRequest` | `SuccessResponse` | Change own password |
| POST | `/api/auth/users` | admin | `UserCreateRequest` | `UserCreateResponse` | Create a new user |
| GET | `/api/auth/users` | admin | — | `UserListWithCountsResponse` | List all users with project counts |
| PUT | `/api/auth/users/{user_id}` | admin | `UserUpdateRequest` | `UserUpdateResponse` | Update user role/email/status |
| DELETE | `/api/auth/users/{user_id}` | admin | — | `UserDeleteResponse` | Delete a user |

---

## Projects

`backend/routes/projects.py` — Prefix: `/api/projects`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/projects` | viewer | — | `ProjectListResponse` | List all projects |
| POST | `/api/projects` | operator | `ProjectCreate` | `ProjectMutationResponse` | Create a project |
| GET | `/api/projects/active` | viewer | — | `ActiveProjectResponse` | Get active project |
| GET | `/api/projects/{project_id}` | viewer | — | `ProjectDetailResponse` | Get project detail |
| PUT | `/api/projects/{project_id}` | owner | `ProjectUpdate` | `ProjectMutationResponse` | Update project |
| PUT | `/api/projects/{project_id}/dependencies` | owner | `list[ProjectDependencyItem]` | `ProjectDependenciesResponse` | Set cross-project deps |
| DELETE | `/api/projects/{project_id}` | owner | — | `SuccessResponse` | Delete project (query: `force`) |
| POST | `/api/projects/{project_id}/activate` | owner | — | `ProjectMutationResponse` | Set as active project |
| POST | `/api/projects/{project_id}/transfer` | owner | `TransferOwnershipRequest` | `TransferOwnershipResponse` | Transfer ownership |

---

## Project Modules

`backend/routes/project_modules.py` — Prefix: `/api/project-modules`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| POST | `/api/project-modules/validate-variables` | viewer | `ValidateVariablesRequest` | — | Validate variable values |
| GET | `/api/project-modules/project/{project_id}` | viewer | — | — | List modules in project |
| POST | `/api/project-modules/project/{project_id}/add` | owner | `AddModuleRequest` | — | Add module to project |
| PUT | `/api/project-modules/{module_id}` | module_owner | `UpdateModuleRequest` | — | Update module config |
| POST | `/api/project-modules/{module_id}/change-version` | module_owner | `ChangeModuleVersionRequest` | — | Re-pin module to another catalog version of its path (D-033; same source only) |
| DELETE | `/api/project-modules/{module_id}` | module_owner | — | — | Remove module |
| GET | `/api/project-modules/{module_id}/status` | viewer | — | — | Get module status |
| GET | `/api/project-modules/{module_id}/variables` | viewer | — | — | Get module variables |
| PUT | `/api/project-modules/{module_id}/dependencies` | module_owner | `SetDependenciesRequest` | — | Set module deps |
| GET | `/api/project-modules/{module_id}/dependencies` | viewer | — | — | Get module deps |
| GET | `/api/project-modules/{module_id}/dependents` | viewer | — | — | Get reverse deps |
| POST | `/api/project-modules/project/{project_id}/calculate-order` | owner | — | — | Auto-calculate deploy order |
| GET | `/api/project-modules/project/{project_id}/dependency-graph` | viewer | — | — | Get dep graph for viz |

---

## Module Execution

`backend/routes/project_execution.py` — Prefix: `/api/project-modules`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| POST | `/{module_id}/init` | module_owner | — | Run `tofu init` |
| GET | `/{module_id}/plan-status` | viewer | — | Get plan status |
| GET | `/{module_id}/validate` | viewer | — | Validate before plan/apply |
| POST | `/{module_id}/plan` | module_owner | — | Run `tofu plan` |
| POST | `/{module_id}/apply` | module_owner | `OpenTofuActionRequest` | Run `tofu apply` |
| POST | `/{module_id}/destroy` | module_owner | `OpenTofuActionRequest` | Run `tofu destroy` |
| POST | `/{module_id}/cancel` | module_owner | — | Cancel running operation |
| POST | `/{module_id}/deploy` | module_owner | — | Trigger deployment |
| POST | `/{module_id}/retry` | module_owner | — | Retry failed deployment |

All paths prefixed with `/api/project-modules`.

---

## Parallel Orchestration

`backend/routes/project_orchestration.py` — Prefix: `/api/projects`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/{project_id}/execution-plan` | viewer | — | Get layer-based execution plan |
| POST | `/{project_id}/deploy-all` | owner | `DeployAllRequest` | Deploy all modules in parallel layers |
| POST | `/{project_id}/destroy-all` | owner | `DestroyAllRequest` | Destroy all in reverse order |
| GET | `/{project_id}/parallel-executions/{exec_id}` | viewer | — | Get parallel execution status |
| GET | `/{project_id}/parallel-executions` | viewer | — | List recent parallel executions |
| GET | `/projects/modules/all` | viewer | — | List all modules across all projects |

All paths prefixed with `/api/projects`.

---

## Deployment History

`backend/routes/project_deployments.py` — Prefix: `/api/project-modules`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/{module_id}/logs` | viewer | Get deployment logs (query: `limit`, `level`) |
| GET | `/{module_id}/deployments` | viewer | Get deployment history (query: `action`, `status`, `limit`) |
| GET | `/project/{project_id}/deployments` | viewer | Get all deployments in project |
| GET | `/{module_id}/state-info` | viewer | Get state file metadata |
| GET | `/{module_id}/state-resources` | viewer | Get managed resources list |
| POST | `/{module_id}/recover-state` | module_owner | Recover state via `tofu refresh` |

---

## Project Variables

`backend/routes/project_variables.py` — Prefix: `/api/projects`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/{project_id}/variable-defaults` | viewer | — | `VariableDefaultsResponse` | Get variable defaults |
| PUT | `/{project_id}/variable-defaults` | owner | `VariableDefaultsUpdate` | — | Update variable defaults |
| DELETE | `/{project_id}/variable-defaults` | owner | — | `SuccessResponse` | Reset to defaults |
| GET | `/{project_id}/variables` | viewer | — | `ProjectVariablesResponse` | Get project variables |
| PUT | `/{project_id}/variables` | owner | `ProjectVariablesUpdate` | `ProjectVariablesMutationResponse` | Update project variables |

---

## Variable Mappings

`backend/routes/project_variable_mappings.py` — Prefix: `/api/project-modules`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/{module_id}/variable-mappings` | viewer | — | Get module variable mappings |
| POST | `/{module_id}/variable-mappings` | module_owner | `VariableMappingRequest` | Create mapping |
| PUT | `/{module_id}/variable-mappings/{mapping_id}` | module_owner | `VariableMappingRequest` | Update mapping |
| DELETE | `/{module_id}/variable-mappings/{mapping_id}` | module_owner | — | Delete mapping |
| GET | `/variable-mapping-templates` | viewer | — | List mapping templates |
| POST | `/variable-mapping-templates` | operator | `VariableMappingTemplateRequest` | Create template |
| POST | `/{module_id}/variable-mappings/apply-template/{template_id}` | module_owner | — | Apply template to module |

---

## Project Secrets

`backend/routes/project_secrets.py`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/projects/{project_id}/secrets` | viewer | — | List secrets (metadata only) |
| POST | `/api/projects/{project_id}/secrets/file` | owner | Multipart: file + form | Upload encrypted file secret |
| POST | `/api/projects/{project_id}/secrets/value` | owner | `ValueSecretCreate` | Create encrypted value secret |
| PUT | `/api/projects/{project_id}/secrets/{secret_id}/file` | owner | Multipart: optional file + form | Update file secret |
| PUT | `/api/projects/{project_id}/secrets/{secret_id}/value` | owner | `ValueSecretUpdate` | Update value secret |
| DELETE | `/api/projects/{project_id}/secrets/{secret_id}` | owner | — | Delete secret (soft delete) |
| GET | `/api/projects/{project_id}/secrets/required` | viewer | — | Get required vs satisfied secrets |

---

## Kubernetes Clusters

`backend/routes/k8s/clusters.py` — Prefix: `/api`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| POST | `/api/projects/{project_id}/k8s/clusters/detect-eks` | owner | — | Auto-detect EKS clusters from modules |
| POST | `/api/projects/{project_id}/k8s/clusters` | owner | `ClusterCreateRequest` | Add a K8s cluster |
| GET | `/api/k8s/clusters` | viewer | — | List all clusters (global) |
| GET | `/api/projects/{project_id}/k8s/clusters` | viewer | — | List project clusters |
| GET | `/api/k8s/clusters/{cluster_id}` | viewer | — | Get cluster detail |
| PUT | `/api/k8s/clusters/{cluster_id}` | cluster_owner | `ClusterUpdateRequest` | Update cluster |
| DELETE | `/api/k8s/clusters/{cluster_id}` | cluster_owner | — | Delete cluster |
| POST | `/api/k8s/clusters/{cluster_id}/refresh-kubeconfig` | cluster_owner | — | Refresh kubeconfig |
| POST | `/api/k8s/clusters/{cluster_id}/test` | cluster_owner | — | Test cluster connectivity |
| GET | `/api/k8s/clusters/{cluster_id}/namespaces` | viewer | — | List namespaces |
| GET | `/api/k8s/clusters/{cluster_id}/nodes/count` | viewer | — | Get node count |
| GET | `/api/k8s/resource-types` | viewer | — | List supported resource types (`ResourceTypeCatalogResponse`) |
| POST | `/api/k8s/clusters/{cluster_id}/scan` | cluster_owner | — | Scan cluster prerequisites (`ClusterScanEnvelope`) |
| POST | `/api/k8s/clusters/{cluster_id}/adaptive-modules` | cluster_owner | `AdaptiveModuleRequest` | Generate adaptive deploy plan |
| POST | `/api/k8s/clusters/{cluster_id}/adaptive-modules/from-scan` | cluster_owner | `AdaptiveModuleRequest` | Adaptive plan from cached scan |
| POST | `/api/k8s/clusters/{cluster_id}/bnk-config` | cluster_owner | `BnkClusterConfigCreateRequest` | Create or update BNK cluster config (tmfifo pool CIDR, join transport, CP host) |
| POST | `/api/k8s/clusters/{cluster_id}/bnk-members` | cluster_owner | `BnkClusterMemberAssignRequest` | Assign bare-metal hosts and DPUs to a BNK cluster with tmfifo IP allocation |

---

## Kubernetes Resources

`backend/routes/k8s/resources.py` — Prefix: `/api`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/k8s/clusters/{cluster_id}/resources/{resource_type}` | viewer | List resources by type (`ResourceListEnvelope`) |
| POST | `/api/k8s/clusters/{cluster_id}/resources/{resource_type}` | cluster_owner | Create resource from YAML |
| PUT | `/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}` | cluster_owner | Update resource YAML |
| DELETE | `/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}` | cluster_owner | Delete resource |
| PATCH | `/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}` | cluster_owner | Patch resource (3 strategies) |
| POST | `.../resources/{resource_type}/{resource_name}/label` | cluster_owner | Add/update labels |
| POST | `.../resources/{resource_type}/{resource_name}/annotate` | cluster_owner | Add/update annotations |
| POST | `/api/k8s/clusters/{cluster_id}/deployments/{name}/scale` | cluster_owner | Scale deployment |
| GET | `/api/k8s/clusters/{cluster_id}/pods/{pod_name}/logs` | viewer | Get pod logs (`PodLogsResponse`) |
| POST | `/api/k8s/clusters/{cluster_id}/pods/{pod_name}/restart` | cluster_owner | Restart pod (`PodRestartResponse`) |
| GET | `/api/k8s/clusters/{cluster_id}/pods/{pod_name}/containers` | viewer | List pod containers |
| GET | `.../resources/{resource_type}/{resource_name}/describe` | viewer | Describe resource (`ResourceDescribeEnvelope`) |
| GET | `/api/k8s/clusters/{cluster_id}/events` | viewer | Get cluster events (`ClusterEventsResponse`) |
| GET | `/api/k8s/clusters/{cluster_id}/top/pods` | viewer | Pod resource usage (`PodTopResponse`) |
| GET | `/api/k8s/clusters/{cluster_id}/top/nodes` | viewer | Node resource usage (`NodeTopResponse`) |
| GET | `.../deployments/{name}/rollout/history` | viewer | Rollout history |
| GET | `.../deployments/{name}/rollout/status` | viewer | Rollout status |
| POST | `.../deployments/{name}/rollout/undo` | cluster_owner | Rollback deployment |
| POST | `.../deployments/{name}/rollout/restart` | cluster_owner | Restart deployment |
| POST | `/api/k8s/clusters/{cluster_id}/nodes/{node_name}/cordon` | cluster_owner | Cordon node |
| POST | `/api/k8s/clusters/{cluster_id}/nodes/{node_name}/uncordon` | cluster_owner | Uncordon node |
| POST | `/api/k8s/clusters/{cluster_id}/nodes/{node_name}/drain` | cluster_owner | Drain node |

---

## F5 BNK

`backend/routes/k8s/f5bnk.py` — Prefix: `/api`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/k8s/clusters/{cluster_id}/f5bnk/data` | viewer | Unified BNK data (16 CRDs + pods) |
| GET | `/api/k8s/clusters/{cluster_id}/f5bnk/health` | viewer | BNK health dashboard |
| GET | `/api/k8s/clusters/{cluster_id}/f5bnk/gateway-topology` | viewer | Gateway topology graph |
| GET | `/api/k8s/clusters/{cluster_id}/f5bnk/policy-gateway-associations` | viewer | Policy-gateway map |

---

## TMM Debug

`backend/routes/k8s/tmm_debug.py` — Prefix: `/api`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `.../clusters/{cluster_id}/tmm-debug/pods` | viewer | — | List TMM pods with debug sidecar |
| POST | `.../clusters/{cluster_id}/tmm-debug/exec` | viewer | `TMMDebugExecRequest` | Raw command in debug sidecar |
| POST | `.../clusters/{cluster_id}/tmm-debug/tmctl` | viewer | `TMMDebugTmctlRequest` | Structured tmctl query |
| POST | `.../clusters/{cluster_id}/tmm-debug/configview` | viewer | `TMMDebugConfigviewRequest` | Inspect CR config |
| POST | `.../clusters/{cluster_id}/tmm-debug/configview/uuids` | viewer | `TMMDebugConfigviewUuidsRequest` | List config UUIDs |
| POST | `.../clusters/{cluster_id}/tmm-debug/bdt` | viewer | `TMMDebugBdtRequest` | bdt_cli networking diagnostic |

---

## SSH Tunnels

`backend/routes/k8s/tunnels.py` — Prefix: `/api`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/k8s/tunnels` | viewer | List all active SSH tunnels |
| GET | `/api/k8s/clusters/{cluster_id}/tunnel` | viewer | Get tunnel status for cluster |
| POST | `/api/k8s/clusters/{cluster_id}/tunnel/open` | cluster_owner | Open SSH tunnel |
| POST | `/api/k8s/clusters/{cluster_id}/tunnel/close` | cluster_owner | Close SSH tunnel |
| POST | `/api/k8s/tunnels/close-all` | admin | Close all tunnels |

---

## Recovery

`backend/routes/k8s/recovery.py` — Prefix: `/api`

| Method | Path | Auth | Response Model | Description |
|--------|------|------|----------------|-------------|
| GET | `.../clusters/{cluster_id}/recovery/status` | operator | `RecoveryStatusResponse` | Pre-flight recovery check |
| POST | `.../clusters/{cluster_id}/recovery/cwc-certs` | operator | `CWCCertResyncResponse` | Re-sync CWC certs from cert-manager |
| POST | `.../clusters/{cluster_id}/recovery/platform-restart` | operator | `PlatformRestartResponse` | Restart BNK platform components |

---

## Helm

`backend/routes/helm.py`

### Cluster-Scoped (per cluster)

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/k8s/{cluster_id}/helm/releases` | viewer | — | List releases |
| GET | `/api/k8s/{cluster_id}/helm/releases/{name}` | viewer | — | Get release detail |
| POST | `/api/k8s/{cluster_id}/helm/install` | cluster_owner | `InstallChartRequest` | Install chart |
| PUT | `/api/k8s/{cluster_id}/helm/releases/{name}/upgrade` | cluster_owner | `UpgradeReleaseRequest` | Upgrade release |
| POST | `/api/k8s/{cluster_id}/helm/releases/{name}/rollback` | cluster_owner | `RollbackReleaseRequest` | Rollback release |
| DELETE | `/api/k8s/{cluster_id}/helm/releases/{name}` | cluster_owner | — | Uninstall release |
| GET | `/api/k8s/{cluster_id}/helm/releases/{name}/history` | viewer | — | Revision history |
| GET | `/api/k8s/{cluster_id}/helm/releases/{name}/values` | viewer | — | Release values |
| GET | `/api/k8s/{cluster_id}/helm/releases/{name}/manifest` | viewer | — | Release manifest |
| POST | `/api/k8s/{cluster_id}/helm/releases/{name}/test` | cluster_owner | — | Run release tests |
| GET | `/api/k8s/{cluster_id}/helm/releases/{name}/compare` | viewer | — | Compare revisions |

### Global (repositories & charts)

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| POST | `/api/helm/repositories` | operator | `AddRepositoryRequest` | Add repository |
| POST | `/api/helm/repositories/update` | operator | — | Update all repos |
| GET | `/api/helm/repositories` | viewer | — | List repositories |
| DELETE | `/api/helm/repositories/{name}` | operator | — | Remove repository |
| GET | `/api/helm/charts/browse` | viewer | — | Browse charts |
| GET | `/api/helm/charts/search` | viewer | — | Search charts |
| POST | `/api/helm/charts/upload` | operator | Multipart `.tgz` | Upload custom chart |
| GET | `/api/helm/charts/uploaded` | viewer | — | List uploaded charts |
| DELETE | `/api/helm/charts/uploaded/{chart_id}` | operator | — | Delete uploaded chart |
| GET | `/api/helm/charts/uploaded/{chart_id}/values` | viewer | — | Get chart values.yaml |
| PUT | `/api/helm/charts/uploaded/{chart_id}/values` | operator | `str` body | Update chart values |
| POST | `/api/helm/charts/clone` | operator | — | Clone chart from repo |

---

## Fleet / Config Export

`backend/routes/config_export.py` — Prefix: `/api`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/clusters/{cluster_id}/bnk/export` | viewer | — | Export BNK config as YAML |
| GET | `/api/clusters/{cluster_id}/bnk/export/json` | viewer | — | Export BNK config as JSON |
| POST | `/api/clusters/{cluster_id}/bnk/import` | cluster_owner | `ConfigImportRequest` | Import BNK config via server-side apply |
| POST | `/api/clusters/bnk/diff` | operator | `DiffRequest` | Diff BNK config between 2 clusters |

---

## Config Promotion

`backend/routes/config_promotion.py` — Prefix: `/api/config`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| POST | `/api/config/promote` | operator | `PromoteRequest` | Promote config source → target (supports dry_run) |
| GET | `/api/config/promotions` | viewer | — | List promotion history |

---

## BNK Upgrade

`backend/routes/bnk_upgrade.py` — Prefix: `/api`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `.../clusters/{cluster_id}/bnk/upgrade/versions` | viewer | — | Available upgrade versions |
| GET | `.../clusters/{cluster_id}/bnk/upgrade/current` | viewer | — | Current BNK version + health |
| POST | `.../clusters/{cluster_id}/bnk/upgrade/plan` | cluster_owner | `CreateUpgradePlanRequest` | Create upgrade plan |
| POST | `.../clusters/{cluster_id}/bnk/upgrade/{id}/execute` | cluster_owner | — | Execute upgrade (async) |
| POST | `.../clusters/{cluster_id}/bnk/upgrade/{id}/rollback` | cluster_owner | — | Rollback failed upgrade (async) |
| POST | `.../clusters/{cluster_id}/bnk/upgrade/{id}/cancel` | cluster_owner | — | Cancel pending upgrade |
| GET | `.../clusters/{cluster_id}/bnk/upgrade/history` | viewer | — | Upgrade history |
| GET | `.../clusters/{cluster_id}/bnk/upgrade/{id}` | viewer | — | Upgrade detail |

All paths prefixed with `/api/k8s`.

---

## Drift Detection

`backend/routes/drift.py`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/projects/{id}/drift/settings` | viewer | — | `DriftSettingsResponse` | Get drift settings |
| PUT | `/api/projects/{id}/drift/settings` | owner | `DriftSettingsRequest` | `DriftSettingsResponse` | Update drift settings |
| POST | `/api/projects/{id}/drift/enable` | owner | — | `DriftSettingsResponse` | Enable drift detection |
| POST | `/api/projects/{id}/drift/disable` | owner | — | `DriftSettingsResponse` | Disable drift detection |
| GET | `/api/projects/{id}/drift/checks` | viewer | — | `list[DriftCheckResponse]` | Get drift check history |
| GET | `/api/drift/checks/{check_id}` | viewer | — | `DriftCheckResponse` | Get drift check detail |
| POST | `/api/projects/{id}/drift/check-now` | owner | `TriggerDriftCheckRequest` | — | Trigger immediate check |
| POST | `/api/project-modules/{id}/drift/check-now` | module_owner | — | — | Check single module |
| GET | `/api/drift/summary` | viewer | — | `DriftSummaryResponse` | Global drift summary |
| GET | `/api/drift/recent` | viewer | — | `list[DriftCheckResponse]` | Recent drift detections |
| GET | `/api/projects/{id}/drift/summary` | viewer | — | `DriftSummaryResponse` | Project drift summary |
| GET | `/api/drift/stats` | viewer | — | — | Drift statistics |
| GET | `/api/clusters/{id}/drift/status` | viewer | — | — | Cluster drift status |

---

## QKView Diagnostics

`backend/routes/qkview.py` — Prefix: `/api/qkview`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/qkview/check` | viewer | — | `QKViewCheckResponse` | Check CWC availability |
| GET | `/api/licensing/{cluster_id}/cwc-setup-status` | viewer | path `cluster_id` | `CWCAPISetupStatusResponse` | Check shared CWC API mTLS setup |
| POST | `/api/licensing/{cluster_id}/cwc-setup` | operator | path `cluster_id` | `CWCAPISetupResponse` | Run shared CWC API mTLS setup |
| GET | `/api/qkview/list` | viewer | — | `QKViewListResponse` | List QKView jobs |
| POST | `/api/qkview/create` | operator | `QKViewCreateRequest` | `QKViewCreateResponse` | Create QKView collection |
| GET | `/api/qkview/{id}` | viewer | — | `QKViewGetResponse` | Get QKView detail |
| GET | `/api/qkview/{id}/status` | viewer | — | `QKViewStatusResponse` | Get job status |
| GET | `/api/qkview/{id}/download` | viewer | — | binary (gzip) | Download QKView tarball |
| DELETE | `/api/qkview/{id}` | operator | — | `QKViewDeleteResponse` | Delete QKView |
| POST | `/api/qkview/{id}/cancel` | operator | — | `QKViewCancelResponse` | Cancel running job |
| POST | `/api/qkview/cleanup-pods` | operator | — | `QKViewCleanupResponse` | Clean up client pods |

All QKView endpoints accept `cluster_id` as a query parameter.

---

## Licensing

`backend/routes/licensing.py` — Prefix: `/api/licensing`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/licensing/{cluster_id}/status` | viewer | — | `LicenseStatusResponse` | License + telemetry status |
| GET | `/api/licensing/{cluster_id}/report` | viewer | — | `LicenseReportResponse` | Telemetry report |
| POST | `/api/licensing/{cluster_id}/activate` | operator | `LicenseActivateRequest` | `LicenseActionResponse` | Activate/switch license |
| POST | `/api/licensing/{cluster_id}/receipt` | operator | `LicenseReceiptRequest` | `LicenseActionResponse` | Send signed receipt |
| GET | `/api/licensing/{cluster_id}/cwc-status` | viewer | — | `CWCStatusResponse` | Full CWC connectivity check |

---

## Runbooks

`backend/routes/runbooks.py` — Prefix: `/api`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/runbooks` | viewer | — | List available runbooks |
| POST | `/api/runbooks/{runbook_id}/run` | viewer | `RunbookExecuteRequest` | Execute runbook against cluster |

---

## Performance Benchmarks

`backend/routes/benchmarks.py`

### Results

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| POST | `/api/benchmarks/results` | public | `BenchmarkResultPush` | `BenchmarkResultPushResponse` | Push benchmark results |
| POST | `/api/benchmarks/results/aiperf` | public | raw JSON body | `BenchmarkResultPushResponse` | Push raw aiperf export |

### Configs

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/benchmarks/configs` | viewer | — | `list[BenchmarkConfigResponse]` | List configs |
| GET | `/api/benchmarks/configs/{id}` | viewer | — | `BenchmarkConfigResponse` | Get config |
| POST | `/api/benchmarks/configs` | public | `BenchmarkConfigCreate` | `BenchmarkConfigResponse` | Create config |
| PUT | `/api/benchmarks/configs/{id}` | public | `BenchmarkConfigUpdate` | `BenchmarkConfigResponse` | Update config |
| DELETE | `/api/benchmarks/configs/{id}` | public | — | 204 | Delete config |

### Runs

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/benchmarks/runs` | viewer | — | `BenchmarkRunListResponse` | List runs (filterable) |
| GET | `/api/benchmarks/runs/{id}` | viewer | — | `BenchmarkRunDetailResponse` | Get run with results |
| POST | `/api/benchmarks/runs` | public | `BenchmarkRunCreate` | `BenchmarkRunResponse` | Create run |
| POST | `/api/benchmarks/runs/{id}/cancel` | public | — | `BenchmarkRunResponse` | Cancel run |
| DELETE | `/api/benchmarks/runs/{id}` | public | — | 204 | Delete run |

### Agents

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| POST | `/api/benchmarks/agents` | public | `BenchmarkAgentRegister` | `BenchmarkAgentResponse` | Register agent |
| GET | `/api/benchmarks/agents` | viewer | — | `list[BenchmarkAgentResponse]` | List agents |
| GET | `/api/benchmarks/agents/{id}` | viewer | — | `BenchmarkAgentResponse` | Get agent |
| DELETE | `/api/benchmarks/agents/{id}` | public | — | 204 | Deregister agent |

### Analysis

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| POST | `/api/benchmarks/compare` | viewer | `BenchmarkCompareRequest` | `BenchmarkCompareResponse` | Compare runs |
| GET | `/api/benchmarks/summary` | viewer | — | `BenchmarkSummaryResponse` | Dashboard summary |

### Targets & Proxies

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/benchmarks/targets` | viewer | — | `BenchmarkTargetListResponse` | List targets |
| GET | `/api/benchmarks/targets/{id}` | viewer | — | `BenchmarkTargetDetailResponse` | Get target + proxies |
| POST | `/api/benchmarks/targets` | public | `BenchmarkTargetCreate` | `BenchmarkTargetResponse` | Create target |
| PUT | `/api/benchmarks/targets/{id}` | public | `BenchmarkTargetUpdate` | `BenchmarkTargetResponse` | Update target |
| DELETE | `/api/benchmarks/targets/{id}` | public | — | 204 | Delete target |
| POST | `/api/benchmarks/targets/{id}/validate` | public | — | `BenchmarkTargetResponse` | Validate target |
| POST | `/api/benchmarks/discover-targets` | public | `DiscoverTargetsRequest` | `DiscoverTargetsResponse` | Scan cluster for LLM services |
| POST | `/api/benchmarks/targets/{id}/discover-proxies` | public | — | `ProxyDiscoveryResponse` | Scan for existing proxies |
| GET | `/api/benchmarks/targets/{id}/proxies` | viewer | — | `list[ProxyDeploymentResponse]` | List proxies |
| GET | `/api/benchmarks/targets/{id}/proxies/{proxy_id}` | viewer | — | `ProxyDeploymentResponse` | Get proxy |
| POST | `/api/benchmarks/targets/{id}/proxies` | public | `ProxyDeployRequest` | `ProxyDeploymentResponse` | Deploy proxy |
| PUT | `/api/benchmarks/targets/{id}/proxies/{proxy_id}` | public | `ProxyDeploymentUpdate` | `ProxyDeploymentResponse` | Update proxy |
| DELETE | `/api/benchmarks/targets/{id}/proxies/{proxy_id}` | public | — | 202 | Undeploy proxy |
| POST | `.../proxies/{proxy_id}/redeploy` | public | — | `ProxyDeploymentResponse` | Redeploy proxy |
| GET | `.../proxies/{proxy_id}/task-status` | viewer | — | `ProxyTaskStatusResponse` | Poll deploy status |
| POST | `.../proxies/{proxy_id}/run` | public | `TriggerRunRequest` | `TriggerRunResponse` | Trigger benchmark run |

---

## Module Library

`backend/routes/module_library.py` — Prefix: `/api/module-library`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/module-library` | viewer | — | List modules (filter: `search`, `category`, `provider`) |
| GET | `/api/module-library/categories` | viewer | — | Get categories with counts |
| GET | `/api/module-library/providers` | viewer | — | Get providers with counts |
| POST | `/api/module-library/sync` | operator | — | Sync from git sources |
| GET | `/api/module-library/version` | viewer | — | Current vs latest version |
| POST | `/api/module-library/user-modules/validate` | operator | `GitSourceValidation` | Validate git source |
| POST | `/api/module-library/user-modules/extract-metadata` | operator | `MetadataExtraction` | Extract module metadata |
| POST | `/api/module-library/user-modules` | operator | `UserModuleCreate` | Add custom module |
| GET | `/api/module-library/user-modules/{id}/versions` | viewer | — | List module versions |
| PUT | `/api/module-library/user-modules/{id}/version` | operator | `VersionUpdate` | Change module version |
| DELETE | `/api/module-library/user-modules/{id}` | operator | — | Delete custom module |
| GET | `/api/module-library/{id}` | viewer | — | Module detail |
| GET | `/api/module-library/{id}/variables` | viewer | — | Module variable defs |
| GET | `/api/module-library/{id}/smart-defaults` | viewer | — | Auto-generate defaults |

---

## Module Sources

`backend/routes/module_sources.py` — Prefix: `/api/module-sources`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/module-sources` | viewer | — | `list[ModuleSourceResponse]` | List sources |
| GET | `/api/module-sources/{id}` | viewer | — | `ModuleSourceResponse` | Get source |
| POST | `/api/module-sources` | operator | `ModuleSourceCreate` | `ModuleSourceResponse` | Create source |
| PUT | `/api/module-sources/{id}` | operator | `ModuleSourceUpdate` | `ModuleSourceResponse` | Update source |
| DELETE | `/api/module-sources/{id}` | operator | — | — | Delete source + modules |
| GET | `/api/module-sources/{id}/modules` | viewer | — | — | List source modules |
| POST | `/api/module-sources/{id}/sync` | operator | — | — | Sync source |

---

## Registry

`backend/routes/registry.py` — Prefix: `/api/registry`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/registry/search` | viewer | — | Search OpenTofu/Terraform Registry |
| GET | `/api/registry/modules/{ns}/{name}/{provider}` | viewer | — | Get registry module |
| GET | `/api/registry/modules/{ns}/{name}/{provider}/versions` | viewer | — | List registry versions |
| POST | `/api/registry/import` | operator | `RegistryModuleImport` | Import module to library |
| GET | `/api/registry/modules/{id}/check-updates` | viewer | — | Check for newer version |
| POST | `/api/registry/modules/{id}/upgrade` | operator | — | Upgrade module version |

---

## Blueprints (Stacks)

`backend/routes/stacks.py` — Prefix: `/api/stacks`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/stacks/templates` | viewer | — | `list[StackTemplateListResponse]` | List templates |
| GET | `/api/stacks/templates/{slug}` | viewer | — | `StackTemplateResponse` | Get template |
| GET | `/api/stacks/templates/{slug}/preview` | viewer | — | `StackPreviewResponse` | Preview modules |
| GET | `/api/stacks/templates/{slug}/required-inputs` | viewer | — | — | Required user inputs |
| GET | `/api/stacks/templates/{slug}/check-prerequisites` | viewer | — | — | Check prerequisites |
| POST | `/api/stacks/projects/{id}/stacks` | owner | `StackInstanceCreate` | `StackInstanceResponse` | Create stack instance |
| GET | `/api/stacks/projects/{id}/stacks` | viewer | — | `list[StackInstanceResponse]` | List stacks in project |
| GET | `/api/stacks/projects/{id}/stacks/{stack_id}` | viewer | — | `StackInstanceResponse` | Get stack detail |
| POST | `/api/stacks/projects/{id}/stacks/{stack_id}/deploy` | owner | — | — | Start stack deploy |
| POST | `/api/stacks/projects/{id}/stacks/{stack_id}/run-deploy` | owner | — | — | Full deploy (init+apply) |
| GET | `/api/stacks/projects/{id}/stacks/{stack_id}/status` | viewer | — | `StackStatusResponse` | Get deploy status |
| DELETE | `/api/stacks/projects/{id}/stacks/{stack_id}` | owner | — | — | Delete stack |
| POST | `/api/stacks/projects/{id}/save-as-template` | owner | `SaveAsTemplateRequest` | `StackTemplateResponse` | Save project as template |
| POST | `/api/stacks/templates/{id}/duplicate` | operator | `DuplicateTemplateRequest` | `StackTemplateResponse` | Duplicate template |
| GET | `/api/stacks/templates/{id}/export` | viewer | — | — | Export as JSON |
| POST | `/api/stacks/templates/import` | operator | `ImportTemplateRequest` | `StackTemplateResponse` | Import from JSON |
| PATCH | `/api/stacks/templates/{id}/publish` | operator | `PublishTemplateRequest` | `StackTemplateResponse` | Publish/unpublish |

---

## Snapshots

`backend/routes/snapshots.py` — Prefix: `/api`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/projects/{id}/snapshots` | viewer | — | List project snapshots |
| POST | `/api/projects/{id}/snapshots` | owner | `CreateSnapshotRequest` | Create manual snapshot |
| GET | `/api/snapshots/{id}` | viewer | — | Get snapshot detail |
| POST | `/api/snapshots/{id}/restore` | admin | — | Restore from snapshot (destructive) |
| POST | `/api/snapshots/diff` | viewer | `DiffSnapshotsRequest` | Diff two snapshots |
| DELETE | `/api/snapshots/{id}` | admin | — | Delete snapshot |

---

## Tasks & Operations

`backend/routes/tasks.py` — Prefix: `/api/tasks`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/tasks` | viewer | List tasks (filter: `project_id`, `status`, `task_type`, `limit`) |
| GET | `/api/tasks/{task_id}` | viewer | Get task detail with logs |
| POST | `/api/tasks/{task_id}/cancel` | viewer | Cancel running task |
| GET | `/api/tasks/stats/summary` | viewer | Task statistics |
| DELETE | `/api/tasks/cleanup` | viewer | Delete old completed tasks |

---

## State Viewer

`backend/routes/state_viewer.py` — Prefix: `/api/state`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/state/module/{id}` | viewer | Parsed Terraform state |
| GET | `/api/state/module/{id}/resources` | viewer | List state resources |
| GET | `/api/state/module/{id}/resource/{address}` | viewer | Resource detail |
| GET | `/api/state/module/{id}/graph` | viewer | Resource dependency graph |
| GET | `/api/state/module/{id}/outputs` | viewer | Module outputs (sensitive masked) |
| POST | `/api/state/module/{id}/refresh` | module_owner | Refresh state from fs |
| GET | `/api/state/module/{id}/raw` | viewer | Raw state file |
| DELETE | `/api/state/module/{id}/reset` | module_owner | Reset module state |
| GET | `/api/state/project/{id}/modules` | viewer | All modules with state status |

---

## SSH Credentials

`backend/routes/ssh_credentials.py` — Prefix: `/api/ssh-credentials`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| POST | `/api/ssh-credentials/setup` | operator | `SSHCredentialSetup` | `SSHCredentialResponse` | Auto-setup (generate + install key) |
| GET | `/api/ssh-credentials` | viewer | — | `list[SSHCredentialResponse]` | List credentials |
| GET | `/api/ssh-credentials/{id}` | viewer | — | `SSHCredentialResponse` | Get credential |
| POST | `/api/ssh-credentials` | operator | `SSHCredentialCreate` | `SSHCredentialResponse` | Create credential |
| PUT | `/api/ssh-credentials/{id}` | operator | `SSHCredentialUpdate` | `SSHCredentialResponse` | Update credential |
| DELETE | `/api/ssh-credentials/{id}` | operator | — | 204 | Delete credential |
| POST | `/api/ssh-credentials/{id}/test` | operator | — | — | Test connectivity |
| POST | `/api/ssh-credentials/{id}/probe-kubeconfig` | operator | — | — | SSH + retrieve kubeconfig |

---

## Credential Templates

`backend/routes/credential_templates.py` — Prefix: `/api/credential-templates`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/credential-templates` | viewer | — | `list[CredentialTemplateResponse]` | List templates |
| GET | `/api/credential-templates/{id}` | viewer | — | `CredentialTemplateResponse` | Get template |
| POST | `/api/credential-templates` | operator | `CredentialTemplateCreate` | `CredentialTemplateResponse` | Create template |
| PUT | `/api/credential-templates/{id}` | operator | `CredentialTemplateUpdate` | `CredentialTemplateResponse` | Update template |
| DELETE | `/api/credential-templates/{id}` | operator | — | 204 | Delete template |
| POST | `/api/credential-templates/{id}/test` | operator | — | — | Test credentials |
| POST | `/api/credential-templates/{id}/authenticate-sso` | operator | — | — | Start SSO flow |
| POST | `/api/credential-templates/{id}/poll-sso` | operator | `SSOPollRequest` | — | Poll SSO completion |
| POST | `/api/credential-templates/{id}/refresh-sso` | operator | — | — | Refresh SSO tokens |
| GET | `/api/credential-templates/{id}/sso-status` | viewer | — | — | SSO auth status |

---

## Cloud Auth (AWS/SSH)

`backend/routes/cloud_auth.py` — Prefix: `/api/cloud-auth` — All endpoints require **operator** role.

### AWS SSO

| Method | Path | Request Body | Description |
|--------|------|-------------|-------------|
| POST | `/api/cloud-auth/aws/sso/initiate` | `AWSSSOInitiateRequest` | Start device auth flow |
| POST | `/api/cloud-auth/aws/sso/poll` | `AWSSSOPollRequest` | Poll for access token |
| POST | `/api/cloud-auth/aws/accounts` | `AWSListAccountsRequest` | List AWS accounts |
| POST | `/api/cloud-auth/aws/accounts/roles` | `AWSListRolesRequest` | List account roles |
| POST | `/api/cloud-auth/aws/credentials` | `AWSCredentialsRequest` | Get temp credentials |
| POST | `/api/cloud-auth/aws/assume-role` | `AWSAssumeRoleRequest` | Assume IAM role via STS |
| GET | `/api/cloud-auth/aws/credentials/{project_id}` | — | Get stored credentials |
| DELETE | `/api/cloud-auth/aws/credentials/{project_id}` | — | Remove credentials |

### SSH

| Method | Path | Request Body | Description |
|--------|------|-------------|-------------|
| POST | `/api/cloud-auth/ssh/configure` | `SSHConfigureRequest` | Configure SSH for project |
| POST | `/api/cloud-auth/ssh/test` | `SSHTestRequest` | Test SSH connection |
| POST | `/api/cloud-auth/ssh/execute` | `SSHExecuteRequest` | Execute remote command |
| POST | `/api/cloud-auth/ssh/generate-key` | `SSHKeyGenerateRequest` | Generate key pair |
| GET | `/api/cloud-auth/ssh/credentials/{project_id}` | — | Get SSH credentials |
| DELETE | `/api/cloud-auth/ssh/credentials/{project_id}` | — | Remove SSH credentials |

### Credential Status

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cloud-auth/credentials/status/{project_id}` | Credential expiration status |
| POST | `/api/cloud-auth/credentials/refresh/{project_id}` | Refresh credentials |
| GET | `/api/cloud-auth/credentials/check-all` | Check all project credentials |

---

## Alert Channels

`backend/routes/alert_channels.py` — Prefix: `/api/alert-channels`

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/alert-channels` | viewer | — | List channels |
| GET | `/api/alert-channels/{id}` | viewer | — | Get channel + recent history |
| POST | `/api/alert-channels` | operator | `AlertChannelCreate` | Create channel |
| PUT | `/api/alert-channels/{id}` | operator | `AlertChannelUpdate` | Update channel |
| DELETE | `/api/alert-channels/{id}` | operator | — | Delete channel |
| POST | `/api/alert-channels/{id}/test` | operator | — | Send test alert |
| POST | `/api/alert-channels/{id}/toggle` | operator | — | Toggle enabled |
| GET | `/api/alert-channels/history/recent` | viewer | — | Recent alerts (all channels) |
| GET | `/api/alert-channels/{id}/history` | viewer | — | Channel alert history |

---

## Notifications

`backend/routes/notifications.py` — Prefix: `/api/notifications`

| Method | Path | Auth | Request Body | Response Model | Description |
|--------|------|------|-------------|----------------|-------------|
| GET | `/api/notifications` | viewer | — | `list[NotificationResponse]` | Get notifications |
| GET | `/api/notifications/unread-count` | viewer | — | `UnreadCountResponse` | Unread count |
| PATCH | `/api/notifications/{id}/read` | viewer | — | `NotificationActionResponse` | Mark as read |
| POST | `/api/notifications/mark-all-read` | viewer | — | `NotificationActionResponse` | Mark all read |
| DELETE | `/api/notifications/{id}` | viewer | — | `NotificationActionResponse` | Delete notification |
| POST | `/api/notifications` | viewer | `NotificationCreate` | `NotificationResponse` | Create notification |

---

## Audit Log

`backend/routes/audit.py` — Prefix: `/api/audit`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/audit` | user | Paginated audit logs (admins see all, others see own) |
| GET | `/api/audit/stats` | user | Audit statistics (query: `days`) |
| GET | `/api/audit/filters` | user | Available filter values |

Query params for `GET /api/audit`: `page`, `page_size`, `user`, `action`, `resource_type`, `resource_id`, `status`, `since`, `until`, `search`.

---

## System Administration

`backend/routes/system.py` — Prefix: `/api/system` — All endpoints require **admin** except `/health`.

| Method | Path | Auth | Request Body | Description |
|--------|------|------|-------------|-------------|
| GET | `/api/system/health` | public | — | System health status |
| GET | `/api/system/queue-metrics` | admin | — | Task queue metrics |
| GET | `/api/system/performance` | admin | — | Performance metrics |
| GET | `/api/system/errors` | admin | — | Recent errors |
| GET | `/api/system/database/stats` | admin | — | Database statistics |
| POST | `/api/system/database/cleanup` | admin | `CleanupRequest` | Clean old records |
| POST | `/api/system/database/vacuum` | admin | — | Run VACUUM ANALYZE |
| GET | `/api/system/workspaces/stats` | admin | — | Workspace statistics |
| GET | `/api/system/workspaces/orphaned` | admin | — | List orphaned workspaces |
| POST | `/api/system/workspaces/cleanup` | admin | — | Clean orphaned workspaces |
| GET | `/api/system/workspaces/locks` | admin | — | List workspace locks |
| GET | `/api/system/workspaces/locks/{project_id}/{module_id}` | admin | — | Check specific lock |
| DELETE | `/api/system/workspaces/locks/{project_id}/{module_id}` | admin | — | Force-release lock |
| POST | `/api/system/containers/restart` | admin | `RestartContainersRequest` | Restart Docker containers |
| GET | `/api/system/containers/status` | admin | — | Container status |
| GET | `/api/system/defaults` | admin | — | Get system defaults |
| GET | `/api/system/defaults/status` | admin | — | Check defaults configured |
| PUT | `/api/system/defaults` | admin | `DefaultsUpdateRequest` | Batch update defaults |
| PUT | `/api/system/defaults/{key}` | admin | — | Update single default |
| GET | `/api/system/version` | admin | — | Current + available versions |
| GET | `/api/system/upgrade/status` | admin | — | Upgrade state |
| GET | `/api/system/upgrade/verify` | admin | — | Post-upgrade verification |
| POST | `/api/system/upgrade` | admin | — | Trigger system upgrade |

---

## Backup & Restore

`backend/routes/system.py` — Prefix: `/api/system` — All endpoints require **admin**.

### POST /api/system/backup

Create a backup archive containing the database and encryption key.

**Authentication:** Admin only

**Request Body:**
```json
{
  "passphrase": "your-secure-passphrase-here"
}
```

**Response:** Binary file download (`.tar.gz`)

**Errors:**
- `400` — Passphrase too short (< 12 characters)
- `409` — Another backup/restore operation in progress
- `500` — Backup failed (pg_dump error, encryption key not found)

---

### GET /api/system/backup/status

Check if a backup or restore operation is in progress.

**Authentication:** Admin only

**Response:**
```json
{
  "in_progress": false,
  "operation": null,
  "started_at": null,
  "message": null
}
```

When an operation is in progress:
```json
{
  "in_progress": true,
  "operation": "restore",
  "started_at": "2026-04-13T12:00:00Z",
  "message": "Database restore in progress. Please wait..."
}
```

---

### POST /api/system/restore

Restore from a backup archive. This is a destructive operation.

**Authentication:** Admin only

**Request:** Multipart form data
- `file` — The backup archive (`.tar.gz`)
- `passphrase` — The passphrase used when creating the backup

**Response:**
```json
{
  "status": "success",
  "tables_restored": 42,
  "migrations_applied": ["a1b2c3d4"],
  "warnings": ["Archive from version 2.1.0, restoring to 2.2.0"],
  "restart_triggered": true
}
```

**Errors:**
- `400` — Invalid passphrase, invalid archive format, incompatible format version
- `409` — Another restore operation in progress
- `500` — Restore failed, migration failed

**Note:** After successful restore, the backend restarts automatically. The frontend should poll `/api/system/health` to detect when the system is back online.

---

### GET /api/system/maintenance

Check maintenance mode status. No authentication required.

**Response:**
```json
{
  "maintenance_mode": false,
  "message": null,
  "started_at": null
}
```

When in maintenance mode:
```json
{
  "maintenance_mode": true,
  "message": "Database restore in progress. Please wait...",
  "started_at": "2026-04-13T12:00:00Z"
}
```

---

## Operator (Legacy)

These endpoints support the legacy operator polling model (superseded by D3 kubeconfig-first).

`backend/routes/operator_polling.py` — Prefix: `/api/operators`

Auth: Operator registration tokens (not user JWTs).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/operators/register-poll` | Register polling-mode operator |
| GET | `/api/operators/{id}/poll` | Poll for commands (+ heartbeat) |
| POST | `/api/operators/{id}/results/{cmd_id}` | Submit command result |
| POST | `/api/operators/{id}/heartbeat` | Submit heartbeat |
| POST | `/api/operators/{id}/health` | Submit health report |

---

## Misc / Root

`backend/routes/api.py`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/` | viewer | API version |
| GET | `/health` | viewer | Health check |
| GET | `/api/health` | viewer | Health check (under /api) |
| GET | `/api/logs` | viewer | List execution logs |
| GET | `/api/logs/{log_name}` | viewer | Get log content |
| GET | `/api/version` | viewer | OpenTofu version (`VersionResponse`) |
| GET | `/api/providers` | viewer | Cloud provider summary |
| GET | `/api/sync/status` | viewer | Sync status |
| GET | `/api/settings` | admin | Get all settings (`SettingsResponse`) |
| PUT | `/api/settings/{key}` | admin | Update setting |
| PUT | `/api/settings` | admin | Batch update settings |
| POST | `/api/aws/auth-method` | admin | Set AWS auth method |
| GET | `/api/aws/auth-method` | admin | Get AWS auth config |
| GET | `/api/database/stats` | admin | Database stats |

---

## WebSocket Endpoints

| Path | Auth | Description |
|------|------|-------------|
| `/ws/k8s/clusters/{cluster_id}/pods/{pod_name}/exec` | JWT query param | Interactive pod terminal |
| `/ws/k8s/clusters/{cluster_id}/pods/{pod_name}/logs/follow` | JWT query param | Follow pod logs |
| `/ws/benchmarks/agents/{agent_id}` | none | Agent connection (commands/heartbeats) |
| `/ws/benchmarks/runs/{run_id}` | none | Watch benchmark run progress |
| `/ws/operator` | operator token | Operator bidirectional commands |
| `/api/notifications/ws/deployments` | none | Deployment status broadcasts |
| `/api/tasks/ws/tasks` | none | Real-time task updates |

---

## Statistics

| Metric | Count |
|--------|-------|
| Route files | 37 |
| HTTP endpoints | ~400 |
| WebSocket endpoints | 7 |
| Endpoints with `response_model` | ~103 |
| Endpoints with untyped responses | ~297 |
| Pydantic schema files (`backend/schemas/`) | 10 |

---

## Schema Locations

Request and response models are defined in two places:

1. **`backend/schemas/`** — Shared Pydantic models (auth, benchmarks, drift, helm, k8s, projects, stacks, system)
2. **Inline in route files** — Many request/response classes are defined at the top of `backend/routes/*.py`

When looking up a schema, always check **both** locations.

---

## Keeping This Document Current

This document was generated from a manual audit of all route files. To verify accuracy:

```bash
# Count routes in code
grep -r "@router\.\|@public_router\.\|@ws_router\." backend/routes/ | wc -l

# Regenerate OpenAPI spec
python scripts/generate-openapi.py

# Compare with this doc
# If adding/removing endpoints, update this file
```

> **Agent rule:** When adding, removing, or changing API endpoints, update this file in the same commit.
