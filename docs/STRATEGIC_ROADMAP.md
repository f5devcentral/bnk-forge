# Strategic Roadmap

> Prioritized strategic epics for BNK Forge v2. Focus: platform trust, contract rigor, operability, and AI-operable productization.

Last updated: 2026-03-27 | Status: Proposed

---

## Why This Roadmap Exists

BNK Forge already has broad capability across deployment, operations, diagnostics, fleet management, and AI tool access. The next phase should optimize for **trust, clarity, and productization** rather than raw feature count.

This roadmap turns that direction into durable epics so the work survives session handoffs and future planning cycles.

---

## Strategic Opportunities

1. **Become the truthful control plane**
   - Replace false-green states with actionable health and diagnostic states.
   - Ensure cluster, BNK, and fleet health surfaces reflect real operator impact.

2. **Make the AI-operable interface a flagship capability**
   - Productize MCP as a safe, observable, versioned, enterprise-grade interface.
   - Treat tool contracts, permissions, and diagnostics as product concerns.

3. **Convert engineering discipline into platform leverage**
   - Use strong testing, service decomposition, and process rigor to increase speed and reduce regression risk.

---

## Strategic Epics

### EPIC PLAT-REL-001 — Platform Reliability Hardening

**Goal:** Make BNK Forge more truthful and dependable in production.

**Outcomes**
- Operators trust health/status information.
- Deployments are safer and easier to verify.
- Failure modes are visible and actionable.

**Scope**
- Standardize health and degraded-state semantics across cluster, fleet, and BNK surfaces.
- Expand connectivity truthfulness beyond simple status badges.
- Add stronger post-deploy verification and runtime checks.
- Surface operational risks like expired licenses and blocked ports in high-visibility UX.

**Candidate backlog items**
- Define canonical status model: configured, healthy, degraded, reachable, port_blocked, auth_failed, unreachable, unknown.
- Audit all status-producing backend services and frontend badges against canonical model.
- Add shared diagnostic payload contract used by connectivity, health, and fleet views.
- Add release smoke suite covering login, health, clusters, topology, and MCP health.
- Promote server-side post-deploy verification into a single scripted workflow.

**Definition of done**
- Major operator-facing status surfaces share common semantics.
- Production verification is repeatable, documented, and scriptable.
- Critical blockers are surfaced proactively, not discovered through logs.

---

### EPIC API-CONTRACT-001 — Critical API Contract Rigor

**Goal:** Eliminate backend/frontend/MCP drift on important interfaces.

**Outcomes**
- Critical endpoints have explicit, trustworthy contracts.
- OpenAPI becomes a more reliable source of truth.
- Frontend and MCP integrations fail less often from schema mismatch.

**Scope**
- Expand `response_model` coverage on critical routes.
- Reduce shape ambiguity between inline schemas and `backend/schemas/`.
- Add contract tests and contract-diff review to CI.
- Create a plan for generated TypeScript types/client bindings where the backend contract is mature.

**Candidate backlog items**
- Define endpoint contract tiers (Tier 1/2/3) by operational importance.
- Add response models for Tier 1 endpoints first: auth, clusters, connectivity, fleet, topology, BNK health.
- Add golden response-shape tests for top endpoints.
- Add OpenAPI diff review workflow for PRs touching routes/schemas.
- Document schema ownership pattern: when to use inline models vs shared schema modules.

**Definition of done**
- Tier 1 endpoints have explicit contracts and matching tests.
- OpenAPI diffs are visible and reviewed.
- Frontend/MSW tests use real backend shapes consistently.

---

### EPIC OBS-001 — Observability and Traceability

**Goal:** Make the platform diagnosable in production and explainable during failure.

**Outcomes**
- Engineers can trace requests, jobs, and tool invocations end-to-end.
- Slow or failing dependencies are visible.
- Support/debugging time is reduced.

**Scope**
- Structured logging and correlation IDs.
- Celery job traceability.
- Endpoint latency/error metrics.
- External dependency timing for Kubernetes, proxy, and MCP flows.
- Audit-oriented event enrichment for mutating operations.

**Candidate backlog items**
- Standardize structured log fields across backend routes and services.
- Add request ID propagation from proxy → backend → Celery → MCP logs.
- Instrument high-value endpoints with latency and result metrics.
- Create troubleshooting dashboards or log search conventions for cluster operations.
- Define error taxonomy for operator-facing failures.

**Definition of done**
- Critical flows can be traced with IDs.
- Top operational error classes are measurable.
- Logs and metrics support routine debugging without ad hoc instrumentation.

---

### EPIC UX-OPS-001 — Operational UX Consistency

**Goal:** Make the UI consistent, actionable, and optimized for operators.

**Outcomes**
- Similar conditions look and behave similarly across the product.
- Error/loading/degraded states are predictable.
- Expert users can move faster.

