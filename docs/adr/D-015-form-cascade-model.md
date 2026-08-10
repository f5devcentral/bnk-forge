# D-015 — FormCascadeModel (credential-template → region → value cascade)

- **Status:** Proposed
- **Date proposed:** 2026-05-13 (deferred) → re-proposed 2026-05-17
- **Backlog id:** `architecture-form-cascade-model`
- **Source memo:** 2026-05-17 deepening walk (new candidate #11; previously deferred on the 2026-05-13 memo's "Deferred" list)
- **Depends on:** none (pairs naturally with D-014 — same files, different concerns)
- **Resume trigger:** next "form won't repopulate after switching credential template" class bug, OR introduction of a new cloud provider with its own region/credential cascade, OR adoption alongside D-014.

## Context

- `frontend-v2/src/components/stacks/StackDetailDialog.tsx:241-564`
- `frontend-v2/src/components/stacks/ImportedBlueprintDeployDialog.tsx:85-128`

Both dialogs run identical `useEffect` chains: credential template selection → region autoselection from `template.region` → mapped input value inheritance → validation. The cascade is the source of the recurring "form won't repopulate after switching credential template" class of bugs.

**Deletion test:** removing the cascade from one dialog leaves the other working with identical logic. Two adapters of the same data cascade = real seam.

## Decision (deeper shape)

A Module owning the cascade transitions — given the prior cred-template / region / input state, compute the next valid form state. Interface (sketch):

```
useFormCascade(provider, templates, inputs) -> { state, onTemplateChange, onRegionChange, onInputChange, mappedValueFor(inputKey) }
```

Both dialogs consume it. IBM Cloud's `resource_group` inheritance, AWS region fallback, and any future provider's quirks live in one Module rather than scattered `useEffect`s.

## Consequences

**Locality:** provider-specific cascade quirks (IBM resource_group, AWS region fallback) move in one place.

**Leverage:** when Stack Detail and ImportedBlueprint Deploy get joined by a third dialog, or a CLI deploy command surfaces equivalent prompts, cascade rules are reusable.

**Test win:** "switching cred template clears stale mapped values" becomes a state-transition assertion, not a render test with three rerenders.

## Relationship to D-014

D-014 owns the *lifecycle* (when queries fire, when submit unlocks). D-015 owns the *data cascade* (how form values flow from credential template selection through region inheritance into mapped inputs). Same two files; orthogonal concerns. Land in either order or together.

## Why re-proposed

Originally deferred on the 2026-05-13 memo as "real duplication, lower urgency than #5". Re-elevated 2026-05-17 because D-014 and D-013 give it natural neighbors on the same surface — the marginal cost of bundling drops.

## References

- Source: 2026-05-17 deepening walk
- Related: D-014 (DeployDialogOrchestrator — same files, complementary)
- Code: `frontend-v2/src/components/stacks/{StackDetailDialog,ImportedBlueprintDeployDialog}.tsx`
