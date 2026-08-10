# PLATFORM-VERSION-001 — BNK Release Maintenance Model for Platform-Context Truth

## Executive Summary

**Recommendation:** adopt a **data-first, backend-owned BNK release registry** for version-aware platform truth, with imperative code limited to detection, normalization, and evaluation logic.

Forge should treat BNK-version-aware support as a governed maintenance discipline, not a scattered implementation habit. The shared backend remains the single source of truth for version-aware platform semantics consumed by frontend, Fleet, MCP, and docs-adjacent support surfaces. New BNK releases should be incorporated through a repeatable workflow that updates declarative release metadata, runs a fixed validation matrix, and only then expands support claims.

### Core recommendation in one line

- **Declarative data owns release facts**
- **Backend code owns interpretation and enforcement**
- **All consumers read backend truth**
- **Support claims ship only after evidence-backed validation**

## Recommended Release-Maintenance Model

### Decision

Use a **hybrid model with a strong declarative center**:

1. **Declarative BNK release metadata**
   - owns version-specific facts, support statements, compatibility declarations, readiness expectations, and known caveats
2. **Imperative backend logic**
   - owns detection, version parsing/normalization, compatibility evaluation, gating decisions, and API serialization
3. **Consumer surfaces**
   - frontend, Fleet, MCP, and operator-facing docs consume backend-computed truth rather than re-encoding version logic

This preserves the already-accepted platform-context architecture while giving future BNK releases a controlled update path.

## Required Decision Dimensions

### 1) Truth Source Design

#### Recommendation

BNK-version-dependent facts should be split as follows:

**Belongs in declarative data**
- supported BNK release identifiers and support status
- per-release capability availability/expectations
- per-release known caveats and support notes
- per-release readiness requirement deltas
- compatibility declarations between BNK release and platform-context semantics
- support tier statements such as:
  - supported
  - limited support
  - validation in progress
  - deprecated
  - unsupported
- release-specific action-gating flags when they are policy/data driven rather than algorithmic
- release-specific minimum/expected prerequisite declarations

**Belongs in imperative code**
- BNK version detection/parsing from runtime evidence
- normalization of raw version strings into canonical release identifiers
- evaluation logic combining:
  - detected platform context
  - detected BNK version
  - cluster capabilities
  - release metadata
- fallback behavior when version evidence is missing/partial
- API shaping and serialization
- orchestration/detection code that gathers evidence from clusters/scans
- conflict resolution when multiple signals disagree

#### Why

Release facts change more often than platform-evaluation logic. If support truth lives mainly in code, each BNK release becomes a code-branching exercise. If logic lives mainly in data, consumers stay aligned and release maintenance becomes reviewable and auditable.

### 2) Ownership Boundary

#### Recommendation

**Backend-owned release metadata** should be the canonical owner of BNK-version-aware truth.

Ownership should be conceptually divided like this:

- **Release metadata artifact owner:** backend/platform-awareness domain
- **Interpretation owner:** shared backend platform-context services
- **Consumer owner:** no independent ownership of semantics; consumers only render backend outputs
- **Documentation owner:** docs summarize shipped support position, but do not define support semantics independently

#### Practical boundary

- Frontend must not keep its own BNK version compatibility tables
- Fleet must not maintain separate BNK support vocabulary
- MCP must not reclassify support semantics on its own
- Docs may explain support, but the executable truth must come from backend-managed metadata

This matches the anti-drift rule already accepted.

### 3) Consumer Consistency

#### Recommendation

All version-aware outputs should flow through the same backend evaluation path.

That means:
- scan/readiness uses backend release-aware evaluation
- action gating uses backend release-aware evaluation
- module/blueprint/platform support messaging uses backend release-aware evaluation
- Fleet uses backend release-aware evaluation
- MCP exposes backend release-aware evaluation, not MCP-local rules

#### Consistency rule

If a surface needs version-aware truth, it should consume one of:
1. a shared backend service result, or
2. a backend API field derived from that shared service

Never duplicate release interpretation in:
- TypeScript constants
- Fleet-only helpers
- MCP-only mapping tables
- prose-only docs

### 4) Backward Compatibility

#### Recommendation

Forge should reason about **multiple supported BNK baselines concurrently**, with explicit status per release line.

Suggested policy:
- maintain explicit truth for:
  - **current target release**
  - **previous supported release**
  - **older releases only while formally still supported**
- once a release is no longer supported by product policy, Forge may:
  - retain detection/identification
  - downgrade support semantics to deprecated/unsupported
  - remove deep validation commitments after a defined sunset window

#### Practical rule

Forge should not pretend all BNK versions behave like the newest one. Older supported versions remain first-class in metadata until intentionally sunset.

### 5) Validation Discipline

#### Recommendation

No new BNK release should be marked supported based only on code inspection or optimistic assumptions.

A new release support claim should require:
- metadata update
- targeted fixture/test expansion
- regression validation of shared backend semantics
- UI/Fleet/MCP consumption verification
- at least one evidence-backed runtime validation path for the release profile being claimed

Support claims should be staged:
1. detected
2. modeled
3. validated
4. shipped as supported

