# Strategic Backlog

> Backlog-ready strategic initiatives for BNK Forge v2. This document preserves the durable planning inventory while the main backlog stays lean and execution-focused.

Last updated: 2026-03-28 | Status: Active

---

## How to Use This Document

- Use this as the source inventory for strategic planning.
- Pull items into `.agent/backlog/BACKLOG.md` when they become active work.
- Keep the main backlog focused on current/ready execution items.
- Update this document when strategic priorities, sequencing, or ticket boundaries change.

---

## Now / Next / Later Sequencing

### Now

These items provide the highest leverage for platform trust and AI-operable maturity.

#### Platform trust foundations — ✅ DONE (Work Package A, commit `546bbce`)
- ~~**PLAT-REL-001 — Canonical Status Semantics**~~
- ~~**PLAT-REL-002 — Diagnostic Payload Standardization**~~
- ~~**PLAT-REL-003 — Truthful Status Surface Audit**~~

#### Contract foundations — ✅ DONE (Work Package B, commit `700b63a`)
- ~~**API-CONTRACT-001 — Endpoint Contract Tiering**~~
- ~~**API-CONTRACT-002 — Tier 1 Response Model Coverage Plan**~~
- ~~**API-CONTRACT-004 — Golden Contract Test Matrix**~~

#### MCP foundations — ✅ DONE (Work Package C, commits `15407ac`–`9f338d4`)
- ~~**MCP-PROD-001 — Tool Catalog Inventory**~~
- ~~**MCP-PROD-002 — Tool Taxonomy and Naming Standard**~~
- ~~**MCP-PROD-003 — Tool Safety Classification**~~
- ~~**MCP-PROD-004 — Route Mapping Verification**~~
- ~~**MCP-PROD-005 — Critical Tool Contract Matrix**~~

#### Deployment and observability minimums — ✅ DONE (Work Package D, 2026-03-28)
- ~~**DEPLOY-001 — Release Checklist Template**~~ → `docs/DEPLOY-001_RELEASE_CHECKLIST.md`
- ~~**DEPLOY-003 — Post-Deploy Verification Workflow**~~ → `docs/DEPLOY-003_POST_DEPLOY_VERIFICATION.md`
- ~~**OBS-001 — Request and Job Correlation Strategy**~~ → `backend/core/correlation.py` + middleware
- ~~**OBS-002 — Structured Log Schema**~~ → `docs/OBS-002_STRUCTURED_LOG_SCHEMA.md`
- ~~**OBS-004 — Error Taxonomy**~~ → `docs/OBS-004_ERROR_TAXONOMY.md`

### Next

These items deepen supportability, consistency, and operational leverage once the foundations above are in place.

#### Reliability and UX — ✅ ALL DONE (2026-03-28)
- ~~**PLAT-REL-004 — Operational Risk Surfacing**~~ ✅ → `docs/specs/PLAT-REL-004_OPERATIONAL_RISK_SURFACING.md`
- ~~**PLAT-REL-005 — Release Smoke Suite Definition**~~ ✅ → `scripts/mcp_live_smoke.py` + DEPLOY-003
- ~~**UX-OPS-001 — Async State UX Standard**~~ ✅ → `docs/specs/UX-OPS-001_ASYNC_STATE_STANDARD.md`
- ~~**UX-OPS-002 — Status Badge Vocabulary and Visual Semantics**~~ ✅ → `docs/specs/BADGE_SEMANTICS.md`
- ~~**UX-OPS-003 — Shared Diagnostic Panel Pattern**~~ ✅ → `docs/specs/UX-OPS-003_DIAGNOSTIC_PANEL_PATTERN.md`

#### Contracts and telemetry — ✅ ALL DONE (2026-03-28)
- ~~**API-CONTRACT-003 — Schema Ownership Convention**~~ ✅ → `docs/specs/API-CONTRACT-003_SCHEMA_OWNERSHIP.md`
- ~~**API-CONTRACT-005 — OpenAPI Diff Review Workflow**~~ ✅ → `docs/specs/OPENAPI_DIFF_REVIEW_WORKFLOW.md`
- ~~**OBS-003 — Metrics Coverage Plan**~~ ✅ → `docs/specs/OBS-003_METRICS_COVERAGE_PLAN.md`
- ~~**OBS-005 — Troubleshooting Dashboard/Query Requirements**~~ ✅ → `docs/specs/OBS-005_TROUBLESHOOTING_DASHBOARD.md`

