# E2E-CRITICAL-003: Shared-Env Smoke vs Local E2E Strategy

**Status:** Complete
**Version:** 2.11.0

---

## Problem

E2E tests can run against two very different targets:
1. **Local Docker Compose** — full stack on the developer's machine or CI runner
2. **Shared staging environment** — persistent instance shared by team

These require different test strategies, timeouts, data isolation, and
cleanup behavior.

---

## Strategy

### Tier 1: Local E2E (CI + Developer)

| Attribute | Value |
|-----------|-------|
| **Target** | `docker compose up` on CI runner or laptop |
| **Trigger** | Every PR, nightly, manual |
| **Duration** | Under 5 minutes |
| **Data isolation** | Fresh DB per run (Docker volume reset) |
| **Cleanup** | Automatic — containers torn down after run |
| **Auth** | Default `admin/changeme` credentials |
| **Specs** | `tests/00-*.spec.ts` through `tests/11-*.spec.ts` |
| **Parallelism** | Serial (single worker) to avoid port/resource conflicts |

**When to use:** Always. This is the primary E2E gate.

### Tier 1.5: Shared-Env Smoke (Staging)

| Attribute | Value |
|-----------|-------|
| **Target** | `https://staging.example.com` (persistent) |
| **Trigger** | Post-deploy webhook, manual |
| **Duration** | Under 2 minutes |
| **Data isolation** | Must NOT create/delete resources (read-only assertions) |
| **Cleanup** | None needed (tests are non-destructive) |
| **Auth** | Staging credentials from CI secrets |
| **Specs** | Subset: `00-smoke.spec.ts` + health checks only |
| **Parallelism** | Single worker |

**When to use:** After deploying to staging. Confirms the deployment is healthy
without disturbing shared data.

### Tier 2: Full Infrastructure (Manual / Nightly)

| Attribute | Value |
|-----------|-------|
| **Target** | Local Docker Compose + real AWS account |
| **Trigger** | Manual, nightly (02:00 UTC), release tags |
| **Duration** | 30-60 minutes |
| **Data isolation** | Unique project names with timestamps |
| **Cleanup** | Terraform destroy + resource verification |
| **Auth** | Default creds + AWS credentials from env |
| **Specs** | `tests/tier2/10-full-deployment.spec.ts` |
| **Parallelism** | Serial (shared state between tests) |

**When to use:** Before releases and nightly for regression detection.

---

## Test Selection Rules

### Shared-Env Safe Tests (Non-Destructive)

These tests can safely run against a shared staging environment:

```
00-smoke.spec.ts          # Login, health, nav — read-only
05-kubernetes-explorer.spec.ts  # Reads cluster state — non-destructive
06-helm-management.spec.ts      # Reads helm state — non-destructive
10-fleet-critical.spec.ts       # Reads fleet state — non-destructive
11-bnk-critical.spec.ts         # Reads BNK state — non-destructive
```

### Local-Only Tests (Destructive)

These tests create/delete resources and must only run locally:

```
01-project-lifecycle.spec.ts    # Creates/deletes projects
02-module-management.spec.ts    # Adds modules
03-stack-operations.spec.ts     # Stack interactions
04-deployment-workflow.spec.ts  # Triggers deployments
07-system-admin.spec.ts         # Modifies system settings
08-rbac.spec.ts                 # Creates/modifies users
09-error-handling.spec.ts       # Tests error states
tier2/10-full-deployment.spec.ts  # Real AWS resources
```

---

## CI Configuration

### PR Gate (Tier 1 Local)

```yaml
# Runs on every PR
- name: E2E Tests
  run: npx playwright test
  env:
    TEST_BASE_URL: https://localhost
    CI: true
```

### Post-Deploy Smoke (Tier 1.5 Staging)

```yaml
# Runs after staging deploy
- name: Staging Smoke
  run: npx playwright test tests/00-smoke.spec.ts
  env:
    TEST_BASE_URL: ${{ secrets.STAGING_URL }}
    TEST_ADMIN_USER: ${{ secrets.STAGING_ADMIN }}
    TEST_ADMIN_PASS: ${{ secrets.STAGING_PASS }}
```

### Environment Variable: `E2E_TARGET`

To make test selection automatic:

```bash
# Local (default) — runs all Tier 1 specs
E2E_TARGET=local npx playwright test

# Staging — runs only non-destructive specs
E2E_TARGET=staging npx playwright test

# Full — includes Tier 2
E2E_TARGET=full E2E_TIER=2 npx playwright test
```

---

## Data Isolation Guidelines

1. **Local:** Fresh DB guaranteed. Tests can create/delete freely.
2. **Staging:** Tests MUST be read-only. Use `test.skip()` for destructive tests
   when `E2E_TARGET=staging`.
3. **Tier 2:** Use timestamped project names (`e2e-test-${Date.now()}`).
   Clean up with Terraform destroy. Verify cleanup with AWS SDK.

---

## Timeout Adjustments by Environment

| Environment | Navigation | API Call | Heavy Page |
|-------------|-----------|----------|------------|
| Local | 15s | 10s | 30s |
| Staging | 30s | 20s | 45s |
| Tier 2 | 30s | 60s | 120s |
