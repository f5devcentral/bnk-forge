# D-020 — F5 Design System & Token Discipline (reskin + de-clown the layouts)

- **Status:** Accepted
- **Date:** 2026-05-28
- **Source:** UX directive — "make forge look F5-skinned and clean up the layouts; they've become a clown show, bright lights everywhere" + "layouts should be spacious like bnkhealth — many of our pages are crammed with a lot of information." Reference design: `gitlab-f5/bnkhealth` (the F5 BNK health dashboard).
- **Canonical design source:** **`gitlab-f5/bnkhealth/frontend` — adopt its palette values, its `F5Logo`, its typography, and its spacing rhythm verbatim.** The deep-dive design-language extraction is the spec.
- **Forge audit:** `frontend-v2/` styling audit — full token system exists but is bypassed ~3,983× across 217 files.
- **Palette precedent (consistency check):** `docs/roadmap.html` F5 restyle (commit `d4c56641`) — same discipline (F5 red as mark, slate neutrals, rationed status color); where it differs from bnkhealth, **bnkhealth wins.**
- **Sibling principle:** this is to *visual consistency* what D-019 is to *dynamic state* — a discipline ADR that governs new code and prioritizes a backlog, not a rewrite.

## Context

Forge's frontend (`frontend-v2`, Vite + React 18 + Tailwind 3.4 + shadcn/ui) has two problems: it's **loud** (vivid status colors painted onto whole cards, multi-hue gradients per category, a different blue/green/amber/red in every panel) and it's **cramped** (pages pack dense information with little whitespace). The reference app (`bnkhealth`) looks clean and "F5" not because of a special framework — both apps are Tailwind + React — but because of **discipline**: it themes everything structural through tokens, rations saturated color to a single accent plus small status pills, and gives content room to breathe (consistent 24px gutters, generous card padding, vertical rhythm). F5 red appears in exactly one place: the logo.

The audit's central finding inverts the obvious assumption:

> **Forge does not lack a design system. It has a complete one and ignores it.**

- A full, correct **shadcn HSL token set already exists** in `src/styles.css` — light **and** dark, plus bonus semantic tokens `--success` / `--warning` / `--info` — and is fully wired into named utilities in `tailwind.config.js` (`bg-primary`, `bg-card`, `text-muted-foreground`, `bg-success`, …). The layout shell (`AppShell` / `Sidebar` / `router`) is clean and token-aware.
- **But the tokens are bypassed almost everywhere:**
  - **~3,983** raw Tailwind palette-color usages across **217 files** (blue 976, red 862, amber 793, emerald 466, green 328, purple 193, orange 138, …).
  - **~850** whole-surface `bg-*-50/100` tints painting entire cards/banners — **this is the literal "bright lights everywhere."**
  - **149** files hand-fork light/dark with `isDark ? 'text-emerald-400' : 'text-emerald-600'` ternaries — re-deriving by hand exactly what the `.dark` token block already does.
  - **21** duplicate ad-hoc `getStatusColor` / `getFleetStatusColor` / `getReleaseStatusColor` helpers, plus 3 "central" libs (`lib/status-colors.ts`, `lib/health-severity.ts`, `lib/categoryColors.ts`) that themselves emit vivid raw colors.
  - **113** files use decorative gradients; `StackDetailDialog.tsx` assigns a *different multi-hue gradient per category* (the literal rainbow).
  - Page chrome is inconsistent: only **3 of 29 pages** use the shared `ResourcePageHeader`; **14 hand-roll** their own `text-3xl font-bold` headers — and many panels use tight padding and no vertical rhythm, so pages read as crammed.
  - `--primary` / `--ring` are generic bright blue; `tailwind.config.js` even hardcodes `destructive: "#ef4444"` instead of `hsl(var(--destructive))`.
  - **F5 red appears zero times** in the app; brand reads as "BNK-Forge blue + a status rainbow."

### The discriminator: token vs raw color

Mirror of D-019's deletion test, for color:

- **Structural color** (page/card/panel backgrounds, borders, body/label text, interactive accent, focus ring, status semantics) **MUST** come from a theme token. Raw palette classes for these are forbidden.
- **Genuinely incidental, data-bound color** (chart series, topology node categories, a syntax-highlighter theme) **MAY** use a fixed palette — but confined to that data-viz surface, never bleeding onto chrome.

A `bg-red-50` on a whole error card is structural → token (`bg-card` + a `text-destructive` badge). A 12-color categorical scale on graph nodes is data-viz → allowed, quarantined.

## Decision

Adopt one F5 design system expressed entirely through the existing shadcn token layer, give pages room to breathe, and **stop bypassing the tokens.** Concretely:

