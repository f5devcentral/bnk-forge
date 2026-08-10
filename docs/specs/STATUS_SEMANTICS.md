# Canonical Status Semantics — PLAT-REL-001

> The single source of truth for how BNK Forge represents operational state across backend, frontend, and MCP surfaces.

Status: **Accepted** | Created: 2026-03-27

---

## Problem

BNK Forge has 17+ status domains using ad-hoc string literals with no shared vocabulary. The same concept is expressed differently across surfaces:

| Concept | Backend A says | Backend B says | Frontend says |
|---------|---------------|----------------|---------------|
| "Everything works" | `healthy` | `active` | green badge |
| "Partially broken" | `warning` | `degraded` | amber badge |
| "Nothing works" | `critical` | `offline` | red badge |
| "We don't know" | `unknown` | (silently omitted) | amber badge |

This causes false-green (cluster shows "Active" but all ports are blocked), inconsistent naming (fleet says "offline", system says "degraded"), and lost diagnostic context.

---

## Canonical Status Model

### Three Orthogonal Dimensions

Status is NOT one thing. Every operational entity has up to three independent dimensions:

#### 1. Health Severity — "How broken is it?"

Answers: **What is the operational health of this thing right now?**

| Value | Meaning | Operator action |
|-------|---------|-----------------|
| `healthy` | All components functional, within expected parameters | None |
| `degraded` | Partially functional, some components impaired | Investigate soon |
| `unhealthy` | Non-functional or critically impaired | Investigate immediately |
| `unknown` | Cannot determine health (probe failed, no data, not configured) | Investigate reliability of monitoring |

**Ordering** (worst to best): `unhealthy` < `degraded` < `unknown` < `healthy`

**Replaces:**
- BNK severity: `critical` → `unhealthy`, `warning` → `degraded` (keeps `healthy`, `unknown`)
- Fleet status: `critical` → `unhealthy`, `warning` → `degraded`, `offline` → `unhealthy` (connectivity context distinguishes reason)
- System health: `degraded` stays, `offline` → `unhealthy`

**Why `unhealthy` instead of `critical`?** The word "critical" implies urgency/priority, which is a triage decision. "Unhealthy" is a factual assessment — the thing doesn't work. The urgency comes from context (is this a production cluster? a test cluster?).

#### 2. Connectivity — "Can we reach it?"

Answers: **What is the network reachability of this target?**

| Value | Meaning | Operator action |
|-------|---------|-----------------|
| `connected` | Network path open, API responsive | None |
| `reachable` | Network path open, but API not responding (auth issue, service down) | Check service/auth |
| `partial` | Some paths work, others blocked (e.g., ICMP works, TCP blocked) | Check firewall rules |
| `unreachable` | No network path (host down, route missing) | Check network/DNS |
| `unknown` | Not checked or unable to determine | Run connectivity check |

**Replaces:**
- Connectivity probe: `healthy` → `connected`, `port_blocked` → `partial`, keeps `reachable`, `unreachable`, `unknown`

**Why rename `healthy` → `connected` in connectivity?** "Healthy" is a health judgment. Connectivity is about reachability. A connected cluster can still be unhealthy.

#### 3. Lifecycle Phase — "Where is it in its process?"

Answers: **What stage of a process is this entity in?**

| Value | Meaning |
|-------|---------|
| `pending` | Queued, waiting to start |
| `active` | Running/executing/in-progress |
| `completed` | Finished successfully |
| `failed` | Finished with error |
| `cancelled` | Stopped by user/system before completion |

This dimension is domain-specific — modules have `planning`/`applying`, tasks have `queued`/`in_progress`, etc. The **existing domain-specific enums** (ModuleStatus, TaskStatus, StackStatus, etc.) are correct and should NOT be replaced. They already have well-defined StrEnum types.

The canonical lifecycle phase exists only as a **semantic classification** — it lets the frontend map any domain lifecycle to a display pattern (green for completed, red for failed, blue for active, gray for pending/cancelled).

---

### Dimension Applicability

Not every entity needs all three dimensions:

