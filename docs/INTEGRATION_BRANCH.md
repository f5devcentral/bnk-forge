# Integration Branch

## Model

The `integration` branch is a **disposable, auto-rebuilt aggregation point** — never a merge source and never committed to directly.

```
                  ┌─────────────────────────────────────────┐
                  │  Each piece of work                     │
                  │                                         │
   feature/foo ───┼──► PR → (review gate) ──► staging      │  ← durable path
   feature/bar ───┼──► PR → (review gate) ──► staging      │
   feature/baz ───┼──► PR → (review gate) ──► staging      │
                  └─────────────────────────────────────────┘

                  ┌─────────────────────────────────────────┐
                  │  integration (rebuilt every trigger)    │
                  │                                         │
                  │  = staging                              │
                  │    + feature/foo  (integrate label + ✓) │
                  │    + feature/bar  (integrate label + ✓) │
                  │    ✗ feature/baz  (red CI — excluded)   │
                  │                                         │
                  │  force-pushed, never merged from        │
                  └─────────────────────────────────────────┘
```

A PR that merges to `staging` is **automatically dropped** from `integration` on the next rebuild — it is already in staging and needs no special action.

---

## Rules

1. **PRs always target `staging`, never `integration`.**
   The reviewed `feature → staging` PR is the canonical unit of work. Do not open
   a PR from `integration` into anything.

2. **Never commit directly to `integration`.**
   The branch is force-pushed wholesale on every rebuild. Any direct commit will be
   silently discarded on the next run.

3. **Bugs found on `integration` are fixed on the owning feature branch.**
   Push the fix to your feature branch (or open a new `feature → staging` PR).
   The next rebuild will pick up the fixed head automatically.

4. **Never branch new work off `integration`.**
   Branch from `staging` (or from your own feature branch). `integration` is a
   read-only view of parallel in-flight work.

---

## Opting a PR into integration

Add the label **`integrate`** to any open PR that targets `staging`.

The rebuild workflow runs within 30 minutes (or immediately if a relevant event
fires first). A PR is included only when:

- it is open and targets `staging`
- it has the `integrate` label
- all its CI checks have passed (no `failure`, `cancelled`, `timed_out`, or
  `action_required` conclusions — advisory/skipped checks are non-blocking)

To opt out: remove the `integrate` label. The next rebuild drops the PR.

---

## `integration-conflict` label

When a PR cannot be merged cleanly into `integration` (git conflict with another
`integrate`-labeled PR or with staging), the rebuild:

1. aborts that merge and skips the PR
2. adds the **`integration-conflict`** label to the PR as a signal

The PR is **not excluded permanently** — resolve the conflict on your feature
branch and push. On the next successful merge the label is removed automatically.

The `integration-conflict` label has no effect on the reviewed `feature → staging`
PR; it is purely advisory.

---

## `INTEGRATION_PUSH_TOKEN` secret

GitHub intentionally prevents pushes made with the default `GITHUB_TOKEN` from
triggering downstream workflow runs. This means:

| Token used | integration rebuilt? | `integration-ci.yml` auto-runs? |
|------------|----------------------|----------------------------------|
| `GITHUB_TOKEN` (default) | Yes | No — must trigger manually |
| `INTEGRATION_PUSH_TOKEN` (PAT) | Yes | Yes — auto-runs after each push |

Integration branch CI is handled by a **standalone** `.github/workflows/integration-ci.yml`.
The existing `.github/workflows/ci.yml` (PR + staging + main CI) is **completely unchanged**
— it has no `integration` branch trigger and is not modified by this setup.

To enable automatic CI on `integration`:

1. Create a **Personal Access Token** (classic, `repo` scope) or a GitHub App
   installation token with `contents: write` on this repo.
2. Add it as a repository secret named **`INTEGRATION_PUSH_TOKEN`**.

Without the secret the rebuild still works; you just need to trigger CI on
`integration` manually (via the `workflow_dispatch` trigger on `integration-ci.yml`)
when you want a full pipeline run.

---

## Deploying integration locally

`integration` follows the same local deploy flow as the `local/integration` branch
described in the session handoffs. After checking out `integration`:

```bash
git checkout integration
make local-deploy        # macOS/laptop
# or
make deploy              # Linux server
```

Because `integration` is force-pushed, always do a `git fetch && git reset --hard origin/integration` rather than `git pull` to avoid local divergence.

---

## Rebuild workflow triggers

The workflow (`.github/workflows/integration-rebuild.yml`) fires on:

| Trigger | Reason |
|---------|--------|
| `push` to `staging` | New merge lands — reseat immediately |
| `pull_request` (labeled/unlabeled/synchronize/reopened/closed) | PR opts in/out or gets a new push |
| `check_suite` completed | A feature PR just went green — re-evaluate it |
| `schedule` every 30 min | Safety-net for any drift |
| `workflow_dispatch` | Manual trigger |

The `check_suite` trigger has a **loop guard**: if the completed suite's
`head_branch` is `integration` itself, the workflow skips. This prevents the
infinite loop of: rebuild → CI runs on integration → check_suite fires → rebuild → …

---

## Future: native merge queue on `staging`

GitHub's native **Merge Queue** (repo Settings → Branches → Require merge queue)
is a complementary mechanism for the *reviewed* line: it serially validates each
PR against the latest staging before merging, eliminating the need to keep feature
branches rebased. Enabling it requires branch-protection rules on `staging` that
require the "CI Gate" check. This is not part of the current PR but is a natural
next step once the reviewed-PR throughput warrants it.
