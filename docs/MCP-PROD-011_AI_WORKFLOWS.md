# MCP-PROD-011: Curated High-Value AI Workflows

**Status:** Complete
**Version:** 2.11.0

---

## Purpose

Define exemplar AI-driven workflows that demonstrate the platform's MCP
value proposition. Each workflow is a sequence of MCP tool calls that
accomplishes a real operational task.

---

## Workflow 1: Cluster Health Investigation

**Trigger:** "Is my cluster healthy? What needs attention?"

**Tool sequence:**
1. `list_clusters` → Get all connected clusters
2. `get_fleet_health` → Overview of cluster health states
3. `get_cluster_resources(cluster_id, resource_type="pod")` → Check for failing pods
4. `get_drift_status(cluster_id)` → Check for configuration drift
5. `get_helm_releases(cluster_id)` → Check for outdated Helm releases

**Output:** Natural language summary of cluster health with actionable items.

**Eligibility:** All Tier 1 (read-only) — fully autonomous.

---

## Workflow 2: Project Status Report

**Trigger:** "What's the status of project X?"

**Tool sequence:**
1. `get_project(project_id)` → Project metadata
2. `list_modules(project_id)` → All modules and their states
3. `get_module_outputs(project_id, module_id)` → Output values for applied modules
4. `list_tasks(project_id, limit=5)` → Recent task history
5. `get_drift_status(project_id)` → Any configuration drift

**Output:** Project health summary with module states, recent activity, and drift status.

**Eligibility:** All Tier 1 (read-only) — fully autonomous.

---

## Workflow 3: Deployment Pipeline

**Trigger:** "Deploy the VPC module in project X"

**Tool sequence:**
1. `get_project(project_id)` → Verify project exists
2. `get_module(project_id, module_id)` → Check module state
3. `plan_module(project_id, module_id)` → Generate plan (Tier 2)
4. **Human confirmation:** "Plan shows +3 resources. Apply?"
5. `apply_module(project_id, module_id)` → Deploy (Tier 3 — needs confirmation)
6. `get_task_status(task_id)` → Poll until complete
7. `get_module_outputs(project_id, module_id)` → Show outputs

**Output:** Deployment result with resource outputs.

**Eligibility:** Mixed tiers — steps 1-3 autonomous, step 5 requires human confirmation.

---

## Workflow 4: Fleet-Wide Drift Check

**Trigger:** "Check all clusters for configuration drift"

**Tool sequence:**
1. `list_clusters` → All connected clusters
2. For each cluster:
   a. `get_drift_status(cluster_id)` → Drift detection results
   b. `get_cluster_resources(cluster_id, "deployment")` → Running vs desired state
3. Aggregate results across fleet

**Output:** Fleet-wide drift report highlighting clusters with drift.

**Eligibility:** All Tier 1 (read-only) — fully autonomous.

---

## Workflow 5: BNK Gateway Troubleshooting

**Trigger:** "Why is traffic not reaching my backend?"

**Tool sequence:**
1. `get_bnk_resources(cluster_id, "gateway")` → Check gateway status
2. `get_bnk_resources(cluster_id, "httproute")` → Check route configuration
3. `get_bnk_resources(cluster_id, "gatewayclass")` → Check gateway class
4. `get_cluster_resources(cluster_id, "pod", namespace="bnk-system")` → Check BNK pods
5. `get_cluster_resources(cluster_id, "service")` → Check service endpoints

**Output:** Diagnosis with specific fix recommendations.

**Eligibility:** All Tier 1 (read-only) — fully autonomous.

---

## Workflow 6: Helm Release Management

**Trigger:** "Upgrade nginx-ingress to the latest version"

**Tool sequence:**
1. `list_helm_releases(cluster_id)` → Find current release
2. `list_helm_repositories(cluster_id)` → Check repo is configured
3. `get_helm_release(cluster_id, release_name)` → Current values and version
4. **Human confirmation:** "Upgrade from v4.10.0 to v4.15.0?"
5. `upgrade_helm_release(cluster_id, release_name, version)` → Execute upgrade (Tier 3)
6. `get_helm_release(cluster_id, release_name)` → Verify new version

**Output:** Upgrade result with before/after comparison.

**Eligibility:** Mixed tiers — steps 1-3 autonomous, step 5 requires confirmation.

---

## Workflow Priority

| # | Workflow | User Value | Implementation Effort |
|---|---------|-----------|----------------------|
| 1 | Cluster Health Investigation | High | Low (all tools exist) |
| 2 | Project Status Report | High | Low (all tools exist) |
| 4 | Fleet-Wide Drift Check | High | Low (all tools exist) |
| 5 | BNK Gateway Troubleshooting | Medium | Low (all tools exist) |
| 3 | Deployment Pipeline | High | Medium (needs task polling) |
| 6 | Helm Release Management | Medium | Medium (needs upgrade tool) |
