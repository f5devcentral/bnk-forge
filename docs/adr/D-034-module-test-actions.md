# D-034 — Module test actions (vendor-CLI e2e / scenario / bench tests via the pipeline)

- **Status:** Accepted
- **Tracking:** #454 (epic) · roadmap PR #455 · sibling #452 (cluster auto-registration)
- **Date:** 2026-07-18
- **Source:** Operator/owner request — "ocibnkctl offers not just the deployment of a cluster, but also e2e, scenarios and scaling tests. How could such features best be exposed via projects and pipeline?" Generalizes beyond ocibnkctl: tmmlitectl (bench performance tests) is queued for integration, and every future `*ctl` container artifact inherits the same contract.
- **Sibling ADRs:** D-033 (multi-version module catalog — the artifact/pack contract this extends). Related issues: #452 (pipeline-deployed cluster auto-registration), #442 (secret_files — precedent for extending the artifact manifest contract).
- **Class of problem it governs:** vendor CLI tools packaged as container artifacts have first-class *test* capabilities (functional scenarios, e2e verification, performance benchmarks) that today are reachable only by an operator running the tool by hand on a host — the pipeline can deploy the product but cannot exercise it.

## Context

All findings code-verified on `staging` + the ocibnkctl/tmmlitectl/bnkctl-index working trees (2026-07-18).

### 1. Container artifacts execute exactly four lifecycle phases; extra phases validate but never run

The artifact-step validator explicitly tolerates arbitrary extra step-set phases beyond `apply`/`destroy` ("Any other declared phases must still satisfy step rules", `backend/services/module_metadata.py:620-624`) — but nothing dispatches them: `container_engine._resolve_steps(ctx, op)` is only ever called with `init`/`plan`/`apply`/`destroy` (`backend/services/execution/container_engine.py:126,143,160,182`), and `tasks/container_tasks.py` defines only the four matching Celery tasks. A declared `test` phase is dead weight today. There is also no post-apply verification hook for the container engine (the SSH engine has `validate_commands`, `backend/modules/base.py:234`; containers have nothing).

### 2. Prior art A — the awsbnkctl use-case runner (tests as a companion lifecycle module)

`cli-bnkctl/awsbnkctl/bnk-demo-usecases` (`backend/services/cli_bnkctl_module_seeder.py:261`) exposes the vendor CLI's demo/scenario tests — including the AI test cases `ai-inference-e2e`, `ai-token-counting`, `ai-semantic-cache` — as a module that `depends_on` the cluster module and maps tests onto the deploy lifecycle: **plan → `--dry-run`, apply → `scenarios run`, destroy → `scenarios clean`** (`backend/services/execution/cli_engine.py:595,712,849`), with a `usecases` selector input. It works because the CLI engine special-cases workspace reuse (`backend/tasks/cli_tasks.py:250`). Lessons: the selector-input UX is right; overloading apply/destroy for "run tests"/"clean tests" is confusing next to a real cluster lifecycle, and a companion catalog module per tool doubles the catalog surface.

### 3. Prior art B — the Benchmarks domain (external load, deliberately outside pipelines)

The Benchmarks product (`backend/models/benchmark.py`, `backend/routes/benchmarks.py`, `backend/services/benchmark_scenarios.py`) runs aiperf LLM load tests from **remote load-generator agent machines** over WebSocket, with run-groups, result ingestion, and a compare UI. It is launched from its own page, not from projects/pipelines — correctly, because the load source is *external* to any deployment. Its scenario-preset registry and run-group shapes are reusable ideas, but its entities are proxy/LLM-specific.

**Launch-path rule this ADR adopts:** tests whose driver runs *inside the deployment's own containers* (tool-embedded) launch via the pipeline; tests whose load is generated *externally* (agent machines) stay in the Benchmarks domain. tmmlitectl's bench drives load from the deployment's own FRR container (`tmmlitectl internal/bench/helpers.go:45-52`) — tool-embedded, hence pipeline.

### 4. The ctl tools already share a de-facto test contract

