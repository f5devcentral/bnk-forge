# D-031 — Cross-cloud e2e reliability harness (forge blueprint deploy → traffic → teardown)

- **Status:** Accepted
- **Date:** 2026-06-13
- **Builds on:** D-029 (EKS+BNK blueprint e2e reliability), D-030 (per-cloud blueprint catalogs)
- **Related:** #309 (D-029 slices, incl. P6 traffic gate), #311 (D-030)

## North Star
forge + blueprints must *repeatably and automatically*: deploy EKS+BNK from a blueprint **via the forge
REST API** → poll the readiness + license gates to truly-ready/Active → run **test traffic** → assert
success → **tear down cleanly** (cloud-cost discipline). The same harness must then generalize to **GKE
and Azure**. The purpose is to drive "it doesn't work" user complaints on the forge blueprint deploy path
to **zero** — every gate (D-029 P1/P2 readiness+license, P0/M2 catalog integrity) is a *means*; this
continuously-runnable up→traffic→down loop is the *acceptance criterion*.

## Context
- The sibling Go CLI `awsbnkctl` already performs AWS e2e standup + 3 TMM data-plane traffic patterns and
  is the working gold standard. The gap is that **forge's own** blueprint path is not proven end-to-end.
- Two e2e assets already exist in-repo and must not be forked:
  - `tests/e2e/` — Playwright UI suite (non-destructive against shared envs; one UI-driven Tier-2 AWS
    deploy spec). Owns the **UI** regression path.
  - `scripts/e2e/` — a Python, API-driven harness with a high-quality **chassis** (orchestrator with
    overall-deadline + always-write-reports, `StepRecorder`/`StepResult` ok/warn/fail/skip model, Pydantic
    config, ret- rying REST client, `--steps`/phase aliases, `--dry-run`, JSON/MD/PDF reporting). Its
    *steps* are entirely bare-metal DPU/BFB-flashing — **zero overlap** with cloud/blueprint/traffic.

## Decision
1. **Driver = forge REST API** (not the CLI, not `awsbnkctl`). The whole point is to prove the path real
   users hit. `awsbnkctl` is reused only as the **port source for the traffic mechanism**.
2. **Extend `scripts/e2e/` by adding a new cloud-blueprint *vertical*** that reuses the chassis but not the
   DPU steps. Same package, same `python -m scripts.e2e` entry point, same report pipeline, registered as a
   new `cloud` phase alias. No fork; no edits to DPU `steps.py`.
3. **The harness owns the destructive/API/cross-cloud loop; Playwright keeps the UI path.** Do not duplicate.
4. **Cloud-gated by credential presence** — without cloud creds (and `aws`/`ssh` on PATH for traffic) the
   `cloud` phase reports `skipped` (green), so it is safe in the default PR gate; real runs are
   scheduled/manual with secrets.
5. **Teardown is guaranteed in a `finally`** — any run that created a project tears it down even on
   failure/timeout/Ctrl-C; a failed teardown is a loud `fail` (leaked cloud resources), never silent.
6. **Cross-cloud has only two seams:** (a) which blueprint *release* + its `cloud_provider`/`region`/
   `credential_template_id`/`variables` (pure config; the harness reads `GET /releases/{id}/required-inputs`
   rather than hard-coding inputs — so D-030 M3 AKS / M4 GKE catalogs work with zero harness change); and
   (b) the traffic ingress/tunnel mechanism (AWS EICE+ENI now; GCP IAP / Azure Bastion are stubbed `skip`
   until those catalogs land).

## Deploy lifecycle (forge API the harness drives)
`login` → resolve/validate release (`/api/blueprint-catalog/releases`) → `GET
/api/stacks/releases/{id}/required-inputs` → `POST /api/stacks/releases/{id}/projects`
(cloud_provider+region+vars) → `POST /api/projects/{pid}/deploy-all` → poll `GET
/api/projects/{pid}/orchestration/{run_handle}` to `completed` → poll
`/api/k8s/clusters/{cid}/f5bnk/health` ready **and** `/api/licensing/{cid}/status` Active (activate if
needed) → **traffic** → assert → `POST /api/projects/{pid}/destroy-all` → poll to `completed` → delete.

Reaching `run_handle=completed` is explicitly **not** "ready" — the readiness + license gates are the
D-029 false-positive killers and are mandatory.

## Traffic (ported from `awsbnkctl`, shelling to `aws`/`ssh`, not reimplemented in boto3)
Three TMM HTTP/Gateway-API patterns: `http-routing-e2e` (single VIP/backend, 5/5 HTTP 200),
`http-traffic-split` (70/30, both backend markers seen), `multi-vip` (2 VIPs from one pool range, per-VIP
marker). Mechanism: mint ephemeral key → `aws ec2-instance-connect send-ssh-public-key` → EICE tunnel →
`curl --interface <jumphost BNK_EXT ENI IP> -H 'Host: …' http://<VIP>/`.

## Slices (tracer-bullet; each independently shippable)
- **S1** — blueprint deploy → poll deploy-terminal → readiness gate → license gate → guaranteed teardown
  (NO traffic), cloud-gated. *Buildable/testable now without a cloud* except the live AWS apply.
- **S2** — port the 3 TMM traffic patterns + traffic-assert gate (curl builders/parsers + success criteria
  unit-testable now; live tunnel needs AWS).
- **S3** — cross-cloud config parameterization + `TunnelProvider` seam (gcp/azure skip-stubs); ties to
  D-030 M3/M4.
- **S4** — CI/scheduled run (Tier-2 analog) with report upload + post-run SDK resource-gone sweep.

## Consequences
- One repeatable, cost-disciplined, forge-API-proven up→traffic→down loop becomes the standing acceptance
  gate for cloud blueprint reliability, reused unchanged as new clouds are added via D-030 catalogs.
- New modules: `scripts/e2e/cloud_steps.py`, `scripts/e2e/traffic.py`; additive cloud methods in
  `client.py`; additive config in `config.py`; one surgical `__main__.py` change (finally-teardown).
- Anti-scope: no new harness/repo; no edits to DPU steps; no Playwright duplication; no boto3 EICE rewrite;
  no hand-derived blueprint inputs; never treat `completed` as ready; never leak cloud resources.
