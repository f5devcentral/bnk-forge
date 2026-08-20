---
title: "The BNK Forge Developer's Guide"
subtitle: "Building, Deploying, and Extending"
date: "May 2026"
documentclass: report
geometry: margin=1in
toc: true
toc-depth: 3
numbersections: true
colorlinks: true
linkcolor: blue
urlcolor: blue
---

# Preface

This guide is your single starting point for building with BNK Forge. It walks you through what BNK Forge is, how to install and run it on every supported platform, and how to author the modules and blueprints that turn it into a deployable platform for the F5 BNK product. It closes with a reference for extending the system itself with new cloud vendors.

Each chapter is example-first. Where a concept can be explained in prose or shown in code, you will find code. Where a contract exists between you and the platform — a manifest field, a variable name, a directory layout — the contract is given exactly, with a working sample alongside.

The intended reader is a developer who has shipped infrastructure as code before, but has not yet written content for BNK Forge. No prior familiarity with the platform is assumed.

---

# Chapter 1 — Introduction to BNK Forge

## 1.1 What BNK Forge is

BNK Forge is an opinionated orchestration platform for deploying the F5 BNK (Big-IP Next for Kubernetes) platform alongside the cloud, cluster, and application infrastructure that BNK depends on. It is built around three primitives:

- **Modules.** Versioned, reusable deployment packs with a defined lifecycle (init, plan, apply, destroy, refresh, drift). A module is the unit of *what* gets deployed: a VPC, a managed Kubernetes cluster, a Helm chart, an Ansible role, a one-shot Python script.
- **Blueprints.** Orchestration definitions that reference one or more modules in a defined order, wire variables and outputs between them, and describe a complete user-facing solution. A blueprint is *how* a set of modules combine into something deployable.
- **Projects.** Runtime instances. A project picks a target environment, supplies a credential template, deploys a blueprint, and tracks state for the modules that ran.

The platform is multi-engine: a single blueprint can mix OpenTofu, Ansible, Kubernetes (manifests or Helm), and Python script modules. The right engine for each module is selected from the module's manifest by a router that also considers connectivity to the target cluster.

## 1.2 Architecture at a glance

BNK Forge ships as a Docker Compose stack. The standard topology has six services:

- **Nginx proxy** terminates HTTPS + WebSockets and fronts the rest of the stack.
- **React frontend** (Vite, React 18, Tailwind, shadcn/ui) renders the user interface.
- **FastAPI backend** holds the API surface and business logic.
- **Postgres** stores projects, modules, blueprints, secrets, and state.
- **Redis** caches and brokers Celery tasks.
- **Celery workers + beat** run async work — module applies, drift checks, cluster auto-registration.

Two optional services extend the stack:

- **MCP Server** exposes Forge's API as Model Context Protocol tools for AI integrations.
- **bnk-operator** is a Kopf-based agent that runs *inside* customer Kubernetes clusters, talking to Forge over a persistent WebSocket and executing K8s operations locally. The operator path avoids exposing the cluster's API server externally.

A high-level data flow:

```
Browser
   |
   v
Nginx (HTTPS + WS)
   |
   +--> Frontend (Vite/React)
   +--> Backend (FastAPI) --+--> Postgres
                            +--> Redis
                            +--> Celery workers
                                       |
                                       v
                                K8s clusters (kubeconfig or bnk-operator)
```

## 1.3 Two core authoring loops

If you are reading this guide you are likely about to enter one of two loops:

1. **Module + blueprint authoring.** You write or extend reusable deployment packs and the orchestration definitions that compose them. Most of this guide is about this loop. Chapters 6 through 12 walk through it end to end.
2. **Cloud vendor extension.** You add support for a new cloud (Oracle, Linode, Alibaba, etc.) so blueprints can target it. Chapter 13 covers this.

Both loops are productive once you have a working development environment. Chapters 2 through 5 show you how to get one.

## 1.4 Repository at a glance

A few directories in `bnk-forge` are worth knowing about before you start:

```
backend/                  FastAPI + SQLAlchemy + Celery
  routes/                 ~42 API route files
  services/               ~73 business-logic services
  schemas/                Pydantic v2 request/response models
  models/                 SQLAlchemy ORM models
  modules/                Python-defined deployment modules
  alembic/                DB migrations
  tests/                  unit / component / contract / integration

frontend-v2/              React 18 + Vite + TypeScript
  src/hooks/              React Query hooks (one per domain)
  src/lib/api/            Axios API modules (one per domain)
  src/components/         shadcn/ui primitives + feature components
  src/pages/              route pages (lazy-loaded)

mcp-server/               Standalone MCP server (own pyproject.toml)
bnk-operator/             Kopf-based Python agent
proxy/                    Nginx config + TLS
cli/                      CLI helpers
scripts/                  Build / maintenance / upgrade
```

You will mostly be touching `backend/` and `frontend-v2/` during platform work, and `modules/` and `blueprints/` (in your own external repositories) during content work.

## 1.5 What ships and what you author

Forge ships with a small set of *built-in* modules and blueprints — enough to bootstrap the platform and demonstrate every engine. Everything beyond that base is meant to come from author-controlled repositories that you register with Forge as **module sources** and **blueprint sources**. Chapters 6 and 7 cover these repositories in depth.

The key conceptual line is:

- **Built-in modules** live inside `bnk-forge/backend/modules/` and ship with the platform.
- **Third-party modules** live in their own git repositories, are imported through a Module Source, and surface in the Forge module catalog after sync.
- **Built-in blueprints** are seeded from `bnk-forge/backend/data/stack_templates.json`.
- **Third-party blueprints** live in their own git repositories, are imported through a Blueprint Source, and surface as deployable releases after sync.

Almost every blueprint and module you write will live in a third-party repository. That is the supported, future-proof path.

---

# Chapter 2 — How to Install BNK Forge

## 2.1 What "install" means

BNK Forge has two install modes:

- **Local development**: Forge runs on your laptop with bridge networking and HTTPS on `localhost`. This is what you want during day-to-day module/blueprint authoring. Chapters 3, 4, and 5 cover the per-platform setup that gets you here.
- **Server deployment**: Forge runs on a Linux server with host networking. The same Makefile drives both modes; the platform detection in `Makefile` picks the right Docker Compose overlay automatically.

You install Forge once, then use `make update` to refresh in place. Local and server installs share the same `make install` target — the difference is entirely the host platform.

## 2.2 What `make install` does

`make install` is the destructive first-time bootstrap. It:

1. Stops any running Forge containers.
2. Wipes all named Docker volumes prefixed with `bnk-forge` (Postgres data, Redis, uploads, MCP keys).
3. Removes Forge images so the next build starts from a clean slate.
4. Builds API, worker, beat, frontend, and MCP images.
5. Starts the stack.
6. Waits for health checks.
7. Prints the URL, default credentials, and next steps.

The destructive nature means you only run it once per environment. After that, you use:

- `make update` — pulls latest code, rebuilds, and restarts non-destructively (preserves all data).
- `make upgrade-safe` — preferred for server upgrades; runs preflight checks and strict verification before swapping containers.
- `make deploy` — rebuilds and restarts (used during active development).
- `make restart` — bounces containers without rebuilding.

`make install` is appropriate for:

- A brand-new laptop setting up Forge for the first time.
- A throwaway environment where you want to start fresh.
- A server where Forge has never been installed before.

`make install` is **not** appropriate for:

- An environment where users have already created projects, secrets, or imported blueprints — you would lose all of it.

## 2.3 System requirements

| Resource | Minimum | Recommended |
| --- | --- | --- |
| OS         | Linux, macOS, or Windows + WSL2 | Same |
| Docker     | Docker Engine 24+ or Docker Desktop 4.30+ | Latest |
| Memory     | 8 GB free for the stack | 16 GB |
| Disk       | 20 GB free | 40 GB |
| CPU        | 4 cores | 8 cores |
| Network    | Outbound internet to pull base images and provider plugins | Same |

For development you also need:

| Tool | Version |
| --- | --- |
| Git           | any modern version |
| Python        | 3.11.x for backend hacking |
| Node.js       | 22+ for frontend hacking |
| Make          | GNU Make 4+ |

The platform-specific chapters below show you how to install each.

## 2.4 The high-level recipe

The same recipe works on every supported platform; the only differences are how you install the prerequisites.

```bash
# 1. Clone
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge

# 2. First-time install (destructive — wipes any prior install)
make install

# 3. Wait for health checks. The final output prints the URL.

# 4. Open the UI
#    macOS / WSL / Linux desktop:  https://localhost/
#    Default login: admin  (password: set DEFAULT_ADMIN_PASSWORD, else the generated one at /app/keys/initial_admin_password; change on first login)
```

That is it. The platform detection in the Makefile does the right thing on Darwin (macOS), WSL2, and native Linux without further configuration.

After login, the recommended bootstrap flow is:

1. Change the admin password.
2. Add a Module Source under **Settings → Modules → Add Source** so the catalog has something to deploy.
3. Optionally add a Blueprint Source under **Settings → Blueprint Sources → Add Source**.
4. Create a project under **Build → Projects → New**.
5. Deploy a blueprint from **Blueprints**.

---

# Chapter 3 — Setting Up a Local Development Environment on macOS

This chapter assumes a fresh macOS machine. By the end you will have a running BNK Forge at `https://localhost/` and a working backend + frontend dev loop.

## 3.1 Install Homebrew

If you do not already have Homebrew, install it:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow the post-install instructions to add `brew` to your shell `PATH`.

## 3.2 Install command-line dependencies

```bash
brew install git make python@3.11 node@22
brew install --cask docker          # Docker Desktop
```

Open Docker Desktop once so it can install its CLI helpers, then verify:

```bash
docker --version          # Docker version 24.x or newer
docker compose version    # Docker Compose v2.x
git --version
make --version            # GNU Make 4+
python3.11 --version
node --version            # v22.x
```

## 3.3 Configure Docker Desktop resources

Open Docker Desktop → **Settings → Resources** and set:

- **CPUs**: at least 4 (8+ recommended)
- **Memory**: at least 8 GB (16 GB recommended)
- **Swap**: 2 GB
- **Disk image size**: 60 GB+

Apply and restart Docker Desktop. The Forge stack with all workers running uses ~6 GB of RAM under load.

## 3.4 Clone and install

```bash
mkdir -p ~/Projects && cd ~/Projects
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge

make install
```

`make install` will detect macOS (`uname -s` returns `Darwin`) and use the local Compose overlay (`docker-compose.local.yml`). This gives you bridge networking with mapped ports — services bind inside the Forge containers and Nginx exposes them through `localhost`.

When the install finishes you will see something like:

```
=========================================
  BNK Forge — Installed
=========================================

  URL:   https://localhost/
  Login: admin  (password: DEFAULT_ADMIN_PASSWORD if set, else generated — see /app/keys/initial_admin_password)
```

Open the URL in your browser. Self-signed cert warnings are expected; accept once.

## 3.5 Set up the backend dev loop

If you plan to edit backend code:

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Test the suite runs:

```bash
python -m pytest tests/unit/ -q
```

You can edit Python files in `backend/` and the running container will pick them up without a rebuild — the backend Dockerfile mounts the source directory in dev mode.

## 3.6 Set up the frontend dev loop

For frontend hacking, run Vite directly so you get HMR:

```bash
cd frontend-v2
npm install
npm run dev
```

This starts Vite on `http://localhost:5173/` with full HMR. The Vite dev server proxies API calls to the running backend on the standard Forge port. Use this URL during frontend work; visit `https://localhost/` (Nginx-fronted) when testing the production-like rendering.

## 3.7 Daily workflow

```bash
# Pull latest, rebuild, restart (non-destructive)
make update

# Inspect logs
make logs                  # all services
docker logs -f bnk-forge-backend

# Stop without removing data
make down

# Bounce just the backend after editing a service
make deploy-backend

# Show overall health
make status
```

## 3.8 Uninstalling

If you want a clean slate, run `make install` again — it is idempotent and destructive in the same direction. To leave macOS unchanged:

```bash
docker compose down -v --remove-orphans
docker volume ls | grep bnk-forge | awk '{print $2}' | xargs -r docker volume rm
docker image ls | grep bnk-forge | awk '{print $3}' | xargs -r docker image rm
```

---

# Chapter 4 — Setting Up a Local Development Environment on Windows with WSL2 and Docker Desktop

This chapter walks you through the recommended Windows path: WSL2 for the Linux user space, Docker Desktop for the engine, and Forge running entirely inside the WSL distro.

## 4.1 Why WSL2

Forge is a Linux-native stack. Running it under WSL2 gives you:

- Real Linux file system, network, and process semantics.
- Predictable Docker Compose behavior (no Windows path translation gotchas).
- Direct compatibility with the same Makefile macOS and Linux developers use.

The Forge Makefile auto-detects WSL2 (it inspects `/proc/version` for `microsoft` or `wsl`) and applies the same Compose overlay macOS uses. Your Windows host is largely transparent.

## 4.2 Install WSL2

From an elevated PowerShell:

```powershell
wsl --install -d Ubuntu-22.04
wsl --set-default-version 2
wsl --update
```

Reboot if prompted. Open the Ubuntu app from the Start menu and create your Linux user account.

Verify from inside Ubuntu:

```bash
uname -a                       # should mention WSL2
cat /proc/version              # should mention "microsoft"
```

## 4.3 Install Docker Desktop with WSL integration

Download Docker Desktop for Windows from <https://docs.docker.com/desktop/install/windows-install/> and install with the default options.

After install, open Docker Desktop → **Settings → Resources → WSL Integration**:

- Enable integration with the default WSL distro.
- Enable integration with `Ubuntu-22.04` (or whichever distro you chose).

Apply and restart. Verify from inside WSL:

```bash
docker --version
docker compose version
docker ps
```

The last command should run without sudo and return an empty list (no containers yet). If it says "permission denied", restart Docker Desktop and try again.

## 4.4 Configure Docker Desktop resources

Same recommendations as macOS: 4+ CPUs, 8+ GB RAM, 60+ GB disk. Set these under **Settings → Resources → Advanced**.

## 4.5 Install command-line dependencies inside WSL

From your WSL Ubuntu shell:

