# Development Strategy — How We Build Without Breaking

The central question: how do we evolve from v2 to the target architecture without breaking what works today?

---

## The Answer: Main Branch, Additive Development, Feature Flags

**Not a separate directory.** Not a long-lived feature branch. Not a rewrite.

Here's why:

### Why NOT `bnk-forge-v3/` (separate directory)
- Two copies of everything. Every bug fix goes in two places.
- No shared code — the whole point is reusing the orchestrator, dependency graph, variable assembly, UI, etc.
- Never merges. Parallel codebases rot. One gets abandoned.
- We'd need to duplicate the Docker setup, the frontend, the database schema, everything.

### Why NOT a long-lived feature branch
- Merge conflicts compound exponentially with time. After 2-3 weeks of parallel development, the merge is brutal.
- No visibility — work is hidden until the big merge day.
- Can't deploy incrementally. It's all or nothing.
- Other people (or future work on main) creates drift.

### Why main branch + additive development
- Every commit is shippable. The existing system never breaks.
- New code lives alongside old code. Both paths work.
- The engine router (Section 11 of ARCHITECTURE_REVIEW.md) is the key: it **routes to the old engine by default** and only routes to the new engine when a module has `execution_engine: "kubernetes"` in its metadata.
- We can test the new engine on one module at a time, gain confidence, then roll it out to more.
- Feature flags in the database (`ApplicationSetting`) control which engine is active.

---

## The Rules

### Rule 1: Never Remove, Only Add (Until It's Proven)

```
Week 1-6:   Old engine works. New engine code exists but is never called.
Week 7-10:  Engine router added. Routes to old engine by default.
            New engine opt-in via module.json metadata flag.
Week 11-14: New engine tested on 1-2 modules in dev/staging.
Week 15+:   Gradually flip modules to new engine.
Eventually: Remove old engine code paths (only after all modules migrated).
```

### Rule 2: Every PR Must Pass Existing Tests + New Tests

The current test suite is tiny (~976 lines across 10 files), but it's what we have. The contract:
- **Existing tests never break.** If they do, the PR doesn't merge.
- **Every new service gets unit tests.** The new engine, scanner, module definitions — all testable in isolation.
- **Integration tests run against the Docker stack.** Before any engine switchover, we deploy a full stack and verify.

### Rule 3: The Database Evolves, Never Resets

- All schema changes via Alembic migrations.
- New columns are always `nullable=True` or have defaults.
- No column removals until the old code path is dead.
- Feature flags live in `ApplicationSetting` table (already a pattern in the codebase).

### Rule 4: The Frontend Evolves In Place

- New components added alongside old ones.
- The deploy wizard (P4-A) is a NEW route (`/deploy`), not a replacement of the stacks page.
- The BNK health dashboard is a NEW component, not a rewrite of the K8s page.
- Old UI paths keep working. New paths are additive.
- Once the new flow is proven, the old stacks page can redirect.

---

## Concrete Development Flow

### Phase 1: Quick Wins (Weeks 1-2)
**Branch strategy:** Direct to main in small PRs.

```
main ──●──●──●──●──●──●──●──  (each ● is a small PR)
       │  │  │  │  │  │  │
       │  │  │  │  │  │  └─ QW-014: log rotation
       │  │  │  │  │  └──── QW-012: DRY docker-compose
       │  │  │  │  └─────── QW-011: remove dead files
       │  │  │  └────────── QW-007: get_or_404 everywhere
       │  │  └───────────── QW-005/006: error handling
       │  └──────────────── QW-001-004: bug fixes
       └─────────────────── QW-015/016: Helm UI + stack parallel
```

Each quick win is:
1. One PR
2. One focused change
3. Passes existing tests
4. Adds tests where appropriate (especially for bug fixes — regression tests)
5. Reviewed and merged same day

**No feature branch needed.** These are all small, independent, low-risk.

### Phase 2: Structural Refactoring (Weeks 3-6)
**Branch strategy:** Short-lived feature branches (1-3 days max), merged to main.

