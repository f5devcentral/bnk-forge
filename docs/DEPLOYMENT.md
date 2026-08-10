# Deployment Guide

> Practical instructions for deploying BNK Forge in production and development environments.

---

## Prerequisites

| Requirement | Minimum Version | Notes |
|------------|----------------|-------|
| **Docker** | 24.0+ | Docker Desktop or Docker Engine |
| **Docker Compose** | v2.20+ | Included with Docker Desktop |
| **Git** | 2.30+ | For cloning the repo and upgrades |
| **Disk Space** | 10 GB free | For images, volumes, and backups |
| **RAM** | 4 GB minimum | 8 GB recommended for comfortable use |

Optional:
- **GitHub CLI (`gh`)** — used by `upgrade.sh` for authenticated pulls from private repos

---

## Quick Start

Use Makefile entry points (preferred) so deployment mode is explicit:

```bash
# 1. Clone the repository
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge

# 2. Create your environment file (optional — works without it for local dev)
cp .env.example .env

# 3a. Laptop (Docker Desktop)
make local-deploy

# 3b. Linux server
make deploy

# 4. Wait for health checks to pass (~30 seconds)
make local-status   # laptop
make status         # Linux server

# 5. Open the UI
open https://localhost
```

That's it. The database migrations run automatically on startup. All 9 containers will start in the correct order with health check dependencies.

---

## First Login

| Field | Value |
|-------|-------|
| **Username** | `admin` |
| **Password** | `changeme` |

**You must change the admin password on first login.** Navigate to Settings → Change Password.

---

## Architecture Overview

BNK Forge runs as **9 Docker containers** orchestrated by Docker Compose:

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `postgres` | postgres:16-alpine | (internal) | Primary database |
| `redis` | redis:7-alpine | (internal) | Task queue broker + cache |
| `backend` | bnk-forge-backend | 8000 | FastAPI REST API |
| `celery-worker` | bnk-forge-backend | — | Background task worker (queue: default, opentofu, orchestrator) |
| `celery-worker-2` | bnk-forge-backend | — | Second task worker (queue: default, opentofu) |
| `celery-beat` | bnk-forge-backend | — | Periodic task scheduler |
| `frontend` | bnk-forge-frontend | 8080 | React SPA (Nginx) |
| `proxy` | bnk-forge-proxy | **80, 443** | Nginx reverse proxy (main entry point, TLS) |
| `postgres-backup` | postgres:16-alpine | — | Automated daily backups at 2 AM |

**Main entry point:** `https://<host>` (self-signed TLS cert, accept the browser warning)

---

## Environment Variables

All environment configuration is in the `.env` file at the project root. **No `.env` file is required for local development** — sensible defaults are built in.

### Core Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLOWED_ORIGINS` | `*` | CORS origins. Set to your domain in production (e.g. `https://bnk-forge.company.com`). Comma-separated for multiple origins. |
| `ENVIRONMENT` | `development` | Set to `production` for security warnings when keys aren't set. Options: `development`, `staging`, `production`. |

### Module Library

| Variable | Default | Description |
|----------|---------|-------------|
| `MODULE_LIBRARY_GIT_URL` | *(none)* | Git URL for your module library repository. **Required for first deployment.** |
| `MODULE_LIBRARY_GIT_REF` | `main` | Git branch/tag to use. Use `release/2.2` for F5 BNK 2.2 GA. |
| `MODULE_LIBRARY_PAT` | *(none)* | GitHub Personal Access Token for private module repos. |

### AWS Credentials (Optional)

These become defaults for new projects. You can also configure per-project in the GUI.

| Variable | Default | Description |
|----------|---------|-------------|
| `AWS_DEFAULT_REGION` | *(none)* | Default AWS region for new projects. |
| `AWS_ACCESS_KEY_ID` | *(none)* | AWS access key (Option 1: Access Keys). |
| `AWS_SECRET_ACCESS_KEY` | *(none)* | AWS secret key. |
| `AWS_REGION` | *(none)* | AWS region. |
| `AWS_SSO_START_URL` | *(none)* | AWS SSO start URL (Option 2: SSO). |
| `AWS_SSO_REGION` | *(none)* | AWS SSO region. |
| `AWS_SSO_ACCOUNT_ID` | *(none)* | AWS SSO account ID. |
| `AWS_SSO_ROLE_NAME` | *(none)* | AWS SSO role name. |

