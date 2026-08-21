# BNK Forge — Installation Guide

## Prerequisites

- **Docker Engine 24+** with **Docker Compose v2.24+**
- Network access to `ghcr.io` (images are public — no registry login required)
- 4 GB RAM minimum (8 GB recommended)
- 10 GB disk space

## Quick Start

### 1. Download and extract

```bash
tar xzf bnk-forge-3.1.6.tar.gz
cd bnk-forge-3.1.6
```

### 2. Configure

```bash
cp .env.example .env
nano .env   # Set BNK_FORGE_REGISTRY and passwords
```

**Required settings in `.env`:**

| Variable | Description | Example |
|---|---|---|
| `BNK_FORGE_REGISTRY` | Container registry URL (no trailing slash) | `ghcr.io/f5devcentral` (public) |
| `BNK_FORGE_VERSION` | Image version tag | `3.1.6` |
| `POSTGRES_PASSWORD` | PostgreSQL password | *(change for production)* |
| `REDIS_PASSWORD` | Redis password | *(change for production)* |

### 3. Install

**Linux server** (host networking — production):
```bash
chmod +x install.sh
./install.sh
```

**macOS / Windows laptop** (bridge networking — development):
```bash
chmod +x install.sh
./install.sh --local
```

### 4. Access

- **Mac/Windows (`--local`):** open **https://localhost**
- **Linux server:** open **https://\<server-ip\>** — the installer prints the exact URL at the end

Accept the self-signed certificate warning. Login: **admin** / **changeme**

---

## Manual Installation (without install.sh)

> **Create the artifact runner network first.** The container-image engine runs
> each artifact step on a dedicated bridge network (`bnk-forge-artifacts`) so
> artifact containers don't share the default bridge with the rest of the host.
> `docker compose up` does **not** create it — no service references it, and
> under host networking none can — so without this step every container-engine
> deployment fails with `network not found`. `install.sh` does this for you; a
> manual install must do it explicitly. It is idempotent.
>
> Any non-overlapping subnet works — `install.sh` doesn't hardcode a single
> default (a fixed `10.200.0.0/24` collided on one field site); it resolves
> one automatically, but only the FIRST time it creates the network: an
> explicit `ARTIFACT_NETWORK_SUBNET=<cidr>` (or `auto` to defer to Docker's
> `default-address-pools`) wins, otherwise it picks the first non-colliding
> entry from `ARTIFACT_NETWORK_SUBNET_CANDIDATES` against the host's routes.
> Both may be set in `.env`. To dedicate a Docker pool for `auto` mode, add to
> `/etc/docker/daemon.json` and restart docker:
> ```json
> { "default-address-pools": [ { "base": "192.168.200.0/20", "size": 24 } ] }
> ```
>
> On a re-run against an existing network, `install.sh` is a no-op here
> except for a one-time warning if you've pinned an explicit CIDR that no
> longer matches what's actually there — it never re-detects (the network's
> own subnet would otherwise look like a collision with itself).

### Linux Server

```bash
cp .env.example .env
# Edit .env with your registry and passwords
docker network create --driver bridge --subnet 10.200.0.0/24 bnk-forge-artifacts   # once; skip if it exists; any free --subnet <cidr> works
docker compose pull
docker compose up -d
```

### macOS / Windows Laptop

```bash
cp .env.example .env
# Edit .env with your registry and passwords
docker network create --driver bridge --subnet 10.200.0.0/24 bnk-forge-artifacts   # once; skip if it exists; any free --subnet <cidr> works
docker compose -f docker-compose.yml -f docker-compose.local.yml pull
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d
```

---

## Operations

### Check status
```bash
docker compose ps
curl -sf http://localhost:8000/api/system/health | python3 -m json.tool
```

### View logs
```bash
docker compose logs -f --tail 50           # All services
docker compose logs -f backend             # Backend only
```

### Stop / Uninstall
```bash
./uninstall.sh                             # Stop containers (keeps data)
./uninstall.sh --purge                     # Stop + delete all data (⚠️ destructive)
./uninstall.sh --purge --force             # Same, skip confirmation prompts
```