#### MCP supportability — ✅ ALL DONE (2026-03-28)
- ~~**MCP-PROD-006 — MCP Compatibility and Deprecation Policy**~~ ✅ → commit `9f338d4`
- ~~**MCP-PROD-007 — MCP Telemetry Requirements**~~ ✅ → OBS-001 correlation + MCP observability proxy
- ~~**MCP-PROD-008 — MCP Error Semantics Standard**~~ ✅ → MCP error envelope + OBS-004 taxonomy
- ~~**MCP-PROD-009 — MCP Operator Guide**~~ ✅ → `docs/specs/MCP-PROD-009_OPERATOR_GUIDE.md`

#### Governance — ✅ ALL DONE (2026-03-28)
- ~~**SEC-GOV-001 — Operation Risk Classification Matrix**~~ ✅ → `docs/specs/SEC-GOV-001_OPERATION_RISK_MATRIX.md`
- ~~**SEC-GOV-002 — Audit Event Requirements**~~ ✅ → `docs/specs/SEC-GOV-002_AUDIT_EVENT_REQUIREMENTS.md`
- ~~**SEC-GOV-003 — Secret Boundary Review**~~ ✅ → `docs/specs/SEC-GOV-003_SECRET_BOUNDARY_REVIEW.md`

### Later

These items become more valuable after core trust, contracts, and observability work is underway.

#### UX and deployment maturity
- ~~**UX-OPS-004 — Information Hierarchy Review**~~ DONE (2026-03-30)
- ~~**UX-OPS-005 — Accessibility Pass for Operational Status UX**~~ DONE (2026-03-30)
- ~~**DEPLOY-002 — Environment Maturity Ladder**~~ DONE (2026-03-30)
- ~~**DEPLOY-004 — Rebuild/Impact Matrix Refresh**~~ DONE (2026-03-30)
- ~~**DEPLOY-005 — Rollback Rehearsal Plan**~~ DONE (2026-03-30)

#### Contract and governance maturity
- ~~**API-CONTRACT-006 — Generated Type Strategy**~~ DONE (2026-03-30)
- ~~**SEC-GOV-004 — New Tool/Endpoint Safety Review Checklist**~~ DONE (2026-03-30)

#### Critical-path end-to-end proof
- ~~**E2E-CRITICAL-001 — Critical Workflow Selection**~~ DONE (2026-03-30)
- ~~**E2E-CRITICAL-002 — Realistic Fixture Matrix**~~ DONE (2026-03-30)
- ~~**E2E-CRITICAL-003 — Shared-Env Smoke vs Local E2E Strategy**~~ DONE (2026-03-30)
- ~~**E2E-CRITICAL-004 — MCP End-to-End Sanity Coverage Plan**~~ DONE (2026-03-30)

#### Advanced MCP maturity
- ~~**MCP-PROD-010 — Autonomous-Use Eligibility Rules**~~ DONE (2026-03-30)
- ~~**MCP-PROD-011 — Curated High-Value AI Workflows**~~ DONE (2026-03-30)
- ~~**MCP-PROD-012 — MCP Readiness Scorecard**~~ DONE (2026-03-30)

---

## Full Ticket Inventory

## PLAT-REL — Platform Reliability Hardening

### PLAT-REL-001 — Canonical Status Semantics
**Priority:** P0

**Goal**
Create a shared status model used across clusters, fleet, BNK health, and connectivity flows.

**Scope**
- Define canonical status vocabulary.
- Map current backend-produced states to canonical states.
- Identify UI badge and tooltip implications.

**Definition of done**
- Written status model exists.
- Affected backend/frontend surfaces are inventoried.
- Drift/gap list is documented for implementation.

### PLAT-REL-002 — Diagnostic Payload Standardization
**Priority:** P0

**Goal**
Define a reusable diagnostic payload contract for health-like APIs.

