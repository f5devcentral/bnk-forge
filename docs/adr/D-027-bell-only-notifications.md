# D-027 — Bell-only, backend-persisted notifications (retire toasts)

> Renumbered from D-025 → D-027 (2026-06-05): D-025/D-026 were concurrently allocated by the fleet-entity / fleet-operator ADRs. Issues #255–259 and branches `feat/d025-p*` retain the old slug; the canonical ADR id is **D-027**.

- **Status:** Accepted
- **Date:** 2026-06-05
- **Source:** Operator feedback — "this is not a system a user stares at constantly, so most toasts are totally useless; the bell is standard for these things."
- **Sibling principle ADRs:** D-017 (success-contract — "HTTP 2xx ≠ operation success"), D-019 (dynamic-by-default)
- **Class of problem it governs:** ephemeral, non-actionable, easily-missed feedback for a low-attention operational console

## Context

Forge has **two unrelated notification systems**:

1. **Toasts** — Sonner popups via the `notify()` helper (`frontend-v2/src/lib/notify.ts`). `notify()` takes a `channel` (`'toast' | 'bell' | 'both'`) that **defaults to `'toast'`** (`notify.ts:106`). ~600 call sites use the default: `success` ×284, `error` ×164, `notifyError` ×106, `info` ×25, `warning` ×15, plus **14 functionally-transient** affordances (`loading` ×12, `promise` ×1, `progress` ×1; `undo` ×0). Toasts auto-dismiss in ~5s and are **never persisted**. `<Toaster/>` is mounted in `main.tsx`.

2. **The bell** — a backend-persisted notification center (`notifications` table → `models/system.py:196`; routes `routes/notifications.py`; UI `components/layout/Header.tsx:158`; polling hook `hooks/useNotifications.ts`; a deployment WebSocket bridge `components/providers/NotificationProvider.tsx`). Today **only** deploy lifecycle events reach it (via `deploymentNotify.*` using `channel:'both'` and the WS bridge). Everything else flashes as a toast and is lost.

The two systems do not talk to each other: a `notify.error()` shows a popup and disappears — it is never recorded in the bell. For a console an operator visits intermittently (not a chat app they watch live), the toast is the wrong primitive: the user is rarely looking when it fires, and there is no history to return to. The bell — persistent, badge-counted, reviewable on the operator's schedule — is the correct standard surface. This also directly worsened a real incident (the friendly "AWS credentials expired" message rendered only as a toast and was "easily missed").

### What is and isn't a notification

The discriminator for this migration:

- **A notification** is a *record of an event the operator may want to know about later* — an error, a completed async/long-running operation, a meaningful state change. It has value **after** the moment it fires → belongs in the **bell**.
- **A transient affordance** is *UI state for an in-flight interaction the operator is actively watching* — a button spinner while a request is in flight, a progress bar on an upload, an inline "undo" window. It has **no value after the moment** → belongs **inline at the point of action**, not in a notification system at all.
- **Pure chatter** — "Saved", "Copied to clipboard", "Filter applied" — has **no value even in the moment** for this audience → **suppressed entirely**.

## Decision

**The persisted bell is the single notification surface. Toasts are removed entirely.**

1. **No toasts.** The Sonner `<Toaster/>` and dependency are removed. No code path produces a popup.
2. **`notify.{success,error,warning,info}` and `notifyError` persist to the bell** (`POST /api/notifications`), carrying `severity`, `category`, and — where an action exists — an `action_url` deep-link (e.g. the parsed remote-auth route → Credential Templates).
3. **Transient affordances are reworked inline, not notified.** `loading`/`promise`/`progress` become inline spinners / progress UI at the call site (button-level pending state, inline progress bar). They never create a notification.
4. **Triage suppresses chatter.** Trivial success confirmations are removed at the call site. The bell records errors, async/long-running results, and meaningful state changes — nothing else. Silent success is acceptable for synchronous, immediately-visible actions (the UI already reflects the result).
5. **The bell becomes a first-class surface:** severity/category filtering, click-to-navigate (`action_url`), pagination, per-item dismiss, mark-all-read, retention.

### Severity / category model

