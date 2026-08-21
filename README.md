# BNK Forge

**Deploy, operate, and monitor F5 BIG-IP Next for Kubernetes (BNK)** — from Day 1 deployment to Day 2 operations through a single pane of glass.

![Version](https://img.shields.io/badge/Version-current_branch-blue)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![React](https://img.shields.io/badge/React-18-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-Python-green)
![Tests](https://img.shields.io/badge/Tests-CI%20validated-brightgreen)

---

## Quick Start

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) with Docker Compose, and Git. That's it — all other tools are inside the container.

### Prerequisite: the artifact runner network

BNK Forge's container-image engine runs each artifact step in its own container,
attached to a dedicated bridge network named **`bnk-forge-artifacts`**. Create it
once per host, picking any subnet that doesn't overlap your network:

```bash
docker network create --driver bridge --subnet 10.200.0.0/24 bnk-forge-artifacts
```

You only need this once per host. If the network already exists, Docker says so and
you can ignore it.

**Why a subnet at all.** Docker's auto-assigned pool (starting around
`172.17.0.0/16`) can overlap a host's VPN or other routed networks, breaking
connectivity mid-deploy — pinning one avoids that. The `make`/`install.sh` paths
below don't hardcode a single default (a fixed `10.200.0.0/24` collided on one
field site); instead they resolve the subnet, in order:

1. **`ARTIFACT_NETWORK_SUBNET=<cidr>`** — use that subnet verbatim.
2. **`ARTIFACT_NETWORK_SUBNET=auto`** — skip pinning and defer to Docker's
   `default-address-pools`. Add a dedicated pool for this network in
   `/etc/docker/daemon.json` (then restart docker):
   ```json
   { "default-address-pools": [ { "base": "192.168.200.0/20", "size": 24 } ] }
   ```
3. **Unset (default)** — auto-detect: try each subnet in
   `ARTIFACT_NETWORK_SUBNET_CANDIDATES` (default `10.200.0.0/24
   192.168.200.0/24 10.213.37.0/24 172.31.255.0/24`) and use the first one that
   doesn't overlap the host's routes or an existing docker network. If none are
   clean, it falls back to mode 2 with a warning.

This only runs when the network doesn't exist yet — once created, its own
subnet becomes a host route, so re-detecting on every `make deploy`/`upgrade`
would treat it as a collision with itself and never settle. On an existing
network these tools are a no-op, except: if you've pinned an explicit CIDR
that no longer matches what's actually there, you'll get a one-time warning
telling you how to recreate it.

Both variables may also be set in a `.env` file in the repo root.

**Why you have to do this by hand.** Docker Compose only creates networks that a
service actually attaches to. No BNK Forge service attaches to this one — and on
a Linux server none *can*, because the stack runs with `network_mode: host`, and
a host-networked service may not also join a bridge network. So declaring it in
`docker-compose.yml` would create nothing. The network exists purely for the
*artifact* containers the engine launches, which is why it has to be created
outside of Compose.

**Why the network exists at all.** Artifact images are third-party code. Putting
them on a network of their own keeps them off Docker's default bridge, where they
would sit alongside every other container on the host. They still get normal
outbound access, which they need to reach cloud control planes (e.g. `roksbnkctl`
talking to IBM Cloud), so this costs you nothing functionally.

If you skip it, everything else works — but any container-engine deployment fails
with `network not found`.

> The `make` targets below (`deploy`, `up`, `install`, `update`, `upgrade-safe`)
> and `dist/install.sh` create this network for you, so if you use them you can
> skip this step. They are a convenience, not a requirement — the command above is
> all they run.

### Laptop (macOS / Windows)

```bash
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge
docker network create --driver bridge --subnet 10.200.0.0/24 bnk-forge-artifacts   # once per host; any free --subnet <cidr> works
make deploy
```

Open **https://localhost** and accept the self-signed certificate warning.

