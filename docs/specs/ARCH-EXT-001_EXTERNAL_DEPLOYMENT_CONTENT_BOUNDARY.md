# ARCH-EXT-001 — External Deployment Content Boundary Recommendation

## Executive Summary

- **Recommendation: choose Option B** — external modules and external blueprints — with a **strict governed import/sync model**, not ad hoc repo consumption.
- This best matches the accepted direction toward **content-as-code**, preserves the existing **module-centric UX**, and avoids keeping long-term deployment truth split between repo code and product-managed blueprint records.
- The decision only works if Forge adopts a **clear repository onboarding contract**: supported module repo shape, supported blueprint repo shape, strong validation, and admin-controlled promotion.
- Forge should support **two onboarding paths**:
  1. **strict native authoring** for new repos, and
  2. a **limited transformation/import helper** for existing non-conforming repos.
  Transformation should normalize metadata, not invent missing deployment semantics.
- **What remains in-product:** execution engines/runners, governance, catalog/import logic, approvals, version pinning/promotion, secrets/auth integration, validation, state, orchestration, and product-native operational workflows.
- **What should move external:** deployable module content and reusable blueprint definitions that describe solution composition.
- Blueprints should become **externally sourced, versioned content artifacts imported into Forge as immutable cataloged releases**, with explicit module-version references and admin promotion gates.
- Migration should be phased: preserve current built-in content during transition, externalize canonical built-ins first, then deprecate in-repo deployment content once imported equivalents are stable.

---

## Final Recommendation

**Choose Option B: External modules and external blueprints.**

Adopt Option B with the following precise interpretation:

- External repos are the **authoritative source** for deployment content.
- Forge remains the **governed control plane** that:
  - discovers/imports approved content,
  - validates manifests,
  - pins versions,
  - exposes catalog entries,
  - applies governance and approval rules,
  - orchestrates deployments and lifecycle actions.
- Blueprints are **not executed directly from Git at runtime**. They are **imported/synced under admin control** into Forge as governed catalog artifacts.
- Forge must provide a **strict authoring contract** for module and blueprint repos, plus a **bounded transformation/import path** for existing customer repos that do not yet match the contract.

This is the best long-term boundary because it removes ambiguity about where deployment truth lives while preserving Forge as the source of truth for **execution governance**, not content authoring.

---

## Decision Rationale by Option

### Option A — External modules, product-managed blueprints

**Summary:** workable, simplest near term, but ultimately leaves blueprint truth split between external module repos and internal product metadata.

#### Product clarity
- Strong in the short term.
- Users still understand blueprints as “things Forge owns.”
- But long term it creates a conceptual mismatch: modules are source-managed, while the compositions built from them are not.

#### Governance / trust boundary
- Strong central governance.
- Simpler approval model because only modules cross the repo boundary.
- But governance becomes asymmetric: module updates are source-driven, blueprint changes are UI/DB-driven.

#### Versioning and drift control
- Weaker than Option B.
- Blueprints referencing external modules need pinning anyway, but blueprint definitions themselves lack a natural source-managed history unless Forge invents its own export/versioning discipline.
- Higher risk of “module changed in repo, blueprint lagged in DB.”

#### Operational simplicity
- Simplest to operate initially.
- Fewer import/sync concepts.
- But debugging content drift over time is harder because blueprint evolution is detached from source control.

#### Migration impact
- Lowest near-term migration cost.
- Allows current built-in blueprint behavior to persist.
- But likely creates a second migration later if blueprints eventually need to externalize.

#### API / catalog implications
- Minimal new API surface.
- Mostly module catalog enhancements plus stronger version pinning on blueprint-module references.
- Avoids blueprint source/import APIs in the short term.

#### Repository onboarding / authoring model
- Simpler because only module repos need a formal contract.
- But misses the stakeholder’s current leaning and does not fully answer how reusable solution definitions travel between environments/teams.

**Verdict:** good transitional model, but not the best durable architecture.

### Option B — External modules and external blueprints

**Summary:** strongest long-term architecture if paired with strict governance and onboarding rules.

