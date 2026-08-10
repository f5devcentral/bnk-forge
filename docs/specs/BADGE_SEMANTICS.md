# Status Badge Vocabulary and Visual Semantics — UX-OPS-002

> Canonical badge specification mapping backend status states to consistent, accessible UI presentation.

Status: **Accepted** | Created: 2026-03-27

---

## Problem

The frontend has 14+ duplicated status mapping functions with inconsistent color/label choices. The same concept ("unknown") renders as amber in one component and grey in another. Badge labels use internal vocabulary ("critical") rather than operator language ("unhealthy").

---

## Badge Design Principles

1. **One badge per concept** — Don't show two badges for the same dimension.
2. **Color is not the only signal** — Labels and icons must be sufficient without color (accessibility).
3. **Labels reflect operator reality** — "Unhealthy" not "critical". "Partial" not "port_blocked".
4. **Consistent across all views** — The same backend status always produces the same badge.
5. **Severity drives urgency** — Red pulses for urgent, amber for attention, grey for informational.

---

## Canonical Badge Vocabulary

### Health Badges (HealthSeverity)

| Backend Value | Label | Dot Color | Text Color | BG | Pulse | Icon Suggestion |
|---------------|-------|-----------|------------|-----|-------|----------------|
| `healthy` | Healthy | emerald-400 | emerald-400 | emerald-500/10 | No | CheckCircle2 |
| `degraded` | Degraded | amber-400 | amber-400 | amber-500/10 | No | AlertTriangle |
| `unhealthy` | Unhealthy | red-400 | red-400 | red-500/10 | Yes | XCircle |
| `unknown` | Unknown | zinc-400 | zinc-400 | zinc-500/10 | No | HelpCircle |

### Connectivity Badges (ConnectivityStatus)

| Backend Value | Label | Dot Color | Text Color | BG | Pulse | Icon Suggestion |
|---------------|-------|-----------|------------|-----|-------|----------------|
| `connected` | Connected | emerald-400 | emerald-400 | emerald-500/10 | Yes | Wifi |
| `reachable` | Reachable | amber-400 | amber-400 | amber-500/10 | No | WifiLow |
| `partial` | Partial | orange-400 | orange-400 | orange-500/10 | No | ShieldAlert |
| `unreachable` | Unreachable | red-400 | red-400 | red-500/10 | No | WifiOff |
| `unknown` | Unknown | zinc-400 | zinc-400 | zinc-500/10 | No | HelpCircle |

### Operator Connection Badges

| Backend Value | Label | Dot Color | Text Color | BG | Pulse |
|---------------|-------|-----------|------------|-----|-------|
| `connected` | Connected | emerald-400 | emerald-400 | emerald-500/10 | Yes |
| `disconnected` | Disconnected | zinc-500 | zinc-400 | zinc-500/10 | No |
| `error` | Error | red-400 | red-400 | red-500/10 | No |

### Lifecycle Phase Badges (Generic)

For domain-specific lifecycle states (modules, tasks, stacks, deployments), the mapping follows a simple pattern:

| Phase Category | Badge Color | Examples |
|---------------|-------------|----------|
| Terminal success | emerald (green) | applied, deployed, completed, passed, synced |
| In-progress | blue | applying, deploying, in_progress, running, syncing |
| Pending/waiting | slate (grey) | pending, queued, not_initialized, scheduled |
| Failed/error | red | failed, error, *_failed |
| Cancelled/reverted | slate (grey) | cancelled, rolled_back, destroyed |

---

## Color Semantics

| Color Family | Meaning | Usage |
|-------------|---------|-------|
| **Emerald** (green) | Good / Success / Operational | healthy, connected, deployed, completed |
| **Amber** (yellow) | Attention / Degraded / Waiting | degraded, reachable, warning (legacy), pending |
| **Orange** | Partial / Blocked | partial connectivity, port-blocked (legacy) |
| **Red** | Bad / Failed / Urgent | unhealthy, unreachable, failed, error |
| **Blue** | In Progress / Active | applying, deploying, running, syncing |
| **Zinc** (grey) | Unknown / Neutral / Inactive | unknown, disconnected, pending, cancelled |

### Pulse Animation

Pulse should be used sparingly — only for states that demand immediate attention or indicate active work:
- `connected` (operator) — live connection indicator
- `unhealthy` — urgent attention needed
- `critical` (legacy) — same as unhealthy

---

## Tooltip Semantics

Every status badge should have a tooltip explaining:
1. **What the status means** — One sentence definition
2. **What caused it** (if diagnostic data available) — From `DiagnosticItem.message`
3. **What to do** (if actionable) — From `DiagnosticItem.suggestion`

Example:
```
Status: Partial
Message: Host responds to ping (170ms) but port 6443 is blocked.
Suggestion: Request firewall rule for TCP 6443 from this server.
```

---

## Accessibility Requirements

1. **Labels are always visible** — Don't rely on color alone. Every badge has a text label.
2. **Contrast ratios** — Badge text should meet WCAG AA (4.5:1 minimum).
3. **Icons supplement color** — Different icon shapes for different severities.
4. **Screen reader text** — Badge should include `aria-label` with full status description.

---

## Legacy Term Mapping

During migration, legacy terms still appear from fleet and BNK health endpoints:

| Legacy Term | Canonical Replacement | Badge Used |
|-------------|----------------------|------------|
| `critical` | `unhealthy` | Red, pulsing |
| `warning` | `degraded` | Amber |
| `offline` | `unhealthy` + connectivity context | Grey (legacy) or Red |
| `port_blocked` | `partial` | Orange |
| `active` (cluster DB) | Not a health state — de-emphasize | Green outline |

The `ConnectionStatusBadge` component already includes backward-compatible legacy entries.

---

## Implementation

### Existing Component: `ConnectionStatusBadge`

Already updated in PLAT-REL-001 to support canonical vocabulary with legacy backward compatibility. This component is the right place for health + connectivity badges.

### Consolidation Targets

The following duplicated badge logic should be consolidated into shared utilities:

| Duplicate | Location | Consolidation Target |
|-----------|----------|---------------------|
| `severityConfig` × 2 | BNKHealthDashboard, HealthDetailCard | Extract to `lib/health-severity.ts` |
| `getStatusColor` × 2 | ReleaseDetailPanel, RollbackReleaseDialog | Extract to `lib/status-colors.ts` |
| `getStatusBadge` × 14 | Various inline | Use shared `StatusConfig` or `ConnectionStatusBadge` |

### New Shared Utility: `lib/health-severity.ts`

```typescript
import { HealthSeverity } from '@/types';

export const SEVERITY_CONFIG: Record<HealthSeverity, SeverityConfig> = {
  healthy: { icon: CheckCircle2, color: 'text-emerald-500', bg: 'bg-emerald-500/10', label: 'Healthy' },
  degraded: { icon: AlertTriangle, color: 'text-amber-500', bg: 'bg-amber-500/10', label: 'Degraded' },
  unhealthy: { icon: XCircle, color: 'text-red-500', bg: 'bg-red-500/10', label: 'Unhealthy' },
  unknown: { icon: HelpCircle, color: 'text-zinc-400', bg: 'bg-zinc-500/10', label: 'Unknown' },
};
```

---

## Related Documents

- [Canonical Status Semantics (PLAT-REL-001)](STATUS_SEMANTICS.md)
- [Diagnostic Payload Standardization (PLAT-REL-002)](DIAGNOSTIC_PAYLOAD.md)
- [Status Surface Audit (PLAT-REL-003)](STATUS_SURFACE_AUDIT.md)