**Scope**
- Standard fields for message, severity, suggestion, evidence, timestamp, source.
- Apply design to connectivity, cluster health, and fleet summaries.

**Definition of done**
- Shared diagnostic schema proposal exists.
- Candidate adoption endpoints are listed.
- Example payloads cover healthy, degraded, blocked, and unknown states.

### PLAT-REL-003 — Truthful Status Surface Audit
**Priority:** P0

**Goal**
Audit operator-facing status surfaces for false-green or ambiguous states.

**Scope**
- Cluster list/detail
- Fleet health
- BNK health dashboard
- Recovery/diagnostic views

**Definition of done**
- Audit findings documented with affected screens/endpoints.
- Findings prioritized by operator impact.

### PLAT-REL-004 — Operational Risk Surfacing
**Priority:** P1

**Goal**
Ensure high-impact operational blockers are surfaced clearly in product UX and health outputs.

**Scope**
- Expired license visibility
- Port-blocked visibility
- Auth failure visibility
- Dependency-unavailable visibility

**Definition of done**
- List of high-impact blockers and intended surfacing locations.
- Severity guidance defined.

### PLAT-REL-005 — Release Smoke Suite Definition
**Priority:** P1

**Goal**
Define the minimum production-truth smoke suite required after deploy.

**Scope**
- Health endpoint
- Login/auth sanity
- Cluster list/connectivity
- BNK topology/health
- MCP health/tool sanity

**Definition of done**
- Smoke suite specification exists.
- Each check has pass/fail criteria and owner.

---

## API-CONTRACT — Critical API Contract Rigor

### API-CONTRACT-001 — Endpoint Contract Tiering
**Priority:** P0

**Goal**
Classify API endpoints by operational importance to sequence contract hardening work.

**Scope**
- Define Tier 1/2/3 rubric.
- Assign tiers to major route groups.

**Definition of done**
- Tiering rubric exists.
- Critical endpoints are classified and published.

### API-CONTRACT-002 — Tier 1 Response Model Coverage Plan
**Priority:** P0

**Goal**
Create a concrete plan to add explicit response models to Tier 1 endpoints.

**Scope**
- Auth
- Clusters
- Connectivity
- Fleet
- BNK topology/health

**Definition of done**
- Endpoint-by-endpoint plan exists.
- Current gaps and blockers are identified.

### API-CONTRACT-003 — Schema Ownership Convention
**Priority:** P1

**Goal**
Reduce ambiguity between inline route schemas and shared schema modules.

**Scope**
- Document ownership rules.
- Define when inline models are allowed.
- Define where public request/response contracts should live.

**Definition of done**
- Convention doc/update exists.
- At least one example per pattern is documented.

### API-CONTRACT-004 — Golden Contract Test Matrix
**Priority:** P1

**Goal**
Define a small set of exact response-shape tests for the most important APIs.

**Scope**
- Select 10–20 critical endpoints.
- Identify expected shape ownership and fixture approach.

**Definition of done**
- Matrix exists with route, owner, and expected assertions.

### API-CONTRACT-005 — OpenAPI Diff Review Workflow
**Priority:** P1

**Goal**
Require visibility and review for public API contract changes.

**Scope**
- Define diff workflow in CI.
- Define review expectations.
- Define exceptions for internal/non-contract changes.

**Definition of done**
- Workflow and reviewer policy are documented.

### API-CONTRACT-006 — Generated Type Strategy
**Priority:** P2

**Goal**
Define whether and how TypeScript types/clients should be generated from OpenAPI.

**Scope**
- Tooling options
- Generation boundaries
- Coexistence with hand-written types during migration

**Definition of done**
- Decision memo exists with phased migration guidance.

---

## OBS — Observability and Traceability

### OBS-001 — Request and Job Correlation Strategy
**Priority:** P1

**Goal**
Define correlation IDs across proxy, backend, Celery, and MCP.

**Scope**
- Request ID propagation
- Job ID linking
- Logging field standards

**Definition of done**
- Correlation model exists with field names and propagation path.

### OBS-002 — Structured Log Schema
**Priority:** P1

**Goal**
Standardize structured logging fields for important operational flows.

**Scope**
- actor
- route/tool
- target cluster/project
- result
- duration
- failure class

