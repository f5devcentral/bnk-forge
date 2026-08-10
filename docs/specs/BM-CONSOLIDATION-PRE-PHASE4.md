# Pre-Phase-4 Consolidation Spec — Discovery Persistence, Step Composability, SSH Consolidation

> Consolidation and enhancement work required before implementing Phase 4-7 of DPU bare-metal deployment.
>
> Date: 2026-04-16
> Branch: `feat/dpu-bare-metal-deploy`
> Status: **DRAFT — Architect Review**
> References:
> - [DPU_DEPLOY_SPEC.md](../DPU_DEPLOY_SPEC.md) — Architecture specification
> - [DPU_DEPLOY_IMPLEMENTATION_PLAN.md](../DPU_DEPLOY_IMPLEMENTATION_PLAN.md) — Task registry

---

## 1. Executive Summary

Three structural gaps must be addressed before Phase 4 (DPU provisioning) can proceed safely: (1) discovery results are ephemeral API responses that vanish between sessions, so step executors cannot read hardware facts without re-probing; (2) deployment is all-or-nothing with no way to run individual phases or steps selectively; (3) two independent SSH subsystems (bare-metal and staging) duplicate ~200 lines of paramiko boilerplate with inconsistent key-loading order. This consolidation spec resolves all three in a scoped, additive manner implementable in 2-3 builder sessions without breaking existing functionality.

---

## 2. Scope

### In Scope

- Promote key hardware facts from JSON blobs to typed columns on `BareMetalHost`
- Persist full discovery results (probes + assessment) to a JSON column for cross-session survival
- Add phase/step selection to deployment creation (composable blueprints)
- Extract shared SSH/paramiko primitives into a single module
- Remove vestigial `SSHConnectionPool` (subprocess-based, unused since paramiko refactor)
- Alembic migration for new columns (additive only)
- Schema updates for new model fields and API inputs
- Unit tests for all new/changed code

### Out of Scope

- Phase 4-7 step executor implementations (those come after this consolidation)
- Frontend deployment wizard redesign (minimal frontend: just expose `selected_phases` in the create form)
- MCP tool updates (deferred to Phase 7)
- Refactoring `DiscoveryService` (project-level multi-node discovery) — it stays as-is
- Consolidating `SSHService.generate_key_pair()` or `SSHService.setup_key_auth()` — those are unique capabilities, not duplicated primitives
- Persistent SSH tunnel lifecycle changes in `SSHTunnelManager` (unique capability, stays as-is)

---

## 3. Design

### 3.1 Concern 1: SSH Consolidation

#### Current State

Five independent implementations of the same two primitives exist across the codebase:

**Key loading (from string content):**
| Location | Try-order | Input |
|----------|-----------|-------|
| `ssh_tunnel_manager.py:565` `_load_private_key()` | RSA → ECDSA → Ed25519 | `io.StringIO` |
| `ssh_service.py` (4 inline copies) | RSA → Ed25519 → ECDSA | `io.StringIO` |
| `discovery_service.py:424` `_ssh_connect()` | Ed25519 → RSA → ECDSA | `io.StringIO` |

**Key loading (from file path):**
| Location | Try-order | Input |
|----------|-----------|-------|
| `bare_metal/ssh_session.py:54` `_load_key()` | Ed25519 → RSA → ECDSA | file path |

**Connect-with-auth (paramiko client setup):** 10+ independent copies across `ssh_tunnel_manager.py`, `ssh_service.py`, `discovery_service.py`, and `bare_metal/ssh_session.py`.

#### Target State

New shared module: `backend/services/ssh/paramiko_utils.py`

```
backend/services/ssh/
├── __init__.py              # Package exports
└── paramiko_utils.py        # Shared primitives
```

**Three shared functions:**

```python
def load_private_key_from_content(content: str, passphrase: str | None = None) -> paramiko.PKey:
    """Load a private key from string content (PEM/OpenSSH format).
    Try-order: Ed25519 → RSA → ECDSA (preferred → legacy)."""

def load_private_key_from_file(path: str, passphrase: str | None = None) -> paramiko.PKey:
    """Load a private key from a file path.
    Try-order: Ed25519 → RSA → ECDSA (preferred → legacy)."""

def decrypt_ssh_credential(credential: "SSHCredential") -> dict:
    """Decrypt an SSHCredential into a connection-ready dict.
    Returns: {username, port, host, auth_type, password, private_key_content, key_passphrase}
    Preference: key auth over password when both are present."""
```

**Who imports what:**

