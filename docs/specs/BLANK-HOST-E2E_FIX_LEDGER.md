# Blank-Host E2E Fix Ledger (Worktree A)

Last updated: 2026-04-10
Scope: Forge host `10.176.11.91` deploying to blank target `ubuntu@10.176.11.143`

## Goal

Record **all fixes used so far** to reach current blank-host flow behavior, with explicit durability status:

- **Persisted (code/image/config)**: survives redeploy/restart
- **Ephemeral (manual runtime/server tweak)**: does not reliably survive rebuild/reprovision

This ledger is the pre-destroy reference for your manual re-apply test.

---

## A) Persisted fixes (in repo code)

These are in tracked source and should survive normal rebuild/redeploy once committed/deployed.

1. **SSH tunnel target alignment on cluster update path**
   - File: `backend/services/cluster_management_service.py`
   - Fix: when enabling `ssh_tunnel_enabled` without explicit remote host/port overrides, update path now derives `ssh_remote_k8s_host`/`ssh_remote_k8s_port` from cluster `api_server` (same behavior as create path).
   - Why: avoids stale default `localhost:6443` when kind kubeconfig actually uses `127.0.0.1:<random-port>`.
   - Validation: `backend/tests/component/test_cluster_management_service.py` (43 passed).

2. **Blank-host module runtime hotfix framework for known kind module drift**
   - File: `backend/services/execution/opentofu_runtime.py`
   - Fixes include bounded runtime rewrites for `infra/ubuntu/kind` workspaces:
     - removes deprecated `hashicorp/terraform` provider stanza when present
     - replaces non-POSIX `set -euo pipefail` with `set -eu`
     - makes remote directory creation idempotent if stale files exist
     - adds `scp` fallback rewrite to `ssh+cat` when `scp` missing
     - materializes inline SSH private key payload into secure temp file (`0600`) when module expects path
     - adds bounded docker/iptables self-healing preflight before `kind create` (legacy iptables switch + docker restart attempt + re-check)
   - Why: converts repeated manual workspace surgery into deterministic runtime behavior.

3. **Stack preflight/runtime reachability safeguards (existing-cluster flow)**
   - File: `backend/services/stack_deployment_service.py`
   - Fix: loopback kubeconfig API endpoints are treated truthfully unless SSH tunnel is enabled; improved actionable guidance.
   - Why: prevents misleading attempts against host-local endpoints from backend/worker containers.

4. **Project SSH credential auto-wiring for SSH modules**
   - File: `backend/services/execution/variable_assembler.py`
   - Fix: if module declares `ssh_host`/`ssh_user`/`ssh_private_key_path` and project has `ssh_credential_id`, values are auto-injected.
   - Why: reduces manual input drift during blank-host bootstrap flows.

5. **Discovery credential reference validation (early truthful 4xx)**
   - File: `backend/services/discovery_service.py`
   - Fix: validates shared/node-level `ssh_credential_id` upfront before persistence.
   - Why: avoids late FK/runtime failures and provides immediate corrective feedback.

6. **Frontend existing-project selection behavior for kind bootstrap template**
   - File: `frontend-v2/src/components/stacks/StackDetailDialog.tsx`
   - Fix: `ubuntu-kind-foundation` can target existing projects even when `cluster_count=0`; cluster-required filtering remains for BNK existing-cluster templates.
   - Why: blank-host bootstrap should allow project selection before cluster registration exists.

7. **Stack status reconciliation for post-destroy stale in-progress rows**
   - Files:
     - `backend/services/stack_deployment_service.py`
     - `backend/services/stack_service.py`
   - Fixes:
     - `update_stack_progress()` now reconciles stacks with zero remaining modules to terminal `destroyed` when appropriate.
     - `get_stack_status()` now triggers reconciliation when stack row is `deploying`/`destroying` before serializing response.
   - Why: prevents UI from showing stale `Deploying` while modules are already destroyed/terminal.

8. **Backend image now includes SSH client tooling by default**
   - File: `backend/Dockerfile`
   - Fix: added `openssh-client` to base image dependencies.
   - Why: removes one recurring manual server intervention where runtime module steps needed `scp`/SSH client binaries.