| Entity | Health | Connectivity | Lifecycle |
|--------|--------|-------------|-----------|
| Cluster (persisted) | ✓ | ✓ | — |
| Fleet member | ✓ | ✓ | — |
| BNK platform | ✓ | — | — |
| BNK component (pod) | ✓ | — | — |
| System service | ✓ | ✓ | — |
| MCP server | ✓ | ✓ | — |
| Module | — | — | ✓ (domain-specific) |
| Task/Job | — | — | ✓ (domain-specific) |
| Upgrade | ✓ | — | ✓ (domain-specific) |
| DPF infrastructure | ✓ | — | — |

---

## Current → Canonical Mapping

### BNK Health Severity

| Current | Canonical Health | Notes |
|---------|-----------------|-------|
| `healthy` | `healthy` | No change |
| `warning` | `degraded` | Rename for clarity |
| `critical` | `unhealthy` | Rename — "critical" is triage, not state |
| `unknown` | `unknown` | No change |

### Cluster Status (DB enum)

| Current | Dimension | Canonical | Notes |
|---------|-----------|-----------|-------|
| `active` | Lifecycle | Keep as-is | DB column, means "enabled in system" |
| `inactive` | Lifecycle | Keep as-is | User-disabled |
| `error` | Health | Map to `unhealthy` | In health dimension |
| `connecting` | Lifecycle | Keep as-is | Transient state |

The existing `ClusterStatus` enum tracks the **configuration lifecycle** in the DB. Health and connectivity are separate runtime dimensions that do NOT belong in the DB status column.

### Connectivity Probe

| Current | Canonical Connectivity | Notes |
|---------|----------------------|-------|
| `healthy` | `connected` | Rename — connectivity ≠ health |
| `reachable` | `reachable` | No change |
| `port_blocked` | `partial` | Generalize — partial reachability |
| `unreachable` | `unreachable` | No change |
| `unknown` | `unknown` | No change |
| `error` | `unknown` | Probe failure = can't determine |

### Fleet

| Current | Canonical Health | Notes |
|---------|-----------------|-------|
| `healthy` | `healthy` | No change |
| `warning` | `degraded` | Rename |
| `critical` | `unhealthy` | Rename |
| `offline` | `unhealthy` | With connectivity = `unreachable` |

### System Services

| Current | Canonical Health | Notes |
|---------|-----------------|-------|
| `healthy` | `healthy` | No change |
| `degraded` | `degraded` | No change |
| `offline` | `unhealthy` | Rename — "offline" conflates cause with state |

### DPF Health

| Current | Canonical Health | Notes |
|---------|-----------------|-------|
| `healthy` | `healthy` | No change |
| `partial` | `degraded` | Rename |
| `degraded` | `degraded` | No change |
| `no_devices` | `degraded` | Sub-state (qualifies why degraded) |
| `not_installed` | `unknown` | Not applicable — no DPF to assess |

---

## Backend Implementation

### New canonical enums: `backend/models/enums.py`

```python
class HealthSeverity(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

class ConnectivityStatus(StrEnum):
    CONNECTED = "connected"
    REACHABLE = "reachable"
    PARTIAL = "partial"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"
```

### Ordering functions: `backend/models/enums.py`

```python
# Health: unhealthy < degraded < unknown < healthy
HealthSeverity.ordering() → dict[HealthSeverity, int]

# Connectivity: unreachable < partial < reachable < unknown < connected
ConnectivityStatus.ordering() → dict[ConnectivityStatus, int]
```

### Migration path

1. **Add enums** — zero-risk, no existing code uses them yet
2. **Update `helpers.py`** — use `HealthSeverity` in `calc_severity()` and `rollup_severity()`
3. **Update connectivity probe** — use `ConnectivityStatus` in `ConnectivityProbeService`
4. **Update schemas** — use `Literal[...]` or enum in Pydantic `status` fields
5. **Broader adoption** — fleet, system health, DPF (separate tickets)

---

## Frontend Implications

The frontend `ConnectionStatusBadge` currently has 11 ad-hoc variants. The canonical model maps to:

| Badge Variant | Canonical Source | Visual |
|---------------|-----------------|--------|
| Healthy | Health = `healthy` | Green |
| Degraded | Health = `degraded` | Amber |
| Unhealthy | Health = `unhealthy` | Red |
| Unknown | Health/Connectivity = `unknown` | Gray |
| Connected | Connectivity = `connected` | Green (pulse) |
| Reachable | Connectivity = `reachable` | Amber |
| Partial | Connectivity = `partial` | Orange |
| Unreachable | Connectivity = `unreachable` | Red |

Frontend badge work is in **UX-OPS-002**.

---

## MCP Implications

MCP tools should return status fields using canonical vocabulary. When a tool reports cluster or fleet health, it should use `healthy`/`degraded`/`unhealthy`/`unknown`, not domain-specific legacy terms.

---

## Affected Surfaces (Implementation Inventory)

### Backend — Must adopt canonical enums

| Surface | File | Current Status Field | Canonical Dimension |
|---------|------|---------------------|-------------------|
| BNK severity helpers | `services/bnk/helpers.py` | `_SEVERITY_ORDER` dict | HealthSeverity |
| BNK health analysis | `services/bnk/health.py` | `calc_severity()` returns | HealthSeverity |
| Connectivity probe | `services/connectivity_probe_service.py` | String literals | ConnectivityStatus |
| Fleet health | `routes/operators/fleet.py` | `_derive_status_from_health()` | HealthSeverity |
| System health | `services/system_service.py` | String literals | HealthSeverity |
| DPF health | `services/dpf/health.py` | String literals | HealthSeverity |
| Scanner prereqs | `services/scanner/constants.py` | `PrerequisiteStatus` class | Keep as-is (domain-specific) |

### Schemas — Must type status fields

| Schema | File | Current | Target |
|--------|------|---------|--------|
| `ClusterConnectivityResponse.status` | `schemas/k8s.py` | `str` | `ConnectivityStatus` |
| `ClusterSummary.status` | `schemas/k8s.py` | `str` | `ClusterStatus` (existing enum) |
| `FleetOperatorHealth.status` | `routes/operators/__init__.py` | `str` | `HealthSeverity` |
| `ServiceHealth.status` | `schemas/system.py` | `str` | `HealthSeverity` |

### Frontend — Must align to canonical vocabulary

| Component | File | Current Variants | Canonical Mapping |
|-----------|------|-----------------|-------------------|
| `ConnectionStatusBadge` | `components/ui/ConnectionStatusBadge.tsx` | 11 ad-hoc | HealthSeverity + ConnectivityStatus |
| `BNKHealthDashboard` | `components/k8s/BNKHealthDashboard.tsx` | `severityConfig` | HealthSeverity values |
| `HealthDetailCard` | `components/health/HealthDetailCard.tsx` | `severityConfig` (duplicate) | HealthSeverity values |
| `SystemHealthPanel` | `components/system/SystemHealthPanel.tsx` | Inline mapping | HealthSeverity values |

---

## Follow-On Implementation Tickets

1. **PLAT-REL-001a** — Add `HealthSeverity` and `ConnectivityStatus` enums to `backend/models/enums.py` ← **DO NOW**
2. **PLAT-REL-001b** — Update `helpers.py` to use `HealthSeverity` enum ← **DO NOW**
3. **PLAT-REL-001c** — Update `ConnectivityProbeService` to use `ConnectivityStatus` enum ← **DO NOW**
4. **PLAT-REL-001d** — Update Pydantic schemas to use enums instead of bare `str`
5. **PLAT-REL-001e** — Update fleet health derivation to use `HealthSeverity`
6. **PLAT-REL-001f** — Update system health to use `HealthSeverity`
7. **PLAT-REL-001g** — Frontend canonical badge component (see UX-OPS-002)

---

## Related Documents

- [Strategic Backlog](../STRATEGIC_BACKLOG.md)
- [Sprint Plan — Platform Truthfulness 001](../SPRINT_PLATFORM_TRUTHFULNESS_001.md)