| Consumer | Uses | Replaces |
|----------|------|----------|
| `bare_metal/ssh_session.py` | `load_private_key_from_file` | inline `_load_key()` |
| `bare_metal/discovery/__init__.py` | `decrypt_ssh_credential` | inline credential resolution in `_build_ssh()` |
| `ssh_tunnel_manager.py` | `load_private_key_from_content` | static `_load_private_key()` |
| `ssh_service.py` | `load_private_key_from_content` | 4 inline copies |
| `discovery_service.py` | `load_private_key_from_content` | inline try-loop in `_ssh_connect()` |

**SSHConnectionPool removal:** Delete `services/bare_metal/ssh_pool.py` + `tests/unit/test_ssh_pool.py`, remove from `__init__.py` exports.

---

### 3.2 Concern 2: Discovery State Persistence

#### Current State

`BareMetalHost` stores discovery results in three JSON blob columns: `os_info`, `dpu_info`, `k8s_info`. These cache a subset of probe data. The full discovery response (including actionable assessment, topology recommendation, version drift) is returned as an API response and never persisted.

#### Target State

**New typed columns on `BareMetalHost`** (promoted from JSON blobs):

| Column | Type | Source | Used by |
|--------|------|--------|---------|
| `nic_mode` | `String(50)` | `host_probe.nic_mode` | Mode-change steps, topology detector |
| `mst_device` | `String(255)` | `host_probe.mst_device` | mlxconfig command construction |
| `rshim_present` | `Boolean` | `host_probe.rshim_present` | DPU flash step routing |
| `default_route_iface` | `String(100)` | `host_probe.default_route_iface` | Netplan staging, fallback timer |
| `phase_c_deposited` | `Boolean` | `host_probe.phase_c_deposited` | Phase C idempotency probe |
| `phase_c_completed` | `Boolean` | `host_probe.phase_c_completed` | Phase C idempotency probe |
| `pf_interfaces` | `JSON` | `host_probe.pf_interfaces` | Connectivity preservation, VLAN probes |
| `vf_count` | `Integer` | `host_probe.vf_count` | SR-IOV configuration |
| `hugepages_host_gb` | `Integer` | `host_probe.hugepages_gb` | Hugepages check |
| `last_discovery_result` | `JSON` | Full response dict | UI re-display, audit, debugging |

All columns are `nullable=True` — additive, no migration risk.

**Existing JSON blob columns (`os_info`, `dpu_info`, `k8s_info`) remain for backward compatibility.**

**New endpoint:**
```
GET /api/projects/{pid}/bare-metal/hosts/{hid}/discovery-result
→ Returns the cached last_discovery_result JSON directly
```

`last_discovery_result` is NOT included in `BareMetalHostResponse` (too large). A `has_discovery_result: bool` flag indicates availability.

---

### 3.3 Concern 3: Step Composability

#### Current State

All-or-nothing: `create_deployment()` generates ALL steps for a topology and runs them sequentially.

#### Target State

**Phase/step selection at deployment creation:**

```python
class BareMetalDeploymentCreate(BaseModel):
    host_id: int
    resume_from_step: int | None = None
    skip_discovery: bool = False
    selected_phases: list[str] | None = None    # None = all phases
    selected_steps: list[str] | None = None     # None = all steps in selected phases
```

**Selection rules:**
- `selected_phases=None, selected_steps=None` → all steps (backward-compatible)
- `selected_phases=["phase_1_dpu"]` → only Phase 1 steps are PENDING, rest SKIPPED
- `selected_steps=["flash_dpu"]` → only that step PENDING (overrides phases if both set)
- Non-selected steps get `status=SKIPPED` in the deployment record (full audit trail)

**Prerequisite validation** uses typed columns from Concern 2:
- Phase 2 requires DPU reachable
- Phase 3 requires K8s running on host
- Phase 4 requires K8s cluster linked

Prerequisites return **warnings** (not hard errors) to allow expert override.

**Step metadata for UI:**
```python
@dataclass(frozen=True)
class StepDefinition:
    phase: DeploymentPhase
    name: str
    description: str
    connectivity_risk: bool = False
    connectivity_mechanism: str | None = None
    estimated_duration_seconds: int = 60
    prerequisites: list[str] = field(default_factory=list)
    idempotent: bool = False
```

**Plan preview endpoint:**
```
POST /api/projects/{pid}/bare-metal/deployments/preview
Body: BareMetalDeploymentCreate
→ Returns DeploymentPlanPreview (no deployment created)
```

**New model columns on `BareMetalDeployment`:**
- `selected_phases: JSON` — what the user requested
- `selected_steps: JSON` — what the user requested

---

## 4. Implementation Plan

### Task Ordering

