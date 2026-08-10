# D-011 — ConnectivityProbeRegistry (consolidate legacy + Phase-2A primitives)

- **Status:** Proposed
- **Date proposed:** 2026-05-17
- **Backlog id:** `architecture-connectivity-probe-registry`
- **Source memo:** 2026-05-17 deepening walk (new candidate #7)
- **Depends on:** none
- **Resume trigger:** D-002 Phase 2B (operator-active-reconnect) — Phase 2B will want the unified probe primitive, OR next bug where banner-suppression / classification rules diverge between legacy and Phase 2A paths.

## Context

- `backend/services/reachability/registry.py` (Phase 2A — async Probe ABC + registry)
- `backend/services/reachability/probes/cluster.py:74-113` — `ClusterProbe` imports `_parse_api_server`, `_probe_tcp`, `_probe_k8s_api` from the legacy module
- `backend/services/reachability/probes/ssh.py:43-82` — `SSHProbe` wraps `TunnelManager` directly
- `backend/services/connectivity_probe_service.py` — legacy synchronous ICMP/TCP/K8s-API primitives (pre-Phase-2A)

Two independent probe implementations coexist. Phase 2A introduced a deep Module at the registry layer, but the connectivity primitives it depends on still live in `connectivity_probe_service.py`. The new `ClusterProbe` borrows three private helpers from the old module — coupling across what should have been the new seam. Banner-suppression (PR #102), timeout policy, and error classification can drift between the two paths.

**Deletion test:** delete `connectivity_probe_service.py` → `ClusterProbe` breaks immediately. The registry is shallow on the connectivity side.

## Decision (deeper shape)

Pull the probe primitives (host parsing, TCP/ICMP/K8s-API checks, latency measurement, error categorization) behind a single Module. Interface (sketch):

```
ConnectivityProbePayload.check(host, port, kind) -> ProbeResult(state, latency_ms, error_category, error_context)
```

Both the legacy sync path and the new async `Probe` adapters consume it. Banner-suppression, timeout policy, and error categorization live in one place.

## Consequences

**Locality:** D-002 Phase 2B plugs in once instead of touching two paths.

**Leverage:** future probe types (MCP-side reachability, cred-template validation, BNK Operator health) get the same primitives for free.

**Test win:** cross-path classification consistency ("why does SSH auth map to `ErrorCategory.AUTH` but a K8s 401 doesn't?") becomes a single unit test instead of an integration scenario.

## References

- Source: 2026-05-17 deepening walk
- Related: D-002 (backend reachability model — this consolidates its primitives), D-009 (frontend twin), D-010 (EngineRegistry — different surface, same global-state pattern)
- Code: `backend/services/reachability/`, `backend/services/connectivity_probe_service.py`
