# Sprint Plan — Platform Truthfulness 001

> Agent-ready sprint plan for the first strategic execution wave. Focus: make BNK Forge status reporting more truthful, consistent, and actionable.

Last updated: 2026-03-27 | Status: Ready for execution

---

## Sprint Summary

**Sprint ID:** `platform-truthfulness-001`  
**Source Work Package:** Work Package A — Platform Truthfulness  
**Primary objective:** Replace ambiguous or misleading health/status reporting with a canonical, actionable model across platform surfaces.

This sprint turns the new strategic planning work into actionable execution units that multiple agents can work on with minimal overlap.

---

## Sprint Goals

1. Define a **canonical status model** for platform health-like states.
2. Define a **shared diagnostic payload** for operator-facing status explanations.
3. Audit current product surfaces for **false-green, ambiguous, or inconsistent states**.
4. Align **frontend badge semantics** with the new platform-truth model.

---

## Tickets in Scope

### 1. PLAT-REL-001 — Canonical Status Semantics

**Why**
Today, operational status can be partially true but not operator-useful. A cluster may be configured, ICMP-reachable, and still functionally unusable. This ticket defines the language the platform should use consistently.

**Deliverables**
- A written canonical status model.
- Mapping from current backend-produced states to canonical states.
- Identification of any states that are missing or overloaded.

**Acceptance criteria**
- The status vocabulary is explicit and unambiguous.
- It distinguishes configuration state from operational reachability and usability.
- It includes degraded and unknown states.
- It can be consumed by backend APIs, frontend badges, and MCP outputs.

**Suggested output artifact(s)**
- Update `docs/STRATEGIC_BACKLOG.md` only if scope changes.
- Prefer a new implementation-oriented status spec in docs if needed.

---

### 2. PLAT-REL-002 — Diagnostic Payload Standardization

**Why**
Operators need more than a badge. They need a standard explanation payload that can be rendered consistently in UI, logs, and AI responses.

**Deliverables**
- A shared diagnostic payload contract.
- Example payloads for healthy, degraded, blocked, and unknown scenarios.
- Mapping guidance for connectivity, fleet, and health endpoints.

**Acceptance criteria**
- Payload includes message, severity, evidence/context, and suggested next action.
- Payload is generic enough to reuse beyond connectivity.
- Examples cover at least one positive and three failure/degraded scenarios.

**Suggested output artifact(s)**
- New schema/spec doc or implementation note.

---

### 3. PLAT-REL-003 — Truthful Status Surface Audit

**Why**
Before changing code broadly, the team needs a concrete list of where current UX/API semantics are misleading or inconsistent.

**Deliverables**
- Audit of current operator-facing status surfaces.
- Prioritized findings list.
- Recommendations grouped by backend contract, frontend UX, or both.

**Acceptance criteria**
- Audit covers cluster list/detail, fleet health, BNK health, and relevant diagnostics views.
- Findings distinguish “false green,” “ambiguous,” “missing explanation,” and “inconsistent naming.”
- Findings are prioritized by operator impact.

**Suggested output artifact(s)**
- Audit doc or section in an existing strategic/implementation doc.

---

### 4. UX-OPS-002 — Status Badge Vocabulary and Visual Semantics

**Why**
Even with better backend semantics, operators still need a clean, consistent, scan-friendly UI representation.

**Deliverables**
- Badge label set.
- Mapping from canonical statuses to badge variants.
- Tooltip/description guidance.
- Accessibility guidance for labels/icons/colors.

**Acceptance criteria**
- Badge labels reflect operator reality, not internal implementation shortcuts.
- Badge spec is consistent with PLAT-REL-001.
- Guidance covers degraded and unknown states clearly.

**Suggested output artifact(s)**
- UX/status spec doc or implementation guidance.

---

## Recommended Execution Order

### Phase 1 — Semantics first
1. **PLAT-REL-001**
2. **PLAT-REL-002**

These are the foundation. Do not start implementation changes to broad status surfaces before these are defined.

### Phase 2 — Audit current product reality
3. **PLAT-REL-003**

Once the target model exists, compare the actual system against it.

### Phase 3 — UI alignment
4. **UX-OPS-002**

Use the results of the model + audit to define the final badge vocabulary and visual semantics.

---

## Parallelization Plan for Agents

### Agent A — Status Model Owner
**Primary ticket:** PLAT-REL-001

**Scope**
- Define canonical status vocabulary.
- Review current cluster/fleet/connectivity terminology.
- Produce mapping proposal.

**Do not do**
- Broad UI code changes.
- Route refactors outside status modeling work.

### Agent B — Diagnostic Contract Owner
**Primary ticket:** PLAT-REL-002

**Scope**
- Define shared diagnostic payload.
- Produce examples and portability guidance.
- Coordinate terminology with Agent A.

**Dependency note**
- Should align with Agent A on naming before finalizing examples.

### Agent C — Surface Audit Owner
**Primary ticket:** PLAT-REL-003

**Scope**
- Audit current cluster/fleet/BNK status surfaces.
- Produce prioritized gap list.

**Dependency note**
- Can begin early, but final classification should be updated after Agent A finalizes canonical states.

### Agent D — Badge/UX Semantics Owner
**Primary ticket:** UX-OPS-002

**Scope**
- Define badge vocabulary and visual mapping.
- Propose tooltip semantics and accessibility guidance.

**Dependency note**
- Should start after PLAT-REL-001 draft exists.

---

## Suggested Branching / Ownership Pattern

If running in parallel, prefer one branch or worktree per ticket:

- `agent/plat-rel-001`
- `agent/plat-rel-002`
- `agent/plat-rel-003`
- `agent/ux-ops-002`

Keep changes documentation-first unless the coordinating lead explicitly promotes tickets into implementation in the main backlog.

---

## Suggested Deliverable Format Per Ticket

Each agent should produce:

1. **Summary**
   - What the ticket solves
2. **Current-state findings**
   - If auditing or gap analysis was involved
3. **Proposal**
   - The actual recommended model/spec/pattern
4. **Impact**
   - Backend / frontend / MCP / docs implications
5. **Follow-on implementation tickets**
   - Concrete coding work that should come next

---

## Definition of Sprint Success

The sprint is successful when:

- The team has one agreed status vocabulary.
- Diagnostic payload semantics are documented.
- Major misleading/inconsistent status surfaces are identified.
- The UI badge vocabulary is aligned to the canonical model.
- Follow-on implementation work can be created without re-debating fundamentals.

---

## Follow-On Tickets Expected After This Sprint

These are likely to become the next execution wave:

- implement canonical backend status enums/models
- update key API responses to use diagnostic payloads
- align cluster list/detail and fleet views to new status semantics
- propagate truthful state model into MCP tool outputs
- add tests around new status contracts and badge mappings

---

## Related Documents

- [Strategic Backlog](STRATEGIC_BACKLOG.md)
- [Strategic Roadmap](STRATEGIC_ROADMAP.md)
- [MCP Productization Plan](MCP_PRODUCTIZATION_PLAN.md)
