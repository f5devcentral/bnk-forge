# bnk-forge end-to-end hardware harness

Drives a full discovery → flash → connectivity-matrix run against
real DPUs by hitting the bnk-forge REST API. Designed to run from any
machine that can reach the bnk-forge HTTPS endpoint *and* the worker
nodes / DPU BMCs.

## Quick start

```bash
# 1. Install harness deps once (pyyaml, pydantic, requests, urllib3, reportlab).
pip install -r scripts/e2e/requirements.txt

# 2. Copy the example config + edit IPs, key paths, BFB image filename.
cp scripts/e2e/e2e-config.example.yaml e2e-config.yaml
$EDITOR e2e-config.yaml

# 3. Run via the shell wrapper (prompts for confirmation).
BNK_FORGE_PASSWORD=$(pass show bnk-forge/admin) scripts/e2e.sh

# Or non-interactively (CI / scripting):
BNK_FORGE_PASSWORD=$(pass show bnk-forge/admin) scripts/e2e.sh --yes

# Direct Python invocation also works:
BNK_FORGE_PASSWORD=$(pass show bnk-forge/admin) \
  python -m scripts.e2e            # picks up ./e2e-config.yaml by default
```

Reports land in `e2e-reports/<timestamp>/{report.json,report.md,report.pdf}`.
The PDF is the operator's primary artifact: cover page with pass/fail
totals, network topology diagram (DPU IPs + DOCA/NIC/BMC firmware),
step-by-step results table, and per-step evidence excerpts.

If a dependency is missing, the harness prints the exact `pip install`
command before exiting (no stack trace).

## Phases

This harness is being built out incrementally. Today (Phase 0+1) it
covers the **non-destructive smoke flow**:

| # | Step | What it does |
|---|---|---|
| 1 | `deploy_bnk_forge` | Clean-room deploy: `make _install-clean` (wipes bnk-forge containers + volumes + images) → `make clean` (auto-yes; prunes dangling Docker images/build-cache/orphan volumes globally) → `make build-clean` (`--no-cache` rebuild) → `make _install-start` (bring up + wait healthy). Refuses if any `bnk-forge` container is already running. Skipped when `local_deploy=false` or `bnk_forge_url` is remote. |
| 2 | `login` | Fetch JWT, store on the client |
| 3 | `create_jumphost` | Register the jumphost SSH credential (skipped if no `jumphost:` block) |
| 4 | `create_project` | Create a timestamped bare-metal project, attach jumphost |
| 5 | `configure_dpu_settings` | Patch project DPU settings: bf.conf template, BFB image, BMC + DPU OS passwords |
| 6 | `run_discovery_workers` | Trigger discovery against the worker-node IPs, wait for completion |
| 7 | `register_hosts` | Register every DPU host the discovery found |
| 8 | `discover_dpus` | Run per-DPU Discover (rshim/lspci for in-band, Redfish for BMC) |
| 9 | `probe_dpu_os` | Run Probe DPU OS All — captures uname, mlxconfig, LACP, connectivity, firmware |
| 10 | `run_matrix` | Trigger DPU↔DPU connectivity matrix, record outcome |

Phase 2 covers the destructive lifecycle: `auto_assign_vlan_ips` →
`flash_dpus` → `wait_flash_complete` → `probe_dpu_os_post_flash` →
`run_matrix_post_flash` (asserts every cell green). Factory-reset
steps exist in `steps.py` but are not in `PHASE_2_STEPS` until a
BMC-discovery step lands that populates each DPU's `bmc_ip`; the
Redfish reset path needs that to talk to the BMC.

## CLI flags

```
positional argument:
  config              path to YAML config

--steps STEPS         comma-separated step names; default: all phase-1
--report-dir DIR      output directory (default: e2e-reports/<ts>/)
--dry-run             list steps without running anything
--continue-on-failure keep running after a step fails
-v, --verbose         debug logging
```