```
main ──────────────────────────●────────────●────────────●──
                              ╱            ╱            ╱
  refactor/split-models ─────●            ╱            ╱
                                         ╱            ╱
  refactor/engine-interface ────────────●            ╱
                                                    ╱
  refactor/centralize-schemas ─────────────────────●
```

Key structural changes and their safety:

| Change | Why It's Safe |
|---|---|
| Split `models.py` into `models/` package | `__init__.py` re-exports everything. No import changes needed. |
| Define `DeploymentEngine` ABC | Interface only — no runtime impact. Nothing calls it yet. |
| Wrap `ExecutionEngine` as `OpenTofuEngine` | Adapter pattern — delegates to existing code. Nothing changes externally. |
| Centralize Pydantic schemas | Move + re-export. Same API contracts. OpenAPI spec is the test. |
| Extract `variable_assembler.py` | Extract method refactoring. Existing code calls the extracted function. |

**The key insight:** Structural refactoring is safe because we're *reorganizing*, not *rewriting*. The `__init__.py` re-export pattern means existing imports don't break.

### Phase 3: Kubernetes Engine (Weeks 7-12)
**Branch strategy:** Feature branch `feat/kubernetes-engine`, merged to main when the engine passes tests. But the engine is **dormant on main** — nothing routes to it until we flip the flag.

```
main ────────────────────────────────────●───────────────────────●──
                                        ╱                       ╱
  feat/kubernetes-engine ──●──●──●──●──●   (engine code added) ╱
                                                               ╱
  feat/cluster-scanner ──●──●──●──────────────────────────────●
```

**What lands on main:**
1. `backend/services/execution/kubernetes_engine.py` — exists but nothing calls it
2. `backend/modules/` — Python module definitions exist but nothing uses them
3. `backend/services/cluster_scanner.py` — exists, exposed as a new API endpoint
4. `backend/services/execution/engine_router.py` — exists, **defaults to OpenTofu for everything**

**How to test without breaking:**
```python
# engine_router.py
class EngineRouter:
    def _determine_engine(self, module) -> str:
        # Feature flag: check ApplicationSetting
        k8s_engine_enabled = self._get_setting("kubernetes_engine_enabled", "false")
        if k8s_engine_enabled != "true":
            return "opentofu"  # ALWAYS use old engine unless explicitly enabled
        
        # ... normal routing logic ...
```

**Test plan for the K8s engine:**
1. **Unit tests** (no cluster needed): Mock `kr8s.asyncio.Api`, test that modules render correct manifests, test that the engine calls the right methods.
2. **Integration tests** (kind/k3s cluster): Spin up a local K8s cluster in CI, deploy a simple manifest module, verify it works.
3. **Manual acceptance** (real cluster): Deploy one BNK module (e.g., `bnk/bnk-gatewayclass`) via the new engine to a dev EKS cluster. Compare with the OpenTofu result.

### Phase 4: Persona UX + Container Consolidation (Weeks 13-16)
**Branch strategy:** Feature branches for each UI component, merged independently.

```
feat/deploy-wizard      → New /deploy route (doesn't replace /stacks)
feat/bnk-health         → New component on K8s page
feat/deployment-profiles → New profile selection in deploy wizard
feat/single-container    → New Dockerfile.slim + docker-compose.slim.yml
```

---

## Testing Strategy

### What Exists Today (Baseline)
```
backend/tests/           6 files, 534 lines  — security tests (path traversal, injection)
tests/                   4 files, 442 lines  — performance, dependency wiring
tests/e2e/               Playwright suite     — broken selectors, not runnable
```

### What We Add (Incremental)

#### Layer 1: Unit Tests for New Code (Every PR)
Every new service gets tests. These run fast (no Docker, no DB, no cluster).

```
backend/tests/
  execution/
    test_variable_assembler.py     # Test 7-layer variable chain with fixtures
    test_kubernetes_engine.py      # Mock kr8s, test manifest apply/destroy
    test_engine_router.py          # Test routing logic
    test_hcl_generator.py          # Test config file generation
  modules/
    test_bnk_gatewayclass.py       # Test manifest rendering
    test_flo.py                    # Test Helm values rendering
    test_far_setup.py              # Test manifest rendering
  test_cluster_scanner.py          # Mock kr8s, test detection logic
  test_adaptive_module_service.py  # Test module selection from scan results
```