```bash
sudo apt update
sudo apt install -y git make build-essential

# Python 3.11 from deadsnakes PPA
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev

# Node.js 22 from NodeSource
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

Verify:

```bash
git --version
make --version
python3.11 --version
node --version
docker --version
```

## 4.6 Clone inside WSL — not on a Windows mount

Important: clone Forge into the WSL file system (`~`), **not** under `/mnt/c/...`. Cross-OS file events on `/mnt/c/...` are unreliable, and Vite HMR + Docker volume mounts will misbehave.

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge
```

If you must keep the project on a Windows drive (e.g. for backup software), enable file-watch polling — see section 4.10.

## 4.7 First-time install

```bash
make install
```

The Makefile detects WSL2 (the `IS_WSL` shell guard runs `grep -qiE 'microsoft|wsl' /proc/version`) and applies the local Compose overlay. The resulting bridge network exposes services through Docker Desktop's port forwarding to Windows.

When install finishes, open the URL it prints in your Windows browser:

```
https://localhost/
```

Docker Desktop on Windows transparently forwards the port from the WSL distro to the Windows host, so `localhost` works in Edge/Chrome/Firefox without further setup.

## 4.8 Set up the backend dev loop

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/unit/ -q
```

## 4.9 Set up the frontend dev loop

```bash
cd frontend-v2
npm install
npm run dev
```

Vite will print `http://localhost:5173/`. Open that in your Windows browser; Docker Desktop forwards 5173 the same way it forwards 443.

## 4.10 If your code lives on a Windows drive

Working from `/mnt/c/...` or `/mnt/d/...` is supported but not recommended because file change events do not propagate reliably from NTFS to inotify. If you must:

```bash
# In your shell startup file (~/.bashrc):
export CHOKIDAR_USEPOLLING=1     # used by Vite
export WATCHPACK_POLLING=true    # used by webpack-style watchers
```

The Makefile already enables polling for backend file watchers in WSL — see commit `0bdfbeae chore(dev): enable file-watch polling for WSL2 mounts`.

Polling is ~10× more CPU-expensive than native inotify, so prefer the WSL-native filesystem when you can.

## 4.11 Common WSL gotchas

- **`docker: command not found` after Windows reboot.** Docker Desktop did not start on login. Open it from the Start menu, wait for the whale icon to settle, then retry.
- **`make: command not found`.** You forgot `sudo apt install make build-essential`.
- **HMR works in Vite logs but the browser does not update.** You are probably on a `/mnt/c/...` mount. Move the project to `~` or enable polling per 4.10.
- **`docker compose up` is slow.** Docker Desktop's WSL2 backend has a memory cap; raise it under Settings → Resources.
- **Slow git operations on `/mnt/c/...`.** Same root cause — move to the WSL native filesystem.

---

# Chapter 5 — Setting Up a Local Development Environment on Linux

Linux is the simplest path because it is the platform Forge was designed for. This chapter targets Ubuntu 22.04 LTS; the package names differ for Fedora/RHEL/Arch but the recipe is otherwise identical.

## 5.1 Install Docker Engine

Use Docker's official apt repository, not the distro package — the distro version is usually old enough to lack Compose v2:

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Add yourself to the `docker` group so you can run docker without sudo:

```bash
sudo usermod -aG docker $USER
newgrp docker          # apply in current shell, or log out and back in
docker run --rm hello-world
```

## 5.2 Install command-line dependencies

```bash
sudo apt install -y git make build-essential

sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev

curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
```

## 5.3 Clone and install

```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/f5devcentral/bnk-forge.git
cd bnk-forge
make install
```

On native Linux the Makefile uses the bare `docker-compose.yml` (host networking — no overlay). Services bind directly to host ports. The install will print the URL when ready:

```
https://localhost/
```

If you are installing on a remote Linux server rather than your local desktop, Forge will be reachable at `https://<server-ip>/` — substitute the actual hostname or IP.

## 5.4 Backend and frontend dev loops

Identical to macOS:

```bash
# Backend
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/unit/ -q

# Frontend
cd ../frontend-v2
npm install
npm run dev          # Vite at http://localhost:5173/
```

## 5.5 Server deployment notes

If this Linux box is a long-running server rather than a developer workstation, prefer:

```bash
make upgrade-safe          # preflight + strict verification
make status                # current container health
```

over `make deploy` for upgrade cycles. `upgrade-safe` will refuse to swap containers if the new build does not pass health checks.

## 5.6 Reverse proxy and TLS in front of Forge

For a real-world server install behind an external reverse proxy (Caddy, Traefik, nginx), terminate TLS at the outer proxy and forward HTTP/WebSocket traffic to Forge's nginx on the standard ports. The internal proxy (`bnk-forge-proxy`) already handles its own self-signed certificate; you can pass through with `proxy_ssl_verify off` or replace the cert via `proxy/tls/`.

---

# Chapter 6 — Creating Module Repositories

A **module repository** is a git repo containing one or more reusable deployment packs that Forge imports into its module catalog. This chapter covers the contract and conventions; chapters 8 through 11 cover engine-specific details.

## 6.1 The mental model

Think of a module repository like a package registry: each subdirectory is a published artifact with its own version, lifecycle, and inputs. Forge syncs the repository, walks every directory containing a `bnkforge.pack.json`, and registers each as a catalog entry.

The cleanest pattern is **one repo per author or vendor**, with all that author's modules underneath. You can also keep a single repo for both modules and blueprints, but separating them is recommended because:

- Modules and blueprints version on different cadences.
- The same module is often referenced by several blueprints.
- Forge tracks them through different source types.

## 6.2 Recommended repository layout

```
example-modules/
  README.md                  Top-level overview
  modules/
    foundation/
      network-core/
        bnkforge.pack.json   Manifest — required
        README.md            Optional but recommended
        main.tf
        variables.tf
        outputs.tf
        versions.tf
      identity-bootstrap/
        bnkforge.pack.json
        README.md
        scripts/
          apply.py
          destroy.py
        outputs/
          result.json
    application/
      runtime-config/
        bnkforge.pack.json
        README.md
        playbooks/
          apply.yml
          destroy.yml
        roles/
          runtime_config/
            tasks/main.yml
        inventory/
          inventory.ini
        outputs/
          result.json
```

The hard requirements:

- Each module sits in its own directory.
- Each module directory contains a `bnkforge.pack.json` at its root.
- The `module.path` field inside that manifest exactly matches the directory path that any blueprint will reference (e.g. `modules/foundation/network-core`).

The path-to-manifest match is the contract between your repo and any blueprint that calls into it. Get it wrong and the blueprint sync will succeed but the deploy will fail with "module not found in catalog".

## 6.3 The `bnkforge.pack.json` manifest

Every module manifest has the same top-level shape:

```json
{
  "schema_version": 1,
  "module": {
    "name": "string",
    "path": "string",
    "version": "string",
    "category": "infra|k8s|bnk|app|other",
    "description": "string",
    "provider": "string|null",
    "supported_platforms": ["string"],
    "tags": ["string"]
  },
  "deployment_pack": {
    "engine": "opentofu|kubernetes|ansible|script",
    "runner_profile": "string",
    "working_directory": ".",
    "entrypoints": {},
    "lifecycle": {
      "supports_init": true,
      "supports_plan": true,
      "supports_apply": true,
      "supports_destroy": false,
      "supports_refresh": true,
      "supports_drift": false
    }
  },
  "dependencies": { "required": [], "optional": [] },
  "inputs":  { "required": [], "optional": [] },
  "outputs": { "key_outputs": [] },
  "credentials": { "required": [], "optional": [] }
}
```

The required minimum is `schema_version`, `module`, `deployment_pack` (with at least `engine`, `runner_profile`, `entrypoints`, `lifecycle`), and an `inputs` block. Everything else has sensible defaults.

### 6.3.1 `module` field reference

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | Display name in the catalog. |
| `path` | yes | Must match the directory path blueprints will reference. |
| `version` | yes | Semver-ish. Immutable. Bump for any user-visible behavior change. |
| `category` | yes | One of `infra`, `k8s`, `bnk`, `app`, `other`. Module vocabulary, separate from blueprint category vocabulary. |
| `description` | yes | One-line summary. |
| `provider` | recommended | `aws`, `azure`, `gcp`, `ibm`, etc., or `null`. |
| `supported_platforms` | recommended | Platforms the module can target. |
| `tags` | recommended | Catalog search keywords. |
| `deploy_model` | k8s only | `manifests` or `helm` (chapter 10). |
| `execution_engine` | k8s only | `kubernetes-direct` or `operator`. |
| `module_source_kind` | optional | Usually `git`; `builtin` is reserved. |

### 6.3.2 `deployment_pack` field reference

`engine` controls which runtime executes the module:

- `opentofu` — Terraform-compatible IaC. Chapter 8.
- `kubernetes` — Direct apply or Helm install. Chapter 10.
- `ansible` — Playbook execution. Chapter 9.
- `script` — Executable file. Chapter 11.

`runner_profile` selects the container/sandbox the engine runs in:

- `opentofu-default`
- `kubernetes-default`
- `ansible-default`
- `script-restricted`

`entrypoints` is engine-specific. The engine-specific chapters give the required and optional keys.

`lifecycle` is required and every key must be explicitly set:

```json
"lifecycle": {
  "supports_init":    true,
  "supports_plan":    true,
  "supports_apply":   true,
  "supports_destroy": true,
  "supports_refresh": true,
  "supports_drift":   false
}
```

`supports_apply: true` is mandatory — a module that does not apply has no purpose. The other flags affect what UI controls appear and which scheduled jobs run.

### 6.3.3 `inputs` field reference

Each input declares:

- `name` — variable name as the engine sees it.
- `type` — `string`, `number`, `bool`, `list(string)`, etc.
- `description` — surfaces in the deploy dialog.
- `source` — `user`, `module`, `auto`, `credential_template`, `project`, `project_secret`.

Recommended optional fields:

- `default`
- `example`
- `sensitive` (set `true` for secrets)
- `validation` — `{ "pattern": "regex", "error_message": "..." }`

Example:

```json
{
  "name": "ibmcloud_api_key",
  "type": "string",
  "description": "IBM Cloud API key",
  "source": "user",
  "sensitive": true
}
```

### 6.3.4 `outputs.key_outputs`

A list of outputs the module advertises to consumers (other modules, the catalog UI). Each entry has `name`, `type`, and `description`. Outputs your module emits but doesn't list here are still captured into `ProjectModule.outputs` and can be referenced — `key_outputs` is the curated subset the UI surfaces.

### 6.3.5 `dependencies`

A list of other modules this module needs:

- `required` — must be present in the catalog and referenced by the blueprint. Forge fails the deploy if missing.
- `optional` — nice-to-have references; the deploy proceeds without them.

Each entry has `module` (the path) and `reason`.

## 6.4 README expectations

A module README should answer four questions:

