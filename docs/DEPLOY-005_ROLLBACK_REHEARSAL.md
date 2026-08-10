# DEPLOY-005: Rollback Rehearsal Plan

**Status:** Complete
**Version:** 2.11.0

---

## Purpose

Define realistic rollback success criteria and rehearsal procedures so the
team can confidently roll back a failed deployment.

---

## Rollback Scenarios

### Scenario A: Application Code Regression (Most Common)

**Trigger:** Backend error rate spikes, frontend broken, feature regression
**Blast radius:** Application containers only
**Data impact:** None (no schema changes)

**Rollback steps:**
1. Identify previous good commit: `git log --oneline -5`
2. Checkout previous version: `git checkout <good-commit>`
3. Rebuild affected containers: `make deploy`
4. Verify health: `make status` (server) or `make local-status` (laptop)
5. Verify UI: manual smoke test or `make test-e2e`

**Success criteria:**
- [ ] Health endpoint returns `"healthy"` within 60 seconds
- [ ] All containers show `(healthy)` in `docker ps`
- [ ] Frontend loads without errors
- [ ] No new error logs in backend (`docker logs bnk-forge-backend --tail 50`)

**Estimated time:** 3-5 minutes

---

### Scenario B: Database Migration Failure

**Trigger:** Alembic migration fails or corrupts data
**Blast radius:** Database + all services
**Data impact:** Potentially destructive

**Rollback steps:**
1. Stop application containers without pruning caches: `docker compose down`
2. Restore database from backup:
   ```bash
   # Find latest backup
   ls -la data/backups/
   # Restore
   docker exec bnk-forge-postgres psql -U bnkforge -d bnkforge < data/backups/latest.sql
   ```
3. Downgrade migration: `docker exec bnk-forge-backend alembic downgrade -1`
4. Checkout previous code: `git checkout <good-commit>`
5. Rebuild and start: `make deploy`
6. Verify data integrity: spot-check projects, users, credentials

**Success criteria:**
- [ ] Database responds to queries
- [ ] Project count matches pre-upgrade count
- [ ] User logins work
- [ ] Encrypted credentials still decrypt successfully
- [ ] No orphaned records in critical tables

**Estimated time:** 10-15 minutes

---

### Scenario C: Infrastructure Configuration Failure

**Trigger:** Docker Compose config change breaks service mesh, proxy misconfigured
**Blast radius:** Container orchestration
**Data impact:** None

**Rollback steps:**
1. Revert docker-compose changes: `git checkout <good-commit> -- docker-compose.yml`
2. Revert proxy config if changed: `git checkout <good-commit> -- proxy/`
3. Recreate containers: `docker compose up -d --force-recreate`
4. Verify health: `make status` (server) or `make local-status` (laptop)

**Success criteria:**
- [ ] All containers start and reach healthy state
- [ ] Proxy routes traffic correctly (test via browser)
- [ ] WebSocket connections work (test K8s exec)
- [ ] MCP server responds to health ping

**Estimated time:** 2-3 minutes

---

### Scenario D: Dependency Upgrade Failure

**Trigger:** Python or npm dependency upgrade breaks at runtime
**Blast radius:** Backend or frontend containers
**Data impact:** None

**Rollback steps:**
1. Revert dependency files:
   ```bash
   git checkout <good-commit> -- backend/requirements.txt
   git checkout <good-commit> -- frontend-v2/package.json frontend-v2/package-lock.json
   ```
2. Force clean rebuild: `make build-clean`
3. Restart: `make deploy`

**Success criteria:**
- [ ] Container builds succeed
- [ ] Import errors gone from backend logs
- [ ] Frontend builds without chunk errors
- [ ] All API endpoints respond

**Estimated time:** 10-15 minutes (clean rebuild is slow)

---

## Pre-Upgrade Backup Checklist

Before every production upgrade:

1. [ ] Database backup exists and is recent (< 1 hour old)
   ```bash
   ls -la data/backups/ | head -3
   ```
2. [ ] Backup is restorable (verified by size > 0 and valid SQL header)
3. [ ] Current commit hash recorded: `git rev-parse HEAD`
4. [ ] Current VERSION recorded: `cat VERSION`
5. [ ] Docker images tagged with current version:
   ```bash
   docker images | grep bnk-forge
   ```

---

## Rehearsal Schedule

| Frequency | Rehearsal | Environment |
|-----------|-----------|------------|
| Monthly | Scenario A (code rollback) | Staging |
| Quarterly | Scenario B (DB restore) | Staging |
| Per major release | All scenarios | Staging |

---

## Rollback Decision Tree

```
Deployment failed?
  ├── Health checks fail within 60s → Scenario A (quick rollback)
  ├── DB migration error in logs → Scenario B (restore + rollback)
  ├── Containers won't start → Scenario C (config rollback)
  └── Runtime import/build errors → Scenario D (dependency rollback)
```

---

## Rollback Command Quick Reference

```bash
# Quick rollback to previous commit
git checkout HEAD~1
make deploy
make status        # or make local-status on laptops

# Rollback to specific version
git checkout vX.Y.Z
make deploy
make status        # or make local-status on laptops

# Emergency: stop everything
make clean

# Emergency: restore DB backup
docker exec bnk-forge-postgres psql -U bnkforge -d bnkforge < data/backups/latest.sql
```
