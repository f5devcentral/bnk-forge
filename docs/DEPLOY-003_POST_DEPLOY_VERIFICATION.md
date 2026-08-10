# DEPLOY-003: Post-Deploy Verification Workflow

**Status:** Active
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

One repeatable workflow to verify BNK-Forge is healthy after any deployment. Server upgrades run the core gates automatically in `upgrade.sh`; the same checks can be run manually with environment-appropriate status targets.

---

## Verification Gates

### Gate 1: Container Health (automated)

**Check:** All critical containers are running and healthy.

```bash
# Automated in upgrade.sh for server upgrades — also available via:
make status        # Linux server / default compose
make local-status  # laptop / Docker Desktop
```

**Expected output:** All services show `Up` + `(healthy)`:

| Container | Health Check | Timeout |
|-----------|-------------|---------|
| `bnk-forge-postgres` | `pg_isready -U bnkforge` | 5s interval, 5 retries |
| `bnk-forge-redis` | `redis-cli ping` | 5s interval, 5 retries |
| `bnk-forge-backend` | `curl -f http://localhost:8000/api/system/health` | 30s interval, 3 retries |
| `bnk-forge-frontend` | `curl -f http://localhost:8080/` | 30s interval, 3 retries |
| `bnk-forge-proxy` | `curl -fk https://localhost:443/api/system/health` | 30s interval, 3 retries |
| `bnk-forge-celery-worker` | `celery inspect ping` | 60s interval, 3 retries |
| `bnk-forge-celery-worker-2` | `celery inspect ping` | 60s interval, 3 retries |
| `bnk-forge-celery-beat` | `test -f /tmp/celerybeat-schedule` | 60s interval, 3 retries |
| `bnk-forge-mcp` | JSON-RPC ping | 30s interval, 3 retries |

**Pass criteria:** All containers are `healthy`. Any `unhealthy` or `restarting` container is a gate failure.

**Failure action:** Check container logs: `docker logs <container> --tail 50`

---

### Gate 2: Backend Log Sanity (automated)

**Check:** No fatal startup errors in backend logs.

```bash
docker logs bnk-forge-backend --tail 50 2>&1 | grep -iE "traceback|modulenotfounderror|importerror|syntaxerror|ERROR: Exception in ASGI"
```

**Pass criteria:** Zero matches.

**Failure action:** The backend has a startup error. Check full logs and fix before proceeding.

---

### Gate 3: Health Endpoint (automated)

**Check:** Backend API responds with healthy status.

```bash
curl -sf http://localhost:8000/api/system/health | jq .
```

**Expected response:**
```json
{
  "status": "healthy",
  "timestamp": "2026-03-28T14:32:01.456789+00:00"
}
```

**Pass criteria:** HTTP 200 and `status` is `"healthy"`.

**Failure action:** Backend is running but unhealthy. Check database connectivity and Redis.

---

### Gate 4: Auth Sanity (manual or E2E)

**Check:** Authentication flow works end-to-end.

```bash
# Login and get JWT token
curl -sfk -X POST https://localhost/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YOUR_PASSWORD"}' | jq .access_token
```

**Pass criteria:** Returns a valid JWT token (non-empty string).

**Failure action:** Check auth middleware, JWT secret configuration, user database.

---

### Gate 5: Frontend Reachability (manual or E2E)

**Check:** SPA loads and renders.

```bash
curl -sfk https://localhost/ -o /dev/null -w "%{http_code}"
# Expected: 200
```

**Pass criteria:** HTTP 200, page loads in browser without JavaScript console errors.

**Failure action:** Check nginx proxy config, frontend build output, static asset serving.

---

### Gate 6: API Smoke (manual or E2E)

**Check:** Core API endpoints return valid data.

```bash
# With a valid JWT token:
TOKEN=$(curl -sfk -X POST https://localhost/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"PASSWORD"}' | jq -r .access_token)

# Projects list
curl -sfk https://localhost/api/projects -H "Authorization: Bearer $TOKEN" | jq length

# System info
curl -sfk https://localhost/api/system/info -H "Authorization: Bearer $TOKEN" | jq .version
```

**Pass criteria:** All return valid JSON with HTTP 200.

---

### Gate 7: Worker Health (manual)

**Check:** Celery workers are processing tasks.

```bash
# Via API (requires auth):
curl -sfk https://localhost/api/system/workers -H "Authorization: Bearer $TOKEN" | jq .

# Or directly:
docker exec bnk-forge-celery-worker celery -A celery_app inspect active
```

**Pass criteria:** At least one worker is active and responsive.

---

### Gate 8: MCP Readiness (if deployed)

**Check:** MCP server responds to protocol health check.

```bash
make mcp-readiness
# Or manually:
make smoke-mcp-live
```

**Pass criteria:** MCP health ping succeeds, at least one tool callable.

---

## Automated vs Manual Gates

| Gate | Automated | Manual | E2E Test |
|------|-----------|--------|----------|
| 1. Container Health | `upgrade.sh` (server) | `make status` / `make local-status` | — |
| 2. Backend Log Sanity | `upgrade.sh` | `docker logs` | — |
| 3. Health Endpoint | `upgrade.sh` | `curl` | `health.spec.ts` |
| 4. Auth Sanity | — | `curl` | `auth.spec.ts` |
| 5. Frontend Reachability | — | Browser | `smoke.spec.ts` |
| 6. API Smoke | — | `curl` | `api.spec.ts` |
| 7. Worker Health | — | `curl`/`celery` | — |
| 8. MCP Readiness | `make mcp-readiness` | — | `mcp.spec.ts` |

---

## Quick Verification Script

Run all automated gates:

```bash
#!/bin/bash
# post-deploy-verify.sh

echo "=== Gate 1: Container Health ==="
docker compose ps --format "table {{.Name}}\t{{.Status}}" | grep -v "healthy" && echo "FAIL: Unhealthy containers" && exit 1
echo "PASS"

echo "=== Gate 2: Backend Log Sanity ==="
ERRORS=$(docker logs bnk-forge-backend --tail 50 2>&1 | grep -ciE "traceback|modulenotfounderror|importerror|syntaxerror")
[ "$ERRORS" -gt 0 ] && echo "FAIL: $ERRORS fatal errors in backend logs" && exit 1
echo "PASS"

echo "=== Gate 3: Health Endpoint ==="
STATUS=$(curl -sf http://localhost:8000/api/system/health | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" != "healthy" ] && echo "FAIL: Health status is $STATUS" && exit 1
echo "PASS"

echo "=== All automated gates passed ==="
```

---

## Timing

| After... | Run gates... |
|----------|-------------|
| `make deploy` | 1, 2, 3 (auto) |
| `make upgrade-safe` | 1, 2, 3 (auto) |
| Manual deploy | 1-8 (full checklist) |
| Hotfix | 1, 2, 3, 6 (quick verify) |
| Major release | 1-8 + full E2E suite |
