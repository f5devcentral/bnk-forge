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

Bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:
- Check whether it already exists. Grep for the capability by name and by what it shells out to (`docker`/`buildx`, `helm`, `kubectl`, Make targets) before writing new code — extend the existing path, don't fork a parallel one.
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

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

