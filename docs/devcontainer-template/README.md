# Generic Python + Node Dev Container

A reusable devcontainer template for Python (FastAPI / Django / Flask) +
Node (React / Vue / Next / etc.) projects, with optional Postgres + Redis
sidecars and pre-installed AI coding tools (Cline extension, Claude Code CLI).

This template is based on a working setup for a real FastAPI + React project,
cleaned up to be project-agnostic. The project-specific bits you need to
customize are clearly marked in each file.

## What's in the box

```
.devcontainer/
├── Dockerfile            # Python 3.11 + Node 20 + Claude Code CLI
├── docker-compose.yml    # devcontainer + postgres + redis (bridge networking)
├── devcontainer.json     # VS Code extensions, port labels, lifecycle hooks
├── post-create.sh        # runs once on first build — template stub
├── post-start.sh         # runs every start — template stub
└── .gitignore
```

**Pre-installed in the image:**
- Python 3.11 (override with `PYTHON_VERSION` build arg)
- Node.js 20 (override with `NODE_MAJOR` build arg)
- `git`, `curl`, `jq`, `ssh`, `vim-tiny`, `procps`, `iproute2`, `sudo`
- `@anthropic-ai/claude-code` CLI (run `claude` in any terminal)
- An unprivileged `vscode` user with passwordless sudo

**Pre-configured in `devcontainer.json`:**
- **Cline** (`saoudrizwan.claude-dev`) — AI agent that runs commands in your shell
- Python: `ms-python.python`, `charliermarsh.ruff`, `ms-python.mypy-type-checker`
- JS/TS: `dbaeumer.vscode-eslint`, `esbenp.prettier-vscode`, `bradlc.vscode-tailwindcss`
- Infra: `redhat.vscode-yaml`, `ms-azuretools.vscode-docker`
- `editor.formatOnSave` enabled with sane defaults per-language

**Sidecars (optional, in `docker-compose.yml`):**
- Postgres 16 — reachable as `postgres:5432` from the devcontainer
- Redis 7 — reachable as `redis:6379` from the devcontainer

Delete either sidecar if your project doesn't need it.

---

## Quick start

### 1. Copy the template into your project

From this repo:

```bash
cp -R docs/devcontainer-template/.devcontainer /path/to/your-project/
cd /path/to/your-project
chmod +x .devcontainer/post-create.sh .devcontainer/post-start.sh
```

### 2. Customize three places

**a) `.devcontainer/devcontainer.json`** — rename the container and match
port labels to what your app actually exposes:

```jsonc
"name": "My Project Dev",
"forwardPorts": [8000, 8080],
```

**b) `.devcontainer/docker-compose.yml`** — adjust the `ports:` list and any
app-specific environment variables. Remove the `postgres` / `redis` services
if your project doesn't need them.

**c) `.devcontainer/post-create.sh` and `post-start.sh`** — uncomment the
blocks that match your stack (pip / poetry / npm / pnpm / alembic / django /
vite / next), delete the rest.

### 3. Prerequisites on your machine

- **Docker** (Docker Desktop on Mac/Windows, or Docker Engine on Linux)
- **VS Code** with the **Dev Containers** extension
  (`ms-vscode-remote.remote-containers`)

### 4. Open the project in the container

1. Open your project folder in VS Code.
2. Press `F1` → **Dev Containers: Reopen in Container**
   (or click the green `><` icon in the bottom-left corner and pick it).
3. First build takes 2–5 minutes. Subsequent starts are ~10 seconds.
4. When it finishes, you'll have a terminal inside the container at `/workspace`.

---

## Using Cline inside the container

**Cline** is a VS Code extension that lets an AI agent plan tasks, read files,
and run commands in your terminal. It's already in the extension list — VS Code
will install it automatically when the container starts.

**First-time setup:**
1. Click the Cline icon in the VS Code sidebar (robot head).
2. Sign in or paste an API key for your chosen provider (Anthropic, OpenAI,
   Gemini, Ollama, OpenRouter, etc. — Cline supports many).
3. Start a new task. Cline operates inside the container and shares the
   `/workspace` filesystem with VS Code, so file edits show up in the editor
   live.

**Tip**: Cline runs commands in a VS Code terminal. That terminal is *inside*
the container, so it can talk to `postgres:5432`, `redis:6379`, and any
localhost services your post-start script launched.

---

## Using Claude Code inside the container

**Claude Code** is Anthropic's CLI coding agent. It's pre-installed in the
image — no setup needed beyond authentication.

```bash
# In any terminal inside the container:
cd /workspace
claude
```