Step names match the table above — e.g.
`--steps login,create_project,configure_dpu_settings` to set up the
project shell only.

## Idempotency / no-cleanup

By design, no step deletes anything on bnk-forge. Project names are
timestamped so re-runs don't collide. The jumphost SSH credential is
named after the host so it's reused across runs. Stale projects can
be cleaned up via the bnk-forge UI when you're done.

## Design

- `config.py` — Pydantic-validated YAML loader. IP fields accept
  single addresses, CIDRs, or `prefix.A-B` shorthand.
- `client.py` — thin REST client. One method per endpoint we use.
  Backend `detail` / `message` fields are extracted from error
  responses so the operator sees *why* a step failed.
- `result.py` — `StepResult` dataclass + `StepRecorder` context
  manager + JSON/Markdown report writers.
- `steps.py` — one function per step. Each takes a `Context`
  (config + client + step-to-step state dict) and returns a
  `StepResult`. Status flips to `warn` when the step succeeds but
  produced unexpected output (matrix not all green, DPU not
  reachable, …) so the report distinguishes "I couldn't run this"
  from "this ran and the answer was bad".
- `__main__.py` — orchestrator: arg parsing, step plan, overall
  timeout, report writing.

## Cloud-blueprint phase (S1)

Run `--steps cloud` to execute the forge blueprint deploy → BNK readiness →
license gate → guaranteed teardown vertical against a live cloud environment.
Without cloud credentials (or without a `cloud:` section in the config) every
step reports `skipped` (green) — safe in the default PR gate.

```bash
# Set AWS credentials + optional license JWT:
export AWS_ACCESS_KEY_ID=...
export AWS_SESSION_TOKEN=...
export CLOUD_LICENSE_JWT="eyJhbGciOiJSUzI1NiIsIn..."   # optional

# Copy the cloud config template from the example file and fill in your values:
cp scripts/e2e/e2e-config.example.yaml e2e-config-cloud.yaml
$EDITOR e2e-config-cloud.yaml   # fill in cloud: section

# Dry-run to preview the step plan (no execution, no credentials needed):
python -m scripts.e2e e2e-config-cloud.yaml --steps cloud --dry-run

# Live run:
BNK_FORGE_PASSWORD=admin123 python -m scripts.e2e e2e-config-cloud.yaml --steps cloud
```

Cloud steps in order:

| # | Step | What it does |
|---|---|---|
| 1 | `cloud_resolve_release` | Resolve blueprint release by id or name/version; assert valid + imported/approved |
| 2 | `cloud_create_project` | POST /api/stacks/releases/{id}/projects — create project from release |
| 3 | `cloud_deploy_all` | POST /api/projects/{id}/deploy-all — triggers deploy and stores `run_handle` |
| 4 | `cloud_wait_deploy_terminal` | Poll /api/projects/{id}/orchestration/{run_handle} until completed or failed |
| 5 | `cloud_wait_bnk_ready` | Poll /api/k8s/clusters/{id}/f5bnk/health until overall ≠ unknown/critical |
| 6 | `cloud_wait_license_active` | Poll /api/licensing/{id}/status; activate (POST /activate) if JWT configured |
| 7 | `cloud_destroy_all` | POST /api/projects/{id}/destroy-all + poll to completed |
| 8 | `cloud_delete_project` | DELETE stacks + project (force=true) |

**Teardown guarantee:** if the run fails, times out, or is interrupted
(Ctrl-C) after `cloud_create_project`, a `cloud_teardown_finally` step runs
automatically in the `finally` block and appears as its own row in the report.
A failed teardown yields `status=failed` — leaked cloud resources are always
visible.

## Future hooks

- **`--rca-on-failure`** — pipe the failure artifacts (logs, matrix
  payload, DPU OS exports) into Claude Code and append the agent's
  hypotheses to the report. Optional — the harness works fine without.
- **`--baseline`** — compare current run's per-step durations against
  the previous report on disk; fail if any step regressed > 20%.
