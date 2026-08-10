# Development Guide

> Monorepo: Python 3.11 backend (FastAPI), TypeScript frontend (React 18 + Vite), Python MCP server, Kopf-based Kubernetes operator. All runtime services ship as Docker containers driven from `Makefile` + `docker-compose.yml`.

Everything below is tool-neutral: build, test, architecture, style, and workflow
conventions for anyone working on this repo. Contribution process lives in
[CONTRIBUTING.md](../CONTRIBUTING.md).

---

## Build / Lint / Test Commands

### Quick Validation (run before every commit)

```bash
make quick-check          # ~15s — ruff + mypy (core/schemas) + openapi types freshness
make pre-push             # ~90s — quick-check + ALL test suites in parallel (mirrors CI)
make push                 # pre-push + git push + gh run watch
```

`make pre-push` is **mandatory** before every push. CI is a validation gate, not a discovery channel.

### Backend (Python — FastAPI + pytest)

```bash
cd backend && source .venv/bin/activate && python -m pytest tests/ --tb=short -q          # all
cd backend && source .venv/bin/activate && python -m pytest tests/unit/test_schemas_system.py -v  # single file
cd backend && source .venv/bin/activate && python -m pytest tests/unit/test_schemas_system.py -k "test_valid_batch_update" -v  # single test

make test-backend-unit        # tests/unit/ — pure functions, schemas, no DB
make test-backend-component   # tests/component/ — service + DB, no HTTP
make test-backend-legacy      # flat tests/test_*.py — route/security tests
make test-contracts           # tests/contract/ — golden response-shape tests

cd backend && source .venv/bin/activate && python -m ruff check . --config pyproject.toml          # lint
cd backend && source .venv/bin/activate && python -m ruff check . --config pyproject.toml --fix    # autofix
cd backend && source .venv/bin/activate && python -m mypy core/ schemas/ --config-file pyproject.toml  # types
```

### Frontend (TypeScript — React + Vitest)

```bash
cd frontend-v2 && npm test -- --run                                              # all
cd frontend-v2 && npx vitest run src/hooks/__tests__/useSystem.test.ts           # single file
cd frontend-v2 && npx vitest run -t "fetches system health status"               # single test
cd frontend-v2 && npx vitest src/hooks/__tests__/useSystem.test.ts               # watch mode

cd frontend-v2 && npm run lint       # eslint
cd frontend-v2 && npx tsc --noEmit   # type check
cd frontend-v2 && npm run build      # build check (catches TS errors tsc may miss)
```

### MCP Server (Python — separate package)

```bash
cd mcp-server && PYTHONPATH=src python -m pytest tests/ --tb=short -q            # all
cd mcp-server && PYTHONPATH=src python -m pytest tests/test_tool_catalog.py -v   # single file
```

### Code Generation

```bash
make openapi-types         # regenerate openapi.json + frontend TypeScript types
make openapi-types-check   # CI-style check: fails if types are stale
# MUST run after changing any route, request model, or response model
```

### Deployment

```bash
make local-deploy          # laptop/macOS/Windows — bridge networking, https://localhost
make deploy                # Linux server — host networking
make deploy-backend        # rebuild + restart backend + workers (most common)
make deploy-frontend       # rebuild + restart frontend only
make upgrade-safe          # preferred server upgrade (preflight + strict verification)
make install               # DESTRUCTIVE first-time bootstrap — wipes all bnk-forge volumes
make mcp-readiness         # two-layer: container liveness + MCP runtime readiness
```

**CRITICAL:** The celery worker runs the same backend code (`tasks/`, `services/`).
`make build` and `make deploy-backend` both include `build-worker` automatically.
If you only run `make build-backend` without `make build-worker`, code changes
to tasks/services will NOT take effect in the running workers.

---

## Architecture

Single Docker Compose stack. Nginx proxy fronts React frontend + FastAPI backend; backend writes to Postgres, caches in Redis, fans tasks out to Celery workers, and talks to customer Kubernetes clusters either directly (kubeconfig) or via a bnk-operator agent running in the cluster.