**Definition of done**
- Logging schema is documented with examples.

### OBS-003 — Metrics Coverage Plan
**Priority:** P1

**Goal**
Identify the most valuable latency/error metrics to instrument first.

**Scope**
- cluster operations
- fleet aggregation
- BNK topology/health
- MCP tools
- background jobs

**Definition of done**
- Metrics plan exists with top-priority endpoints/jobs.

### OBS-004 — Error Taxonomy
**Priority:** P1

**Goal**
Create a shared taxonomy for operator-facing and support-facing failures.

**Scope**
- validation
- auth/permission
- connectivity
- dependency failure
- timeout
- internal error

**Definition of done**
- Error classes, meanings, and intended presentation are documented.

### OBS-005 — Troubleshooting Dashboard/Query Requirements
**Priority:** P2

**Goal**
Define the minimum queries or dashboards needed for routine support.

**Scope**
- slow endpoints
- failing cluster ops
- MCP failures
- recurring background job failures

**Definition of done**
- Support-oriented dashboard/query spec exists.

---

## UX-OPS — Operational UX Consistency

### UX-OPS-001 — Async State UX Standard
**Priority:** P1

**Goal**
Standardize loading, empty, stale, degraded, and retry states across operational UIs.

**Scope**
- tables
- detail panes
- dashboards
- cluster and BNK pages

**Definition of done**
- UX standard exists with examples and usage guidance.

### UX-OPS-002 — Status Badge Vocabulary and Visual Semantics
**Priority:** P1

**Goal**
Create one badge vocabulary and mapping for key platform states.

**Scope**
- badge labels
- colors/icons
- tooltip language
- accessibility expectations

**Definition of done**
- Canonical badge spec exists and maps to backend states.

### UX-OPS-003 — Shared Diagnostic Panel Pattern
**Priority:** P1

**Goal**
Define a reusable pattern for showing actionable diagnostics.

**Scope**
- message
- evidence
- recommended next step
- raw detail access

**Definition of done**
- Pattern spec exists with representative mock structure.

### UX-OPS-004 — Information Hierarchy Review
**Priority:** P2

**Goal**
Audit top operational screens for actionability and operator scan speed.

**Scope**
- primary status vs detail
- alert prominence
- expert affordances

**Definition of done**
- Review findings prioritized by usability impact.

### UX-OPS-005 — Accessibility Pass for Operational Status UX
**Priority:** P2

**Goal**
Ensure status communication does not depend only on color or dense expert knowledge.

**Scope**
- labels
- iconography
- keyboard access
- tooltip/panel readability

**Definition of done**
- Accessibility checklist and gaps list exist.

---

## DEPLOY — Release and Deployment Hardening

### DEPLOY-001 — Release Checklist Template
**Priority:** P1

**Goal**
Create a repeatable release checklist covering code, deploy, verify, and rollback readiness.

**Scope**
- backend/frontend/infra changes
- schema changes
- docs changes
- smoke requirements

**Definition of done**
- Release checklist template exists and is ready for use.

### DEPLOY-002 — Environment Maturity Ladder
**Priority:** P2

**Goal**
Define expectations and quality gates for local, shared dev, test, and production-like environments.

**Scope**
- required checks
- acceptable risk
- verification depth

**Definition of done**
- Environment ladder is documented.

### DEPLOY-003 — Post-Deploy Verification Workflow
**Priority:** P1

**Goal**
Specify one repeatable workflow for post-deploy verification.

**Scope**
- container health
- backend boot logs
- health endpoint
- frontend reachability
- MCP reachability

**Definition of done**
- Verification workflow is documented with expected outputs.

### DEPLOY-004 — Rebuild/Impact Matrix Refresh
**Priority:** P2

**Goal**
Refresh and expand the matrix that maps code changes to rebuild/deploy requirements.

**Scope**
- backend
- frontend
- proxy
- MCP
- docs/configuration

**Definition of done**
- Change-impact matrix exists and is easy to reference.

### DEPLOY-005 — Rollback Rehearsal Plan
**Priority:** P2

**Goal**
Define a realistic rollback rehearsal and evidence standard.

