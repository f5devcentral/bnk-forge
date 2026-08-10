# Troubleshooting Guide

Common issues and solutions for BNK Forge.

**Current Version:** See the repository `VERSION` file for the current release.

## Table of Contents

- [Installation Issues](#installation-issues)
- [Startup Issues](#startup-issues)
- [Deployment Issues](#deployment-issues)
- [AWS Issues](#aws-issues)
- [Module Library Issues](#module-library-issues)
- [Kubernetes Issues](#kubernetes-issues)
- [Helm Issues](#helm-issues)
- [Database Issues](#database-issues)
- [Performance Issues](#performance-issues)
- [Getting Help](#getting-help)

---

## Installation Issues

### Docker Compose Version Error

**Symptom:**
```
ERROR: The Compose file is invalid
```

**Solution:**
Ensure you have Docker Compose v2:
```bash
docker compose version
# Should show: Docker Compose version v2.x.x

# If you have v1, upgrade Docker Desktop or install compose v2
```

### Port Already in Use

**Symptom:**
```
Error: bind: address already in use
```

**Solution:**
```bash
# Find what's using the port
lsof -i :443

# Kill the process or change the port in docker-compose.yml
# Or stop existing containers
docker compose down
```

### Permission Denied

**Symptom:**
```
permission denied while trying to connect to Docker daemon
```

**Solution:**
```bash
# Add user to docker group
sudo usermod -aG docker $USER

# Log out and back in, or run
newgrp docker
```

---

## Startup Issues

### Containers Keep Restarting

**Symptom:**
```
docker compose ps
# Shows containers in "Restarting" state
```

**Diagnosis:**
```bash
# Check logs
docker compose logs backend
docker compose logs celery-worker
```

**Common Causes:**

1. **Database not ready**
   ```bash
   # Wait for postgres to be healthy
   docker compose logs postgres
   # Look for: "database system is ready to accept connections"
   ```

2. **Missing module library config** (if using private repos)
   ```bash
   # Create .env only if you need private module library
   cp .env.example .env
   # Edit with your MODULE_LIBRARY_* settings
   ```

3. **Port conflicts**
   ```bash
   # Check for conflicts
   docker compose down
   docker compose up -d
   ```

### Backend Fails to Start

**Symptom:**
```
ModuleNotFoundError: No module named 'xxx'
```

**Solution:**
```bash
# Rebuild the backend image (cached rebuild preferred)
make build-backend
docker compose up -d backend celery-worker celery-worker-2 celery-beat
```

### Frontend Shows Blank Page

**Symptom:**
Browser shows white/blank page at https://localhost

**Diagnosis:**
```bash
# Check frontend container
docker compose logs frontend

# Check proxy
docker compose logs proxy

# Try direct backend access
curl http://localhost:8000/api/system/health
```

**Solutions:**

1. **Clear browser cache**
   - Hard refresh: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac)

2. **Rebuild frontend**
   ```bash
   make build-frontend
   docker compose up -d frontend proxy
   ```

3. **Check console errors**
   - Open browser DevTools (F12)
   - Check Console tab for errors

---

## Deployment Issues

### Module Stuck in "Deploying"

**Symptom:**
Module shows "deploying" or "initializing" indefinitely. Blueprint stays in "deploying" status. No progress after the first module.

**Diagnosis:**

```bash
# 1. Check worker logs for "unregistered task" — this is the #1 cause
docker logs worker --tail 200 2>&1 | grep -i "unregistered"

# 2. If you see "Received unregistered task of type 'tasks.xxx'":
#    The task module is missing from celery_app.py's include= list.
#    See PM-001 in docs/POST_MORTEMS.md for the full root cause analysis.

# 3. Check worker is running and healthy
docker compose ps | grep worker

# 4. Check worker logs for errors
docker logs worker --tail 100
```

**Solutions:**

1. **"Received unregistered task" in worker logs** — a task module was dropped from `celery_app.py`'s `include=` list. Add it back and redeploy:
   ```bash
   # Edit backend/celery_app.py — add the missing module to include=
   make deploy-backend   # rebuilds backend + worker
   ```

2. **Worker crashed or restarted mid-task** — restart and force-delete the stuck blueprint:
   ```bash
   docker compose restart worker
   ```
   Then in the UI: three-dot menu on the stuck blueprint > Force Delete. Redeploy fresh.

3. **No obvious error** — the stale-execution janitor (runs every 2 minutes) should mark orphaned tasks as failed. Check if the janitor caught it:
   ```bash
   docker logs backend --tail 100 2>&1 | grep -i "janitor\|stale"
   ```

### Blueprint Stuck in "Deploying" (Can't Retry)

**Symptom:**
Blueprint status shows "deploying" but no modules are actively running. The Deploy button is hidden — you can't retry.

**Root cause:** The frontend hides the Deploy button when status is `deploying`. If the worker dies or discards tasks, the stack never transitions to `failed` because no callback fires.

**Solutions:**

1. **Force delete the stuck blueprint** — three-dot menu > Delete. The delete dialog will show "Force Delete Stuck Blueprint?" with a warning. This removes the DB entry directly.

2. **Retry via API** — if the frontend retry fix is deployed (branch `fix/stuck-deploying-retry`), the "Retry Deploy" button appears in the three-dot menu for `deploying` stacks. The backend's stale-deploying recovery (`stack_service.py:862-895`) detects that no tasks are active and resets to `failed` before proceeding.

### "Dependencies not satisfied"

**Symptom:**
Can't plan/apply module, shows dependency error

**Solution:**
1. Check which dependencies are missing in the UI
2. Deploy dependencies first (they should show ✅)
3. Verify dependency outputs exist:
   ```bash
   docker exec -it bnk-forge-backend python -c "
   from database import SessionLocal
   from models import ProjectModule
   db = SessionLocal()
   module = db.query(ProjectModule).filter_by(id=<dep_id>).first()
   print(module.outputs)
   "
   ```

### OpenTofu Init Fails

**Symptom:**
```
Error: Failed to query available provider packages
```

**Solutions:**

1. **Check internet connectivity**
   ```bash
   docker exec bnk-forge-backend curl -I https://registry.terraform.io
   ```

2. **Check module source URL**
   - Verify the module Git URL is correct
   - Check GitHub PAT has access

3. **Network issues behind proxy**
   ```bash
   # Add to docker compose.yml environment:
   - HTTP_PROXY=http://proxy:port
   - HTTPS_PROXY=http://proxy:port
   ```

### Apply Fails with Provider Error

**Symptom:**
```
Error: error configuring AWS Provider
```

**Solution:**
Verify AWS credentials are configured:
```bash
# Check environment
docker exec bnk-forge-backend env | grep AWS

# Test credentials
docker exec bnk-forge-backend aws sts get-caller-identity
```

---

## Variable Issues

### "Variable not set" or Empty Values

**Symptom:**
Apply fails with "variable X is required but not set" or variables appear empty

**Solutions:**

1. **Check variable source type**
   - Variables from `module` source should auto-wire from dependencies
   - Variables from `user` source require manual input
   - Go to module > Variables tab and verify values

2. **Dependency outputs missing**
   - Ensure dependency module was applied successfully
   - Check dependency has outputs defined
   ```bash
   # Check outputs in database
   docker exec -it bnk-forge-backend python -c "
   from database import SessionLocal
   from models import ProjectModule
   db = SessionLocal()
   module = db.query(ProjectModule).filter_by(name='<dependency_name>').first()
   print(module.outputs)
   "
   ```

3. **Re-sync module metadata**
   - Go to Settings > Environment Config > Sync Modules
   - This refreshes variable definitions from module.json

### Variables Not Wiring from Dependencies

**Symptom:**
Variable shows as "user" source but should auto-wire from another module

**Diagnosis:**
1. Check module.json has correct `from_module` and `from_output` fields
2. Verify dependency module path matches exactly

**Solutions:**

1. **Check module.json configuration**
   ```json
   {
     "inputs": {
       "required": [
         {
           "name": "vpc_id",
           "source": "module",
           "from_module": "infra/aws/vpc",
           "from_output": "vpc_id"
         }
       ]
     }
   }
   ```

2. **Verify dependency is added to project**
   - The source module must be in the same project
   - Check Build > Projects > [Your Project] for both modules

3. **Re-add the module**
   - Delete and re-add the module to trigger fresh variable wiring

### Variable Type Mismatch

**Symptom:**
```
Error: Invalid value for input variable
```

**Solutions:**

1. **Check expected type**
   - String: `"value"`
   - Number: `123` (no quotes)
   - Boolean: `true` or `false`
   - List: `["a", "b", "c"]`
   - Map: `{"key": "value"}`

2. **JSON format for complex types**
   - Lists and maps must be valid JSON
   - Use the UI's JSON editor for complex values

### Sensitive Variables Not Showing

**Symptom:**
Sensitive variables (passwords, keys) show as `***` or empty

**This is expected behavior.** Sensitive variables are masked in the UI for security.

**To verify they're set:**
```bash
# Check in database (values are encrypted)
docker exec -it bnk-forge-backend python -c "
from database import SessionLocal
from models import ProjectModuleVariable
db = SessionLocal()
var = db.query(ProjectModuleVariable).filter_by(name='<var_name>').first()
print(f'Has value: {bool(var.value)}')
"
```

### System Defaults Not Applied

**Symptom:**
New modules don't get default values from System Defaults

**Solutions:**

1. **Check defaults are configured**
   - Go to Settings > Defaults
   - Verify the default key matches the variable name exactly

2. **Re-sync modules**
   - After updating defaults, re-sync modules
   - Delete and re-add modules to apply new defaults

3. **Check default scope**
   - System defaults only apply when adding new modules
   - Existing modules keep their values

---

## AWS Issues

### "No valid credential sources found"

**Symptom:**
```
NoCredentialProviders: no valid providers in chain
```

**Solutions:**

1. **Environment variables**
   ```bash
   # Add to .env
   AWS_ACCESS_KEY_ID=xxx
   AWS_SECRET_ACCESS_KEY=xxx
   AWS_REGION=us-west-2
   ```

2. **AWS SSO (in UI)**
   - Project > Cloud tab
   - Click "Configure AWS SSO"
   - Complete browser authentication

3. **Mount credentials volume**
   ```yaml
   # In docker compose.yml under backend:
   volumes:
     - ~/.aws:/root/.aws:ro
   ```

### "ExpiredToken"

**Symptom:**
```
ExpiredToken: The security token included in the request is expired
```

**Solution:**
- For SSO: Re-authenticate in UI (Project > Cloud tab)
- For static credentials: Generate new access keys

### "AccessDenied"

**Symptom:**
```
AccessDenied: User is not authorized to perform: xxx
```

**Solution:**
Verify IAM permissions. BNK Forge needs permissions for resources it creates:
- For VPC: `ec2:*`
- For EKS: `eks:*`, `iam:*`
- Check specific error for required permission

---

## Module Library Issues

### Sync Fails

**Symptom:**
"Failed to sync module library" error

**Diagnosis:**
```bash
# Check git access
docker exec bnk-forge-backend git ls-remote $MODULE_LIBRARY_GIT_URL

# Check PAT is set (for private repos)
docker exec bnk-forge-backend env | grep MODULE_LIBRARY
```

**Solutions:**

1. **Verify URL format**
   ```bash
   # Correct format
   MODULE_LIBRARY_GIT_URL=https://github.com/org/repo.git
   ```

2. **Check PAT permissions**
   - PAT needs `repo` scope for private repos
   - Generate new PAT if expired

3. **Try manual clone**
   ```bash
   docker exec bnk-forge-backend git clone $MODULE_LIBRARY_GIT_URL /tmp/test-clone
   ```

### Modules Not Appearing

**Symptom:**
Sync succeeds but modules don't appear in catalog

**Diagnosis:**
```bash
# Check sync results
docker exec -it bnk-forge-backend python -c "
from database import SessionLocal
from models import ModuleLibrary
db = SessionLocal()
print(f'Modules in DB: {db.query(ModuleLibrary).count()}')
"
```

**Solutions:**

1. **Check module.json format**
   - Each module needs valid `module.json`
   - Check syntax with `jq`:
   ```bash
   cat module.json | jq .
   ```

2. **Check module paths**
   - Modules should be in subdirectories
   - Each subdirectory needs `module.json`

3. **Check logs for parse errors**
   ```bash
   docker compose logs backend | grep -i "module"
   ```

---

## Database Issues

### Connection Refused

**Symptom:**
```
ConnectionRefusedError: [Errno 111] Connection refused
```

**Solution:**
```bash
# Check postgres is running
docker compose ps postgres

# Check postgres logs
docker compose logs postgres

# Restart postgres
docker compose restart postgres
```

### Migration Errors

**Symptom:**
```
alembic.util.exc.CommandError: Can't locate revision
```

**Solution:**
```bash
# Reset migrations (CAUTION: loses data)
docker exec bnk-forge-backend alembic downgrade base
docker exec bnk-forge-backend alembic upgrade head
```

### "Database is locked" (SQLite only)

**Note:** SQLite is not recommended. Use PostgreSQL.

**Solution:**
```bash
# Migrate to PostgreSQL
DATABASE_URL=postgresql://bnkforge:password@postgres:5432/bnkforge
```

### Reset Database

**CAUTION:** This deletes all data.

```bash
docker compose down -v
docker compose up -d
```

### Complete Fresh Start

If you need to completely reset everything (containers, images, volumes, and rebuild):

```bash
make install
```

> **Warning:** This destroys all data (projects, clusters, credentials). Only use for a full reset or first-time install.

This:
- Removes all BNK Forge containers, volumes, and images
- Rebuilds and starts all services from scratch

> Do **not** use this as a normal upgrade path. For non-destructive upgrades, use `make upgrade-safe` on Linux servers or `git pull --ff-only && make local-deploy` on laptops.

---

## Kubernetes Issues

### "Cluster not reachable"

**Symptom:**
Kubernetes page shows "Cannot connect to cluster"

**Diagnosis:**
```bash
# Check kubeconfig is valid
docker exec bnk-forge-backend python -c "
from services.kubernetes_service import KubernetesService
k8s = KubernetesService()
print(k8s.list_clusters())
"
```

**Solutions:**

1. **Kubeconfig expired** (EKS)
   - Re-register the cluster with fresh kubeconfig
   - For auto-registered EKS clusters, click "Refresh Kubeconfig"

2. **Network connectivity**
   - Ensure the backend container can reach the cluster API
   - Check VPN/firewall rules

3. **Invalid kubeconfig**
   - Delete and re-add the cluster
   - Verify kubeconfig works locally first

### WebSocket Exec/Logs Not Working

**Symptom:**
Terminal or log streaming shows blank or disconnects immediately

**Diagnosis:**
```bash
# Check WebSocket connections
docker compose logs backend | grep -i websocket
```

**Solutions:**

1. **Proxy configuration**
   - Ensure nginx proxy is configured for WebSocket upgrade
   - Check `proxy/nginx.conf` has WebSocket headers

2. **Browser issues**
   - Try a different browser
   - Disable browser extensions that might block WebSockets

---

## Helm Issues

### "Repository not found"

**Symptom:**
Cannot find charts from a repository

**Solution:**
```bash
# Update all repositories
# In UI: Kubernetes page > Helm section > Update All

# Or manually:
docker exec bnk-forge-backend helm repo update
```

### "Release not found"

**Symptom:**
Release exists in cluster but not shown in UI

**Solution:**
- Select the correct namespace
- Refresh the page
- Check if the release was created outside BNK Forge

### Helm Version Mismatch

**Symptom:**
"helm version detection failed" error

**Diagnosis:**
```bash
docker exec bnk-forge-backend helm version
# Should show: v3.17.0
```

**Solution:**
- Rebuild the backend container: `docker compose build backend`

---

## Performance Issues

### System Page Slow (API Latency)

**Symptom:**
System page takes long to load or shows high API latency

**Background:**
Performance regressions should be investigated against current baseline behavior rather than an old release-specific claim. If you're seeing slow performance:

**Diagnosis:**
```bash
# Check Celery worker status
docker exec bnk-forge-celery-worker celery -A celery_app inspect active

# Check for stuck tasks
docker exec bnk-forge-celery-worker celery -A celery_app inspect active --timeout=5
```

**Solutions:**

1. **Restart Celery worker**
   ```bash
   docker compose restart celery-worker
   ```

2. **Clear stuck tasks**
   ```bash
   docker exec bnk-forge-redis redis-cli FLUSHDB
   ```

3. **Run database migrations** (for index improvements)
   ```bash
   docker exec bnk-forge-backend alembic upgrade head
   ```

### Slow UI

**Possible Causes:**

1. **Large project count**
   - Archive old projects
   - Use filters to reduce displayed items

2. **Browser caching**
   - Clear browser cache
   - Disable browser extensions

3. **Network latency (remote server)**
   - Consider running locally
   - Check server resources

### High Memory Usage

**Diagnosis:**
```bash
docker stats
```

**Solutions:**

1. **Limit container memory**
   ```yaml
   # In docker compose.yml
   services:
     backend:
       deploy:
         resources:
           limits:
             memory: 2G
   ```

2. **Reduce worker concurrency**
   ```yaml
   celery-worker:
     command: celery -A celery_app worker --loglevel=info --concurrency=2
   ```

### Tasks Queue Building Up

**Diagnosis:**
```bash
# Check queue depth
docker exec bnk-forge-redis redis-cli LLEN celery
```

**Solutions:**

1. **Increase workers**
   ```yaml
   celery-worker:
     command: celery -A celery_app worker --loglevel=info --concurrency=8
   ```

2. **Add more worker containers**
   ```bash
   docker compose up -d --scale celery-worker=3
   ```

### Deployment Stacks Taking Long

**Symptom:**
Stack deployment is slower than expected

**Solutions:**

1. **Use parallel execution** (enabled by default for stacks)
2. **Check network connectivity** to AWS
3. **Review module timeouts** in Settings > System > Defaults

---

## Getting Help

### Collect Diagnostics

Before reporting issues, collect:

```bash
# System info
docker --version
docker compose version

# Container status
docker compose ps

# Logs (last 100 lines)
docker compose logs --tail=100 > logs.txt

# Environment (sanitized - remove secrets!)
grep -v "SECRET\|KEY\|PASSWORD" .env > env-sanitized.txt
```

### Log Locations

| Service | Log Command |
|---------|-------------|
| Backend | `docker compose logs backend` |
| Celery Worker | `docker compose logs celery-worker` |
| Frontend | `docker compose logs frontend` |
| Proxy | `docker compose logs proxy` |
| Database | `docker compose logs postgres` |
| Redis | `docker compose logs redis` |

### Debug Mode

Enable debug logging:

```bash
# In .env
ENVIRONMENT=development

# Restart
docker compose restart backend celery-worker
```

### Report Issues

GitHub Issues: https://github.com/f5devcentral/bnk-forge/issues

Include:
1. What you were trying to do
2. What happened (error message)
3. Steps to reproduce
4. Diagnostic information above