Or manually:
```bash
docker compose down                        # Stop containers (keeps data)
docker compose down -v                     # Stop + delete all data (⚠️ destructive)
```

### Upgrade
```bash
# Update BNK_FORGE_VERSION in .env, then:
docker compose pull
docker compose up -d --force-recreate
```

### Backup database
```bash
docker exec bnk-forge-postgres pg_dump -U bnkforge bnkforge | gzip > backup_$(date +%Y%m%d).sql.gz
```

### Restore database
```bash
gunzip -c backup_20260417.sql.gz | docker exec -i bnk-forge-postgres psql -U bnkforge bnkforge
```

---

## Architecture

| Service | Image | Port | Purpose |
|---|---|---|---|
| backend | bnk-forge-api | 8000 | FastAPI REST API |
| celery-worker | bnk-forge-worker | — | Async task execution (OpenTofu, Helm) |
| celery-worker-2 | bnk-forge-worker | — | Additional worker capacity |
| celery-beat | bnk-forge-beat | — | Periodic task scheduler |
| frontend | bnk-forge-frontend | 8080 | React SPA (nginx) |
| proxy | bnk-forge-proxy | 80, 443 | TLS termination + reverse proxy |
| mcp | bnk-forge-mcp | 8081 | AI assistant MCP server |
| postgres | postgres:16-alpine | 5432 | PostgreSQL database |
| redis | redis:7-alpine | 6379 | Task queue + caching |
| docker-socket-proxy | tecnativa/docker-socket-proxy | 127.0.0.1:2375 | Scoped Docker API for the artifact (container-image) engine |

---

## File Structure

```
bnk-forge-3.1.6/
├── docker-compose.yml          # Main compose (Linux server — host networking)
├── docker-compose.local.yml    # Overlay for macOS/Windows (bridge networking)
├── .env.example                # Configuration template
├── VERSION                     # Version file
├── install.sh                  # One-command installer
├── uninstall.sh                # Uninstaller (stop + optional data purge)
├── README.md                   # This file
├── nginx/
│   ├── proxy.local.conf        # Proxy nginx config for local mode
│   └── frontend.local.conf     # Frontend nginx config for local mode
└── secrets/                    # Mount point for credentials (FAR, etc.)
```

## Publishing a Release (for maintainers)

This section is for maintainers who build and publish new releases.

### Prerequisites

- GitHub CLI installed: `brew install gh`
- Authenticated: `gh auth login`
- Docker with buildx support (included in Docker Desktop)

### Step 1: Set up multi-arch builder (one-time)

```bash
cd /path/to/bnk-forge
make buildx-setup
```

This registers QEMU emulators for cross-platform builds and creates a `docker-container` buildx builder named `bnk-forge-multiarch` that supports both `linux/amd64` and `linux/arm64`.

> **Docker Desktop users:** QEMU is already included — `buildx-setup` will detect this automatically.
>
> **Linux servers:** Requires `qemu-user-static` or `tonistiigi/binfmt` (installed automatically by the target).

### Step 2: Build the distribution tarball

```bash
make dist
```

This creates `dist/bnk-forge-VERSION.tar.gz` containing all files needed for installation.

### Step 3: Push multi-arch Docker images to registry

```bash
# Authenticate to your registry first
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Build + push all images for amd64 + arm64 (default)
make push-images BNK_FORGE_REGISTRY=ghcr.io/f5devcentral

# Or push only amd64 (faster, if you don't need ARM)
make push-images BNK_FORGE_REGISTRY=ghcr.io/f5devcentral PLATFORMS=linux/amd64
```

This uses `docker buildx build --push` to build all 6 images (api, worker, beat, frontend, proxy, mcp) for both architectures and push **multi-arch manifest lists** to the registry. Each tag (e.g., `bnk-forge-api:3.1.6`) is a manifest that Docker automatically resolves to the correct platform on `docker pull`.

**Verify the manifest:**
```bash
docker manifest inspect ghcr.io/f5devcentral/bnk-forge-api:3.1.6
```

You should see entries for both `linux/amd64` and `linux/arm64`.

