# Sprint Plan — Contract Trust 001

> Agent-ready sprint plan for the second strategic execution wave. Focus: make critical BNK Forge APIs explicit, stable, and safer for frontend and MCP consumers.

Last updated: 2026-03-27 | Status: **COMPLETE** — All 4 tickets delivered

---

## Sprint Summary

**Sprint ID:** `contract-trust-001`  
**Source Work Package:** Work Package B — Contract Trust  
**Primary objective:** establish clear contract priorities, response-model hardening strategy, and verification patterns for critical platform APIs.

---

## Sprint Goals

1. Classify endpoints by contract criticality.
2. Define the Tier 1 response-model hardening plan.
3. Select the first exact contract-verification matrix.
4. Define how API contract changes should be reviewed over time.

---

## Tickets in Scope

### API-CONTRACT-001 — Endpoint Contract Tiering

**Why**
Not every route needs the same rigor first. The team needs a clear way to focus contract hardening where breakage hurts most.

**Deliverables**
- Tier 1/2/3 rubric.
- Initial endpoint classification by domain.
- Rationale for why Tier 1 surfaces are business-critical.

**Acceptance criteria**
- Tiering is easy for backend, frontend, and MCP contributors to apply.
- Tier 1 clearly includes operator-critical and AI-consumed routes.
- Coverage is sufficient to guide implementation planning.

---

### API-CONTRACT-002 — Tier 1 Response Model Coverage Plan

**Why**
The project already knows contract drift is a structural risk. This ticket turns that into a concrete route-by-route hardening plan.

**Deliverables**
- List of Tier 1 endpoints lacking sufficient explicit response models.
- Proposed response-model adoption order.
- Blockers/unknowns list.

**Acceptance criteria**
- Top Tier 1 routes are identified unambiguously.
- Plan distinguishes easy wins from routes needing design decisions.
- Backend/frontend/MCP impact is called out.

---

### API-CONTRACT-004 — Golden Contract Test Matrix

**Why**
Critical routes need exact response-shape verification, not only optimistic mocks.

**Deliverables**
- Matrix of first contract-verification targets.
- Proposed fixture approach.
- Suggested assertions and ownership.

**Acceptance criteria**
- Matrix covers the most important operator-critical routes.
- Includes routes heavily used by frontend and MCP.
- Exact-shape testing expectations are clear.

---

### API-CONTRACT-005 — OpenAPI Diff Review Workflow

**Why**
Contracts should not change silently. The team needs review visibility when public shapes move.

**Deliverables**
- Proposed CI/review workflow for OpenAPI diffs.
- Policy for when diffs are required and who reviews them.
- Notes on exceptions and limitations.

**Acceptance criteria**
- Workflow is simple enough to adopt.
- Policy explains what counts as a public contract change.
- Review expectations are explicit.

---

## Recommended Execution Order

1. **API-CONTRACT-001**
2. **API-CONTRACT-002**
3. **API-CONTRACT-004**
4. **API-CONTRACT-005**

The tiering and hardening plan should exist before the team locks contract tests and review workflow.

---

## Parallelization Plan for Agents

### Agent A — Tiering Owner
**Primary ticket:** API-CONTRACT-001

### Agent B — Tier 1 Coverage Planner
**Primary ticket:** API-CONTRACT-002

**Dependency note**
- Should align with Agent A’s rubric before final route prioritization.

### Agent C — Golden Contract Matrix Owner
**Primary ticket:** API-CONTRACT-004

**Dependency note**
- Can begin route discovery early; final list should reflect Agent A/B output.

### Agent D — OpenAPI Diff Workflow Owner
**Primary ticket:** API-CONTRACT-005

**Dependency note**
- Can work mostly independently after understanding route/schema workflow.

---

## Suggested Branching / Ownership Pattern

- `agent/api-contract-001`
- `agent/api-contract-002`
- `agent/api-contract-004`
- `agent/api-contract-005`

---

## Suggested Deliverable Format Per Ticket

1. Summary
2. Current-state findings
3. Proposal
4. Impact on backend/frontend/MCP/testing
5. Follow-on implementation tickets

---

## Definition of Sprint Success

- Tier 1 endpoints are identified.
- Response-model hardening order is agreed.
- Golden contract test targets are defined.
- API contract changes have a review workflow.

---

## Follow-On Tickets Expected After This Sprint

- implement Tier 1 response models
- wire contract tests into CI
- align frontend/MSW fixtures to tiered contracts
- align MCP outputs to hardened response shapes

---

## Deliverables Produced

| Ticket | Deliverable | Location |
|--------|------------|----------|
| API-CONTRACT-001 | Tier 1/2/3 rubric + full endpoint classification | `docs/specs/ENDPOINT_CONTRACT_TIERING.md` |
| API-CONTRACT-002 | Route-by-route hardening plan (3 batches, ~9h effort) | `docs/specs/TIER1_RESPONSE_MODEL_PLAN.md` |
| API-CONTRACT-004 | 20-endpoint golden test matrix + fixture approach | `docs/specs/GOLDEN_CONTRACT_TEST_MATRIX.md` |
| API-CONTRACT-005 | 3-layer OpenAPI diff workflow + review policy | `docs/specs/OPENAPI_DIFF_REVIEW_WORKFLOW.md` |

---

## Follow-On Implementation Tickets

### P0 — Wire Existing Schemas (immediate, low-risk)
- **CT-B01** Wire `schemas/k8s.py` response models to cluster management routes
- **CT-B02** Wire `schemas/k8s.py` connectivity response models to connectivity routes
- **CT-B03** Adapt + wire `schemas/helm.py` response models to Helm routes
- **CT-B04** Wire `schemas/system.py` SystemHealthResponse to health route
- **CT-B08** Verify no consumer depends on fields stripped by response_model
- **CT-B10** Create `backend/tests/contract/` directory and shared fixtures
- **CT-B11** Implement P0 golden tests (auth, clusters, connectivity, system, fleet, projects)
- **CT-B13** Add `make test-contracts` target and CI stage

### P1 — Create New Schemas + Wire
- **CT-B05** Create + wire K8s resource describe/logs/events/metrics response models
- **CT-B06** Create system VersionResponse and SettingsListResponse
- **CT-B07** Design + wire BNK data/health/topology response models
- **CT-B12** Implement P1 golden tests (BNK, Helm, licensing, recovery, resources)
- **CT-B20** Create `scripts/openapi-diff-summary.py`
- **CT-B21** Add `openapi-diff` CI job to Phase 1
- **CT-B22** Embed Tier 1 endpoint list in diff script

### P2 — MCP Alignment + Promotion
- **CT-B14** Audit frontend MSW handlers against golden schemas
- **CT-B15** Document MCP tool output contracts referencing golden schemas
- **CT-B23** Add MCP tool → route cross-reference to diff output
- **CT-B24** Evaluate promoting Tier 1 breaking changes to merge blocker

---

## Related Documents

- [Strategic Backlog](STRATEGIC_BACKLOG.md)
- [Strategic Roadmap](STRATEGIC_ROADMAP.md)
- [MCP Productization Plan](MCP_PRODUCTIZATION_PLAN.md)
- [Endpoint Contract Tiering](specs/ENDPOINT_CONTRACT_TIERING.md)
- [Tier 1 Response Model Coverage Plan](specs/TIER1_RESPONSE_MODEL_PLAN.md)
- [Golden Contract Test Matrix](specs/GOLDEN_CONTRACT_TEST_MATRIX.md)
- [OpenAPI Diff Review Workflow](specs/OPENAPI_DIFF_REVIEW_WORKFLOW.md)
