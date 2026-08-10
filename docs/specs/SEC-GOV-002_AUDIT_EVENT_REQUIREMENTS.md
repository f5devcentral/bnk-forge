# SEC-GOV-002: Audit Event Requirements

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Define what every mutating action must record for accountability. The audit trail enables compliance, incident investigation, and change tracking.

---

## Audit Event Schema

Every mutating API call (POST, PUT, PATCH, DELETE) to `/api/*` is recorded with:

| Field | Type | Required | Source | Description |
|-------|------|----------|--------|-------------|
| `timestamp` | datetime (UTC) | Yes | Auto | When the action occurred |
| `user` | string | Yes | JWT `sub` claim | Who performed the action |
| `user_id` | integer | No | JWT payload | FK to users table |
| `action` | string | Yes | HTTP method + path | What was done (create, update, delete, deploy, destroy, etc.) |
| `resource_type` | string | Yes | URL pattern match | What type of resource (project, module, cluster, helm_release, etc.) |
| `resource_id` | string | No | URL parameter | Which specific resource |
| `resource_name` | string | No | Service layer | Human-readable resource name |
| `status` | string | Yes | HTTP status code | Result: `success` (<400), `failed` (4xx), `error` (5xx) |
| `details` | JSON | No | Service layer | Additional context (parameters, before/after values) |
| `ip_address` | string | No | X-Forwarded-For / client | Client IP address |
| `user_agent` | string | No | User-Agent header | Client identifier (max 500 chars) |
| `http_method` | string | Yes | Request | POST, PUT, PATCH, DELETE |
| `http_path` | string | Yes | Request URL | Full API path |
| `http_status_code` | integer | Yes | Response | HTTP response code |
| `duration_ms` | integer | Yes | Timer | Request processing time |
| `request_id` | string | No | OBS-001 middleware | Correlation ID for cross-service tracing |

---

## What Gets Audited

### Always Audited (automatic via AuditMiddleware)

All `POST`, `PUT`, `PATCH`, `DELETE` requests to `/api/*` paths except:

**Excluded paths (high-frequency, low-value):**
- `/api/system/health` — health checks
- `/api/system/performance` — metrics polling
- `/api/system/queue-metrics` — worker metrics
- `/ws/*` — WebSocket connections
- `/api/auth/me` — session polling

### Audit Event Categories

| Category | Actions | Risk Level |
|----------|---------|------------|
| **Authentication** | login, change_password, create_user, delete_user | R1–R3 |
| **Project lifecycle** | create, update, delete project | R1–R3 |
| **Infrastructure** | init, plan, apply, destroy module | R2–R3 |
| **Orchestration** | deploy_all, destroy_all | R2–R3 |
| **Kubernetes** | create/delete cluster, scale workload, pause/resume rollout | R1–R3 |
| **Helm** | install, upgrade, rollback, uninstall release | R2–R3 |
| **Stacks** | create, deploy, destroy stack | R1–R3 |
| **Configuration** | update settings, update defaults, manage secrets | R1 |
| **Fleet** | promote config, export/import config | R2 |
| **Module sources** | create, update, delete, sync module source | R1–R3 |

---

## Query Patterns

### By User
```sql
SELECT * FROM audit_logs WHERE user = 'admin' ORDER BY timestamp DESC LIMIT 50;
```

### By Resource
```sql
SELECT * FROM audit_logs WHERE resource_type = 'module' AND resource_id = '15' ORDER BY timestamp DESC;
```

### By Correlation ID
```sql
SELECT * FROM audit_logs WHERE request_id = 'a1b2c3d4e5f6';
```

### Failed Operations
```sql
SELECT * FROM audit_logs WHERE status IN ('failed', 'error') ORDER BY timestamp DESC LIMIT 100;
```

### Destructive Actions (last 24h)
```sql
SELECT * FROM audit_logs
WHERE action IN ('delete', 'destroy', 'destroy_all', 'uninstall')
  AND timestamp > NOW() - INTERVAL '24 hours'
ORDER BY timestamp DESC;
```

---

## Database Indexes

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_audit_timestamp_action` | (timestamp, action) | Time-range + action queries |
| `idx_audit_user_action` | (user, action) | User activity queries |
| `idx_audit_resource` | (resource_type, resource_id) | Resource history queries |
| `idx_audit_request_id` | (request_id) | Correlation ID lookups (OBS-001) |

---

## Frontend Access

- **Audit Log UI:** `pages/System.tsx` → Audit tab → `components/settings/AuditLog.tsx`
- **API endpoints:** `GET /api/audit`, `GET /api/audit/filters`, `GET /api/audit/stats?days=7`
- **Filtering:** By user, action, resource_type, status, date range

---

## Implementation

| Component | File | Role |
|-----------|------|------|
| Middleware | `backend/core/audit_middleware.py` | Auto-captures all mutating requests |
| Service | `backend/services/audit_service.py` | `create_audit_log()` for programmatic use |
| Model | `backend/models/system.py:AuditLog` | Database schema |
| Migration | `backend/alembic/versions/v2_023_audit_log_enhancements.py` | Schema + indexes |
| Correlation | `backend/alembic/versions/v2_048_add_audit_request_id.py` | OBS-001 request_id column |
| Route patterns | `backend/core/audit_middleware.py:ROUTE_PATTERNS` | URL → resource_type mapping |