> **Enabling MCP:** the MCP server and backend share a service credential you must
> set — put `MCP_SERVICE_PASSWORD` in `.env` before starting. Without it the stack
> still comes up, but the MCP server can't authenticate and MCP tools return auth
> errors until you set the variable and restart. See [.env.example](.env.example).

`make deploy` detects macOS/WSL and switches to bridge networking with published
ports (`docker-compose.local.yml`); on a Linux server it uses host networking. You
do not pick — it picks.

### Windows + WSL2

Everything above works from inside your WSL2 distro, with three differences worth
knowing:

1. **Docker must be exposed to the distro.** BNK Forge talks to Docker from inside
   WSL, so enable Docker Desktop → *Settings → Resources → WSL Integration* for the
   distro you clone into. Without it you get `The command 'docker' could not be
   found in this WSL 2 distro`. Everything runs on Docker Desktop's daemon, which is
   shared across distros — so create `bnk-forge-artifacts` **once**, not per distro.

2. **Bridge networking, not host networking — this is automatic.** WSL2 reports
   itself as Linux, but it runs Docker Desktop, so a `network_mode: host` container
   binds inside the hidden `docker-desktop` distro rather than yours, and nothing
   you start is reachable from Windows. The Makefile detects WSL (via `/proc/version`)
   and applies the laptop overlay, publishing ports instead. Consequence: the workers
   reach the Docker socket proxy by service DNS (`tcp://docker-socket-proxy:2375`)
   rather than `tcp://127.0.0.1:2375` as on a server.

3. **Reach it on published ports.** With the overlay, the stack publishes
   `443`/`80` (proxy), `8080` (frontend), `8000` (backend API), and `8081` (MCP). Use
   **https://localhost**; if port 443 isn't forwarded on your box, the frontend is
   directly available at **http://localhost:8080** and the API at
   **http://localhost:8000**.

Artifact containers behave the same as on a server: they attach to
`bnk-forge-artifacts` and mount their workspace from the named volume by subpath —
which is the only thing that works on Docker Desktop, where a host-path bind does
*not* share storage with the worker's named-volume mount.

### Linux Server

```bash
ssh user@your-server
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge
docker network create --driver bridge --subnet 10.200.0.0/24 bnk-forge-artifacts   # once per host; any free --subnet <cidr> works
make deploy
# Access from browser: https://your-server-ip
```

For first-time destructive bootstrap only (wipes existing BNK Forge volumes), use `make install`.

### Login

| Field | Value |
|-------|-------|
| **Username** | `admin` |
| **Password** | _generated on first startup — see below_ |

The admin password is generated randomly on first startup (there is no shipped
default). Retrieve it once from the backend logs:

```bash
docker exec bnk-forge-backend cat /app/keys/initial_admin_password
```

Or choose your own beforehand by setting `DEFAULT_ADMIN_PASSWORD` in `.env`. You
will be **required** to change it on first login (enforced server-side).

---

## Git Workflow (Required)

- `staging` is the shared day-to-day integration branch.
- Create feature/work branches from `staging`.
- Merge normal feature/work branches back into `staging`.
- `main` is protected and release-only (promotion path: `staging -> main`).

Example day-to-day flow:

```bash
git checkout staging
git pull --ff-only
git checkout -b feature/my-change
# ...work...
# open PR/merge target: staging
```

---

## Common Commands

### Laptop (macOS / Windows)

```bash
make local-deploy     # Build + start everything on your laptop
make local-up         # Start containers (already built)
make local-down       # Stop and remove containers
make local-status     # Show container health
make local-logs       # Tail container logs
make local-restart    # Restart all containers
```

### Linux Server

```bash
make deploy           # Build + start everything on a Linux server
make up               # Start containers (already built)
make down             # Stop and remove containers
make status           # Show container health
make server-logs      # Tail container logs
make restart          # Restart all containers
```

