# D-026 — Fleet operator model & information architecture (manage fleets, not clusters)

- **Status:** Accepted
- **Date:** 2026-06-05
- **Builds on:** D-025 (explicit Fleet entity) and D-022 (fleet label layer, gated executor, policy/compliance, lifecycle). This ADR governs the *operator experience* over that machinery — the IA and the workflow, not new primitives.
- **Source:** Operator review of D-022 P5 on localhost: *"I like fleet inventory but I can't do anything with it — display only. I can't intuitively understand what to do with bulk-ops or compliance, or how it ties together. Imagine 200–300 clusters across dozens of fleets — what do I need to manage, and how? Cluster Health should be inside a fleet; policy config belongs in fleet mgmt; migration belongs on the K8s page."*
- **Research basis:** Primary-source teardown of GKE Fleets, Azure Kubernetes Fleet Manager, Red Hat ACM/OCM, Karmada, Rancher Fleet, VMware Tanzu Mission Control (2026-06-05). All converge on the model below.

## Context

D-022/D-025 delivered the fleet *machinery* (label-selected Fleet entity, derived members + lifecycle, gated wave executor, inform/enforce policy + compliance) but presented it as **flat, disconnected top-level tabs** (Inventory, Bulk-ops, Compliance, Cluster Health, Migration). On first real use this failed three ways: the inventory was **display-only** (no verbs), bulk-ops/compliance had **no obvious purpose or connection** to the inventory, and **"Cluster Health"** sat as a sibling of "Fleets" — two meanings of fleet, one tab apart. The unanswered question — *"at 200–300 clusters, what do I manage and how?"* — is the real design driver.

### What the field does (convergent across six products)

- **Group is first-class; membership is by label.** Tanzu *cluster group*, ACM *ManagedClusterSet*, GKE *fleet/team-scope*, Azure *fleet + member labels*, Rancher Fleet *label-selector targets*. Label a cluster → it auto-joins the matching group/rollout. (Forge already has this: Fleet = saved selector, D-025.)
- **Policy attaches to the group; members inherit.** Tanzu is canonical: attach a policy to a cluster group and **every member — including clusters added later — inherits it**. This is *why* policy lives in fleet management.
- **Health is a conformance/coverage % that rolls up, then drills down.** GKE leads with *"X% policy-covered, Y in sync, Z security concerns"* — not per-cluster up/down. Rancher Fleet propagates a **worst-state** (`Ready→…→ErrApplied`) and an **"N/M ready"** ratio from resource→cluster→group. ACM rolls compliance up per-Policy and per-PolicySet. **Per-cluster status does not scale past ~50 clusters; conformance % does.**
- **Inventory is actionable.** Rancher = selection-driven bulk row actions; Tanzu = act-on-the-group fan-out. Selecting a group/clusters **exposes verbs** (apply policy, push config, upgrade ring).
- **Bulk operations split Strategy from Run.** Azure Fleet Manager: a reusable **update strategy** (stages → groups, no version baked in) vs a live **update run** — a bottom-up state tree (run→stage→group→cluster) with **approval gates, TimedWait soak, a `maxConcurrency` % "safety↔speed" dial, `Consistent` artifact pinning, and actionable "Pending reasons"** ("stuck because maintenance window closed → next opens 02:00"). Re-run an older snapshot = rollback. This is the most intuitive *and* safe staged-rollout UX in the market.
- **Observe before enforce.** Tanzu `dryrun → Insights → deny`; ACM `inform → enforce`. The view that *shows* a violation is one toggle from the action that *fixes* it, fleet-wide.

## Decision

Adopt the **fleet operator model**: you manage **fleets and their conformance**, not individual clusters. Clusters are drill-down. Reorganize the UI to match, reusing all D-022/D-025 backend.

1. **Fleet page = list → per-fleet detail drill-down.** The Fleets list shows, per row, a **conformance rollup** (compliant/total, drift count, worst-state badge). Clicking a fleet opens a **detail view scoped to that fleet** with four facets:
   - **Health** — the fleet's member clusters + a conformance/health rollup (this *is* the former "Cluster Health", scoped). A fleet whose selector matches everything is the global health view.
   - **Members** — the inventory for this fleet, **actionable**: select members → verbs (Apply policy, Run operation, Relabel).
   - **Policies** — the compliance baseline **attached to this fleet**; members inherit; per-member drift; a single **inform↔enforce** toggle.
   - **Operations** — staged bulk-ops on this fleet, as **runs** you watch (waves, gates, cancel).
   The flat top-level Inventory/Bulk-ops/Compliance/Cluster-Health tabs are retired in favour of this "one fleet, four facets" hierarchy.

2. **Health = conformance rollup with worst-state.** Add a per-fleet rollup: members compliant/total, drift count, unreachable count, and a worst-state badge propagated member→fleet (Rancher-style). Lead with the percentage; drill member → resource.

3. **Operations = Strategy + Run.** Introduce a reusable **operation strategy** (ordered waves by fault-domain/label, per-wave `maxConcurrency` %, between-wave gate: Approval or TimedWait) distinct from a live **run** (the existing `FleetBulkRun`, surfaced as a bottom-up wave→member state tree with actionable per-member reasons + cancel + re-run). The existing gated wave executor already provides the safety substrate; this adds the reusable-strategy authoring and the run-tree UX on top.

4. **Inform/enforce stays the compliance verb**, surfaced in the fleet's Policies facet with per-member drift and one-toggle enforce (drives the gated executor — never an ungated mutation).

5. **Migration moves to the K8s page.** Migration is a *per-cluster scan* concern, not fleet-wide. It becomes a K8s-page tab (Dashboard / Advanced / Migration), fed by the cluster scan; the duplicate cards are removed from the scan-results page (single source).

### What is reused (unchanged)

Fleet entity (D-025), `FleetMember` + derived `lifecycle_state`, the gated wave executor + `SAFE_ACTIONS`, immutable `FleetDecision`/`PolicyEvaluation`, inform/enforce with the `os-compliance-seed` inform-only invariant, `require_fleet_mutation` RBAC, Project-as-Scope, and the migration components themselves. This ADR restructures presentation + adds the conformance rollup and the strategy/run framing — it does not replace the engine.

## Consequences

**Positive** — Matches how operators actually run fleets at scale and how every major product is built; turns four disconnected tabs into one coherent "fleet → four facets" object; makes inventory actionable, compliance purposeful, and bulk-ops legible+safe (staged waves, gates, % blast-radius dial, why-stuck reasons); conformance-% health scales past hundreds of clusters where per-cluster status doesn't.

**Negative / costs** — A real IA restructure of the Fleet page (not a patch); introduces a new operation-strategy concept (kept thin, layered on the existing executor); per-fleet rollup adds an aggregation endpoint. Mitigated by reusing the entire D-022/D-025 backend and shipping in four independently-deployable slices: (P6-1) Migration→K8s + de-dupe; (P6-2) Fleet detail + per-fleet Health rollup; (P6-3) actionable Members + Policies-on-fleet; (P6-4) staged Operations (Strategy/Run).

**Delivery:** D-022 P6 on `customer-build` → localhost, four slices, each deployed for review.