1. **What does this module do** in one paragraph?
2. **What inputs does it need** (refer to the manifest's `inputs.required`)?
3. **What outputs does it produce** that consumers can rely on?
4. **What state does it manage** (cloud resources created, K8s objects applied, files written)?

Forge surfaces the README in the Module Detail panel, so keep it readable. Markdown headings, fenced code, and tables render correctly.

## 6.5 Importing a module repository into Forge

1. Push your repository to a git remote Forge can reach.
2. Settings → **Modules → Add Source** → enter the git URL and a sync interval.
3. Forge sweeps the repo, finds every `bnkforge.pack.json`, validates each, and registers the modules.
4. Validation errors surface as toast notifications — the module is rejected; the rest still import.
5. Re-sync to pick up new modules or updated manifests. Bumping `module.version` for content changes is mandatory; reusing a version with different content is flagged as a conflict.

## 6.6 Versioning

`module.version` is part of the catalog's identity. Bump it whenever:

- The Tofu code, playbook, manifest YAML, or script changes in a way users should see as a new release.
- An input is added, removed, renamed, or changes type.
- An output's contract changes.

Don't bump for:

- README changes.
- Comment-only code edits.
- Rewriting a stable behavior into the same observable result.

Use plain semver (`1.0.0`, not `v1.0.0`). The catalog UI is consistent about that.

---

# Chapter 7 — Creating Blueprint Repositories

A **blueprint repository** is a git repo containing one or more `forge-blueprint.json` manifests. Forge syncs the repository, walks every directory containing one, and creates a *discovered* release for each. Releases become deployable after the user clicks **Import** in the catalog.

## 7.1 Recommended layout

```
example-blueprints/
  README.md
  blueprints/
    aws-eks-foundation/
      forge-blueprint.json
      README.md
    edge-fleet-bootstrap/
      forge-blueprint.json
      README.md
    bnk-platform-on-roks/
      forge-blueprint.json
      README.md
```

Each blueprint sits in its own directory. The directory name doesn't have to match `blueprint.id` but matching helps readers.

## 7.2 The `forge-blueprint.json` manifest

```json
{
  "schema_version": 1,
  "blueprint": {
    "id": "vendor.complete-solution",
    "version": "1.0.0",
    "name": "Complete Solution",
    "description": "Deploys a complete solution using three reusable modules."
  },
  "compatibility": {
    "supported_platform_profiles": ["generic_onprem", "eks", "aks", "gke", "ocp"],
    "required_capabilities": []
  },
  "category": "app",
  "cloud_provider": null,
  "icon": "Layers",
  "color": "slate",
  "estimated_time": "15-30 minutes",
  "estimated_cost": "Varies by environment",
  "difficulty": "intermediate",
  "maturity": "reference",
  "bnk_version": "2.3",
  "tags": ["example", "third-party", "multi-module"],
  "outcomes": [
    "Foundation resources prepared",
    "Bootstrap automation executed",
    "Application services configured"
  ],
  "prerequisites": [
    {
      "type": "requirement",
      "description": "Referenced modules must already be synced into the Forge module catalog."
    },
    {
      "type": "project_secret",
      "name": "api_token",
      "description": "Credential required by one or more modules."
    }
  ],
  "input_summary": [
    { "label": "Input guidance", "value": "Provide environment-specific networking, authentication, and runtime values." }
  ],
  "platform_defaults": {},
  "variable_templates": {},
  "inputs": {
    "required": [],
    "optional": []
  },
  "modules": []
}
```

### 7.2.1 `blueprint` field

| Field | Notes |
| --- | --- |
| `id` | Stable identity. Forge tracks releases by `(blueprint_id, blueprint_version)`. Don't change after publishing. |
| `version` | Author this **without** a leading `v`. The catalog UI prepends `v` when rendering, so authoring `v1.0.0` produces `vv1.0.0`. |
| `name` | Display name. |
| `description` | One-line summary, surfaces in the deploy dialog. |

### 7.2.2 `category`

The catalog UI recognizes exactly four author-facing categories. Anything else falls back to `infrastructure` styling silently.

| `category` | Catalog group | Lifecycle stage line |
| --- | --- | --- |
| `infrastructure` | `1. Infrastructure` | Stage 1: create the target environment and cluster foundation. |
| `bnk` | `2. Platform (BNK)` | Stage 2: install the BNK platform onto an existing cluster. |
| `solution` | `3. Solutions` | Stage 3: deploy solution/application components on top of BNK. |
| `bare-metal` | `1. Bare Metal` | DPU + Bare Metal Infrastructure — deploys directly to physical hosts via SSH. |

`custom` is reserved for user-created blueprints saved from a project; do not author third-party content with that category.

### 7.2.3 `bnk_version`

Optional top-level field that surfaces in the catalog row's "BNK Version" column as a `BNK <value>` badge. Use it to advertise platform compatibility for blueprints that install or depend on a specific BNK platform release. Convention is the BNK major.minor (`"2.2"`, `"2.3"`). Omit it for pure infrastructure or bare-metal blueprints.

### 7.2.4 `cloud_provider`

Catalog filter and badge. Recognized values: `aws`, `azure`, `gcp`, `openshift`, `ibm`, `any`, `bare-metal`. Use `any` for multi-platform blueprints.

### 7.2.5 `prerequisites`

Describes what the project must have before this blueprint can deploy. Forge surfaces the list in the deploy dialog and may gate the *Deploy* button on it.

```json
{
  "type": "kubernetes_cluster",
  "description": "Target project must have at least one registered Kubernetes cluster."
}
{
  "type": "credential_template",
  "name": "ibmcloud_api_key",
  "description": "Provide an IBM Cloud credential template that can supply the IBM Cloud API key."
}
{
  "type": "project_secret",
  "name": "jwt_token",
  "description": "F5 license JWT token from MyF5."
}
{
  "type": "requirement",
  "description": "Free-form note shown to the user."
}
```

`project_secret` prerequisites are the most operationally important: Forge gates deploys on missing project secrets, and the variable assembler injects matching project secrets into module variables by name automatically.

### 7.2.6 `platform_defaults`

A per-platform-profile map of variables the variable assembler injects when a deploy targets that platform:

```json
"platform_defaults": {
  "eks": {
    "container_platform": "AWS",
    "storage_class_name": "gp3",
    "cni_type": "host-device"
  },
  "roks": {
    "container_platform": "Generic",
    "storage_class_name": "ibmc-vpc-block-10iops-tier",
    "cni_type": "ipvlan"
  }
}
```

The platform profile key is the cluster service code (`eks`, `aks`, `gke`, `roks`), not the cloud provider code. This is the right place to put cluster-shape defaults so blueprint authors don't bloat the deploy form with namespace and CNI fields.

### 7.2.7 `inputs` and `modules`

Top-level `inputs` are what the user fills in on the Deploy dialog. Each entry has the same shape as a module input plus an `order` field that controls form ordering.

`modules` is the orchestration list — each entry references a module from the catalog and supplies its inputs. Chapter 12 covers variable wiring exhaustively.

## 7.3 README expectations

A blueprint README should describe:

1. **What outcome the blueprint produces** (matches but expands the manifest's `outcomes` list).
2. **Lifecycle stage** — Stage 1, 2, 3, or hybrid. Refer to the matching stage in chapter 7's category table.
3. **Prerequisites** — required modules, project secrets, credential templates, target cluster type.
4. **What to do after apply** — does the user need to run any post-step?

Keep it concise; Forge surfaces the README on the catalog detail page.

## 7.4 Importing a blueprint repository into Forge

1. Settings → **Blueprint Sources → Add Source** → git URL + sync interval.
2. Forge syncs every `forge-blueprint.json` and creates a `discovered` release per `(blueprint_id, blueprint_version)` pair.
3. Click **Import** on a release to make it deployable.
4. Re-sync at any time to pick up updates. Re-syncing with the same `blueprint.version` but different content gets flagged as a conflict — bump the version or delete the existing release.

## 7.5 Versioning

Bump `blueprint.version` whenever the orchestration changes — new modules, dependency reordering, input shape changes. A blueprint version bump does not require a module bump unless the underlying module also changed.

Author versions without a leading `v`. The catalog renders the badge as `v{version}`, so `1.0.0` becomes `v1.0.0` in the UI.

---

# Chapter 8 — Developing and Using OpenTofu / Terraform Modules

This is the most mature engine in BNK Forge and the right default when the work is "manage cloud or provider resources with declarative state".

## 8.1 When to use the OpenTofu engine

Pick OpenTofu when your module manages cloud or provider resources where you want declarative state, plan/apply lifecycle, and drift detection. The engine supports the full lifecycle (`init`, `plan`, `apply`, `destroy`, `refresh`, `drift`).

Strong fits:

- Cloud foundations (VPCs, subnets, IAM, KMS keys)
- Managed Kubernetes clusters (EKS, AKS, GKE, ROKS)
- Cloud object storage, DNS, load balancers
- Anything else with a Terraform/OpenTofu provider

## 8.2 File layout

```
modules/aws-vpc/
  bnkforge.pack.json
  README.md
  main.tf
  variables.tf
  outputs.tf
  versions.tf
```

If you prefer to keep `*.tf` files in a subdirectory, set `entrypoints.module_root` to that path (e.g. `"tofu"`). The default `working_directory: "."` plus `entrypoints.module_root: "."` puts everything at the pack root.

## 8.3 Required engine configuration

```json
"deployment_pack": {
  "engine": "opentofu",
  "runner_profile": "opentofu-default",
  "working_directory": ".",
  "entrypoints": { "module_root": "." },
  "lifecycle": {
    "supports_init":    true, "supports_plan":    true, "supports_apply":   true,
    "supports_destroy": true, "supports_refresh": true, "supports_drift":   true
  }
}
```

## 8.4 Hands-on: `modules/aws-vpc`

`main.tf`:

```hcl
resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.vpc_name }
}

resource "aws_subnet" "private" {
  for_each          = toset(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.this.id
  cidr_block        = each.value
  availability_zone = data.aws_availability_zones.available.names[index(var.private_subnet_cidrs, each.value)]
  tags              = { Name = "${var.vpc_name}-private-${each.value}" }
}

data "aws_availability_zones" "available" { state = "available" }
```

`variables.tf`:

```hcl
variable "aws_region"           { type = string }
variable "vpc_name"             { type = string }
variable "vpc_cidr"             { type = string  default = "10.0.0.0/16" }
variable "private_subnet_cidrs" { type = list(string) default = ["10.0.1.0/24", "10.0.2.0/24"] }
```

`outputs.tf`:

```hcl
output "vpc_id"             { value = aws_vpc.this.id }
output "private_subnet_ids" { value = [for s in aws_subnet.private : s.id] }
output "vpc_cidr_block"     { value = aws_vpc.this.cidr_block }
```

`versions.tf`:

```hcl
terraform {
  required_version = ">= 1.7.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}
```

`bnkforge.pack.json`:

```json
{
  "schema_version": 1,
  "module": {
    "name": "AWS VPC",
    "path": "modules/aws-vpc",
    "version": "1.0.0",
    "category": "infra",
    "description": "Creates a VPC with private subnets across the available AZs.",
    "provider": "aws",
    "supported_platforms": ["eks"],
    "tags": ["aws", "vpc", "network"]
  },
  "deployment_pack": {
    "engine": "opentofu",
    "runner_profile": "opentofu-default",
    "working_directory": ".",
    "entrypoints": { "module_root": "." },
    "lifecycle": {
      "supports_init":    true, "supports_plan":    true, "supports_apply":   true,
      "supports_destroy": true, "supports_refresh": true, "supports_drift":   true
    }
  },
  "dependencies": { "required": [], "optional": [] },
  "inputs": {
    "required": [
      { "name": "aws_region", "type": "string", "source": "user" },
      { "name": "vpc_name",   "type": "string", "source": "user" }
    ],
    "optional": [
      { "name": "vpc_cidr",             "type": "string",       "default": "10.0.0.0/16", "source": "user" },
      { "name": "private_subnet_cidrs", "type": "list(string)", "default": ["10.0.1.0/24", "10.0.2.0/24"], "source": "user" }
    ]
  },
  "outputs": {
    "key_outputs": [
      { "name": "vpc_id",             "type": "string" },
      { "name": "private_subnet_ids", "type": "list(string)" },
      { "name": "vpc_cidr_block",     "type": "string" }
    ]
  }
}
```

## 8.5 Hands-on: a downstream module that consumes the VPC

`modules/aws-eks-cluster/bnkforge.pack.json` (excerpt — note the `dependencies` block):

```json
{
  "module": {
    "name": "AWS EKS Cluster",
    "path": "modules/aws-eks-cluster",
    "version": "1.0.0",
    "category": "k8s",
    "provider": "aws",
    "supported_platforms": ["eks"],
    "tags": ["aws", "eks", "kubernetes"]
  },
  "deployment_pack": {
    "engine": "opentofu",
    "runner_profile": "opentofu-default",
    "working_directory": ".",
    "entrypoints": { "module_root": "." },
    "lifecycle": {
      "supports_init":    true, "supports_plan":    true, "supports_apply":   true,
      "supports_destroy": true, "supports_refresh": true, "supports_drift":   true
    }
  },
  "dependencies": {
    "required": [
      { "module": "modules/aws-vpc", "reason": "EKS needs VPC + private subnets." }
    ],
    "optional": []
  },
  "inputs": {
    "required": [
      { "name": "aws_region",         "type": "string",       "source": "user" },
      { "name": "cluster_name",       "type": "string",       "source": "user" },
      { "name": "vpc_id",             "type": "string",       "source": "module" },
      { "name": "private_subnet_ids", "type": "list(string)", "source": "module" }
    ],
    "optional": [
      { "name": "kubernetes_version", "type": "string", "default": "1.30", "source": "user" }
    ]
  },
  "outputs": {
    "key_outputs": [
      { "name": "cluster_name",                       "type": "string" },
      { "name": "cluster_endpoint",                   "type": "string" },
      { "name": "cluster_certificate_authority_data", "type": "string" }
    ]
  }
}
```

`source: "module"` flags an input as wired from another module's output. The blueprint manifest specifies which module supplies it (chapter 12).

## 8.6 How variables flow into Tofu

For each module run, Forge:

1. Resolves every `${...}` reference in the blueprint's `module.inputs` to a flat `{name: value}` map.
2. Writes a `terraform.tfvars.json` file into the module workspace.
3. Calls `tofu init`, then `tofu plan`, then `tofu apply` with the workspace as `cwd` and credential env vars (e.g. `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) injected from the project's credential template.

Your `variables.tf` declarations are the contract. Forge will only write keys that match `inputs[*].name` in `bnkforge.pack.json`, so the declared input set must mirror your `variable "..." {}` blocks.

## 8.7 How outputs flow back

After `tofu apply` succeeds, Forge runs `tofu output -json`, normalizes the result, and stores it on `ProjectModule.outputs`. Anything in `outputs.tf` is captured automatically. The `outputs.key_outputs` list in your manifest is metadata that controls UI presentation — it does not gate what gets captured.

The blueprint's `${modules.<id>.outputs.<name>}` references read from the same JSON snapshot before the next module runs.

## 8.8 Drift detection

`supports_drift: true` opts the module into Forge's drift detection, which calls `terraform plan -refresh-only` against the project state on a schedule. If your module has providers that produce noisy non-deterministic plans, set `supports_drift: false` until you can stabilize them.

## 8.9 Sensitive outputs

Tofu's `sensitive = true` is honored — Forge stores the value but the UI redacts it.

## 8.10 Provider compatibility — Helm v2 vs v3

The Helm provider's `kubernetes` argument syntax changed in v3:

```hcl
# v2 — block syntax
provider "helm" {
  kubernetes {
    host                   = ...
    token                  = ...
    cluster_ca_certificate = ...
  }
}

# v3+ — argument with equals
provider "helm" {
  kubernetes = {
    host                   = ...
    token                  = ...
    cluster_ca_certificate = ...
  }
}
```

If your `versions.tf` pins Helm to v3+, you must use the argument form. Tofu rejects the block form at plan time with `Error: Unsupported block type`.

## 8.11 Local validation

```bash
cd modules/aws-vpc
tofu fmt -check
tofu validate
python3 -c "import json; json.load(open('bnkforge.pack.json'))"
```

## 8.12 Packaging and publishing

1. Commit your module directory and its `bnkforge.pack.json`.
2. Push to a git remote (GitHub, GitLab, internal hosting).
3. Tag releases (`v1.0.0`, `v1.0.1`, ...) so consumers can pin against tags if they prefer.
4. Update `module.version` for any user-visible change. Do not reuse a version with different content — Forge flags this as a sync conflict.
5. Optional: add a CI workflow that runs `tofu fmt -check`, `tofu validate`, and a JSON-schema check on `bnkforge.pack.json` before merge.

## 8.13 Tips and gotchas

- **`category` on the module manifest** uses module vocabulary (`infra | k8s | bnk | app | other`). Blueprints use a different vocabulary (`infrastructure | bnk | solution | bare-metal | custom`). Don't mix them up.
- **`response_model` silently drops fields** is a Forge backend gotcha that occasionally leaks into manifest design: Pydantic strips anything not declared in the response schema.
- **K8s API returns `null`, not missing keys** — when your Tofu code parses K8s responses, prefer `(spec.get("ports") or [])` over truthy chaining.
- **OpenAPI + generated TS types stay in sync** automatically when you don't touch routes; you only need `make openapi-types` after a route or schema change.

---

# Chapter 9 — Developing and Using Ansible Playbooks and Roles

Ansible is the right default when "configure servers or VMs over SSH" is the work, or when the cloud you target lacks a Terraform provider but has a usable Ansible collection.

## 9.1 When to use the Ansible engine

- Configure servers or VMs over SSH (no cloud provider lifecycle needed).
- Run idempotent steps that already have well-tested community modules.
- Bridge to a system that has no Terraform provider but does have an Ansible collection.
- Run multi-host orchestration that benefits from inventory and groups.

The engine supports `init`, `plan`, `apply`, `destroy`, and `refresh`. Drift detection is not currently supported (`supports_drift: false`).

## 9.2 File layout

```
modules/base-os-hardening/
  bnkforge.pack.json
  README.md
  playbooks/
    apply.yml
    destroy.yml
  roles/
    base_os_hardening/
      tasks/main.yml
      handlers/main.yml
      defaults/main.yml
  inventory/
    inventory.ini
  outputs/
    result.json     # written by the playbook at the end of apply
```

Each role lives inside its own deployment pack so its lifecycle, inputs, and outputs can be tracked independently. Authoring large multi-role playbooks as a single module is supported but discouraged — you lose granular drift tracking and dependency wiring.

## 9.3 Required engine configuration

- `engine: "ansible"`
- `runner_profile: "ansible-default"`
- `entrypoints.playbook` — required.
- `entrypoints.destroy_playbook` — optional but recommended.
- `entrypoints.inventory_source` — optional.
- `entrypoints.outputs_file` — required if you declare any `outputs.key_outputs`.

## 9.4 Hands-on: `modules/base-os-hardening`

`playbooks/apply.yml`:

```yaml
- name: Base OS hardening
  hosts: all
  become: true
  gather_facts: true
  roles:
    - role: base_os_hardening
  post_tasks:
    - name: Emit module outputs
      copy:
        dest: "{{ playbook_dir }}/../outputs/result.json"
        content: |
          {
            "hardening_applied": true,
            "kernel_version": "{{ ansible_kernel }}",
            "selinux_state": "{{ ansible_selinux.status | default('disabled') }}"
          }
      delegate_to: localhost
      become: false
      run_once: true
```

`playbooks/destroy.yml`:

```yaml
- name: Roll back base OS hardening
  hosts: all
  become: true
  roles:
    - role: base_os_hardening
      vars:
        hardening_state: "absent"
```

`roles/base_os_hardening/tasks/main.yml` (excerpt):

```yaml
- name: Ensure unattended upgrades package is installed
  ansible.builtin.package:
    name: unattended-upgrades
    state: "{{ hardening_state | default('present') }}"

- name: Configure SSH daemon hardening
  ansible.builtin.lineinfile:
    path: /etc/ssh/sshd_config
    regexp: '^#?PasswordAuthentication'
    line: 'PasswordAuthentication no'
  notify: Restart sshd
```

`bnkforge.pack.json`:

```json
{
  "schema_version": 1,
  "module": {
    "name": "Base OS Hardening",
    "path": "modules/base-os-hardening",
    "version": "1.0.0",
    "category": "infra",
    "description": "Applies a baseline OS hardening role: package patches, SSH config, sysctl tweaks.",
    "provider": null,
    "supported_platforms": ["generic_onprem"],
    "tags": ["ansible", "hardening", "ssh"]
  },
  "deployment_pack": {
    "engine": "ansible",
    "runner_profile": "ansible-default",
    "working_directory": ".",
    "entrypoints": {
      "playbook":         "playbooks/apply.yml",
      "destroy_playbook": "playbooks/destroy.yml",
      "inventory_source": "inventory/inventory.ini",
      "outputs_file":     "outputs/result.json"
    },
    "lifecycle": {
      "supports_init":    true, "supports_plan":    true, "supports_apply":   true,
      "supports_destroy": true, "supports_refresh": true, "supports_drift":   false
    }
  },
  "inputs": {
    "required": [],
    "optional": [
      { "name": "ssh_user", "type": "string", "default": "ubuntu", "source": "user" }
    ]
  },
  "outputs": {
    "key_outputs": [
      { "name": "hardening_applied", "type": "bool" },
      { "name": "kernel_version",    "type": "string" }
    ]
  }
}
```

## 9.5 How variables flow into Ansible

For each module run, Forge:

1. Resolves every `${...}` reference in the blueprint's `module.inputs`.
2. Writes a YAML extra-vars file into a temp location.
3. Calls `ansible-playbook <playbook> --extra-vars @<file> [-i <inventory>]` with the module's working directory as `cwd` and credential env vars (e.g. `ANSIBLE_PRIVATE_KEY_FILE`) injected from the project's credential template.

Inside the playbook, refer to the values as ordinary Ansible variables (`{{ edge_hostname }}`, `{{ ssh_user }}`). They behave like any extra-vars: they take precedence over role and group defaults.

## 9.6 Writing the outputs file

The cleanest idiom is a `post_tasks` block at the end of `apply.yml`, with `delegate_to: localhost` and `run_once: true` so the file lands in the workspace, not on a remote host. The output contract:

- The file must be valid JSON.
- It must be written by the playbook (Forge does not generate it).
- Every `outputs.key_outputs` entry should be present in the JSON; missing keys cause a validation warning.
- Keys you don't advertise in `key_outputs` are still captured into `ProjectModule.outputs`.

## 9.7 Plan and dry-run

`supports_plan: true` is allowed. Forge runs `--check` mode for plan, exercising the playbook in dry-run.

## 9.8 Idempotency

Forge will retry runs and run drift refresh — playbooks that aren't idempotent will surface as flapping state in the UI. Use `state:` parameters and conditionals to stay idempotent.

## 9.9 Inventory

`inventory_source` is a relative path from the pack root. If you do not declare one, Forge will run with no inventory — fine for `localhost` plays, but most edge/server packs need it.

## 9.10 Sensitive variables

Declare them with `sensitive: true` in `bnkforge.pack.json`. Forge writes them into the extra-vars file with file permissions tightened, but you should still be careful not to log them via `debug:` tasks.

## 9.11 Local validation

```bash
ansible-playbook --syntax-check modules/base-os-hardening/playbooks/apply.yml
ansible-lint modules/base-os-hardening/

# Dry-run against a real host
ansible-playbook \
  -i modules/base-os-hardening/inventory/inventory.ini \
  --extra-vars @vars.yml \
  --check \
  modules/base-os-hardening/playbooks/apply.yml
```

## 9.12 Packaging and publishing

The same workflow as Tofu modules: commit, push, tag, bump `module.version` for user-visible changes. Add CI for `ansible-lint` and the JSON-schema check on `bnkforge.pack.json` if you publish to multiple consumers.

## 9.13 Python on managed nodes

Ansible needs Python on every managed node. Use `ansible_python_interpreter` in inventory or extra-vars when targets ship with non-default Python paths.

---

# Chapter 10 — Developing Kubernetes Modules and Using Helm Charts

The Kubernetes engine is purpose-built for "land objects on a cluster". It's dramatically faster than Tofu for stamp-out work because there's no Terraform state file and no plan/apply split — apply via [`kr8s`](https://docs.kr8s.org/) (manifests) or `helm upgrade --install` (charts), wait for readiness, collect outputs.

## 10.1 When to use the Kubernetes engine

- The work is "apply YAML or install a chart" against an existing cluster.
- You want server-side apply with automatic readiness waits.
- The cluster might have a connected `bnk-operator`, in which case execution can run *inside* the cluster (no kubeconfig egress).

Pick OpenTofu instead if you genuinely need state-backed lifecycle management of arbitrary cloud + cluster resources together.

## 10.2 Two deploy models

The Kubernetes engine supports two `deploy_model` values:

| `deploy_model` | What you ship | Apply mechanism |
| --- | --- | --- |
| `manifests` | a directory of `.yaml` (with optional Jinja2 templating) | `kr8s` server-side apply, then ready-wait |
| `helm` | a chart reference (`oci://...` / `repo/chart`) **or** a chart directory in the pack, plus `values.yaml` | `helm upgrade --install`, then ready-wait |

You declare the deploy model via `module.deploy_model`.

## 10.3 File layouts

Manifests:

```
modules/bnk-namespaces/
  bnkforge.pack.json
  README.md
  manifests/
    00-namespace-bnk.yaml
    10-namespace-utils.yaml
```

Helm:

```
modules/cert-manager-edge/
  bnkforge.pack.json
  README.md
  values.yaml.j2
  # No chart shipped here — chart_ref points at the public Jetstack repo
```

## 10.4 Required engine configuration

- `engine: "kubernetes"`
- `runner_profile: "kubernetes-default"`
- `module.deploy_model: "manifests"` or `"helm"`
- `module.execution_engine: "kubernetes-direct"` or `"operator"` (lets the engine router pick K8s instead of falling back to OpenTofu).

For `manifests`:

- `entrypoints.manifest_path` — path to a directory of `*.yaml`/`*.yml` (relative to the pack root).
- `entrypoints.template_engine: "jinja2"` — opt into file-text rendering before YAML parsing.

For `helm`:

- `entrypoints.chart_ref` — Helm repo URL, OCI ref, or `repo/chart`.
- `entrypoints.chart_path` — alternative: directory inside the pack containing the chart.
- `entrypoints.values_path` — file in the pack (commonly `values.yaml.j2`).
- `entrypoints.release_name`, `entrypoints.namespace`, `entrypoints.chart_version` — accept Jinja2 expressions.
- `entrypoints.create_namespace: true` — Helm's native flag.

## 10.5 Hands-on (manifests): `modules/bnk-namespaces`

`manifests/00-namespace-bnk.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: {{ flo_namespace | default("f5-bnk") }}
  labels:
    bnk.f5.com/managed-by: bnk-forge
    bnk.f5.com/component: platform
```

`manifests/10-namespace-utils.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: {{ flo_utils_namespace | default("f5-utils") }}
  labels:
    bnk.f5.com/managed-by: bnk-forge
    bnk.f5.com/component: utils
```

`bnkforge.pack.json`:

```json
{
  "schema_version": 1,
  "module": {
    "name": "BNK Namespaces",
    "path": "modules/bnk-namespaces",
    "version": "1.0.0",
    "category": "k8s",
    "description": "Creates the platform and utils namespaces every BNK install expects.",
    "provider": null,
    "supported_platforms": ["eks", "aks", "gke", "ocp", "ibm_roks"],
    "tags": ["k8s", "bnk", "namespace"],
    "deploy_model": "manifests",
    "execution_engine": "kubernetes-direct",
    "module_source_kind": "git"
  },
  "deployment_pack": {
    "engine": "kubernetes",
    "runner_profile": "kubernetes-default",
    "working_directory": ".",
    "entrypoints": {
      "manifest_path":   "manifests",
      "template_engine": "jinja2"
    },
    "lifecycle": {
      "supports_init":    true, "supports_plan":    true, "supports_apply":   true,
      "supports_destroy": true, "supports_refresh": false, "supports_drift":   true
    }
  },
  "inputs": {
    "required": [],
    "optional": [
      { "name": "flo_namespace",       "type": "string", "default": "f5-bnk",   "source": "user" },
      { "name": "flo_utils_namespace", "type": "string", "default": "f5-utils", "source": "user" }
    ]
  },
  "outputs": {
    "key_outputs": [
      { "name": "platform_namespace", "type": "string" },
      { "name": "utils_namespace",    "type": "string" }
    ]
  }
}
```

## 10.6 Hands-on (Helm): `modules/cert-manager-edge`

`values.yaml.j2`:

```yaml
installCRDs: true

resources:
  requests:
    cpu:    {{ cert_manager_cpu_request | default("100m") }}
    memory: {{ cert_manager_mem_request | default("256Mi") }}

prometheus:
  enabled: false

webhook:
  timeoutSeconds: 30

global:
  leaderElection:
    namespace: {{ flo_namespace }}
```

`bnkforge.pack.json`:

```json
{
  "schema_version": 1,
  "module": {
    "name": "cert-manager (edge)",
    "path": "modules/cert-manager-edge",
    "version": "1.0.0",
    "category": "k8s",
    "description": "Installs cert-manager from the Jetstack chart and registers its CRDs.",
    "provider": null,
    "supported_platforms": ["eks", "aks", "gke", "ocp", "ibm_roks"],
    "tags": ["k8s", "cert-manager", "tls"],
    "deploy_model": "helm",
    "execution_engine": "kubernetes-direct",
    "module_source_kind": "git"
  },
  "deployment_pack": {
    "engine": "kubernetes",
    "runner_profile": "kubernetes-default",
    "working_directory": ".",
    "entrypoints": {
      "chart_ref":        "https://charts.jetstack.io/cert-manager",
      "chart_version":    "{{ cert_manager_version | default('v1.17.3') }}",
      "values_path":      "values.yaml.j2",
      "release_name":     "cert-manager",
      "namespace":        "{{ cert_manager_namespace | default('cert-manager') }}",
      "create_namespace": true
    },
    "lifecycle": {
      "supports_init":    true, "supports_plan":    true, "supports_apply":   true,
      "supports_destroy": true, "supports_refresh": false, "supports_drift":   true
    },
    "timeout_seconds": 600
  },
  "dependencies": {
    "required": [
      { "module": "modules/bnk-namespaces", "reason": "cert-manager runs alongside the BNK platform namespaces." }
    ],
    "optional": []
  },
  "inputs": {
    "required": [
      { "name": "flo_namespace", "type": "string", "source": "user" }
    ],
    "optional": [
      { "name": "cert_manager_namespace",  "type": "string", "default": "cert-manager", "source": "user" },
      { "name": "cert_manager_version",    "type": "string", "default": "v1.17.3",      "source": "user" },
      { "name": "cert_manager_cpu_request","type": "string", "default": "100m",          "source": "user" },
      { "name": "cert_manager_mem_request","type": "string", "default": "256Mi",         "source": "user" }
    ]
  },
  "outputs": {
    "key_outputs": [
      { "name": "cert_manager_release",   "type": "string" },
      { "name": "cert_manager_crd_ready", "type": "bool" }
    ]
  }
}
```

## 10.7 Manifest templating

When `entrypoints.template_engine: "jinja2"` is set, every `*.yaml` / `*.yml` file under `manifest_path` is rendered as a Jinja2 template before YAML parsing. Reference inputs as `{{ flo_namespace }}` and use `default("...")` for optional values.

Forge's Jinja2 environment uses `StrictUndefined` — referencing a variable that isn't supplied is a hard error at render time. Use `default("...")` filters for optional values.

## 10.8 Helm values templating

`values_path` is also rendered by Jinja2. Name the file `values.yaml.j2` for clarity.

## 10.9 Drift detection

For `manifests` packs, drift compares rendered manifests to live state. For `helm` packs, drift checks the release status and the chart version. Pin chart versions explicitly so drift is meaningful.

## 10.10 Server-side apply quirks

`kr8s`'s server-side apply expects fully-formed manifests. Don't ship templates that produce empty documents — Forge will error.

When a module ships CRDs, the next module that depends on those CRDs must wait for them. Either declare `depends_on` on the consuming module and ensure the producing module sets `supports_apply` to wait for `Established`, or surface a `*_crd_ready` boolean output and reference it from the consumer.

## 10.11 Helm idempotency

Forge uses `helm upgrade --install` so re-runs are idempotent. If your values file has a non-deterministic field (random password, timestamp), Helm sees a diff every time — set those fields once and persist them outside the values file.

## 10.12 Local validation

```bash
# JSON sanity
python3 -c "import json; json.load(open('modules/bnk-namespaces/bnkforge.pack.json'))"

# Render Jinja2 manifests locally to catch template errors
python3 - <<'EOF'
from pathlib import Path
import jinja2
env = jinja2.Environment(undefined=jinja2.StrictUndefined)
vars = {"flo_namespace": "f5-bnk", "flo_utils_namespace": "f5-utils"}
for p in Path("modules/bnk-namespaces/manifests").glob("*.yaml"):
    print("---", p, "---")
    print(env.from_string(p.read_text()).render(**vars))
EOF

# Server-side dry-run on a real cluster
kubectl --kubeconfig=$KUBECONFIG apply --dry-run=server -f /tmp/rendered/

# Helm: lint the chart with rendered values
helm template cert-manager https://charts.jetstack.io/cert-manager \
  --version v1.17.3 \
  -f modules/cert-manager-edge/values.yaml.j2.rendered.yaml \
  | kubectl --kubeconfig=$KUBECONFIG apply --dry-run=server -f -
```

## 10.13 Packaging and publishing

Same workflow as the other engines: commit, push, tag, bump `module.version` for user-visible changes. Pin chart versions explicitly in your manifest's `chart_version` entrypoint so users get deterministic behavior.

---

# Chapter 11 — Writing Script Modules for BNK Forge

The script engine is the right choice when the work is imperative (discovery, registration, one-shot data shape change) and doesn't fit declarative IaC.

## 11.1 When to use the script engine

- You have an imperative procedure that doesn't fit declarative IaC (discovery, registration, one-shot data import).
- You want fine-grained control over flow without dragging in a config-management runtime.
- The same Python you would write as a stand-alone CLI script is the right shape for the work.

If the work has long-lived desired state (resources to create, drift, destroy), prefer the OpenTofu engine instead.

> **Runtime status.** The script engine is part of the documented authoring contract and is recognized by the module-metadata layer (`engine: "script"`, `runner_profile: "script-restricted"`). The execution router currently routes script-engine modules through the OpenTofu engine path by default; end-to-end script execution is not yet wired in the runtime. Author your packs to the contract described below — they will validate and import cleanly — but expect to work with the Forge team to land the script runner before they execute. If you need a fully-runnable Python module today, wrap the Python entrypoint in an Ansible playbook (chapter 9), or drive the Python work from an OpenTofu `null_resource` `local-exec` provisioner (chapter 8).

## 11.2 File layout

```
modules/cos-instance-discover/
  bnkforge.pack.json
  README.md
  scripts/
    apply.py
    destroy.py
  outputs/
    result.json    # written by apply.py
```

Make `scripts/apply.py` and `scripts/destroy.py` executable and add a shebang. The runner runs them as their own process; the file does not need to be a shell script — it just needs to be executable.

```bash
chmod +x modules/cos-instance-discover/scripts/apply.py
chmod +x modules/cos-instance-discover/scripts/destroy.py
```

## 11.3 Required engine configuration

- `engine: "script"`
- `runner_profile: "script-restricted"`
- `entrypoints.apply_script` — required.
- `entrypoints.destroy_script` — optional but recommended.
- `entrypoints.outputs_file` — required if you declare any `outputs.key_outputs`.

## 11.4 Hands-on: `modules/cos-instance-discover`

`scripts/apply.py`:

```python
#!/usr/bin/env python3
"""Resolve an existing IBM COS instance and emit BNK-friendly outputs."""
from __future__ import annotations

import json
import os
import sys


def load_inputs() -> dict:
    path = os.environ.get("FORGE_INPUTS_FILE")
    if not path:
        sys.exit("FORGE_INPUTS_FILE not set; refusing to run.")
    with open(path) as f:
        return json.load(f)


def write_outputs(outputs: dict) -> None:
    path = os.environ.get("FORGE_OUTPUTS_FILE")
    if not path:
        sys.exit("FORGE_OUTPUTS_FILE not set; refusing to write outputs.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(outputs, f, indent=2)


def main() -> int:
    inputs = load_inputs()
    crn = lookup_cos_crn(
        inputs["ibmcloud_api_key"],
        inputs.get("ibmcloud_resource_group", "default"),
        inputs["ibmcloud_cos_instance_name"],
    )
    guid = crn.rsplit(":", 1)[-1]
    write_outputs({"cos_instance_crn": crn, "cos_instance_guid": guid})
    return 0


def lookup_cos_crn(api_key: str, rg: str, name: str) -> str:
    raise NotImplementedError("Replace with real IBM Cloud lookup.")


if __name__ == "__main__":
    sys.exit(main())
```

`scripts/destroy.py`:

```python
#!/usr/bin/env python3
"""Discovery module — destroy is a no-op (no resources were created)."""
import sys
sys.exit(0)
```

`bnkforge.pack.json`:

```json
{
  "schema_version": 1,
  "module": {
    "name": "IBM COS Instance Discover",
    "path": "modules/cos-instance-discover",
    "version": "1.0.0",
    "category": "other",
    "description": "Resolves an existing IBM COS instance by name and emits its CRN/GUID.",
    "provider": "ibm",
    "supported_platforms": ["ibm_roks"],
    "tags": ["ibm", "cos", "discovery"]
  },
  "deployment_pack": {
    "engine": "script",
    "runner_profile": "script-restricted",
    "working_directory": ".",
    "entrypoints": {
      "apply_script":   "scripts/apply.py",
      "destroy_script": "scripts/destroy.py",
      "outputs_file":   "outputs/result.json"
    },
    "lifecycle": {
      "supports_init":    true, "supports_plan":    false, "supports_apply":   true,
      "supports_destroy": true, "supports_refresh": false, "supports_drift":   false
    }
  },
  "inputs": {
    "required": [
      { "name": "ibmcloud_api_key",            "type": "string", "source": "user", "sensitive": true },
      { "name": "ibmcloud_cos_instance_name",  "type": "string", "source": "user" }
    ],
    "optional": [
      { "name": "ibmcloud_resource_group", "type": "string", "default": "default", "source": "user" }
    ]
  },
  "outputs": {
    "key_outputs": [
      { "name": "cos_instance_crn",  "type": "string" },
      { "name": "cos_instance_guid", "type": "string" }
    ]
  }
}
```

`supports_plan: false` is correct for most script modules — there is no plan/apply split.

## 11.5 Input/output contract

When the runtime lands, Forge will:

1. Resolve every `${...}` reference in the blueprint's `module.inputs`, producing a flat `{name: value}` map.
2. Serialize the map as JSON to a temporary file. The path is exposed via `FORGE_INPUTS_FILE`.
3. Invoke `entrypoints.apply_script` (or `destroy_script`) as its own process, with:
   - the module's working directory as `cwd`,
   - credential env vars from the project's credential template (e.g. `IBMCLOUD_API_KEY`),
   - `FORGE_INPUTS_FILE` and `FORGE_OUTPUTS_FILE` set to absolute paths.
4. Treat exit code `0` as success. Any non-zero exit fails the module.

Authoring guidance:

- Read inputs from `FORGE_INPUTS_FILE` rather than from `os.environ`. Inputs may contain nested structures and should not be flattened into env vars.
- Treat env vars as the channel for credentials only.
- Do not write to `stdout` for outputs — use `FORGE_OUTPUTS_FILE`. `stdout` and `stderr` are streamed into the task log for the user to see.

## 11.6 Idempotency and process model

Scripts run as their own short-lived process. Forge will retry runs — make `apply.py` safe to re-run, and do not rely on shared state between apply runs other than what you persist via `FORGE_OUTPUTS_FILE`.

Logs: anything you `print()` shows up in the task output stream. Don't print secrets.

## 11.7 Local validation

```bash
# JSON sanity
python3 -c "import json; json.load(open('modules/cos-instance-discover/bnkforge.pack.json'))"

# Standalone script run (simulating Forge's environment)
mkdir -p /tmp/forge-test/{inputs,outputs}
cat > /tmp/forge-test/inputs/vars.json <<EOF
{ "ibmcloud_api_key": "***", "ibmcloud_cos_instance_name": "bnk-orchestration", "ibmcloud_resource_group": "default" }
EOF
FORGE_INPUTS_FILE=/tmp/forge-test/inputs/vars.json \
FORGE_OUTPUTS_FILE=/tmp/forge-test/outputs/result.json \
  ./modules/cos-instance-discover/scripts/apply.py
cat /tmp/forge-test/outputs/result.json
```

This is the same I/O contract Forge will use — if your script works under it locally, importing into Forge should be uneventful once the runtime lands.

## 11.8 Packaging and publishing

Same workflow as the other engines: commit, push, tag, bump `module.version` for user-visible changes. Verify the script's shebang, executable bit, and dependencies work in a clean container before publishing.

---

# Chapter 12 — Authoring Blueprints with Modules

A blueprint is the orchestration definition that takes one or more modules and turns them into a deployable solution. This chapter covers the authoring contract end-to-end, with a complete worked example.

## 12.1 The big picture

When a user clicks **Deploy** on your blueprint, Forge:

1. Creates (or uses) a project, attaches the chosen credential template, and resolves the user's inputs.
2. Converts the blueprint's `modules` array into a dependency graph driven by `depends_on`. Independent modules can run concurrently.
3. For each module, in dependency order:
   - Resolves every `${...}` reference in its `inputs` block.
   - Selects the engine (using the manifest's `deploy_model` / `execution_engine` plus runtime connectivity).
   - Hands the module to the engine for execution.
   - Captures outputs and persists them on `ProjectModule.outputs` for downstream modules to reference.

Your blueprint manifest is the user's API. Make it small, opinionated, and consistent with the other blueprints in the catalog.

## 12.2 Manifest top-level fields recap

Already covered in chapter 7. Quick recap of the fields that matter for orchestration:

| Field | Why it matters |
| --- | --- |
| `category` | Catalog group + lifecycle stage line |
| `cloud_provider` | Catalog filter and badge |
| `bnk_version` | Catalog `BNK <value>` badge |
| `prerequisites` | Gate the deploy on cluster, credential template, project secrets |
| `platform_defaults` | Per-platform variable injection at apply time |
| `inputs` | What the user fills in on the deploy form |
| `modules` | Orchestration list with explicit dependencies and inputs |

## 12.3 Variable wiring

Three patterns to internalize.

### 12.3.1 Top-level input → module input

The user fills in a top-level input (e.g. `aws_region`); the blueprint references it in a module's inputs:

```json
"inputs": {
  "aws_region": "${aws_region}"
}
```

### 12.3.2 Module output → next module's input

Module A emits an output (`vpc_id`); module B reads it. The blueprint wires it:

```json
{
  "id": "eks",
  "depends_on": ["vpc"],
  "inputs": {
    "vpc_id":             "${modules.vpc.outputs.vpc_id}",
    "private_subnet_ids": "${modules.vpc.outputs.private_subnet_ids}"
  }
}
```

Forge resolves `${modules.vpc.outputs.vpc_id}` after `vpc` has applied successfully and before `eks` runs. The output value is read from `ProjectModule.outputs`, which the engine populated during `vpc`'s apply.

### 12.3.3 Inheritance from credential template / project

A top-level input declared with `source: "credential_template"` or `source: "project"` is prefilled by Forge before the deploy dialog shows the input. This is the pattern that keeps the deploy form short:

```json
{
  "name": "ibmcloud_api_key",
  "type": "string",
  "source": "credential_template",
  "source_field": "ibmcloud_api_key",
  "sensitive": true,
  "order": 10
},
{
  "name": "ibmcloud_cluster_region",
  "type": "string",
  "source": "project",
  "source_field": "region",
  "order": 11
}
```

When the user picks an existing project, Forge populates these from the project's record. When the user creates a new project with a credential template selected, Forge populates them from the template's fields.

### 12.3.4 Engine-specific resolution

After variable resolution, the assembled `{name: value}` map is passed to the engine using its native channel:

| Engine | How variables are passed |
| --- | --- |
| OpenTofu | written to `terraform.tfvars.json` in the workspace |
| Ansible | passed via `--extra-vars @vars.yml` |
| Kubernetes | exposed to Jinja2 when rendering manifests / values files |
| Script | written to JSON at `FORGE_INPUTS_FILE` |

## 12.4 Prerequisites

The `prerequisites` block tells Forge what the project must already have. Each prerequisite is one of four types:

- `requirement` — free-form note. Forge does not auto-check this; it's a hint to the user.
- `credential_template` — declare which canonical credential to expect; combine with `source: "credential_template"` in `inputs[]` for automatic prefill.
- `project_secret` — Forge gates the deploy if the named secret is missing or invalid. The variable assembler injects matching project secrets into module variables by name automatically.
- `kubernetes_cluster` — forces the deploy dialog into "existing project" mode and filters the project picker to projects that have a registered cluster. Used by Stage 2 (BNK platform) blueprints.

## 12.5 `platform_defaults`

A per-platform-profile map that lets you bake cluster-shape defaults into the blueprint without bloating the deploy form:

```json
"platform_defaults": {
  "eks": {
    "container_platform": "AWS",
    "storage_class_name": "gp3",
    "cni_type": "host-device"
  },
  "roks": {
    "container_platform": "Generic",
    "storage_class_name": "ibmc-vpc-block-10iops-tier",
    "cni_type": "ipvlan",
    "cert_manager_namespace": "cert-manager",
    "flo_namespace": "f5-bnk"
  }
}
```

The variable assembler layers `platform_defaults["<profile_key>"]` into the module's variable map at apply time, between user-specified `module.inputs` and project secrets. Modules with matching `variable "..." {}` blocks pick up the values automatically.

The platform profile key is the cluster service code (`eks`, `aks`, `gke`, `roks`), not the cloud provider code.

## 12.6 Hands-on: AWS EKS Foundation blueprint

A complete two-module blueprint that creates a VPC and an EKS cluster on top of it.

```json
{
  "schema_version": 1,
  "blueprint": {
    "id": "aws-eks-foundation",
    "version": "1.0.0",
    "name": "AWS EKS Foundation",
    "description": "Provisions an AWS VPC and an EKS cluster on top of it. Emits BNK registration outputs."
  },
  "compatibility": {
    "supported_platform_profiles": ["eks"],
    "required_capabilities": []
  },
  "category": "infrastructure",
  "cloud_provider": "aws",
  "estimated_time": "20-40 minutes",
  "estimated_cost": "AWS usage-based",
  "difficulty": "intermediate",
  "maturity": "reference",
  "tags": ["aws", "eks", "vpc", "infrastructure"],
  "outcomes": [
    "An AWS VPC is created with private subnets across the available AZs",
    "An EKS cluster is provisioned in the new VPC",
    "Cluster outputs are available for BNK managed-cluster registration"
  ],
  "prerequisites": [
    {
      "type": "requirement",
      "description": "modules/aws-vpc and modules/aws-eks-cluster must be synced into Forge."
    },
    {
      "type": "credential_template",
      "name": "aws_credentials",
      "description": "AWS credential template granting permission to create VPC and EKS resources."
    }
  ],
  "platform_defaults": {
    "eks": {
      "container_platform": "AWS",
      "storage_class_name": "gp3",
      "cni_type": "host-device"
    }
  },
  "inputs": {
    "required": [
      { "name": "aws_region",   "type": "string", "source": "project",   "source_field": "region", "order": 10 },
      { "name": "vpc_name",     "type": "string", "order": 20 },
      { "name": "cluster_name", "type": "string", "order": 30 }
    ],
    "optional": [
      { "name": "kubernetes_version", "type": "string", "default": "1.30", "order": 40 }
    ]
  },
  "modules": [
    {
      "id": "vpc",
      "name": "VPC",
      "module": "modules/aws-vpc",
      "version": "1.0.0",
      "depends_on": [],
      "inputs": {
        "aws_region": "${aws_region}",
        "vpc_name":   "${vpc_name}"
      }
    },
    {
      "id": "eks",
      "name": "EKS Cluster",
      "module": "modules/aws-eks-cluster",
      "version": "1.0.0",
      "depends_on": ["vpc"],
      "inputs": {
        "aws_region":         "${aws_region}",
        "cluster_name":       "${cluster_name}",
        "kubernetes_version": "${kubernetes_version}",
        "vpc_id":             "${modules.vpc.outputs.vpc_id}",
        "private_subnet_ids": "${modules.vpc.outputs.private_subnet_ids}"
      }
    }
  ]
}
```

The user types **three** values on the deploy form: `vpc_name`, `cluster_name`, `kubernetes_version`. Region prefills from the project. The credential template injects AWS credentials at apply time. `platform_defaults["eks"]` provides the storage class and CNI type to any module that consumes them.

## 12.7 Hands-on: BNK Platform on IBM ROKS blueprint (Stage 2)

A more sophisticated blueprint that targets an existing cluster, gates on prerequisites, and uses `platform_defaults` to keep the form short:

```json
{
  "schema_version": 1,
  "blueprint": {
    "id": "ibm-roks-existing-cluster",
    "version": "1.0.0",
    "name": "BNK 2.3 Platform (IBM ROKS)",
    "description": "Install the BNK 2.3 platform onto an existing IBM ROKS cluster: cert-manager, F5 Lifecycle Operator, CNEInstance, and the BNK License."
  },
  "compatibility": {
    "supported_platform_profiles": ["ibm_roks"],
    "required_capabilities": []
  },
  "category": "bnk",
  "cloud_provider": "ibm",
  "estimated_time": "5-10 minutes",
  "difficulty": "intermediate",
  "maturity": "reference",
  "bnk_version": "2.3",
  "tags": ["ibm", "roks", "openshift", "bnk", "2.3"],
  "outcomes": [
    "Existing IBM ROKS cluster referenced by name or ID",
    "cert-manager installed with its CRDs registered",
    "F5 Lifecycle Operator (FLO) installed and bound to an IBM IAM trusted profile",
    "CNEInstance deployed with TMM/Helm pods Ready",
    "BNK License CR applied using the JWT from IBM COS"
  ],
  "prerequisites": [
    {
      "type": "kubernetes_cluster",
      "description": "An existing IBM ROKS cluster reachable from the project."
    },
    {
      "type": "credential_template",
      "name": "ibmcloud_api_key",
      "description": "IBM Cloud credential template that supplies the API key, default resource group, and the COS instance reference."
    },
    {
      "type": "project_secret",
      "name": "bigip_password",
      "description": "BIG-IP admin password used by the FLO CIS controller. Stored encrypted; never serialized into terraform.tfvars.json or task output."
    }
  ],
  "platform_defaults": {
    "roks": {
      "container_platform": "Generic",
      "storage_class_name": "ibmc-vpc-block-10iops-tier",
      "cloud_provider": "ibm",
      "cni_type": "ipvlan",
      "nad_cni_type": "ipvlan",
      "nad_interface_name": "ens3",
      "nad_ipvlan_mode": "l2",
      "cert_manager_namespace": "cert-manager",
      "flo_namespace": "f5-bnk",
      "flo_utils_namespace": "f5-utils"
    }
  },
  "inputs": {
    "required": [
      { "name": "ibmcloud_api_key",        "type": "string", "sensitive": true,
        "source": "credential_template",   "source_field": "ibmcloud_api_key",     "order": 10 },
      { "name": "ibmcloud_cluster_region", "type": "string",
        "source": "project",               "source_field": "region",               "order": 11 },
      { "name": "roks_cluster_name_or_id", "type": "string", "order": 20 }
    ],
    "optional": [
      { "name": "ibmcloud_resource_group", "type": "string", "default": "default",
        "source": "credential_template",   "source_field": "ibmcloud_resource_group", "order": 12 },
      { "name": "ibmcloud_cos_instance_name", "type": "string", "default": "bnk-orchestration",
        "source": "credential_template",   "source_field": "ibm_cos_instance_name",  "order": 30 },
      { "name": "bigip_url",      "type": "string", "default": "",      "order": 40 },
      { "name": "bigip_username", "type": "string", "default": "admin", "order": 41 }
    ]
  },
  "modules": [
    {
      "id": "cluster-register",
      "name": "ROKS Cluster Register",
      "module": "modules/roks-cluster-register",
      "version": "v1.0.0",
      "depends_on": [],
      "inputs": {
        "ibmcloud_api_key":         "${ibmcloud_api_key}",
        "ibmcloud_cluster_region":  "${ibmcloud_cluster_region}",
        "ibmcloud_resource_group":  "${ibmcloud_resource_group}",
        "roks_cluster_name_or_id":  "${roks_cluster_name_or_id}"
      }
    },
    {
      "id": "cert-manager",
      "name": "cert-manager",
      "module": "modules/roks-cluster-install-cert-manager",
      "version": "v1.0.0",
      "depends_on": ["cluster-register"],
      "inputs": {
        "ibmcloud_api_key":         "${ibmcloud_api_key}",
        "ibmcloud_cluster_region":  "${ibmcloud_cluster_region}",
        "ibmcloud_resource_group":  "${ibmcloud_resource_group}",
        "roks_cluster_name_or_id":  "${roks_cluster_name_or_id}"
      }
    },
    {
      "id": "flo",
      "name": "F5 Lifecycle Operator",
      "module": "modules/roks-cluster-install-flo",
      "version": "v1.0.0",
      "depends_on": ["cert-manager"],
      "inputs": {
        "ibmcloud_api_key":           "${ibmcloud_api_key}",
        "ibmcloud_cluster_region":    "${ibmcloud_cluster_region}",
        "ibmcloud_resource_group":    "${ibmcloud_resource_group}",
        "roks_cluster_name_or_id":    "${roks_cluster_name_or_id}",
        "ibmcloud_cos_instance_name": "${ibmcloud_cos_instance_name}",
        "cert_manager_crd_ready":     true,
        "bigip_url":                  "${bigip_url}",
        "bigip_username":             "${bigip_username}"
      }
    },
    {
      "id": "cneinstance",
      "name": "CNEInstance",
      "module": "modules/roks-cluster-cneinstall",
      "version": "v1.0.0",
      "depends_on": ["flo"],
      "inputs": {
        "ibmcloud_api_key":         "${ibmcloud_api_key}",
        "ibmcloud_cluster_region":  "${ibmcloud_cluster_region}",
        "ibmcloud_resource_group":  "${ibmcloud_resource_group}",
        "roks_cluster_name_or_id":  "${roks_cluster_name_or_id}"
      }
    },
    {
      "id": "license",
      "name": "BNK License",
      "module": "modules/roks-cluster-license",
      "version": "v1.0.0",
      "depends_on": ["cneinstance"],
      "inputs": {
        "ibmcloud_api_key":           "${ibmcloud_api_key}",
        "ibmcloud_cluster_region":    "${ibmcloud_cluster_region}",
        "ibmcloud_resource_group":    "${ibmcloud_resource_group}",
        "roks_cluster_name_or_id":    "${roks_cluster_name_or_id}",
        "ibmcloud_cos_instance_name": "${ibmcloud_cos_instance_name}",
        "use_cos_bucket":             true
      }
    }
  ]
}
```

What the user actually types on the deploy form:

- `roks_cluster_name_or_id` (the only manual entry)
- Optionally: `bigip_url`, `bigip_username`

Everything else flows from the credential template, project context, project secrets, and `platform_defaults["roks"]`. The deploy form has dropped from 25 fields to 1 manual + 4 prefilled-or-empty.

## 12.8 README expectations

A blueprint README answers four questions:

1. **What outcome the blueprint produces** (matches but expands the manifest's `outcomes` list).
2. **Lifecycle stage** — Stage 1, 2, 3, or hybrid.
3. **Prerequisites** — required modules, project secrets, credential templates, target cluster type.
4. **What to do after apply** — does the user need to run any post-step?

Keep it concise. Forge surfaces the README on the catalog detail page.

## 12.9 Local validation

```bash
python3 -c "import json; json.load(open('blueprints/bnk-on-roks/forge-blueprint.json'))"
```

For more thorough validation, render the variable wiring locally to confirm every `${...}` reference resolves to a value:

```python
import json
manifest = json.load(open("blueprints/bnk-on-roks/forge-blueprint.json"))

declared_inputs = {i["name"] for inp_set in manifest.get("inputs", {}).values() for i in inp_set}

for module in manifest["modules"]:
    for var_name, value in module.get("inputs", {}).items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            ref = value[2:-1]
            if ref.startswith("modules."):
                # cross-module reference; just check the module id exists
                module_id = ref.split(".")[1]
                if not any(m["id"] == module_id for m in manifest["modules"]):
                    print(f"BAD: {module['id']}.{var_name} references unknown module {module_id}")
            elif ref not in declared_inputs:
                print(f"BAD: {module['id']}.{var_name} references undeclared input {ref}")
```

## 12.10 Packaging and publishing

1. Commit your blueprint repository.
2. Push to git.
3. Tag releases (`bp-1.0.0`, etc.) so consumers can pin if they want.
4. Bump `blueprint.version` for any orchestration change.
5. Settings → **Blueprint Sources → Add Source** in Forge to register the repo.

If you ship modules and blueprints from the same repository, Forge supports that — just add it as both a Module Source and a Blueprint Source. Most authors prefer separate repos for the cadence reasons noted in chapter 6.

## 12.11 Versioning + source re-sync

Forge tracks releases by `(blueprint_id, blueprint_version)` and modules by `(module.path, module.version)`. The behaviors:

- **Same version, same content** — re-sync is a no-op.
- **Same version, different content** — flagged as a *conflict*. Bump the version or delete the existing release before re-importing.
- **New version, new content** — a new release/module entry is created. Old versions remain in the catalog for projects that pinned to them.

Bump `module.version` for any user-visible change in the engine code. Bump `blueprint.version` for any orchestration change. A blueprint version bump does not require a module bump unless the underlying module also changed.

The catalog UI prepends a `v` to versions when rendering. Author your version strings without a leading `v` (`1.0.0`, not `v1.0.0`).

## 12.12 Tips and gotchas

- **`response_model` silently drops fields** — Pydantic strips anything not in the declared schema, even if the route returns it. When adding a field to a response, update the schema.
- **Schemas live in TWO places** — always check both `backend/schemas/*.py` AND inline definitions in `backend/routes/*.py`.
- **MCP body vs. query drift** — backend routes may require query params where the MCP server sends JSON body. Read the route signature before writing MCP wiring.
- **Frontend-only deploy misses backend changes** — use `make deploy` (or `make deploy-backend`) whenever backend code changed.
- **Sensitive inputs**: declare them with `sensitive: true`. Forge tightens file permissions on the inputs file or extra-vars file and redacts values in the UI. Avoid logging them in task output.
- **Idempotency**: Forge will retry runs and run drift refresh — non-idempotent modules surface as flapping state.
- **CRDs and dependency order**: when a module ships CRDs, the next module that depends on those CRDs must wait for them.
- **Helm idempotency**: Forge uses `helm upgrade --install` so re-runs are idempotent. Avoid non-deterministic values.

---

# Chapter 13 — Adding New Cloud Vendor Implementations

This chapter is for platform engineers extending Forge itself with a new cloud vendor (Oracle, Linode, Alibaba, DigitalOcean, ...). The shape of this chapter is reverse-engineered from the AWS↔IBM standardization sweep — following the same touch points keeps new clouds organized and reviewable.

## 13.1 Mental model

A cloud integration in BNK Forge is the sum of seven concerns: **identity**, **regions**, **clusters**, **state storage**, **validation**, **catalog presentation**, and **per-blueprint platform defaults**. Each concern has a well-defined home in the codebase. New clouds are added by extending each home with a new branch, never by inventing parallel structures.

The architecture has three principles:

**Per-cloud helpers, not per-cloud monoliths.** AWS doesn't have an `aws_orchestrator.py` that knows everything; it has `aws_auth_service.py` (auth), `eks_service.py` (clusters), and shared infrastructure (`credential_template_service.py`, `engine_router.py`) with AWS-specific branches. Mirror this — break new code along the same lines.

**Routes live in shared files under per-cloud namespaces.** AWS routes are in `routes/cloud_auth.py` under `/api/cloud-auth/aws/*`. IBM routes are in the same file under `/api/cloud-auth/ibm/*`. Don't create `routes/oracle.py`; add Oracle routes to `cloud_auth.py` under `/api/cloud-auth/oracle/*`. The shared route file is a feature.

**The blueprint manifest is the user's API.** A new cloud implementation must let blueprint authors target it with the same shape as existing clouds. Designs that require special treatment in the manifest are a smell.

## 13.2 The seven touch points

| # | Concern | Backend home | Frontend home |
| - | ------- | ------------ | ------------- |
| 1 | Identity (credential template fields) | `models/system.py`, `services/credential_template_service.py`, alembic migration | `components/settings/CredentialTemplates.tsx` |
| 2 | Regions (validation + listing) | `utils/validators.py`, `services/<cloud>_cloud_service.py`, `routes/cloud_auth.py` | `lib/<cloud>-regions.ts` (optional), `components/cloud/CloudRegionSelector.tsx` |
| 3 | Clusters (auto-register + kubeconfig) | `services/<cloud>_service.py`, `services/cluster_management_service.py`, `services/execution/engine_router.py` | none (auto) |
| 4 | State storage (optional) | alembic migration, `models/projects.py`, `services/state_storage_service.py` | `components/projects/{Create,Edit}ProjectDialog.tsx` |
| 5 | Validation (project name + region) | `utils/naming.py`, `utils/validators.py`, `schemas/projects.py` | `components/projects/CreateProjectDialog.tsx` (zod superRefine) |
| 6 | Catalog presentation | `services/imported_blueprint_service.py` (touchpoint, no per-cloud code) | `pages/Stacks.tsx` (`providerConfig`) |
| 7 | Platform defaults / profile map | `services/platform_context_service.py` | none (consumed by blueprints) |

## 13.3 Step-by-step recipe

The order matters — schema first, then routes, then UI, then validation polish.

### Phase A — schema and identity

1. **Decide the canonical names.** Provider code (`oracle`), project type (`cloud-oracle`), platform profile key (`oke`), credential field prefix (`oracle_<field>`).
2. **Write an alembic migration** adding the new credential-template columns. Always use `IF NOT EXISTS` guards.
3. **Add the columns to `models/system.py`** under `class CloudCredentialTemplate`.

### Phase B — auth + cloud service

4. **Create `backend/services/<provider>_cloud_service.py`** with `resolve_effective_template`, `list_regions`, `list_regions_from_template`, `_exchange_api_key(self, api_key, *, template=None)`. Mirror `IBMCloudService`.
5. **Add token caching columns** if the cloud's auth involves short-lived tokens. Mirror `ibm_iam_token_encrypted` + `ibm_iam_token_expiry`.
6. **Create `backend/services/<provider>_auth_service.py` only if** there's a multi-step auth flow (SSO, OIDC, trusted-profile exchange). Static API keys don't need it.

### Phase C — clusters

7. **Create `backend/services/<cluster_service>_service.py`** (e.g. `oke_service.py`). Mirror `eks_service.py` exactly: `register_<kind>_cluster`, `unregister_<kind>_cluster`, helpers as needed.
8. **Wire bearer-token refresh into `engine_router.py`** if your cloud's stored kubeconfigs embed short-lived tokens. Mirror `_inject_eks_bearer_token` and `_inject_ibm_bearer_token`. Fail soft.
9. **Update `services/cluster_management_service.py`'s classification helpers** so `_classify_managed_cluster_module` returns the new cluster code.
10. **Update `services/platform_context_service.py`'s profile maps** to add the provider→profile mapping (and reverse).

### Phase D — routes

11. **Add routes to `backend/routes/cloud_auth.py` under `/api/cloud-auth/<provider>/*`.** Always include `/regions`. If the cloud needs auth-flow endpoints, add them here under the same namespace. If you're moving an old non-namespaced route, keep it as a hidden alias.
12. **Update `frontend-v2/src/lib/api/credentials.ts`** with the new helpers. Match the shape of existing IBM and AWS calls.
13. **Run `make openapi-types`** to regenerate `backend/openapi.json` and `frontend-v2/src/types/api-generated.ts`.

### Phase E — frontend

14. **Add the project-type entry to the canonical union.**
15. **Wire `CredentialTemplates.tsx`** to support the new provider's auth method radio + fields.
16. **Wire `CreateProjectDialog.tsx`**: row in `PROJECT_TYPES`, credential-template filter branch, region pre-discovery query, name validation via `slugify<Provider>SafeProjectName`.
17. **Add a `providerConfig` entry in `pages/Stacks.tsx`**: pick a color not already used.
18. **Region selector**: use `CloudRegionSelector` if basic; build a dedicated `<Provider>RegionSelector.tsx` if rich UX is needed.

### Phase F — validation

19. **Add naming + region validators** mirroring IBM. `is_<provider>_safe_name`, `slugify_<provider>_name`, `VALID_<PROVIDER>_REGIONS`, `validate_<provider>_region`.
20. **Wire both into `backend/schemas/projects.py`** alongside the AWS branch.
21. **Mirror in the frontend** — zod superRefine + inline warning paragraph.

### Phase G — catalog wiring

22. **Validate `compatibility.supported_platform_profiles` aligns** with the platform profile map.
23. **Document the `platform_defaults["<profile_key>"]` schema** for your cloud.

### Phase H — verify

24. **Run `make pre-push`.**
25. **Smoke-test the full flow**: create a credential template, create a project, sync a blueprint, deploy a cluster blueprint, wait until the IAM/STS token would expire, confirm runner kubeconfig refresh works.

## 13.4 Concrete templates

Concrete diffs you can copy and adapt. Substitute `oracle / oci / oke` for the cloud you're adding.

### 13.4.1 Alembic migration

```python
"""Add Oracle Cloud credential-template fields.

Revision ID: v2_NNN
Revises: v2_NNN-1
"""

import sqlalchemy as sa
from alembic import op

revision = "v2_NNN"
down_revision = "v2_NNN-1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {
        "oracle_api_key_encrypted":      sa.Text(),
        "oracle_user_ocid":              sa.String(length=255),
        "oracle_tenancy_ocid":           sa.String(length=255),
        "oracle_compartment_ocid":       sa.String(length=255),
        "oracle_fingerprint":            sa.String(length=255),
        "oracle_iam_token_encrypted":    sa.Text(),
        "oracle_iam_token_expiry":       sa.DateTime(timezone=True),
    }
    for name, type_ in cols.items():
        existing = bind.execute(sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'cloud_credential_templates' AND column_name = :name"
        ), {"name": name}).fetchone()
        if not existing:
            op.add_column("cloud_credential_templates", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    for name in (
        "oracle_iam_token_expiry", "oracle_iam_token_encrypted",
        "oracle_fingerprint", "oracle_compartment_ocid",
        "oracle_tenancy_ocid", "oracle_user_ocid", "oracle_api_key_encrypted",
    ):
        op.drop_column("cloud_credential_templates", name)
```

### 13.4.2 Cloud service skeleton

```python
# backend/services/oracle_cloud_service.py
"""Oracle Cloud (OCI) API wrapper for region discovery + token exchange."""
import logging
from datetime import UTC, datetime, timedelta

import requests
from sqlalchemy.orm import Session

from core.encryption import decrypt_value, encrypt_value
from core.errors import BadRequestError, NotFoundError, ServiceError
from models import CloudCredentialTemplate

logger = logging.getLogger(__name__)

OCI_IAM_TOKEN_URL = "https://identity.oraclecloud.com/oauth2/v1/token"

OCI_REGIONS = [
    {"value": "us-ashburn-1", "label": "US East (Ashburn)"},
    {"value": "us-phoenix-1", "label": "US West (Phoenix)"},
    # ... add the rest
]


class OracleCloudService:
    def __init__(self, db: Session):
        self.db = db

    def resolve_effective_template(self, template_id: int | None = None) -> CloudCredentialTemplate:
        # Mirror IBMCloudService.resolve_effective_template
        ...

    def list_regions_from_template(self, template_id: int | None = None) -> list[dict[str, str]]:
        template = self.resolve_effective_template(template_id)
        api_key = decrypt_value(template.oracle_api_key_encrypted)
        if not api_key:
            raise ServiceError("oracle_cloud", "Failed to decrypt OCI API key")
        self._exchange_api_key(api_key, template=template)
        return OCI_REGIONS

    def _exchange_api_key(self, api_key: str, *, template: CloudCredentialTemplate | None = None) -> str:
        if template is not None and template.oracle_iam_token_encrypted and template.oracle_iam_token_expiry:
            now = datetime.now(UTC)
            expiry = template.oracle_iam_token_expiry
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry - now > timedelta(seconds=60):
                cached = decrypt_value(template.oracle_iam_token_encrypted)
                if cached:
                    return cached
        # ... real exchange + cache write ...
```

### 13.4.3 Cluster service skeleton

```python
# backend/services/oke_service.py
"""OKE-specific service for auto-registration and IAM bearer-token refresh."""
from sqlalchemy.orm import Session
from models import KubernetesCluster, ProjectModule


def register_oke_cluster(db: Session, module: ProjectModule) -> KubernetesCluster:
    """Mirror eks_service.register_eks_cluster / roks_service.register_roks_cluster."""
    ...


def unregister_oke_cluster(db: Session, module: ProjectModule) -> bool:
    """Mirror eks_service.unregister_eks_cluster / roks_service.unregister_roks_cluster."""
    ...


def fetch_iam_bearer_token(api_key: str) -> str | None:
    """Best-effort exchange — returns None on failure."""
    try:
        from services.oracle_cloud_service import OracleCloudService
        svc = OracleCloudService.__new__(OracleCloudService)
        svc.db = None
        return svc._exchange_api_key(api_key)
    except Exception:
        return None
```

### 13.4.4 Engine router bearer-token injection

```python
# In services/execution/engine_router.py, inside resolve_kubeconfig:
elif cluster.cloud_provider in ("oracle", "oci"):
    kubeconfig_content = _inject_oci_bearer_token(
        kubeconfig_content, cluster, project, db,
    )

# Helper (next to _inject_ibm_bearer_token):
def _inject_oci_bearer_token(kubeconfig_content: str, cluster, project, db) -> str:
    # Mirror _inject_eks_bearer_token / _inject_ibm_bearer_token
    ...
```

### 13.4.5 Validators

```python
# In utils/naming.py
ORACLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,30}$")

def is_oracle_safe_name(value: str) -> bool:
    return bool(value) and ORACLE_NAME_PATTERN.fullmatch(value) is not None

def slugify_oracle_name(value: str, *, max_length: int = 31, fallback: str = "bnk-forge") -> str:
    # Mirror slugify_ibm_name
    ...

# In utils/validators.py
VALID_ORACLE_REGIONS = {"us-ashburn-1", "us-phoenix-1", "ap-tokyo-1", ...}

def validate_oracle_region(value: str | None, field_name: str = "region") -> None:
    if not value:
        return
    pattern = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")
    if not pattern.match(value):
        return
    if value not in VALID_ORACLE_REGIONS:
        raise ValueError(
            f"Invalid OCI region for '{field_name}': '{value}'. "
            f"See https://docs.oracle.com/iaas/Content/General/Concepts/regions.htm"
        )
```

### 13.4.6 Frontend `Stacks.tsx` provider entry

```ts
const providerConfig: Record<string, { label: string; color: string }> = {
  aws:        { label: 'AWS',     color: 'bg-orange-500/10 text-orange-400 border-orange-500/30' },
  azure:      { label: 'Azure',   color: 'bg-blue-500/10 text-blue-400 border-blue-500/30' },
  gcp:        { label: 'GCP',     color: 'bg-red-500/10 text-red-400 border-red-500/30' },
  ibm:        { label: 'IBM',     color: 'bg-sky-500/10 text-sky-400 border-sky-500/30' },
  oracle:     { label: 'Oracle',  color: 'bg-rose-500/10 text-rose-400 border-rose-500/30' }, // new
  any:        { label: 'Multi-Platform', color: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' },
  'bare-metal': { label: 'Bare Metal',   color: 'bg-amber-500/10 text-amber-400 border-amber-500/30' },
};
```

### 13.4.7 Project type + create dialog

```tsx
// In types/projects.ts
export type ProjectType = 'cloud-aws' | 'cloud-azure' | 'cloud-gcp' | 'cloud-ibm' | 'cloud-oracle' | 'kubernetes' | 'bare-metal';

// In CreateProjectDialog.tsx
const PROJECT_TYPES = [
  ...,
  { value: 'cloud-oracle', label: 'Oracle Cloud', icon: 'OCI', description: 'Deploy on Oracle OKE' },
];

const ORACLE_NAME_PATTERN = /^[a-z][a-z0-9-]{0,30}$/;

function slugifyOracleSafeProjectName(value: string): string { /* ... */ }

// In zod superRefine:
if (data.project_type === 'cloud-oracle' && data.name && !ORACLE_NAME_PATTERN.test(data.name)) {
  ctx.addIssue({
    code: z.ZodIssueCode.custom,
    message: `Oracle Cloud resource names use lowercase letters, digits, and '-' only (max 31 chars). Try '${slugifyOracleSafeProjectName(data.name)}'.`,
    path: ['name'],
  });
}
```

## 13.5 Cloud-onboarding checklist

Copy this into your PR description.

### Backend

- [ ] Alembic migration adds credential-template columns idempotently
- [ ] `models/system.py` declares the new columns
- [ ] `services/<provider>_cloud_service.py` exists with `list_regions`, `list_regions_from_template`, `_exchange_api_key` (with optional `template=` for caching)
- [ ] `services/<cluster>_service.py` exists with `register_<cluster>_cluster`, `unregister_<cluster>_cluster`, `fetch_iam_bearer_token`
- [ ] `services/cluster_management_service.py:_classify_managed_cluster_module` returns the new cluster code for matching modules
- [ ] `services/platform_context_service.py:_MANAGED_CLOUD_PROFILE_MAP` includes the new provider→profile mapping
- [ ] `services/execution/engine_router.py:resolve_kubeconfig` injects bearer tokens for the new cluster type
- [ ] `routes/cloud_auth.py` exposes `/api/cloud-auth/<provider>/regions`
- [ ] `utils/naming.py` has `is_<provider>_safe_name` + `slugify_<provider>_name`
- [ ] `utils/validators.py` has `VALID_<PROVIDER>_REGIONS` + `validate_<provider>_region`
- [ ] `schemas/projects.py:ProjectCreate._validate_region` runs both validators when project type matches
- [ ] `make openapi-types` was re-run

### Frontend

- [ ] `types/projects.ts:ProjectType` includes `cloud-<provider>`
- [ ] `lib/api/credentials.ts` has `list<Provider>Regions`
- [ ] `components/settings/CredentialTemplates.tsx` form supports the new credential fields
- [ ] `components/projects/CreateProjectDialog.tsx`:
  - [ ] `PROJECT_TYPES` includes the new cloud
  - [ ] Credential template filter branches on `cloud-<provider>`
  - [ ] Region pre-discovery query (if applicable)
  - [ ] zod superRefine validates the project name
  - [ ] Inline warning paragraph for invalid names
- [ ] `pages/Stacks.tsx:providerConfig` includes the new provider with a unique color
- [ ] `CloudRegionSelector` handles the new provider (or a dedicated `<Provider>RegionSelector` exists)

### Verification

- [ ] `make pre-push` passes
- [ ] Credential template can be created, edited, and validated for the new cloud
- [ ] Project create dialog: invalid name → inline warning + zod error; valid name → submit
- [ ] Backend rejects invalid `cloud-<provider>` project names with the suggested slug in the error
- [ ] Backend rejects invalid `cloud-<provider>` regions with a useful message
- [ ] `/api/cloud-auth/<provider>/regions` returns `{provider, regions: [{value, label}]}`
- [ ] Sample blueprint with `cloud_provider: <provider>` syncs into the catalog and shows the right pill
- [ ] Cluster blueprint deploy → auto-registers a `KubernetesCluster` with `cloud_provider = '<provider>'`
- [ ] Stale-token test: edit `<provider>_iam_token_expiry` to a past timestamp on the cluster's credential template, run a module against the cluster, confirm `engine_router` refreshes the token and the apply succeeds

## 13.6 Common pitfalls

- **Inventing new patterns.** If your design needs a new file location, a new module-level layer, or a new variable wiring syntax, stop. AWS and IBM cover almost every realistic onboarding shape. Re-read the existing pattern; you almost certainly want to mirror it instead of innovating.
- **Skipping the platform profile map.** `_MANAGED_CLOUD_PROFILE_MAP` is what lets `platform_defaults["<profile_key>"]` reach modules at apply time. Forget to add the mapping and your blueprints will silently miss every default.
- **Hardcoding a region list in the frontend.** AWS does this for legacy reasons; new clouds should fetch from `/api/cloud-auth/<provider>/regions`.
- **Forgetting to re-run `make openapi-types`.** Pre-push will fail with `make openapi-types-check` complaining about stale types.
- **Storing API keys without encryption.** Always go through `core.encryption.{encrypt_value,decrypt_value}` and use the `_encrypted` column suffix.
- **Putting routes under `/api/<provider>/*` instead of `/api/cloud-auth/<provider>/*`.**
- **Half-implementing token caching.** If your cloud's auth involves short-lived tokens, do the cache columns + cache-aware `_exchange_api_key` together.
- **Letting credential-template-driven inputs end up in the deploy form.** When a blueprint declares an input with `source: "credential_template"` + `source_field`, Forge prefills it; the user shouldn't see it as a typed field.
- **Using free-form `requirement` prerequisites.** Modern blueprints declare `kubernetes_cluster`, `credential_template`, and `project_secret` prerequisites with proper types so Forge can gate the deploy.

---

# Appendix A — Schema Quick Reference

## A.1 Module manifest top-level keys

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Always `1`. |
| `module` | object | yes | Identity, category, provider, tags. |
| `deployment_pack` | object | yes | Engine, runner profile, entrypoints, lifecycle. |
| `dependencies` | object | no | Required and optional module references. |
| `inputs` | object | yes | Required and optional input lists. |
| `outputs` | object | no | `key_outputs` curated list for UI. |
| `credentials` | object | no | Reserved for future explicit credential declarations. |

## A.2 Blueprint manifest top-level keys

| Key | Type | Required | Notes |
| --- | --- | --- | --- |
| `schema_version` | int | yes | Always `1`. |
| `blueprint` | object | yes | `id`, `version`, `name`, `description`. |
| `compatibility` | object | yes | `supported_platform_profiles`, `required_capabilities`. |
| `category` | string | yes | One of `infrastructure | bnk | solution | bare-metal`. |
| `cloud_provider` | string | no | `aws | azure | gcp | openshift | ibm | any | bare-metal`. |
| `bnk_version` | string | no | `"2.2"`, `"2.3"`, etc. Surfaces as catalog badge. |
| `maturity` | string | no | `production-ready | reference | beta | alpha | experimental`. |
| `tags` | list | no | Searchable in the catalog. |
| `outcomes` | list | no | Human-readable expectations. |
| `prerequisites` | list | no | Required modules, credential templates, secrets, cluster. |
| `input_summary` | list | no | Human-readable input guidance. |
| `inputs` | object | yes | Top-level user inputs (required + optional). |
| `modules` | list | yes | Orchestration list, must have at least one entry. |
| `icon`, `color` | string | no | Currently ignored by the catalog UI. |
| `platform_defaults` | object | no | Per-platform defaults map. |
| `variable_templates` | object | no | Reusable variable templates. |

## A.3 Engine entrypoint summary

| Engine | Required entrypoints | Optional entrypoints |
| --- | --- | --- |
| OpenTofu | `module_root` | — |
| Ansible | `playbook` | `destroy_playbook`, `inventory_source`, `outputs_file` |
| Kubernetes | `manifest_path` (manifests) **or** `chart_ref` / `chart_path` (helm) | `template_engine`, `values_path`, `release_name`, `namespace`, `chart_version`, `create_namespace` |
| Script | `apply_script` | `destroy_script`, `outputs_file` |

## A.4 Lifecycle support matrix

| Engine | init | plan | apply | destroy | refresh | drift |
| --- | --- | --- | --- | --- | --- | --- |
| OpenTofu | yes | yes | yes | yes | yes | yes |
| Ansible | yes | yes (via `--check`) | yes | yes | yes | no |
| Kubernetes | yes | yes | yes | yes | no | yes |
| Script | yes | no | yes | yes | no | no |

## A.5 Cloud onboarding seven touch points

| # | Concern | Backend home | Frontend home |
| - | --- | --- | --- |
| 1 | Identity | `models/system.py`, `services/credential_template_service.py`, alembic | `components/settings/CredentialTemplates.tsx` |
| 2 | Regions | `utils/validators.py`, `services/<cloud>_cloud_service.py`, `routes/cloud_auth.py` | `lib/<cloud>-regions.ts`, `components/cloud/CloudRegionSelector.tsx` |
| 3 | Clusters | `services/<cloud>_service.py`, `cluster_management_service.py`, `engine_router.py` | none |
| 4 | State storage | alembic, `models/projects.py`, `services/state_storage_service.py` | `components/projects/{Create,Edit}ProjectDialog.tsx` |
| 5 | Validation | `utils/naming.py`, `utils/validators.py`, `schemas/projects.py` | `CreateProjectDialog.tsx` |
| 6 | Catalog | `services/imported_blueprint_service.py` | `pages/Stacks.tsx` |
| 7 | Profile map | `services/platform_context_service.py` | none |

---

# Appendix B — External References

## B.1 Engines and tools

- **OpenTofu** — open-source Terraform fork. Documentation: <https://opentofu.org/docs/>
- **Terraform language reference** (compatible with OpenTofu): <https://developer.hashicorp.com/terraform/language>
- **AWS Terraform provider**: <https://registry.terraform.io/providers/hashicorp/aws/latest/docs>
- **Azure Terraform provider**: <https://registry.terraform.io/providers/hashicorp/azurerm/latest/docs>
- **Google Cloud Terraform provider**: <https://registry.terraform.io/providers/hashicorp/google/latest/docs>
- **IBM Cloud Terraform provider**: <https://registry.terraform.io/providers/IBM-Cloud/ibm/latest/docs>
- **Ansible documentation**: <https://docs.ansible.com/ansible/latest/index.html>
- **Ansible role structure**: <https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_reuse_roles.html>
- **ansible-lint**: <https://ansible.readthedocs.io/projects/lint/>
- **Helm**: <https://helm.sh/docs/>
- **Helm chart best practices**: <https://helm.sh/docs/chart_best_practices/>
- **kr8s**: <https://docs.kr8s.org/>
- **Jinja2**: <https://jinja.palletsprojects.com/>
- **kubectl server-side apply**: <https://kubernetes.io/docs/reference/using-api/server-side-apply/>

## B.2 Cloud platforms

- **AWS EKS**: <https://docs.aws.amazon.com/eks/>
- **Azure AKS**: <https://learn.microsoft.com/azure/aks/>
- **Google GKE**: <https://cloud.google.com/kubernetes-engine/docs>
- **IBM Cloud API key management**: <https://cloud.ibm.com/docs/account?topic=account-userapikey>
- **IBM ROKS**: <https://cloud.ibm.com/docs/openshift>
- **IBM Cloud Object Storage**: <https://cloud.ibm.com/docs/cloud-object-storage>
- **IBM Cloud SDK for Python**: <https://github.com/IBM/ibm-cloud-sdk-common>
- **Oracle OKE**: <https://docs.oracle.com/iaas/Content/ContEng/home.htm>

## B.3 Kubernetes references

- **Kubernetes API conventions**: <https://github.com/kubernetes/community/blob/master/contributors/devel/sig-architecture/api-conventions.md>
- **CustomResourceDefinitions**: <https://kubernetes.io/docs/tasks/extend-kubernetes/custom-resources/custom-resource-definitions/>
- **cert-manager**: <https://cert-manager.io/docs/>

## B.4 Schema validation

- **Pydantic v2**: <https://docs.pydantic.dev/latest/>
- **JSON Schema**: <https://json-schema.org/>

## B.5 Forge platform

- **F5 BIG-IP Next for Kubernetes** (BNK): your internal F5 documentation. Forge ships compatible with BNK 2.2 and 2.3.

---

# Appendix C — Glossary

**Apply** — the operation that brings a module's declared state into existence. For OpenTofu it's `tofu apply`; for Ansible it's `ansible-playbook apply.yml`; for Kubernetes it's `kr8s` server-side apply or `helm upgrade --install`; for scripts it's running `apply.py`.

**Blueprint** — a `forge-blueprint.json` file that orchestrates one or more modules into a deployable solution. The user-facing unit in the Forge catalog.

**Blueprint release** — a frozen manifest snapshot for a `(blueprint_id, blueprint_version)` pair, created by syncing a blueprint source. A release becomes deployable only after the user clicks *Import*.

**bnk-operator** — a Kopf-based Python agent that runs inside customer Kubernetes clusters. Talks to Forge over a persistent WebSocket (or HTTP polling) and executes K8s operations locally. The operator path avoids exposing the cluster's API server externally.

**Catalog** — the collection of imported modules and blueprints Forge can deploy. Visible on the Modules and Blueprints pages.

**Credential template** — a reusable bundle of cloud credentials attached to a project. Forge injects the right env vars when modules run, and can prefill blueprint inputs that reference its fields.

**Deploy model** — for Kubernetes modules, the choice between `manifests` (apply YAML files) and `helm` (install a chart).

**Drift** — divergence between declared state and live cluster/cloud state. Forge runs drift checks on a schedule for modules with `supports_drift: true`.

**Engine** — the runtime that executes a module: OpenTofu, Ansible, Kubernetes, or script.

**Inventory** (Ansible) — a file or dynamic source listing the hosts a playbook should target.

**Lifecycle** — the set of operations Forge can call on a module: `init`, `plan`, `apply`, `destroy`, `refresh`, `drift`.

**Module** — a versioned, reusable deployment pack. The reusable unit referenced by blueprints.

**Module pack** — synonym for module. The directory containing `bnkforge.pack.json` plus engine-specific files.

**Operator-mediated execution** — Kubernetes engine path where apply runs from inside the cluster via the bnk-operator agent. Preferred over direct kubeconfig.

**Plan** — a dry-run that shows what apply *would* change without making changes. OpenTofu has native plan; Ansible runs `--check`; Kubernetes plan compares rendered manifests to live state; scripts typically don't have plan.

**Platform profile** — the cluster service code (`eks`, `aks`, `gke`, `roks`, `oke`). Used as the key for `platform_defaults` and as the value reported by `cluster.detected_platform_profile`.

**Project** — a Forge runtime instance. Has a target environment, a credential template, deployed modules, and persisted state.

**Provider** — the cloud vendor code (`aws`, `azure`, `gcp`, `ibm`, `oracle`). Used as `cloud_provider` on credential templates and projects.

**Release** — see *Blueprint release*.

**Runner profile** — selects the container/sandbox the engine runs in. `opentofu-default`, `kubernetes-default`, `ansible-default`, `script-restricted`.

**Source** — a registered git repository Forge syncs from. Two flavors: Module Source (sync `bnkforge.pack.json` files) and Blueprint Source (sync `forge-blueprint.json` files).

**Stage 1 / Stage 2 / Stage 3** — Forge's mental model for blueprint categories: Infrastructure (creates the cluster) → Platform (installs BNK) → Solutions (apps on top of BNK).

**Variable wiring** — the `${...}` references in a blueprint's `module.inputs` that resolve top-level inputs and prior modules' outputs into the current module's input map.

---

*This guide supersedes the older split documents. If you spot an inconsistency or a missing recipe, file an issue against the bnk-forge repo with the affected chapter and section so it can be folded back into the next revision.*
