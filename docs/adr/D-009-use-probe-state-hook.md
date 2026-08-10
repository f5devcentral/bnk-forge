# D-009 — useProbeState Hook (frontend reachability render seam)

- **Status:** Proposed
- **Date proposed:** 2026-05-13 (re-confirmed 2026-05-17)
- **Backlog id:** `architecture-probe-state-hook`
- **Source memo:** `architecture_deepening_2026-05-13_six_candidates.md` (#5)
- **Related follow-up:** `followup_unified_connectivity_indicators.md` (jumphost-widget extraction — different scope, same surface)
- **Depends on:** none (backend reachability model in D-002 already shipped)
- **Resume trigger:** next UI work that touches any reachability badge, OR introduction of a new probe state (e.g., "stale-healthy", "degraded"), OR D-002 Phase 2B (operator-active-reconnect).

## Context

- `frontend-v2/src/components/ClusterStatusBadge.tsx` (lines 56-91)
- `frontend-v2/src/components/SSHConnectivityBadge.tsx` (lines 48-84)
- `frontend-v2/src/components/ConnectivityBanner.tsx` (lines 42-69)
- `frontend-v2/src/pages/Dashboard.tsx` (inline reachability)
- `frontend-v2/src/pages/ProjectDetailV2.tsx:667-680`
- `frontend-v2/src/pages/K8sClusterList.tsx:343-415`

Three places independently encode the same state machine: `checking (amber) → reachable (emerald) | unreachable (red) | unknown (grey)`, plus tooltip text, retry button, stale-healthy UX. `useConnectivity` / `useSSHConnectivity` are deep at the hook layer, but the render seam at the badge level is shallow and duplicated. Strings ("Checking…") hardcoded in 3 components.

**Deletion test:** delete any one badge — the others still work, so it's not pass-through. But the state-machine vocabulary is duplicated 3× — change one state and you must change three.

## Decision (deeper shape)

`useProbeState(target)` hook returning:

```
{ state, label, icon, tooltip, onRetry }
```

One state vocabulary, one i18n surface. Badges become render-only adapters; banner and inline checks consume the same hook.

## Consequences

**Locality:** adding a new state (e.g., "degraded") edits one place.

**Leverage:** future probe types (MCP reachability, cred-template validation per D-011) reuse the same render seam.

**Test win:** state logic tests move off DOM snapshots into hook unit tests. Components become tiny render tests.

## Overlap with D-002

D-002 covers the backend reachability *model*. This is the frontend render seam, which D-002 explicitly punted to the queued `unified-connectivity-indicators` follow-up. Not a conflict.

## References

- Source memo: `architecture_deepening_2026-05-13_six_candidates.md`
- Related: D-002 (backend reachability model), D-011 (ConnectivityProbeRegistry — backend twin)
- Follow-up: `followup_unified_connectivity_indicators.md`