These targets run `make ensure-artifact-network` first, so the
`bnk-forge-artifacts` network (see [Prerequisite](#prerequisite-the-artifact-runner-network))
is created if you haven't already made it yourself. Bringing the stack up with a
bare `docker compose up` skips that, so create the network by hand on that path.

### General

```bash
make install          # First-time destructive server bootstrap (wipes BNK Forge data)
make update           # Backward-compatible non-destructive update wrapper
make upgrade-safe     # Preferred server upgrade path (preflight + strict verification)
make mcp-readiness    # MCP liveness + runtime readiness gate
make test             # Run all tests
make help             # Show all available commands
```

---

## What It Does

### Day 1 — Deploy BNK
- **Deployment Wizard** — 3 choices, running BNK in under 10 minutes
- **Deployment Blueprints** — pre-packaged stacks that deploy BNK in minutes
- **20 Python-defined modules** — zero OpenTofu dependency for BNK stack
- **Smart dependency management** — automatic variable wiring between modules
- **Parallel execution** — independent modules deploy concurrently (25-50% faster)

### Day 2 — Operate & Monitor
- **Traffic Flow Overview** — visualize how traffic flows through gateways, routes, and backends
- **BNK Health Dashboard** — real-time health of FLO, TMM, gateways, data plane, security
- **Multi-Cluster Fleet** — aggregate health across all connected clusters
- **Drift Detection** — scheduled checks catch out-of-band changes
- **BNK Upgrade Workflow** — rolling upgrade with pre-checks, health gates, rollback
- **Config Export/Import** — snapshot BNK resources, diff clusters, promote configs
- **Webhook Alerting** — Slack, Teams, and generic webhook notifications

### Kubernetes & Helm
- **Full resource management** — view, describe, scale, restart, delete across all types
- **Pod operations** — real-time logs, exec terminal, events, metrics
- **Helm package management** — install, upgrade, rollback with hierarchical browsing
- **Node operations** — cordon, drain, uncordon for maintenance

### Security
- **RBAC** — admin, operator, and viewer roles on all 200+ API routes
- **Audit trail** — automatic logging of every mutating operation
- **JWT authentication** with forced password change on first use

---

## Architecture

```
                        Browser
                           |
                           v
                    ┌─────────────┐
                    │  Nginx Proxy │  (HTTPS + WebSocket)
                    └──────┬──────┘
                     /     │     /api/
                     v     │      v
              ┌──────────┐ │ ┌──────────┐
              │ Frontend  │ │ │ Backend  │
              │ React 18  │ │ │ FastAPI  │
              │ TypeScript│ │ │ Python   │
              └──────────┘ │ └────┬─────┘
                           │      │
                  ┌────────┼──────┼────────┐
                  v        v      v        v
              PostgreSQL  Redis  Celery   K8s
               (data)    (cache) (tasks)  (clusters)
```

All services run as Docker containers via `docker-compose.yml`, with `docker-compose.local.yml` layered on laptops/Docker Desktop.

---

## Configuration

### Module Library

After first login, configure the module library to enable deployment blueprints:

1. Go to **Settings > Defaults**
2. Set **Module Library Git URL** to: `https://github.com/JLCode-tech/bnk-forge-modules.git`
3. Set **Module Library Git Ref** to: `release/2.2`
4. Go to **Settings > Environment Config** and click **Sync Modules**

### Connecting Kubernetes Clusters

BNK Forge connects to clusters via kubeconfig:

1. Go to **Kubernetes > Clusters**
2. Click **Add Cluster**
3. Upload or paste your kubeconfig

Kubeconfig-first fleet (D3) is the primary architecture. Operator connectivity remains available as a secondary/legacy-supported path for specific environments.

### Environment Variables (Optional)

Most settings are configurable in the GUI. For advanced configuration, copy and edit the example:

```bash
cp .env.example .env
# Edit .env with your settings
```

See [.env.example](.env.example) for all available options.

---

## Updating

Use deployment-mode-aware update commands:

```bash
# Laptop (Docker Desktop)
git pull --ff-only
make local-deploy

# Linux server (preferred)
make upgrade-safe

# Compatibility wrapper (non-destructive)
make update
```

These paths keep existing data and run health verification. Use `make install` only for intentional clean-slate setup.

---

## Troubleshooting

```bash
# Check container status
make status

# View backend logs
docker compose logs backend --tail 50

# View all service logs
docker compose logs --tail 20

# Full reset (WARNING: destroys all data)
make install
```

For detailed troubleshooting, see [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

---

## Project Structure

```
bnk-forge/
├── backend/              # FastAPI backend (Python)
│   ├── routes/           #   API endpoints (200+)
│   ├── services/         #   Business logic
│   ├── models/           #   SQLAlchemy models
│   └── modules/          #   Python-defined K8s/BNK modules
├── frontend-v2/          # React 18 frontend (TypeScript)
│   └── src/
│       ├── components/   #   UI components
│       ├── pages/        #   Route pages
│       └── hooks/        #   React Query hooks
├── proxy/                # Nginx reverse proxy
├── scripts/              # Build and maintenance scripts
├── docs/                 # Documentation
├── docker-compose.yml    # Server stack (Linux — host networking)
├── docker-compose.local.yml  # Laptop overlay (macOS/Windows — bridge networking)
├── Makefile              # All commands (make help)
└── upgrade.sh            # Non-destructive upgrade script
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [User Guide](docs/USER_GUIDE.md) | Complete guide to using BNK Forge |
| [API Reference](docs/API_REFERENCE.md) | Full REST API endpoint catalog (200+ endpoints) |
| [Testing Guide](docs/TESTING.md) | Test architecture, patterns, and how to run tests |
| [Installation Guide](docs/INSTALLATION.md) | Detailed setup for all environments |
| [Deployment Guide](docs/DEPLOYMENT.md) | Production deployment |
| [Upgrade Runbook](docs/UPGRADE_RUNBOOK.md) | Server upgrade procedure |
| [Architecture Index](docs/architecture/README.md) | Current vs archived architecture documents |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common issues and solutions |
| [AWS SSO Setup](docs/AWS_SSO_SETUP.md) | Configure AWS SSO authentication |
| [Docker Architecture](docs/DOCKER.md) | Multi-target Dockerfile design |
| [Disk Management](docs/DISK_MANAGEMENT.md) | Docker disk usage and cleanup |
| [Product Vision](docs/PRODUCT_VISION.md) | Product strategy and positioning |
| [Strategic Roadmap](docs/STRATEGIC_ROADMAP.md) | Strategic epics and sequencing for platform maturity |
| [Strategic Backlog](docs/STRATEGIC_BACKLOG.md) | Backlog-ready strategic tickets with Now/Next/Later sequencing |
| [MCP Productization Plan](docs/MCP_PRODUCTIZATION_PLAN.md) | Roadmap for safe, observable AI-operable interface |
| [Sprint Plan — Platform Truthfulness 001](docs/SPRINT_PLATFORM_TRUTHFULNESS_001.md) | Agent-ready sprint plan for the first strategic execution wave |
| [Sprint Plan — Contract Trust 001](docs/SPRINT_CONTRACT_TRUST_001.md) | Agent-ready sprint plan for API contract hardening |
| [Sprint Plan — MCP Foundation 001](docs/SPRINT_MCP_FOUNDATION_001.md) | Agent-ready sprint plan for AI-operable interface foundations |
| [Sprint Plan — Operability Baseline 001](docs/SPRINT_OPERABILITY_BASELINE_001.md) | Agent-ready sprint plan for observability and release discipline |

---

## Development

```bash
# Run all tests
make test

# Run checks before pushing
make pre-push

# See all commands
make help
```

### Version Compatibility

| BNK Forge | Module Library | F5 BNK | Status |
|-----------|---------------|--------|--------|
| **Current branch** | **release/2.2** | 2.2 GA | **Active** |
| 2.10.x - 2.12.x | release/2.2 | 2.2 GA | Supported |

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## Project governance

- [Contributing](CONTRIBUTING.md) — workflow, ADRs, code style
- [Development guide](docs/DEVELOPMENT.md) — build, test, architecture
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md) — how to report a vulnerability
- Licensed under the [Apache License 2.0](LICENSE)
