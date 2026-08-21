# Installation Guide

This guide covers installing BNK Forge on various environments.

**Versioning note:** Refer to the repo `VERSION` file and release tags for the current shipped version.

## Table of Contents

- [Quick Start](#quick-start)
- [System Requirements](#system-requirements)
- [Local Development](#local-development)
- [Production Deployment](#production-deployment)
- [Provisioned VM (KVM or cloud-init)](#provisioned-vm-kvm-or-cloud-init)
- [Configuration Options](#configuration-options)
- [Verify Installation](#verify-installation)
- [First Steps After Installation](#first-steps-after-installation)

---

## Quick Start

### Laptop (macOS / Windows — Docker Desktop)

```bash
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge
make local-deploy
```

Open **https://localhost** and accept the self-signed certificate warning. Log in as **admin** — no default password ships; retrieve the generated one from `/app/keys/initial_admin_password` (the boot log points at this file; the plaintext is never logged), or set `DEFAULT_ADMIN_PASSWORD`. You'll change it on first login.

### Linux Server

```bash
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge
make deploy
```

For first-time clean-slate bootstrap only (destructive), run `make install`.

Log in as **admin** using the generated password from `/app/keys/initial_admin_password` (or set `DEFAULT_ADMIN_PASSWORD`); you'll be prompted to change it on first login.

---

## System Requirements

### Minimum Requirements

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8+ GB |
| Disk | 10 GB | 20+ GB |
| Docker | 20.10+ | Latest |
| Docker Compose | v2.0+ | Latest |

### Software Requirements

- **Docker** with Docker Compose v2
- **Git** for cloning the repository

**Note:** All other tools (OpenTofu, Helm, AWS CLI) are pre-installed in the container.

### Network Requirements

| Port | Service | Description |
|------|---------|-------------|
| 443 | Nginx Proxy | Main application entry (HTTPS) |
| 80 | Nginx Proxy | HTTP redirect to HTTPS |
| 8080 | Frontend | Direct frontend access (no HTTPS) |
| 8000 | Backend API | Direct API access (optional) |
| 5432 | PostgreSQL | Database (internal on server, exposed on laptop) |
| 6379 | Redis | Queue (internal on server, exposed on laptop) |

---

## Local Development (Laptop)

### Step 1: Clone Repository

```bash
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge
```

### Step 2: Deploy Locally

```bash
make local-deploy
```

This builds and starts all services using **bridge networking** (compatible with Docker Desktop on macOS and Windows):
- PostgreSQL database
- Redis queue
- FastAPI backend (port 8000)
- Celery workers (task execution)
- Celery beat (scheduled tasks)
- React frontend (port 8080)
- Nginx proxy (ports 80/443)

### Step 3: Verify Services

```bash
make local-status

# Or check containers directly:
docker compose -f docker-compose.yml -f docker-compose.local.yml ps
```

### Step 4: Access Application

Open **https://localhost** in your browser. Accept the self-signed certificate warning.

You can also access services directly:
- **https://localhost** — main UI via proxy (HTTPS)
- **http://localhost:8080** — frontend direct (no HTTPS)
- **http://localhost:8000/api/system/health** — backend API

### Step 5: Log In

A default admin account is created automatically on first startup:

| Field | Value |
|-------|-------|
| **Username** | `admin` |
| **Password** | _generated on first startup_ |

No default password ships. Where the password comes from — and where to retrieve
it — depends on whether `DEFAULT_ADMIN_PASSWORD` is set:

- **`DEFAULT_ADMIN_PASSWORD` set** (Helm always sets it, wiring it from the
  chart's per-install `admin-password` Secret): the backend seeds `admin` from
  that value — and, on upgrade from a build that shipped a default password,
  rotates `admin` to it. No keys-file is written. Retrieve it from that source:

  ```bash
  # Helm (the admin-password Secret is the source of truth)
  kubectl get secret <release>-bnk-forge-secrets -o jsonpath='{.data.admin-password}' | base64 -d
  ```

- **`DEFAULT_ADMIN_PASSWORD` unset** (Docker Compose default): the backend
  generates a random password and writes it to `/app/keys/initial_admin_password`
  (mode 600); the boot log points at that file (the plaintext itself is never
  logged). Retrieve it from the file:

  ```bash
  # Docker Compose
  docker exec bnk-forge-backend cat /app/keys/initial_admin_password
  ```

You will be **required** to change it on first login (the API refuses other calls
until you do).

### Managing Your Local Deployment

```bash
make local-up         # Start (already built)
make local-down       # Stop and remove
make local-restart    # Restart all
make local-status     # Health check
make local-logs       # Tail logs
```

### Networking: Laptop vs Server

| | Laptop (`local-*`) | Server (`server-*`) |
|---|---|---|
| **Docker networking** | Bridge (port mappings) | Host (bind directly) |
| **Compose files** | `docker-compose.yml` + `docker-compose.local.yml` | `docker-compose.yml` |
| **Inter-container DNS** | Service names (`backend`, `redis`) | `localhost` |
| **OS support** | macOS, Windows, Linux | Linux only |
| **GUI Upgrade button** | Not available | Available (with override) |

> **Why two modes?** Docker Desktop (macOS/Windows) runs containers in a Linux VM. `network_mode: host` binds to the VM's network, not your laptop's — so ports aren't accessible from your browser. The local overlay switches to bridge networking with explicit port mappings.

### Optional: Configure Module Library

If you have a private module library, create a `.env` file:

```bash
cp .env.example .env
```

Edit `.env`:
```bash
MODULE_LIBRARY_GIT_URL=https://github.com/your-org/your-modules.git
MODULE_LIBRARY_PAT=ghp_xxxxxxxxxxxx
```

Then restart: `make local-restart`

---

## Production Deployment

### Step 1: Prepare Server

Ensure Docker and Docker Compose are installed on your server.

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Verify installation
docker --version
docker compose version
```

### Step 2: Clone Repository

```bash
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge
```

### Step 3: Deploy on Server

```bash
make deploy
```

This is the standard non-destructive server deploy entry point.

> **Clean-slate bootstrap only:** Use `make install` when you intentionally want to wipe existing BNK Forge data/volumes and rebuild from scratch.

> **Optional:** If you need custom CORS origins or a private module library, create a `.env` file before running the script:
> ```bash
> cp .env.example .env
> # Edit ALLOWED_ORIGINS, MODULE_LIBRARY_GIT_URL, MODULE_LIBRARY_PAT as needed
> ```

### Step 4: Configure Firewall

Allow access to ports 80 and 443:

```bash
# UFW (Ubuntu)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# firewalld (CentOS/RHEL)
sudo firewall-cmd --permanent --add-port=80/tcp --add-port=443/tcp
sudo firewall-cmd --reload
```

### Step 5: Access Application

Access from any browser: `https://your-server-ip`

Log in as **admin** using the generated password from `/app/keys/initial_admin_password` (or set `DEFAULT_ADMIN_PASSWORD`); you'll be prompted to change it on first login.

Accept the self-signed certificate warning, or replace the certs with your own (see proxy/Dockerfile).

### Step 6: Managing Your Server Deployment

```bash
make deploy           # Build + restart all
make up               # Start (already built)
make down             # Stop and remove
make status           # Health check
make server-logs      # Tail logs
```

---

## Provisioned VM (KVM or cloud-init)

The steps above assume a host you already have. To get a **fresh** VM that
installs BNK Forge unattended on first boot, use the harness in
[`vm-bnk-forge/`](../vm-bnk-forge/README.md) — it renders a cloud-init that
installs Docker, clones the repo, and runs `make install`.

```bash
cd vm-bnk-forge
cp config.env.example config.env
$EDITOR config.env       # VM_NAME, sizing, BRANCH (pin a release tag for demos)

./make-vm.sh             # Linux + KVM host: builds a seed disk and virt-installs
./render-cloud-init.sh   # any host: emits user-data to paste into a cloud provider
```

Roughly six minutes later the VM serves `https://<vm-ip>:8443` with every
container healthy (8443/8082, since `make install` runs the host-networked
server topology). The VM path applies the same hardening this guide describes:
`ufw` is enabled during cloud-init for 22 plus those proxy ports, sshd is
key-only with root login disabled, and the GitHub deploy key is shredded once
the clone completes.

The generated admin credentials (see the setup notes above) and the Docker-socket mount
still apply — read the README's security notes before giving such a VM a
public address.

---

## Configuration Options

Most configuration is done in the GUI after startup at **Settings → Environment Config**.

### Environment Variables (`.env` file)

Only needed for specific use cases:

| Variable | When Needed | Example |
|----------|-------------|---------|
| `ALLOWED_ORIGINS` | Restrict CORS access | `https://192.168.1.100` |
| `MODULE_LIBRARY_GIT_URL` | Private module repo | `https://github.com/org/modules.git` |
| `MODULE_LIBRARY_PAT` | Private module repo | `ghp_xxxxxxxxxxxx` |
| `MODULE_LIBRARY_GIT_REF` | Specific branch/tag | `main` |

### AWS Configuration

AWS credentials can be configured:
1. **In the GUI** - Per-project at Project Settings → Cloud Authentication
2. **In `.env`** - As defaults for all projects
3. **Via volume mount** - Mount `~/.aws:/root/.aws:ro` in docker-compose.yml

### What's Pre-configured

These are already set in `docker-compose.yml` and don't need configuration:
- `DATABASE_URL` - PostgreSQL connection
- `REDIS_URL` - Redis connection
- `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` - Task queue
- `ALLOWED_ORIGINS=*` - CORS (permissive default)

---

## Verify Installation

### Check Services

```bash
# Laptop
make local-status

# Server
make status

# Or check directly:
curl http://localhost:8000/api/system/health
curl -Ik https://localhost
```

### Check Logs

```bash
# All logs
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f celery-worker
```

### Test CLI Tools

```bash
# Verify Helm and kubectl (available in API container)
docker exec bnk-forge-backend helm version --short
docker exec bnk-forge-backend kubectl version --client --short

# Verify OpenTofu (available in worker container only)
docker exec bnk-forge-celery-worker tofu version
```

---

## First Steps After Installation

After starting BNK Forge for the first time:

### 1. Log In

Open the application URL and log in as **`admin`**. There is no default
password: retrieve the one generated on first startup —
`docker exec bnk-forge-backend cat /app/keys/initial_admin_password` (compose),
or `kubectl get secret <release>-bnk-forge-secrets -o jsonpath='{.data.admin-password}' | base64 -d` (Helm).
You will be **required** to set a new password before the API accepts other calls.

### 2. Connect a Kubernetes Cluster

If you have an existing K8s cluster with F5 BNK:

1. Navigate to **Kubernetes > Clusters**
2. Click **Add Cluster**
3. Select a project (or create one)
4. Upload or paste your kubeconfig
5. Explore **F5 BNK** in the sidebar for Traffic Flow, Health Dashboard, Topology, and more

### 3. Configure Module Library (Optional — for OpenTofu deployments)

If you want to deploy infrastructure modules via OpenTofu:

1. Navigate to **Settings > System > Defaults**
2. Set **Module Library Git URL** to: `https://github.com/JLCode-tech/bnk-forge-modules.git`
3. Set **Module Library Git Ref** to: `release/2.2`
4. Go to **Settings > Environment Config** and click **Sync Modules**

### 4. Create a Project and Deploy

Navigate to **Build > Projects**:
- Click **New Project**
- Enter a name and select your region
- Add modules from the catalog, or use **Build > Blueprints** for pre-configured stacks

### Test Database

```bash
# Check database connection
docker exec bnk-forge-backend python -c "
from database import engine
from sqlalchemy import text
with engine.connect() as conn:
    result = conn.execute(text('SELECT 1'))
    print('Database connection: OK')
"
```

### Test Module Library Sync

1. Open https://localhost (or your server URL)
2. Navigate to **Settings**
3. Click **Environment Config** tab
4. Configure module library URL (if not in `.env`)
5. Click **Sync Modules** in Module Library section
6. Verify modules appear in **Build → Modules**

---

## Updating

To update to the latest version:

```bash
cd bnk-forge

# Laptop path
git pull --ff-only
make local-deploy

# Linux server path (preferred)
make upgrade-safe

# Compatibility wrapper
make update
```

---

## Uninstalling

To completely remove BNK Forge:

```bash
cd bnk-forge

# Stop and remove containers
docker compose down

# Remove volumes (CAUTION: deletes all data)
docker compose down -v

# Remove the directory
cd ..
rm -rf bnk-forge
```

---

## Next Steps

- [User Guide](USER_GUIDE.md) - Learn how to use BNK Forge
- [AWS SSO Setup](AWS_SSO_SETUP.md) - Configure AWS SSO authentication
- [Troubleshooting](TROUBLESHOOTING.md) - Common issues and solutions
