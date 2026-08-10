# E2E-CRITICAL-001: Critical Workflow Selection

**Status:** Complete
**Version:** 2.11.0

---

## Objective

Select the minimum set of end-to-end workflows that prove the platform
delivers value and is safe to operate. These workflows should be runnable
in CI (Tier 1) against a real backend — no mocks.

---

## Selected Critical Workflows (7)

### CW-1: Login and Session Lifecycle
**Already covered by:** `00-smoke.spec.ts`
- Login with valid credentials
- Dashboard renders
- Sidebar navigation present
- Logout redirects to login
- Protected routes redirect unauthenticated users

### CW-2: Project + Module Lifecycle (CRUD)
**Already covered by:** `01-project-lifecycle.spec.ts`, `02-module-management.spec.ts`
- Create project
- Browse module catalog
- Add module to project
- View project detail with modules
- Delete project

### CW-3: Kubernetes Cluster Visibility
**Already covered by:** `05-kubernetes-explorer.spec.ts`
- K8s page loads
- Cluster list renders
- Resource listing works
- Detail panels open

### CW-4: Fleet Health and Multi-Cluster Awareness
**NEW — requires:** `10-fleet-critical.spec.ts`
- Fleet page loads
- Cluster health cards render
- DPF/DPU tab is accessible
- Config promotion wizard opens
- Empty state shows when no clusters

### CW-5: BNK Gateway Topology and Health
**NEW — requires:** `11-bnk-critical.spec.ts`
- BNK page loads with cluster selector
- Health dashboard renders
- Gateway topology view loads
- Resource sidebar navigation works
- Traffic flow view loads

### CW-6: Deployment Dispatch and Task Tracking
**Already covered by:** `04-deployment-workflow.spec.ts`
- Task history page loads
- Task list with filtering
- Task detail with logs
- Module action menu accessible

### CW-7: System Health and Diagnostics
**Already covered by:** `07-system-admin.spec.ts`
- System settings page loads
- Health status displays
- User management CRUD
- Credential templates accessible

---

## Coverage Matrix

| Workflow | Spec File | Tests | Status |
|----------|-----------|-------|--------|
| CW-1 Login/Session | `00-smoke.spec.ts` | 6 | Existing |
| CW-2 Project/Module | `01-*.spec.ts`, `02-*.spec.ts` | 12 | Existing |
| CW-3 K8s Visibility | `05-kubernetes-explorer.spec.ts` | 9 | Existing |
| CW-4 Fleet Health | `10-fleet-critical.spec.ts` | 7 | **NEW** |
| CW-5 BNK Gateway | `11-bnk-critical.spec.ts` | 8 | **NEW** |
| CW-6 Deployment | `04-deployment-workflow.spec.ts` | 6 | Existing |
| CW-7 System Health | `07-system-admin.spec.ts` | 11 | Existing |
| **Total** | | **59** | |

---

## Selection Criteria

1. **Value proof** — Does this workflow demonstrate the platform's core
   value proposition (IaC management, K8s visibility, BNK operations)?
2. **Safety gate** — Would a regression here be caught before users hit it?
3. **Breadth** — Does the set cover all major navigation areas?
4. **CI-safe** — Can this run in under 5 minutes against a local Docker stack?

---

## Out of Scope (deferred to E2E-CRITICAL-003/004)

- MCP tool invocation E2E (requires dedicated MCP test harness)
- Real Terraform apply/destroy (Tier 2 only)
- Multi-user concurrent session testing
- Performance/load testing