#### Product clarity
- Clear once framed properly:
  - **Modules** are deployable building blocks.
  - **Blueprints** are reusable composed solutions.
  - Both are content artifacts managed externally and governed by Forge.
- Preserves module-centric UX because the UI concept remains “module” and “blueprint,” not “repo.”

#### Governance / trust boundary
- Strong if import/sync remains:
  - admin-managed,
  - allowlisted,
  - versioned,
  - validated,
  - promoted into Forge.
- Avoids direct runtime trust in arbitrary Git state.
- Gives one consistent governance model for all deployable content.

#### Versioning and drift control
- Best of all options.
- Blueprints can explicitly pin:
  - module identifiers,
  - module versions/releases,
  - blueprint schema version,
  - compatibility expectations.
- Drift becomes visible and governable through import/sync and promotion, not hidden in mutable DB objects.

#### Operational simplicity
- More complex than A initially.
- But operationally cleaner long term because all deployable content follows the same lifecycle:
  source → validate → import → approve → catalog → deploy.
- Better fit for one-click reliability because approved releases can be frozen and validated before use.

#### Migration impact
- Higher initial cost.
- Requires blueprint repo contract, import model, and release semantics.
- But avoids preserving a permanent split-brain content model.

#### API / catalog implications
- Requires additive blueprint catalog/source concepts:
  - blueprint source/import metadata,
  - versioned blueprint catalog entries,
  - module-version reference semantics,
  - validation/reporting surfaces.
- This is meaningful new work but architecturally coherent.

#### Repository onboarding / authoring model
- Requires explicit repo shapes for both modules and blueprints.
- Forces a real answer for “how does a supplied repo become Forge-usable?”
- Best fit for enterprise teams who want reviewable, versioned, portable deployment content.

**Verdict:** **recommended**.

### Option C — Hybrid

**Summary:** best migration flexibility, but worst long-term clarity.

#### Product clarity
- Weakest.
- “Some blueprints are native, some are imported” is understandable to admins but not elegant as a durable default.
- Increases teaching and support burden.

#### Governance / trust boundary
- Mixed.
- Two different blueprint ownership/governance paths create policy complexity and precedence questions.

#### Versioning and drift control
- Better than A, worse than B.
- Drift handling depends on blueprint origin.
- Harder to communicate pinning and promotion rules consistently.

#### Operational simplicity
- Worst overall.
- Every operational surface needs origin-aware logic.
- Debugging becomes “is this a native blueprint issue or imported blueprint issue?”

#### Migration impact
- Best incremental migration story.
- Lets Forge keep current built-ins while introducing imported blueprints.
- But if retained permanently, it becomes a product tax.

#### API / catalog implications
- Most complex surface area.
- Requires origin-aware blueprint APIs, sync semantics, precedence rules, and edit restrictions.

#### Repository onboarding / authoring model
- Flexible, but too many modes.
- Can encourage teams to delay standardization indefinitely.

**Verdict:** acceptable only as a **temporary migration phase**, not as the target architecture.

## What Must Remain In-Product vs What Should Be Externalized

### Must remain in-product

Forge should continue to own these capabilities as product behavior, not content:

