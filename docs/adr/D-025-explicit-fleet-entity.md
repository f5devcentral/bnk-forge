# D-025 — Explicit Fleet entity supersedes the pure-label model (D-022 P5)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Supersedes (in part):** D-022 "Fleet management" — specifically its *pure-label / label-emergent* fleet model. The label layer, gated bulk-op executor, policy/compliance engine, and derived lifecycle from D-022 P1–P4 all stand; only the "a fleet is never a first-class object, just an emergent label query" stance is reversed.
- **Source:** Operator review of D-022 P1–P4 running on the `customer-build` localhost line. Direct feedback: *"there is no CRUD for fleet operations… I created a bulk-op but how do I CRUD it? I can't re-select it, update it, delete it. Overview displays clusters (which are not fleets, just grouped)."* The label-emergent model did not match the operator's mental model of "a fleet is a thing I name, scope work to, and manage."
- **Sibling principle:** like D-019 (dynamic state) and D-020 (token discipline), this is a *model* decision that governs the fleet surface — but unlike those it is a deliberate **reversal** of a prior ADR's stance, hence its own record.

## Context

D-022 shipped the hard backend of fleet management as a **label layer over existing inventory**: `FleetMember` rows reconciled from clusters/hosts/DPUs, a `FleetTarget` (saved label-selector) that resolves to an immutable `FleetDecision`, a gated wave executor for bulk-ops, a policy/compliance engine, and a derived per-member `lifecycle_state`. The guiding decision was **"a fleet = a collection of labels + pointers; reuse Project as the Scope; do not add a tenancy/fleet container."** Fleets were *emergent*: you expressed one by writing a selector, and membership fell out of the labels.

On first operator use of the running UI, three gaps surfaced that trace back to that stance:

1. **No fleet CRUD.** `FleetTarget` had create + read + resolve, but **no update and no delete**. There was no way to name, edit, or remove "a fleet" — because the model didn't treat a fleet as an object you manage, only as a query you run.
2. **Bulk-ops were unmanageable.** Runs could be *started* (`POST /bulk-ops`), read by id, and resumed — but not **listed, re-selected, or cancelled**. An operator who kicked off a run had no way back to it.
3. **Conceptual collision on "Fleet."** The Fleet page's "Overview" tab showed the legacy UX-012 cluster-health dashboard (`/api/operators/fleet-health`, querying `KubernetesCluster` directly) — a *different* meaning of "fleet" than the D-022 label model, sitting one tab away from it.

Policies had the same CRUD hole as targets (create/read, no update/delete). The label-emergent model was internally coherent but operationally incomplete: operators think in terms of named, durable fleets they CRUD and scope work to — not in terms of re-typing selectors.

## Decision

Adopt a **first-class, named Fleet entity** as the operator-facing primitive, **by evolving the existing `FleetTarget`** rather than adding a new table.

1. **Fleet = evolved `FleetTarget`.** A Fleet *is* a `FleetTarget` (it already carries `name`, `selector`, `project_id`, `created_by`, timestamps) plus three added columns: `description`, `pinned_member_ids` (optional always-included members layered over the selector result), and `deleted_at` (soft-delete). The entire downstream chain — `target_id → FleetDecision → FleetBulkRun / FleetPolicy`, the immutable-Decision snapshot, and the gated executor — is **reused verbatim**. The UI label is "Fleet"; the table/route stem stays `fleet_targets` / `/api/fleet/targets` to keep the change additive and reversible. **No new `fleets` table** (and none existed to collide with — the legacy Overview queries clusters directly, not any `fleets` table; the only collision was the *word*).
   - *Rejected:* a new `Fleet` table wrapping `FleetTarget`. It would force every `target_id` FK and every service signature to learn a second identity for zero behavioural gain.

2. **Full CRUD with soft-delete.** Add `PUT`/`DELETE` for both Fleets (targets) and Policies. **Delete is soft** (`deleted_at`), never hard: a `FleetDecision` is an immutable audit snapshot and `FleetBulkRun.decision_id` is `ON DELETE RESTRICT`, so a hard delete would either destroy the audit trail or error. Soft-delete hides the Fleet/Policy from active lists while preserving all historical runs and evaluations. Edits mutate only the entity row — **never** a historical immutable `FleetDecision`/`PolicyEvaluation` snapshot; a changed selector supersedes on next resolve.

3. **Scope work to a selected Fleet.** Selecting a Fleet scopes the Inventory / Bulk-ops / Compliance surfaces to it (its selector filters members; its `target_id` filters policies; it pre-selects the target for a bulk-op). Scoping reuses the existing resolve→Decision path — no new scoping primitive.

4. **Bulk-ops become observable and stoppable.** Add `GET /bulk-ops` (list, filterable by fleet/status) and `POST /bulk-ops/{id}/cancel`. **Cancel is cooperative, never a hard kill:** it flips run status to `cancelled`, and the wave executor re-reads that status at each wave boundary and stops cleanly, letting the in-flight wave's members finish. A Celery revoke (SIGKILL mid-wave) is explicitly rejected — it would orphan half-applied `set-labels`/`re-reconcile` member writes. This preserves the D-022 gated-executor safety contract.

5. **Disambiguate "Fleet" in the UI.** The legacy cluster-health "Overview" is **relabeled "Cluster Health"** (kept verbatim — it carries real telemetry not duplicated elsewhere), and a new **"Fleets" tab becomes the primary landing** for the entity CRUD. Migration detections (existing proxies + CIS) move off the busy scan page into a dedicated **"Migration" tab** fed per-cluster.

### What stays from D-022 (unchanged)

The label layer, `FleetMember` reconcile + derived `lifecycle_state`, the gated wave executor + `SAFE_ACTIONS` allowlist, the immutable `FleetDecision`/`PolicyEvaluation` snapshots, policy inform/enforce with the `os-compliance-seed` inform-only invariant, `require_fleet_mutation` fail-closed RBAC, and **Project as the Scope** (no new tenancy container) all remain. A Fleet is still selector-driven; it is now also *named, durable, and CRUD-able*.

## Consequences

**Positive**
- Matches the operator mental model: name a fleet, scope work to it, manage it, see and cancel its runs.
- Additive and low-risk: evolves one table + adds verbs; every safety invariant (immutable snapshots, gated executor, cooperative-only cancel, RBAC) is preserved and test-asserted.
- Audit trail is strengthened, not weakened: soft-delete keeps historical runs/evaluations queryable.

**Negative / costs**
- "Fleet" and `FleetTarget` are now the same thing under two names (UI vs table/route). Tolerated for this phase to keep the diff reversible; a later cosmetic `/api/fleet/fleets` route-alias is possible if the dual naming causes confusion.
- A `FleetPolicy.target_id` can reference a soft-deleted Fleet; surfaces must render "(deleted fleet)" rather than error. Handled.
- The pure-label purity of D-022 is gone — a Fleet is now a managed object, not only an emergent query. This is the intended reversal.

**Delivery:** D-022 P5, five slices on `customer-build`, deployed to localhost (migrations `v2_126` Fleet columns, `v2_127` policy soft-delete). Built before any staging PR per the local-integration workflow.