### 6) Operational Workflow

#### Recommendation

Define a release update workflow triggered by BNK release events and executed as a bounded maintenance package.

Primary trigger:
- new BNK release candidate or GA release

Secondary triggers:
- field evidence that an existing supported release behaves differently than modeled
- support-policy change for an older release
- discovery of a release-specific caveat affecting readiness/gating truth

## Data vs Code Ownership

### Data Ownership: What belongs in declarative metadata

The declarative layer should contain **facts and policy statements**, not procedural logic.

#### Put in data
- canonical BNK release identifiers
- support status per release
- support tier/claim wording
- known caveats by release
- release-specific capability expectations
- release-specific readiness requirement deltas
- release-specific gating declarations where the condition is straightforward
- compatibility notes tied to platform profiles/capabilities
- sunset/deprecation markers for older releases
- evidence/status markers such as:
  - planned
  - validation_in_progress
  - supported
  - deprecated
  - unsupported

#### Examples of suitable data facts
- BNK 2.2 generic existing-cluster support is supported with defined caveats
- BNK 2.3 introduces/changes requirement expectations for specific readiness checks
- BNK 2.x on specific platform profiles has declared limitations
- a given release requires revised wording or gating around a capability

### Code Ownership: What stays imperative

The imperative layer should contain **logic and evidence interpretation**.

#### Keep in code
- detect raw BNK version from cluster/runtime signals
- normalize variants like patch/build strings into canonical release family
- decide which metadata record applies
- merge release metadata with platform-context and cluster scan results
- compute final readiness/gating/support outputs
- handle missing/unknown version cases truthfully
- serialize outputs to APIs used by frontend/Fleet/MCP

#### Rule of thumb

If it is a **changing support fact**, prefer data.
If it is **how facts are derived or combined**, keep it in code.

## Where Version-Aware Metadata Should Live Conceptually

### Recommendation

Version-aware metadata should live as a **backend-managed structured support registry** within the platform-awareness domain, conceptually adjacent to platform-context truth, not scattered across:
- module catalogs
- frontend types
- docs prose
- Fleet-specific assets
- MCP metadata

### Conceptual placement

The metadata should be modeled as:

1. **BNK Release Registry**
   - release identity
   - support status
   - support window/deprecation state

2. **BNK Release Capability/Constraint Metadata**
   - declarative per-release capability expectations
   - readiness/gating caveats
   - known support limitations

3. **Shared Evaluation Service**
   - combines platform context + detected BNK version + release metadata

This should be one coherent backend truth source, even if later implemented across a small number of files/artifacts.

### Recommended conceptual rule

- **Platform-context vocabulary remains the stable schema**
- **BNK release metadata parameterizes that schema over time**

That avoids redesigning vocabulary while still supporting release evolution.

## Practical Release Workflow / Checklist

### Trigger

Start the workflow when any of the following occurs:
- BNK release candidate announced for intended support
- BNK GA release shipped
- field validation reveals behavior drift in a supported BNK version
- support/deprecation policy changes for an older version

### Release Update Steps

#### Phase 1 — Intake and classification
- identify the BNK release line and canonical version label to represent
- determine whether this is:
  - additive support
  - behavior change
  - caveat change
  - deprecation/sunset update

#### Phase 2 — Evidence gathering
- collect release notes, internal validation notes, and field findings
- identify which platform-context/readiness/gating semantics changed, if any
- explicitly record whether changes are:
  - metadata-only
  - code-required
  - both

#### Phase 3 — Metadata update design
- add/update the release registry entry
- add/update per-release capability/caveat/readiness declarations
- define support status:
  - validation_in_progress
  - supported
  - deprecated
  - unsupported
- define any older-version status changes caused by the new release

#### Phase 4 — Backend impact review
- verify whether shared evaluation logic already supports the new release facts
- if not, identify minimal code changes required for:
  - detection normalization
  - evaluation logic
  - serialization
- confirm no consumer-local semantics are needed

#### Phase 5 — Validation execution
Run the required matrix:
- backend unit/contract tests for shared model outputs
- version-aware fixtures/golden cases
- scan/readiness/gating verification
- Fleet surface verification
- MCP surface verification
- representative runtime validation for claimed support paths

#### Phase 6 — Ship decision
Only mark the release as supported if:
- metadata is complete
- validation matrix passes
- known caveats are documented truthfully
- consumers display consistent backend-driven support truth
- at least one representative support path has evidence

#### Phase 7 — Publish/support communication
- update product-facing support summary/docs
- state support status and caveats clearly
- if older versions are affected, publish deprecation/sunset note

## Ship Criteria

A BNK release is ready to ship as supported only when all are true:

- canonical release metadata exists
- backend evaluation returns stable, truthful outputs for the release
- no frontend/Fleet/MCP local workaround is needed
- regression suite passes
- version-specific fixtures/golden cases pass
- known caveats are surfaced, not hidden
- backward-compatibility status for older supported releases is updated
- support statement is evidence-backed, not inferred

## Validation / Test Matrix for New BNK Releases

### Minimum required matrix

#### A. Metadata integrity
- release entry exists and is internally consistent
- support status is defined
- required release fields are complete
- caveats/limitations are present where applicable

