# D-022 — Fleet Management (heterogeneous estate: K8s + hardware + OS)

- **Status:** Accepted (umbrella / north-star); **P1 committed**, P2–P4 accepted-in-shape.
- **Date:** 2026-06-03
- **Source:** planning session 2026-06-03 — code-grounding of all target types + external research on cross-domain fleet-management (GKE Fleet, Red Hat ACM/OCM, Rancher Fleet, Canonical MAAS, Metal3, AWS SSM, osquery/Fleet, Argo Rollouts, Redfish).
- **Governing principle:** D-019 (dynamic-by-default — inventory is *discovered and reconciled*, never a hand-maintained list)
- **Reuses pattern:** D-018 (discovery cached at scan time)
- **Relates to:** the future TMOS/BIG-IP engine (a fleet member type), `proxy`/`bnk` actuators

## Context

Forge has strong **per-target actuators** but **no fleet layer** over them:

- **Clusters** (`models/kubernetes.py`) — a registry + a **clusters-only** read-only fleet view (`GET /api/operators/fleet-health`, `frontend-v2/src/pages/Fleet.tsx`, `useFleet.ts`).
- **Bare-metal / DPU** (`models/bare_metal.py`, `models/dpu.py`) — a *rich* per-host model: BMC/Redfish/IPMI access, a hardware-facts cache (`os_info` / `dpu_info` / `k8s_info`, NIC/firmware/rshim), and a real per-host deployment **state machine** (`BareMetalDeployment` / `DeploymentStep`); `Dpu` has its own flash lifecycle. **But** it is per-host/per-project, has **no bulk operations**, and **is absent from the fleet view**.
- **OS-level** — effectively **nothing**: SSH runs arbitrary commands and hardware facts are probed, but there is no patch / config / compliance / drift / OS inventory.

Two structural gaps make "fleet management" impossible today:

1. **No common abstraction.** `KubernetesCluster` and `BareMetalHost` (and `Dpu`, and the future BIG-IP device) are fully separate tables with separate services, routes, and UI. There is no shared way to *list*, *group*, or *act on* "all my managed things."
2. **No grouping primitive beyond `project_id`.** No labels, tags, regions, environments-on-members, or cross-target groups. (`SSHCredential` is the one element already shared across target types.)

### What the field converged on

Every reference system independently arrived at the **same 7-capability model** — only the verbs and lifecycle states differ per domain:

**Inventory · Grouping/Targeting · Bulk-Ops/Rollout · Policy & Compliance/Drift · Lifecycle states · Health/Observability · Multi-tenancy/RBAC**

Load-bearing findings:

- **Reconciled, discovery-driven inventory is the foundation** (MAAS / Metal3 / osquery). It is forge's single biggest gap, and it *is* D-019 applied to fleet membership: members self-report discovered facts on a loop; every downstream capability keys off those facts. A hand-maintained inventory is wrong within days.
- **Labels are the universal targeting primitive** — the one mechanism identical across k8s / HW / OS. **Selectors resolve to a stored, auditable member set before acting** (OCM `Placement → PlacementDecision`), so an operator sees exactly what an op will hit. Mitigates blast radius on a mixed estate.
- **Keep three groupings separate:** a thin **`Fleet`/Scope** object = tenancy + RBAC boundary (exclusive — one per member); **fault-domains** = rollout-wave ordering (single-membership); **labels** = everything else (overlapping, many-to-many). Conflating tenancy with targeting is the #1 modeling mistake (you can no longer express "deploy to prod across two teams' members").
- **Every bulk op is a gated progressive rollout** — waves ordered by fault-domain, health-gated promotion, per-wave concurrency cap, instant rollback. Never "apply to all" (vanilla Deployments give no blast-radius control). Far more important here because forge actions include firmware flash and BIG-IP config, not just pods.
- **Policy is inform-then-enforce** with a fleet-wide compliance rollup (ACM Governance, SSM Patch/Compliance). Auto-enforce without an inform+approve step is dangerous on an appliance/HW estate; detect-without-remediate is useless. Model both modes.