---

## B) Ephemeral/manual server fixes used during debugging

These were applied in environment/runtime and were initially non-durable. Items marked **resolved** are now codified.

1. **Runtime container package install (`scp`)**
   - Action used: install `openssh-client` in running backend/worker containers.
   - Purpose: unblock module step that shells out to `scp` for kubeconfig retrieval.
   - Durability: **Resolved in code** — backend image now includes `openssh-client` (`backend/Dockerfile`).

2. **Host Docker/iptables compatibility adjustment on blank target**
   - Action used: switch `iptables` alternatives to legacy on `10.176.11.143` to recover Docker chain behavior (e.g., `DOCKER-FORWARD`).
   - Purpose: allow kind networking/bootstrap where Docker iptables path was broken.
   - Durability: **Partially resolved in runtime hotfix path** — kind module execution now attempts bounded remediation automatically before failing; still host-dependent if remediation cannot succeed.

3. **In-place workspace edits during triage (before runtime hotfix codification)**
   - Actions used: manual `main.tf` / workspace script edits in `/app/workspaces/...` while debugging.
   - Purpose: unblock immediate runs.
   - Durability: **Resolved in code** for covered patterns via `OpenTofuRuntime` bounded hotfix application.

4. **Direct DB/API toggles while debugging tunnel setup**
   - Actions used: manually set `ssh_tunnel_enabled`, `ssh_credential_id`, and related fields during investigation.
   - Purpose: force runtime path for evidence gathering.
   - Durability: **Partially resolved** — update path now auto-derives tunnel target when enabling SSH tunnel; still depends on cluster registration/update flow setting tunnel-enabled intent.

---

## C) Single-click readiness gaps still open

To satisfy "single click and work" after destroy/re-apply, the following must be true every run:

1. **No ad-hoc package install in running containers** ✅
   - Completed hardening: backend image now bakes `openssh-client`; runtime no longer depends on manual apt install for SSH client tooling.

2. **No host-manual iptables surgery** (improved)
   - Completed hardening: runtime path now attempts bounded auto-remediation for common nft/legacy mismatch before kind create.
   - Remaining risk: truly broken host Docker/networking still needs host-level repair (outside Forge process scope).

3. **No manual cluster tunnel toggling**
   - Required hardening: kind blueprint registration/update path should deterministically enable SSH tunnel and derive remote target from kubeconfig API server.

4. **No manual prerequisite secret patching surprises**
   - Required hardening: BNK preflight must surface required project secrets (`jwt_token`, `cne_pull_secret`) before deploy click, with clear remediation path.

---

## D) Post-destroy manual apply acceptance checklist

Use this checklist for your upcoming manual destroy + apply verification:

1. Destroy prior stack/environment state.
2. Re-run kind blueprint from Forge UI/API (no shell edits).
3. Confirm stack status truthfulness after destroy/apply transitions:
   - no stale `Deploying` when modules are already destroyed/terminal.
4. Confirm cluster is registered and has:
   - `ssh_tunnel_enabled=true`
   - `ssh_remote_k8s_host` + `ssh_remote_k8s_port` aligned to kubeconfig API server.
5. Confirm cluster runtime reachability check passes with tunnel path.
6. Add/confirm project secrets required by BNK blueprint:
   - `jwt_token`
   - `cne_pull_secret`
7. Run BNK preflight and verify no loopback/reachability blocker remains.
8. Start BNK deploy and verify stack transitions are truthful (no stuck deploying).

Pass criteria: no manual workspace edits, no manual in-container package install, no manual DB surgery required.

---

## E) Current conclusion

- SSH tunnel is the preferred method for kind-based blank-host flow.
- We have converted key debug findings into code-level behavior, including stack-status reconciliation and baked SSH client tooling.
- Remaining cross-install risk is primarily host Docker/iptables readiness on truly blank targets; this still needs stronger bootstrap/preflight automation to guarantee one-click reproducibility.
