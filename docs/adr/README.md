# Architecture Decision Records

Per-decision files. The roll-up index lives in [`.agent/DECISIONS.md`](../../.agent/DECISIONS.md); start there.

## Format

Each file is one decision: ID, title, status, context, decision (deeper shape), consequences, references. Loosely MADR — see existing files for the exact shape.

## Statuses

- **Proposed** — surfaced by `/engineering-improve-codebase-architecture`; no commitment yet. A future agent can pick one up, write a TASK.md, and transition.
- **Accepted** — work has started or is queued; full RFC may live in user memory.
- **Deferred** — accepted in spirit, parked behind a dependency. The ADR documents the resume trigger.
- **Rejected** — load-bearing reason recorded so future architecture reviews don't re-suggest the same thing.
- **Superseded by D-NNN** — replaced; keep the file, mark the supersession.

## Vocabulary

Use the terms in [`.agent/context/architecture-language.md`](../../.agent/context/architecture-language.md) — Module / Interface / Implementation / Depth / Seam / Adapter / Leverage / Locality. Apply the deletion test. One adapter = hypothetical seam; two = real.

## When transitioning a Proposed ADR

1. Read the ADR file end-to-end.
2. Re-run the deletion test against current code — code shifts; the ADR may be stale.
3. Create `.agent/tasks/active/<backlog-id>/TASK.md` (the backlog id is in the ADR).
4. Bump the ADR status to `Accepted` and add a `Tracking:` line pointing to the task / PR.
5. Update `.agent/DECISIONS.md` index.

## Numbering

Sequential, never reused. D-001..D-004 are inline in `.agent/DECISIONS.md`. D-005 onward are per-file here (currently through D-034).