### 1. Brand & accent model — *bnkhealth values verbatim; red mark, blue accent*

All values below are **bnkhealth's actual palette** (`tailwind.config.js` + `src/index.css`). F5 red is **confined to the `F5Logo` SVG** — bnkhealth uses no red rail and no red UI surfaces, and neither will forge.

| Role | bnkhealth value | shadcn token (forge keeps the name, sets the value) | Notes |
|---|---|---|---|
| **Brand mark** | F5 red `#E4002B` | *(none — lives inside `F5Logo.tsx`)* | The ONLY saturated red anywhere. Not a UI color, not a rail, not a fill. |
| **Interactive accent** | `brand-600 #2563eb` / `brand-500 #3b82f6` | `--primary` (`#2563eb`), `--ring` | Active nav, primary buttons, focus rings, links — the single saturated color on a resting screen. |
| **Destructive / error** | `#ef4444` | `--destructive` | Distinct "danger" red; wire `hsl(var(--destructive))` (fix the hardcoded `#ef4444` in `tailwind.config.js`). |
| **Neutrals (light)** | bg `#ffffff` · secondary `#f8fafc` · tertiary `#f1f5f9` · card `#ffffff` · border `#e2e8f0` · text `#0f172a`/`#475569`/`#94a3b8` | `--background`/`--secondary`/`--muted`/`--card`/`--border`/`--foreground`/`--muted-foreground` | Slate ramp; borders separate surfaces, not heavy shadows. |
| **Neutrals (dark)** | bg `#111217` · secondary `#1e2028` · tertiary `#22252b` · card `#161a1f` · sidebar `#111217` · border `rgba(204,204,220,0.15)` · text `#ccccdc`/`0.65`/`0.40` | same tokens, `.dark` block | Near-black blue-grey, not pure black. |
| **Status** | emerald `#10b981` · amber `#f59e0b` · red `#ef4444` · info-blue · zinc (unknown) | `--success`/`--warning`/`--destructive`/`--info`/`--muted` | bnkhealth formula: `bg-{c}-500/10 text-{c}-700 dark:text-{c}-400 border-{c}-500/20`. **Tints on small elements only** — never whole-card fills. |

Values are stored as the shadcn HSL token format (`H S% L%`); the builder converts the hexes above exactly. Token *names* are forge's existing shadcn set (so 390 components keep working unchanged); token *values* become bnkhealth's.

### 2. Theme mode — *both, default light*

Retune **both** `:root` (light) and `.dark` token blocks to bnkhealth's palette; **default to light** (forge keeps the toggle). Light = bnkhealth light values (white/`#f8fafc`/`#f1f5f9` surfaces, `#e2e8f0` borders, `#0f172a` text); dark = bnkhealth dark values (`#111217`/`#161a1f`, near-black blue-grey). Because both blocks already exist, this is value-tuning, and it lets us **delete the 149 `isDark` ternaries** (the token blocks handle dark automatically). *(Note: bnkhealth itself ships dark-default; forge stays light-default per the locked decision — a one-line flip if we change our minds.)*

### 2a. Typography & logo — *adopt bnkhealth's*

- **Fonts:** Inter (sans) + JetBrains Mono (mono), loaded via a Google Fonts `<link>`, wired into `tailwind.config.js` `fontFamily`. Three weights in use: semibold / medium / bold. Small, dense type (`text-sm` body, `text-xs` labels) — *dense type, not dense layout* (see §2b).
- **Logo:** copy bnkhealth's `src/components/branding/F5Logo.tsx` verbatim (this is where — and the only place — F5 red `#E4002B` appears). Use it in the sidebar identity + login.

### 2b. Spacing & density — *give pages room to breathe* (bnkhealth's rhythm)

The pages are crammed; bnkhealth feels calm because whitespace does the work. Adopt its spacing rhythm and reduce per-view information density:

- **Consistent page gutter:** every page's content sits in `p-6` (24px) — already the `AppShell` `<main>` default; stop pages from adding their own ad-hoc padding.
- **Vertical rhythm:** sections stack with `space-y-6`; groups within a section `space-y-4`; grids `gap-4`/`gap-6`. Multiples of 4px, mostly 16/24.
- **Generous card padding:** the standard surface is `rounded-xl border bg-card p-6` (compact variant `px-4 py-4`) — not `p-2`/`p-3`. Panels breathe.
- **Progressive disclosure over cramming:** a view shows its primary information; secondary detail moves into tabs, expandable rows/accordions, "show more," or a drill-in — rather than packing everything onto one screen. Long lists paginate or virtualize.
- **Readable width:** prose/detail columns get a sensible max-width; full-bleed reserved for tables and topology.
- **Restraint = whitespace, not density:** removing color (the de-clown rules) and adding spacing are the same goal — a calm screen where the eye lands on what matters. Quiet eyebrow labels (`text-xs font-semibold uppercase tracking-wider text-muted-foreground`) replace big bold headers.