**Pattern:** In-memory SQLite + mocked external services.
```python
# Example: test_kubernetes_engine.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from services.execution.kubernetes_engine import KubernetesEngine

@pytest.fixture
def mock_kr8s_api():
    api = AsyncMock()
    api.apply = AsyncMock(return_value=MagicMock())
    api.get = AsyncMock(return_value=MagicMock())
    return api

@pytest.mark.asyncio
async def test_apply_manifest_module(mock_kr8s_api):
    engine = KubernetesEngine.__new__(KubernetesEngine)
    engine._api = mock_kr8s_api
    
    result = await engine.apply(
        module=ModuleConfig(path="bnk/bnk-gatewayclass", ...),
        variables={"gatewayclass_name": "test-gc"},
        credentials_env={},
    )
    
    assert result.success
    assert mock_kr8s_api.apply.call_count == 2  # GatewayClass + Config
```

#### Layer 2: Integration Tests Against Docker Stack (Weekly)
Run the full Docker stack, hit the API, verify end-to-end.

```
tests/integration/
  test_project_lifecycle.py        # Create project, add modules, init, plan
  test_stack_deployment.py         # Deploy a stack template, verify modules created
  test_credential_templates.py     # CRUD credentials, test validation
  test_helm_operations.py          # List/install/upgrade via Helm API
  conftest.py                      # Docker stack fixture, API client
```

**Pattern:** Requires `docker compose up`, uses `httpx` async client.
```python
# conftest.py
import httpx
import pytest

@pytest.fixture(scope="session")
def api_client():
    """Assumes docker compose is running."""
    return httpx.AsyncClient(base_url="http://localhost:2651")

@pytest.mark.asyncio
async def test_create_project(api_client):
    response = await api_client.post("/api/projects", json={
        "name": "test-project",
        "cloud_provider": "aws",
        "aws_region": "us-east-1",
    })
    assert response.status_code == 200
    assert response.json()["name"] == "test-project"
```

#### Layer 3: K8s Engine Tests Against kind/k3s (Before Engine Switchover)
Before flipping any module to the K8s engine, we test against a real (local) cluster.

```
tests/k8s_engine/
  test_manifest_apply.py           # Apply a CRD manifest, verify it exists
  test_helm_install.py             # Install a Helm chart, verify release
  test_readiness_wait.py           # Apply resource, wait for ready
  test_destroy.py                  # Create then destroy, verify cleanup
  test_cluster_scanner.py          # Scan a kind cluster, verify detection
  conftest.py                      # kind cluster fixture
```

**Pattern:** Uses `kind` or `k3s` in Docker.
```python
# conftest.py
import subprocess
import pytest

@pytest.fixture(scope="session")
def k8s_cluster():
    """Create a kind cluster for testing."""
    subprocess.run(["kind", "create", "cluster", "--name", "bnk-forge-test"], check=True)
    kubeconfig = subprocess.run(
        ["kind", "get", "kubeconfig", "--name", "bnk-forge-test"],
        capture_output=True, text=True, check=True
    ).stdout
    yield kubeconfig
    subprocess.run(["kind", "delete", "cluster", "--name", "bnk-forge-test"])
```

#### Layer 4: E2E Tests (Fix Existing + Add New Flows)
Fix the broken Playwright selectors, add the new deploy wizard flow.

---

## The Container Evolution

We don't need to decide on the final container architecture now. Here's the incremental path:

### Today: 9 containers (keep working)
```
postgres, redis, backend, celery-worker, celery-worker-2, 
celery-beat, frontend, proxy, postgres-backup
```

### Phase 2: 7 containers (consolidate redundancy)
```diff
  postgres, redis, backend, celery-worker,
- celery-worker-2,        # Merge into celery-worker with concurrency=8
- celery-beat,            # Move to APScheduler in backend (already imported)
  frontend,
- proxy                   # Remove — frontend nginx already proxies /api/
- postgres-backup         # Move to cron in postgres container
```

### Phase 4: 3 containers (production-lean)
```
postgres, redis, app (backend + worker + frontend static files)
```

