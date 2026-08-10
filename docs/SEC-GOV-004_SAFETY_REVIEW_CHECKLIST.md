# SEC-GOV-004: New Tool/Endpoint Safety Review Checklist

**Status:** Complete
**Version:** 2.11.0

---

## Purpose

Lightweight safety review to complete before adding any new high-risk
automation endpoint (API route, MCP tool, Celery task, or WebSocket handler).

---

## Checklist

### 1. Authentication & Authorization

- [ ] Endpoint requires authentication (JWT token via AuthMiddleware)
- [ ] RBAC role check applied (admin/operator/viewer)
- [ ] Public paths explicitly listed in `auth_middleware.py` skip list (if unauthenticated)
- [ ] Username extracted from JWT for audit trail (not hardcoded)

### 2. Input Validation

- [ ] Request body validated via Pydantic model (not raw dict)
- [ ] Path parameters validated (type, range, format)
- [ ] Query parameters validated with defaults
- [ ] File uploads: size limit, type whitelist, content validation
- [ ] No SQL injection vectors (all queries via SQLAlchemy ORM)
- [ ] No command injection vectors (no `subprocess` with user input, or properly escaped)

### 3. Secret Handling

- [ ] No secrets in URL parameters or query strings
- [ ] No secrets logged (check `logger.info/debug/error` calls)
- [ ] Secrets encrypted before database storage (`encrypt_value()`)
- [ ] API response uses boolean flags, not raw secrets (`has_password: true`)
- [ ] Error responses don't leak secret values (`scrub_secrets()` applied)

### 4. Error Handling

- [ ] Route handler wrapped with `@handle_route_errors("operation")`
- [ ] Service methods wrapped with `@handle_service_errors("operation")`
- [ ] Custom exceptions mapped in `handle_exception()` if needed
- [ ] Error response follows standard format (`format_error_response()`)
- [ ] No bare `except: pass` blocks (use specific exception types)
- [ ] Correlation ID propagated via `get_request_id()`

### 5. Audit Trail

- [ ] Mutating operations (POST/PUT/DELETE/PATCH) logged by AuditMiddleware
- [ ] Username captured in audit record
- [ ] Resource type and ID recorded
- [ ] If bypassing AuditMiddleware: manual audit log entry created

### 6. Rate Limiting & Abuse Prevention

- [ ] Sensitive endpoints rate-limited (login, token generation)
- [ ] Bulk operations have reasonable limits (pagination, max items)
- [ ] Long-running operations dispatched to Celery (not blocking API)
- [ ] WebSocket connections have timeout and max message size

### 7. Data Integrity

- [ ] Database operations use transactions (commit/rollback)
- [ ] Concurrent access handled (optimistic locking or select-for-update)
- [ ] Cascade deletes reviewed (no orphaned records)
- [ ] Foreign key constraints in place

### 8. MCP-Specific (if adding MCP tool)

- [ ] Tool registered in `tool_catalog.json` with risk class
- [ ] Mutating tools marked `mutating: true` in catalog
- [ ] Tool has contract test in `tests/test_tool_catalog.py`
- [ ] Error responses use MCP envelope format (`ok: false, error: {...}`)
- [ ] Invocation logged via `ObservabilityMCPProxy`
- [ ] No credential logging in tool arguments

### 9. Testing

- [ ] Unit test covers happy path
- [ ] Unit test covers error/edge cases
- [ ] Integration test if endpoint interacts with external service
- [ ] Contract test if API shape matters to consumers
- [ ] E2E test updated if workflow affected

### 10. Documentation

- [ ] OpenAPI spec updated (auto-generated from Pydantic models)
- [ ] CHANGELOG entry if user-facing
- [ ] AUDIT.md updated if fixing a tracked issue

---

## Risk Classification

| Risk Level | Examples | Required Reviews |
|------------|---------|-----------------|
| **Low** | Read-only list endpoints, health checks | Self-review |
| **Medium** | CRUD operations, config changes | Peer review |
| **High** | Credential handling, K8s mutations, Terraform apply | Peer review + security check |
| **Critical** | Auth system changes, encryption changes, MCP mutating tools | Full checklist + manual test |

---

## When to Use

- Adding a new API route in `backend/routes/`
- Adding a new MCP tool in `mcp-server/src/bnk_forge_mcp/tools/`
- Adding a new Celery task in `backend/tasks/`
- Adding a new WebSocket handler in `backend/routes/k8s_websocket.py`
- Modifying authentication or authorization logic
- Adding endpoints that handle secrets or credentials
