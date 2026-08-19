# Branch Protection Setup

## Required GitHub Settings

Go to: **Settings → Branches → Branch protection rules**

### Rule 1: Protect `main`

| Setting | Value |
|---------|-------|
| Branch name pattern | `main` |
| Require a pull request before merging | ✅ |
| Required approvals | 1 (adjust as needed) |
| Require status checks to pass before merging | ✅ |
| **Required status checks** | **`CI Gate`** |
| Require branches to be up to date | ✅ |
| Require conversation resolution | ✅ |
| Do not allow bypassing | ✅ (optional) |

### Rule 2: Protect `staging`

| Setting | Value |
|---------|-------|
| Branch name pattern | `staging` |
| Require a pull request before merging | ✅ |
| Required approvals | 1 (adjust as needed) |
| Require status checks to pass before merging | ✅ |
| **Required status checks** | **`CI Gate`** |
| Require branches to be up to date | ✅ |

### How to configure required status checks

1. Go to **Settings → Branches → Add rule**
2. Enter branch name pattern
3. Check "Require status checks to pass before merging"
4. In the search box, type `CI Gate` — this is the job name from `ci.yml`
5. Select it as required
6. Save

> **Important:** The `CI Gate` job won't appear in the search until the workflow
> has run at least once. Push a commit to trigger CI first, then configure protection.

## CI Pipeline Phases

```
Phase 1 (P1): Lint + Unit + Contract    ~60s   [automatic, blocks merge]
    ↓
Phase 2 (P2): Component + Legacy Tests  ~90s   [automatic, blocks merge]
    ↓
Phase 3 (P3): Integration Tests         ~2m    [automatic, blocks merge]
    ↓
Phase 4 (P4): Security + Docker         ~3m    [automatic, blocks merge]
    ↓
CI Gate: ✅ All phases passed → merge allowed

Phase 5 (P5): E2E Tests                 ~10m   [manual/nightly, advisory]
    ↓
Release: Version bump + tag + changelog          [manual, requires CI Gate]
```

## Status Checks Explained

| Check Name | Required for Merge | Notes |
|------------|-------------------|-------|
| `CI Gate` | ✅ **Yes** | Aggregates P1-P4, the only check you need |
| `P1 · Lint Backend` | No (aggregated) | Backend ruff lint via `make lint-backend` |
| `P1 · Lint Frontend` | No (aggregated) | Frontend eslint via `make lint-frontend` |
| `P1 · Unit Tests · Backend` | No (aggregated) | Unit tests via `make test-backend-unit` |
| `P1 · Unit Tests · Frontend` | No (aggregated) | Vitest via `make test-frontend` |
| `P1 · Unit Tests · Operator` | No (aggregated) | Operator pytest via `make test-operator` |
| `P1 · Contract Tests` | No (aggregated) | Response-shape tests via `make test-contracts` |
| `P1 · MCP Server Tests (advisory)` | ❌ No | Advisory only — failure does not block merge |
| `P2 · Component Tests · Backend` | No (aggregated) | Service+DB tests via `make test-backend-component` |
| `P2 · Legacy Tests · Backend` | No (aggregated) | Flat test files via `make test-backend-legacy` |
| `P2 · Proxy Config` | No (aggregated) | Nginx config via `make test-proxy` |
| `P2 · DB Migrations` | No (aggregated) | Migration tests via `make test-db` |
| `P2 · Build · Frontend` | No (aggregated) | Build check via `make build-frontend-check` |
| `P3 · Integration Tests` | No (aggregated) | Integration tests via `make test-integration` + `make test-integration-full` (complementary marker sets — both are needed to cover `tests/integration/`) |
| `P4 · Security Audit` | No (aggregated) | pip-audit + npm audit via `make security-audit` |
| `P4 · Docker Build + Scan` | No (aggregated) | Docker build + Trivy scan |
| `P5 · E2E Tests` | ❌ No | Manual/nightly only |

## Makefile Source-of-Truth

All lint/test/build commands live in `Makefile` targets. CI YAML only handles:
checkout, runtime setup (setup-python/setup-node with caching), and `make <target>`.

This means **`make pre-push` locally ≡ CI pipeline** — if pre-push passes, CI will pass.

### Key Makefile targets

| Target | Description |
|--------|-------------|
| `make quick-check` | Fast ~15s: lint + mypy + openapi types |
| `make pre-push` | Full ~90s parallel: quick-check + all test suites |
| `make test-backend-unit` | Backend unit tests (tests/unit/) |
| `make test-backend-component` | Backend component tests (tests/component/) |
| `make test-backend-legacy` | Backend legacy tests (excludes contract/) |
| `make test-contracts` | Golden contract tests (tests/contract/) |
| `make test-mcp` | MCP server tests (advisory) |
| `make security-audit` | Python pip-audit + npm audit |
| `make docker-check` | Docker build + size threshold verification |

## CI Triggers

| Event | Branches | Notes |
|-------|----------|-------|
| Pull Request | `main`, `staging`, `develop` | Main gate for feature branches |
| Push | `main` | Post-merge deploy triggers |
| Manual | — | Release workflow, E2E tests |
| Nightly | — | E2E tests at 02:00 UTC |
| Release tags | `v*` | E2E tests on release |

### Path-based change detection

CI uses `dorny/paths-filter` to skip irrelevant jobs:

| Filter | Paths |
|--------|-------|
| `backend` | `backend/**`, `requirements*.txt`, `openapi.json` |
| `frontend` | `frontend-v2/**` |
| `operator` | `bnk-operator/**` |
| `mcp` | `mcp-server/**` |
| `docker` | `backend/Dockerfile`, `frontend-v2/Dockerfile`, `docker-compose*.yml` |
| `ci` | `.github/workflows/**`, `Makefile` |

## Workflow Files

| File | Purpose | Trigger |
|------|---------|---------|
| `.github/workflows/ci.yml` | Phases 1-4 + CI Gate | Push, PR |
| `.github/workflows/e2e-tests.yml` | Phase 5 E2E tests | Manual, nightly, tags |
| `.github/workflows/release.yml` | Release pipeline | Manual only |

## Release Process

1. Ensure CI Gate has passed on your branch
2. Go to **Actions → Release → Run workflow**
3. Choose version bump type (patch/minor/major)
4. Optionally enable E2E tests before release
5. Enter release notes
6. Click "Run workflow"

The release workflow will:
- ✅ Verify CI has passed
- ✅ Run E2E tests (if selected)
- ✅ Bump VERSION file
- ✅ Update CHANGELOG.md
- ✅ Commit, tag, push
- ✅ Create GitHub Release