#### B. Backend shared-model validation
- version normalization resolves expected release family correctly
- shared platform-context evaluation remains stable
- release-aware readiness outputs match declared metadata
- release-aware gating outputs match declared metadata
- unknown/missing-version fallback remains truthful

#### C. Contract/API validation
- backend API responses carrying release-aware truth remain shape-stable
- response-model/contract coverage for affected surfaces remains green
- additive fields remain backward compatible where required

#### D. Consumer verification
- frontend renders backend release-aware truth without parallel logic
- Fleet reflects release-aware backend truth
- MCP returns the same backend-driven semantics
- no consumer introduces its own version table or caveat mapping

#### E. Representative runtime evidence
For each newly claimed support release, validate at least:
- one generic existing-cluster path
- any platform profile explicitly claimed as supported for that release
- any release-specific caveat scenario that affects readiness/gating

#### F. Regression coverage
Re-run or expand:
- shared platform-context tests
- scan/readiness tests
- action-gating tests
- compatibility summary tests
- Fleet platform-context propagation tests
- MCP platform-context propagation tests
- any release-aware golden fixture suite introduced for this model

## Evidence Threshold by Support Status

### validation_in_progress
Allowed when:
- detection/metadata exist
- not enough runtime evidence yet
- UI/Fleet/MCP remain truthful about limited certainty

### supported
Allowed only when:
- validation matrix is complete
- representative runtime evidence exists
- known caveats are documented

### deprecated
Allowed when:
- release remains recognized
- support is still partially maintained
- sunset window and limitations are explicit

### unsupported
Allowed when:
- release is still detectable
- Forge truthfully states lack of support
- no false readiness/support claim remains

## Backward Compatibility Policy

### Recommendation

Adopt an explicit **N / N-1 / sunset** model.

- **N** = current primary supported BNK release line
- **N-1** = previous supported line with active explicit truth
- **Sunset set** = older versions still recognized but marked deprecated or unsupported according to policy

### Policy details

1. **Recognition persists longer than deep support**
   - older versions may still be detected and labeled truthfully even after deep validation ceases

2. **Support status must be explicit**
   - never silently inherit the newest release semantics for older releases

3. **Additive-first evolution**
   - new release metadata should extend shared truth where possible rather than breaking existing consumers

4. **Sunsetting is a documented state change**
   - moving a release from supported to deprecated/unsupported must be a deliberate metadata and docs update, not incidental neglect

5. **Unknown versions must fail truthfully**
   - if Forge detects a BNK version it does not model, outputs should indicate limited confidence/unsupported evaluation rather than optimistic support

## Recommended Bounded Follow-On Implementation Slices

These are planning-only follow-ons suitable for later backlog slicing.

### 1. BNK release registry foundation
Create the backend-owned structured metadata artifact for BNK release support/caveat/capability truth.

**Scope**
- canonical release entry format
- support status vocabulary
- caveat/constraint structure
- ownership and loading path

### 2. Version normalization and shared evaluation plumbing
Extend shared backend platform-context evaluation to resolve detected BNK version against the release registry.

**Scope**
- canonical version matching
- unknown-version handling
- registry lookup integration
- additive API fields if needed

### 3. Release-aware readiness and action-gating integration
Make scan/readiness/gating surfaces consume release-aware backend truth from the shared evaluator.

**Scope**
- no new vocabulary
- no frontend-local logic
- preserve generic existing-cluster truthfulness

### 4. Fleet and MCP release-truth propagation
Ensure Fleet and MCP expose the same release-aware semantics already computed by backend.

**Scope**
- additive propagation only
- no parallel classification logic

### 5. Version-aware golden fixtures and validation harness
Add fixture-driven regression coverage for supported BNK release lines.

**Scope**
- release-specific fixtures
- golden expected outputs for readiness/gating/support semantics
- unknown-version fallback cases

### 6. Support-policy/docs alignment slice
Add bounded documentation surfaces that summarize shipped support status from the backend-owned model.

**Scope**
- release support summary
- deprecation/sunset guidance
- operator-facing workflow notes

## Recommended Final Position

Forge should adopt a **backend-owned declarative BNK release registry plus shared evaluator** as the maintenance model for BNK-version-aware platform truth.

This is the best fit because it:
- preserves the accepted shared platform-context architecture
- prevents frontend/Fleet/MCP drift
- keeps release updates mostly data-driven
- reserves code changes for true logic/detection needs
- enables explicit multi-version support and sunset policy
- gives future BNK releases a repeatable, auditable path to support

## Assumptions / Open Questions

- Assumption: Forge intends to support multiple BNK release lines concurrently, at least for a limited overlap window.
- Assumption: version detection signals already exist or can be normalized without redesigning the broader platform-context model.
- Open question: what exact support-window policy should product adopt for BNK release lines beyond the recommended N / N-1 / sunset pattern?
- Open question: should release metadata include formal evidence-state fields visible in APIs, or remain internal and only surface resulting support status?
- Open question: what minimum runtime evidence is required per platform profile before marking a new BNK release fully supported across that profile?