**Scope**
- choose one representative release scenario
- define rollback success criteria

**Definition of done**
- Rehearsal plan is documented.

---

## SEC-GOV — Operation Risk Governance

### SEC-GOV-001 — Operation Risk Classification Matrix
**Priority:** P1

**Goal**
Classify endpoints and tools by blast radius and operational risk.

**Scope**
- read-only
- low-risk mutation
- privileged mutation
- destructive

**Definition of done**
- Risk matrix exists and covers top domains.

### SEC-GOV-002 — Audit Event Requirements
**Priority:** P1

**Goal**
Define what every mutating action must record for accountability.

**Scope**
- actor
- action
- target
- result
- time
- source interface

**Definition of done**
- Audit event contract is documented.

### SEC-GOV-003 — Secret Boundary Review
**Priority:** P1

**Goal**
Review where secrets, kubeconfigs, tokens, and sensitive outputs may leak.

**Scope**
- API logs
- MCP responses
- background tasks
- error payloads

**Definition of done**
- Secret boundary review findings are documented and prioritized.

### SEC-GOV-004 — New Tool/Endpoint Safety Review Checklist
**Priority:** P2

**Goal**
Define a lightweight safety review before adding high-risk automation.

**Scope**
- blast radius
- auth model
- auditability
- rollback implications

**Definition of done**
- Checklist exists and is ready for adoption.

---

## E2E-CRITICAL — Critical Path Validation

### E2E-CRITICAL-001 — Critical Workflow Selection DONE
**Priority:** P1 | **Completed:** 2026-03-30

7 critical workflows selected covering login, project/module lifecycle, K8s visibility,
fleet health, BNK gateway topology, deployment tracking, and system health.
Two new spec files written: `10-fleet-critical.spec.ts` (7 tests), `11-bnk-critical.spec.ts` (8 tests).

**Deliverables:**
- `tests/e2e/E2E_CRITICAL_WORKFLOWS.md` — workflow selection with rationale
- `tests/e2e/tests/10-fleet-critical.spec.ts` — CW-4 Fleet tests
- `tests/e2e/tests/11-bnk-critical.spec.ts` — CW-5 BNK tests

### E2E-CRITICAL-002 — Realistic Fixture Matrix DONE
**Priority:** P1 | **Completed:** 2026-03-30

9 scenario states defined: healthy platform, healthy cluster, empty state, auth failures,
API failures, fleet health variants, BNK states, RBAC permissions, and module lifecycle states.

**Deliverables:**
- `tests/e2e/fixtures/scenario-states.ts` — typed fixture matrix with expectations

### E2E-CRITICAL-003 — Shared-Env Smoke vs Local E2E Strategy
**Priority:** P2

**Goal**
Separate full browser E2E from environment smoke checks to improve practicality.

**Scope**
- local-only tests
- shared-env smoke
- release smoke ownership

**Definition of done**
- Strategy doc exists with test categories and triggers.

### E2E-CRITICAL-004 — MCP End-to-End Sanity Coverage Plan
**Priority:** P2

**Goal**
Define minimal end-to-end proof that MCP remains operational across releases.

**Scope**
- health
- one read-only tool
- one diagnostic tool
- one guarded mutating path if appropriate

**Definition of done**
- MCP sanity plan exists with pass/fail expectations.

---

## MCP-PROD — AI-Operable Interface Productization

### MCP-PROD-001 — Tool Catalog Inventory
**Priority:** P0

**Goal**
Create a canonical catalog of all MCP tools and their backing capabilities.

**Scope**
- tool name
- domain
- purpose
- route mapping
- auth requirement
- risk class
- mutability class

**Definition of done**
- Catalog exists and covers all tools.

### MCP-PROD-002 — Tool Taxonomy and Naming Standard
**Priority:** P0

**Goal**
Standardize tool naming and descriptions for discoverability and consistency.

**Scope**
- naming conventions
- domain grouping
- description templates

**Definition of done**
- Standard exists and inconsistencies are identified.

### MCP-PROD-003 — Tool Safety Classification
**Priority:** P0

**Goal**
Classify every tool by safety and blast radius.

**Scope**
- read_only
- low_risk_mutation
- privileged_mutation
- destructive

