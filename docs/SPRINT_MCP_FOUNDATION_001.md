# Sprint Plan — MCP Foundation 001

> Agent-ready sprint plan for the third strategic execution wave. Focus: establish the foundations required to productize the BNK Forge AI-operable interface.

Last updated: 2026-03-27 | Status: Ready for execution

---

## Sprint Summary

**Sprint ID:** `mcp-foundation-001`  
**Source Work Package:** Work Package C — MCP Foundation  
**Primary objective:** create the inventory, safety model, route verification, and critical contract map needed to safely evolve MCP as a first-class product interface.

---

## Sprint Goals

1. Build a canonical tool inventory.
2. Standardize tool naming and taxonomy.
3. Classify tool safety and blast radius.
4. Verify every tool’s backend route mapping.
5. Define the critical MCP tool contract set.

---

## Tickets in Scope

### MCP-PROD-001 — Tool Catalog Inventory

**Why**
The MCP surface is strategically important, but it cannot be safely evolved without a canonical tool inventory.

**Deliverables**
- Full inventory of MCP tools.
- Domain grouping.
- Backing route/method/auth mapping fields.

**Acceptance criteria**
- Inventory covers all current tools.
- Catalog is usable by engineering, docs, and governance work.

---

### MCP-PROD-002 — Tool Taxonomy and Naming Standard

**Why**
Tool discoverability and consistency matter for both human operators and AI assistants.

**Deliverables**
- Naming convention.
- Domain grouping standard.
- Tool description guidelines.

**Acceptance criteria**
- Standard is explicit enough to review current names against it.
- It supports safe discoverability and future growth.

---

### MCP-PROD-003 — Tool Safety Classification

**Why**
AI-operable systems need a clear safety model before deeper autonomy is considered.

**Deliverables**
- Safety classes.
- Initial provisional classification for all tools.
- Notes on risky or ambiguous tools.

**Acceptance criteria**
- Every tool can be assigned to a class.
- Classes are meaningful for docs, audit, and future policy.

---

### MCP-PROD-004 — Route Mapping Verification

**Why**
The project already learned that guessing routes causes real breakage. This ticket verifies the MCP surface against actual backend reality.

**Deliverables**
- Verification record for each tool.
- List of mismatches or uncertain mappings.
- Prioritized remediation guidance.

**Acceptance criteria**
- Every tool is reviewed against real route definitions.
- Parameter/auth mismatches are captured clearly.

---

### MCP-PROD-005 — Critical Tool Contract Matrix

**Why**
Some tools are too important to leave under-specified. This defines the first class of MCP contracts that need exact validation.

**Deliverables**
- Critical tool list.
- Expected output contract notes.
- Suggested validation/test ownership.

**Acceptance criteria**
- Critical tools reflect operator value and product risk.
- Matrix is specific enough to guide implementation.

---

## Recommended Execution Order

1. **MCP-PROD-001**
2. **MCP-PROD-002**
3. **MCP-PROD-003**
4. **MCP-PROD-004**
5. **MCP-PROD-005**

Catalog and taxonomy should exist before finalizing safety classification and contract priorities.

---

## Parallelization Plan for Agents

### Agent A — Catalog Owner
**Primary ticket:** MCP-PROD-001

### Agent B — Taxonomy Owner
**Primary ticket:** MCP-PROD-002

**Dependency note**
- Should coordinate naming/domain assumptions with Agent A.

### Agent C — Safety Classification Owner
**Primary ticket:** MCP-PROD-003

**Dependency note**
- Can draft classes early; final classification should use Agent A catalog.

### Agent D — Route Verification Owner
**Primary ticket:** MCP-PROD-004

### Agent E — Critical Contract Matrix Owner
**Primary ticket:** MCP-PROD-005

**Dependency note**
- Should use Agent A/D output for final prioritization.

---

## Suggested Branching / Ownership Pattern

- `agent/mcp-prod-001`
- `agent/mcp-prod-002`
- `agent/mcp-prod-003`
- `agent/mcp-prod-004`
- `agent/mcp-prod-005`

---

## Suggested Deliverable Format Per Ticket

1. Summary
2. Current-state findings
3. Proposal
4. Impact on MCP/API/docs/governance
5. Follow-on implementation tickets

---

## Definition of Sprint Success

- MCP tools are fully inventoried.
- Naming/taxonomy guidance exists.
- Safety classification exists.
- Route verification is complete or near-complete.
- Critical MCP contracts are identified.

---

## Follow-On Tickets Expected After This Sprint

- telemetry instrumentation
- MCP error semantics
- operator guide
- compatibility/deprecation policy
- contract validation automation

---

## Related Documents

- [MCP Productization Plan](MCP_PRODUCTIZATION_PLAN.md)
- [Strategic Backlog](STRATEGIC_BACKLOG.md)
- [Strategic Roadmap](STRATEGIC_ROADMAP.md)