### 3. Color & layout discipline rules (the de-clown rules)

1. **Structural color comes only from tokens.** No `bg-/text-/border-{red,green,blue,amber,emerald,purple,orange,cyan,…}-NNN` in feature code.
2. **Status color lives in a small element** — a badge, a dot, or a 2px left-border — **never a card/banner background.** This single rule kills the ~850 surface tints (the biggest visual win).
3. **One accent.** Blue (`#2563eb`) is the only saturated chrome color; F5 red lives only inside `F5Logo` — no red rail, no red fills.
4. **No decorative gradients on chrome.** Remove per-category gradient maps; gradients (if any) only on intentional hero/marketing surfaces.
5. **Status vocabulary collapses to 5:** success (green) / warning (amber) / error (red) / info (blue) / unknown (grey) — via tokens. Emerald/teal/cyan/purple/orange/pink variants are dropped.
6. **Surfaces are uniform and spacious:** `rounded-xl border bg-card p-6`, subtle `shadow-sm`; borders do the separating; `space-y-6` rhythm between sections.
7. **Standard page chrome:** every page uses `ResourcePageHeader` (the `p-6` gutter) + `SectionCard`; no hand-rolled `text-3xl font-bold` blocks, no per-page padding.
8. **Don't cram:** secondary information goes behind tabs / accordions / drill-ins, not onto the primary view.

### 4. Consolidation & enforcement

- **One status-color utility.** Collapse `lib/status-colors.ts` + `lib/health-severity.ts` + the 21 local helpers into a single token-emitting function; rewrite `components/ui/badge.tsx` and `card.tsx` to be token-pure.
- **Standardize page chrome.** Enforce the existing shared `ResourcePageHeader` on all pages (currently only 3 of 29 do; 14 hand-roll `text-3xl font-bold`) and **add a `SectionCard`** primitive (the `rounded-xl border bg-card p-6` surface with the spacing rhythm baked in) for in-page panels.
- **Lint guard against regression — ESLint rule.** A custom `no-restricted-syntax` rule on `className` literals forbidding raw palette color classes (`bg-/text-/border-{red,green,blue,amber,emerald,…}-NNN`) in `src/`, outside the central color lib and an allowlisted data-viz set (see resolved decisions). Editor-level feedback + CI gate; ship warn-then-error so Phase 2's worklist = the rule's violation list.

### 5. Token strategy — *keep shadcn names, adopt bnkhealth's values*

Forge's shadcn HSL tokens are functionally equivalent to (and better integrated than) bnkhealth's `--bg-*`/`--text-*` vars — they're already wired into Tailwind utilities (`bg-card`, `text-muted-foreground`) that 390 components use. We **keep the shadcn token names** and set their **values to bnkhealth's** (§1). We adopt bnkhealth's *palette, logo, fonts, spacing, and discipline* — not its variable naming. Rewriting 390 components from `bg-card` → `bg-[var(--bg-card)]` would be churn with zero visual benefit.

## Rollout — phased, high-leverage first

**Phase 1 (fast, visible win — one reviewable PR):**
1. Retune `--primary`/`--ring`/neutrals/`--success`/`--warning`/`--info`/`--destructive` to bnkhealth's values (§1) in both blocks of `styles.css`; fix `tailwind.config.js` `destructive` to `hsl(var(--destructive))`.
2. Adopt typography: Inter + JetBrains Mono via Google Fonts `<link>`, wired into `tailwind.config.js`.
3. Copy bnkhealth's `F5Logo.tsx`; use it in `Sidebar` identity + `Login`; detune the blue gradients in `Sidebar`/`Login` to token-based (no gradient, `bg-primary`).
4. Rewrite the central color libs + `badge.tsx` + `card.tsx` to be token-pure (single status util); add the `SectionCard` primitive with the spacing rhythm baked in.
5. De-rainbow **and de-cram** the top offenders: `Dashboard.tsx`, `StackDetailDialog.tsx`, `SystemUpgrade.tsx`, `BareMetalPanel.tsx`, `BNKUpgradePanel.tsx` — token colors + `space-y-6`/`p-6` rhythm + move secondary detail behind tabs/accordions.
6. Add the ESLint lint guard (warn-then-error) so Phase 2 doesn't regress.
7. Verify both themes on localhost (light primary).