```
Browser → Nginx (HTTPS + WS) → Frontend (React/Vite) + Backend (FastAPI)
                                            ↓
                               PostgreSQL / Redis / Celery / K8s (kubeconfig or bnk-operator)
                                            ↓
                                       MCP Server (REST wrapper, /mcp endpoint)
```

### Deployment modes

Two compose topologies layered from the same `docker-compose.yml`:
- **Server (Linux)**: `docker-compose.yml` only — `network_mode: host`, services bind directly to host ports.
- **Laptop (macOS/Windows)**: `docker-compose.yml` + `docker-compose.local.yml` overlay — bridge networking with port mappings.

The Makefile selects the right combination via `COMPOSE_SERVER` / `COMPOSE_LOCAL`. Never mix `make deploy` on a laptop or `make local-deploy` on a server.

---

## Project Structure

```
backend/                   # FastAPI + SQLAlchemy + Celery
  routes/                  # ~42 API route files (thin HTTP handlers)
  services/                # ~73 service files (business logic)
  schemas/                 # Pydantic request/response models
  models/                  # SQLAlchemy ORM models
  modules/                 # Python-defined deployment modules (bnk/, k8s/, bare_metal/, app/)
  core/                    # Auth, errors, config, encryption, cache
  tasks/                   # Celery tasks (async/scheduled work)
  alembic/                 # DB migrations (run via Alembic, not bare SQL)
  tests/unit/              # Pure unit tests (no DB)
  tests/component/         # Service + DB tests
  tests/contract/          # Golden response-shape tests
frontend-v2/               # React 18 + Vite + TailwindCSS + shadcn/ui
  src/hooks/               # React Query hooks (one per domain)
  src/lib/api/             # Axios API modules (one per domain)
  src/types/               # TypeScript types (hand-written + generated)
  src/components/          # UI components (shadcn/ui + custom)
  src/pages/               # Route pages (lazy-loaded)
mcp-server/                # Standalone MCP server (thin REST wrapper, /mcp endpoint)
  src/bnk_forge_mcp/       # Python package source
bnk-operator/              # Kopf-based Python agent run inside customer clusters
proxy/                     # Nginx config + TLS
cli/                       # CLI helpers
scripts/                   # Build/maintenance/upgrade (notably upgrade.sh, mcp_live_smoke.py)
vm-bnk-forge/              # cloud-init harness: fresh KVM/cloud VM that self-installs bnk-forge
```

### Backend layering

- `routes/` — thin HTTP handlers, one file per domain. **Every route must use `@handle_route_errors("description")`** and specify `response_model=` for typed responses.
- `services/` — business logic. Services take `db: Session` in `__init__`. Routes delegate here.
- `schemas/` — Pydantic v2 request/response models. **Schemas live in TWO places**: `backend/schemas/*.py` AND inline in `backend/routes/*.py` — check both.
- `models/` — SQLAlchemy ORM models.
- `modules/` — Python-defined deployment modules for the BNK stack. These replace the legacy OpenTofu modules. Dependency wiring and parallel execution are handled by `services/dependency_graph_service.py` + `services/execution/`.
- `core/` — auth middleware, domain errors, config, encryption, cache, structured logging, maintenance gate. **mypy is strict here and in `schemas/`** — everywhere else is gradual.
- `tasks/` — Celery tasks (async/scheduled work).

### Frontend layering

- `hooks/use*.ts` — React Query hook per domain; the canonical data-fetch layer.
- `lib/api/*.ts` — Axios functions called by hooks; all share the `apiClient` in `lib/api/client.ts`.
- `types/` — hand-written types + `api-generated.ts` (regenerated from `backend/openapi.json`).
- `components/ui/` — shadcn/ui primitives (Radix + Tailwind). Feature components live alongside pages.
- `pages/` — route pages, lazy-loaded via `router.tsx`.
- State: `@tanstack/react-query` for server state, `zustand` for client state.

