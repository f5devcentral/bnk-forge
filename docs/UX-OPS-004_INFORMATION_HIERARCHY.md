# UX-OPS-004: Information Hierarchy Review

**Status:** Complete
**Version:** 2.11.0

---

## Purpose

Audit operational screens for actionability — ensure status indicators are
prominent, errors stand out, and the most important information is above
the fold.

---

## Findings by Page

### Dashboard (Command Center)

| Element | Status | Notes |
|---------|--------|-------|
| Project summary cards | Good | Count + status at a glance |
| System health badge | Good | Top-right, color-coded |
| Attention cards | Needs fix | "View Error" button was no-op (fixed in audit) |
| Recent activity | Good | Chronological, timestamped |
| Quick actions | Good | Create project, add cluster prominent |

**Verdict:** Strong hierarchy. Most important metrics above the fold.

### Fleet Page

| Element | Status | Notes |
|---------|--------|-------|
| Cluster health cards | Good | Status badges prominent |
| DPF tab | Good | Accessible, clear navigation |
| Empty state | Good | Uses shared EmptyState component |
| Error state | Needs improvement | ErrorState added (audit fix), but could show more context |

**Verdict:** Good structure. Health status is the primary visual.

### Kubernetes Explorer

| Element | Status | Notes |
|---------|--------|-------|
| Cluster selector | Good | Dropdown at top |
| Resource counts | Good | Namespace/resource counts visible |
| Pod status indicators | Good | Color-coded badges (Running/Pending/Failed) |
| Error states | Fixed | ErrorState component added in audit |
| Resource detail panel | Good | Side panel with key-value pairs |

**Verdict:** Solid hierarchy. Resource status badges are effective.

### F5 BNK Page

| Element | Status | Notes |
|---------|--------|-------|
| Cluster selector | Good | Required before any view |
| Health dashboard | Good | Component health scores prominent |
| Sidebar categories | Good | Logical grouping (Insights → Build → Manage) |
| Gateway topology | Good | Visual graph representation |
| Empty states | Good | Per-resource-type empty states |

**Verdict:** Well-organized. The Insights → Build → Manage flow is intuitive.

### Project Detail

| Element | Status | Notes |
|---------|--------|-------|
| Module status badges | Good | Color-coded per state |
| Action menus | Good | Context-appropriate actions per state |
| Dependency indicators | Good | Shows blocked/ready state |
| State info popover | Fixed | Removed non-functional buttons (audit) |
| Deploy all button | Good | Prominent when modules ready |

**Verdict:** Good. Module lifecycle states are clearly communicated.

---

## Recommendations

### Priority 1: Consistent Error Prominence

All pages now have ErrorState components (post-audit). Ensure error states
include:
- Red/destructive color scheme
- Specific error message (not just "Something went wrong")
- Retry button
- Link to relevant diagnostics

### Priority 2: Stale Data Indicators

No pages currently show when data was last fetched. Add a subtle
"Updated X ago" indicator on pages with polled data (Fleet health,
K8s resources, task status).

### Priority 3: Notification Badge on Sidebar

The notification bell exists but sidebar navigation items don't show
counts or badges for items needing attention (e.g., failed tasks,
drift detected, expiring credentials).

---

## Information Hierarchy Principles (Documented)

1. **Status first** — Health/status badges should be the first thing visible
2. **Errors above fold** — Error states should never require scrolling to find
3. **Actions contextual** — Show only valid actions for current state
4. **Progressive disclosure** — Summary → detail → logs (don't show everything at once)
5. **Consistent severity colors** — Red = error, Yellow = warning, Green = healthy, Blue = info
