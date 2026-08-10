# DPU Gap Closure Plan: Blueprint ↔ PR #46 Integration

> Architecture, design, and implementation plan for closing the gaps between
> the BNK Forge blueprint module system and PR #46's DPU provisioning services.
>
> Date: 2026-04-22
> Branch: feat/bare-metal-deploy-v2
> Prerequisite reading: `docs/DPU_BLUEPRINT_VS_PR46_ANALYSIS.md`,
> `.agent-local/decisions/D1-blueprint-pr46-integration.md`

---

## 1. Scope & Work Packages

Five work packages in priority order. Each closes a specific gap identified in the analysis document.

| WP | Module/Change | Size | Key Pattern |
|----|--------------|------|-------------|
| WP-1 | `install-rshim` module | S | Standard SSHModule (shell commands via SSH) |
| WP-2 | BFB cache integration | M | New `FileTransferSpec` engine hook (server-side cache → SCP → host) |
| WP-3 | `render-bf-conf` module | L | Port 804-line poc-deployer generator + FileTransferSpec pattern |
| WP-4 | `factory-reset-dpu` module | S | SSHModule with `connectivity_risk=True` (existing pattern) |
| WP-5 | `stage_detail` tracking | S | Alembic migration + engine DB write hook |

### WP-1: `install-rshim` module (Priority 1)

Wrap PR #46's `rshim_service.py` install logic as a new SSHModule in `backend/modules/bare_metal/install_rshim.py`. The rshim daemon + kernel-mft-dkms are prerequisites for every in-band DPU operation — probe, flash, serial console, factory reset. Today, blueprints assume rshim is pre-installed; this module makes it a first-class, idempotent step.

**New files:** `backend/modules/bare_metal/install_rshim.py`
**Modified files:** `backend/data/stack_templates.json` (insert before `probe-dpu`)

### WP-2: BFB cache integration (Priority 2)

Integrate `bfb_cache_service.py` into the `flash-dpu` module's BFB download step. The current `flash_dpu.py` downloads the BFB directly on the target host via `wget`/`curl`. This works but re-downloads 2 GiB files on every retry or multi-host flash. The BFB cache runs on the Forge server with atomic rename, SHA-256 integrity, and LRU eviction at 20 GiB.

**New files:** `backend/services/execution/file_transfer.py` (FileTransferSpec + SCP helper)
**Modified files:** `flash_dpu.py`, `ssh_engine.py`, `base.py` (add `server_side_files()` hook)

### WP-3: `render-bf-conf` module (Priority 3)

Port poc-deployer's `BfbConfigGenerator` (804 lines of topology-aware bf.conf rendering using `string.Template`) as a new blueprint module. Today, `flash-dpu` accepts an optional `bfb_config_url` but has NO built-in renderer. This module renders bf.conf on the Forge server, writes it to a temp directory, and SCP-transfers it to the target host.

**New files:**
- `backend/modules/bare_metal/render_bf_conf.py`
- `backend/services/bare_metal/bf_conf_generator.py` (ported from poc-deployer)
- `backend/services/bare_metal/bf_conf_constants.py`
- `backend/services/bare_metal/bf_conf_helpers.py`
- `backend/data/templates/bf_cfg_regular.tmpl`, `bf_cfg_bf3.tmpl`, `post_bfb_regular.tmpl`, `post_bfb_bf3.tmpl`, `nat_setup_block.tmpl`

**Modified files:** `flash_dpu.py` (accept `bf_conf_local_path` input), `stack_templates.json`

### WP-4: `factory-reset-dpu` module (Priority 4)

Wrap PR #46's `DpuFactoryResetService._run_inband()` as a new SSHModule. Only the in-band path is relevant for blueprints (blueprints SSH to hosts, not BMCs). Must add the blueprint system's connectivity-risk handling because the in-band reset issues `SW_RESET 1`, which drops host connectivity if networking depends on DPU VFs.

**New files:** `backend/modules/bare_metal/factory_reset_dpu.py`
**Modified files:** `stack_templates.json` (add as optional module before `flash-dpu`)

### WP-5: `stage_detail` tracking (Priority 5)