### Other services

- `bnk-operator/` — Kopf-based Python agent that runs inside customer clusters. Talks to the backend via persistent WebSocket (default) or HTTP polling; executes K8s ops locally using its ServiceAccount. Key files: `main.py`, `command_handlers.py`, `control_plane_client.py`, `polling_client.py`.
- `mcp-server/` — standalone MCP server exposing backend routes as AI-callable tools (`/mcp` endpoint). Thin REST wrapper; its own `pyproject.toml` and test suite.

---

## Code Style

### Python (Backend + MCP Server)

- **Formatter/linter:** ruff (line-length 120, target Python 3.11)
- **Rules:** E, F, I (isort), N (pep8-naming), W, UP (pyupgrade)
- **Type checking:** mypy on `core/` and `schemas/` (strict); gradual elsewhere
- **Imports:** stdlib → third-party → first-party (`core`, `database`, `models`, `routes`,
  `services`, `schemas`, `tasks`, `modules`, `utils`) → relative
- **Naming:** files `snake_case.py`, classes `UpperCamelCase`, functions/vars `snake_case`,
  constants `SCREAMING_SNAKE_CASE`
- **Type hints:** Required on `core/` and `schemas/`. Use `X | None` (not `Optional[X]`),
  `dict[str, ...]`, `list[...]` (lowercase builtins, not `Dict`/`List`)
- **Error handling:** Use domain errors from `core.errors` (`NotFoundError`, `ValidationError`,
  `BadRequestError`, `UnauthorizedError`, `ForbiddenError`, `InternalError`).
  Every route must use `@handle_route_errors("description")` decorator.
- **Route pattern:** Thin HTTP handlers in `routes/`, delegate to service classes in `services/`.
  Services take `db: Session` in `__init__`. Routes use `response_model=` for typed responses.
- **Schemas:** Pydantic v2 `BaseModel`. Live in `backend/schemas/` AND inline in route files.
  Check BOTH locations. Request models often inline; response models in `schemas/`.

### TypeScript (Frontend)

- **Formatter/linter:** ESLint 9 (flat config) + Prettier + prettier-plugin-tailwindcss
- **Strict mode:** `"strict": true`, `noUnusedLocals`, `noUnusedParameters`
- **`no-explicit-any`:** ERROR level — no `any` types allowed
- **Unused vars:** `_` prefix to suppress (`argsIgnorePattern: "^_"`)
- **Imports:** Path alias `@/` → `src/`. Use `@/hooks/useSystem` not relative paths.
  Order: external packages → `@/...` internal → relative → types
- **Naming:** files `kebab-case.ts` / `PascalCase.tsx`, components `PascalCase`,
  hooks `useCamelCase`, functions/vars `camelCase`, constants `SCREAMING_SNAKE_CASE`,
  types/interfaces `PascalCase`
- **State:** `@tanstack/react-query` for server state, `zustand` for client state
- **API layer:** Hooks in `src/hooks/use*.ts` → API functions in `src/lib/api/*.ts`
  → shared `apiClient` (axios) from `src/lib/api/client.ts`
- **Components:** shadcn/ui (Radix primitives) + TailwindCSS in `src/components/ui/`
- **Mutation + polling pattern:** When a mutation triggers an async backend process whose status is tracked by a polling `refetchInterval` hook, the mutation's `onSuccess` MUST use `await queryClient.refetchQueries({ queryKey: ... })` (not `invalidateQueries`) for the query key that the polling hook reads. This ensures the fresh transitional status (e.g., "pending", "deploying") is in cache before the interval evaluator decides whether to poll. `invalidateQueries` is fine for other keys that don't feed polling hooks.

---

## Testing Conventions

### Backend Tests

