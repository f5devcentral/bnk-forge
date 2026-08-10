# DEPLOY-004: Rebuild/Impact Matrix

**Status:** Complete
**Version:** 2.11.0

---

## Purpose

Map which code changes require which containers to be rebuilt and restarted,
so operators know the blast radius of each change.

---

## Change → Rebuild Matrix

| Changed Path | Containers to Rebuild | Restart Required | Estimated Time |
|-------------|----------------------|-----------------|---------------|
| `backend/**/*.py` | backend | backend, celery-worker, celery-worker-2, celery-beat | ~30s (cached) |
| `backend/requirements.txt` | backend (full) | All backend services | ~2-5m (deps) |
| `frontend-v2/src/**` | frontend | frontend | ~20s (cached) |
| `frontend-v2/package.json` | frontend (full) | frontend | ~1-3m (deps) |
| `proxy/nginx*.conf` | proxy | proxy | ~5s |
| `mcp-server/src/**` | mcp | mcp | ~15s |
| `mcp-server/pyproject.toml` | mcp (full) | mcp | ~1-2m (deps) |
| `docker-compose*.yml` | None | All affected services | ~10s |
| `VERSION` | frontend | frontend (bakes version) | ~20s |
| `Dockerfile` | All | All | ~5-10m |
| `.env` / `secrets/` | None | All (env reload) | ~10s |
| `backend/alembic/**` | None | backend (migration) | ~10-30s |

---

## Service Dependency Chain

When restarting services, respect the dependency order:

```
postgres, redis          (no deps — start first)
  ↓
backend                  (depends on: postgres, redis)
  ↓
celery-worker            (depends on: postgres, redis, backend)
celery-worker-2          (depends on: postgres, redis, backend)
celery-beat              (depends on: postgres, redis, backend)
frontend                 (depends on: backend)
  ↓
proxy                    (depends on: frontend, backend)
mcp                      (depends on: backend)
postgres-backup          (depends on: postgres)
```

---

## Smart Build Detection

`upgrade.sh` uses `git diff` to detect what changed and rebuilds only
affected containers. The logic:

```
if backend/** changed     → rebuild backend image → restart backend + workers
if frontend-v2/** changed → rebuild frontend image → restart frontend
if proxy/** changed       → rebuild proxy image → restart proxy
if requirements.txt changed → full backend rebuild (slow)
if docker-compose changed → recreate all services
if nothing detected       → skip build, just restart
```

---

## Risk Assessment by Change Type

| Change Type | Risk | Rollback Complexity |
|-------------|------|-------------------|
| Frontend CSS/text | Low | Redeploy previous image |
| Backend route logic | Medium | Redeploy previous image |
| Database migration | High | Requires downgrade migration |
| Docker Compose config | Medium | Revert and `docker compose up` |
| Nginx config | Low | Revert and restart proxy |
| Auth/encryption changes | Critical | May affect existing sessions/data |
| Dependency upgrades | Medium-High | Pin previous versions |

---

## Makefile Commands by Scope

| Scope | Command | What It Rebuilds |
|-------|---------|-----------------|
| Everything | `make build-all` | All images |
| Backend only | `make deploy-backend` | backend image → restart backend + workers |
| Frontend only | `make deploy-frontend` | frontend image → restart frontend |
| Full deploy | `make deploy` | backend + frontend images, then restart app containers |
| Clean rebuild | `make build-clean` | All images (no cache) |
| MCP only | `make mcp-recreate` | mcp container restart |
