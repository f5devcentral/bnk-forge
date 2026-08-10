# SEC-GOV-001: Operation Risk Classification Matrix

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Classify API endpoints and MCP tools by blast radius and operational risk to inform audit depth, confirmation requirements, and rate limiting.

---

## Risk Levels

| Level | Label | Description | Examples |
|-------|-------|-------------|---------|
| **R0** | Read-only | No side effects. Safe for any caller. | List projects, get cluster status |
| **R1** | Low-risk mutation | Creates or updates non-critical data. Easily reversible. | Create project, update settings |
| **R2** | Privileged mutation | Modifies infrastructure or security state. Requires audit. | Deploy module, install Helm chart |
| **R3** | Destructive | Deletes data or infrastructure. Difficult/impossible to reverse. | Destroy module, delete project, uninstall release |

---

## API Endpoint Classification

### R0 — Read-Only

| Domain | Endpoints |
|--------|-----------|
| Auth | `GET /api/auth/me` |
| Projects | `GET /api/projects`, `GET /api/projects/{id}` |
| Modules | `GET /api/projects/{id}/modules`, `GET /api/projects/{id}/modules/{mid}` |
| K8s | `GET /api/k8s/clusters`, `GET /api/k8s/clusters/{id}`, `GET /api/k8s/clusters/{id}/resources/*` |
| Helm | `GET /api/k8s/{id}/helm/releases`, `GET /api/k8s/{id}/helm/charts/*` |
| Fleet | `GET /api/k8s/fleet/*` |
| BNK | `GET /api/k8s/clusters/{id}/bnk/*` |
| Health | `GET /api/health`, `GET /api/system/health` |
| Tasks | `GET /api/tasks/*` |
| Drift | `GET /api/projects/{id}/drift/*`, `GET /api/clusters/{id}/drift/status` |
| Notifications | `GET /api/notifications/*` |
| Audit | `GET /api/audit/*` |
| System | `GET /api/system/info`, `GET /api/system/workers`, `GET /api/settings/*` |

### R1 — Low-Risk Mutation

| Domain | Endpoints | Rationale |
|--------|-----------|-----------|
| Auth | `POST /api/auth/login`, `POST /api/auth/change-password` | User-scoped, no infra impact |
| Projects | `POST /api/projects`, `PUT /api/projects/{id}` | Creates/edits metadata only |
| Settings | `PUT /api/settings/*`, `PUT /api/system/defaults` | App config, reversible |
| Notifications | `POST /api/notifications/mark-all-read` | User preference |
| Module Sources | `POST /api/module-sources`, `PUT /api/module-sources/{id}` | Catalog management |
| Drift | `PUT /api/projects/{id}/drift/settings` | Config only |
| Snapshots | `POST /api/k8s/clusters/{id}/config/snapshots` | Creates backup, additive |
| Benchmarks | `POST /api/benchmarks/configs`, `POST /api/benchmarks/targets` | Test config |
| SSH Creds | `POST /api/ssh-credentials`, `PUT /api/ssh-credentials/{id}` | Credential storage |

### R2 — Privileged Mutation

| Domain | Endpoints | Rationale |
|--------|-----------|-----------|
| Modules | `POST /api/projects/{id}/modules/{mid}/init` | Initializes Terraform workspace |
| Modules | `POST /api/projects/{id}/modules/{mid}/plan` | Generates execution plan |
| Modules | `POST /api/projects/{id}/modules/{mid}/apply` | **Deploys infrastructure** |
| Orchestration | `POST /api/projects/{id}/deploy-all` | **Deploys all modules** |
| Helm | `POST /api/k8s/{id}/helm/releases/install` | Installs K8s workloads |
| Helm | `POST /api/k8s/{id}/helm/releases/{name}/upgrade` | Upgrades running workloads |
| K8s | `POST /api/k8s/clusters` | Registers cluster access |
| K8s | `PUT /api/k8s/clusters/{id}` | Modifies cluster config |
| K8s | `POST /api/k8s/clusters/{id}/resources/*/scale` | Scales workloads |
| K8s | `PATCH /api/k8s/clusters/{id}/rollout/*` | Pauses/resumes rollouts |
| Stacks | `POST /api/stacks/instances/{id}/deploy` | Deploys stack |
| BNK Upgrade | `POST /api/k8s/clusters/{id}/bnk-upgrade` | Upgrades BNK firmware |
| Config Promote | `POST /api/k8s/fleet/promote` | Promotes config across fleet |
| Users | `POST /api/auth/users`, `PUT /api/auth/users/{id}` | Creates/modifies user access |

### R3 — Destructive

| Domain | Endpoints | Rationale |
|--------|-----------|-----------|
| Modules | `POST /api/projects/{id}/modules/{mid}/destroy` | **Destroys infrastructure** |
| Orchestration | `POST /api/projects/{id}/destroy-all` | **Destroys all modules** |
| Projects | `DELETE /api/projects/{id}` | Deletes project + all modules |
| K8s | `DELETE /api/k8s/clusters/{id}` | Removes cluster registration |
| Helm | `POST /api/k8s/{id}/helm/releases/{name}/uninstall` | Removes K8s workloads |
| Stacks | `POST /api/stacks/instances/{id}/destroy` | Destroys stack infra |
| Stacks | `DELETE /api/stacks/instances/{id}` | Deletes stack record |
| Users | `DELETE /api/auth/users/{id}` | Removes user access |
| Secrets | `DELETE /api/projects/{id}/secrets/{sid}` | Deletes encrypted secret |
| Module Sources | `DELETE /api/module-sources/{id}` | Removes module catalog source |
| System | `POST /api/system/database/cleanup` | Database maintenance |

---

## MCP Tool Classification

| Risk | Tools |
|------|-------|
| **R0** | `list_clusters`, `get_cluster_status`, `list_projects`, `get_project_modules`, `get_fleet_health`, `get_bnk_topology`, `get_bnk_health`, `list_helm_releases`, `get_connectivity_status` |
| **R1** | `create_project`, `update_settings`, `create_snapshot` |
| **R2** | `deploy_module`, `install_helm_release`, `scale_workload`, `promote_config` |
| **R3** | `destroy_module`, `delete_project`, `uninstall_helm_release` |

---

## Enforcement Rules

| Risk | Audit | Confirmation | Rate Limit | MCP Behavior |
|------|-------|-------------|------------|--------------|
| **R0** | Not logged | None | Standard (100/min) | Auto-execute |
| **R1** | Logged | None | Standard (100/min) | Auto-execute |
| **R2** | Logged + details | UI confirmation dialog | Moderate (30/min) | Execute with warning |
| **R3** | Logged + details + user | UI confirmation + type resource name | Strict (10/min) | Require human confirmation |