**Phase 2…N (systematic sweep, per feature area, separate PRs):** k8s → stacks → fleet → dpu/bare-metal/discovery → settings → projects/modules. Each PR: remove raw palette classes, drop `isDark` ternaries, route status through the central util, standardize on `ResourcePageHeader` + `SectionCard`, apply the spacing rhythm and progressive disclosure. Tracked as its own issue with a per-area checklist; the lint guard's error list is the worklist.

## Consequences

- **Biggest win is cheap:** ~6 central files + the surface-tint rule + the `SectionCard`/spacing primitive shift a large fraction of the look before the long-tail sweep even starts.
- **Reviews gain a checklist gate** (below); the lint guard makes the color part mechanical.
- **Deletes surface, not adds it:** 149 `isDark` ternaries and 21 duplicate helpers go away; net less code.
- **Migration is incremental, not a rewrite** — Phase 1 ships a coherent look; Phase 2 is opportunistic-but-tracked, mirroring D-019's "govern new code, prioritize the backlog" stance.
- **Cost / risk:** ~217-file blast radius means visual regressions are possible during the sweep; mitigated by phasing, per-area PRs, the lint guard, and localhost verification each phase. The shell is already clean, so layout risk is low. Spacing/progressive-disclosure changes are more judgment-driven than color (harder to lint) — caught in review.
- **Dark theme parity:** retuned in P1 but lighter-touch on verification (default is light); dark polish can trail.

### Reviewer checklist (apply to every PR)

1. Any raw palette color class (`bg-/text-/border-{color}-NNN`) on structural UI? → reject; use a token.
2. Is status color on a whole-surface background instead of a small badge/dot/border? → reject.
3. New `isDark ? … : …` color ternary? → reject; let the token block handle dark.
4. New local `getXStatusColor` helper? → reject; use the central util.
5. New decorative gradient on chrome, or a second accent color? → reject.
6. New page rolling its own `<h1>` instead of `ResourcePageHeader`? Panels not using `SectionCard`? → reject.
7. Is the view crammed — tight padding, no `space-y-*` rhythm, everything on one screen? → push secondary detail behind tabs/accordions; apply `p-6`/`space-y-6`.

## Resolved decisions (2026-05-28)

1. **Canonical source = bnkhealth, verbatim.** Adopt its palette values, `F5Logo`, typography, and spacing rhythm exactly; where any other precedent differs, bnkhealth wins.
2. **Lint mechanism:** custom **ESLint** `no-restricted-syntax` rule on className literals (editor feedback + CI), not a grep gate.
3. **Shared primitives:** enforce the existing **`ResourcePageHeader`** on all pages and **add a `SectionCard`** primitive (surface + spacing rhythm); status renders via the single status util / token-pure `badge`.
4. **Dark theme scope:** P1 = **light-correct + dark-functional**; a dedicated dark-polish pass trails (default is light).
5. **F5 red:** `#E4002B`, **confined to `F5Logo`** (no red rail / no red UI). On dark backgrounds the logo carries its own contrast — no separate brand-tint token.
6. **Data-viz allowlist** (exempt from the lint guard): topology (`reactflow`/`@xyflow` components), charts (`recharts` series colors), and the syntax highlighter theme. These may use a fixed palette, confined to those surfaces.
7. **Spacious layouts:** adopt bnkhealth's spacing rhythm (`p-6` gutters, `space-y-6`, generous card padding) and reduce per-view density via progressive disclosure (§2b).

## References

- Reference design: `gitlab-f5/bnkhealth/frontend` — tokens `src/index.css`, shell `src/App.tsx` + `src/chrome/*`, `src/lib/utils.ts` (`cn`, `statusColor`), `src/common/components/{statusbadge,resourcetable,tabs}`, spacing in `src/resource/overview/index.tsx` (`SectionCard`).
- Forge tokens: `frontend-v2/src/styles.css`, `frontend-v2/tailwind.config.js`.
- Forge central color libs (to consolidate): `frontend-v2/src/lib/{status-colors,health-severity,categoryColors}.ts`, `src/components/ui/{badge,card}.tsx`.
- Forge top offenders: `src/components/stacks/StackDetailDialog.tsx`, `src/pages/Dashboard.tsx`, `src/components/settings/SystemUpgrade.tsx`, `src/components/bare-metal/BareMetalPanel.tsx`, `src/components/k8s/BNKUpgradePanel.tsx`.
- Approved F5 palette: `docs/roadmap.html` (commit `d4c56641`).
- Sibling discipline ADR: `D-019-dynamic-by-default.md`.
