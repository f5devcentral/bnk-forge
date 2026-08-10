# Roadmap & Tracking Process

How architecture work flows from **decision → tracked → shipped**. Applies to humans *and* agents. Goal: everyone pulls in the same direction, and nothing decided gets lost.

## The pipeline

1. **Decide → ADR.** Any architecture decision becomes an ADR: `docs/adr/D-0XX-title.md` (or inline in `.agent/DECISIONS.md` for the legacy D-001..D-004). Status line: `Proposed` → `Accepted` → (`Superseded`). One decision = one ADR.
2. **Slice into GitHub issues.** When an ADR has buildable work, create issue(s) — one per epic, or a tracking issue + per-slice issues for multi-PR epics. Label `enhancement` + a theme label (e.g. `dynamic-by-default`). The issue body links its ADR and this roadmap. **GitHub issues are the cross-clone source of truth for "is it done"** — the `.agent/` framework is local & git-ignored (per clone), so it can't be the shared record.
3. **Add to the roadmap.** Add the item to `docs/roadmap.yaml` (the single source of truth) — either by hand or with `bin/roadmap-add.py` — then regenerate. **Do not hand-edit `docs/ROADMAP.md` or `docs/roadmap.html`; they are generated.** See "How to add/update a roadmap item" below. `docs/ROADMAP.md` is the shared "what's next"; `.agent/backlog/BACKLOG.md` is the *local* agent queue and just points here.
4. **Build → PR → `staging`.** PRs target `staging` (review-gated). Reference the issue in the PR body. Note: `staging` is non-default, so `Closes #N` does **not** auto-fire.
5. **Close + update status.** On merge: close the issue with a comment citing the PR (manually, `bin/maf-gh.sh issue close … --as-role lead --confirm-write --reason "…"`, or the auto-close Action once #185 ships). Flip the ROADMAP status to ✅. A partial epic gets a **progress comment** and stays open — never close an epic for a single slice.
6. **Regenerate the docs.** Both `docs/ROADMAP.md` and `docs/roadmap.html` are generated from `docs/roadmap.yaml` by `bin/roadmap-gen.py`; refresh them whenever the yaml changes (the add command does this for you).

## How to add/update a roadmap item

`docs/roadmap.yaml` is the **single source of truth**. `docs/ROADMAP.md` and `docs/roadmap.html` are both generated from it — never edit them directly (your edits will be overwritten on the next regen). The yaml's comment header documents the full schema.

**Dependencies:** the scripts need PyYAML. The repo venv has it — run them with `backend/.venv/bin/python`. (Plain `python3 bin/roadmap-gen.py` also works if your system Python has `yaml`; the scripts print a clear error and exit non-zero if it's missing.)

**Regenerate** (after editing the yaml by hand):

```
backend/.venv/bin/python bin/roadmap-gen.py
```

This rewrites `docs/ROADMAP.md` + `docs/roadmap.html` and is idempotent.

**Add an item** (appends to the yaml, then regenerates):

```
backend/.venv/bin/python bin/roadmap-add.py \
  --section <section-id> --title "..." --status <key> \
  [--refs "#216,PR #188"] [--note "..."] [--group "..."]
```

- `--list-sections` prints the available section ids + headings.
- `--status` must be one of: `shipped`, `in_progress`, `blocked`, `deferred`, `planned`.
- `--refs` is comma-separated; values like `#216` / `PR #188` become GitHub links.
- `--group` (optional) buckets the item into a named card on the HTML view.
- After adding, also update the **§12 issue index** (`render: raw`, edited by hand in the yaml) and re-run the generator so the index stays in sync.

> **Note:** `roadmap-add.py` rewrites the yaml via PyYAML, which drops the file's comment header and reflows multi-line strings (no `ruamel.yaml` in the venv). For larger/structural edits, edit `docs/roadmap.yaml` by hand and run `roadmap-gen.py` instead, which never touches the yaml. If the comment header is lost after an add, re-paste it from git history.

## For agents — answering "what's next?"

- Read `docs/ROADMAP.md` §1 (or open `docs/roadmap.html`). Pick the highest-priority **unblocked** item that has an open issue.
- Discover a new bug/idea mid-work → **file an issue + add it to the roadmap**; never leave an orphan TODO (the "no orphan bugs during refactor" rule).
- Always verify a "remaining" item against current `staging` before starting — memos and the roadmap can lag recent merges.

## Status vocabulary

✅ shipped · 🟡 in-progress / partial · ⛔ blocked (state the blocker) · 💤 deferred (state the resume-trigger) · ⚪ not-started / proposed.

## Where things live

| Artifact | Location | Shared? | Role |
|---|---|---|---|
| Decisions | `docs/adr/` + `.agent/DECISIONS.md` | ✅ docs/ tracked | the "why" |
| Roadmap (canonical) | `docs/ROADMAP.md` | ✅ | the "what's next" |
| Roadmap (human view) | `docs/roadmap.html` | ✅ | clickable, generated from ROADMAP.md |
| This process | `docs/ROADMAP_PROCESS.md` | ✅ | the "how" |
| Tracking | GitHub issues | ✅ | the "is it done" |
| Local agent queue | `.agent/backlog/BACKLOG.md`, `.agent/*` | ❌ per-clone | local MAF scaffolding; points at ROADMAP.md |