On first run, it will walk you through authentication (browser flow or API
key). After that, just type what you want:

```
claude "find the file that handles user login and add rate limiting"
```

Claude Code has full read/write access to `/workspace` and can run shell
commands — same scope as Cline, different interface. Pick whichever fits your
workflow; they don't conflict and you can use both.

**Docs**: <https://docs.claude.com/en/docs/claude-code>

---

## Port forwarding

Ports listed in `forwardPorts` (in `devcontainer.json`) and in `ports:` (in
`docker-compose.yml`) are automatically forwarded to your host machine.

The compose file binds to `0.0.0.0:` so ports are reachable even when the
devcontainer runs inside a Linux VM on a Mac host (useful for Docker Desktop
users who want to open the forwarded URL from a native browser).

**Example**: with the defaults, a backend on `:8000` inside the container is
reachable at `http://localhost:8000` from your host browser.

---

## Common customizations

### Add a new forwarded port

1. Add it to `docker-compose.yml` under `ports:` (binds host → container):
   ```yaml
   - "0.0.0.0:3000:3000"
   ```
2. Add it to `devcontainer.json` under `forwardPorts` (tells VS Code to label
   it in the Ports panel):
   ```jsonc
   "forwardPorts": [8000, 8080, 3000]
   ```

### Drop the Postgres/Redis sidecars

Edit `docker-compose.yml` and delete the `postgres:` and/or `redis:` service
blocks, the matching `depends_on:` entries under `devcontainer:`, and the
`pgdata`/`redisdata` volume declarations.

### Change Python or Node version

In `docker-compose.yml`:
```yaml
build:
  args:
    PYTHON_VERSION: "3.12"
    NODE_MAJOR: "22"
```

Then rebuild: **Dev Containers: Rebuild Container**.

### Add system packages

Add to the `apt-get install` line in `Dockerfile`. Example for projects that
need build tools and Postgres client libs:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates git openssh-client gnupg jq procps iproute2 sudo vim-tiny \
      build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
```

### Add kubectl / helm / terraform

Uncomment the blocks marked `OPTIONAL` near the bottom of the `Dockerfile`.

### Persist state across container rebuilds

The project source is bind-mounted (`..:/workspace:cached`), so all edits are
persistent by definition. Postgres and Redis use named volumes (`pgdata`,
`redisdata`) that also survive rebuilds. If you need another persistent
volume, add it to the `volumes:` block.

### Survive uvicorn reloads for in-memory state

Apps like FastAPI with `--reload` re-import modules on file changes, which
wipes module-level state (e.g. in-memory encryption keys). If you need
state to survive a reload, write it to a file under a bind-mounted path —
e.g., `.devcontainer/.keys/` with a matching env var:

```yaml
environment:
  KEYS_DIR: /workspace/.devcontainer/.keys
```

(The `.devcontainer/.gitignore` in this template already excludes `.keys/`.)

---

## Troubleshooting

### "Frontend: FAILED" right after container start

If you start background processes in `post-start.sh` without `< /dev/null`,
tools like Vite will detect the missing stdin and exit immediately. The
template uses the correct pattern:

```bash
nohup command < /dev/null > /tmp/output.log 2>&1 &
disown
```

Check `/tmp/frontend.log` (or whatever log file you chose) for the actual
error.

### "Address already in use"

Something on your host is bound to the port you're trying to forward. Either
change the host-side port in `docker-compose.yml`:

```yaml
- "0.0.0.0:18080:8080"  # host 18080 → container 8080
```

Or stop the conflicting process on the host.

### Database connection fails right after container start

`depends_on: condition: service_healthy` already waits for the Postgres
healthcheck to pass before the devcontainer starts, but if your migration
runs before the app-level connection pool is warm, you can add an explicit
`pg_isready` loop at the top of `post-start.sh` (example is in the script).

### VS Code can't find the Python interpreter

The Dockerfile puts Python at `/usr/local/bin/python`, and `devcontainer.json`
sets `python.defaultInterpreterPath` accordingly. If you installed Python a
different way (pyenv / conda / etc.), update that setting to match.

### Rebuild after editing Dockerfile

VS Code command palette → **Dev Containers: Rebuild Container**. This forces
a fresh image build. Use **Rebuild Without Cache** if you suspect a stale
layer.

### Claude Code or Cline can't reach the internet

The devcontainer has full outbound internet access by default. If you're
behind a corporate proxy, set `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` in
the `environment:` block of `docker-compose.yml`.

---

## License

This template is released as-is, no warranty. Copy it, fork it, modify it
freely.
