# CODE-AUDIT-002 — Repo-wide Maintainability Audit Methodology

## Executive Summary

This item defines **how** Forge should perform a repo-wide maintainability audit later, not the audit itself.

The recommended approach is a **three-layer audit method**:

1. **Lightweight inventory** to measure size, concentration, and candidate hotspots.
2. **Focused manual review** of the highest-signal files, directories, and patterns.
3. **Risk categorization and bounded follow-on slicing** so the outcome becomes small, actionable backlog items instead of a vague cleanup epic.

The audit must remain **evidence-first**. It should distinguish:
- intentional duplication from accidental drift,
- compatibility scaffolding from dead code,
- real partial implementations from harmless placeholder text,
- minor untidiness from changes that materially increase bug risk or delivery cost.

The end product should be a decision-quality report that identifies:
- where maintainability risk is concentrated,
- what is safe to clean up,
- what requires bounded refactor work,
- what is architecture debt,
- and what should become small follow-on backlog slices.

## Goals

The eventual audit should answer these questions concretely:

- Where is codebase size concentrated?
- Which files/directories appear disproportionately large or complex?
- Where is duplication creating regression risk or semantic drift?
- Which placeholders, stubs, or partial implementations matter operationally?
- Which paths look stale, legacy, or weakly justified?
- What are the top maintainability risks, ranked by impact and confidence?
- What small, bounded follow-on slices should be created from the findings?

## Non-Goals

The audit must **not**:

- become a rewrite plan for the whole repo
- treat every `TODO`, `pass`, or comment marker as a defect
- assume all duplication is bad without boundary/context review
- merge backend/frontend/MCP concerns into one forced abstraction
- turn style preferences into audit findings
- recommend deletion/removal without reference and usage checks
- produce one giant “cleanup the repo” epic

## Scope

The audit should cover at minimum:

- `backend/`
- `frontend-v2/`
- `mcp-server/`
- key shared/config/docs surfaces where truthfulness or drift matters:
  - root docs
  - `docs/`
  - config and compose files
  - `.agent/` only where planning drift materially affects maintainability decisions

The audit should be repo-wide in visibility, but **not equally deep everywhere**. Depth should follow risk and signal.

## Audit Methodology

### 1. Lightweight Inventory Pass

Purpose: establish measurable baseline and identify where manual review should focus.

#### 1.1 Size / concentration inventory
Collect:

- LOC by major area:
  - backend
  - frontend-v2
  - mcp-server
  - tests
  - docs/config if useful
- largest directories by LOC
- largest files by LOC
- unusually dense “hub” files or modules
- test-to-source concentration where relevant

#### 1.2 Candidate pattern inventory
Collect raw candidate lists for:

- repeated status/enum/mapping logic
- repeated auth/source/secret handling
- repeated API/serialization/adapter patterns
- repeated UI action gating/badges/status rendering
- placeholder/stub markers:
  - `TODO`
  - `FIXME`
  - `XXX`
  - `pass`
  - `NotImplementedError`
  - “placeholder”
  - “stub”
  - “temporary”
- stale/dead-path indicators:
  - archived/legacy naming
  - deprecated compatibility surfaces
  - old docs referencing retired flows
  - files/dirs with suspiciously weak references

#### 1.3 Prior evidence cross-check
Cross-check candidate hotspots against existing repo knowledge:

- prior audit docs
- architecture/spec docs
- backlog/spec concerns already recorded
- known drift themes already documented

This prevents rediscovering known issues without adding prioritization.

### 2. Focused Manual Review Pass

Purpose: convert raw matches into evidence-backed findings.

The audit should **not** manually inspect every match. It should inspect:

- the top size-concentrated files/directories
- the highest-frequency duplication candidates
- the most operationally risky placeholder/partial markers
- stale/dead-path candidates with the strongest evidence of drift

#### 2.1 Manual review rules
For each candidate hotspot, review enough surrounding context to answer:

- Is this intentional duplication across product boundaries?
- Is the repeated logic semantically coupled and likely to drift?
- Is the placeholder/stub harmless, internal-only, or user-facing?
- Is the path truly stale, or is it compatibility scaffolding with live references?
- Would cleanup be safe, bounded refactor, or architecture-level work?

#### 2.2 Required evidence standard
A finding should only be promoted if it includes at least some combination of:

- concrete file/path references
- repeated example patterns
- evidence of semantic drift risk or maintenance burden
- evidence of user/operator confusion, bug risk, or refactor friction
- reference mismatch between docs/config/code where relevant

Raw grep counts alone are insufficient.

### 3. Risk Categorization Pass

Purpose: rank findings by maintainability importance rather than by count.

Each finding should be classified by:

- **type**
  - size/concentration
  - duplication/DRY
  - placeholder/partial
  - stale/dead-path
  - docs/truthfulness drift
  - architecture debt

- **impact**
  - low
  - medium
  - high
  - critical

- **confidence**
  - low
  - medium
  - high

- **recommended action**
  - safe cleanup
  - bounded refactor
  - architecture decision / design follow-on
  - docs/truthfulness refresh
  - defer / monitor