## Decision

Build forge fleet management as **a thin label + Fleet layer over the existing per-target tables — no table merge** — defined once and specialized per actuator. This ADR is the umbrella; it commits to a **P1 inventory foundation** and accepts P2–P4 in shape.

### The data model (the north star)

- **Member** = any managed target (cluster, bare-metal host, DPU, future BIG-IP). Members are **not merged into one table**; instead a **polymorphic fleet-membership + label layer** references the existing rows (`{member_type, member_id}`). Per-target actuators and tables are preserved.
- **Labels** — every member carries an arbitrary key/value label set, split into:
  - **discovered** (vendor, model, k8s version, OS/kernel, region, has-dpu, firmware) — reconciled from the live source per D-019, never hand-typed;
  - **assigned** (env, team, criticality) — operator-set, governed by a controlled vocabulary to avoid label sprawl.
- **Target** = `{ labelSelector }` that **resolves to a stored, reviewable member set** (the OCM Placement/PlacementDecision split) — the unit every bulk op and policy binds to.
- **Scope / tenancy boundary = the existing `Project`** — **do not introduce a new `Fleet` container.** The research's "Fleet/Scope" is defined as the *exclusive, one-per-member tenancy + RBAC boundary* (GKE "one fleet per member"; ACM `ManagedClusterSet`). Forge's `Project` already is exactly that: a member belongs to one Project via FK, and Project carries `team` + `visibility`. So `Scope = Project`; this ADR adds **no** new exclusive container. (P4 only layers RBAC/rollout-defaults onto Project; it does not create a parallel entity.) The day-to-day grouping mechanism is labels+selectors, **not** Project — because Projects are exclusive silos and cannot express cross-project sets ("all prod GPU hosts across three teams").
- **Fault-domain / Zone** = single-membership grouping used only for rollout-wave ordering.
- **Actuator interface** = a common contract (`inventory()`, `apply()`, `lifecycle transition`, `health()`) implemented per domain: k8s via API, hosts via SSH+Redfish, BIG-IP via its declarative API. This is where the engine abstraction (D-010 EngineRegistry) and per-target services already in the repo plug in.

### Phasing

| Phase | Scope | Status |
|---|---|---|
| **P1 — Inventory foundation** | Polymorphic label/membership layer over clusters+hosts+DPUs; **normalize the already-captured-but-siloed per-type facts** (`platform_capabilities` on clusters, `os_info`/`dpu_info` on hosts) into one **queryable label space** — discovered labels (incl. OS facts via SSH probe) + **assigned** labels w/ controlled vocabulary; one **unified fleet view** that finally includes hosts/DPUs (not clusters-only); filter & group by label, **across Projects**. | **Committed** |
| **P2 — Targeting + gated bulk ops** | Label `Target` → stored `Decision`; bulk operations executed as **progressive rollouts** (fault-domain-ordered waves, health gate, concurrency cap, rollback). | Accepted, not scheduled |
| **P3 — Policy & compliance / drift** | Declarative desired-state policy in **inform-then-enforce** modes; fleet-wide compliance rollup. **OS patch/compliance lands here** (OS facts already labels from P1). | Accepted, not scheduled |
| **P4 — Lifecycle + multi-tenancy** | Explicit per-domain **lifecycle state machines** (hosts: commission→inspect→provision→release; clusters/appliances analogues) surfaced fleet-wide; **RBAC + default-rollout-policy layered onto the existing `Project`** (the Scope boundary — no new container). | Accepted, not scheduled |

**OS-level management** is folded in, not a separate epic: OS facts become discovered labels in P1; OS patch/config/compliance/drift rides the P3 policy phase. No dedicated OS config-management engine is introduced by this ADR.

### Guardrails (the research's anti-patterns, as rules)

