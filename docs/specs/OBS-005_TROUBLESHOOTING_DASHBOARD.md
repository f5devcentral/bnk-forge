# OBS-005: Troubleshooting Dashboard/Query Requirements

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Define the minimum queries and dashboard views needed for routine support of BNK-Forge.

---

## Query Categories

### 1. Slow Endpoints

**Question:** Which API endpoints are consistently slow?

```sql
-- Top 10 slowest endpoints (last 24h)
SELECT http_path, http_method,
       COUNT(*) as call_count,
       AVG(duration_ms) as avg_ms,
       MAX(duration_ms) as max_ms
FROM audit_logs
WHERE timestamp > NOW() - INTERVAL '24 hours'
GROUP BY http_path, http_method
ORDER BY avg_ms DESC
LIMIT 10;
```

**Log query (JSON logs):**
```
level:INFO AND extra.duration_ms:>5000
```

### 2. Failing Operations

**Question:** What operations are failing and why?

```sql
-- Failed operations (last 24h)
SELECT action, resource_type, status, COUNT(*) as failures,
       http_path, http_status_code
FROM audit_logs
WHERE status IN ('failed', 'error')
  AND timestamp > NOW() - INTERVAL '24 hours'
GROUP BY action, resource_type, status, http_path, http_status_code
ORDER BY failures DESC;
```

### 3. User Activity

**Question:** What did a specific user do recently?

```sql
-- User activity trail
SELECT timestamp, action, resource_type, resource_id, resource_name,
       status, http_method, http_path, duration_ms, request_id
FROM audit_logs
WHERE "user" = 'admin'
ORDER BY timestamp DESC
LIMIT 50;
```

### 4. Request Tracing

**Question:** What happened during a specific request?

```sql
-- Full request trace (using OBS-001 correlation ID)
SELECT * FROM audit_logs WHERE request_id = 'a1b2c3d4e5f6';
```

**Log query:**
```
request_id:"a1b2c3d4e5f6"
```

### 5. Infrastructure Deployment History

**Question:** What was deployed/destroyed recently?

```sql
-- Deployment activity (last 7 days)
SELECT timestamp, "user", action, resource_type, resource_name,
       status, duration_ms
FROM audit_logs
WHERE action IN ('apply', 'destroy', 'deploy_all', 'destroy_all', 'deploy', 'install', 'upgrade', 'uninstall')
  AND timestamp > NOW() - INTERVAL '7 days'
ORDER BY timestamp DESC;
```

### 6. Error Patterns

**Question:** Are there recurring errors?

```sql
-- Error patterns (last 7 days, grouped)
SELECT DATE_TRUNC('hour', timestamp) as hour,
       http_path, http_status_code, COUNT(*) as error_count
FROM audit_logs
WHERE status = 'error'
  AND timestamp > NOW() - INTERVAL '7 days'
GROUP BY hour, http_path, http_status_code
ORDER BY hour DESC, error_count DESC;
```

### 7. Background Task Health

**Question:** Are Celery tasks completing successfully?

```sql
-- Task completion rates (from structured logs)
-- Use log aggregation to query:
service:"bnk-forge-worker" AND level:ERROR
```

**Worker status API:**
```bash
curl -sfk https://localhost/api/system/workers -H "Authorization: Bearer $TOKEN" | jq .
```

### 8. MCP Tool Failures

**Question:** Which MCP tools are failing?

**Log query:**
```
service:"bnk-forge-mcp" AND "tool_invocation_result" AND "success":false
```

---

## Dashboard Views

### View 1: Operations Overview (daily)

| Panel | Data Source | Visualization |
|-------|-----------|---------------|
| Total API calls | Audit logs | Counter |
| Error rate | Audit logs (status=error) | Percentage gauge |
| Avg response time | Audit logs (duration_ms) | Time series |
| Active workers | Worker heartbeat API | Gauge |
| Active deployments | Tasks in progress | Counter |

### View 2: Error Investigation

| Panel | Data Source | Visualization |
|-------|-----------|---------------|
| Errors by endpoint | Audit logs | Bar chart |
| Errors by user | Audit logs | Table |
| Error timeline | Audit logs | Time series |
| Recent 5xx errors | Audit logs | Table with details |

### View 3: Deployment Activity

| Panel | Data Source | Visualization |
|-------|-----------|---------------|
| Deployments today | Audit logs (action=apply/deploy) | Counter |
| Deployment success rate | Audit logs | Percentage |
| Deployment duration | Audit logs (duration_ms) | Histogram |
| Recent deployments | Audit logs | Timeline |

### View 4: System Health

| Panel | Data Source | Visualization |
|-------|-----------|---------------|
| Container status | Docker health checks | Status grid |
| Database pool | SQLAlchemy stats | Gauge |
| Redis latency | Redis info | Time series |
| Disk usage | `make check-disk` | Gauge |

---

## Implementation

### Available Today
- Audit log SQL queries (all queries above work against `audit_logs` table)
- Structured JSON log queries (via ELK/CloudWatch/Loki)
- Worker status API (`/api/system/workers`)
- Health API (`/api/system/health`)
- Docker compose status (`make status` on servers, `make local-status` on laptops)

### Future Additions
- Grafana dashboard templates (JSON export)
- Prometheus metrics endpoint (`/metrics`)
- Pre-built CloudWatch Insights queries
- Automated alerting rules