- **Execution engine framework** and governed runner implementations
  - Clarified 2026-07-18: for the `container` engine the runner **image** is
    author-supplied content (built by the tool's own repo, digest-pinned); what
    stays in-product is the engine/substrate — admission validation, sandboxing,
    workspace/secrets, outputs readback. See DEPLOY-ENGINE-EXT-003 Amendment A.
- **Allowlist/source governance** and source approval controls
- **Import/sync orchestration** for modules and blueprints
- **Catalog persistence** for approved imported artifacts
- **Validation and policy enforcement**
- **Version pinning and promotion workflows**
- **Secrets/auth integration** and runtime credential handling
- **Deployment state**, history, logs, outputs, and reconciliation
- **Project/stack orchestration semantics**
- **Capability truth and action gating**
- **Operational safety rails** for apply/destroy/retry/recovery
- **Built-in product workflows** that are platform features rather than deployable content
  - examples: scanning, health, inventory, diagnostics, platform-awareness, support guidance

### Should be externally sourced

Forge should externalize these as governed content artifacts:

- **Module deployment content**
  - manifest-backed module/deployment-pack definitions
  - engine-specific execution payloads and metadata
- **Blueprint definitions**
  - composed solution templates
  - module references, ordering, defaults, input mappings, dependency relationships
- **Reusable deployment examples/reference packs**
- **Vendor/customer solution bundles** that are deployment content, not product behavior

## Blueprint Ownership Model

### Recommended model

Blueprints should be **externally authored, versioned artifacts** that Forge imports into a **Blueprint Catalog** under admin control.

### Ownership rules

- **Source of truth:** external approved repo
- **Governed runtime source:** imported/cataloged release inside Forge
- **Editing in Forge:** no freeform structural editing of imported blueprints
  - allow only bounded local metadata overlays if needed later (for example visibility labels), not structural mutation
- **Promotion model:** blueprint versions progress through admin approval/import states before they become deployable
- **Reference model:** blueprint versions reference explicit module identifiers and pinned compatible module versions or version ranges
- **Immutability:** imported blueprint release artifacts should be treated as immutable after cataloging

### Why

This keeps blueprint history, review, portability, and environment promotion aligned with modules while avoiding runtime dependence on mutable repo HEAD.

## Versioning and Drift Control Policy

### Blueprint-to-module reference policy

Blueprints must not reference “latest” implicitly.

They should reference:

- stable module identity
- explicit module version or approved bounded version rule
- declared compatibility schema version

### Recommended drift controls

- **Import-time validation** that all referenced modules exist or are importable
- **Compatibility validation** between blueprint schema and referenced module metadata
- **Pinned deployable releases** in Forge catalog
- **Explicit update workflow** when a newer module or blueprint release is available
- **Visibility of stale references** in admin/catalog UI and APIs

### Promotion principle

A blueprint becomes deployable only after:

1. source approved,
2. blueprint manifest valid,
3. referenced module versions resolvable,
4. policy checks pass,
5. import completed,
6. release promoted/approved.

## Repository Onboarding Recommendation

### 1) Supported repo shape for module repos

#### Recommended native module repo contract

Support either:

- **single-pack repo**, or
- **catalog repo containing multiple packs**

Each deployable module must have a canonical manifest and path contract, centered on the already accepted deployment-pack model.

#### Minimum expectations

Each module pack should contain:

- manifest file for pack metadata
- explicit engine type from supported closed set
- declared entrypoints/artifacts required by that engine
- declared inputs/outputs/dependencies/lifecycle capability metadata
- stable pack identity and version metadata
- repo-relative path truth

#### Recommended structure

Example shape:

```text
repo-root/
  modules/
    <module-slug>/
      bnkforge.pack.json
      <engine-specific files>
      README.md
```

Allow a single-pack root shape as a convenience:

```text
repo-root/
  bnkforge.pack.json
  <engine-specific files>
  README.md
```

#### Module repo rules

- pack identity must be stable across syncs
- manifest path must match discovered repo-relative path
- engine-specific files must exist
- lifecycle declarations must be truthful
- secrets/auth requirements must be declared, not implied
- unsupported engines or arbitrary runner expansions must fail validation

### 2) Supported repo shape for blueprint repos

#### Recommended native blueprint repo contract

Blueprint repos should support:

- **single-blueprint repo**, or
- **catalog repo with multiple blueprint bundles**

Each blueprint bundle should include a manifest describing:

- blueprint identity
- version
- display metadata
- supported platform constraints if applicable
- ordered module references
- per-module configuration defaults
- input mapping/required parameters
- dependency edges between modules
- optional outputs surfaced to Forge
- compatibility/schema version metadata

#### Recommended structure

```text
repo-root/
  blueprints/
    <blueprint-slug>/
      forge-blueprint.json
      README.md
      docs/
      examples/
```

Single-blueprint root shape may also be supported:

```text
repo-root/
  forge-blueprint.json
  README.md
```

#### Blueprint repo rules

- blueprint manifest must fully describe the composition
- module references must target catalog-resolvable module identities
- version references must be explicit
- dependency graph must be acyclic
- inputs/defaults must be schema-valid
- unsupported or unresolved module references must block import
- runtime behavior must not depend on undocumented repo conventions

### 3) Should transformation/import tooling be recommended?

**Yes — but bounded.**

#### Recommendation

Forge should support a **limited transformation/import helper** for onboarding existing repos that do not match the native contract.

#### Scope of transformation tooling

The tool/helper may:

- inspect repo layout
- detect likely module packs or blueprint candidates
- propose normalized metadata
- generate starter manifests/templates
- highlight missing required fields
- produce a dry-run validation report

The tool/helper should **not**:

- infer complex lifecycle semantics without author confirmation
- silently invent dependency graphs
- auto-approve invalid repo content
- execute arbitrary repo logic to “discover” intent

#### Position

Transformation is a migration convenience, not a substitute for a clear authoring contract.

### 4) Required authoring guidance/templates/docs

Forge should provide all of the following:

- **module repo authoring guide**
  - supported repo shapes
  - required manifest fields
  - engine-specific examples
  - lifecycle declaration rules
  - versioning rules
- **blueprint repo authoring guide**
  - required manifest schema
  - module reference rules
  - dependency graph rules
  - input/default/output conventions
  - release/promotion guidance
- **starter templates**
  - single module repo
  - multi-module catalog repo
  - single blueprint repo
  - multi-blueprint catalog repo
- **migration cookbook**
  - how to adapt a legacy module repo
  - how to convert an internal/built-in blueprint into external shape
  - how to validate before import
- **validation reference**
  - all error classes
  - field requirements
  - common non-conformance examples
  - remediation guidance

### 5) Validation feedback Forge should return for non-conforming repos

Validation feedback must be **structured, specific, and actionable**.

Forge should return errors grouped by:

- **source-level failures**
  - repo unreachable
  - source not allowlisted
  - auth/access failure
  - unsupported repo layout
- **manifest-level failures**
  - missing manifest
  - invalid schema version
  - duplicate identities
  - invalid version format
- **module-pack failures**
  - unsupported engine
  - missing required engine files
  - invalid lifecycle declarations
  - unresolved secret/input metadata
- **blueprint failures**
  - missing blueprint manifest
  - unresolved module references
  - cyclic dependencies
  - invalid defaults/mappings
  - incompatible version references
- **policy failures**
  - disallowed source
  - disallowed engine/runner
  - unapproved release state

Each error should include:

- artifact path
- severity
- machine-readable error code
- human-readable explanation
- remediation suggestion
- whether import can continue partially or must fail

## Recommended Product Boundary

### Product-managed core
Forge remains the product for:

- governance
- validation
- orchestration
- cataloging
- promotion
- execution
- state and observability
- operational safety and supportability

### Externally managed content
Approved source repos should own:

- module implementation content
- blueprint composition content
- version history of deployment artifacts
- reusable solution packaging

This keeps the platform/content boundary clean:

- **Forge is the governed deployment system**
- **External repos are the content source**

## Migration Guidance for Remaining In-Repo Deployment Content

### Migration principle

Any deployable content that is currently in-repo should move out **unless it is actually product behavior**.

### Keep in repo
Keep these in the Forge product repo:

- engine implementations
- import/catalog logic
- policy/validation code
- product-native operational workflows
- built-in support/diagnostic features
- non-deployable UX/config metadata needed for product operation

### Migrate out
Move these to external source-managed content over time:

- built-in deployable modules
- built-in blueprint/template definitions that exist primarily to deploy solution content
- reference/sample deployment bundles that are really reusable content, not product logic

### Suggested migration phases

#### Phase 1 — canonicalize current built-ins
- Define native module and blueprint repo contracts.
- Export or re-home a small set of canonical built-in content into external-form repos.
- Keep existing in-product copies active during validation.

#### Phase 2 — dual-read transition
- Allow Forge to import externalized versions while preserving current built-ins as fallback.
- Compare behavior and stabilize metadata/versioning contracts.

#### Phase 3 — external source becomes canonical
- Mark imported external artifacts as preferred/default.
- Freeze structural edits to equivalent in-product built-ins.
- Add deprecation labels to legacy internal content.

#### Phase 4 — retire in-repo deployable content
- Remove or archive built-in deployable content once external equivalents are proven stable and documented.

## API / Catalog Implications

Choosing Option B implies the following additive architectural needs:

### Module side
Mostly aligned with current accepted direction; continue using:

- source-backed module import
- engine/capability metadata
- governed catalog persistence

### Blueprint side
New required concepts:

- **BlueprintSource**
  - approved external source metadata
- **BlueprintCatalog / BlueprintRelease**
  - imported immutable blueprint versions
- **Blueprint validation status**
  - importability, policy pass/fail, dependency resolution state
- **Reference semantics**
  - module identity + version pinning/range policy
- **Promotion state**
  - discovered / imported / approved / deprecated / inactive

### API expectations
Likely later slices need:

- blueprint source registration/sync APIs
- blueprint validation/import result APIs
- blueprint release/list/detail APIs
- blueprint-to-module resolution visibility
- stale/incompatible reference reporting

## Explicit Rejected Alternatives

### Rejected: Option A as final target
**Why rejected:**
It is too likely to preserve a permanent split between source-managed modules and product-managed blueprint logic. That weakens content portability, reviewability, and long-term drift control.

### Rejected: Option C as final target
**Why rejected:**
It is useful as a migration state, but not as the durable architecture. Permanent hybrid ownership makes blueprint behavior harder to explain, govern, and support.

### Rejected: direct runtime execution from arbitrary Git state
**Why rejected:**
Violates the accepted allowlisted/admin-managed governance direction and weakens operational safety and reproducibility.

### Rejected: transformation-only onboarding with no strict native contract
**Why rejected:**
A transformation layer without a canonical supported shape becomes an endless compatibility surface and weakens product clarity.

### Rejected: strict native contract only, with no migration helper
**Why rejected:**
Too unfriendly to real customer repos and likely to slow adoption. Some bounded transformation/import assistance is warranted.

## Implementation Follow-Ons

### 1. ARCH-EXT-002a — Blueprint artifact contract
Define the canonical blueprint manifest/schema, identity/version rules, module reference format, dependency graph rules, and compatibility metadata.

### 2. ARCH-EXT-002b — Blueprint catalog and source model
Plan persistent source/catalog/release entities, approval states, sync semantics, and immutability rules for imported blueprint releases.

### 3. ARCH-EXT-002c — Repo onboarding and validation UX
Specify strict repo shapes, validation pipeline, structured error vocabulary, dry-run import behavior, and admin feedback surfaces for module/blueprint sources.

### 4. ARCH-EXT-002d — Transformation/import helper scope
Define the bounded migration helper: what it may detect/generate, what it must never infer automatically, and the operator workflow for converting non-conforming repos.

### 5. ARCH-EXT-002e — Built-in content migration plan
Inventory current in-repo deployable modules/blueprints, classify what is content vs product behavior, and sequence externalization/deprecation.

### 6. REPO-AUTH-001 — Repo authentication and approval model
Design source authentication, credential storage, provider patterns, rotation, and approval flow against the now-set architecture.

### 7. ARCH-EXT-002f — Version pinning and promotion semantics
Define how blueprint releases pin or constrain module versions, how compatibility checks run, and how promoted releases remain reproducible.

## Assumptions / Open Questions

- Assumption: blueprint artifacts will be imported as immutable releases rather than edited freely in-product.
- Assumption: module version pinning/range semantics can be introduced without reopening prior DEPLOY-ENGINE-EXT decisions.
- Open question: whether blueprint manifests should allow bounded version ranges or require exact module release pins only.
- Open question: whether a single repo may contain both modules and blueprints as a supported pattern, or whether Forge should strongly prefer separate source types even if shared repos are technically allowed.
- Open question: what minimum compatibility metadata is required on blueprint manifests for platform/profile targeting versus leaving that entirely to referenced modules.
- Open question: whether limited local blueprint overlays in Forge are needed for tenancy/visibility metadata, or whether all blueprint metadata should remain source-authored.