### Step 4: Create GitHub Release

```bash
VERSION=$(cat VERSION)
gh release create v${VERSION} dist/bnk-forge-${VERSION}.tar.gz \
  --title "BNK Forge ${VERSION}" \
  --notes "Release notes here"
```

**Useful flags:**
- `--draft` — Create a draft release (not visible until published)
- `--prerelease` — Mark as pre-release
- `--generate-notes` — Auto-generate release notes from commits

### What `gh release create` does

1. Creates a Git tag (`v3.1.6`) on the current commit
2. Creates a GitHub Release page at `https://github.com/f5devcentral/bnk-forge/releases/tag/v3.1.6`
3. Uploads the tarball as a downloadable release asset

### End-user download URL

After publishing, users can download and install with:

```bash
# Download from GitHub Releases
curl -L https://github.com/f5devcentral/bnk-forge/releases/download/v3.1.6/bnk-forge-3.1.6.tar.gz | tar xz
cd bnk-forge-3.1.6
./install.sh
```

---

## Troubleshooting

**Backend won't start:**
```bash
docker logs bnk-forge-backend
```

**Can't pull images:**
```bash
# Verify registry auth
docker pull ${BNK_FORGE_REGISTRY}/bnk-forge-api:${BNK_FORGE_VERSION}
```

**Port conflicts:**
```bash
# Check what's using port 443/8000
lsof -i :443
lsof -i :8000
```
**On Mac exec format error:**
```
make dist

...

=> ERROR [base 2/4] RUN groupadd -g 999 docker || true &&     groupadd -g 1000 bnkforge &&     useradd -m -u 1000 -g bnkforge -G docker -s /bin/bash bnkforge                            0.0s
------
 > [base 2/4] RUN groupadd -g 999 docker || true &&     groupadd -g 1000 bnkforge &&     useradd -m -u 1000 -g bnkforge -G docker -s /bin/bash bnkforge:
------
Dockerfile:45

--------------------

  44 |     # Create docker group with GID 999 (common on Linux hosts) for socket access

  45 | >>> RUN groupadd -g 999 docker || true && \

  46 | >>>     groupadd -g ${GID} bnkforge && \

  47 | >>>     useradd -m -u ${UID} -g bnkforge -G docker -s /bin/bash bnkforge

  48 |     

--------------------

failed to solve: failed to compute cache key: failed to get stream processor for application/vnd.oci.image.layer.v1.tar+gzip: fork/exec /usr/bin/unpigz: exec format error
```
Restart Docker Desktop

**Container bnk-forge-backend Error:**

When running `make deploy` or `make local-deploy` or `./install`.  This can be cause by out of sync `alembic_version` and what is in the <project_dir>/alembic/versions files.  This usually only occurs when you have an existing postgres database already running with a previous version.
```
=== Starting all services ===
[+] up 10/10
 ✔ Container bnk-forge-postgres        Healthy                                                                                             4.5s
 ✔ Container bnk-forge-postgres-backup Started                                                                                             4.0s
 ✔ Container bnk-forge-redis           Healthy                                                                                             4.5s
 ✘ Container bnk-forge-backend         Error dependency backend failed to start                                                           10.5s
 ✔ Container bnk-forge-celery-worker-2 Created                                                                                             0.2s
 ✔ Container bnk-forge-frontend        Created                                                                                             0.1s
 ✔ Container bnk-forge-mcp             Created                                                                                             0.1s
 ✔ Container bnk-forge-celery-beat     Created                                                                                             0.2s
 ✔ Container bnk-forge-celery-worker   Created                                                                                             0.2s
 ✔ Container bnk-forge-proxy           Created

```

Check the version in the database table:
```bash
docker exec bnk-forge-postgres psql -U bnkforge -d bnkforge -c "SELECT * from alembic_version"
 version_num
-------------
 v2_060
(1 row)
```

Update the version to the highest version in <project_dir>/alembic/versions
```bash
docker exec bnk-forge-postgres psql -U bnkforge -d bnkforge -c "UPDATE alembic_version SET version_num = 'v2_056' WHERE version_num = 'v2_060';" 2>&1
```