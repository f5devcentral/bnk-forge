# D-016 — Port Helm operations to Celery (engine parity)

- **Status:** Proposed
- **Date proposed:** 2026-05-21
- **Backlog id:** `architecture-helm-celery-port`
- **Source memo:** 2026-05-21 licensing + helm-releases audit (helm sync-route anti-pattern)
- **Depends on:** none (touches `backend/routes/helm.py`, `backend/services/helm_service.py`, `backend/tasks/`, `frontend-v2/src/hooks/useHelm.ts`)
- **Resume trigger:** next helm-related outage (api worker pool exhaustion under concurrent install/upgrade), next user-visible "No Helm releases" bug on a cloud cluster the API container can't auth to, or when a second engine (e.g. `kustomize`, `argocd`) is about to be added the same way.

## Context

Helm is the only infrastructure engine in this codebase that bypasses the Celery pattern every other engine follows.

| Engine | Tasks module | Auth context | Pattern |
|---|---|---|---|
| OpenTofu | `backend/tasks/opentofu_tasks.py` (5 tasks) | worker | Celery, fence-token locked, status streamed |
| Ansible | `backend/tasks/ansible_tasks.py` (4 tasks) | worker | Celery |
| SSH / bare-metal | `backend/tasks/ssh_tasks.py`, `bare_metal_tasks.py` | worker | Celery |
| BNK upgrade | `backend/tasks/bnk_upgrade_tasks.py` | worker | Celery, heartbeat+fence (PR #105 / #118) |
| Proxy deploy | `backend/tasks/proxy_deploy_tasks.py` | worker | Celery |
| Kubernetes Python client ops | (sync HTTP, no shellout) | api | OK — no binary deps |
| **Helm** | **none** | **api** | **`subprocess.run(['helm', ...])` from `backend/routes/helm.py` (25+ sync routes, 18 call sites in `helm_service.py`, 5-min `timeout`)** |

This wasn't a deliberate architectural choice — `backend/Dockerfile:21,27,148-149,228` deliberately omits `aws`/`tofu`/`docker`/`git` from the slim api image precisely *because* "those are worker-only", and the `KubernetesService.load_kubeconfig` path (`backend/services/kubernetes/_base.py:91-119`) mints a boto3 static EKS token specifically so api-side Python-client K8s ops don't need `aws`. Helm shellouts slipped in under a different kubeconfig path (`backend/services/cluster_utils.py:_write_kubeconfig` leaves the exec plugin intact) and never got reconciled.

**Deletion test:** removing the celery wrappers from any other engine breaks its routes; removing the (nonexistent) helm celery wrappers breaks nothing — because helm is wholly in the api process. Genuine seam.

## Consequences of the status quo (concrete, observed)

1. **EKS helm-list is broken in the slim api image.** Live evidence on `aws-syd-test-cluster` (id=9), 2026-05-21:
   ```
   GET /api/k8s/9/helm/releases?all_namespaces=true → HTTP 500
   "Failed to list releases: ... exec: executable aws not found ...
    you are trying to use a client-go credential plugin that is not installed"
   ```
   `bnk-forge-backend` has `helm`+`kubectl` but no `aws`; `bnk-forge-celery-worker` has all three. Every helm op against an EKS cluster fails for the same reason.

2. **Frontend masks the 500 as "No Helm releases".** `KubernetesV2.tsx:773-799` has loading + empty + table branches but no `isError` branch. The user sees a clean empty state when a real upstream failure occurred. This is itself a downstream symptom of (1) — fixing (1) makes (2) cosmetic.

3. **`helm install`/`upgrade` block FastAPI workers for up to 5 minutes** (`helm_service.py:98` `timeout=300`). Sync HTTP routes against a small uvicorn worker pool means a handful of concurrent installs can starve unrelated traffic.

4. **No progress, retry, or locking** for helm operations. Every other engine inherits these from the Celery infra (`tasks/_tofu_helpers.py`, `services/module_lock_service.py`, the heartbeat+fence pattern from PR #99/#105/#118). Helm operations have none.

5. **Two kubeconfig-prep paths persist** because one code path (helm) needed the exec plugin and the other (kubernetes Python client) didn't. After the port, the helm path no longer cares whether the kubeconfig has an exec plugin (worker has `aws`), so we can leave `cluster_utils.prepare_kubeconfig` as-is, and the two paths converge by *purpose* (worker-only / api-only) rather than by accident.

## Decision (deeper shape)

Port all helm operations to Celery tasks following the existing engine pattern. Reads stay synchronous from the user's perspective via short-poll or SSE; writes run async with progress.

```
backend/tasks/helm_tasks.py
  list_releases_task        # @celery_app.task(queue="default")
  get_release_task
  release_history_task
  release_values_task
  release_manifest_task
  install_release_task      # bind=True, base=CallbackTask
  upgrade_release_task
  rollback_release_task
  uninstall_release_task
  test_release_task
```

API routes thin out to enqueue + return task_id (mirrors `tasks/opentofu_tasks.py` ↔ `routes/opentofu.py` shape). FE gets back a task handle and polls/streams.

**Reads are still allowed to be sync-feeling** — same approach the Stack pages use: enqueue, await result with a short timeout (e.g. 8s), return inline on hit, fall back to task_id on miss.

**Auth context comes free**: worker has `aws` + `gcloud`/`gke-gcloud-auth-plugin` + raw kubeconfig support, so the entire load_kubeconfig acrobatics in `_base.py:91-119` becomes unnecessary on the helm path.

## Migration outline (not a build plan — sketch for the resume trigger)

1. Create `backend/tasks/helm_tasks.py`. Wrap each `HelmService` method as a task. Keep `HelmService` intact so the task body is a thin shim.
2. Convert `routes/helm.py` handlers to `.delay()` and return task_id (or short-poll for reads). Match the `opentofu_tasks` ↔ `routes/opentofu.py` shape — there's a working precedent to copy.
3. Update FE hooks (`useHelm.ts`, `useAllHelmReleases.ts`) to use the task-polling pattern. Add `isError` branch in `KubernetesV2.tsx:773-799` for the helm-releases section.
4. Add helm-install/upgrade/uninstall to the entity-locking pattern from PR #99 (treat each `(cluster_id, release_name)` as a lockable entity — prevents concurrent install+rollback races).
5. Once helm no longer shells out from api, consider removing `helm` from the api image too (`backend/Dockerfile:246-256`) for further slim. Optional.

## Out of scope

- The "kubeconfig exec plugin token-mint" pattern in `_base.py:91-119`. After the port the api path no longer needs helm, and the worker path doesn't need the rewrite (it has `aws`), so we don't have to touch the dual-prep paths. They converge by *who runs them* instead of by *what they produce*.
- Helm repo management routes (`POST /api/helm/repositories`, etc. — they're cluster-independent and don't have the auth problem).

## Test win

- **One** integration test (worker-side) per helm op replaces 18 subprocess-mocked unit tests.
- "concurrent install + rollback" becomes a real race we can assert against (fence-token rejection), not a TODO comment.
- "EKS helm list works" stops depending on whether the slim api image happened to keep the `aws` binary on this build.

## References

- Source: `bin/maf-status.sh` 2026-05-21 audit transcript (helm sync-route inventory + live aws-not-found probe on `aws-syd-test-cluster`)
- Code: `backend/routes/helm.py`, `backend/services/helm_service.py`, `backend/services/cluster_utils.py:119-198`, `backend/services/kubernetes/_base.py:91-119`, `backend/Dockerfile:21-228`
- Precedents to mirror: `backend/tasks/opentofu_tasks.py`, `backend/tasks/ansible_tasks.py`, `backend/tasks/bnk_upgrade_tasks.py`
- Lock pattern to extend: PR #99 (Phase 1.5) → PR #105 (Phase 1.6) → PR #118 (Phase 1.6 alembic linearize)
- FE rendering bug to pair-fix: `frontend-v2/src/pages/KubernetesV2.tsx:773-799`
