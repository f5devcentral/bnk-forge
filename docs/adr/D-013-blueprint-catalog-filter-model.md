# D-013 — BlueprintCatalogFilterModel

- **Status:** Proposed
- **Date proposed:** 2026-05-17
- **Backlog id:** `architecture-blueprint-catalog-filter-model`
- **Source memo:** 2026-05-17 deepening walk (new candidate #9)
- **Depends on:** none (cleaner if D-012 lands first — category becomes a trustworthy filter axis)
- **Resume trigger:** introduction of a second catalog-like view (Stack Catalog, Module Catalog, Blueprint Releases list, MCP catalog tool), OR next bug in the platform/validation/release-state cascade, OR when the panel exceeds ~1200 lines.

## Context

- `frontend-v2/src/components/catalog/BlueprintCatalogPanel.tsx:143-200+` (965 lines total)

The Blueprint Catalog panel shipped in PR #113 carries seven independent filter `useState`s (search, platform, source, validation, releaseState, two delete-dialog states) plus cascade rules inline in JSX: validation choices depend on platform; release-state choices depend on import status.

**Deletion test:** no separate caller breaks because there *isn't* a separate caller. But as soon as the Blueprint Releases list, Stack Catalog view, or MCP Catalog tool wants the same filter semantics, the rules get re-derived. Hypothetical seam today; will become real with the second adopter.

## Decision (deeper shape)

Lift the filter state machine + cascade rules into a Module the panel consumes. Interface (sketch):

```
useBlueprintCatalogFilter(initial?) -> { filters, setFilter, validOptions, apply(items) -> filtered }
```

Render stays in the panel; rules (which combinations are valid, how filter A constrains filter B's options) become pure transformations.

## Consequences

**Locality:** a new filter (e.g., "by engine") or a new cascade ("category restricts platform list") edits one Module.

**Leverage:** a future Stack/Module catalog view inherits the same model — no second 965-line file.

**Test win:** cascade behavior tests stop rendering JSX; one assertion per rule, no React Query mocking.

## Note on the "two adapters" rule

This is one adapter today — hypothetical seam. Surface it now because (a) the panel is already large and growing, (b) D-012 will likely produce a second consumer once Catalog semantics stabilize, (c) cost to extract grows with the JSX it's woven into.

## References

- Source: 2026-05-17 deepening walk
- Related: D-012 (BlueprintCategoryResolver — gives this Module a trustworthy axis)
- Code: `frontend-v2/src/components/catalog/BlueprintCatalogPanel.tsx`