Add a `stage_detail` column to `ProjectModule` and update `SSHEngine` to write it as each command runs. Enables poll-based UI progress (like PR #46's `flash_stage_detail`) for all SSH modules.

**New files:** `backend/alembic/versions/v2_077_add_module_stage_detail.py`
**Modified files:** `backend/models/project.py`, `backend/services/execution/ssh_engine.py`

---

## 2. Architecture Changes

### FileTransferSpec — Shared Abstraction (WP-2 + WP-3)

Both WP-2 (BFB cache) and WP-3 (bf.conf rendering) need files prepared on the Forge server then transferred to the target host before shell commands run. This requires a new engine-level abstraction:

```python
@dataclass
class FileTransferSpec:
    """Declares a file that must be prepared server-side and transferred to the host."""
    source_type: str          # "url" (download+cache) or "generated" (module produces it)
    source_url: str = ""      # For "url" type: HTTP URL to download
    cache_key: str = ""       # Cache filename (for BFB cache dedup)
    local_path: str = ""      # For "generated" type: path on Forge server
    remote_path: str = ""     # Target path on the SSH host
    variable_name: str = ""   # Variable name to inject with the remote path
```

The SSHEngine gains a `_prepare_files()` hook called before `apply()`:

1. `SSHModule.server_side_files(variables)` → `list[FileTransferSpec]`
2. For each spec:
   - `source_type="url"`: `BfbCacheService.get(cache_key, source_url)` → local path
   - `source_type="generated"`: module already wrote the file to `local_path`
3. SCP local_path → remote_path on host via paramiko SFTP
4. Inject `variables[spec.variable_name] = spec.remote_path`

This is an **additive** change to `SSHModule` base class (optional method) and `SSHEngine` (new hook in `apply()` before running shell commands).

### stage_detail — Engine Infrastructure (WP-5)

The SSHEngine already receives `db_session_factory` in `__init__()`. The change extends it to write `stage_detail` during `apply()`:

```python
def _update_stage(self, ctx: ModuleContext, detail: str):
    """Write stage_detail to ProjectModule row for poll-based UI."""
    if not self._db_factory:
        return
    try:
        db = self._db_factory()
        db.query(ProjectModule).filter(
            ProjectModule.id == ctx.module_id,
        ).update({"stage_detail": detail})
        db.commit()
    except Exception:
        logger.debug("stage_detail write failed for module %s", ctx.module_id)
```

Called at phase transitions: "Connecting...", "Planning...", "Pre-staging...", "Executing command 2/5...", "Waiting for SSH reconnect (30s)...", "Validating...", "Complete"/"Failed".

---

## 3. Design Details

### WP-1: install-rshim

**Inputs:** `bare_metal_host_id` (host), `install_mft` (bool, default true)
**Outputs:** `rshim_installed` (bool), `rshim_device_present` (bool), `mft_installed` (bool)
**Dependencies:** None (first step in pipeline)
**Connectivity risk:** None

**Plan commands:** Check `systemctl list-unit-files rshim.service` + `test -e /dev/rshim0/misc` → skip if already installed.

**Apply commands** (from `rshim_service.py` lines 498-504):
1. `sudo apt-get update -y`
2. `sudo DEBIAN_FRONTEND=noninteractive apt-get install -y rshim`
3. `sudo systemctl enable --now rshim`
4. Wait loop for `/dev/rshim0/misc` (20s timeout)
5. `sudo apt-get install -y linux-headers-$(uname -r)` (best-effort)
6. `sudo DEBIAN_FRONTEND=noninteractive apt-get install -y mft kernel-mft-dkms` (best-effort)
7. `sudo mst start`

**Error handling:** MFT install failure is non-fatal (output `mft_installed=false`). rshim install failure is fatal.

### WP-2: BFB cache integration

**Flash module change:** If `use_bfb_cache=true` (default), the flash module declares a `FileTransferSpec` for the BFB URL. The engine downloads to cache, SCPs to host, injects `_cached_bfb_remote_path`. `apply_commands()` uses the cached path instead of wget.

**Fallback:** If cache or SCP fails, fall back to existing direct wget/curl on host. This makes cache integration purely additive — it can never make flash less reliable.

### WP-3: render-bf-conf

**Port scope:** `BfbConfigGenerator` class + `_bfb_constants.py` + `_bfb_helpers.py` + 5 template files. Remove CLI dependencies (`cli.util.logging` → standard logging, `cli.config.model` → direct constructor args, `cli.util.errors` → `core.errors`).

**Adaptation:** `ensure_ssh_key()` reads from project SSH credentials instead of generating keys on the workstation. `hash_password()` runs `openssl passwd -6 -stdin` inside the Forge Docker container.

**Integration:** The module's `server_side_files()` method instantiates `BfbConfigGenerator`, calls `generate()`, returns `FileTransferSpec(source_type="generated", local_path=bf_cfg_path, remote_path="/tmp/bf.conf")`. Flash module reads `bf_conf_local_path` from wired output.

### WP-4: factory-reset-dpu

**Apply commands** (from `dpu_factory_reset_service.py` lines 319-348):
1. `sudo mst start`
2. Resolve MST device from PCI address via `mst status -v`
3. `sudo mlxconfig -d $MSTDEV -y reset`
4. `echo SW_RESET 1 > /dev/rshim0/misc` ← triggers reboot, drops connectivity

**Connectivity risk:** `connectivity_risk = True`, `reconnect_timeout = 600`. Pre-stage VF netplan fallback (same pattern as flash-dpu).

### WP-5: stage_detail

**Schema:** `ALTER TABLE project_modules ADD COLUMN stage_detail VARCHAR(255);`
**Response schema:** Add `stage_detail: str | None` to ProjectModule response models.

---

## 4. Implementation Plan

### Ordering

```
WP-5 (stage_detail)     ← infrastructure first
  │
  ├── WP-1 (install-rshim)    ← standalone, no file-transfer needed
  │
  ├── WP-2 (BFB cache) ──┐
  │                       ├── both need FileTransferSpec
  └── WP-3 (bf-conf) ────┘
  │
  └── WP-4 (factory-reset)    ← standalone, connectivity-risk pattern
```

### Effort Estimates

| WP | Size | Estimate | Can Parallelize? |
|----|------|----------|-----------------|
| WP-5 | S | 0.5 day | First (blocking) |
| WP-1 | S | 0.5 day | Yes (with WP-2) |
| WP-2 | M | 1.5 days | Yes (with WP-1) |
| WP-3 | L | 2-3 days | After WP-2 (needs FileTransferSpec) |
| WP-4 | S | 0.5 day | Yes (independent) |
| **Total** | | **~5-6 days** | |

### Optimal Parallel Execution (2 builders)

```
Builder A: WP-5 → WP-2 → WP-3 (module wiring)
Builder B: WP-1 → WP-4 → WP-3 (generator port + templates)
```

### Template Updates

New module order in both `bnk-bare-metal-dpu-infra` and `bnk-bare-metal-full-poc`:

```
1. bare-metal/install-rshim        (NEW, optional)
2. bare-metal/probe-dpu            (existing)
3. bare-metal/set-nic-mode         (existing, optional)
4. bare-metal/factory-reset-dpu    (NEW, optional)
5. bare-metal/render-bf-conf       (NEW, optional)
6. bare-metal/flash-dpu            (existing)
7. bare-metal/wait-dpu-ready       (existing)
8-13. ... (K8s bootstrap unchanged)
```

### Test Strategy

- **Unit tests:** All modules: `plan_commands()`, `apply_commands()`, `parse_plan_output()`, `parse_apply_output()` with mocked outputs
- **Unit tests:** `BfbConfigGenerator` port — test rendering for both topologies against golden files
- **Integration tests:** SSHEngine + FileTransferSpec with mock SSH session
- **Hardware tests:** Run on test server (10.176.11.91) — WP-1 and WP-4 are most critical to validate on real hardware

---

## 5. Non-Goals (Deferred)

| Capability | Why Deferred |
|-----------|-------------|
| Redfish discovery in blueprint probe | Requires BMC credential handling + HTTPS access pattern; blueprint system is SSH-only |
| BMC credential resilience (`0penBmc` fallback) | Only relevant when blueprints gain BMC access |
| Full BMC-managed topology in blueprints | Requires new engine variant (BMC-SSH); substantial architectural change |
| Jinja2 bf.conf templates (DB-backed, UI-editable) | Port poc-deployer `string.Template` first; Jinja2 as future enhancement |
| DPU OS probe via BMC ARP sweep | Blueprint `wait-dpu-ready` covers in-band path; ARP sweep is BMC-topology-specific |
| Post-BFB script execution (`post_bfb.sh`) | Existing `wait-dpu-ready` + `install-dpu-prereqs` cover critical post-flash steps |