### Future: 1 container (single-user mode)
```
bnk-forge (SQLite + asyncio tasks + static files)
```

**Key:** We don't rip out Celery/Redis/Postgres. We build the asyncio alternative *alongside* Celery. A settings flag controls which task backend is active:

```python
# core/config.py
TASK_BACKEND = os.getenv("TASK_BACKEND", "celery")  # "celery" or "asyncio"
```

```python
# services/task_dispatcher.py
async def dispatch_task(task_name: str, **kwargs):
    if settings.TASK_BACKEND == "celery":
        return celery_app.send_task(task_name, kwargs=kwargs)
    else:
        return await asyncio_task_runner.run(task_name, **kwargs)
```

This means:
- `docker-compose.yml` (current) keeps working for teams/servers
- `docker-compose.slim.yml` (new) runs the lean 3-container version
- `Dockerfile.slim` (new) bundles everything into one image with SQLite
- Eventually: `docker run bnk-forge` for the single-container experience

---

## Credential Template Evolution

Credential templates are reused and extended — they're the "key" to everything:

### What Changes (Additive)

```python
# models.py — Add to CloudCredentialTemplate:
class CloudCredentialTemplate(Base):
    # Existing AWS, SSH, GCP (schema), Azure (schema) fields stay
    
    # NEW: Kubeconfig credential type
    kubeconfig_encrypted = Column(Text, nullable=True)
    kubeconfig_context = Column(String(255), nullable=True)
    
    # NEW: Template capabilities (what this credential can do)
    capabilities = Column(JSON, nullable=True)  
    # e.g., {"infra": true, "k8s": true, "helm": true}
    # AWS template: {"infra": true, "k8s": true}
    # Kubeconfig template: {"k8s": true, "helm": true}
    # SSH template: {"tunnel": true}
```

### How Templates Drive the Three Personas

```
PERSONA 1: "Build It All"
  └─ Project has: AWS credential template
     └─ Capabilities: infra ✓, k8s ✓, helm ✓
     └─ Used by: OpenTofu (VPC, EKS) → auto-generates kubeconfig → K8s engine (BNK)
     └─ Flow: Create infra → EKS outputs kubeconfig → auto-attach to project → deploy BNK

PERSONA 2: "I Have a Cluster"  
  └─ Project has: Kubeconfig credential template (NEW)
     └─ Capabilities: k8s ✓, helm ✓
     └─ Used by: K8s engine directly (no OpenTofu)
     └─ Flow: Upload kubeconfig → scan cluster → deploy BNK

  OR: Project has: AWS credential template (SSO/keys)
      └─ Used by: aws eks get-token for kubeconfig auth
      └─ Flow: Select EKS cluster → auto-generate kubeconfig → scan → deploy BNK

PERSONA 3: "Just BNK"
  └─ Project has: Kubeconfig credential template (same as Persona 2)
     └─ Flow: Upload kubeconfig → scan shows cert-manager ✓, Multus ✓ → deploy only BNK modules
```

### Multi-Credential per Project

Currently: `Project.credential_template_id` (singular FK — one template per project).

The "Build It All" persona needs BOTH cloud credentials (for infra) AND cluster credentials (for BNK). Options:

**Option A: Auto-chain (recommended for now)**
- AWS template creates EKS → EKS module output is a kubeconfig → auto-stored on KubernetesCluster
- The K8s engine reads from KubernetesCluster.kubeconfig, not from the credential template
- No schema change needed — already works this way for EKS auto-registration

**Option B: Multi-credential FK (future)**
```python
# New join table:
class ProjectCredential(Base):
    project_id = Column(Integer, ForeignKey("projects.id"))
    template_id = Column(Integer, ForeignKey("cloud_credential_templates.id"))
    purpose = Column(String(50))  # "cloud_infra", "kubernetes", "ssh_tunnel"
```

**Start with Option A** — it's how the system already works for the greenfield persona. Add Option B later when on-prem multi-credential becomes a real need.

---

## Directory Structure Evolution

We don't reorganize the whole repo. We add new directories alongside existing ones:

