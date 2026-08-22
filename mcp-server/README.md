# BNK-Forge MCP Server

MCP (Model Context Protocol) server that exposes BNK-Forge's API as AI-accessible tools.

## Quick Start

### Docker (Recommended)

The MCP server runs as a container alongside BNK-Forge:

```bash
# Included in standard deployment
make deploy        # server
make local-deploy  # laptop
```

Connect your AI assistant to: `https://<your-forge-host>/mcp`

## Live MCP Smoke Validation (Slice 8)

To complement unit/contract hardening, run a bounded **live runtime smoke** check
against a deployed MCP endpoint:

```bash
# direct MCP container (common on server host)
make smoke-mcp-live

# via reverse proxy TLS endpoint (self-signed cert)
MCP_SMOKE_URL=https://localhost/mcp MCP_SMOKE_INSECURE_TLS=1 make smoke-mcp-live
```

Underlying script: `scripts/mcp_live_smoke.py`

For an operator-facing two-layer check (container liveness + runtime readiness),
use:

```bash
make mcp-readiness
```

This intentionally keeps container health semantics stable while making runtime
readiness verification explicit and repeatable.

> **Important:** MCP endpoint reachability (`ping`) is **not** enough to prove
> MCP runtime readiness. Tool calls require successful backend auth/bootstrap.

### What it verifies

1. MCP endpoint reachability + JSON-RPC protocol round-trip (`ping`)
2. Tool discovery (`tools/list`) includes required governed tools
3. Read-only governed tool execution succeeds (`system_version`, `list_clusters`)
4. Failing governed tool call returns a structured MCP error envelope
   (`error_class`, `retryable`, `next_action`, `status_code`)

### Interpreting results (readiness truthfulness)

- `ping` + `tools/list` pass, but `system_version`/`list_clusters` fail with `auth_error`:
  MCP transport is up, but runtime auth/bootstrap is not ready.
