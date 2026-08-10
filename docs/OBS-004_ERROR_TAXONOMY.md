# OBS-004: Error Taxonomy

**Status:** Implemented
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Define a shared taxonomy for operator-facing and support-facing failures. Every error in BNK-Forge maps to one of these categories, enabling consistent error handling, alerting, and troubleshooting.

---

## Error Response Shape

All API errors return this JSON structure:

```json
{
  "error": {
    "code": "PROJECT_NOT_FOUND",
    "message": "Project not found",
    "details": { "project_id": "42" },
    "path": "/api/projects/42",
    "request_id": "a1b2c3d4e5f6"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Machine-readable error code (UPPER_SNAKE_CASE) |
| `message` | string | Human-readable description |
| `details` | object | Additional context (field errors, resource IDs, etc.) |
| `path` | string | API path that produced the error |
| `request_id` | string | Correlation ID for support tracing (OBS-001) |

---

## Error Classes

### Class Hierarchy

```
Exception
  └── AppError (base, 500)
        ├── BadRequestError (400) — validation, business logic
        ├── UnauthorizedError (401) — missing/invalid auth
        ├── ForbiddenError (403) — insufficient permissions
        ├── NotFoundError (404) — resource doesn't exist
        ├── ValidationError (422) — field-level validation
        ├── ConflictError (409) — duplicate, FK violation
        ├── TimeoutError (500) — operation timed out
        ├── ServiceError (500) — external service failure
        ├── DecryptionError (500) — encryption key mismatch
        └── InternalError (500) — unexpected/unknown failure
```

---

## Error Categories

### 1. Validation Errors (400, 422)

**When:** Client sent invalid input.
**Action:** Fix the request and retry.

| Code | HTTP | Trigger | Example |
|------|------|---------|---------|
| `BAD_REQUEST` | 400 | General bad input | Missing required field |
| `VALIDATION_ERROR` | 422 | Field-level validation | Invalid IP format |

**Frontend handling:** Show inline field errors or toast with message.

### 2. Authentication Errors (401)

**When:** No valid credentials provided.
**Action:** Re-authenticate (login again).

| Code | HTTP | Trigger | Example |
|------|------|---------|---------|
| `UNAUTHORIZED` | 401 | Missing/expired JWT | Token expired |

**Frontend handling:** Redirect to login page. `apiClient` interceptor handles this automatically.

### 3. Permission Errors (403)

**When:** Authenticated but insufficient role.
**Action:** Contact admin for access.

| Code | HTTP | Trigger | Example |
|------|------|---------|---------|
| `FORBIDDEN` | 403 | Role check failed | Viewer trying to deploy |

**Frontend handling:** Show "Insufficient permissions" toast.

### 4. Not Found Errors (404)

**When:** Requested resource doesn't exist.
**Action:** Check the ID or navigate elsewhere.

| Code | HTTP | Trigger | Example |
|------|------|---------|---------|
| `{RESOURCE}_NOT_FOUND` | 404 | DB lookup returned null | `PROJECT_NOT_FOUND` |

**Frontend handling:** Show "not found" message, optionally redirect to list view.

### 5. Conflict Errors (409)

**When:** Operation conflicts with existing state.
**Action:** Resolve the conflict (rename, delete duplicate, etc.).

| Code | HTTP | Trigger | Example |
|------|------|---------|---------|
| `{RESOURCE}_CONFLICT` | 409 | Unique constraint, FK | Duplicate project name |

**Frontend handling:** Show specific conflict message from `error.message`.

### 6. Timeout Errors (500)

**When:** Operation exceeded time limit.
**Action:** Retry or check system health.

| Code | HTTP | Trigger | Example |
|------|------|---------|---------|
| `TIMEOUT` | 500 | Operation timeout | Terraform apply > 2 hours |

**Frontend handling:** Show timeout toast with retry option.

### 7. Service Errors (500)

**When:** External dependency failed.
**Action:** Check the dependent service health.

| Code | HTTP | Trigger | Example |
|------|------|---------|---------|
| `{SERVICE}_ERROR` | 500 | External call failed | `KUBERNETES_ERROR`, `DOCKER_ERROR`, `HTTP_ERROR` |

**Frontend handling:** Show service-specific error with details.

### 8. Internal Errors (500)

**When:** Unexpected/unhandled failure.
**Action:** Check logs using the `request_id`.

| Code | HTTP | Trigger | Example |
|------|------|---------|---------|
| `INTERNAL_ERROR` | 500 | Unhandled exception | Bug in code |
| `INTERNAL_SERVER_ERROR` | 500 | Completely unexpected | Null pointer, import error |
| `DECRYPTION_ERROR` | 500 | Key mismatch | Wrong encryption key |

**Frontend handling:** Show generic error with `request_id` for support.

---

## Exception Mapping

The `handle_exception()` function in `core/errors.py` maps Python exceptions to AppError subclasses:

| Python Exception | Maps To | HTTP |
|-----------------|---------|------|
| `ValueError` | `BadRequestError` | 400 |
| `json.JSONDecodeError` | `BadRequestError` | 400 |
| `FileNotFoundError` | `NotFoundError` | 404 |
| `sqlalchemy.IntegrityError` | `ConflictError` | 409 |
| `sqlalchemy.OperationalError` | `InternalError` | 500 |
| `RuntimeError` | `ServiceError` | 500 |
| `subprocess.CalledProcessError` | `InternalError` | 500 |
| `kubernetes.ApiException(404)` | `NotFoundError` | 404 |
| `kubernetes.ApiException(409)` | `ConflictError` | 409 |
| `kubernetes.ApiException(422)` | `BadRequestError` | 400 |
| `kubernetes.ApiException(*)` | `ServiceError` | 500 |
| `requests.ConnectionError` | `ServiceError` | 500 |
| Any other `Exception` | `InternalError` | 500 |

---

## Error Handling Decorators

### Route-level: `@handle_route_errors("operation")`

```python
from core.errors import handle_route_errors

@router.post("/api/projects")
@handle_route_errors("create project")
async def create_project(data: CreateProjectRequest, db: Session = Depends(get_db)):
    # No try/except needed — decorator handles it
    project = project_service.create(db, data)
    return project
```

### Service-level: `@handle_service_errors("operation")`

```python
from core.errors import handle_service_errors

class HelmService:
    @handle_service_errors("install Helm chart")
    def install(self, cluster_id: int, chart: str, values: dict):
        # AppError subclasses propagate unchanged
        # Other exceptions are mapped automatically
        ...
```

---

## Frontend Error Handling

### API Client Interceptor (`lib/api/client.ts`)

- **401:** Redirects to `/login`
- **502/503/504:** Retries up to 3 times with backoff
- **All others:** Propagates to caller via `Promise.reject`

### React Query `onError` Pattern

```typescript
const mutation = useMutation({
  mutationFn: (data) => api.projects.create(data),
  onError: (error) => {
    // error.response.data.error contains the structured error
    const { code, message, request_id } = error.response?.data?.error || {};
    notify.error('Failed to create project', message || 'Unknown error');
  },
});
```

---

## Implementation Files

| File | Purpose |
|------|---------|
| `backend/core/errors.py` | Error class hierarchy, handlers, decorators |
| `backend/main.py:257-259` | Global exception handler registration |
| `frontend-v2/src/lib/api/client.ts` | API client error interceptors |
| `frontend-v2/src/lib/notify.ts` | Toast notification helper |
