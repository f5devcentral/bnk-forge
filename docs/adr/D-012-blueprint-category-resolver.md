# D-012 — BlueprintCategoryResolver (canonical Catalog → category rules)

- **Status:** Proposed
- **Date proposed:** 2026-05-17
- **Backlog id:** `architecture-blueprint-category-resolver`
- **Source memo:** 2026-05-17 deepening walk (new candidate #8)
- **Depends on:** none
- **Resume trigger:** any time a new Catalog repo with unfamiliar layout is onboarded, OR next ask to group/filter by category in UI/MCP/CLI beyond what PR #113 ships, OR next bug where the same Blueprint shows under two different categories.

## Context

- `backend/services/module_sync_service.py:~1001` — `_guess_category(path, name)`
- `backend/services/blueprint_sync_service.py:~285` — separate category-from-git-path heuristic
- `backend/services/blueprint_catalog_service.py` — `_resolve_category` fallback (added in PR #113)
- `backend/routes/blueprint_catalog.py:~97` — category passed through opaquely

Category derivation is reinvented in three places — Module sync, Blueprint sync, and a fallback in the Catalog read path. PR #113's grouping work had to fall back to grouping by `blueprint_id` because category itself is untrustworthy: the same Blueprint can land in `infra/` vs `kubernetes/` vs `bnk/` depending on the Catalog repo's layout.

**Deletion test:** remove all three guessers — every consumer (UI groupings, filter chips, MCP tool responses) collapses into "other". The classification rules never existed anywhere singular.

## Decision (deeper shape)

One Module owning the canonical Catalog → category rules. Interface (sketch):

```
classify(path, blueprint_metadata) -> Category
list_categories() -> [Category]
filter(blueprints, category) -> [Blueprint]
```

Sync paths and read paths both delegate. Includes precedence (infra vs app vs bnk) and validation that a guess didn't lie.

## Consequences

**Locality:** when a new Catalog repo lands with unfamiliar structure, the classification rule moves in one place — UI groupings and filter chips inherit.

**Leverage:** the MCP server, CLI Catalog views, and D-010 (EngineRegistry) all consume a stable category contract.

**Test win:** category semantics become a pure unit test (edge cases: ambiguous paths, version-suffixed dirs, new blueprint kinds) instead of a sync-roundtrip integration test.

## References

- Source: 2026-05-17 deepening walk
- Related context: PR #113 (Blueprint Catalog grouping) routed around this; this ADR is the systemic fix
- Code: `backend/services/{module,blueprint}_sync_service.py`, `backend/services/blueprint_catalog_service.py`
