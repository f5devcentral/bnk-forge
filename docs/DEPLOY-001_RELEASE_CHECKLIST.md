# DEPLOY-001: Release Checklist Template

**Status:** Active
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Repeatable checklist for every BNK-Forge release. Copy this template into the release PR or issue.

---

## Pre-Release

### Code Quality

- [ ] All CI checks pass on target branch (lint, unit, component, integration, security)
- [ ] No `FIXME` or `TODO` markers in changed files (unless tracked in backlog)
- [ ] New endpoints have response models and error handling decorators
- [ ] New frontend pages have loading, error, and empty states
- [ ] Sensitive data (tokens, passwords, kubeconfigs) not logged or exposed in API responses

### Database

- [ ] Alembic migration file exists for any model changes
- [ ] Migration is backward-compatible (nullable columns, no renames without alias)
- [ ] Migration tested locally: `alembic upgrade head` + `alembic downgrade -1`
- [ ] No orphaned data from schema changes (cleanup migration if needed)

### API Contracts

- [ ] No breaking changes to Tier 1 endpoints (auth, clusters, fleet, BNK health)
- [ ] New endpoints documented in OpenAPI (auto-generated from FastAPI models)
- [ ] Response shape changes are additive (new fields OK, removals need deprecation)

### Frontend

- [ ] Build succeeds: `cd frontend-v2 && npm run build`
- [ ] No TypeScript errors: `cd frontend-v2 && npx tsc --noEmit`
- [ ] New UI elements tested in both light and dark mode
- [ ] No broken navigation (SPA routes work, no `window.location.href`)

### Dependencies

- [ ] Python: `requirements.txt` changes reviewed (no known CVEs)
- [ ] Node: `package.json` changes reviewed (no known CVEs)
- [ ] Docker base images are pinned versions (not `latest`)

### Documentation

- [ ] CHANGELOG.md updated with version entry
- [ ] VERSION file bumped (semver: patch/minor/major)
- [ ] Breaking changes documented in release notes
- [ ] New features documented (README or feature docs)

---

## Release

### Version Bump

- [ ] VERSION file contains new version number
- [ ] CHANGELOG.md has entry for new version with date
- [ ] Git tag created: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`

### Build & Deploy

- [ ] Build/deploy path succeeds for the target environment (`make local-deploy`, `make deploy`, or `make upgrade-safe`)
- [ ] All containers start: `docker compose ps` shows all healthy
- [ ] No startup errors in backend logs: `docker logs bnk-forge-backend --tail 50`

### Database Migration

- [ ] Migration runs: `alembic upgrade head` (automatic in `upgrade.sh`)
- [ ] No migration errors in logs
- [ ] Database schema matches expected state

---

## Post-Release (DEPLOY-003)

### Smoke Tests (must all pass)

- [ ] **Health:** `curl -sf http://localhost:8000/api/system/health | jq .status` returns `"healthy"`
- [ ] **Auth:** Login succeeds with valid credentials
- [ ] **Frontend:** UI loads at `https://localhost/` without console errors
- [ ] **API:** `GET /api/projects` returns valid JSON
- [ ] **MCP:** `make mcp-readiness` passes (if MCP deployed)

### Verification

- [ ] All containers healthy: `docker compose ps` or environment-specific status target (`make local-status` / `make status`)
- [ ] Backend logs clean: no `Traceback`, `ImportError`, `SyntaxError` in last 50 lines
- [ ] WebSocket connectivity: browser console shows no WS connection errors
- [ ] Workers active: `GET /api/system/workers` returns active workers

### Monitoring

- [ ] Check error rate in logs (compare to pre-release baseline)
- [ ] Verify scheduled tasks running: drift checks, health monitor, heartbeat
- [ ] Database backup completed successfully after migration

---

## Rollback Plan

If post-release verification fails:

1. **Stop new containers:** `docker compose down`
2. **Restore previous code and rebuild for the target environment:** `git checkout v{PREVIOUS}` then `make deploy` (server) or `make local-deploy` (laptop)
3. **Downgrade database:** `docker exec bnk-forge-backend alembic downgrade -{N}` (N = number of new migrations)
4. **Restart:** use the same target environment deploy command if needed
5. **Verify:** Run post-release smoke tests again
6. **Notify:** Document rollback reason in incident channel

### Rollback Decision Criteria

| Condition | Action |
|-----------|--------|
| Health endpoint returns unhealthy | Rollback immediately |
| Backend won't start (import/syntax error) | Rollback immediately |
| Database migration failed | Rollback immediately |
| Single feature broken, core works | Hotfix forward (patch release) |
| Performance degraded >50% | Rollback after 15 min monitoring |

---

## Quick Reference Commands

```bash
# Full upgrade (pull + build + restart + migrate + verify)
make upgrade-safe

# Check status
make local-status   # laptop / Docker Desktop
make status         # Linux server / default compose

# View logs
docker logs bnk-forge-backend --tail 100
docker logs bnk-forge-celery-worker --tail 100

# Run smoke test
curl -sf http://localhost:8000/api/system/health | jq .

# MCP readiness
make mcp-readiness

# Rollback
git checkout v{PREVIOUS_VERSION}
make deploy         # server
# or: make local-deploy
docker exec bnk-forge-backend alembic downgrade -{N}
```

---

## Release Cadence

| Type | Frequency | Scope |
|------|-----------|-------|
| **Patch** (x.y.Z) | As needed | Bug fixes, security patches |
| **Minor** (x.Y.0) | Weekly/biweekly | New features, non-breaking changes |
| **Major** (X.0.0) | Quarterly | Breaking changes, architecture shifts |
