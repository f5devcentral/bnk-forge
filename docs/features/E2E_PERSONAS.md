# BNK Forge — E2E Testing Personas & Workflows

> **Purpose.** Six personas that drive end-to-end testing of BNK Forge. Each owns one
> stage of the Day‑0 → Day‑1 → Day‑2 lifecycle (one of them isn't human — it's an AI agent
> driving Forge through MCP/API). Each has a **golden path** (the happy-path smoke test) and
> **rough-edge probes** (negative/variation paths that surface the bugs and rough edges real
> users hit). Use these as the spec for an always-on regression gate.
>
> **Real humans don't move in a straight line.** Golden paths here are *not* a single forward
> march — they include the things people actually do mid-flow: hit **Cancel**, **refresh** the
> page, click browser **Back/Forward**, **navigate away and come back**, open a **second tab**,
> **bookmark/deep-link** a screen, double-click a button, or let a session **idle out**. Those
> interruptions are where state-loss, stale-data, and orphaned-operation bugs live. Every persona's
> golden path weaves in the most relevant ones, and the shared
> [**Cross-cutting interaction probes**](#cross-cutting-real-human-interaction-probes) below apply
> to *all* personas.
>
> Human-readable version: [`E2E_PERSONAS.html`](./E2E_PERSONAS.html)
> Grounded in: `docs/bnk-forge-features.csv` (461 features), the real app nav (`frontend-v2/src/router.tsx`),
> and the existing harness (`scripts/e2e/`).

---

## Why these personas

The app spans a full **Day‑0 → Day‑1 → Day‑2** lifecycle for F5 BIG‑IP Next for Kubernetes (BNK).
The friction that real users feel clusters around the **handoffs between roles** — the infra person
(cloud *or* on-prem) hands a running cluster to the network person, who hands traffic config to ops,
etc. Each persona owns one stage, and the **seams between them** are where bugs hide. One persona
isn't human at all — an **AI agent** driving Forge through MCP/API on allocated tasks — because that
is now a first-class way the product is consumed.

Known weak spots from the feature audit (23 `Blocked`, 2 `Error`, 1 `Partial`) are tagged against the
persona that trips on them, so the negative-path tests target real gaps rather than imagined ones.

| # | Persona | Stage | Primary surfaces |
|---|---------|-------|------------------|
| 1 | **Priya (cloud) + Raj (on-prem)** — Infrastructure Builders | Day‑0/Day‑1 build | Auth/SSH Templates, Cloud Credentials, Catalog, Stacks, Projects, Orchestration, Licensing · DPU/BlueField, Bare-Metal Hosts, SSH engine |
| 2 | **Marcus** — NetOps / Application Delivery Engineer | Day‑2 traffic | F5 BNK, CNF, Discovery, Proxy/CIS migration, Connectivity |
| 3 | **Sofia** — SRE / Platform Operations (on-call) | Day‑2 operate | Dashboard, Fleet, Operators, Alerts, Drift, BNK Upgrade, Tasks, Snapshots/Backup/QKView |
| 4 | **Dev/QA** — Performance & Validation Engineer | validate | Benchmarks (Targets/Agents/Configs/Runs/Compare) |
| 5 | **Aisha** — Platform Admin / Team Lead | govern | Users, Credential Templates, Module Sources/Registry, Config Export/Promotion, Audit, MCP |
| 6 | **Atlas** — Autonomous AI Agent (MCP/API) | drive (headless) | MCP server (150 tools), REST API, `mcp` service account — spans every surface, no UI |

---

## 1. Priya & Raj — Infrastructure Builders  *(Day‑0/Day‑1)*

**One role, two environments.** Priya stands infra up in the **cloud**; Raj stands it up **on-prem**
(DPU / bare-metal). Same job-to-be-done, same handoff: both produce a **running, licensed BNK cluster
that they then hand off** to the Day‑2 personas (Marcus for traffic, Sofia for operations). Testing them
together keeps the cloud (Terraform/OpenTofu) engine path and the on-prem (SSH) engine path honest
*against the same downstream contract* — whatever they hand off must look the same to operate against.

**Goal / JTBD.** *"Give me a working, licensed BNK cluster — from a blueprint in my cloud, or over SSH
on my own hardware — without hand-wiring it, and hand it off clean."*

### 1a. Priya — Cloud Infrastructure Engineer
**Profile.** Owns the cloud account and the "stand it up from nothing" job. Lives in AWS/Azure/GCP
consoles, comfortable with Terraform, impatient with anything that hangs without telling her why.
First-time-user energy: evaluating whether Forge is faster than her own scripts.
**Environment.** Fresh AWS account (SSO), no clusters yet.

**Golden path** *(primary smoke test — if this breaks, nothing else matters)*
1. Login → **Auth Templates**: create a cloud Credential Template (AWS), mark default.
2. **Cloud Credentials**: SSO device-code login → acquire temp creds → verify account/region appear.
3. **Catalog → Blueprints** (or **Stacks**): pick an EKS+BNK blueprint, read its description/module list.
4. **Projects → Create**: instantiate the blueprint → land on Project Detail.
5. **Variables/Wiring**: fill required vars (region, cluster name, secrets with `target_variable_name`); resolve any `missing:` modules.
6. **Deploy-all**: watch the **Orchestration** event-chain advance module→module (eks-create → cneinstall → ready-gate → license-gate).
7. **Licensing**: confirm License CR activates and BNK reaches **Active** (all modules).
8. **Handoff + Teardown**: hand the running cluster to Marcus/Sofia; later destroy → **orphan-free** (reverse-DAG destroy).

**Rough-edge probes**
- Deploy with a **deliberately missing required variable** → UI blocks clearly, or fails mid-chain?
- **AWS creds expire mid-deploy** → friendly 401 + stale-badge clears, not a 500 wall. *(regression-guard a prior fix)*
- A module **fails then retries** → does the chain keep auto-advancing? *(D‑031 #320 chain-advance defect)*
- **AKS/GKE** instead of EKS → token injection only wired for EKS/ROKS (#218); test documents the gap.
- Destroy that **halts at FLO on a stale token** → force-destroy / fail-soft path works.

**Interaction (non-linear) — the deploy is long, so she won't just sit and watch**
- **Refresh the browser mid-deploy** → Orchestration progress resumes live, not lost / not stuck "pending".
- **Navigate away to Dashboard and back** mid-deploy → progress still there and accurate.
- **Cancel a deploy in flight** → it actually stops and leaves no orphaned cloud resources / no half-locked modules.
- **Cancel the Create-Project wizard halfway** → no partial project / no orphaned vars written.
- **Browser Back out of the variable form** → re-entry preserves entered values (or warns), doesn't silently drop them.
- **Double-click Deploy-all** → exactly one deploy starts, not two (idempotent submit).
- **Deep-link / refresh on `/projects/:id/:section`** → loads directly (no SPA 404), not only reachable by clicking through.

### 1b. Raj — On-Prem / DPU & Bare-Metal Engineer
**Profile.** Owns physical hosts and BlueField DPUs in his own data center. No cloud console — he works
over SSH and PXE/BFB images. DPU is the **largest single surface (33 features)** and the least-mature;
it exercises the **SSH engine** rather than the cloud/Terraform engine.
**Environment.** Bare-metal host(s) + BlueField DPU, reachable via an SSH jumphost. No cluster yet.

**Golden path**
1. **SSH Credentials**: register jumphost + key (auto key-bootstrap), **Test connection**, probe kubeconfig over SSH.
2. **Infrastructure → Bare-Metal Hosts**: register a host; **BlueField Images**: upload/select a BFB image.
3. **Catalog → Blueprints**: pick the SSH/DPU BNK blueprint; resolve bare-metal version profile.
4. **Projects → Create + Deploy**: SSH-deploy the BNK layer (scaffolding → manifest/helm ports → cneinstance → VLANs/gatewayclass).
5. **Licensing**: BNK reaches **Active** on the DPU.
6. **Handoff + Destroy**: hand off to Day‑2; destroy cleanly (e2e on dpu-server).

**Rough-edge probes**
- **SSH connection test / key bootstrap** fails on wrong port/host → clear error (these are `Blocked` audit items — prime targets).
- **Probe kubeconfig via SSH** against a host with no cluster → graceful, not a hang.
- SSH command mid-deploy **drops the connection** → retried/resumed, not silently stuck.
- The **handoff contract matches Priya's** — a cluster Sofia operates looks the same whether built in cloud or on-prem.

**Interaction (non-linear)**
- **Cancel a Test-connection / BFB upload** in progress → cancels cleanly, no stuck spinner, no half-uploaded image.
- **Refresh during the SSH deploy** → step progress resumes, doesn't restart from zero.
- **Cancel an SSH deploy mid-step** → remote host left in a known/recoverable state, not a wedged half-install.

### Known weak spots this role hits
Cloud Credentials SSO/IAM (`Blocked`); **Execution: Deploy Module (full chain)** and **OpenTofu Apply** (the two `Error` features); all 7 **Licensing** (`Blocked`); **SSH** test/bootstrap/command/probe (all `Blocked`); SSH-BNK-layer module ports in progress (#204/#205/#210).

---

## 2. Marcus — NetOps / Application Delivery Engineer  *(Day‑2 traffic)*

**Profile.** Owns north-south traffic. Knows F5 classic (TMOS/AS3), VIPs, iRules, CIS. He inherits
Priya's running cluster and has to make it actually serve and route traffic — and migrate existing
F5 estate onto BNK.

**Environment.** A licensed BNK cluster + an existing classic BIG-IP and/or CIS-managed Ingress.

**Goal / JTBD.** *"Configure CNFs/gateways on BNK, and migrate my existing proxies/CIS config onto it without rebuilding by hand."*

### Golden path
1. **F5 BNK** page: view the licensed instance, health, version.
2. **CNF** (CRD dashboard): browse live CRs, inspect a GatewayClass / VLAN / listener.
3. **Discovery**: point at an existing classic BIG-IP / CIS deployment → inventory VIPs, AS3, Ingress.
4. **Migrate (D‑021 / D‑023)**: classify discovered objects (migratable vs BNK-native vs unsupported) → generate BNK CRs → review diff.
5. **Apply** config to BNK → CRs reconcile and traffic path comes up.
6. **Connectivity / reachability**: verify the data-plane end-to-end.

### Rough-edge probes
- CRD write path ("CRD Wizard", D‑018 P3) is **Blocked on authz/audit** — a write should be gated, not silently dropped.
- CIS **IngressClass kind-aware classify** (#295) — feed edge cases (IngressLink, OpenShift Route, ConfigMap AS3); each lands in the right bucket.
- Discovery against an **unreachable / wrong-cred** device → clear error vs hang.
- Migrate an **unsupported object type** → warns, doesn't emit a broken CR.

### Interaction (non-linear)
- **Refresh the CNF/CRD dashboard** → live CR list reloads from the cluster, doesn't show a stale snapshot.
- **Cancel a discovery scan** in flight → stops, partial results discarded or clearly marked partial.
- **Back button out of the migration review/diff** → returns to classification without losing the generated plan.
- **Change screens (BNK → CNF → Discovery) and back** → each view re-orients to current state, no leftover selection from the prior screen.

### Known weak spots
CNF write path (`Blocked`); AS3/CIS coverage edges; SSH Connection/Command (`Blocked`) when reaching on-prem devices.

---

## 3. Sofia — SRE / Platform Operations  *(Day‑2 keep-it-running, on-call)*

**Profile.** Owns the fleet's health at 3am. Doesn't deploy new things; she watches, upgrades, and
recovers. She judges the product by how fast it tells her *what's wrong and what to do*.

**Environment.** Several running projects/clusters across clouds.

**Goal / JTBD.** *"Tell me what's drifting or unhealthy, let me upgrade safely, and let me recover a failed module without nuking the cluster."*

### Golden path
1. **Dashboard / Command Center**: scan fleet health, recent deploys, alerts.
2. **Fleet**: drill into a cluster + its **Operators** tab → health.
3. **Alerts**: configure a Slack/webhook channel → **Test** it → confirm delivery + history.
4. **Drift**: detect config drift on a project → review → reconcile.
5. **BNK Upgrade**: plan an upgrade (registry-driven GA resolution) → run → handle supersede/cancel.
6. **Tasks / Orchestration**: open a failed task → read logs → retry just that module.
7. **Snapshots / Backup / QKView**: snapshot before upgrade; pull a QKView for support.

### Rough-edge probes
- Upgrade with an **undecodable FLO secret** → graceful handling (#284 hardening), not a crash.
- Two upgrades on the same entity → **supersede / 409** behaves.
- **Alembic schema drift** at deploy (#161, recurring crash) → fails loud, doesn't silently corrupt.
- Alert channel with a **dead webhook URL** → failure recorded in history, rate-limited, not retried into oblivion.
- Notifications stay **bell-only / zero-toast** (D‑027) — no toast regressions.
- Retry a mid-chain failure → **event-chain resumes** (ties to Priya's #320 probe).

### Interaction (non-linear) — she's on-call, jumping between screens fast
- **Cancel an upgrade in flight** → stops safely, supersede/cancel UX is honest about final state (#284).
- **Refresh during drift reconcile** → progress preserved, doesn't double-apply.
- **Navigate away from a running task and back** → live logs/status reconnect, not frozen.
- **Two tabs open on the same project** (or same alert channel) → edits don't clobber; last-write conflict surfaces.
- **Bell notification deep-links** to the right entity even after a refresh / on a fresh session.
- **Session idles out** while she's reading the dashboard → next action re-auths gracefully (no data loss, no white screen).

### Known weak spots
Licensing renew/switch (`Blocked`); QKView; drift reconcile.

---

## 4. Dev/QA — Performance & Validation Engineer  *(prove it's fast / prove it works)*

**Profile.** Runs load against the deployed BNK to validate SLOs and compare releases. Cares about
reproducibility and clean comparisons.

**Environment.** A running BNK target + one or more benchmark agent hosts (built-in or remote-provisioned).

**Goal / JTBD.** *"Register a target and an agent, run a scenario, and compare two runs to see if a release regressed."*

### Golden path
1. **Benchmarks → Targets**: register a BNK target.
2. **Benchmarks → Agents**: use built-in agent, or register + SSH-provision a remote agent host.
3. **Configs**: create/select a scenario config.
4. **Runs**: launch a run → watch live progress → run completes + persists.
5. **Run Detail / Group View**: inspect results.
6. **Compare**: diff two runs / a run group → spot regression.

### Rough-edge probes
- **Scenario override guard** (#282 security fix) — an injected override that should be rejected, is.
- Remote agent **auth flag** (#286 Slice‑4) — unauthenticated agent call refused.
- Agent host that **goes away mid-run** → run marked failed cleanly, not stuck "running" forever.
- Compare runs with **mismatched configs** → warns, doesn't silently mislead.

### Interaction (non-linear)
- **Stop/cancel a run in progress** → stops cleanly, partial results saved-or-discarded clearly (no zombie "running").
- **Refresh during a live run** → progress + live metrics resume, don't reset.
- **Navigate away and back** mid-run → reconnects to the same run, doesn't spawn a duplicate.
- **Back button out of the New-Run wizard** → entered config preserved or clearly discarded.

### Known weak spots
Performance Testing edge; remote agent provisioning over SSH (SSH `Blocked` items).

---

## 5. Aisha — Platform Admin / Team Lead  *(governance, multi-env, reporting)*

**Profile.** Owns *who can do what* and *how config moves between environments*. Manager-adjacent:
also wants a clean read-only story for reporting up. Security- and audit-minded.

**Environment.** A team of operators/viewers, a dev and a prod Forge, shared module sources / a private registry.

**Goal / JTBD.** *"Manage users and roles, govern where modules come from, promote config dev→prod, and prove who did what."*

### Golden path
1. **Users**: create operator + viewer accounts (role validation, `must_change_password`); confirm RBAC scoping.
2. **Credential Templates**: central provider creds; mark defaults; **Test** template.
3. **Module Sources / Registry / Git VCS**: register a Git module source → sync → modules appear in Catalog.
4. **Config Export / Promotion**: export a project's config from dev → import/promote to prod.
5. **Audit**: filter/search the audit log (by user/action/resource) → export stats.
6. **MCP**: confirm the MCP service account exposes fleet state to Claude/clients (non-human account, survives admin rotation).

### Rough-edge probes
- **Viewer tries a write** → forbidden, audited (RBAC enforcement is the test).
- **Self-demotion / self-delete** by an admin → blocked (FEAT‑0016 / FEAT‑0018).
- Audit **search hits the `details` column** (FEAT‑0014 was a real bug; WO‑4 #357 fix) — regression-guard it.
- **Test Credential Template** → currently `Blocked`; document expected behavior.
- Promote config where target env is **missing a referenced secret/module** → clear failure, not a half-applied state.
- **GitLab token metadata normalization** (the one `Partial` feature) — feed a GitLab PAT and check normalization.

### Interaction (non-linear)
- **Cancel the Create-User dialog** → no half-created user; **Back** out of an edit → no partial role change.
- **Cancel a module-source sync** in flight → stops, Catalog not left half-populated.
- **Refresh the audit log** with filters applied → filters + page persist (or are in the URL), not reset to defaults.
- **Paginate / change audit filters and Back** → returns to the prior result set, not page 1.
- **Two admins editing the same user** in two tabs → conflict surfaces, not silent last-write-wins.

### Known weak spots
Test Credential Template + SSO refresh for template (`Blocked`); config-promotion gaps.

---

## 6. Atlas — Autonomous AI Agent  *(MCP / API, headless)*

**Profile.** Not a human. An LLM agent (Claude over MCP, or a CI/automation bot over REST) that gets
handed a **scoped task** — *"stand up a dev cluster," "check fleet health and remediate drift," "run
the nightly benchmark and report"* — and drives Forge to completion through tools, with no UI. It
judges the product by whether **tool contracts are truthful, errors are machine-recoverable, and risky
operations are gated** so it can't do damage by accident. This is now a first-class consumption path,
so it gets first-class e2e coverage.

**Environment.** The **MCP server — 150 catalogued tools** (`iac_operations` 42, `cluster_management`
36, `bnk_operations` 19, `diagnostics_fleet` 19, `cloud_auth` 13, `helm` 11, `system` 6,
`config_management` 4), risk-classed **read_only 81 / mutating 53 / destructive 16**, each with an
`auth_expectation`, a `tier`/`stability`, and a truthful output contract — plus the REST API and the
dedicated non-human **`mcp` service account** (PR #214, survives admin rotation).

**Goal / JTBD.** *"Hand me a scoped task and let me drive Forge to completion through tools — with
truthful results, recoverable errors, and guardrails on destructive actions."*

### Golden path
1. **Authenticate** as the `mcp` service account (non-human; survives admin password rotation).
2. **Discover** the tool catalog → tool names, risk classes, `auth_expectation`s.
3. **Orient** with read-only tools (`system_health`, `fleet_health`, list clusters/projects).
4. **Execute the allocated task** via mutating tools end-to-end (e.g. `create_project` → deploy → poll `/orchestration/{run_handle}`).
5. **Parse normalized envelopes** — nested `project`/`cluster` objects; success derived from **outcome, not HTTP 2xx** (D‑017).
6. **On failure**, consume the **structured error envelope** (`failure_class`) → retry / recover / escalate.
7. **Report / hand off** the result to the human or the next agent.

### Rough-edge probes  *(the bug-surfacing gold for agents)*
- **Destructive-tool guardrail** — a `destructive` tool (e.g. `delete_cluster`; 16 exist) requires explicit confirmation / elevated auth; the agent can't nuke by accident.
- **`auth_expectation` enforced** — calling a `cluster_owner` tool as a non-owner is refused, not silently executed.
- **Truthful output contract** — every tool's envelope shape matches the catalog (the `test_tool_output_contracts` guarantee); no field drift that breaks parsing.
- **Structured error envelope** — failures return a machine-parseable `failure_class`, not a raw 500 / HTML page.
- **Outcome-derived success** (D‑017) — agent never false-reports success on a 2xx that didn't actually license/deploy.
- **Admin rotation doesn't break MCP auth** (PR #214) — a long-running agent survives credential rotation.
- **Deprecated → `replacement_tool`** pointer honored — agent isn't stranded on a dead tool.
- **Long-poll orchestration** — agent polls `/orchestration/{run_handle}` (real int id — *not* the dead endpoint from the D‑031 lesson) to a terminal state without hanging.
- **Idempotency / double-submit** — a retried `create` yields 409 / supersede, not a duplicate (module locking Phase 1.6).

### Interaction (non-linear / programmatic) — the agent's version of "cancel / refresh / come back"
- **Cancel a long operation via API/tool** mid-flight → it stops and reports a clean terminal state (the agent's "Cancel button").
- **Resume after disconnect** — agent drops and reconnects, then re-polls `/orchestration/{run_handle}` and picks up the *same* run (the agent's "refresh / navigate back").
- **Two agents (or agent + human in the UI)** act on the same project concurrently → locking arbitrates; the loser gets a clear conflict, not corruption.
- **Token expires mid-task** → tool call returns a structured auth error the agent can recover from (re-auth), not an opaque hang.

### Known weak spots it hits
`cloud_auth` tools (13) lean on the SSO/IAM `Blocked` items; destructive-tool confirmation semantics; any tool whose envelope drifts from the catalog; MCP write path that overlaps the `Blocked` CRD-write authz work.

---

## Cross-cutting: real-human interaction probes

These apply to **every persona** (and, in their programmatic form, to Atlas). Real users interrupt,
back out, refresh, and multitask — so each golden path should be run with these layered on, not just
as a clean forward march. Treat this as a reusable checklist the per-persona tests inherit.

### Interruption & abort
- [ ] **Cancel** any long operation (deploy, upgrade, discovery, benchmark, sync, upload) → it actually stops, reports an honest terminal state, and leaves **no orphaned resources / no half-locked modules**.
- [ ] **Cancel a dialog/wizard** half-filled → **no partial writes** (no orphan project/user/channel).
- [ ] **Double-click / rapid double-submit** a primary action → exactly **one** operation runs (idempotent).
- [ ] **Stop/abort then immediately retry** → the retry works and doesn't collide with the aborted one.

### Navigation (it's never one-way)
- [ ] **Browser refresh (F5)** on any screen — including mid-operation — recovers live state, doesn't reset to "pending"/blank or show stale data.
- [ ] **Refresh on a deep route** (`/projects/:id/:section`, `/catalog?tab=…`) → loads directly, **no SPA 404**.
- [ ] **Browser Back / Forward** through a flow → preserves entered data (or warns), never corrupts state.
- [ ] **Navigate away mid-operation and return** → progress/logs reconnect and are accurate.
- [ ] **Change screens repeatedly** (tab to tab, section to section) → each view re-orients to current state; no leftover selection or stale filter bleeds across.
- [ ] **Bookmark / deep-link / share a URL** → opens to the right place for the next user/session.

### Concurrency & sessions
- [ ] **Two tabs / two users on the same entity** → conflict surfaces (lock or warning), not silent last-write-wins.
- [ ] **Session idle / token expiry** mid-flow → graceful re-auth, **no data loss, no white screen** (ties to the AWS cred-expiry friendly-401 work).
- [ ] **Admin credential rotation** while others are active → live sessions/MCP agents survive or re-auth cleanly (PR #214).

### State rendering (the three states every screen owes you)
- [ ] **Empty state** (first load, no data) renders intentionally — not a blank or a spinner forever.
- [ ] **Loading state** on a slow fetch → skeleton/spinner, not a flash of "nothing here".
- [ ] **Error state** on a failed fetch → shared `ErrorState` with a retry, not a raw 500/stack trace (D‑025/D‑027 + the 6 k8s pages routed through `ErrorState`).
- [ ] **Stale-after-action** → after a mutation the list/detail reflects it without a manual hard refresh.

> **Test-design note.** The cheapest way to get coverage is to bake these into the harness as *decorators*
> over each persona's golden path (run-step → refresh → assert state; run-step → cancel → assert clean),
> rather than writing them out longhand per persona. Many are pure Tier‑1 (no cloud spend).

---

## How to operationalize this for e2e

### Priority order for a fast regression gate  *(cheapest signal first)*
1. **Atlas MCP contract tests** — tool-output contracts + risk/auth guardrails against a seeded Forge. Cheapest signal, no cloud, and they guard the agent path *and* the API every other persona rides on.
2. **Priya golden path** (deploy → license → Active → teardown) — the keystone; harness exists in `scripts/e2e/` + the D‑031 reliability harness.
3. **Sofia** alerts + drift + retry-resume — pure Day‑2, no cloud spend for the UI/API layer.
4. **Aisha** RBAC + audit + module-source sync — fully mockable, no cloud; catches governance regressions cheaply.
5. **Marcus** discovery/migrate — needs a target device or fixture.
6. **Raj** on-prem/DPU build — needs an SSH-reachable host (dpu-server).
7. **Dev/QA** benchmarks — needs a live target.

### Two-tier design  *(so it stays a "quick check")*
- **Tier 1 (no real spend):** Atlas MCP/API contract + guardrail tests, plus Sofia & Aisha e2e, entirely against a seeded Forge — runs on every PR.
- **Tier 2 (real cloud / real hardware):** Priya (live AWS) + Raj (SSH host) + Marcus/Dev‑QA against real targets, nightly / pre-release. Matches the existing `.github/workflows/e2e-tests.yml` chassis.

Each **rough-edge probe is a negative-path test** — those surface bugs and rough edges more than the golden paths do.

---

*Last updated: 2026-06-28. Maintained alongside `docs/bnk-forge-features.csv`. Regenerate the HTML view after edits.*
