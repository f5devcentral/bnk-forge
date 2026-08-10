# BNK Forge Product Vision

> **The management plane for F5 BNK — deploy, operate, monitor, and evolve BNK deployments with confidence.**

Last updated: 2026-03-31 | Product-state note: this document tracks direction, not release numbering.

---

## Current State

BNK Forge has matured from an internal deployment utility into a broad operations platform spanning deployment, diagnostics, topology, policy management, and multi-cluster lifecycle workflows.

### Current Capabilities (current branch)

| Capability | Status | Notes |
|---|---|---|
| Multi-engine execution model | ✅ Active | OpenTofu + direct K8s + operator connectivity path |
| Kubeconfig-first fleet architecture (D3) | ✅ Active | Fleet health from direct cluster queries, no required external operator install |
| Gateway Topology + Backends + Policy Builder + Configuration Builder | ✅ Active | Topology is now the central UX hub for BNK traffic and security workflows |
| Day-2 diagnostics | ✅ Active | Runbooks, QKView, licensing, TMM debug integrated in BNK workflows |
| Upgrade, drift, snapshots, config promotion | ✅ Active | End-to-end lifecycle controls in product |
| RBAC + ownership enforcement + audit trail | ✅ Active | Security baseline enforced on mutating APIs |
| Contract-aware CI + local guardrails | ✅ Active | OpenAPI/type freshness + phased CI gates + pre-push workflow |

---

## Remaining Strategic Gaps

| Gap | Why it matters | Suggested priority |
|---|---|---|
| Architecture maintainability (large file concentration) | Slows feature velocity and increases regression risk | High |
| API contract consistency in untyped response surfaces | Increases frontend/backend drift risk | High |
| Explicit bounded-context modularization | Needed for long-term scale and onboarding | High |
| End-to-end operator path validation in current architecture | Keeps secondary connectivity model healthy | Medium |
| Gateway Topology Phase 3 completion (TOPO-004) | Final mile for topology-centric UX | Medium |

---

## Vision and Direction

### Core Product Thesis

Day-1 deployment remains important, but BNK Forge’s long-term value is Day-2/Day-N operations:
- understanding live topology,
- safely changing routing/policy,
- diagnosing incidents quickly,
- and promoting known-good config across clusters.

### Product Direction

1. **Topology-first operations UX**
   - Topology as the control surface (inspect → edit → validate → apply)
2. **Contract-first platform behavior**
   - API and UI evolve together with schema guarantees
3. **Modular architecture with clear domains**
   - Execution, K8s ops, BNK insights, diagnostics, config lifecycle as explicit bounded contexts
4. **Operational trust at scale**
   - Better observability, predictable rollbacks, and safer multi-cluster promotion patterns

---

## Horizon Plan

### Horizon 1 (Current): Consolidate Topology-Centric Workflows
- Finalize Gateway Topology Phase 3 scope (TOPO-004)
- ~~Complete D3 cleanup of residual operator-token install surface~~ **DONE (2026-03-10)**
- Keep diagnostics + config builders tightly integrated with topology context

### Horizon 2 (Near-Term): Architecture Hardening
- Decompose largest backend/frontend files
- Standardize API response contracts where still implicit
- Publish and enforce bounded-context package boundaries

### Horizon 3 (Product Maturity)
- API versioning and external SDK posture
- Enterprise auth integrations (OIDC/LDAP) as demand requires
- Optional packaging/distribution refinements (CLI, deployment mode options)

---

## Product Principles

1. **BNK Forge runs independently of managed clusters**
2. **Multiple connectivity paths, one coherent UX**
3. **Intent-first UX with full expert override**
4. **Day-2 operations are the product core**
5. **Errors should be actionable, not just descriptive**
6. **Security and auditability are default, not optional**

---

## Success Indicators (Updated)

- Faster implementation cycle time for net-new BNK workflows
- Fewer frontend/backend contract mismatches
- Reduced “mega-file” concentration in critical domains
- Stable, trusted multi-cluster operation and promotion flows
- Continued high CI reliability with local-first validation discipline

---

## Related Documents

- [UX Roadmap](UX_ROADMAP.md)
- [Engineering Improvements](ENGINEERING_IMPROVEMENTS.md)
- [Architecture index](architecture/README.md)
- Operator connectivity path is retained as secondary/legacy-supported (see D3 in [Architecture Decisions](../.agent/DECISIONS.md))
- [Architecture Decisions](../.agent/DECISIONS.md)