| Field | Values | Drives |
|---|---|---|
| `severity` | `info` · `success` · `warning` · `error` · `critical` | icon/color, filter, badge weighting |
| `category` | `deployment` · `cluster` · `credentials` · `system` · `security` · `fleet` · `general` | grouping + filter |
| `action_url` | route string or null | click-to-navigate deep-link |
| `dedupe_key` | string or null | collapse duplicate events within a window |

`type` (the existing `success|error|info|warning` column) is retained for back-compat and mapped from `severity`.

### Required behavior

- **Persistence is the contract:** if a `notify.*` call is worth making, it is worth a bell row. If it isn't worth a row, the call is deleted (triage), not silently toasted.
- **Deep-link or nothing:** an error/result that has a relevant page MUST set `action_url` so the bell item is actionable; otherwise the operator can read but not act.
- **No unbounded growth:** retention caps rows per user and age (Celery-beat cleanup); `dedupe_key` prevents floods from repeated identical events.
- **Authz is enforced:** mark-read / delete are scoped to the owning user (today's PATCH/DELETE are unscoped — fixed in P1).

## Phased delivery (tracer-bullet vertical slices)

Each phase is independently shippable to `staging`.

- **P1 — Backend foundation.** Alembic migration adding `severity`, `category`, `action_url`, `dedupe_key`, `metadata`; create-time dedupe; retention (Celery-beat: delete read >30d, cap ~500/user); GET filters (`category`, `severity`) + cursor pagination; fix PATCH/DELETE RBAC scoping. *Bullet: a notification can be created with severity/category/deep-link, listed filtered, and GC'd.*
- **P2 — Channel flip.** `notify.{success,error,warning,info}` + `notifyError` default to the bell, deriving severity/category/`action_url`. Transients remain toast **temporarily** (carved out in P5) so the app stays functional mid-migration. *Bullet: a CRUD error lands in the bell, not a popup.*
- **P3 — Bell as primary UX.** Header bell: filters, grouping, click→navigate, infinite scroll, per-item dismiss, mark-all-read, richer item + empty state. *Bullet: clicking a bell item jumps to the resource.*
- **P4 — Triage sweep.** Per-call-site pass over ~600 sites: delete chatter, assign category/severity, set `action_url` where relevant. *Bullet: the bell shows only meaningful events.*
- **P5 — Zero toasts.** Rework `loading`/`promise`/`progress` into inline UI; remove `<Toaster/>` and the Sonner dependency. *Bullet: no popup code path remains in the bundle.*

## Consequences

- **History + reviewability:** every meaningful event is recoverable; the operator is no longer required to be looking at the right second.
- **Single surface, single mental model:** one place for "what happened", badge-counted.
- **Migration is incremental, not a rewrite:** the pre-existing `channel` abstraction means P2 is largely a default flip; the bulk of effort is the P4 triage and P5 transient rework. The app stays shippable at every phase.
- **Costs:** more `POST /api/notifications` writes (bounded by triage + dedupe + retention); the bell must earn its new role (P3 UX); a per-call-site sweep (P4) is unavoidable to avoid simply relocating the noise.
- **Risks:** (a) under-triage relocates noise into the bell — mitigated by the "is it worth a row?" test in P4; (b) losing the quick inline `undo`/`loading` affordance — mitigated by reworking them inline (P5) rather than deleting the UX; (c) backend write volume — mitigated by dedupe + retention.

### Reviewer checklist (apply to every PR after P2)

1. Does this PR call `notify.*` / create a toast? If a toast — reject; route to the bell or delete it.
2. Is the notification worth a persisted row for an operator reviewing later? If no — delete the call (don't relocate chatter).
3. Does an error/result with a relevant page set `action_url`?
4. Are `severity` and `category` set (not defaulted to `general`/`info` by accident)?
5. Is any new "loading/progress/undo" affordance inline UI, not a notification?

## References

- Investigation: notification architecture map (this session) — `notify.ts` API inventory, bell wiring, the toast/bell decoupling.
- Code: `frontend-v2/src/lib/notify.ts`, `components/layout/Header.tsx`, `components/providers/NotificationProvider.tsx`, `hooks/useNotifications.ts`, `backend/models/system.py` (`notifications`), `backend/routes/notifications.py`.
- Sibling ADRs: D-017 (success contract), D-019 (dynamic-by-default).
