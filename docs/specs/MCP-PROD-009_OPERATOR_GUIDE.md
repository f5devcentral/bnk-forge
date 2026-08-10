# MCP-PROD-009: MCP Operator Guide

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Document safe adoption patterns and troubleshooting guidance for MCP consumers (AI agents, CLI tools, integrations).

---

## Quick Start

### 1. Connection

BNK-Forge MCP server is available at `http://localhost:8081/mcp` (Streamable HTTP).

```json
{
  "mcpServers": {
    "bnk-forge": {
      "url": "http://your-server:8081/mcp",
      "transport": "streamable-http"
    }
  }
}
```

### 2. Authentication

The MCP server uses the same JWT authentication as the main API. Obtain a token via login:

```bash
TOKEN=$(curl -sf -X POST https://your-server/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"your-user","password":"your-pass"}' | jq -r .access_token)
```

The MCP server bootstraps its own token on startup using configured credentials.

### 3. Health Check

```bash
make mcp-readiness
# Or manually:
make smoke-mcp-live
```

---

## Tool Safety Classification

Every MCP tool has a safety classification (per SEC-GOV-001):

| Risk Class | Description | MCP Behavior |
|------------|-------------|--------------|
| `read_only` | No side effects | Auto-execute, safe for any query |
| `low_risk_mutation` | Creates/updates non-critical data | Auto-execute with logging |
| `privileged_mutation` | Modifies infrastructure | Execute with warning in response |
| `destructive` | Deletes data/infrastructure | **Require human confirmation** |

### Safe Tools (read_only)

These tools can be called freely without risk:
- `list_clusters` — List all K8s clusters
- `get_cluster_status` — Get cluster health and connectivity
- `list_projects` — List all projects
- `get_project_modules` — Get modules for a project
- `get_fleet_health` — Fleet-wide health summary
- `get_bnk_topology` — BNK component topology
- `get_bnk_health` — BNK health diagnostics
- `list_helm_releases` — Helm releases on a cluster
- `get_connectivity_status` — Connectivity probe results
- `bnk_telemetry_report` — Telemetry data

### Mutating Tools (use with care)

These tools modify state and should include confirmation:
- `deploy_module` — Deploys infrastructure (R2)
- `install_helm_release` — Installs K8s workloads (R2)
- `scale_workload` — Changes replica count (R2)
- `promote_config` — Promotes config across fleet (R2)

### Destructive Tools (require confirmation)

These tools are irreversible and MUST have human confirmation:
- `destroy_module` — Tears down infrastructure (R3)
- `delete_project` — Removes project and all modules (R3)
- `uninstall_helm_release` — Removes K8s workloads (R3)

---

## Error Handling

### Error Response Envelope

When a tool call fails, the response includes:

```json
{
  "ok": false,
  "error": {
    "status_code": 404,
    "detail": "Cluster not found",
    "error_class": "not_found",
    "retryable": false,
    "url": "/api/k8s/clusters/99",
    "next_action": "Verify target ID/name exists"
  }
}
```

### Error Classes

| Class | Meaning | Recommended Action |
|-------|---------|-------------------|
| `validation_error` | Bad input parameters | Check arguments and retry |
| `auth_error` | Authentication/permission failed | Re-authenticate |
| `not_found` | Resource doesn't exist | Verify ID/name |
| `transient_error` | Temporary failure (502/503/504) | Retry after delay |
| `server_error` | Backend error | Check backend health |
| `request_error` | Other client error | Review request |

### Retry Strategy

Only retry `transient_error` responses. Use exponential backoff:
- 1st retry: 2 seconds
- 2nd retry: 5 seconds
- 3rd retry: 15 seconds
- Max retries: 3

---

## Common Workflows

### 1. Investigate Cluster Connectivity

```
1. list_clusters → find target cluster
2. get_cluster_status → check overall health
3. get_connectivity_status → detailed probe results
4. (if degraded) get_bnk_health → component-level diagnostics
```

### 2. Deploy Infrastructure

```
1. list_projects → find target project
2. get_project_modules → check module status
3. deploy_module → deploy specific module (R2, requires confirmation context)
4. get_project_modules → verify deployment status
```

### 3. Fleet Health Check

```
1. get_fleet_health → overview of all clusters
2. (for each degraded cluster) get_cluster_status → details
3. (if BNK issues) get_bnk_topology + get_bnk_health → component diagnostics
```

### 4. Helm Release Management

```
1. list_helm_releases → current state
2. install_helm_release → install new (R2)
3. list_helm_releases → verify installed
```

---

## Troubleshooting

### MCP Server Won't Start

1. Check backend is healthy: `curl -sf http://localhost:8000/api/system/health`
2. Check MCP container logs: `docker logs bnk-forge-mcp --tail 50`
3. Verify credentials are configured in `.env`
4. Run: `make mcp-recreate` to restart with fresh credentials

### Tool Calls Failing

1. Check the `error_class` in the response
2. For `auth_error`: Token may have expired, restart MCP server
3. For `transient_error`: Backend may be overloaded, retry
4. For `server_error`: Check backend logs with the `request_id`

### Slow Responses

1. Check backend worker count: `GET /api/system/workers`
2. Check if tasks are queued: `GET /api/system/queue-metrics`
3. Infrastructure operations (deploy/destroy) can take 5-30 minutes

---

## Compatibility

### Tool Lifecycle

Tools follow the MCP-PROD-006 compatibility policy:

| Stability | Meaning |
|-----------|---------|
| `stable` | Breaking changes require deprecation period |
| `beta` | May change between minor versions |
| `experimental` | May change or be removed at any time |
| `deprecated` | Will be removed; use `replacement_tool` |

### Version Checking

The MCP server reports its version in the initialization response. Check compatibility before relying on specific tools.

---

## Observability

### Correlation IDs

Every MCP tool invocation generates:
- `invocation_id` — Unique per-tool-call ID (in MCP server logs)
- `request_id` — API correlation ID (in backend logs, OBS-001)

Use these to trace issues across MCP → backend → database.

### Telemetry Events

Each tool call produces structured log events:
```
mcp_tool_event {"event":"tool_invocation_start","invocation_id":"abc123","tool_name":"list_clusters","risk_class":"read_only"}
mcp_tool_event {"event":"tool_invocation_result","invocation_id":"abc123","success":true,"duration_ms":245}
```