- **Naming:** `test_{unit}_{condition}_{expectedResult}` (e.g. `test_build_failsWhenSchemeNotFound`)
- **Structure:** Arrange–Act–Assert. One behavior per test.
- **Markers:** `@pytest.mark.unit`, `@pytest.mark.component`, `@pytest.mark.full`
- **Unit** (`tests/unit/`): No DB. Pure functions, validators, schemas.
- **Component** (`tests/component/`): Service + in-memory DB. Use `tests/factories.py`.
- **Contract** (`tests/contract/`): Verify response shapes match Pydantic models.

### Frontend Tests

- **Stack:** Vitest + @testing-library/react + MSW 2.x for API mocking
- **Location:** Co-located as `__tests__/*.test.ts` next to source
- **CT-012 pattern (MANDATORY for hook tests):**
  1. Read the backend route file for the real response shape
  2. Read the Pydantic schema (check `backend/schemas/` AND inline in routes)
  3. MSW handlers must return the REAL response shape (not made-up data)
  4. Capture `request.json()` in MSW handlers and assert payload matches backend schema
- **QueryClient wrapper:** Every hook test needs `createWrapper()` with `retry: false, gcTime: 0`

---

## Key Gotchas

- **`response_model` silently drops fields** — Pydantic strips anything not in the declared schema, even if the route returns it. When adding a field to a response, update the schema.
- **Schemas live in TWO places** — always check both `backend/schemas/*.py` AND inline definitions in `backend/routes/*.py`.
- **K8s API returns `null`, not missing keys** — defensively use `(spec.get("ports") or [])` rather than truthy chaining.
- **OpenAPI + generated TS types must stay in sync** — run `make openapi-types` after any route/schema change; `make openapi-types-check` fails CI otherwise.
- **MCP body vs. query drift** — backend routes may require query params where the MCP server sends JSON body. Read the route signature before writing MCP wiring.
- **Frontend-only deploy misses backend changes** — use `make deploy` (or `make deploy-backend`) whenever backend code changed, not just `make deploy-frontend`.
- **Two module libraries** — `backend/modules/` holds Python-defined modules (current). Any references to OpenTofu/Terraform modules are legacy; the current stack has **zero OpenTofu dependency** for BNK deployments.
- **`make install` is destructive** — wipes all `bnk-forge` Docker volumes/images. Use `make upgrade-safe` or `make update` for in-place updates.
- **Image build/publish already exists** — building and pushing all six images is covered by the `Makefile` (`build`, `build-all`, `build-*`, `buildx-setup`, `push-images`, `dist`), `docker-bake.hcl`, and `.github/workflows/release.yml`. Extend these before adding any new build or publish logic (PR #213 re-implemented this from scratch without noticing).
- **Celery `include` list is high stomping risk** — `celery_app.py` has all task modules on a single line. Rewriting the line to add a module can silently drop another (regression `eb32caf2` dropped `tasks.ssh_tasks`). The regression guard test `tests/unit/test_celery_task_registration.py` AST-parses `task_dispatch.py` to catch this.
- **`invalidateQueries` + polling `refetchInterval` race condition** — When a mutation triggers an async backend process (deploy, discover, flash, etc.) and a polling hook uses a data-dependent `refetchInterval`, using `invalidateQueries` in `onSuccess` creates a race: the interval evaluator fires on stale cached data (old terminal status) and returns `false`, so polling never starts. Fix: use `await queryClient.refetchQueries()` for the specific key that feeds the polling hook. `invalidateQueries` is fine for non-polling queries.

---

## Git Workflow

- `staging` is the shared integration branch; create feature branches from `staging` and merge back to `staging`.
- `main` is protected and release-only — promotion path is `staging → main`.
- Conventional commits: `feat: / fix: / docs: / refactor: / test: / chore: / perf:`.
- Agent-generated branches use `agent/[item-id]`. Never force-push.
- Update docs in the same commit as code changes (API_REFERENCE.md, TESTING.md, USER_GUIDE.md, openapi.json).

```bash
make quick-check           # before every commit (~15s)
make pre-push              # before every push (~90s) — mirrors CI
make push                  # pre-push + git push + watch CI status
```