### Security Keys

| Variable | Default | Description |
|----------|---------|-------------|
| `JWT_SECRET_KEY` | *(auto-generated)* | JWT signing key. Auto-generated at startup in dev. **Set explicitly in production:** `openssl rand -hex 32` |
| `ENCRYPTION_KEY` | *(auto-generated)* | Credential encryption key. Auto-generated in dev. **Set explicitly in production:** `openssl rand -hex 16` |

### Infrastructure

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST_REPO_PATH` | *(none)* | Absolute path to the bnk-forge repo on the host. Required for GUI "Upgrade Now" button. |

### Database & Redis

Credentials are configured via `.env` and injected into `docker-compose.yml` via variable substitution. Defaults are safe for local development. **For production, set strong passwords.**

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_PASSWORD` | `bnkforge_dev_password` | PostgreSQL password. Used by postgres, backend, and backup containers. |
| `REDIS_PASSWORD` | `bnkforge_redis_dev` | Redis password. Used by redis, backend, and Celery workers. |
| `DATABASE_URL` | *(auto-composed from POSTGRES_PASSWORD)* | Full PostgreSQL connection string. Override only if using an external DB. |
| `REDIS_URL` | *(auto-composed from REDIS_PASSWORD)* | Full Redis connection string. Override only if using an external Redis. |
| `CELERY_BROKER_URL` | Same as `REDIS_URL` | Celery broker URL. |
| `CELERY_RESULT_BACKEND` | Same as `REDIS_URL` | Celery result backend URL. |

---

## Database Setup

**Migrations run automatically on startup.** The backend container runs Alembic migrations during its startup sequence — no manual steps required.

If you need to run migrations manually:

```bash
docker exec bnk-forge-backend alembic upgrade head
```

---

## F5 BNK Credentials

For F5 BIG-IP Next for Kubernetes deployments, you need FAR (F5 Artifact Registry) credentials:

1. Place your F5 service account JSON key file in the `./secrets/` directory
2. Rename it to `far-credentials.json`

```bash
cp /path/to/your/service-account-key.json ./secrets/far-credentials.json
```

The file is mounted read-only into containers at `/app/secrets/far-credentials.json`.

