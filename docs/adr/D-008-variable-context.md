# D-008 — VariableContext Module

- **Status:** Proposed
- **Date proposed:** 2026-05-13
- **Backlog id:** `architecture-variable-context`
- **Source memo:** `architecture_deepening_2026-05-13_six_candidates.md` (#4)
- **Depends on:** none
- **Resume trigger:** next reported "variables resolve in wrong order" or "secrets missing because dep didn't materialize" class bug, OR opportunistically when touching `variable_assembler.py`.

## Context

- `backend/services/execution/variable_assembler.py` (180+ lines)
- `backend/app/tasks/kubernetes_tasks._build_variables` (lines 89-106)
- `backend/app/tasks/ansible_tasks._build_context` (lines 42-65)
- `backend/app/tasks/opentofu_tasks` (lines 180-200+, inline)

Engines all call `build_variables(db, module, operation)` then layer their own dependency-output merging, secret injection, and error translation on top. The full Interface (resolution + deps + secrets + defaults + error translation) is hidden behind 4 per-engine adaptations.

**Deletion test:** removing per-engine layers re-concentrates the resolution pipeline — it was earning its keep, but in 4 places. The Module's actual Interface is the pipeline, not `build_variables` alone.

## Decision (deeper shape)

A `VariableContext` Module owning the full resolution pipeline. Interface (sketch):

```
resolve(module, operation) -> ResolvedVariables | ResolutionError
```

Declared deps resolve before consumers; secrets prepared before render; missing-input errors translated once.

## Consequences

**Locality:** secret-injection rules, dependency-output merging, error translation move to one Module.

**Leverage:** cross-engine bugs (e.g., "ssh credentials missing because dep output didn't materialize") become a unit test.

**Test win:** variable resolution is testable independently of any engine — currently impossible without spinning up the engine.

## References

- Source memo: `architecture_deepening_2026-05-13_six_candidates.md`
- Related: D-005 (TaskOrchestrator) consumes resolved variables — clean handoff between the two
- Code: `backend/services/execution/variable_assembler.py`
