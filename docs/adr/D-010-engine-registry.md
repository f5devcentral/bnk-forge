# D-010 — EngineRegistry Module

- **Status:** Proposed
- **Date proposed:** 2026-05-13
- **Backlog id:** `architecture-engine-registry`
- **Source memo:** `architecture_deepening_2026-05-13_six_candidates.md` (#6)
- **Depends on:** none (testability foundation for several D-003 phases)
- **Resume trigger:** next D-003 phase that touches dispatch reliability or circuit-breaker logic, OR next flake caused by `_circuit_breaker_state` leaking across tests.

## Context

- `backend/services/execution/engine_router.py`
  - `_circuit_breaker_state` (module-level dict)
  - `_health_cache` (module-level dict)
  - Dispatch logic at lines 200-250

Engine health state lives in module-level dicts — no seam to mock per test, and one test mutating `_circuit_breaker_state["kubernetes"]` leaks into the next. The router Interface (`get_engine(name)`) advertises a simple lookup but hides circuit-breaking, last-probe-time, fallback. Callers can't control any of it from tests.

**Deletion test:** removing the globals doesn't eliminate circuit-breaking — it eliminates the false promise that the lookup is pure. Failing fast on unhealthy engines is correct behavior; today it's untestable.

## Decision (deeper shape)

An `EngineRegistry` Module owning health state and lookups. Interface (sketch):

```
get(name) -> EngineAdapter | EngineUnavailableError
```

Tests inject `FakeEngineRegistry` with controlled health. No global state.

## Consequences

**Locality:** circuit-breaker policy moves to one Module — half-open thresholds, probe cadence, fallback rules all live together.

**Leverage:** every dispatch test arranges engine failures deterministically. The Celery anti-patterns in D-003 #7 become easier to land because the seam exists.

**Test win:** the "module-level dict leaks across tests" class of flakes goes away.

## References

- Source memo: `architecture_deepening_2026-05-13_six_candidates.md`
- Related: D-003 (deploy reliability — this is testability foundation for several phases), D-005 (TaskOrchestrator — registry gives adapters their home)
- Code: `backend/services/execution/engine_router.py`
