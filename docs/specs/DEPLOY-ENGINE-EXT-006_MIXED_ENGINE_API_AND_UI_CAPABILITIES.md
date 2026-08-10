# DEPLOY-ENGINE-EXT-006 — Mixed-Engine API and UI Capability Exposure

> Promoted from accepted `.agent` planning artifacts after implementation completion.

## Objective

Expose engine type and lifecycle capability truthfully in the API and UI while preserving the product’s module-centric mental model.

This ensures users can understand what kind of module they are using and what operations are actually supported, without assuming all modules behave like OpenTofu modules.

## Decision summary

### Adopt

1. **Main UX remains centered on “modules.”**
2. **Expose engine/capability metadata additively**, not as a replacement vocabulary.
3. **Do not offer unsupported actions in UI.**
4. **Prefer backend-provided capability metadata** over frontend inference.
5. **Preserve legacy fallback behavior only where metadata is absent**, but make explicit metadata the preferred source of truth.

---

## Why this slice is necessary

Evidence from the current code path that existed before this work:

1. frontend engine typing was too narrow
2. backend module-library route used heuristic engine inference
3. UI actions assumed mostly OpenTofu-like behavior

Conclusion:
- once new engines arrive, capability truth must be surfaced centrally from the backend or the UI will misrepresent what a module can do

---

## API contract direction

Relevant module- and blueprint-related API responses should expose explicit engine and lifecycle capability metadata.

Recommended response additions:

For catalog module responses:
- `engine_type`
- `module_type` (still useful, but not the primary capability source)
- `is_builtin`
- `lifecycle_capabilities`
- `runner_profile` (optional for advanced/detail views)
- `source_kind` / `pack_manifest_present` (optional if useful)

For project module responses:
- module engine type
- lifecycle capabilities
- capability-aware action hints if helpful

For stack/blueprint detail responses:
- engine and lifecycle capability metadata per included module

---

## Backend source of truth

Backend should compute or expose capability metadata from stored catalog data, not require frontend inference.

Recommendation:
- use `ModuleLibrary.engine_type` and `pack_manifest` / compatibility metadata as the main source for API serialization

Fallback only when metadata is absent:
- built-in Python registry module → infer `kubernetes`
- legacy non-pack Terraform module → infer `opentofu`

This fallback is transitional, not the long-term primary contract.

---

## Proposed capability payload shape

```json
{
  "engine_type": "ansible",
  "lifecycle_capabilities": {
    "init": true,
    "plan": true,
    "apply": true,
    "destroy": false,
    "refresh": false,
    "drift": false
  }
}
```

Why not reuse raw manifest booleans directly in UI:
- backend should map manifest semantics into a stable API contract the frontend can trust without knowing manifest internals

---

## Module library UI behavior

Module library views remain module-first, with engine/capability details shown as metadata badges/details.

Recommended additions:
- engine badge:
  - OpenTofu
  - K8s
  - Ansible
  - Script (later)
- capability summary:
  - supports plan
  - supports destroy
  - etc.
- source detail:
  - built-in / approved source / pack-based

Placement:
- visible but secondary in list cards/tables
- more detailed in module detail dialogs/panels

---

## Project module action behavior

Project-level action menus must only offer actions the module actually supports.

Examples:
- if `destroy=false`, do not show destroy action or show it disabled with explicit reason
- if `plan=false`, do not present standard plan dialog as if supported
- if `apply=true`, deploy/apply remains available

This is the main product truthfulness requirement for mixed engines.

---

## Blueprint/stack presentation behavior

Blueprint/stack detail views should surface mixed-engine composition without renaming the blueprint/module model.

Recommended behavior:
- show engine badge per module row/card
- show warnings if blueprint contains modules with mixed lifecycle capabilities
- prevent misleading bulk actions if some included modules do not support them in the same way

Example:
- if a blueprint contains modules where destroy is not universally supported, bulk destroy flow should warn explicitly and explain which modules are non-destructive or unsupported

---

## Frontend type changes

Update TypeScript types to treat engine/capability metadata as first-class.

Immediate type expansion recommendations:

### `ModuleLibrary.engine_type`
- `kubernetes`
- `opentofu`
- `ansible`
- `script`
- `container` (vendor `*ctl` runner images — EXT-003 Amendment A; needs its own engine badge)
- `ssh`

### Add
- `lifecycle_capabilities?: { init: boolean; plan: boolean; apply: boolean; destroy: boolean; refresh: boolean; drift: boolean }`

### Keep
- `is_builtin`
- `module_type`

---

## UX truthfulness rules

Adopt these product rules:

1. Do not display unsupported operations as available.
2. Do not label a plan/preview experience as equivalent across engines if it is not.
3. Keep engine details visible enough to avoid surprises.
4. Keep module terminology intact.
5. When capability metadata is missing for a legacy module, use backward-compatible defaults but avoid overclaiming advanced support.

---

## Transitional compatibility behavior

Legacy modules without explicit capability metadata should still render, but with inferred defaults.

### Suggested defaults

#### legacy OpenTofu module
- init=true
- plan=true
- apply=true
- destroy=true
- refresh=true
- drift=true

#### built-in K8s module
- init=true
- plan=true
- apply=true
- destroy=true or capability-specific if existing module type needs nuance

Why:
- keeps current UX functional while new pack-aware metadata gradually becomes dominant

---

## Testing expectations

Required test areas:

1. backend serialization returns explicit engine/capability metadata when present
2. legacy fallback behavior remains correct when metadata absent
3. frontend renders new engine badges correctly
4. unsupported actions are hidden/disabled correctly
5. blueprint/stack views behave truthfully with mixed engine modules

---

## Rejected alternatives

- keep engine inference primarily in frontend
- rename all user-facing modules to deployment packs
- show all actions uniformly and fail later at execution time

Reasons:
- too brittle and duplicates product semantics in the wrong layer
- unnecessary UX churn and contradicts resolved direction
- misleading and avoidably frustrating