ocibnkctl and tmmlitectl (siblings) both provide:
- `scenario list / run [name] / run --all / clean` — functional pass/fail suites, **green/amber/red rated** (red = untestable in the tool's shape and skipped), dependency-ordered (topo sort), per-scenario JSON reports, non-zero exit on any failure (ocibnkctl `internal/cli/scenario.go:44-59,226-228` — 15 scenarios; tmmlitectl `internal/scenarios/runner.go:23-24,90-115`).
- `e2e` — resumable full-pipeline driver with aggregate `run-<poc>-<stamp>.{json,md}` reports and per-phase logs; `--with-scenarios` appends the green scenarios (ocibnkctl `internal/cli/e2e.go:42-54,561-598`).
- tmmlitectl adds `bench list / run [name] / clean` — **metrics, not pass/fail** (Gbit/s, req/s, latency, TCP retransmits), JSON `Report` per run including host-contention warnings (`internal/bench/bench.go:24-39`, `internal/bench/hostload.go`); 5 registered benchmarks.
- Shared conventions: `[N/M]` phase markers on stdout, `poc.yaml` + `artifacts/` state, reports under `<poc>/reports/<UTC-timestamp>/{scenarios,bench}/`, exit-code pass/fail semantics.

The shipped ocibnkctl artifact (`bnkctl-index/tools/ocibnkctl/bnkforge.artifact.json`) exposes none of this: `apply` = `validate` + `e2e --yolo`, `entrypoints: {}` empty.

### 5. Live phase streaming already exists

The deploy-UX work (PR #450) parses `[N/M] <phase>` markers from runner output into `project_modules.stage_detail` and renders them live in the blueprint header and pipeline nodes. Test actions emitting the same markers get live progress for free.

### 6. Scaling is a lifecycle mutation, not a test

`ocibnkctl scale --tmm N` mutates the cluster and persists the new count into `poc.yaml` (`internal/cli/scale.go:62-170`) — it changes the deployment's declared state rather than exercising it. **Decision (owner-confirmed): scaling is modeled as editing the module's `tmm_nodes` variable and re-applying**, not as an action. This keeps the declarative contract honest: the module's variables always describe the deployed shape, and a re-apply is resumable/idempotent by the tools' own design.

## Decision

**The artifact manifest gains a declarative `actions` block; the container engine gains one generic action dispatcher; the UI exposes actions on post-apply modules with per-scenario selection. Results are logs + pass/fail status in v1 — no report-ingestion table yet.**

Deeper shape, in dependency order:

1. **Manifest contract.** `bnkforge.artifact.json` gains a top-level `actions` object: named entries, each `{ title, description?, steps: [...], inputs?: {...}, rating?: green|amber, timeout_seconds?, when? }`. Steps obey the exact existing artifact-step rules — argv-only invoking the artifact's own image, denylist (no shell/script/image/entrypoint override, `module_metadata.py:128,139`), `{{inputs.*}}` templating, per-step timeouts. Actions run in the module's **existing workspace** (same `state.mount_path`/scope resolution as lifecycle steps) — they see the `poc.yaml`, kubeconfig, and state the deploy produced by construction, which is why actions beat companion modules (a companion would need `state.scope: deployment` workspace-sharing and a second catalog module per tool). Validation lives beside `_validate_artifact_steps`; `_parse_pack_module` merges `actions` into the stored pack manifest (its merge list is explicit — the #442 lesson).
2. **Execution.** One new Celery task `run_container_action(task_id, module_id, action, inputs)` mirroring `run_container_apply`'s structure (module lock, Task record, deployment-style run record, `stage_detail` streaming, log capture); one route `POST /api/projects/{project_id}/modules/{module_id}/actions/{action}` (operator role, `response_model=`, `@handle_route_errors`). **Status gate:** actions dispatch only when the module is in a post-apply state (`applied`/equivalent) — a test against an absent cluster fails fast with an actionable error. Exit code → `succeeded`/`failed` on the run record; the tools' exit semantics are already correct for this.
3. **Selection UX (owner-confirmed).** Both granularities: per-scenario runs and a "run all green" preset. An action may declare an `inputs.scenario` enum; the pack author enumerates scenario names + ratings in the manifest (static list, versioned with the pack — consistent with D-033's immutable-version model; a `list --json` action can refresh authorship, not runtime). **Amber-rated scenarios are runnable with an explicit warning** (badge + confirm text naming why amber, e.g. "needs AI model resources"); red-rated are not offered.
4. **Results (owner-confirmed).** v1 records logs + status only: the action's Task/run record with captured output, live `stage_detail`, and pass/fail. The tools' JSON reports remain in the workspace (retrievable via existing workspace tooling). A structured `action_runs` ingestion table (per-scenario detail, bench metric tables, run-group aggregation for bench sweeps) is an explicitly deferred follow-up — the report-directory contract in Context §4 is the ingestion surface when wanted.
5. **Launch-path boundary.** Tool-embedded tests (ocibnkctl scenarios/e2e-verify, tmmlitectl scenarios/bench, awsbnkctl use-cases if/when containerized) launch via module actions in the pipeline. The Benchmarks domain remains the sole launch surface for externally-generated load (aiperf agents). No coupling in v1; an optional future bridge may ingest ctl bench reports into a comparable view, but never the reverse.
6. **Follow-up phases (out of v1 scope, recorded):** (a) blueprint DAG support for an auto-run action node post-deploy — the container analog of SSH `validate_commands` ("deploy, then verify e2e green"); (b) an MCP `run_module_action` tool beside `iac_operations`; (c) the `action_runs` ingestion table + bench sweep aggregation.

### Scope boundaries

- Container artifacts only (`container_image` kind). The CLI engine's use-case runner keeps its current shape; migrating awsbnkctl to a container artifact with actions is a separate decision.
- Suggested slicing: **PR-1** manifest validation + dispatcher task + route (carries the regression-test weight); **PR-2** UI (Actions menu on module rows, scenario picker, amber warning, live phase); **PR-3** bnkctl-index pack updates (ocibnkctl `actions` block; tmmlitectl pack is net-new and includes its actions from day one) + authoring-guide section.

## Consequences

- Every containerized ctl tool exposes its test surface through one declarative block — no per-tool backend code, no companion modules doubling the catalog.
- The pipeline can finally answer "deploy succeeded, but does it work?" — and later auto-answer it via the blueprint action node.
- Actions share the lifecycle steps' security posture (argv-only, denylist, no worker env, allowlisted credentials only) — no new attack surface class; the #443 hardening applies unchanged.
- Scenario lists are versioned with the pack: a new tool release with new scenarios is a new immutable module version (D-033), so "which scenarios existed at deploy time" is always answerable.
- Bench actions produce logs + status only in v1; operators doing per-core scaling sweeps read the workspace JSON reports until the ingestion follow-up lands.
- `scale` deliberately gets no action: operators change `tmm_nodes` and re-apply. If re-apply latency ever matters, the tools' own resumable e2e makes targeted re-apply cheap.

## Interim workaround (zero code)

Run the tool's test commands by hand against the pipeline-deployed cluster: the workspace volume holds the `poc` directory, so `docker run` with the runner image, the workspace mount, and e.g. `ocibnkctl scenario run tcpl4lb --poc /state/<poc>` works today — exactly what the actions block automates.

## References

- `backend/services/module_metadata.py:113-139,533,592-647` (artifact kinds, step rules, denylist)
- `backend/services/execution/container_engine.py:126-182,470-541`; `backend/tasks/container_tasks.py`
- `backend/services/cli_bnkctl_module_seeder.py:44,261-281`; `backend/services/execution/cli_engine.py:78-110,113,595,712,849`
- `backend/services/benchmark_scenarios.py:276,397`; `backend/routes/benchmarks.py:1111,1139`
- ocibnkctl: `internal/cli/{e2e.go,scenario.go,scale.go}`; tmmlitectl: `internal/cli/{bench.go,scenario.go,e2e.go}`, `internal/bench/`
- `bnkctl-index/tools/ocibnkctl/{bnkforge.pack.json,bnkforge.artifact.json}`
- Issues: #452 (auto-registration), #442 (secret_files); PR #450 (live phase streaming)
