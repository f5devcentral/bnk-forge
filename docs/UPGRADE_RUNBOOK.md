# Server Upgrade Runbook

Canonical guide for upgrading BNK Forge on the test server (`.91`).

---

## Normal Upgrade (recommended)

```bash
# 1. Push code from local
git push origin staging

# 2. SSH to server
ssh ubuntu@10.176.11.91

# 3. Pull latest
cd /home/ubuntu/bnk-forge
git pull --ff-only

# 4. Run safe upgrade
make upgrade-safe
```

This runs `./upgrade.sh --local` which enforces:
- Git working tree is clean
- Branch is `staging`
- HEAD matches `origin/staging`
- Disk usage below critical threshold
- After build/restart: container health, backend log sanity, health endpoint

---

## Override Flags

Use overrides **only when you understand the risk**.

| Flag | What it skips | When to use |
|------|---------------|-------------|
| `--allow-dirty` | Clean working tree check | Agent left stale files; you verified they're safe |
| `--allow-diverged` | HEAD == origin/staging check | Intentionally deploying a local-only commit |
| `--allow-non-main` | Must be on `staging` branch | Testing a feature branch on server (rare) |
| `--allow-same-version` | VERSION bump policy (D17) | Docs-only or .agent-only changes |
| `--skip-disk-check` | Disk usage threshold | Disk script missing or false positive |
| `--no-cache` | Docker build cache | requirements.txt or Dockerfile tool versions changed |

### Example: deploy with overrides

```bash
./upgrade.sh --local --allow-dirty --allow-same-version
```

---

## Failure Recovery

### Preflight failed (exit 6)

Script stopped before any changes were made. Fix the reported issue and retry.

| Error | Fix |
|-------|-----|
| Working tree is not clean | `git stash` or `git checkout -- .` |
| Local HEAD does not match origin/staging | `git pull --ff-only` |
| Upgrade must run from staging branch | `git checkout staging` |
| Disk usage above critical threshold | `make clean-docker` or `./scripts/docker-cleanup.sh` |
| VERSION was not bumped | Bump `VERSION` file, commit, push, pull again |

### Build failed (exit 2)

No containers were restarted. Previous version is still running.

```bash
# Check build output for errors, then retry
docker compose build 2>&1 | tail -50
make upgrade-safe
```

### Restart failed (exit 3)

Some containers may be in a bad state.

```bash
docker compose ps
docker compose logs --tail 50 <failed-service>
docker compose up -d --force-recreate
```

### Migration failed (exit 4)

Services are running but database may be inconsistent.

```bash
docker exec bnk-forge-backend alembic upgrade head
# If that fails, check migration history:
docker exec bnk-forge-backend alembic history --verbose | head -20
```

### Health check failed (exit 5)

Services started but aren't healthy.

```bash
# Check which service is unhealthy
docker ps --format 'table {{.Names}}\t{{.Status}}'

# Check backend logs
docker logs bnk-forge-backend --tail 50

# Check health endpoint directly
docker exec bnk-forge-backend curl -s http://localhost:8000/api/system/health
```

---

## Post-Upgrade Verification (mandatory)

Always run these three checks after any upgrade:

```bash
# 1. All containers healthy
docker ps --format 'table {{.Names}}\t{{.Status}}' | grep bnk-forge

# 2. Backend logs clean
docker logs bnk-forge-backend --tail 20

# 3. Health endpoint responds
curl -sk https://10.176.11.91/api/system/health
```

---

## Testing the Upgrade Script

Run the automated policy guard tests locally:

```bash
./scripts/test-upgrade-policy.sh
```

This validates all preflight guard paths (dirty tree, non-staging branch, diverged HEAD) in an isolated temporary git repo.

---

## Related Files

- `upgrade.sh` — the upgrade script
- `Makefile` — `make upgrade-safe` wrapper
- `scripts/test-upgrade-policy.sh` — automated policy tests
- `scripts/check-disk-space.sh` — disk threshold checker
- `docs/DISK_MANAGEMENT.md` — disk cleanup guide