**Scope**
- Shared async/loading/error/degraded-state patterns.
- Shared status-badge and diagnostic panel semantics.
- Better expert affordances for inspection/export/raw details.
- Information hierarchy review for primary operator workflows.

**Candidate backlog items**
- Define standard patterns for loading, empty, partial, stale, degraded, and retry states.
- Build a shared diagnostic drawer/panel contract and component pattern.
- Standardize badge vocabulary and visual semantics across clusters, fleet, and BNK pages.
- Audit top workflows for excessive ambiguity or hidden detail.
- Add accessibility pass for non-color-dependent status presentation.

**Definition of done**
- Core operator workflows share the same interaction patterns.
- Users can quickly tell what needs action and why.

---

### EPIC DEPLOY-001 — Release and Deployment Hardening

**Goal:** Make releases predictable, safer, and less dependent on tribal knowledge.

**Outcomes**
- Deployment risk is lowered.
- Post-deploy verification is automated.
- Rollback readiness improves.

**Scope**
- Formalize release checklist.
- Encode preflight, rebuild, smoke, and rollback steps.
- Distinguish environment maturity levels.
- Track deploy-impacting change types more explicitly.

**Candidate backlog items**
- Create release checklist template for backend/frontend/infra/API changes.
- Script post-deploy verification for containers, logs, health, and MCP endpoint checks.
- Document environment ladder: local, shared dev, test server, production.
- Add changed-surface matrix for deciding what must be rebuilt and verified.
- Rehearse rollback workflow for one representative release.

**Definition of done**
- Releases use a repeatable checklist and automated verification.
- Deployment safety is documented and testable.

---

### EPIC SEC-GOV-001 — Operation Risk Governance

**Goal:** Ensure dangerous capabilities are visible, controlled, and auditable.

**Outcomes**
- Mutating and destructive operations are clearly classified.
- Tooling and APIs have clearer safety boundaries.
- Auditability improves for automation and human actions.

**Scope**
- Risk classification for endpoints and tools.
- Better audit metadata for who/what/where/result.
- Secret-handling review for logs and AI-facing interfaces.
- Security review gate for high-risk automation.

**Candidate backlog items**
- Label operations as read-only, low-risk mutation, privileged, destructive.
- Add safety guidance and confirm flows for high-blast-radius actions.
- Review log redaction and secret boundaries in API and MCP paths.
- Create a lightweight security review checklist for new tools/endpoints.

**Definition of done**
- High-risk operations are classified and auditable.
- Product teams can reason about blast radius before exposing new capabilities.

---

### EPIC E2E-CRITICAL-001 — Critical Path Validation

**Goal:** Prove the platform works end-to-end where it matters most.

**Outcomes**
- Confidence is based on workflows, not only unit-level correctness.
- Deploy verification aligns with user-critical paths.

**Scope**
- Small but high-value browser and API smoke coverage.
- Production-like validation after deploy.
- Representative fixtures for degraded and failure states.

**Candidate backlog items**
- Select 8–12 critical-path E2E scenarios.
- Create realistic fixture matrix for healthy, blocked, auth_failed, and degraded clusters.
- Add MCP sanity coverage to smoke suite.
- Distinguish local-only E2E from shared-env smoke checks.

**Definition of done**
- Core workflows are proven end-to-end.
- Key regressions are caught closer to production reality.

---

### EPIC MCP-PROD-001 — AI-Operable Interface Productization

**Goal:** Turn the MCP server from a completed feature into a flagship, enterprise-grade interface.

**Outcomes**
- MCP is safe, observable, versioned, and easy to trust.
- AI assistants can operate BNK Forge with predictable contracts.
- The platform gains a differentiated automation story.

**Scope**
- Tool taxonomy and naming consistency.
- Tool versioning and deprecation policy.
- Permission/risk classification for tools.
- Contract validation and route-mapping verification.
- Telemetry, diagnostics, and operator documentation.

**Candidate backlog items**
- Build tool catalog with domain, mutability, risk, required auth, and backing route.
- Define safe / mutating / destructive tool classes and expose that in docs.
- Audit tool names and descriptions for discoverability and consistency.
- Add automated validation that every tool maps to a real backend route and documented contract.
- Create tool compatibility/versioning policy and deprecation workflow.
- Add telemetry: tool invoked, actor, target, duration, result, failure class.
- Add user-facing documentation for best-practice AI usage and guardrails.
- Define response conventions for tool errors so assistants can recover reliably.

**Definition of done**
- MCP has explicit governance, observability, and compatibility rules.
- Tool consumers can discover capabilities safely.
- The AI-operable interface is supportable as a first-class product surface.

---

## Recommended Sequencing

### Now
1. PLAT-REL-001 — truthfulness and reliability
2. API-CONTRACT-001 — contract rigor for critical endpoints
3. MCP-PROD-001 — AI-operable interface productization foundations

### Next
4. OBS-001 — observability and traceability
5. DEPLOY-001 — release/deploy hardening
6. UX-OPS-001 — operational consistency