- Typical cause: the MCP service-account password drifted from the backend's seeded
  value (e.g. `MCP_SERVICE_PASSWORD` changed on one side only). MCP uses its own
  dedicated `mcp` service account, never the human admin login (#187).
- Action: set `MCP_USERNAME=mcp` / `MCP_PASSWORD` (backend `MCP_SERVICE_PASSWORD`) for
  the MCP service and recreate the `mcp` container, then rerun smoke.

### Scope boundaries (intentional)

- This is a **small smoke suite**, not a full e2e framework
- It focuses on low-risk/read-only checks plus one controlled failure-path check
- It does **not** prove every tool, every auth role permutation, or every
  environment-specific dependency path

### Local Development

```bash
cd mcp-server
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `BNK_FORGE_API_URL` | `http://bnk-forge-backend:8000` | Backend API URL |
| `BNK_FORGE_TOKEN` | (empty) | JWT token (overrides username/password) |
| `BNK_FORGE_USERNAME` | `mcp` | Auto-login username (dedicated MCP service account) |
| `BNK_FORGE_PASSWORD` | (none) | Auto-login password — must be set via `MCP_SERVICE_PASSWORD` env var; backend reconciles on every startup |
| `MCP_PORT` | `8081` | Server listen port |
| `MCP_LOG_LEVEL` | `INFO` | Logging level |

### Runtime auth/bootstrap contract

- MCP runtime is healthy only when **both** conditions are true:
  1. MCP JSON-RPC endpoint responds (`ping`)
  2. MCP can authenticate to backend and execute governed read-only tools
- The MCP container process reads **only** `BNK_FORGE_*` (see the table above):
  its login password comes from `BNK_FORGE_PASSWORD`. In the shipped compose
  files the operator sets a single `.env` value, `MCP_SERVICE_PASSWORD`, which
  compose maps to `BNK_FORGE_PASSWORD` for this container **and** to
  `MCP_SERVICE_PASSWORD` for the backend — so the two always agree. That value
  must match what the backend seeded, or supply `BNK_FORGE_TOKEN` instead. It is
  decoupled from the human admin password (#187). (bonnyr-f5 #193 M7: earlier
  text named `MCP_PASSWORD` here — nothing in this process reads that name; it is
  only a legacy compose-level alias for the password `.env` value.)
- Without this alignment, the MCP container may look healthy at protocol level
  while tool execution fails with backend login 401.

### Credential rotation runbook (bounded)

When the MCP service-account password (`MCP_SERVICE_PASSWORD`) is rotated:

1. Set the new password in the compose `.env` as `MCP_SERVICE_PASSWORD` (bonnyr-f5
   #193 M7/B1: this is the ONLY var to set — compose maps it to the container's
   `BNK_FORGE_PASSWORD` and the backend's `MCP_SERVICE_PASSWORD`. Do NOT set
   `MCP_USERNAME`: it is not read by either process, and the username is fixed to
   the dedicated `mcp` service account; the password's legacy alias `MCP_PASSWORD`
   still works but prefer the canonical name.)
2. Recreate MCP so the new env value is applied:

```bash
# server/default compose
make mcp-recreate

# laptop/local overlay
make local-mcp-recreate
```

3. Verify MCP runtime truthfully:

```bash
make mcp-readiness
```

Expected interpretation:
- MCP container liveness healthy + readiness smoke pass => MCP runtime ready
- MCP container liveness healthy + readiness smoke fail => protocol is up, runtime not ready (usually auth/bootstrap drift)

## Tools (84 total)

### System (6 tools)
`system_health`, `system_version`, `system_settings`, `list_users`, `system_queue_metrics`, `audit_log`

### Cluster Management (14 tools)
`list_clusters`, `get_cluster`, `test_cluster_connectivity`, `scan_cluster`, `list_namespaces`, `list_resources`, `get_resource`, `get_pod_logs`, `describe_resource`, `get_cluster_events`, `get_node_metrics`, `get_pod_metrics`, `restart_pod`, `scale_deployment`

### F5 BNK Operations (18 tools)
`bnk_data`, `bnk_gateway_topology`, `bnk_health`, `bnk_policy_associations`, `a2a_discover_agents`, `tmm_list_pods`, `tmm_exec_command`, `tmm_configview`, `bnk_current_version`, `bnk_available_versions`, `bnk_upgrade_plan`, `bnk_upgrade_execute`, `bnk_upgrade_history`, `bnk_license_status`, `bnk_telemetry_report`, `bnk_recovery_cert_sync`, `bnk_platform_restart`, `bnk_recovery_status`

### Diagnostics & Fleet (17 tools)
`qkview_create`, `qkview_list`, `qkview_status`, `qkview_setup_certs`, `fleet_health`, `list_operators`, `drift_check`, `drift_history`, `list_snapshots`, `create_snapshot`, `compare_snapshots`, `list_runbooks`, `execute_runbook`, `dpf_detect`, `dpf_data`, `dpf_health`, `list_alert_channels`

### Helm (11 tools)
`helm_list_repos`, `helm_add_repo`, `helm_search_charts`, `helm_list_releases`, `helm_get_release`, `helm_get_values`, `helm_release_history`, `helm_install`, `helm_upgrade`, `helm_rollback`, `helm_uninstall`

### Config Management (4 tools)
`config_export`, `config_diff`, `config_promote`, `config_import`

### IaC Operations (14 tools)
`list_projects`, `get_project`, `project_dependency_graph`, `list_project_modules`, `list_module_catalog`, `project_plan`, `project_apply`, `project_destroy`, `project_deploy_all`, `project_destroy_all`, `deployment_history`, `list_stacks`, `get_stack`, `deploy_stack`

#### Registration tool response envelopes (Issue 2 — breaking change)

`create_project` and `create_cluster` now return a consistent normalized envelope
instead of the prior inconsistent flat / bare-object shapes. This is a **breaking
change** coordinated with the awsbnkctl client.

**`create_project`** (was: flat `{success, project_id, name, message, ...}`)
```json
{
  "success": true,
  "project": {"id": 39, "name": "my-project", "environment": "dev"},
  "message": "Project created successfully"
}
```
`project_id` is mapped to `project.id`. `success` and `message` are promoted to
top level. All other fields nest under `project`.

**`create_cluster`** (was: bare object `{id, name, project_id, status, ...}` with no `success`)
```json
{
  "success": true,
  "cluster": {"id": 12, "name": "prod-eks", "project_id": 39, "status": "active"},
  "message": "Cluster registered; scan running in background."
}
```
`message` (if present in backend object) is promoted to top level and removed from
`cluster`. All other fields nest under `cluster`.

Backend REST routes (`POST /api/projects` and `POST /api/projects/{id}/k8s/clusters`)
are **unchanged** — normalization happens only at the MCP tool layer.

## Architecture

```
AI Assistant ↔ MCP Protocol (Streamable HTTP) ↔ MCP Server (:8081) ↔ HTTP ↔ FastAPI Backend (:8000)
                                                      ↑
                                               Nginx Proxy (:443)
                                               routes /mcp → :8081
```

- **Transport**: Streamable HTTP (stateless) — production-ready, supports SSE streaming
- **Auth**: Reuses BNK-Forge JWT tokens — auto-login with env credentials
- **No business logic duplication**: All tools are thin wrappers over existing REST API

## Contract Hardening Conventions

These rules are mandatory for MCP tool changes:

1. **No guessed endpoints** — always verify route decorators in `backend/routes/**`.
2. **Match parameter location** — if backend defines a value as query param, MCP must send it as `params`, not request JSON body.
3. **Align to typed Tier-1 contracts first** — prioritize tools backed by response-model routes (system, clusters, connectivity, k8s resources, helm, fleet).
4. **Use structured tool error semantics** — MCP client returns a stable envelope on backend errors:

```json
{
  "ok": false,
  "request": {"method": "GET", "path": "/api/..."},
  "error": {
    "status_code": 404,
    "detail": "Not found",
    "error_class": "not_found",
    "retryable": false,
    "next_action": "Verify the target ID/name exists and that the route/path parameters are correct.",
    "url": "/api/..."
  }
}
```

`error_class` values: `validation_error`, `auth_error`, `not_found`, `transient_error`, `server_error`, `request_error`.

### Output contract truthfulness (Slice 7, bounded)

Request-shape and route-mapping governance is now complemented by **bounded output-shape
verification** for a high-value governed subset:

- `system` (typed Tier-1 outputs)
- `cluster_management` (typed Tier-1 resource/connectivity/metrics/restart outputs)
- selected `helm` tools with typed/structured responses commonly used by operators

Current guarantee for this subset: MCP tools return a truthful JSON serialization of backend
response payloads (including structured MCP error envelopes) without silently dropping or
rewrapping critical top-level contract fields.

This is intentionally scoped and does not claim full output-contract verification for all governed
tools yet.

## MCP Governance Tool Catalog (Slice 6)

MCP hardening now includes a machine-readable governance catalog with **bounded, enforced scope**:

- **Canonical JSON artifact:** `mcp-server/tools/mcp_tool_catalog.json`
- **Python source of truth used by tests:** `mcp-server/src/bnk_forge_mcp/tool_catalog.py`

### Current scope boundary

- **Full module coverage (governed):**
  `system`, `cluster_management`, `helm`, `config_management`, `iac_operations`, `bnk_operations`

This is intentionally a truthful partial inventory, not a fake "100% all-tools" claim.
Coverage expands in slices; non-governed modules remain out of bounded full-coverage guarantees.

Each catalog entry includes:

- tool name and module/domain
- HTTP method + backend path template
- auth expectation (as inferable from backend dependencies)
- risk class
- query-vs-body usage expectations
- lifecycle metadata: `stability`, `since_version`, `deprecated`, `replacement_tool`
- tier/notes for unusual contract behavior

### Lifecycle metadata conventions (enforced)

- `stability`: `experimental` | `stable` | `internal`
- `since_version`: lightweight introduction marker for compatibility tracking
- `deprecated`: boolean lifecycle flag
- `replacement_tool`: optional, used when `deprecated=true` and direct replacement exists

If `deprecated=true`, catalog policy requires either:

1. a `replacement_tool`, or
2. explicit deprecation/sunset justification in `notes`

This keeps deprecation decisions machine-readable and human-auditable without introducing a heavyweight versioning system.

### Compatibility and deprecation policy (Slice 11)

This is intentionally lightweight and enforceable:

1. **Additive-first compatibility:**
   - Prefer additive changes to existing tool behavior and outputs.
   - Avoid silent tool removals/renames in normal hardening changes.

2. **Deprecation trigger:**
   - Mark a tool `deprecated=true` when it is superseded by a clearer tool name or semantics,
     while keeping the old invocation callable during transition.

3. **`replacement_tool` semantics:**
   - When present, `replacement_tool` must reference a real, different catalog tool.
   - Non-deprecated tools must not set `replacement_tool`.

4. **Compatibility window communication:**
   - Deprecated entries must keep explicit transition guidance in `notes`
     (replacement and/or sunset/removal language).
   - Deprecated tools remain callable until an explicit follow-up removal change.

5. **`since_version` intent:**
   - `since_version` is a lightweight introduction marker for consumers and support,
     not a full semantic-versioning framework.

Current bounded example:
- `get_resource` is a deprecated compatibility alias of `describe_resource`.
  New consumers should call `describe_resource`.

### Risk classes

- `read_only`
- `mutating`
- `destructive`

### Auth expectation conventions (enforced)

- `viewer`: backend route uses `require_viewer`
- `operator`: backend route uses `require_operator`
- `admin`: backend route uses `require_admin`
- `cluster_owner` / `module_owner` / `project_owner`: backend route ownership dependencies
- `authenticated`: route is auth-gated via `get_current_user` without fixed role dependency

Allowed values are enforced in catalog tests (`ALLOWED_AUTH_EXPECTATIONS` in
`src/bnk_forge_mcp/tool_catalog.py`).

Bounded auth trust checks now also validate governed-module auth expectations
against known backend dependency patterns in route files (representative static
verification, not full dependency introspection).

> Example correction from this verification pass: `system_health` is cataloged as
> `admin` because `/api/system/*` routes are protected by router-level
> `Depends(require_admin)`.

### MCP invocation observability contract (Slice 4)

MCP now emits structured invocation logs at two layers:

1. **Tool boundary** (`bnk_forge_mcp.observability`):
   - `event`: `tool_invocation_start` / `tool_invocation_result` /
     `tool_invocation_blocked`
   - `invocation_id`
   - `tool_name`, `module`
   - catalog context when available: `risk_class`, `auth_expectation`,
     `backend_method`, `backend_path`
   - `success`, `duration_ms`, `error_class` (on failure)
   - `reason` (on `tool_invocation_blocked`)

### Destructive-tool confirmation gate

Tools catalogued `risk_class: destructive` are enforced at runtime, not only
logged. Each one carries an extra `confirm: bool = False` argument, and calling
it without `confirm=true` returns a `CONFIRMATION_REQUIRED` refusal without
touching the backend:

```json
{
  "ok": false,
  "error": {
    "error_class": "confirmation_required",
    "code": "CONFIRMATION_REQUIRED",
    "retryable": false,
    "next_action": "Verify this is the intended target, then re-invoke with confirm=true..."
  }
}
```

This matters because the `mcp` service account is `role=admin`: without a gate,
one tool call from an autonomous agent deletes a real project or cluster with no
second factor. The gate is applied where tools are registered, so it follows the
catalog — mark a new tool `destructive` and it is gated automatically.

`confirm` is distinct from a tool's own `force` argument. `force` bypasses
*backend* safety checks (e.g. deleting an active project); `confirm` asserts
*intent* to run a destructive operation at all.

Set `BNK_FORGE_MCP_REQUIRE_CONFIRMATION=false` to disable the gate for trusted
non-interactive teardown (CI tearing down its own fixtures). It is read per
call, and defaults to enabled.

2. **HTTP client boundary** (`bnk_forge_mcp.client`):
   - `method`, `path`
   - `success`, `duration_ms`
   - `error_class`, `status_code` (on failure)

Security boundary: logs intentionally do **not** include tool args, request bodies,
tokens, passwords, or backend response payloads. This keeps logs useful for
debugging while avoiding obvious secret leakage.

### Maintenance workflow (required when changing covered tools)

1. Update MCP tool implementation under `src/bnk_forge_mcp/tools/`.
2. Verify backend route signature (method/path + query vs JSON body) in `backend/routes/**`.
3. Update `tool_catalog.py` entry and regenerate/align `tools/mcp_tool_catalog.json`.
4. If adding/changing a tool in governed modules (`system`, `cluster_management`, `helm`, `config_management`, `iac_operations`, `bnk_operations`),
   catalog coverage is mandatory (tests fail if missing).
5. Update mapping checks in `tests/test_tool_mapping_hardening.py` for mutating/request-shape-sensitive tools.
6. Run targeted checks:

```bash
PYTHONPATH=src python3 -m pytest \
  tests/test_tool_output_contracts.py \
  tests/test_tool_catalog.py \
  tests/test_observability.py \
  tests/test_tool_mapping_hardening.py \
  tests/test_client_error_semantics.py \
  tests/test_url_audit.py

# optional live runtime sanity (requires running MCP deployment)
python3 ../scripts/mcp_live_smoke.py --mcp-url http://localhost:8081/mcp
```

### Enforced policy guarantees

- JSON artifact must exactly match Python catalog source.
- Every catalog entry must use allowed risk class values.
- Every catalog entry must use allowed lifecycle stability values.
- Every catalog entry must include lifecycle metadata (`since_version`, `deprecated`, optional `replacement_tool`).
- `replacement_tool` (when set) must point to a real, different tool.
- Non-deprecated entries must not define `replacement_tool`.
- Deprecated entries must document compatibility/sunset guidance in `notes`.
- Every catalog entry must map to a known tool URL/method in URL audit ground truth.
- Every catalog entry must exist in registered MCP tools.
- Every tool in governed modules must be present in catalog.

This makes catalog drift fail loudly in CI for declared scope while allowing incremental expansion elsewhere.
