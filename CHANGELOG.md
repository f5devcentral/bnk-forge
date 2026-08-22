# Changelog

All notable changes to BNK-Forge.

---

## v3.1.6 (2026-08-10) — 3.1.x line

Milestone `v3.1.6` — the last release before 4.0.0, and the initial public
release tag on `f5devcentral`. This mirror is squashed: the `v3.1.6` tag is a
single `feat: initial public release` commit, so there is no per-change history
behind it to link here. Work that came *after* this tag — including the
container-runner hardening series (#2, #123, #161) and the ADR-424 bare-metal/DPU
work — is part of 4.0.0, not v3.1.6, and is recorded under the 4.0.0 entry when
that release is cut.

> **Heads-up for the 4.0.0 upgrade — two breaking changes:**
>
> 1. **Container runner non-root gate:** it now refuses *named* users — an image
>    using the distroless-standard `USER nonroot` is rejected. Switch it to a
>    numeric uid. Use **`USER 1000`**: the workspace is mounted from the host
>    and chowned `1000:1000`, so uid 1000 is the only value that clears the gate
>    *and* can write it. A higher uid such as `65532` passes the non-root gate but
>    cannot write the workspace, so the step fails on its first write.
> 2. **`MCP_SERVICE_PASSWORD` becomes required in 4.0.0 (via bonnyr-f5 #188):**
>    starting with 4.0.0 the backend refuses to boot in staging/production if it
>    is unset or still a shipped default (`changeme` / `mcp-service-changeme`).
>    That boot-time check ships in #188 — it is *not* in the 3.1.x line and is
>    called out here only so the upgrade step is ready before #188 lands. Every
>    existing install still carries one of those defaults, so before upgrading to
>    4.0.0 **set `MCP_SERVICE_PASSWORD` to a real secret** (the same value the MCP
>    server receives as `BNK_FORGE_PASSWORD`); once #188 is in the tree, leaving
>    it at a default will `SystemExit` the stack at startup.
>
> **Merge ordering (integration dependency).** The dist-bundle wiring these two
> steps assume — the dedicated `mcp` service account for the bundled MCP server,
> and the `MCP_SERVICE_PASSWORD` boot check — arrives in **bonnyr-f5 #186** (service
> account + removal of the shipped `changeme` / `mcp-service-changeme` defaults) and
> **#188** (boot check). This release documents them forward-looking and is therefore
> sequenced to merge **with or after #186 + #188**. Merged ahead of them, the
> `MCP_SERVICE_PASSWORD` guidance is inert for the dist stack (the compose file does
> not pass that variable to the backend) and #186 will conflict in
> `user-pack/install-guide.html` — resolve by taking #186's credential model, not by
> re-adding the `changeme` default this guide describes as a stopgap.

## v3.0.1 — 3.0.x line

The first 3.x release after the 2.x line below (upstream tag dated 2026-04-09).
Bridged entry; this repo is a squashed public mirror, so the `v3.0.1` tag and its
per-change history live upstream, not here.

## v2.10.74 (2026-03-04) — TMM Debug Panel Enhancements: F5 Docs Commands, Netkvest, Bug Fix

### Bug Fixes
- **Config View UUID picker** — UUID picker now hides when clicking any other command card (tmctl, bdt_cli, raw exec). Previously it stayed visible after opening Config View.

### Frontend
- **7 new tmctl command cards** from F5 BNK debug sidecar docs: DOCA Flow Entries, DOCA Flow Forward, Flow Redirect, UDP Profile Stats, Protocol Inspection, DNS Cache, DoS Stats
- **2 new bdt_cli command cards**: L2 Forwarding (l2forward), TMM Health Check (gRPC connection check)
- **Netkvest connectivity section** — new UI with SNAT pool source, destination IP, ping/traceroute toggle for network connectivity checks from TMM
- **Config View** moved to its own "Configuration (configview)" section header for better organization
- **13 tmctl cards** (was 6), **5 bdt_cli cards** (was 3), plus netkvest section

### Tests
- 2 new component tests: UUID picker hiding behavior, netkvest connectivity check
- Updated command card section test for all new cards
- 1,839 tests across 227 files — all passing, TypeScript clean

---

## v2.10.73 (2026-03-03) — Fleet Page Fixes: BNK Version, Uptime, Navigation

### Backend
- **BNK version extraction** — fleet health now extracts BNK version from TMM/FLO pod container image tags (e.g. `spk-tmm:v1.23.1` → `1.23.1`)
- **Uptime from pod startTime** — computes cluster uptime from the oldest running TMM or FLO pod start time instead of relying on operator `uptime_seconds`
- **Pod startTime in pod_to_dict()** — added `startTime` field to pod discovery for uptime calculation
- **cluster_id in fleet response** — added `cluster_id` to `FleetOperatorHealth` for frontend deep linking

### Frontend
- **View Health** → navigates to `/bnk` page with cluster pre-selected (was: switched to Operators tab)
- **View Config** → navigates to `/kubernetes` page with cluster pre-selected (was: switched to Operators tab)
- **Operators tab** — removed ConnectClusterDialog, token generation UI, and token list. Replaced with kubeconfig-first info banner and "Add Cluster" button pointing to `/kubernetes`
- **Page description** updated: "Monitor fleet health across all clusters via kubeconfig"

---

## v2.10.72 (2026-03-03) — Kubeconfig-First Fleet Health (D3)

### Features
- **Kubeconfig-first fleet health** — rewrote `GET /api/operators/fleet-health` to query all `KubernetesCluster` records directly via kubeconfig instead of relying on operator health reports. Fleet health now works immediately for any added cluster — no operator installation required. Uses `fetch_all_bnk_data()` + `analyze_health()` with parallel queries (ThreadPoolExecutor, max 10 workers). Unreachable clusters gracefully marked as "offline". Operator metadata enriched when available.
- **Fleet compare endpoint** — tries cluster-based config diff first, falls back to operator health report comparison
- **Persistent agent pod** — upgraded ephemeral QKView/licensing pod from `sleep 3600` to `sleep infinity`, unified label `app=bnk-forge-agent` (was `bnk-forge-qkview-client`), added dual-label cleanup for migration

### Frontend
- **Fleet Overview tab** — "Connect Cluster" button replaced with "Add Cluster" (navigates to /kubernetes cluster management). Removed `ConnectClusterDialog` from Overview tab. Added "kubeconfig" connectivity mode label (green K8s badge). Updated empty state messaging for kubeconfig-first approach.

---

## v2.10.71 (2026-03-03) — K8s UX Sprint Complete + Licensing Legacy Fallback

### K8s UX Sprint (10/10 tasks)
- **K8S-UX-001 to K8S-UX-003** — initial K8s UX improvements
- **K8S-UX-004** — Helm integrated into K8s page (no more standalone Helm page)
- **K8S-UX-005** — Fleet + Operators merged into unified Fleet page with tabs
- **K8S-UX-006** — Activity renamed to "Operations Log"
- **K8S-UX-007** — Blueprints page simplified
- **K8S-UX-008** — Module Catalog layout refresh
- **K8S-UX-009** — Command Center dashboard rework
- **K8S-UX-010** — Unified operator pod with CWC integration (Decision D2)
  - Operator: 14 CWC commands via aiohttp mTLS (setup, QKView, licensing, generic)
  - Backend: 5 licensing endpoints, QKView operator dispatch with legacy fallback
  - Frontend: LicenseStatusCard, LicensingPanel, 6 React Query hooks, 14 new tests

### Features
- **Licensing legacy fallback** — licensing routes now use operator-first, legacy-fallback pattern (same as QKView). When no operator is connected, licensing operations fall back to the ephemeral curl pod infrastructure. Eliminates "operator required" blocker for license status viewing.
- **CWC raw body support** — `_cwc_request()` extended with `raw_body` parameter for CWC `/reactivate` (JWT) and `/receipt` (manifest) endpoints that require non-JSON payloads

### Bug Fixes
- **BUG-001, BUG-002** — resolved
- **LicensingPanel** — no longer shows OperatorRequiredBanner when legacy CWC path is available; shows contextual CWC connectivity status instead

---

## v2.10.70 (2026-03-02) — Tech Debt Cleanup

### Cleanup
- **Removed deprecated cost estimation feature** — deleted 13 files (routes, service, components, hooks, types, API client, tests), cleaned 17 files of cost references, added Alembic migration v2_042 to drop `cost_estimates` table and denormalized columns from `project_modules`
- **Removed deprecated sync endpoint** — deleted `POST /api/sync/trigger` tombstone route (returned 410 since v2.0.0) and its integration test
- **Removed Infracost config** — cleaned `INFRACOST_API_KEY` from `.env` and `.env.example`
- **Dashboard stat card** — replaced "Est. Cost" with "Modules" count (active module count was already fetched but not displayed)

---

## v2.10.70 (2026-03-01) — K8s First-Class Citizen Sprint Complete

### Features
- **Pod exec terminal** — WebSocket-based interactive shell (`/bin/sh`) into K8s containers with JWT auth, proper binary framing, and terminal resize support
- **Pod logs live streaming** — real-time log follow with auto-scroll, buffered line splitting, and `urllib3` stream iterator fix
- **QKView diagnostic collection** — full CWC integration with cert-manager setup, mTLS client certs, tar-stream binary download, and ephemeral client pod lifecycle
- **Auto-detect clusters** — after creating a K8s project, automatically discovers clusters from the connected operator
- **SSH auto-key-setup** — password-first UX with automatic Ed25519 key bootstrap for on-prem clusters

### K8s Sprint (16/16 tasks)
- **K8S-001 to K8S-005** — removed legacy SSH credential template references from all code paths
- **K8S-006 to K8S-009** — streamlined K8s cluster addition via SSH with auto-detect
- **K8S-010 to K8S-016** — feature parity with AWS, legacy cleanup, UX copy updates, OpenAPI regeneration

### Bug Fixes
- **Pod exec reconnect loop** — removed `connectionStatus` from `useCallback` deps (caused infinite WebSocket reconnects via effect re-fires); replaced with `useRef`
- **Distroless container handling** — catch `ValueError` from `WSClient.returncode` when exec fails on shell-less containers; show clear "no shell available" error
- **Pod logs scroll** — fixed `max-h` vs `h` flexbox overflow issue; used fixed `h-[90vh]` so `flex-1` children can calculate their share
- **Radix DialogContent warning** — suppressed `aria-describedby` console warning
- **QKView: 10+ fixes** — CWC pod label (`app=cwc`), curl connection failure detection, protobuf unknown field rejection, tar-stream binary encoding, server cert CA mismatch

---

## v2.10.67 (2026-02-27) — Docker Diet + Bulletproof Upgrades

### Docker Diet Sprint (13/14 tasks)
- **Multi-target Dockerfile** — split monolithic image into `api`, `worker`, `beat` targets; API image excludes git/tofu/helm/kubectl (150MB+ savings)
- **Infracost made opt-in** — `INSTALL_INFRACOST` build arg, no longer installed by default
- **Trimmed apt packages** — removed build-essential, python3-dev, and other unnecessary packages from production image
- **CI Docker layer caching** — `docker/build-push-action` with GitHub Actions cache backend
- **Image size gates** — CI fails if image exceeds threshold
- **Root `.dockerignore`** — excludes `.git`, `node_modules`, `__pycache__`, docs
- **SHA256 checksums** — all downloaded binaries verified against pinned checksums
- **Lazy boto3 imports** — CLI-only deps imported inside functions, not at module level (prevents API container crashes)

### Upgrade Sprint (14/14 tasks)
- **Bulletproof upgrade flow** — pre-upgrade safety checks, post-upgrade verification, global upgrade lock indicator
- **Upgrade state persistence** — stored in `ApplicationSetting` DB table, survives container restarts
- **Frontend upgrade UI** — `BnkUpgradePanel` with live progress, lock indicator, and rollback support

---

## v2.10.63 (2026-02-27) — Contract Testing Sprint

### Contract Testing (31 tasks, 4 phases)
- **OpenAPI type generation pipeline** — `scripts/generate-openapi.py` produces `openapi.json`, `openapi-to-ts` generates TypeScript types
- **264 payload assertion tests** — every frontend mutation now captures `request.json()` and asserts shape against backend Pydantic schemas
- **6 payload bugs found and fixed** — including `PUT /api/settings` body wrapping, `importClusterConfig` missing wrapper
- **Negative schema tests** — 8 test suites covering system, projects, K8s, Helm, operators, drift, stacks, auth domains
- **CI gates** — OpenAPI freshness check + TypeScript type check added to pipeline

---

## v2.10.60 (2026-02-26) — Multi-User Ownership

### Features
- **User management page** — admin-only `/users` with full CRUD, React Query hooks
- **Project ownership** — `user_id` FK on Project model, Alembic migration v2_041 with backfill
- **3 ownership dependencies** — `require_project_owner`, `require_module_owner`, `require_cluster_owner`
- **~80 endpoints enforced** — all mutating endpoints gated by ownership (admin bypasses)
- **Ownership transfer** — `POST /api/projects/{id}/transfer` with audit trail
- **"My Projects" filter** — sidebar toggle between owned and all projects

### Bug Fixes
- **Auth test regression** — `getUsers` returns `{users, total}` not array after MU-006
- **Radix focus-scope flaky** — `dispatchEvent` TypeError fix with `vi.useFakeTimers`
- **Cost estimate test** — create real module entity for `require_module_owner` dependency

---

## v2.10.53 (2026-02-25) — Test Coverage Expansion

### Testing
- **4,830+ tests total** (up from ~860) across backend and frontend
- **Frontend Phase 2** — 788 tests across 91 files (hooks, pages, dialogs, widgets, layouts, integration)
- **Backend Phase 3** — 92 integration route tests across 8 domains
- **Backend component tests** — 6 waves covering execution engine, services, and edge cases
- **E2E Phase 4** — 10 Tier 1 Playwright specs (51 tests), 7 page objects, Tier 1/2 split

### CI
- **Phased CI pipeline** — 4 phases (lint → component → integration → security/docker) with merge gates
- **8 parallel CI jobs** — including operator, proxy, and DB migration tests
- **Coverage thresholds** — frontend 15%, backend 40% (enforced in CI)
- **ESLint zero warnings** — 645 warnings eliminated across the frontend

### Bug Fixes
- **Memory leak** — kubeconfig temp files never deleted in health monitor task
- **SSH credential visibility** — `ssh_credential_id` missing from project API responses (Pydantic response model silently stripped it)
- **Ed25519 key generation** — switched from paramiko (lacks `.generate()`) to cryptography library

---

## v2.10.47 (2026-02-23) — Test Coverage Phase 2: Backend Unit Tests

### Testing
- **6 new backend unit test suites** covering core service modules:
  - `test_variable_assembler.py` — 47 tests (7-layer chain, transforms, JWT guard, destroy mode)
  - `test_opentofu_runtime.py` — 21 tests (workspace prep, init, plan, apply, destroy)
  - `test_secrets_service.py` — 25 tests (encrypt, decrypt, file upload, validation)
  - `test_kubernetes_engine.py` — 20 tests (apply, destroy, placeholder injection, KNOWN_PLURALS, error suggestions, helm ops)
  - `test_helm_service.py` — 21 tests (install, upgrade, rollback, uninstall, repo management)
  - `test_routes_execution.py` — 23 tests (plan, apply, destroy endpoints, auth, concurrency)
- **Total: 861 backend tests passing** (up from 704), 154 frontend tests passing
- Zero regressions

---

## v2.10.42 (2026-02-23) — Docker Image Security Fix (All 5 CI Jobs Green)

### Docker
- **Tool upgrades** — OpenTofu 1.11.4→1.11.5, Helm 3.17.0→3.20.0, kubectl 1.32.2→1.32.12, Infracost 0.10.39→0.10.43
- **Trivy scan now passes** — added `.trivyignore` for Go stdlib CVE-2025-68121 (no upstream fix available, all tools built with Go < 1.25.7) and CVE-2024-45337 (not exploitable in our context)
- **Infracost go-git fix** — v0.10.43 includes updated go-git dependency fixing CVE-2025-21613

### CI
- **All 5 CI jobs now pass:** Backend Tests, Frontend Checks, Security Audit, Docker Build, Docker Image Scan ✅

---

## v2.10.41 (2026-02-23) — CI Pipeline Fix

### Backend
- **Ruff lint clean** — fixed 4,700+ lint errors across 272 files (I001 import sorting, W293 whitespace, F401 unused imports, E722 bare excepts, E741 ambiguous variables, UP035 deprecated typing, F821 undefined names)
- **Per-file-ignores expanded** — `pyproject.toml` now properly ignores E402 for files with intentional deferred imports (main.py, celery_app.py, routes, services, tasks, modules)

### Frontend
- **ThemeProvider in test wrapper** — `test-utils.tsx` now wraps with ThemeProvider, fixing Login component tests
- **Login test assertions fixed** — updated to match actual component text and zod-based validation (not HTML required)
- **Hook error test timeouts fixed** — changed MSW error status codes from 502/503 (which trigger axios retry interceptor) to 500 for immediate error propagation

### CI
- All 4 CI jobs now pass: Backend Tests (ruff + pytest), Frontend Checks (lint + test + build), Security Audit, Docker Build

---

## v2.10.40 (2026-02-23) — Codebase Review & Architecture Improvements

### Architecture
- **BaseService pattern** — `services/base_service.py` with shared `get_or_raise()`, `_get_project()`, `_get_module()`, `_get_cluster()` helpers; 5 services refactored to extend it
- **ProjectCRUDService** — extracted ~500 lines from fat `routes/projects.py` controller into dedicated service
- **Project model decomposition** — extracted `ProjectBackendConfig`, `ProjectCloudConfig`, `ProjectEncryptionConfig`, `ProjectInfraConfig` from 64-column god-model via 4 new Alembic migrations (v2_035–v2_038)
- **Association tables** — replaced JSON columns (`deployed_modules`, `successful_modules`, etc.) with proper relational tables
- **Startup decomposition** — `main.py` lifespan split into testable `startup_steps.py`
- **16 string enums** — `models/enums.py` with type-safe `(str, Enum)` classes for all status fields (no DB migration needed)

### Backend
- **Celery retry config** — `autoretry_for`, `retry_backoff`, `retry_jitter` on all infrastructure tasks
- **Session management migration** — all Celery tasks use `get_db_context()` context manager
- **CallbackTask consolidation** — 3 duplicate implementations merged into one (bug fix)
- **Silent exception swallowing fixed** — task files now properly log and re-raise
- **Pydantic schemas** — new `schemas/drift.py` and `schemas/stacks.py` for typed API responses
- **Dead AWS SSO columns removed** — Alembic migration v2_035
- **Codebase review document** — `docs/CODEBASE_REVIEW.md` with prioritized findings

### Frontend
- **K8s hooks split** — monolithic `useK8s.ts` (859 lines) decomposed into 6 domain-specific hooks (`useBnk`, `useClusterCRUD`, `usePods`, `useResources`, `useRollouts`, `useTunnels`)
- **react-hook-form + zod** — `CreateProjectDialog` wired with schema validation
- **Frontend logger** — `lib/logger.ts` for structured client-side logging
- **Constants centralized** — `lib/constants.ts` for shared magic values

### CI/CD
- **Security audit enforcement** — CI pipeline now fails on security audit issues (IMP-024)
- **Docker compose override** — `docker-compose.override.example.yml` for dev customization

### Branch Cleanup
- Merged PR #40 (codebase review fixes) and PR #41 (BaseService + model extraction)
- Resolved 43 merge conflicts across overlapping PRs
- Merged `agent/sprint-15-phase-4` (8 LOW priority fixes) and `agent/sprint-15-e2e-tests` (E2E tests)
- Deleted all feature branches — main is the only branch

---

## v2.10.32 (2026-02-19) — Safety & Integration Fixes

### Backend
- Fixed CRD plural derivation — moved `KNOWN_PLURALS` to a shared module-level dict in `kubernetes_engine.py`, used by config export, config promotion, and snapshot restore (previously each site guessed with `kind.lower() + "s"`, breaking hyphenated CRDs like `network-attachment-definitions`)
- Added JWT authentication to WebSocket endpoints (`pod exec`, `log follow`) — validates token query param before accepting the connection, closes with `4401` on failure
- Removed `/health` from operator polling bypass list in auth middleware (operator health checks now go through standard JWT auth)
- Fixed transaction boundaries in config promotion — helper `_log_promotion` now flushes instead of committing; route-level `db.commit()` added to both success and error paths
- Changed audit user fallback from `"admin"` to `"system"` in operators and config promotion routes
- Fixed operator install YAML — `CONTROL_PLANE_URL` now resolves dynamically from ngrok tunnel or request Host header instead of the placeholder `<BNK_FORGE_HOST>`
- Implemented terminal resize for pod exec WebSocket via K8s exec channel 4

### Frontend
- Fixed drift check history — response is a flat array, not `{ checks: [] }`
- Fixed Helm chart values dialog — removed extra `.data` nesting on `valuesData` access
- Fixed credentials API extractor — added missing `.then(res => res.data)`
- Fixed Helm values API calls — added proper type annotations and `.then(res => res.data)` extractors
- Added `ErrorBoundary` component — catches chunk-load failures with a "new version available" prompt, generic error recovery, and a `NotFound` (404) catch-all route
- Added error states to Fleet health page and Snapshot history (shows message instead of silent failure)
- Removed dead `useWebSocket` hook and its 184-line test file (WebSocket connections are handled by `NotificationProvider` and the new authenticated WS endpoints)

### Cleanup
- Replaced `console.log` / `console.error` calls with user-facing toast notifications in `KubernetesV2`, `ProjectDetailV2`, `ModuleOutputsViewer`, `NotificationProvider`, and `SSOAuthDialog`
- Fixed Command Palette navigation — `/deployments` → `/tasks` (matching current route structure)
- Disabled placeholder "Environment settings" menu item (was logging to console on click)
- Tightened `moduleToAction` type from `any` to `ProjectModule | null` across project detail components

---

## v2.10.6 (2026-02-18) — Documentation Overhaul & Vision

### Documentation
- **New:** `docs/PRODUCT_VISION.md` — Where BNK-Forge is heading (three horizons, success metrics, architecture principles)
- **New:** `docs/UX_ROADMAP.md` — Making K8s + network functions ridiculously easy (4 phases, detailed UX improvements with progressive disclosure)
- **New:** `docs/ENGINEERING_IMPROVEMENTS.md` — Technical debt, testing strategy, reliability improvements (12 prioritized items)
- **New:** `docs/archive/OPERATOR_GUIDE.md` — Operator deployment guide retained as archived reference after kubeconfig-first became the primary path
- **Updated:** README.md — New identity ("Deploy, Operate, Monitor BNK"), updated architecture diagram, v2.10.6 version, restructured features around Day 1/Day 2
- **Updated:** Documentation sweep at the time refreshed INSTALLATION, USER_GUIDE, TROUBLESHOOTING, and `docs/architecture/README.md`
- **Updated:** Architecture README now links to new vision docs

### Cleanup
- **Removed:** `configs/README.md` — referenced Terragrunt/TerraDash (dead project names, wrong tool)
- **Removed:** `configs/example/sample-module/terragrunt.hcl` — legacy sample from v1
- **Removed:** Root `TROUBLESHOOTING.md` — was a dev session log, not user documentation
- **Removed:** `backend/seed_stack_templates.py` — dead code, replaced by `services/stack_template_seed.py`

---

## v2.10.0 (2026-02-17) — Sprint 8: Enterprise Readiness

### Sprint 8.3 — Operator Production Hardening (`30f3204`)
- Operator TLS: SSL context for wss/https, CA cert support, `TLS_INSECURE` for dev
- Polling auth: `register-poll` endpoint, token validation on all polling paths
- Heartbeat: exponential backoff with jitter (±30%), missed heartbeat detection, stale cleanup
- Multi-cluster fan-out: `send_command_to_multiple()`, fan-out API (by IDs/labels/all)
- Metrics: 8 Prometheus-compatible metrics, structured JSON logging (`LOG_FORMAT=json`)
- Health watcher: shared handlers, configurable intervals
- Helm chart v1.1.0: TLS, ServiceMonitor, PDB, NetworkPolicy, TLS CA Secret
- Celery Beat: `operator-cleanup` task every 2 minutes
- Tests: 42 new (token, connection, fan-out, auth, schemas)

### Sprint 8.2 — BNK Upgrade Workflow (`5d94cb3`)
- `BnkUpgrade` model with full status lifecycle (planning→ready→in_progress→completed/failed/rolled_back)
- `BnkUpgradeService` (~650 lines): version parsing, pre-checks, 9-step upgrade plan, rolling execution, health gates, rollback
- 8 API endpoints for version listing, plan creation, execution, rollback, history
- `BNKUpgradePanel` frontend component (~470 lines) with live progress
- Tests: 54 new (version parsing, pre-checks, plan generation, health evaluation)

### Sprint 8.1 — RBAC Enforcement (`1747cf0`, `83d7139`)
- `require_role()` dependency factory applied to ALL 200+ routes across 22 files
- Frontend: `RoleGuard` component, `useRole` hook, role-filtered sidebar
- Health endpoint fix: `/api/system/health` exempt from admin requirement
- Config export rewritten to use kubernetes client (removed kr8s dependency)

---

## v2.9.0 (2026-02-16) — Sprint 6-7: Day 2 Operations

### Sprint 7.3 — Config Export (`cb28691`)
- Export 27 BNK resource types as YAML/JSON snapshots
- Import config to target cluster via server-side apply
- Diff two cluster configurations

### Sprint 7.2 — Drift Detection (`cb28691`)
- K8s-engine drift: renders desired manifests, fetches live state, diffs spec-level fields
- Helm drift: compares rendered values against deployed release values
- Alert wiring: fires `drift_detected` events to configured channels
- Dashboard drift banner with per-module status

### Sprint 7.1 — Webhook Alerting (`2e4d0c2`)
- `AlertChannel` model: webhook/slack/msteams/email, rate limiting, delivery stats
- Alert dispatch service: Slack Block Kit, MS Teams Adaptive Cards, generic webhook
- Health monitor task (60s Celery beat): fires alerts on severity change
- AlertChannels UI on System page

### Sprint 6.3 — Region Rename (`2e4d0c2`)
- Alembic migration: `aws_region` → `region` on projects, credential_templates, environments
- 40+ backend + 30+ frontend references updated
- Backward compat: OpenTofu modules still receive `aws_region` (aliased)

### Sprint 6.1 — Dead Code Removal (`2e4d0c2`)
- Deleted `AWSAuthConfig.tsx` (283 lines, never imported)
- Renamed ~15 AWS-branded labels to provider-neutral
- Removed hardcoded `us-west-2`/`us-east-1` defaults from 12 files

---

## v2.8.1 (2026-02-16) — Weeks 1-5: Foundation

### Week 5 — Provider-Neutral Refactor
- `project_type` model field with conditional Create Project dialog
- On-prem stack template ("F5 BNK 2.2 on On-Premises (Bare Metal / VM)")
- Provider-neutral error messages, workflow guides, location badges

### Week 4 — Operator + Connectivity
- Operator skeleton (~750 lines): 8 command handlers, Helm chart, health monitoring
- 5 connectivity modes: direct WS, reverse SSH, polling, ngrok, in-cluster
- Cluster linking UI, OperatorEngine for operator-mode deployments
- SSH tunnel management: per-cluster SSH credentials, managed tunnels in backend container
- Cluster scanner (16 parallel K8s API calls), adaptive module selection
- Built-in module catalog (self-registering at startup, no Git sync)

### Week 2-3 — K8s Engine + Health Dashboard
- 20 Python module definitions (18 manifest + 2 Helm) — zero OpenTofu for BNK
- K8s engine via kr8s (server-side apply), 7-layer variable assembler
- BNK Health Dashboard (default landing page)
- Helm OCI registry auth for repo.f5.com
- 364 unit tests

### Week 1 — Auth + Quick Wins
- JWT authentication (24h tokens, HS256, admin/operator/viewer roles)
- Visual refresh: blue accent, dark mode default, zinc grays
- Audit trail: middleware, user attribution, paginated UI
- 17 quick wins: dead code, DRY Docker Compose, version management, error consistency
