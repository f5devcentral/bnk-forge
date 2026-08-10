# Disk Management Guide

**Status**: Active  
**Last Updated**: 2026-03-15  
**Related**: OPT-001 (BuildKit cache mounts), Fresh disk issue on .91 (95% usage from 30GB BuildKit cache)

---

## Problem Statement

Docker BuildKit cache accumulates over time, especially on servers where `docker compose build --no-cache` is run frequently. On the test server (10.176.11.91), this led to:

- **95% disk usage** (only 2.8GB free on 50GB root volume)
- **30GB BuildKit cache** from repeated full rebuilds
- **Docker commands hanging** due to insufficient disk space
- **No automated cleanup** — cache grew indefinitely

This document provides a multi-layered prevention strategy.

---

## Quick Reference

```bash
# Check disk space (warns if > 70%, fails if > 85%)
make check-disk

# Clean up Docker manually (safe, interactive for volumes)
make clean-docker

# Install weekly automated cleanup (Sunday 2 AM)
make setup-cleanup-cron
```

---

## Layer 1: Developer Best Practices

### ✅ DO: Use cached builds for code changes
```bash
make build              # or: docker compose build backend frontend
make deploy             # build + restart + health check
make upgrade-safe       # server-safe path: preflight + strict verification
```
BuildKit cache mounts keep pip/npm downloads fast (~30s for code changes).

### ❌ DON'T: Use `--no-cache` for normal work
```bash
# BAD: Triggers full rebuild + downloads 500MB+ of tools/packages
docker compose build --no-cache

# WORSE: Leaves 1-2GB of cache PER BUILD
# After 15 rebuilds → 30GB cache
```

### ✅ DO: Use `build-clean` only when needed
```bash
# Only for requirements.txt / Dockerfile tool version changes
make build-clean
```

### ✅ DO: Check disk before heavy operations
```bash
make check-disk         # Exit 2 if > 85% (prevents builds from failing mid-way)
```

---

## Layer 2: Automated Cleanup (Recommended)

Install a weekly cron job that safely reclaims disk space:

```bash
make setup-cleanup-cron
```

**Schedule**: Every Sunday at 2:00 AM  
**Log**: `/var/log/docker-cleanup.log`

### What it cleans:
- ✅ Dangling images (untagged layers from old builds)
- ✅ Build cache older than 7 days (keeps recent builds fast)
- ✅ Stopped containers older than 24 hours
- ✅ Unused volumes (asks for confirmation)

### What it NEVER touches:
- ❌ Running containers
- ❌ Tagged images currently in use
- ❌ Named volumes (postgres_data, redis_data, etc.) unless you confirm
- ❌ Build cache from last 7 days

**To verify the cron is installed:**
```bash
crontab -l | grep docker-cleanup
```

**To check the log:**
```bash
tail -50 /var/log/docker-cleanup.log
```

---

## Layer 3: Manual Cleanup

When disk is getting full (> 70%), run manual cleanup:

```bash
# Interactive cleanup (asks before deleting volumes)
make clean-docker

# Or run the script directly
./scripts/docker-cleanup.sh
```

**Before/after comparison:**
```
Before:  95% used (2.8GB free, 30GB BuildKit cache)
After:   32% used (33GB free, ~5GB BuildKit cache)
Reclaimed: ~30GB
```

---

## Layer 4: Monitoring & Alerts

The `check-disk` target is integrated into deploy workflows:

```bash
make check-disk         # Exit 0 if OK, 1 if warning, 2 if critical
```

**Usage in CI/scripts:**
```bash
# Fail fast if disk is critically low
make check-disk || exit 1
make build
```

**Exit codes:**
- `0` = OK (< 70% used)
- `1` = WARNING (70-85% used) — consider cleanup soon
- `2` = CRITICAL (> 85% used) — cleanup required before builds

---

## Layer 5: Docker Daemon Config (Future)

For production deployments, configure Docker to auto-prune:

```json
// /etc/docker/daemon.json
{
  "ip-forward-no-drop": true,
  "builder": {
    "gc": {
      "enabled": true,
      "policy": [
        {"keepStorage": "10GB", "filter": ["unused-for=168h"]},
        {"keepStorage": "5GB", "all": true}
      ]
    }
  }
}
```

**Note**: Not enabled yet — requires Docker daemon restart which would affect ROI tool.

---

## Troubleshooting

### Disk still full after cleanup

Check what's using space:
```bash
# Top-level disk hogs
sudo du -xh --max-depth=1 / | sort -rh | head -15

# Docker-specific
docker system df -v
```

Common culprits:
- `/var/log/` — old logs (solution: rotate logs)
- `/home/ubuntu/Code/` — large git repos (solution: git clean -fdx)
- `/var/lib/docker/volumes/` — orphaned volumes (solution: docker volume prune)

### Cron job not running

Check cron status:
```bash
# Verify cron service is running
sudo systemctl status cron

# Check user's crontab
crontab -l

# Check syslog for cron execution
grep CRON /var/log/syslog | tail -20
```

### Docker commands still hanging

If Docker is unresponsive even after freeing disk:
```bash
# Restart Docker daemon (WARNING: stops all containers)
sudo systemctl restart docker

# Then restart containers
cd ~/Code/github/bnk-forge-v2
docker compose up -d
```

---

## Best Practices Summary

| Scenario | Command | Frequency |
|----------|---------|-----------|
| Code changes | `make deploy` | Every commit/push |
| Server upgrade | `make upgrade-safe` | Preferred on .91 after `git pull --ff-only` |
| Deps changed | `make build-clean` | When requirements.txt changes |
| Check disk | `make check-disk` | Before heavy builds |
| Manual cleanup | `make clean-docker` | When disk > 70% |
| Auto cleanup | `make setup-cleanup-cron` | Once per server (weekly job) |

---

## Related Documentation

- `Makefile` — All build and cleanup targets
- `scripts/docker-cleanup.sh` — Manual cleanup script
- `scripts/check-disk-space.sh` — Disk monitoring
- `scripts/setup-cleanup-cron.sh` — Cron job installer
- `docs/OPT-001.md` — BuildKit cache optimization (if exists)

---

## History

**2026-03-15**: Created after .91 server disk reached 95% due to 30GB BuildKit cache accumulation.