- **blast radius**
  - local
  - subsystem
  - cross-cutting

## Required Analysis Dimensions

### 1. Codebase Size / Concentration

The audit should assess:

- LOC by major area
- top 10 largest files
- top 10 largest directories/subtrees
- concentration of business logic into “god files” or overloaded service layers
- places where ownership or reasoning cost appears too concentrated

#### What counts as a meaningful finding
- very large files that act as multi-purpose hubs
- directories with disproportionate complexity concentration
- areas where change risk is high because many unrelated concerns meet in one file

#### What does not count by itself
- a large file that is stable, cohesive, and low-churn
- generated artifacts unless they are mistakenly treated as hand-maintained

### 2. Duplication / DRY Hotspots

The audit should look for duplication in:

- business logic
- status/severity/state mapping
- auth/provider/source credential handling
- secret validation / prerequisite logic
- API response normalization / serialization
- UI action gating / engine/capability semantics
- repeated docs/runbook instructions that may drift

#### Distinguish intentional vs accidental duplication
Treat duplication as **intentional/acceptable** when:

- it cleanly reflects different runtime boundaries
- it preserves explicit contracts between backend/frontend/MCP
- shared abstraction would introduce unsafe coupling or indirection
- duplication is small and stable

Treat duplication as **problematic** when:

- the same semantics are re-encoded in multiple places
- similar flows have already drifted in behavior or vocabulary
- the same bug would likely need to be fixed in many places
- a shared helper/model/policy would reduce risk without harming boundaries

#### Required output style
Do not say “duplication exists” in the abstract. Report:

- duplicated concern
- where it appears
- whether it is intentional or accidental
- actual risk created
- likely action type

### 3. Placeholder / Stub / Partial Implementation Inventory

The audit should identify:

- explicit markers (`TODO`, `FIXME`, `pass`, `NotImplementedError`)
- incomplete branches that look production-ready but degrade truthfully
- UI affordances that imply support beyond actual implementation
- compatibility paths that are intentionally partial but insufficiently labeled

#### Required distinction
Every item should be classified as one of:

- harmless/internal note
- intentional bounded scaffold
- deferred but truthfully surfaced limitation
- misleading partial implementation
- likely abandoned path

The highest-value findings are **misleading partial implementations**, not comment counts.

### 4. Dead or Stale Path Identification

The audit should identify candidates, not declare removal automatically.

Candidate stale/dead paths include:

- legacy flows contradicted by active architecture direction
- docs/runbooks that point to retired/non-preferred workflows
- code paths with weak usage/reference evidence
- compatibility layers whose owning decision is unclear
- tests/CI/docs still keeping an old path alive

#### Required evidence for stale-path claims
Each stale-path candidate should include:

- why it appears stale
- what still references it
- whether it is:
  - safe cleanup candidate
  - legacy-but-live
  - archive candidate
  - removal-plan candidate requiring deliberate retirement

A path with live code/test/CI/docs references should not be labeled dead; it should be labeled **legacy but still active**.

### 5. Maintainability Risk Ranking

The audit should rank findings by practical engineering cost, not aesthetics.

#### Ranking factors
Score findings using these dimensions:

- **bug risk** — how likely drift or confusion causes real defects
- **change friction** — how much it slows normal delivery
- **operator/user confusion** — whether the repo/product appears to support something it does not
- **cross-surface drift risk** — backend/frontend/MCP/docs mismatch potential
- **blast radius** — how many workflows/files are affected
- **remediation cost** — safe quick win vs deep refactor

#### Suggested priority bands

##### P0 — Critical maintainability risk
Use when the issue:
- creates active regression risk,
- causes product truthfulness problems,
- or makes routine changes unsafe across multiple surfaces.

##### P1 — High-value follow-up
Use when the issue:
- materially slows delivery,
- causes repeated semantic drift,
- or concentrates too much logic in fragile areas.

##### P2 — Useful bounded cleanup
Use when the issue:
- is real but locally contained,
- can be addressed safely in a small slice,
- and is unlikely to create immediate bugs if deferred.

##### P3 — Track / defer
Use when the issue:
- is mostly cosmetic,
- weakly evidenced,
- or only worth revisiting if adjacent work touches the area.

## How to Avoid Low-Value Noise

The audit must explicitly reduce false positives.

### 1. Placeholder noise controls
Do **not** count the following as findings without context:

- UI placeholder text for form fields
- “skeleton/loading placeholder” component naming
- examples in docs/specs
- archived docs unless they still mislead active workflows
- comments that describe future ideas but do not affect runtime truth

### 2. `pass` / `TODO` noise controls
Do not assume every `pass` or `TODO` is a defect. Only escalate when:

- it sits in an active runtime path
- it suppresses behavior the UI/docs imply exists
- it hides error handling or fallback gaps
- it appears in a critical service/integration path

### 3. Duplication noise controls
Do not flag duplication solely because two systems expose similar data. Escalate only when:

