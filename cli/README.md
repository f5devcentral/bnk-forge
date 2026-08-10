# bnk-forge CLI

A stdlib-only Python script for operating BNK-Forge from a terminal or CI/CD
pipeline. No `pip` dependencies — runs anywhere with Python 3.9+.

The CLI's purpose is to drive the **deploy → wait → assert → report** loop
that almost every CI/CD pipeline implements. Interactive features (browsing,
discovery, dashboards) live in the web UI; the CLI focuses on actions a
pipeline takes when it has decided what to do.

---

## Install

Copy `cli/bnk-forge` into a directory on your `$PATH` (or run it directly):

```bash
sudo install -m 0755 cli/bnk-forge /usr/local/bin/bnk-forge
bnk-forge version
```

In a CI runner, just `chmod +x cli/bnk-forge` from the checked-out repo and
invoke it directly.

---

## Authentication

The CLI accepts either a JWT (interactive) or a long-lived **API token**
(non-interactive). API tokens are the right choice for CI/CD.

### Issue a token (one-time, interactive)

```bash
bnk-forge login --url https://forge.example.com --username admin --password ******
bnk-forge auth token create --name ci-pipeline --role operator --expires-days 90
# → Token created: ci-pipeline (id=3, role=operator)
#    Save this token now — it will not be shown again:
#    bnk_K3M7QBVZX9A1HPNW2RS4TJEF6L8CGYDU
```

The plaintext token is shown exactly once. The DB stores only a SHA-256
hash; if the token is lost, revoke it and issue a new one.

A token can never grant more than the role it was issued with, and never more
than its owner's *current* account role — `--role` genuinely downscopes (an
admin can issue an `operator`- or `viewer`-only token for a CI pipeline, and
that token is refused admin-only routes). Demoting the owner immediately
narrows every token they issued, and a downscoped token cannot mint a more
privileged one.

### Use the token from CI/CD

Set two environment variables and the CLI works without any config file:

```bash
export BNK_FORGE_URL=https://forge.example.com
export BNK_FORGE_TOKEN=bnk_K3M7QBVZX9A1HPNW2RS4TJEF6L8CGYDU
bnk-forge project show production
```

Optional: `BNK_FORGE_INSECURE=1` to skip TLS verification (dev only).

### List / revoke

```bash
bnk-forge auth token list
bnk-forge auth token delete 3
```

---

## Command catalog

| Command | Description |
| --- | --- |
| `auth token create --name X [--role R] [--expires-days N]` | Issue a token. Plaintext shown once. |
| `auth token list` | List your tokens (no plaintext). |
| `auth token delete <id>` | Revoke a token. |
| `project show <id-or-name>` | Show project + module state. |
| `project deploy <id-or-name> [--module M] [--wait] [--timeout 30m]` | Deploy a module (or whole project). |
| `project plan <id-or-name> --module M [--timeout 30m]` | Plan a module. **Exits 2 if changes pending.** |
| `project destroy <id-or-name> [--module M] --yes [--wait]` | Tear down. `--yes` is required. |
| `project secret set <id-or-name> <key> [--value V \| --from-stdin \| --from-file PATH]` | Set a project secret. |
| `task status <task-id> [--wait] [--timeout 30m]` | Inspect or poll a Celery task. |

Projects accept either the numeric DB id or an exact project name (names are
not unique — an ambiguous name is an error, so prefer the id in automation).
Modules accept either the numeric DB id or the project's `path_in_project`
(e.g. `infra/vpc`).

### Output format

Every command honors `--format json` (or `--format table`, default). JSON
output is stable and meant to be parsed by downstream pipeline steps:

```bash
bnk-forge --format json project show prod | jq '.modules[].status'
```

### Exit codes

| Code | Meaning | Notes |
| --- | --- | --- |
| 0 | Success | All commands. |
| 1 | Generic error | Anything not covered below. |
| 2 | Plan has changes | Only `project plan`. Lets pipelines distinguish "drift" from "broken". |
| 3 | Auth failure | 401/403, or missing `BNK_FORGE_TOKEN`. |
| 4 | Not found | 404. Pipelines can decide whether that's expected. |
| 5 | Wait timeout | `--wait` exceeded `--timeout`. |

---

## Example: GitHub Actions deploy job

```yaml
name: Deploy infra
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Plan
        env:
          BNK_FORGE_URL: ${{ secrets.BNK_FORGE_URL }}
          BNK_FORGE_TOKEN: ${{ secrets.BNK_FORGE_TOKEN }}
        run: |
          ./cli/bnk-forge project plan production --module infra/vpc --timeout 10m
          rc=$?
          if [ $rc -eq 0 ]; then
            echo "::notice::No changes to apply."
            echo "should_apply=false" >> "$GITHUB_OUTPUT"
            exit 0
          elif [ $rc -eq 2 ]; then
            echo "should_apply=true" >> "$GITHUB_OUTPUT"
          else
            exit $rc
          fi

      - name: Apply
        if: steps.plan.outputs.should_apply == 'true'
        env:
          BNK_FORGE_URL: ${{ secrets.BNK_FORGE_URL }}
          BNK_FORGE_TOKEN: ${{ secrets.BNK_FORGE_TOKEN }}
        run: |
          ./cli/bnk-forge project deploy production --module infra/vpc --wait --timeout 30m

      - name: Report
        if: always()
        env:
          BNK_FORGE_URL: ${{ secrets.BNK_FORGE_URL }}
          BNK_FORGE_TOKEN: ${{ secrets.BNK_FORGE_TOKEN }}
        run: |
          ./cli/bnk-forge --format json project show production > project-state.json
          jq '.modules[] | {path: .path_in_project, status: .status}' project-state.json
```

### Piping secrets without leaking them to shell history

```bash
echo -n "$DB_PASSWORD" | bnk-forge project secret set production DB_PASSWORD --from-stdin
```

`--from-stdin` reads the value from stdin so secrets never appear on a command
line or in process listings.

---

## What's intentionally not in scope

Operations that *discover* state or browse catalogs (registry search,
blueprint browsing, project list with rich filters, drift dashboards) live in
the web UI. The CLI surface is deliberately narrow because in CI/CD you
already know what you want to do — you don't need to browse to it.

If you find yourself wanting to add a new command here because the pipeline
needs it, that's the right reason. If you're tempted to add a command because
it'd be a "nice mirror of the API", reach for the REST API directly instead.