```
backend/
  services/
    execution/                    # NEW — engine abstraction
      __init__.py
      engine_interface.py         # DeploymentEngine ABC
      opentofu_engine.py          # Wraps existing ExecutionEngine
      kubernetes_engine.py        # NEW — kr8s-based
      engine_router.py            # Routes to correct engine
      variable_assembler.py       # Extracted from execution_engine.py
      hcl_generator.py            # Extracted from execution_engine.py
    execution_engine.py           # KEPT — still works, called by opentofu_engine.py
    workspace_manager.py          # KEPT — only used by opentofu_engine.py
    cluster_scanner.py            # NEW
    adaptive_module_service.py    # NEW
    ...existing services...       # UNCHANGED
  
  modules/                        # NEW — Python module definitions for K8s engine
    __init__.py                   # MODULE_REGISTRY dict
    base.py                       # BaseModule, HelmModule, ManifestModule
    k8s/
      cert_manager.py
      network_setup.py
    bnk/
      far_setup.py
      flo.py
      bnk_gatewayclass.py
      gateway.py
      routes.py
      bnk_secpolicy.py
      bnk_netpolicy.py
  
  models/                         # Phase 2 — split from models.py
    __init__.py                   # Re-exports everything
    ...domain files...
  
  models.py                       # KEPT until models/ is proven, then becomes import redirect
  
  tests/
    ...existing security tests... # UNCHANGED
    execution/                    # NEW
      test_kubernetes_engine.py
      test_variable_assembler.py
      test_engine_router.py
    modules/                      # NEW
      test_bnk_gatewayclass.py
      test_flo.py
    test_cluster_scanner.py       # NEW

frontend-v2/
  src/
    components/
      deploy/                     # NEW — unified deploy wizard
        DeployWizard.tsx
        ClusterConnect.tsx
        ClusterScanResults.tsx
        ProfileSelector.tsx
        DeployProgress.tsx
      bnk/
        BNKHealthDashboard.tsx    # NEW
        ...existing components... # UNCHANGED
      stacks/
        ...existing components... # UNCHANGED (still works)
    pages/
      DeployPage.tsx              # NEW route /deploy
      ...existing pages...        # UNCHANGED
```

**The principle:** New directories are additive. Existing files are untouched until the new code is proven. Then we migrate callers one at a time.

---

## PR Workflow

```
1. Create short-lived feature branch (1-5 days max)
2. Write code + tests
3. Run existing tests locally: `cd backend && python -m pytest tests/`
4. Run new tests: `cd backend && python -m pytest tests/execution/ tests/modules/`
5. Verify Docker stack still works: `docker compose up -d && curl localhost:2651/`
6. PR to main with description of what's added, what's unchanged
7. Merge. Deploy. Verify.
```

For the K8s engine specifically:
```
1. Feature branch: feat/kubernetes-engine
2. Build engine + module definitions + tests
3. Engine is behind feature flag (defaults OFF)
4. Merge to main — engine exists but is dormant
5. Test in dev environment by enabling the flag
6. When confident: flip flag for one module (e.g., bnk-gatewayclass)
7. Compare results: K8s engine vs OpenTofu engine for same module
8. When all modules verified: flip flag for all BNK modules
9. Eventually: remove OpenTofu code paths for BNK modules
```

---

## Risk Mitigation

| Risk | Mitigation |
|---|---|
| New engine breaks existing deployments | Feature flag defaults to OFF. Old engine always available. |
| Models refactor breaks imports | `__init__.py` re-exports. No external import changes. |
| Schema migration breaks database | All new columns nullable with defaults. Alembic rollback tested. |
| kr8s library is too immature | Fallback to official kubernetes-client/python. Same interface, more verbose. |
| Celery removal breaks workers | Celery kept. Asyncio alternative is opt-in via env var. Both coexist. |
| Frontend changes break existing flows | New routes/components only. Old pages untouched. |
| Docker consolidation breaks deployments | `docker-compose.yml` unchanged. `docker-compose.slim.yml` is new, opt-in. |

---

## Summary: The Golden Rule

> **Everything new is additive. Everything old keeps working. Cutover is gradual and reversible.**

This means:
- No "big bang" migration day
- No "hope the merge works" branch
- No "now we support v2 and v3" split
- Just steady, testable, incremental progress on main
