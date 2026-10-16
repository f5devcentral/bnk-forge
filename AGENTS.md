# AGENTS.md — Coding Agent Reference

> Optional. This file exists for coding agents; nothing in this repo requires
> you to use one. Humans should read **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**
> (build, test, architecture, style, gotchas) and
> **[CONTRIBUTING.md](CONTRIBUTING.md)** (contribution process) instead — those
> are the source of truth, and this file only adds agent-specific framing on top.

**Agents: read [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) first.** It carries the
build/lint/test commands, architecture, project structure, code style, testing
conventions, key gotchas, and git workflow. The sections below are the extra
context an autonomous agent needs and a human does not.

---

## Roadmap & Tracking — what to work on

**Asked "what's next"?** Read `docs/ROADMAP.md` (the shared roadmap; clickable human view `docs/roadmap.html`) and take the highest-priority **unblocked** item with an open GitHub issue. New decisions/epics flow **ADR → GitHub issue → roadmap → PR** per `docs/ROADMAP_PROCESS.md`. **GitHub issues are the source of truth for "done"**.

---

## Behavioral Guidelines

Bias toward direct action, low turn count, and surgical execution. Never run open-ended repository audits or sequential file chunking when a targeted edit is requested.

### 1. In-Place Fixes & Code Reuse (Fix Existing, Don't Reinvent)

- **Fix Existing Code In-Place**: Feedback or requests regarding existing functionality mean **fix or extend the existing code directly in-place**. Never create parallel implementations, duplicate functions (e.g., `_v2`), new route variants, or wrapper abstractions when existing code already handles that domain.
- **Fast 1-Step Discovery (No Wandering Audits)**:
  - To locate existing functionality, run **one targeted grep** (e.g., `git grep -n 'def <name>'` or `git grep -n '<feature_keyword>'`).
  - Find the existing home in **1 step**, jump directly to that line number, and modify it in-place.
  - **Anti-Pattern (Audit Wandering)**: Do NOT conduct wide multi-system audits, live container/docker queries, or read files in sequential chunks just to check for prior art. One targeted grep is sufficient.
- **Direct Edit from Grep (Zero Redundant Viewing)**: When `git grep -n` reveals the line number and code to edit (e.g. badges, labels, strings, flags, configs), **DO NOT call `view_file`**. Jump immediately from `git grep -n` to `replace_file_content`. Viewing lines already in grep output is pure turn waste.
- **When `view_file` is Needed**: Only use `view_file` for complex multi-line logic design. In that case, view the entire block in ONE single call (up to 250 lines). Micro-slicing and window chaining (e.g. 240-285, then 285-315) are strictly forbidden.
- **Zero Pre-Flight Hoarding (Task-by-Task Execution)**: When executing a multi-task audit or phase (e.g. Tasks 1 to 7), execute Task 1 immediately. Never read files for Tasks 2–7 before applying code for Task 1. Reading 20 files before making a single edit is strictly forbidden.
- **Batch Refactors & Multi-File Audits (> 3 files)**: Do not edit 15–30 files sequentially on the main thread (avoids 1,000-turn monster sessions). Dispatch 2–3 bounded `self` subagents grouped by functional area. Main coordinator aggregates results, verifies build/tests once, and stays under 20 turns.
- **Zero Scope Bleed**: When editing `backend/`, NEVER tour `frontend-v2/`, database tables, or callers unless explicitly requested.

### 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask: *"Would a senior engineer say this is overcomplicated?"* If yes, simplify.

### 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:
- Remove imports / variables / functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Commit conventions

Conventional Commits (`type: subject`, optional body, `BREAKING CHANGE:` footer for a
major). One repo-specific trap worth stating outright:

- **Never write a CI-control marker as literal text anywhere in a commit message —
  subject *or* body — even when quoting it in prose.** GitHub scans the whole message,
  so a `[skip ci]` / `[ci skip]` sitting in a sentence suppresses the run for that
  commit. This has bitten us twice, most recently on a shell-script change where the
  gates that got skipped (ShellCheck, Script Self-Tests, Secret Scan) were exactly the
  ones that mattered. Refer to it indirectly instead: "CI suppressed", "the skip-CI
  marker", or split it across backticks. The release job's *deliberate* skip is the
  only legitimate use, and it lands on the subject line where the release loop reads it.
  This is now **enforced**, not just documented: the `commit-lint` CI gate and the
  `.githooks/pre-push` hook both run `scripts/lint-commit-markers.sh`, which fails a
  push/PR whose commit range carries any CI-control marker (bonnyr-f5 #182 r3, #166:
  documentation is not enforcement).
- **Declare a major bump with a real `BREAKING CHANGE: <description>` footer**, not a
  bold `**BREAKING CHANGE**` heading or a bare colon-less line. `compute_version_bump.sh`
  majors on the phrase, so a prose line that *looks* like a footer ships a spurious major
  release; `commit-lint` rejects the line-start prose forms while allowing the plain
  footer.

---