- the same semantics are maintained separately without a clear owner
- observable drift already exists or is likely
- consolidation would reduce risk more than it adds coupling

### 4. Stale-path noise controls
Do not call a path dead solely because it feels old. Require some combination of:

- weak or no active references
- conflicting current architecture direction
- obsolete docs/config
- maintenance burden without current product justification

## Expected Deliverable Structure for the Eventual Audit

### 1. Executive Summary
- 1–2 page summary
- overall maintainability posture
- top 5 risks
- top 5 bounded follow-on recommendations

### 2. Audit Scope and Method
- areas reviewed
- inventory methods used
- manual-review focus rules
- limitations/confidence notes

### 3. Repo Size Summary
- LOC by major area
- largest directories
- largest files
- concentration observations

### 4. Top Maintainability Hotspots
For each hotspot:
- title
- type
- affected paths
- evidence summary
- why it matters
- risk rank
- recommended action type

### 5. Duplication Inventory
For each duplication cluster:
- concern/semantic area
- file/path examples
- intentional vs accidental assessment
- drift risk
- recommended next step

### 6. Placeholder / Partial / Stub Inventory
For each item:
- path
- marker/type
- runtime relevance
- classification
- recommendation

### 7. Dead / Stale Path Inventory
For each candidate:
- path or surface
- why it looks stale
- current references
- confidence
- recommended disposition

### 8. Recommendations by Action Type
Grouped into:
- safe cleanup
- bounded refactor
- architecture debt / design follow-on
- docs/truthfulness refresh
- defer / monitor

### 9. Prioritized Follow-on Backlog Proposals
Each proposed slice should include:
- title
- rationale
- bounded scope
- likely files/areas
- risk level
- expected validation shape
- dependencies if any

### 10. Appendix / Evidence Tables
- raw size tables
- candidate match inventories
- reference notes
- excluded false-positive examples if useful

## Recommended Finding Template

### Finding: [Short title]

- **Type:** duplication | size/concentration | placeholder/partial | stale/dead-path | docs drift | architecture debt
- **Priority:** P0 | P1 | P2 | P3
- **Confidence:** low | medium | high
- **Blast radius:** local | subsystem | cross-cutting
- **Affected paths:** `path/a`, `path/b`
- **Summary:** one paragraph
- **Evidence:**
  - concrete example 1
  - concrete example 2
  - concrete example 3
- **Why it matters:** bug risk / delivery friction / truthfulness / operator confusion
- **Intentional vs accidental:** explicit assessment
- **Recommended action:** safe cleanup | bounded refactor | design follow-on | docs refresh | defer
- **Suggested follow-on slice:** one bounded ticket-sized next step

## Bounded Follow-on Slice Types

The audit should produce follow-ons only in these bounded categories:

### 1. Safe Cleanup Slice
Use for:
- dead comments/config/docs with low blast radius
- clearly unused local helper/path after reference review
- archive/promote/remove work that does not alter active behavior

### 2. Docs / Truthfulness Refresh Slice
Use for:
- active docs contradicting current behavior
- UI/support wording drift
- runbook/config guidance mismatch

### 3. Local Refactor Slice
Use for:
- one file or one tightly related cluster of files
- extraction/consolidation with limited behavior change
- repeated mapping/helper logic in a bounded subsystem

### 4. Cross-Surface Contract Alignment Slice
Use for:
- backend/frontend/MCP/docs semantic drift
- duplicated status/capability/auth semantics with a clear source-of-truth fix

### 5. Legacy Retirement Planning Slice
Use for:
- old paths that cannot yet be removed
- compatibility scaffolding needing explicit ownership and staged retirement

### 6. Architecture Debt Investigation Slice
Use for:
- findings too large or ambiguous for immediate refactor
- cases requiring design decision before cleanup

### 7. Test/Verification Hardening Slice
Use for:
- areas where drift persists because regression guards are missing
- contract/coverage gaps that make cleanup risky

## Guardrails for Follow-on Slice Creation

Every follow-on created from the audit should:

- fit within a single builder session when possible
- name exact affected area(s)
- state whether it changes behavior or only structure/docs
- define validation expectations
- avoid combining cleanup, refactor, and architecture redesign in one ticket
- preserve proven boundary decisions unless a separate design item reopens them

## Success Criteria for CODE-AUDIT-002 Completion

This planning item is complete when the methodology is concrete enough that another agent can run the audit consistently and produce:

- a measurable repo profile
- evidence-backed hotspot findings
- explicit intentional-vs-accidental duplication judgments
- low-noise placeholder/stale-path inventories
- risk-ranked recommendations
- small, actionable follow-on backlog slices

## Assumptions / Open Questions

- Assumes lightweight inventory tooling will be available to the executing agent, but the methodology should remain valid even if exact commands change.
- Assumes archived docs are generally lower priority unless they still shape active operator/developer behavior.
- Open question: whether the eventual audit should include simple churn/history signals if available, or remain strictly current-state structural analysis.
- Open question: whether generated artifacts should be fully excluded from size ranking or reported separately for completeness.
