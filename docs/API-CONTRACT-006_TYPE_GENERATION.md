# API-CONTRACT-006: Generated Type Strategy

**Status:** Complete
**Version:** 2.11.0

---

## Problem

The backend defines API shapes via Pydantic models. The frontend manually
maintains TypeScript interfaces that mirror these shapes. When the backend
changes, the frontend types can drift silently — causing runtime errors
that TypeScript cannot catch.

---

## Strategy: OpenAPI → TypeScript Generation

### Chosen Approach

**Tool:** `openapi-typescript` (npm package)
**Flow:** Backend Pydantic → FastAPI OpenAPI spec → `openapi-typescript` → `.d.ts` file

```
backend/main.py (FastAPI)
  → GET /openapi.json (auto-generated)
    → openapi-typescript (CLI)
      → frontend-v2/src/types/api.generated.d.ts
```

### Why This Approach

| Alternative | Pros | Cons | Decision |
|------------|------|------|----------|
| `openapi-typescript` | Zero runtime, `.d.ts` only, widely adopted | Needs OpenAPI spec | **Chosen** |
| `openapi-fetch` | Type-safe fetch client | Heavy, replaces apiClient | Rejected |
| `orval` | Generates hooks + client | Too opinionated, couples to React Query | Rejected |
| Manual sync | No tooling needed | Drift-prone, doesn't scale | Current (replacing) |

### Integration Points

1. **Generation command:** `make generate-types`
2. **CI check:** Fail if generated types differ from committed file
3. **Developer workflow:** Run after changing Pydantic models

---

## Implementation Plan

### Step 1: Add OpenAPI Export to Makefile

```makefile
generate-types:
	@echo "Exporting OpenAPI spec..."
	cd backend && python -c "from main import app; import json; print(json.dumps(app.openapi()))" > openapi.json
	@echo "Generating TypeScript types..."
	cd frontend-v2 && npx openapi-typescript ../backend/openapi.json -o src/types/api.generated.d.ts
	@echo "Types generated at frontend-v2/src/types/api.generated.d.ts"
```

### Step 2: CI Drift Check

```yaml
- name: Check generated types are up to date
  run: |
    make generate-types
    git diff --exit-code frontend-v2/src/types/api.generated.d.ts
```

### Step 3: Gradual Migration

1. Generate types file (don't change existing code)
2. Add `api.generated.d.ts` to version control
3. Gradually replace manual interfaces with generated imports
4. Add `// @generated` comment to prevent manual edits

---

## Type Mapping

| Pydantic Type | OpenAPI Type | TypeScript Type |
|---------------|-------------|-----------------|
| `str` | `string` | `string` |
| `int` | `integer` | `number` |
| `float` | `number` | `number` |
| `bool` | `boolean` | `boolean` |
| `datetime` | `string (date-time)` | `string` |
| `dict` | `object` | `Record<string, unknown>` |
| `list[T]` | `array` | `T[]` |
| `Optional[T]` | `T \| null` | `T \| null` |
| `Literal["a","b"]` | `enum` | `"a" \| "b"` |

---

## File Structure

```
frontend-v2/src/types/
├── api.generated.d.ts   # Auto-generated (DO NOT EDIT)
├── common.ts            # App-specific types (keep)
├── helm.ts              # Helm-specific types (migrate gradually)
└── ...                  # Other manual types (migrate gradually)
```

---

## Naming Convention

Generated types follow the Pydantic model names:

```typescript
// Generated from backend Pydantic models
export interface components {
  schemas: {
    ProjectResponse: { ... };
    ModuleResponse: { ... };
    TaskResponse: { ... };
    // etc.
  };
}
```

Usage in frontend:

```typescript
import type { components } from '@/types/api.generated';
type Project = components['schemas']['ProjectResponse'];
```

---

## Out of Scope

- Generating API client code (keep `apiClient` + manual service layer)
- Generating React Query hooks (keep manual hooks for control)
- Runtime validation (TypeScript types are compile-time only)