1. SSH consolidation first (pure refactor, no model changes)
2. Discovery persistence second (adds columns step executors need)
3. Composability third (depends on typed columns for prerequisite validation)

### Task Registry

| Task ID | Description | Depends On | Est. |
|---------|-------------|-----------|------|
| **CON-001** | Create `services/ssh/paramiko_utils.py` with shared primitives | — | 30min |
| **CON-002** | Refactor `bare_metal/ssh_session.py` to import from shared | CON-001 | 15min |
| **CON-003** | Refactor `bare_metal/discovery/__init__.py` to use `decrypt_ssh_credential()` | CON-001 | 20min |
| **CON-004** | Refactor `ssh_tunnel_manager.py` to import from shared | CON-001 | 15min |
| **CON-005** | Refactor `ssh_service.py` to import from shared | CON-001 | 20min |
| **CON-006** | Refactor `discovery_service.py` to import from shared | CON-001 | 10min |
| **CON-007** | Delete `ssh_pool.py` + tests, remove exports | CON-002 | 5min |
| **CON-010** | Add discovery persistence columns to `BareMetalHost` model | — | 10min |
| **CON-011** | Alembic migration for new columns | CON-010 | 10min |
| **CON-012** | Update discovery service to write all results to typed columns + cache | CON-010 | 30min |
| **CON-013** | Update schema + add `/discovery-result` endpoint | CON-010, CON-012 | 20min |
| **CON-020** | Add metadata to `StepDefinition`, update all topology registries | — | 25min |
| **CON-021** | Add selection to deployment model/schema, update `create_deployment()` | CON-011, CON-020 | 45min |
| **CON-022** | Add `DeploymentPlanPreview` schema + `/preview` endpoint | CON-021 | 30min |

### Commit Plan

| Commit | Tasks | Message |
|--------|-------|---------|
| 1 | CON-001..CON-007 | `refactor: extract shared SSH/paramiko primitives, delete vestigial ssh_pool` |
| 2 | CON-010..CON-013 | `feat: persist discovery results to typed columns + full result cache` |
| 3 | CON-020..CON-022 | `feat: composable deployment with phase/step selection + plan preview` |

**Estimated total: ~5 hours builder time across 2-3 sessions.**

---

## 5. Acceptance Criteria

### SSH Consolidation
- [ ] `services/ssh/paramiko_utils.py` exists with 3 exported functions
- [ ] Key try-order is Ed25519 → RSA → ECDSA in ALL consumers
- [ ] Zero inline key-loading loops remain (grep confirms)
- [ ] `ssh_pool.py` deleted
- [ ] All existing tests pass

### Discovery Persistence
- [ ] 10 new nullable columns on `BareMetalHost`
- [ ] Alembic migration applies cleanly (up + down)
- [ ] After discovery, `host.nic_mode`, `host.mst_device`, etc. populated
- [ ] `host.last_discovery_result` contains full serialized response
- [ ] GET host response includes new fields
- [ ] GET `/discovery-result` returns cached result
- [ ] Existing JSON blobs still populated (backward compat)

### Step Composability
- [ ] `selected_phases=None` produces all steps (backward-compatible)
- [ ] `selected_phases=["phase_1_dpu"]` → only Phase 1 PENDING, rest SKIPPED
- [ ] Prerequisite validation warns when Phase 1 outputs missing for Phase 2
- [ ] Prerequisites are warnings (not hard errors) with expert override
- [ ] POST `/preview` returns plan without creating deployment
- [ ] `StepDefinition` has metadata fields
- [ ] `BareMetalDeploymentResponse` includes `selected_phases`/`selected_steps`

---

## 6. Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Key try-order change breaks existing credential | Low | High | Unit test all three key types |
| `decrypt_ssh_credential()` edge case (key + password = passphrase) | Medium | Medium | Explicit test for "key with passphrase" case |
| Discovery result JSON too large | Low | Low | Strip `output_log` before caching if needed |
| Composability prerequisites too strict | Medium | Low | Return warnings not errors; add `force` override |

---

## 7. Non-Goals

1. Phase 4-7 step executor implementations (come after this consolidation)
2. Frontend deployment wizard redesign (minimal: add `selected_phases` to create form)
3. Merging the two discovery systems (`DiscoveryService` and `BareMetalDiscoveryService` remain separate)
4. Consolidating SSH tunnel lifecycle (`SSHTunnelManager` stays as-is)
5. MCP tool updates (deferred to Phase 7)
6. Implementing probe-before-act in step executors (infrastructure exists; implementations come with Phase 4)
7. Removing existing JSON blob columns (`os_info`, `dpu_info`, `k8s_info` remain for backward compat)