To obtain credentials:
1. Log in to [my.f5.com](https://my.f5.com)
2. Navigate to your F5 Distributed Cloud account
3. Download your service account JSON key file

---

## Upgrading

Non-destructive — preserves all data, volumes, and configuration:

```bash
# Preferred on Linux servers
make upgrade-safe

# Compatibility wrapper (also non-destructive)
make update

# On laptops, pull then redeploy local overlay
git pull --ff-only
make local-deploy
```

What it does:
1. Records current version
2. Pulls latest code from Git
3. Checks if already up to date (skips rebuild if so)
4. Rebuilds Docker images
5. Restarts services in correct order
6. Runs database migrations
7. Verifies health checks pass
8. Shows version diff

### MCP runtime smoke check (recommended after deploy)

After backend/proxy/MCP services are healthy, run a bounded MCP runtime smoke
validation:

```bash
# Direct MCP container endpoint
make smoke-mcp-live

# Through HTTPS proxy (self-signed cert)
MCP_SMOKE_URL=https://localhost/mcp MCP_SMOKE_INSECURE_TLS=1 make smoke-mcp-live
```

What it checks (bounded):
- MCP JSON-RPC reachability (`ping`)
- MCP tool discovery (`tools/list`)
- low-risk governed tool execution (`system_version`, `list_clusters`)
- structured MCP error envelope on a controlled failing request

This is intentionally a small smoke suite, not exhaustive MCP e2e coverage.

### MCP readiness check (recommended operational gate)

Use the layered readiness target to verify both:

1. MCP container liveness signal (protocol `ping` healthcheck)
2. MCP runtime readiness (`system_version` / `list_clusters` tool execution)

```bash
make mcp-readiness
```

This preserves safe container health semantics while making runtime readiness
explicit and operator-visible.

### MCP auth/bootstrap readiness (critical)

MCP has two distinct readiness layers:

1. **Protocol reachability** (`ping`, `tools/list`)
2. **Runtime auth/bootstrap readiness** (tool execution against backend)

A deployment can pass layer 1 and still fail layer 2 if MCP credentials are out
of sync with backend credentials.

Current compose defaults assume backend seeded admin credentials (`admin/changeme`).
If you rotate the admin password (recommended), also set MCP credentials in your
runtime environment before deploy/restart:

```bash
MCP_USERNAME=admin
MCP_PASSWORD=<current-admin-password>
```

Then recreate MCP:

```bash
# server/default compose
make mcp-recreate

# local laptop overlay
make local-mcp-recreate
```

Interpret smoke outcomes truthfully:
- `ping` succeeds but read-only tool calls fail with `auth_error` / login 401:
  MCP endpoint is reachable, but MCP runtime is **not ready**.
- Do not treat protocol-only success as release-ready MCP.

Recommended post-rotation sequence:

```bash
make mcp-recreate        # or make local-mcp-recreate
make mcp-readiness
```

**GUI Upgrade:** You can also upgrade from the UI at Settings → System → Upgrade Now when `HOST_REPO_PATH` is set and the Docker socket is mounted via `docker-compose.override.yml` / equivalent override.

> **Never use `docker compose build` directly on the server.** Use `make upgrade-safe` (preferred) or `make update`.

> **Never use `make install` on a running system** unless you want to wipe all data. It destroys all Docker volumes.

---

## Backup & Restore

### Automatic Backups

The `postgres-backup` container automatically:
- Runs a full database dump on startup
- Schedules daily backups at 2:00 AM
- Retains 7 days of backups
- Stores backups in the `postgres_backups` Docker volume

### Manual Backup

```bash
# Create a backup now
docker exec bnk-forge-postgres pg_dump -U bnkforge bnkforge | gzip > backup_$(date +%Y%m%d).sql.gz
```

### Restore from Backup

```bash
# Stop the backend first
docker-compose stop backend celery-worker celery-worker-2 celery-beat

# Restore
gunzip -c backup_20260219.sql.gz | docker exec -i bnk-forge-postgres psql -U bnkforge bnkforge

# Restart
docker-compose up -d
```

### Copy Backups to Host

Optionally mount the backup volume to the host by adding to `docker-compose.yml`:

```yaml
postgres-backup:
  volumes:
    - postgres_backups:/backups
    - ./backups:/backups  # Add this line
```

---

## Production Checklist

Before deploying to production:

- [ ] Set `ENVIRONMENT=production` in `.env`
- [ ] Set explicit `JWT_SECRET_KEY` and `ENCRYPTION_KEY` (don't rely on auto-generation)
- [ ] Set `ALLOWED_ORIGINS` to your actual domain (not `*`)
- [ ] Change default admin password on first login
- [ ] Configure `MODULE_LIBRARY_GIT_URL` and `MODULE_LIBRARY_GIT_REF`
- [ ] Set `HOST_REPO_PATH` if you want GUI upgrades
- [ ] Set strong `POSTGRES_PASSWORD` and `REDIS_PASSWORD` in `.env`
- [ ] If backend admin password changed, set matching `MCP_USERNAME` / `MCP_PASSWORD` for MCP runtime
- [ ] After MCP credential changes, recreate MCP (`make mcp-recreate` or `make local-mcp-recreate`)
- [ ] Run `make mcp-readiness` and confirm runtime tool calls pass
- [ ] Ensure firewall rules allow ports 80/443 only from trusted networks
- [ ] Set up TLS termination (reverse proxy with Let's Encrypt, or load balancer)

---

## Docker Socket Security

The Docker socket (`/var/run/docker.sock`) is mounted into the backend container to enable the **GUI "Upgrade Now"** feature (Settings → System → Upgrade Now). This grants the container full Docker daemon access, which is **root-equivalent** on the host.

**For production deployments, you have two options:**

### Option 1: Remove Docker socket (recommended for high-security environments)

Comment out the Docker socket mount in `docker-compose.yml`:

```yaml
# - /var/run/docker.sock:/var/run/docker.sock
```

The GUI upgrade button will show "not configured". Use SSH + `make upgrade-safe` (preferred) or `./upgrade.sh` for upgrades instead.

### Option 2: Keep Docker socket with group permissions

Ensure the host user running Docker Compose is in the `docker` group:

```bash
sudo usermod -aG docker $USER
newgrp docker
```

**Do NOT** use `chmod 666 /var/run/docker.sock` — this makes the socket world-writable.

---

## Network & Ports

| Port | Service | Exposed To |
|------|---------|-----------|
| **80** | Proxy (Nginx) | **Host** — HTTP redirect to HTTPS |
| **443** | Proxy (Nginx) | **Host** — main entry point (HTTPS) |
| 8000 | Backend (FastAPI) | Host — direct API access (optional, for debugging) |
| 8080 | Frontend (Nginx) | Host — direct frontend access (optional) |
| 5432 | PostgreSQL | Docker network only |
| 6379 | Redis | Docker network only |

Only ports **80** and **443** need to be accessible to users. Port 8000 can be used for direct API debugging but is not required.

---

## TLS/HTTPS Configuration

BNK Forge includes a **self-signed TLS certificate** by default (port 443). For production deployments, you should replace it with a proper certificate.

### Option 1: External TLS Terminator (Recommended)

Place a TLS-terminating reverse proxy in front of BNK Forge. The proxy already sets `X-Forwarded-Proto` headers for upstream detection.

**Using Caddy (easiest — auto-HTTPS with Let's Encrypt):**

```bash
# Caddyfile
bnk-forge.company.com {
    reverse_proxy localhost:8080
}
```

```bash
caddy run --config Caddyfile
```

**Using nginx on the host (with certbot):**

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx

# Obtain certificate
sudo certbot --nginx -d bnk-forge.company.com

# /etc/nginx/sites-available/bnk-forge
server {
    listen 443 ssl;
    server_name bnk-forge.company.com;

    ssl_certificate /etc/letsencrypt/live/bnk-forge.company.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/bnk-forge.company.com/privkey.pem;

    # Modern TLS settings
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    # HSTS — tells browsers to always use HTTPS
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 600s;
    }
}

# HTTP redirect
server {
    listen 80;
    server_name bnk-forge.company.com;
    return 301 https://$host$request_uri;
}
```

**Using a cloud load balancer (AWS ALB, GCP LB, etc.):**

Configure the load balancer to terminate TLS and forward to port 8080 (frontend) or 8000 (API) over HTTP. Ensure the load balancer sets `X-Forwarded-Proto: https`.

### Option 2: Self-Signed Certificate (Internal Networks)

For internal/lab deployments without a domain name:

```bash
# Generate self-signed certificate (valid for 1 year)
mkdir -p certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout certs/server.key -out certs/server.crt \
  -subj "/CN=bnk-forge" \
  -addext "subjectAltName=IP:192.168.1.96,DNS:localhost"
```

Then use the external TLS terminator approach above, pointing to the generated certificate files.

### After Enabling TLS

1. Update `ALLOWED_ORIGINS` in `.env` to use `https://`:
   ```
   ALLOWED_ORIGINS=https://bnk-forge.company.com
   ```
2. Restart the backend: `docker-compose restart backend celery-worker celery-worker-2`

---

## Troubleshooting

### Services won't start

```bash
# Check container status
docker-compose ps

# View logs for a specific service
docker-compose logs backend --tail 50
docker-compose logs postgres --tail 50

# Check health status
curl -k https://localhost/api/system/health
```

### Database connection errors

```bash
# Verify PostgreSQL is running and healthy
docker-compose ps postgres

# Check PostgreSQL logs
docker-compose logs postgres --tail 20

# If the database is corrupted, you can restore from backup (see Backup & Restore above)
```

### Backend fails to start

```bash
# Check backend logs for startup errors
docker-compose logs backend --tail 50

# Common issues:
# - Missing MODULE_LIBRARY_GIT_URL: Set in .env
# - Database migration failed: Check postgres logs first
# - Port 2651 already in use: Stop conflicting service
```

### Frontend shows blank page or API errors

```bash
# Rebuild frontend
docker-compose up -d --build frontend

# Restart proxy
docker-compose restart proxy

# Clear browser cache (hard refresh: Ctrl+Shift+R)
```

### Celery workers not processing tasks

```bash
# Check worker logs
docker-compose logs celery-worker --tail 50

# Restart workers
docker-compose restart celery-worker celery-worker-2

# Check Redis connectivity
docker-compose logs redis --tail 10
```

### Docker build fails on macOS with Colima

If you see credential store errors during build:

```bash
# Edit Docker config to remove credsStore
# ~/.docker/config.json — remove the "credsStore": "osxkeychain" line
# Then rebuild
docker-compose build
```

### Disk space issues

```bash
# Check Docker disk usage
docker system df

# Clean up unused images and build cache
docker system prune -f

# Clean up old backup files (if mounted to host)
find ./backups -name "*.sql.gz" -mtime +30 -delete
```

---

## Test Server (10.176.11.91)

BNK Forge is deployed on a test server for staging and demos.

| Detail | Value |
|--------|-------|
| **URL** | `https://10.176.11.91` |
| **ROI Tool** | `https://10.176.11.91/roi/` |
| **SSH** | `ubuntu@10.176.11.91` (pw: `F5@apcj`) |
| **PVE Jump** | `root@10.176.10.132` (pw: `F5@apcj`) → SSH to .91 |
| **VM** | Proxmox VM 150 (webdemo2) — 8 CPU, 16GB RAM, 52GB disk |
| **OS** | Ubuntu 22.04 LTS |
| **Docker** | 28.1.1 |

### Key Differences from Standard Deployment

1. **Host networking** — All Docker services use `network_mode: host` because Docker bridge networking breaks SSH connectivity on this VM (iptables FORWARD DROP policy interferes with traffic routing through the gateway).

2. **HTTPS on port 443** — The proxy container serves HTTPS with a self-signed cert (10 year, SAN=10.176.11.91). Corporate firewall only allows ports 22 and 443.

3. **Port layout** (all on localhost, no Docker network):

   | Port | Service |
   |------|---------|
   | 443 | Proxy (nginx SSL — main entry) |
   | 8080 | Frontend (nginx) |
   | 8000 | Backend (FastAPI) |
   | 5432 | PostgreSQL |
   | 6379 | Redis |
   | 3030 | ROI Calculator API |
   | 3031 | ROI Calculator Frontend |

4. **ROI tool** — Served behind `/roi/` path on the BNK Forge proxy. The ROI tool's own compose lives at `/home/ubuntu/bnk-roi/`.

### Upgrading the Test Server

```bash
ssh ubuntu@10.176.11.91
cd ~/bnk-forge
make upgrade-safe
# ROI tool is separate:
cd ~/bnk-roi && docker compose up -d
```

> **Note:** For first-time clean-slate setup only, use `make install` (destructive). For normal upgrades, use `make upgrade-safe`.

### Files Modified on Server (not in git)

These files differ from the repo on the test server to support host networking:

- `docker-compose.yml` — `network_mode: host` on all services, `@localhost` instead of container names, certs volume on proxy
- `proxy/nginx.conf` — SSL on 443, `localhost` upstreams instead of Docker DNS, ROI tool routes, no resolver directive
- `frontend-v2/nginx.conf` — listens on 8080 instead of 80, `localhost:8000` instead of `backend:8000`
- `certs/server.crt` + `certs/server.key` — Self-signed TLS cert (not in git)

---

## Related Documentation

- [Product Vision](PRODUCT_VISION.md) — Where BNK Forge is heading
- [Engineering Improvements](ENGINEERING_IMPROVEMENTS.md) — Technical debt and reliability plan
- [Architecture Index](architecture/README.md) — Current and archived architecture documents