**Definition of done**
- All tools have a provisional safety class.

### MCP-PROD-004 — Route Mapping Verification
**Priority:** P0

**Goal**
Verify every MCP tool is backed by the intended real backend route and method.

**Scope**
- route path
- HTTP method
- path parameter mapping
- auth assumptions

**Definition of done**
- Verification results exist for all tools.
- Mismatches are identified and prioritized.

### MCP-PROD-005 — Critical Tool Contract Matrix
**Priority:** P0

**Goal**
Define a contract verification set for the most important MCP tools.

**Scope**
- health
- cluster ops
- connectivity
- BNK topology/health
- diagnostics

**Definition of done**
- Critical tool matrix exists with expected output contracts.

### MCP-PROD-006 — MCP Compatibility and Deprecation Policy
**Priority:** P1

**Goal**
Define how tool changes are communicated and managed over time.

**Scope**
- additive changes
- breaking changes
- renames
- aliases
- deprecation windows

**Definition of done**
- Compatibility/deprecation policy is documented.

### MCP-PROD-007 — MCP Telemetry Requirements
**Priority:** P1

**Goal**
Define the minimum telemetry needed to support MCP in production.

**Scope**
- caller
- target
- duration
- result
- failure class
- correlation ID

**Definition of done**
- Telemetry spec exists with event field definitions.

### MCP-PROD-008 — MCP Error Semantics Standard
**Priority:** P1

**Goal**
Standardize how MCP tools report failures so AI clients can recover intelligently.

**Scope**
- validation errors
- auth errors
- not found
- timeout
- dependency unavailable

**Definition of done**
- Error shape standard exists with examples.

### MCP-PROD-009 — MCP Operator Guide
**Priority:** P1

**Goal**
Document safe adoption patterns and troubleshooting guidance for MCP consumers.

**Scope**
- setup/auth
- safe usage
- risky operations
- debugging
- example workflows

**Definition of done**
- Operator guide outline or draft exists.

### MCP-PROD-010 — Autonomous-Use Eligibility Rules
**Priority:** P2

**Goal**
Define which tool classes are suitable for autonomous invocation and which require explicit human confirmation.

**Scope**
- safety class mapping
- confirmation expectations
- environment sensitivity

**Definition of done**
- Eligibility rules are documented and traceable to tool classes.

### MCP-PROD-011 — Curated High-Value AI Workflows
**Priority:** P2

**Goal**
Define a small set of exemplar AI-driven workflows that BNK Forge should support exceptionally well.

**Scope**
- investigate cluster connectivity issue
- inspect fleet health
- diagnose BNK degradation
- export/report operational state

**Definition of done**
- Workflow list exists with required tools, risks, and success expectations.

### MCP-PROD-012 — MCP Readiness Scorecard
**Priority:** P2

**Goal**
Create a repeatable scorecard to track MCP maturity across safety, contracts, observability, and docs.

**Scope**
- scoring dimensions
- evidence required
- review cadence

**Definition of done**
- Scorecard template exists and can be used in planning reviews.

---

## Recommended Initial Work Packages

### Work Package A — Platform Truthfulness
- PLAT-REL-001
- PLAT-REL-002
- PLAT-REL-003
- UX-OPS-002

### Work Package B — Contract Trust
- API-CONTRACT-001
- API-CONTRACT-002
- API-CONTRACT-004
- API-CONTRACT-005

### Work Package C — MCP Foundation
- MCP-PROD-001
- MCP-PROD-002
- MCP-PROD-003
- MCP-PROD-004
- MCP-PROD-005

### Work Package D — Operability Baseline
- OBS-001
- OBS-002
- OBS-004
- DEPLOY-001
- DEPLOY-003

---

## Related Documents

- [Strategic Roadmap](STRATEGIC_ROADMAP.md)
- [MCP Productization Plan](MCP_PRODUCTIZATION_PLAN.md)
- [Sprint Plan — Platform Truthfulness 001](SPRINT_PLATFORM_TRUTHFULNESS_001.md)
- [Engineering Improvements](ENGINEERING_IMPROVEMENTS.md)
- [Product Vision](PRODUCT_VISION.md)
