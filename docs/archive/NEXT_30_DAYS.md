# BNK Forge v2 — 30-Day Backlog and Roadmap

> Last updated: 2026-03-09
> Current state: v2.10.74, kubeconfig-first fleet architecture (D3), Gateway Topology sprint active

---

## Program Status

### Recently Completed

- Gateway Topology Phase 1: all route types + click-to-detail
- TOPO-002: Backends collection view (services cross-referenced with route backends)
- TOPO-003: Policy Builder (visual security/network policy creation)
- TOPO-003b: Configuration Builder (wizard for Gateway + routes + policies)
- Fleet dashboard (kubeconfig-first) and diagnostics expansion (QKView, licensing, TMM debug)
- CI hardening + local check workflow (`make quick-check`, `make pre-push`)

### In Progress

- Architecture investigation + roadmap refresh (what to keep, what to redesign, phased migration strategy)

### Upcoming Priorities

1. **Gateway Topology Phase 3 (TOPO-004) — scope definition and execution**
   - Candidate scope: richer drag-and-drop attachment UX, undo/redo history, topology-level apply previews
2. ~~**D3 cleanup completion**~~ — **DONE (2026-03-10)**: Removed token endpoints, ConnectClusterDialog,
   OperatorRequiredBanner, dead Operators/Helm pages, stale useOperators refs. Net -3,200+ lines.
3. **Architecture hardening track**
   - Decompose largest backend/frontend files (bnk_data_service.py, cluster_scanner.py, etc.)
   - Standardize API response contracts in remaining untyped surfaces
   - Document bounded contexts and enforce module boundaries

---

## Remaining Backlog (Near-Term)

| # | Item | Priority | Notes |
|---|------|----------|-------|
| 1 | TOPO-004 spec + implementation | High | Complete Gateway Topology Phase 3 scope definition and build |
| 2 | D3 cleanup (operator token flow removal) | Medium | Align UI/routes with kubeconfig-first architecture |
| 3 | Architecture hardening sprint definition | High | Convert investigation output into executable backlog |
| 4 | End-to-end operator path validation | Medium | Keep operator path healthy as secondary connectivity mode |

---

## Deferred / Later Horizon

| Item | Why deferred |
|------|--------------|
| OIDC/LDAP auth | Enterprise integration track, not required for current deployment model |
| CLI/SDK packaging | Valuable after API/versioning stabilizes |
| Full multi-cloud infra modules | AWS + kubeconfig-first path currently covers primary usage |
| Single-container distribution mode | Compose remains primary deployment target |

---

## Source of Truth

- Tactical execution backlog: `.agent/backlog/BACKLOG.md`
- Session-level status: `.agent/CURRENT_WORK.md`
- Long-term architecture decisions: `.agent/DECISIONS.md`
