# MCP Productization Plan

> Roadmap for turning the BNK Forge MCP server into a safe, observable, and enterprise-ready AI-operable interface.

Last updated: 2026-03-27 | Status: Proposed

---

## Vision

BNK Forge already exposes a large portion of its operational capability through MCP tools. The next step is not simply “more tools,” but a **better AI-operable product surface**:

- assistants can discover capabilities reliably
- tool contracts are stable and explainable
- risky actions are clearly classified
- operators can audit and troubleshoot tool usage
- the MCP interface becomes a differentiated, trusted way to control BNK Forge

---

## Strategic Goals

### 1. Make tools safe to use
- Distinguish read-only, mutating, privileged, and destructive tools.
- Add clear guidance for when confirmation or extra safeguards are required.
- Prevent silent high-blast-radius actions.

### 2. Make tools easy to understand
- Use consistent naming, descriptions, and domains.
- Improve discoverability for common operator tasks.
- Make tool behavior predictable across similar operations.

### 3. Make tools contract-stable
- Tie tools to validated backend routes and schemas.
- Reduce drift between REST responses and MCP tool outputs.
- Define versioning and deprecation expectations.

### 4. Make tools observable
- Capture tool usage, duration, target, actor, and failure class.
- Make debugging and support straightforward.
- Give operators confidence in what an assistant did.

---

## What “Good” Looks Like

An enterprise-ready MCP interface should provide:

- **discoverability:** users can find the right tool quickly
- **safety:** dangerous tools are clearly marked and governed
- **compatibility:** consumers know what can change and when
- **observability:** support teams can trace tool activity end-to-end
- **recoverability:** errors are actionable for both humans and AI agents

---

## Workstreams

### MCP-WS-001 — Tool Catalog and Taxonomy

Create a canonical inventory for every MCP tool with:
- tool name
- domain
- purpose
- backing route/path
- HTTP method
- auth requirements
- mutability class
- risk class
- expected response contract
- notes on side effects or follow-up verification

**Deliverables**
- machine-readable tool catalog
- human-readable documentation table
- naming and description guidelines

**Why it matters**
- discoverability
- auditing
- safety review
- easier evolution over time

---

### MCP-WS-002 — Tool Safety and Governance

Define a safety model for tool exposure:

**Suggested classes**
- `read_only`
- `low_risk_mutation`
- `privileged_mutation`
- `destructive`

For each class, define:
- required auth/role expectations
- confirmation expectations
- audit requirements
- documentation requirements
- whether the tool is recommended for autonomous use

**Deliverables**
- safety policy
- risk classification matrix
- criteria for exposing new tools

---

### MCP-WS-003 — Contract Verification and Compatibility

Ensure every tool is backed by a verified API contract.

**Key actions**
- verify route existence and HTTP mapping
- verify request/response shape alignment
- prioritize typed response models on critical routes
- define output normalization rules when backend responses are currently inconsistent

**Deliverables**
- route-mapping validation checks
- contract test suite for critical tools
- compatibility/versioning policy
- deprecation workflow for renamed/changed tools

**Suggested compatibility rules**
- additive fields are preferred over breaking renames/removals
- destructive behavior changes require explicit release-note callout
- tool renames require deprecation period and alias strategy where feasible

---

### MCP-WS-004 — Telemetry, Audit, and Debuggability

Track enough data to support production use.

**Key telemetry fields**
- request ID / trace ID
- tool name
- caller identity
- cluster/project target
- duration
- result: success/failure
- failure class
- whether action was mutating

**Deliverables**
- structured logging schema for MCP
- dashboards or log queries for top failure modes
- correlation between MCP tool calls and backend route execution

---

### MCP-WS-005 — Error Semantics for AI Consumers

Design tool failures as a product surface.

**Goals**
- assistants should be able to recover, retry, or ask better follow-up questions
- errors should distinguish transient vs permanent failures
- errors should explain operator actionability

**Error model guidance**
- include what failed
- include target context
- identify likely cause where known
- include safe next action or suggestion
- classify retryability where possible

**Deliverables**
- standard tool error shape
- guidance for backend-to-MCP error mapping
- examples for connectivity/auth/not-found/validation/timeout cases

---

### MCP-WS-006 — Operator Documentation and Adoption

Help users adopt the AI-operable interface safely.

**Documentation themes**
- what MCP is in BNK Forge
- supported assistant environments
- how authentication works
- safe usage patterns
- when to prefer read-only vs mutating tools
- troubleshooting common failures
- examples of high-value operational workflows

**Deliverables**
- operator guide
- quickstart examples
- “safe prompting” and guardrail guidance
- support/runbook notes for MCP troubleshooting

---

## Recommended Backlog Sequence

### Phase 1 — Foundations
1. Build tool catalog and taxonomy
2. Classify tool mutability/risk
3. Verify route mappings and close obvious contract gaps

### Phase 2 — Trust and Supportability
4. Add telemetry and audit trail
5. Standardize error semantics
6. Publish operator-facing MCP docs

### Phase 3 — Maturity
7. Add compatibility and deprecation policy
8. Add critical-tool contract tests in CI
9. Add adoption guidance and curated workflows

---

## Exit Criteria

The MCP interface can be considered productized when:

- all tools have catalog entries and risk classes
- critical tools have verified route/contract mappings
- telemetry exists for production support
- error outputs are actionable and consistent
- documentation explains safe usage and limitations
- compatibility expectations are explicit

---

## Backlog-Ready Tickets

### Foundation

- **MCP-PROD-001 — Tool Catalog Inventory**
  - Build canonical inventory of all MCP tools with route mapping, domain, auth, mutability, and risk.
- **MCP-PROD-002 — Tool Taxonomy and Naming Standard**
  - Define naming/domain/description consistency rules.
- **MCP-PROD-003 — Tool Safety Classification**
  - Assign each tool a safety/blast-radius class.
- **MCP-PROD-004 — Route Mapping Verification**
  - Verify each tool against real backend routes and parameter mappings.
- **MCP-PROD-005 — Critical Tool Contract Matrix**
  - Identify top tools requiring exact contract validation.

### Trust and Supportability

- **MCP-PROD-006 — MCP Compatibility and Deprecation Policy**
  - Define change rules, deprecation windows, and alias expectations.
- **MCP-PROD-007 — MCP Telemetry Requirements**
  - Define required event/log fields for supportability.
- **MCP-PROD-008 — MCP Error Semantics Standard**
  - Standardize error structure for AI recoverability.
- **MCP-PROD-009 — MCP Operator Guide**
  - Document setup, auth, safe use, risk awareness, and troubleshooting.

### Maturity

- **MCP-PROD-010 — Autonomous-Use Eligibility Rules**
  - Define which tool classes may be safely automated.
- **MCP-PROD-011 — Curated High-Value AI Workflows**
  - Select exemplar workflows and required tool coverage.
- **MCP-PROD-012 — MCP Readiness Scorecard**
  - Track maturity across contracts, telemetry, safety, and docs.

---

## Relationship to Broader Strategy

This plan depends on and reinforces:

- **Platform reliability** — tools must report truthful system state
- **API contract rigor** — MCP can only be stable if REST contracts are stable
- **Observability** — tool invocation must be traceable
- **Security governance** — AI-operable actions increase blast radius if unmanaged

---

## Related Documents

- [Strategic Roadmap](STRATEGIC_ROADMAP.md)
- [API Reference](API_REFERENCE.md)
- [Engineering Improvements](ENGINEERING_IMPROVEMENTS.md)