1. **No static inventory.** Membership facts are discovered/reconciled (D-019); a hand-maintained member list is forbidden.
2. **No ungated bulk op.** Every multi-member action resolves to a reviewable Decision and runs as gated waves with a concurrency cap — no "apply to all."
3. **Tenancy ≠ targeting.** The `Project`/Scope (who owns) stays separate from label selectors (what an op hits) stays separate from fault-domains (wave order). Never reuse `project_id` as the bulk-op selector — selectors are label-based and cross-Project.
4. **Label governance.** Discovered vs assigned labels are distinct; assigned keys come from a controlled vocabulary.
5. **Show the blast radius first.** Surface the resolved member set before any op executes.
6. **`Fleet` is not a security boundary** on its own — it pairs with real RBAC (GKE's explicit warning).

## Consequences

- **Incremental, not a rewrite.** The thin layer preserves every existing per-target table, service, and flow; clusters/hosts keep their actuators. P1 adds a membership+label layer and one view — no destructive migration.
- **Reuses `Project` as the Scope** — no new tenancy container; one fewer entity to build, and existing ownership/RBAC carries forward. The genuinely new substrate is the **overlapping label space** (Projects are exclusive; labels are many-to-many) and the **normalization of siloed per-type facts** into something queryable across target types.
- **D-019 made concrete at the fleet level.** Inventory is discovery-driven by construction; this ADR is a major consumer of the dynamic-by-default principle.
- **Foundation-first sequencing matches the research.** Inventory+labels is the substrate all other capabilities key off; shipping it first de-risks P2–P4 and immediately closes the "hosts aren't in the fleet view" gap.
- **Future BIG-IP slots in as a member type**, not a special case — the TMOS engine becomes one more actuator behind the common interface.
- **Cost / risk concentration is deferred.** The genuinely dangerous capability (bulk ops / firmware / BIG-IP config across a live estate) lives in P2+ behind gated rollouts, not P1.
- **Reviewer checklist gains a fleet rule:** any new "list/act on many members" path must go through the label/selector/Decision layer, not a bespoke per-target loop.

## References

- Internal grounding (this session): `models/kubernetes.py`, `models/bare_metal.py`, `models/dpu.py`, `models/ssh_credential.py`, `models/project.py` (Project/Environment), `routes/operators/fleet.py` (`/api/operators/fleet-health`, `/fleet/compare`), `frontend-v2/src/pages/Fleet.tsx`, `src/hooks/useFleet.ts`, `src/types/fleet.ts`
- Principle: D-019 (dynamic-by-default); discovery pattern: D-018; engine/actuator seam: D-010 (EngineRegistry)
- External research: [GKE fleet concepts](https://docs.cloud.google.com/kubernetes-engine/fleet-management/docs/fleet-concepts) · [GKE team management](https://docs.cloud.google.com/kubernetes-engine/fleet-management/docs/team-management) · [OCM Placement](https://open-cluster-management.io/docs/concepts/content-placement/placement/) · [Red Hat ACM Governance](https://docs.redhat.com/en/documentation/red_hat_advanced_cluster_management_for_kubernetes/2.11/html/governance/governance) · [Rancher Fleet targets](https://fleet.rancher.io/how-tos-for-users/gitrepo-targets) · [MAAS machine groups](https://canonical.com/maas/docs/about-machine-groups) · [MAAS machine lifecycle](https://canonical.com/maas/docs/about-the-machine-life-cycle) · [Metal3 BareMetalHost](https://github.com/metal3-io/baremetal-operator) · [AWS SSM Compliance/Patch](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-compliance.html) · [osquery/Fleet live queries](https://fleetdm.com/guides/get-current-telemetry-from-your-devices-with-live-queries) · [Argo Rollouts canary](https://argo-rollouts.readthedocs.io/en/stable/features/canary/) · [Redfish](https://en.wikipedia.org/wiki/Redfish_(specification)) · [Ansible inventory](https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html)
