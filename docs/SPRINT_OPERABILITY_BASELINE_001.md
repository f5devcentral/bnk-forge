# Sprint Plan — Operability Baseline 001

> Agent-ready sprint plan for the fourth strategic execution wave. Focus: build the minimum observability and release discipline required to support BNK Forge confidently in production.

Last updated: 2026-03-27 | Status: Ready for execution

---

## Sprint Summary

**Sprint ID:** `operability-baseline-001`  
**Source Work Package:** Work Package D — Operability Baseline  
**Primary objective:** define the minimum traceability, logging, error classification, and deployment discipline required to support the platform safely.

---

## Sprint Goals

1. Define correlation across request, job, and tool flows.
2. Standardize structured log fields.
3. Define a shared operational error taxonomy.
4. Create a reusable release checklist.
5. Define one post-deploy verification workflow.

---

## Tickets in Scope

### OBS-001 — Request and Job Correlation Strategy

**Why**
Distributed operational systems are hard to debug without shared trace identifiers.

**Deliverables**
- Correlation-ID strategy across proxy, backend, Celery, and MCP.
- Propagation guidance.

**Acceptance criteria**
- Strategy is explicit enough to implement later without redesign.
- It supports tracing both user and AI-driven flows.

---

### OBS-002 — Structured Log Schema

**Why**
Logs need to support operations, not just development.

**Deliverables**
- Standard field set.
- Example events for key flows.
- Guidance for backend and MCP alignment.

**Acceptance criteria**
- Schema supports actor/target/result/duration/failure analysis.
- It is realistic for adoption across major domains.

---

### OBS-004 — Error Taxonomy

**Why**
The team needs shared failure categories so diagnostics, alerts, and support can speak the same language.

**Deliverables**
- Error taxonomy.
- Definitions and usage guidance.
- Mapping examples.

**Acceptance criteria**
- Categories distinguish retryability and operator actionability where relevant.
- Taxonomy is suitable for UI, logs, and MCP errors.

---

### DEPLOY-001 — Release Checklist Template

**Why**
Safe release behavior should not depend on memory or heroics.

**Deliverables**
- Release checklist template.
- Sections for change analysis, deploy, verify, and rollback readiness.

**Acceptance criteria**
- Template is practical for routine use.
- It reflects backend/frontend/MCP concerns.

---

### DEPLOY-003 — Post-Deploy Verification Workflow

**Why**
The platform needs a repeatable definition of “the deployment actually worked.”

**Deliverables**
- Verification workflow.
- Expected outputs and failure interpretation.

**Acceptance criteria**
- Workflow covers container health, logs, health endpoints, and MCP reachability.
- Steps are specific enough to automate later.

---

## Recommended Execution Order

1. **OBS-001**
2. **OBS-002**
3. **OBS-004**
4. **DEPLOY-001**
5. **DEPLOY-003**

Correlation and log structure should inform deployment verification language and evidence.

---

## Parallelization Plan for Agents

### Agent A — Correlation Strategy Owner
**Primary ticket:** OBS-001

### Agent B — Structured Logging Owner
**Primary ticket:** OBS-002

**Dependency note**
- Should coordinate field naming with Agent A.

### Agent C — Error Taxonomy Owner
**Primary ticket:** OBS-004

### Agent D — Release Checklist Owner
**Primary ticket:** DEPLOY-001

### Agent E — Post-Deploy Verification Owner
**Primary ticket:** DEPLOY-003

**Dependency note**
- Should align verification outputs with Agent A/B/C terminology.

---

## Suggested Branching / Ownership Pattern

- `agent/obs-001`
- `agent/obs-002`
- `agent/obs-004`
- `agent/deploy-001`
- `agent/deploy-003`

---

## Suggested Deliverable Format Per Ticket

1. Summary
2. Current-state findings
3. Proposal
4. Impact on backend/MCP/ops workflow/docs
5. Follow-on implementation tickets

---

## Definition of Sprint Success

- Correlation strategy exists.
- Structured log schema exists.
- Error taxonomy exists.
- Release checklist exists.
- Post-deploy verification workflow exists.

---

## Follow-On Tickets Expected After This Sprint

- implement request ID propagation
- implement structured logging adoption
- align MCP telemetry to correlation model
- automate release verification checks

---

## Related Documents

- [Strategic Backlog](STRATEGIC_BACKLOG.md)
- [Strategic Roadmap](STRATEGIC_ROADMAP.md)
- [MCP Productization Plan](MCP_PRODUCTIZATION_PLAN.md)
