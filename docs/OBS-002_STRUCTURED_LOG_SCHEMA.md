# OBS-002: Structured Log Schema

**Status:** Implemented
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Standardize structured logging fields across all BNK-Forge services to enable consistent querying, alerting, and correlation in log aggregation tools (ELK, Datadog, CloudWatch, Loki).

---

## Log Line Format

All services emit **JSON-formatted log lines** in production/staging. Development mode uses human-readable text.

### Standard Fields (every log line)

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `timestamp` | string (ISO 8601) | Yes | UTC timestamp with millisecond precision | `"2026-03-28T14:32:01.456Z"` |
| `level` | string | Yes | Log severity level | `"INFO"`, `"WARNING"`, `"ERROR"` |
| `logger` | string | Yes | Python logger name (module path) | `"routes.projects"`, `"services.helm_service"` |
| `message` | string | Yes | Human-readable log message | `"Project created successfully"` |
| `service` | string | Yes | Service identifier | `"bnk-forge"`, `"bnk-forge-worker"`, `"bnk-forge-mcp"` |

### Correlation Fields (when available)

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `request_id` | string | Auto | Correlation ID from OBS-001 middleware | `"a1b2c3d4e5f6"` |

### Context Fields (WARNING+ severity)

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `source.file` | string | Auto (WARNING+) | Source file path | `"/app/routes/projects.py"` |
| `source.line` | int | Auto (WARNING+) | Line number | `142` |
| `source.function` | string | Auto (WARNING+) | Function name | `"create_project"` |

### Error Fields (when exception occurs)

| Field | Type | Required | Description | Example |
|-------|------|----------|-------------|---------|
| `exception` | string | Auto | Full formatted traceback | `"Traceback (most recent call last):\n..."` |

### Extra Fields (caller-provided)

Any additional context passed via `logger.info("msg", extra={...})` appears under the `extra` key:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `extra.user` | string | Authenticated username | `"admin"` |
| `extra.project_id` | int | Project being operated on | `42` |
| `extra.cluster_id` | int | K8s cluster context | `7` |
| `extra.module_id` | int | Module being deployed | `15` |
| `extra.task_id` | string | Celery task ID | `"abc-123-def"` |
| `extra.duration_ms` | int | Operation duration | `1250` |
| `extra.error_code` | string | Structured error code | `"PROJECT_NOT_FOUND"` |

---

## Service-Specific Fields

### API Server (`bnk-forge`)

Standard fields plus `request_id` on every request-scoped log line.

### Celery Worker (`bnk-forge-worker`)

| Field | Source | Description |
|-------|--------|-------------|
| `service` | Config | Always `"bnk-forge-worker"` |
| `extra.celery_task_id` | Task context | Celery task UUID |
| `extra.celery_task_name` | Task context | Task function name |
| `extra.worker_hostname` | Worker | Worker process hostname |
| `extra.request_id` | Task headers | Inherited correlation ID from API request |

### MCP Server (`bnk-forge-mcp`)

| Field | Source | Description |
|-------|--------|-------------|
| `service` | Config | Always `"bnk-forge-mcp"` |
| `extra.invocation_id` | Generated | Per-tool-call UUID (12 hex chars) |
| `extra.tool_name` | MCP protocol | Tool being invoked |
| `extra.risk_class` | Tool catalog | Safety classification |
| `extra.duration_ms` | Timer | Tool execution time |

---

## Logging Best Practices

### DO

```python
# Use structured extra fields for queryable context
logger.info("Module deployed", extra={"project_id": 42, "module_id": 15, "duration_ms": 3200})

# Use get_logger for consistent naming
from core.logging_config import get_logger
logger = get_logger(__name__)

# Log at appropriate levels
logger.debug("Cache hit for key: %s", key)           # Verbose debugging
logger.info("Project created: %s", project.name)      # Normal operations
logger.warning("Retry attempt %d for cluster %d", n, cid)  # Recoverable issues
logger.error("Failed to connect to cluster %d", cid)  # Operation failures
logger.critical("Database connection pool exhausted")  # System-level failures
```

### DON'T

```python
# Don't use print() — it bypasses structured logging
print(f"Project created: {project.name}")  # BAD

# Don't log secrets or credentials
logger.info(f"Using token: {token}")  # BAD — leaks secrets

# Don't use bare string formatting for log messages (prevents aggregation)
logger.info(f"Failed for project {project_id}")  # OK but loses parameterization
logger.info("Failed for project %s", project_id)  # BETTER — aggregation-friendly
```

---

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Minimum log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |
| `LOG_FORMAT` | Auto | Force `json` or `text` format (auto-detected from `ENVIRONMENT`) |
| `ENVIRONMENT` | `development` | `development` = text, `staging`/`production` = JSON |

---

## Implementation

- **Config:** `backend/core/logging_config.py` — `JSONFormatter`, `HumanReadableFormatter`, `configure_logging()`
- **Filter:** `backend/core/correlation.py` — `CorrelationLogFilter` (injects `request_id`)
- **Worker:** `backend/celery_app.py` — `configure_logging(service_name="bnk-forge-worker")`
- **MCP:** `mcp-server/src/bnk_forge_mcp/observability.py` — `ObservabilityMCPProxy`

---

## Example Log Lines

### JSON (production)
```json
{"timestamp":"2026-03-28T14:32:01.456Z","level":"INFO","logger":"routes.projects","message":"Project created: my-vpc","service":"bnk-forge","request_id":"a1b2c3d4e5f6","extra":{"project_id":42,"user":"admin"}}
```

### JSON (error with traceback)
```json
{"timestamp":"2026-03-28T14:32:05.789Z","level":"ERROR","logger":"services.helm_service","message":"Helm install failed","service":"bnk-forge","request_id":"a1b2c3d4e5f6","source":{"file":"/app/services/helm_service.py","line":142,"function":"install_release"},"exception":"Traceback...","extra":{"cluster_id":7,"chart":"nginx-ingress"}}
```

### Text (development)
```
2026-03-28 14:32:01 | INFO     | routes.projects | Project created: my-vpc
```
