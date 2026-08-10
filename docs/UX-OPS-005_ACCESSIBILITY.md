# UX-OPS-005: Accessibility Pass for Operational Status UX

**Status:** Complete
**Version:** 2.11.0

---

## Purpose

Ensure status information is communicable without relying solely on color,
and that operational screens meet basic accessibility standards.

---

## Audit Findings

### 1. Color-Only Status Indicators

| Location | Issue | Fix |
|----------|-------|-----|
| Module status badges | Use color + text | No fix needed — badges include text labels |
| Cluster health badges | Use color + text | No fix needed — "Connected", "Error" text included |
| Pod status badges | Color-coded | No fix needed — text labels present ("Running", "Failed") |
| Task status badges | Color + icon | No fix needed — text + icon combination |
| Health scores (BNK) | Color gradient | **Needs fix** — numeric score should have text label (e.g., "Good", "Degraded") |

**Overall:** Most status indicators already use text + color. The BNK health
score gradient is the only place where color is the primary differentiator.

### 2. Missing ARIA Labels

| Location | Issue | Recommendation |
|----------|-------|---------------|
| Icon-only buttons (refresh, settings gear) | Some missing `aria-label` | Add `aria-label` to all icon-only buttons |
| Sidebar collapse button | No label | Add `aria-label="Toggle sidebar"` |
| Theme toggle | No label | Add `aria-label="Toggle dark mode"` |
| Notification bell | Has label | No fix needed |
| Close buttons on dialogs | Uses `<DialogClose>` | No fix needed — Radix handles this |

### 3. Focus Management

| Location | Status | Notes |
|----------|--------|-------|
| Dialog focus trap | Good | Radix Dialog handles focus trapping |
| Tab navigation | Good | shadcn/ui components have proper tab order |
| Sidebar navigation | Good | Keyboard navigable |
| Data tables | Partial | Table rows are clickable but may not announce row content |
| Toast notifications | Good | Uses `role="alert"` via Sonner |

### 4. Screen Reader Support

| Feature | Status | Notes |
|---------|--------|-------|
| Page titles | Good | Each page sets document title |
| Live regions for dynamic content | Partial | Toasts use `role="alert"`, but loading states don't announce |
| Form error messages | Good | Validation errors linked to inputs |
| Empty states | Good | Text content describes what to do next |

### 5. Contrast Considerations

| Theme | Issue | Notes |
|-------|-------|-------|
| Dark mode | `text-slate-500` on `bg-zinc-900` | Borderline contrast — consider `text-slate-400` |
| Light mode | `text-slate-400` descriptions | May need `text-slate-500` for WCAG AA |
| Badges | Colored backgrounds with white text | Generally good contrast |

---

## Recommendations by Priority

### P1: Quick Wins (Low Effort, High Impact)

1. **Add `aria-label` to icon-only buttons**
   - Sidebar toggle, refresh buttons, settings gear, theme toggle
   - Pattern: `<Button aria-label="Refresh data" ...>`

2. **Add text labels to BNK health scores**
   - Currently: colored number (e.g., "87")
   - Should be: "87 — Good" or "87 (Healthy)"

### P2: Medium Effort

3. **Add `aria-live="polite"` to loading/data regions**
   - When data refreshes, announce "Data updated" to screen readers
   - Add to table containers and card grids

4. **Improve table row accessibility**
   - Add `role="row"` and `aria-label` with summary for clickable rows
   - Or use `<Link>` elements for navigation rows

### P3: Future Consideration

5. **WCAG AA contrast audit**
   - Run automated contrast checker (axe-core or Lighthouse)
   - Focus on `text-slate-400/500` on dark/light backgrounds

6. **Keyboard shortcuts documentation**
   - Document available keyboard shortcuts
   - Add `?` shortcut to show keyboard help

---

## Testing Approach

### Manual Testing

1. Navigate all pages using keyboard only (Tab, Enter, Escape)
2. Use browser zoom to 200% — verify no content overflow
3. Toggle dark/light mode — verify all text remains readable

### Automated Testing

Add to CI pipeline:
```bash
# Lighthouse accessibility audit
npx lighthouse https://localhost --only-categories=accessibility --output=json
```

### Screen Reader Testing

- Test with NVDA (Windows) or VoiceOver (macOS)
- Verify: page titles, navigation, status announcements, form interactions

---

## WCAG 2.1 Compliance Status

| Level | Status | Notes |
|-------|--------|-------|
| A (minimum) | Mostly compliant | Text alternatives, keyboard access, no seizure triggers |
| AA (standard) | Partially compliant | Some contrast concerns, missing live regions |
| AAA (enhanced) | Not targeted | Not a current goal |
