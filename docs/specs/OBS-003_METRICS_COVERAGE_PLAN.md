# OBS-003: Metrics Coverage Plan

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Identify the most valuable latency/error metrics to instrument first, enabling performance monitoring and alerting.

---

## Priority 1 — Critical Operations

These metrics have the highest impact on operator experience and should be instrumented first.

| Metric | Type | Source | Labels | Alert Threshold |
|--------|------|--------|--------|-----------------|
| `api_request_duration_ms` | Histogram | Backend middleware | method, path, status | p99 > 5000ms |
| `api_request_total` | Counter | Backend middleware | method, path, status | Error rate > 5% |
| `task_duration_ms` | Histogram | Celery signals | task_name, status | p99 > 300000ms (5min) |
| `task_total` | Counter | Celery signals | task_name, status | Failure rate > 10% |
| `health_check_status` | Gauge | Health endpoint | component | Any unhealthy |
| `active_workers` | Gauge | Worker heartbeat | hostname | < 1 |

## Priority 2 — Infrastructure Operations

| Metric | Type | Source | Labels | Alert Threshold |
|--------|------|--------|--------|-----------------|
| `tofu_apply_duration_ms` | Histogram | Task completion | project, module, action | p99 > 600000ms |
| `tofu_apply_total` | Counter | Task completion | project, action, status | Failure rate > 20% |
| `helm_operation_duration_ms` | Histogram | Helm service | cluster, action | p99 > 60000ms |
| `k8s_api_duration_ms` | Histogram | K8s client | cluster, operation | p99 > 10000ms |
| `connectivity_probe_duration_ms` | Histogram | Probe service | cluster | p99 > 30000ms |
| `drift_check_duration_ms` | Histogram | Drift task | project | p99 > 120000ms |

## Priority 3 — Platform Health

| Metric | Type | Source | Labels | Alert Threshold |
|--------|------|--------|--------|-----------------|
| `db_query_duration_ms` | Histogram | SQLAlchemy events | operation | p99 > 1000ms |
| `db_pool_active` | Gauge | SQLAlchemy pool | — | > 80% capacity |
| `redis_operation_duration_ms` | Histogram | Redis client | command | p99 > 100ms |
| `websocket_connections_active` | Gauge | WS service | — | Sudden drop > 50% |
| `mcp_tool_duration_ms` | Histogram | MCP proxy | tool_name, status | p99 > 10000ms |
| `mcp_tool_total` | Counter | MCP proxy | tool_name, error_class | Error rate > 10% |

---

## Implementation Approach

### Phase 1: Structured Logs (Current)

Extract metrics from structured JSON logs using log aggregation tools:
- `duration_ms` field in audit logs → API latency
- Task completion logs → task duration
- Error logs → error rate

This is available today with no code changes.

### Phase 2: Prometheus Metrics (Future)

Add `prometheus_client` to backend:
```python
from prometheus_client import Histogram, Counter, Gauge

api_duration = Histogram('api_request_duration_seconds', 'API latency', ['method', 'path', 'status'])
api_errors = Counter('api_errors_total', 'API errors', ['method', 'path', 'error_code'])
```

Expose via `/metrics` endpoint for Prometheus scraping.

### Phase 3: Dashboard

Build Grafana/CloudWatch dashboards from collected metrics:
- API latency p50/p95/p99 over time
- Error rate by endpoint
- Task queue depth and processing time
- Worker utilization
- Database connection pool usage

---

## Key SLOs (Service Level Objectives)

| Service | Metric | Target |
|---------|--------|--------|
| API | p99 latency | < 5 seconds |
| API | Error rate (5xx) | < 1% |
| Task queue | Processing time (p95) | < 5 minutes |
| Task queue | Failure rate | < 5% |
| Health endpoint | Availability | > 99.9% |
| Frontend | Page load time | < 3 seconds |
| MCP | Tool response time (p95) | < 10 seconds |

---

## Data Sources Available Today

| Source | Fields | Queryable Via |
|--------|--------|---------------|
| Audit logs (DB) | duration_ms, status, http_method, path | SQL queries |
| JSON logs (stdout) | timestamp, level, request_id, service, extra.* | Log aggregation |
| Worker heartbeat (Redis) | worker status, active tasks | API endpoint |
| MCP observability logs | invocation_id, duration_ms, tool_name | Log aggregation |
| Docker health checks | container status | `docker compose ps` |