### After foundations are stronger
7. SEC-GOV-001 — governance expansion
8. E2E-CRITICAL-001 — workflow proof at critical paths

---

## Success Metrics

Track progress using measurable indicators:

- Fewer production-only regressions from contract drift
- Faster diagnosis time for cluster and BNK failures
- More routes with explicit response models
- More MCP tools with risk classification and route verification
- Lower time-to-verify after deploy
- Higher confidence in operator-facing status accuracy

---

## Backlog-Ready Ticket Set

The items below are structured to be pulled into the main backlog with minimal rewriting. Each ticket includes a clear goal, scope boundary, and practical definition of done.

### PLAT-REL-001 — Canonical Status Semantics

**Priority:** P0  
**Epic:** PLAT-REL-001

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
**Epic:** PLAT-REL-001

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
**Epic:** PLAT-REL-001

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
**Epic:** PLAT-REL-001

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
**Epic:** PLAT-REL-001

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

### API-CONTRACT-001 — Endpoint Contract Tiering

**Priority:** P0  
**Epic:** API-CONTRACT-001

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
**Epic:** API-CONTRACT-001

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
**Epic:** API-CONTRACT-001

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
**Epic:** API-CONTRACT-001

**Goal**
Define a small set of exact response-shape tests for the most important APIs.

**Scope**
- Select 10–20 critical endpoints.
- Identify expected shape ownership and fixture approach.

**Definition of done**
- Matrix exists with route, owner, and expected assertions.

### API-CONTRACT-005 — OpenAPI Diff Review Workflow

**Priority:** P1  
**Epic:** API-CONTRACT-001

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
**Epic:** API-CONTRACT-001

**Goal**
Define whether and how TypeScript types/clients should be generated from OpenAPI.

**Scope**
- Tooling options
- Generation boundaries
- Coexistence with hand-written types during migration

**Definition of done**
- Decision memo exists with phased migration guidance.

---

### OBS-001 — Request and Job Correlation Strategy

**Priority:** P1  
**Epic:** OBS-001

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
**Epic:** OBS-001

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
**Epic:** OBS-001

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
**Epic:** OBS-001

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
**Epic:** OBS-001

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

### UX-OPS-001 — Async State UX Standard

**Priority:** P1  
**Epic:** UX-OPS-001

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
**Epic:** UX-OPS-001

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
**Epic:** UX-OPS-001

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
**Epic:** UX-OPS-001

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
**Epic:** UX-OPS-001

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

### DEPLOY-001 — Release Checklist Template

**Priority:** P1  
**Epic:** DEPLOY-001

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
**Epic:** DEPLOY-001

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
**Epic:** DEPLOY-001

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
**Epic:** DEPLOY-001

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
**Epic:** DEPLOY-001

**Goal**
Define a realistic rollback rehearsal and evidence standard.

**Scope**
- choose one representative release scenario
- define rollback success criteria

**Definition of done**
- Rehearsal plan is documented.

---

### SEC-GOV-001 — Operation Risk Classification Matrix

**Priority:** P1  
**Epic:** SEC-GOV-001

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
**Epic:** SEC-GOV-001

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
**Epic:** SEC-GOV-001

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
**Epic:** SEC-GOV-001

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

### E2E-CRITICAL-001 — Critical Workflow Selection

**Priority:** P1  
**Epic:** E2E-CRITICAL-001

**Goal**
Select the minimum set of end-to-end workflows that prove platform value and safety.

**Scope**
- login
- cluster list/add/check
- fleet visibility
- BNK topology/health
- one diagnostic flow
- MCP sanity

**Definition of done**
- Named workflow list exists with rationale and owner.

### E2E-CRITICAL-002 — Realistic Fixture Matrix

**Priority:** P1  
**Epic:** E2E-CRITICAL-001

**Goal**
Define realistic test states for healthy and degraded platform scenarios.

**Scope**
- healthy cluster
- port blocked
- auth failed
- degraded fleet
- empty state

**Definition of done**
- Fixture matrix exists and maps to workflows/tests.

### E2E-CRITICAL-003 — Shared-Env Smoke vs Local E2E Strategy

**Priority:** P2  
**Epic:** E2E-CRITICAL-001

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
**Epic:** E2E-CRITICAL-001

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

### MCP-PROD-001 — Tool Catalog Inventory

**Priority:** P0  
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

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
**Epic:** MCP-PROD-001

**Goal**
Create a repeatable scorecard to track MCP maturity across safety, contracts, observability, and docs.

**Scope**
- scoring dimensions
- evidence required
- review cadence

**Definition of done**
- Scorecard template exists and can be used in planning reviews.

---

## Related Documents

- [Engineering Improvements](ENGINEERING_IMPROVEMENTS.md)
- [Product Vision](PRODUCT_VISION.md)
- [UX Roadmap](UX_ROADMAP.md)
- [API Reference](API_REFERENCE.md)
