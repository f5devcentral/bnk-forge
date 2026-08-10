# D-014 — DeployDialogOrchestrator (load/prereq/submit lifecycle)

- **Status:** Proposed
- **Date proposed:** 2026-05-17
- **Backlog id:** `architecture-deploy-dialog-orchestrator`
- **Source memo:** 2026-05-17 deepening walk (new candidate #10)
- **Depends on:** none (pairs naturally with D-015 — same files, different concerns)
- **Resume trigger:** introduction of a third Deployment entry point (MCP-driven deploy, bulk deploy, CLI deploy), OR next double-submit / button-flicker bug in either dialog, OR when StackDetailDialog exceeds ~2000 lines.

## Context

- `frontend-v2/src/components/stacks/StackDetailDialog.tsx:238-800` (1,825 lines)
- `frontend-v2/src/components/stacks/ImportedBlueprintDeployDialog.tsx:30-205` (477 lines)

Both Deployment dialogs run the same lifecycle: load template → fetch prerequisites → load credential templates → validate all → submit. StackDetail has five query states plus submission; ImportedBlueprint has three plus submission. "Disable submit while prerequisite check is in flight" is reimplemented in JSX conditionals in both files.

**Deletion test:** remove the orchestration from one dialog — the other still works, both have the same gate conditions. Two adapters of the same lifecycle = real seam.

## Decision (deeper shape)

A Module owning the multi-query state machine — parallel loading, prerequisite polling, ready-gate, error-class handling (409 project-exists vs other), submission. Interface (sketch):

```
useDeployPrep(target) -> { template, prerequisites, credentialTemplates, isReady, errors, submit }
```

Both dialogs consume it; chrome and form layout stay per-dialog.

## Consequences

**Locality:** prereq-polling semantics and submit-readiness rules move in one place; UX bugs like "button flickered enabled between queries, user double-clicked" become solvable once.

**Leverage:** when a third deploy entry point lands (MCP-driven deploy, bulk deploy, CLI), the orchestrator is the seam.

**Test win:** "submit disabled when prereq pending" is one assertion against the Module instead of two snapshot tests.

## Relationship to D-015

D-014 owns the *lifecycle* (when queries fire, when submit unlocks). D-015 owns the *data cascade* (how form values flow from credential template selection through region inheritance into mapped inputs). Same two files; orthogonal concerns. Land in either order or together.

## References

- Source: 2026-05-17 deepening walk
- Related: D-015 (FormCascadeModel — same files, complementary)
- Code: `frontend-v2/src/components/stacks/{StackDetailDialog,ImportedBlueprintDeployDialog}.tsx`
