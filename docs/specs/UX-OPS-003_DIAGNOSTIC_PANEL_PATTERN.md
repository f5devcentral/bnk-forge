# UX-OPS-003: Shared Diagnostic Panel Pattern

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Define a reusable UI pattern for showing actionable diagnostics across operational screens (cluster health, connectivity, BNK status, drift).

---

## Diagnostic Item Structure

Every diagnostic item follows this data shape:

```typescript
interface DiagnosticItem {
  severity: 'critical' | 'warning' | 'info' | 'success';
  message: string;           // What happened (1 sentence)
  evidence?: string;         // Raw data/output supporting the diagnostic
  suggestion?: string;       // What the operator should do next
  source: string;            // Which check produced this (e.g., "connectivity_probe", "drift_check")
  timestamp: string;         // When this was last evaluated
  action?: {
    label: string;           // Button text (e.g., "View Logs", "Refresh")
    route?: string;          // Navigate to this route
    callback?: () => void;   // Or execute this function
  };
}
```

---

## Visual Layout

```
┌─────────────────────────────────────────────────┐
│ 🔴 Port 443 unreachable from server             │
│    Evidence: Connection refused after 10s timeout│
│    Suggestion: Check firewall rules for port 443 │
│    [View Connectivity Logs]              2m ago  │
├─────────────────────────────────────────────────┤
│ 🟡 Drift detected in 2 modules                  │
│    Evidence: main.tf modified since last apply   │
│    Suggestion: Review drift and re-apply         │
│    [Review Drift]                       15m ago  │
├─────────────────────────────────────────────────┤
│ 🟢 BNK topology healthy                         │
│    All 4 components reporting                    │
│                                         30s ago  │
└─────────────────────────────────────────────────┘
```

### Component Anatomy

1. **Severity icon** — Color-coded dot or icon (critical=red, warning=yellow, info=blue, success=green)
2. **Message** — Bold, 1-line summary of the diagnostic
3. **Evidence** — Monospace text showing raw data (collapsible for long output)
4. **Suggestion** — Italic text with recommended next action
5. **Action button** — Optional button to navigate or trigger action
6. **Timestamp** — Relative time since last check ("2m ago", "just now")

---

## Severity Ordering

Diagnostics are always sorted by severity:
1. `critical` — Red, always expanded, at top
2. `warning` — Yellow, expanded by default
3. `info` — Blue, collapsed by default
4. `success` — Green, collapsed by default (or hidden when other issues present)

---

## Usage Locations

| Screen | Diagnostic Source | Current State |
|--------|-------------------|---------------|
| Cluster detail → Connectivity | Connectivity probe service | Has diagnostic panel (custom) |
| Cluster detail → BNK Health | BNK health check service | Uses HealthDetailCard (custom) |
| Project detail → Drift | Drift check service | DriftDetailPanel (custom) |
| Fleet overview → Health | Fleet health aggregation | FleetOverview cards (custom) |
| Dashboard → Attention | Various services | AttentionCard (custom) |

**Problem:** Each location implements its own diagnostic display pattern. No shared component.

---

## Shared Component Spec

### `<DiagnosticPanel>`

```tsx
interface DiagnosticPanelProps {
  items: DiagnosticItem[];
  title?: string;             // Panel heading (e.g., "Diagnostics")
  collapsible?: boolean;      // Allow collapsing the panel
  maxVisible?: number;        // Show N items, "Show more" for rest (default: 5)
  emptyMessage?: string;      // Text when no diagnostics (default: "No issues detected")
  showTimestamps?: boolean;   // Show relative timestamps (default: true)
}
```

### `<DiagnosticItem>`

```tsx
interface DiagnosticItemProps {
  item: DiagnosticItem;
  defaultExpanded?: boolean;  // Override auto-expand based on severity
}
```

---

## Backend Contract

Diagnostic-producing endpoints should return items matching this shape:

```json
{
  "diagnostics": [
    {
      "severity": "warning",
      "message": "Port 443 unreachable",
      "evidence": "Connection timed out after 10s",
      "suggestion": "Check firewall rules",
      "source": "connectivity_probe",
      "timestamp": "2026-03-28T14:32:01Z"
    }
  ]
}
```

This aligns with PLAT-REL-002 (Diagnostic Payload Standardization).

---

## Migration Path

1. Create shared `<DiagnosticPanel>` component in `components/shared/`
2. Adopt in cluster connectivity page (most complex diagnostic display)
3. Adopt in BNK health page
4. Adopt in drift detail panel
5. Adopt in dashboard attention section
