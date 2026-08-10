# DPU Deployment: Blueprint Modules vs PR #46 DPU Services — Analysis

> Comparative analysis of the two DPU deployment approaches in BNK Forge.
> Date: 2026-04-22
> Branch: feat/bare-metal-deploy-v2
> Related: PR #44 (blueprint system), PR #45 (SSH engine fixes), PR #46 (DPU provisioning)

## 1. Origins and Scope

### Blueprint Modules (PRs #44, #45)
- Ported from `bnk-poc-deployer` shell scripts and Python CLI (`feat/native-python` branch)
- 14 composable SSH modules in `backend/modules/bare_metal/`
- Covers the **regular** and **bf3** deployment topologies from poc-deployer
- Full end-to-end pipeline: probe → flash → K8s bootstrap → DPU join → labels/taints
- Orchestrated by `backend/services/execution/ssh_engine.py` with plan/apply/validate lifecycle
- Blueprint templates in `backend/data/stack_templates.json` (`bnk-bare-metal-dpu-infra`, `bnk-bare-metal-full-poc`)

### PR #46 DPU Provisioning Services
- Built independently as Forge-native services (not ported from poc-deployer)
- Purpose-built services in `backend/services/dpu_*.py` + `backend/services/bare_metal/bluefield/`
- Covers a **fourth topology**: BMC-managed DPUs accessed via Redfish + SSH to BMC (distinct from poc-deployer's BMC topology which runs from the host)
- Focuses on DPU hardware lifecycle: discovery, rshim install, flash, factory reset, OS probe
- Does NOT cover K8s bootstrap, CNI, SR-IOV, storage, kubeadm, or cluster join
- Celery async + DB state machine + REST API (`backend/routes/dpus.py`) + full DPU tab UI

## 2. Topology Coverage

| Topology | poc-deployer | Blueprint Modules | PR #46 |
|----------|-------------|-------------------|--------|
| **regular** (host has independent NIC) | ✅ shell + Python | ✅ ported | ❌ |
| **bf3** (host networking via BF3 PF/VF) | ✅ shell + Python | ✅ ported | ❌ |
| **bmc** (poc-deployer: Redfish from host + SSH) | ✅ shell (deploy-bmc.sh) | ❌ not ported | N/A |
| **BMC-managed** (PR #46: SSH to BMC, SFTP to BMC rshim) | ❌ different approach | ❌ | ✅ |

Key distinction: poc-deployer's BMC topology (`deploy-bmc.sh`) orchestrates from the *host* using Redfish for BIOS/power and SSH for flash. PR #46's approach SSHes directly into the *BMC* for SFTP-based flash, which is a genuinely different access pattern (a fourth topology).

## 3. Module-by-Module Comparison

### 3.1 Flash (BFB)

**Blueprint `flash_dpu.py`** (from poc-deployer):
- Uses `bfb-install` tool (Nvidia's official flash utility)
- `bfb-install` handles SW_RESET internally — no manual BOOT_MODE/SW_RESET needed
- Supports optional `bfb_config_url` for bf.conf
- Connectivity-risk pattern: pre-stages VF netplan fallback before flash
- Fallback: `cat $BFB > /dev/rshim0/boot` if bfb-install unavailable

**PR #46 `dpu_flash_service.py`**:
- Bypasses `bfb-install` entirely
- Manually issues `BOOT_MODE 1` + `SW_RESET 1` to put DPU in BFB-receive mode (required because it writes directly to rshim)
- Streams BFB + rendered bf.conf concatenation via SFTP (BMC path) or `dd` (in-band)
- No connectivity preservation (assumes BMC access is stable)
- Rich stage machine: preparing → rendering bf.conf → caching BFB → resetting DPU → uploading (42%) → waiting_reboot

**Why PR #46 needs SW_RESET but we don't**: `bfb-install` performs the SW_RESET as part of its internal procedure. PR #46 bypasses `bfb-install` because the BMC topology requires SFTP-based streaming to the BMC's `/dev/rshim0/boot`, which `bfb-install` doesn't support (it only works on the local host).

### 3.2 bf.conf Generation

**poc-deployer Python** (`cli/phases/phase1/bfb_config.py`, 804 lines):
- `BfbConfigGenerator` class with `string.Template` rendering
- Supports regular and bf3 topologies
- Per-DPU: hostname, IP, rshim MAC, OVS port names
- SHA-512 password hashing (via openssl), SSH key generation/embedding
- DNS/resolv.conf injection, containerd config, grub cmdline
- Generates bf.cfg + post_bfb.sh per DPU
- Version profile integration (RUNC_VER, CTRD_VER, K8S_VER, PAUSE_IMAGE_TAG)

**Blueprint modules**: Accept a `bfb_config_url` parameter but have NO built-in renderer. The poc-deployer's `BfbConfigGenerator` was NOT ported.

**PR #46** (`bf_conf_renderer.py`, 382 lines):
- Jinja2 rendering (via Ansible-compatible `hostvars` context)
- Per-DPU VLAN IP auto-assignment from project subnet templates
- SHA-512 password hashing, SSH authorized_keys injection
- DNS/NTP configuration from project DPU settings
- bf.conf templates stored in DB (`BfConfTemplate` model) — UI-editable
- Lint validation: `bash -n` + YAML heredoc extraction + strict YAML parse

**Assessment**: Both the poc-deployer and PR #46 have rich bf.conf rendering. The poc-deployer's version uses `string.Template` and is topology-aware (regular vs bf3 OVS layouts). PR #46's version uses Jinja2 and is DB-backed (templates editable in UI). Neither was ported to blueprints.

### 3.3 Discovery / Probe

**Blueprint `probe_dpu.py`** (from poc-deployer host probes):
- Host SSH: `mst start`, `mlxconfig q INTERNAL_CPU_MODEL`, rshim check, DPU SSH (192.168.100.2), VF count, default route interface, internet check
- Outputs: flat dict fed to downstream modules

**PR #46** (`dpu_discovery_service.py` + `bluefield/probe.py` + `bluefield/inventory.py`):
- Dual access modes: BMC (Redfish HTTPS) and in-band (host SSH + rshim + lspci)
- BMC: serial number, UUID, part number, power state, all Ethernet interfaces (MAC/IP/DHCP/link), full BIOS settings (NicMode, HostPrivilegeLevel, InternalCPUModel, FieldMode, SMMU, OPTee...), 9 firmware component versions
- In-band: lspci + mlxfwmanager + rshim misc + existing host-side probes
- Rich typed model: `DpuInventory` with nested dataclasses
- Credential resilience: `0penBmc` fallback + auto-PATCH of root password

**Assessment**: Both serve different purposes. Blueprint probe gives "enough to decide if flash is needed." PR #46 discovery gives a full hardware inventory for management UI. Both are valuable; they're not competing.

### 3.4 Remaining Modules (K8s Bootstrap)

These 8 modules exist ONLY in the blueprint system (ported from poc-deployer phases 2-3):
- `install_dpu_prereqs.py` — containerd, kubeadm, kubelet on arm64 DPU
- `install_k8s_prereqs.py` — same for amd64 host
- `kubeadm_init.py` — cluster bootstrap + auto-registration contract
- `install_cni.py` — Calico CNI
- `install_sriov.py` — SR-IOV operator
- `install_storage.py` — local-path provisioner
- `kubeadm_join.py` — DPU joins cluster via join command
- `label_dpu_node.py` + `taint_dpu_node.py` — BNK-specific node preparation

PR #46 has NONE of these. It stops at "DPU OS is online."

## 4. Novel Capabilities in PR #46

### 4.1 Factory Reset
Three independent reset layers: NIC (`mlxconfig reset` + `mlxprivhost`), BIOS (`Bios.ResetBios` via Redfish), BMC (`Manager.ResetToDefaults` via Redfish). Correct execution order: NIC first (while OS responsive), then BIOS, then PowerCycle, then BMC last.

**In-band variant**: `mlxconfig reset` on host MST device + rshim `SW_RESET 1`. Note: the in-band factory reset issues SW_RESET which WILL drop connectivity if host networking depends on DPU VFs. The service doesn't pre-stage connectivity preservation — the existing blueprint `connectivity_risk` pattern would need to wrap this.

### 4.2 BFB Cache Service (`bfb_cache_service.py`, 273 lines)
On-disk cache at `/app/bfb-cache` with fcntl file locking for concurrent access, SHA-256 integrity sidecar files, LRU eviction at 20 GiB cap, atomic rename pattern (no partial files served). Avoids re-downloading 2 GiB BFB files on retries or multi-DPU flashes.

### 4.3 DPU OS Probe (`dpu_os_probe_service.py`)
**BMC path**: SSH into BMC → read BMC's eth0 MAC → derive DPU OS MAC (BMC_MAC - 1, a BlueField-3-specific sequential allocation trick) → ping-sweep the /24 → find DPU OS IP from ARP table → SSH to discovered IP for uname/hostname/ip addr/routes.

**In-band path**: SSH to host → direct-tcpip channel to 192.168.100.2:22 (tmfifo) → SSH through that channel as ubuntu. No ARP sweep needed since the address is fixed.

**Same-device MAC filtering**: Prevents cross-contamination when multiple DPUs share a BMC L2 segment. Falls back to Mellanox OUI matching when the MAC-delta trick doesn't find a match.

### 4.4 BMC Credential Resilience (`dpu_credentials.py`)
On auth failure (SSH or Redfish 401), automatically retries with Nvidia's factory default password `0penBmc` (not a typo — zero, not O). On successful fallback, asynchronously PATCHes the BMC's `root` account back to the user's configured password via Redfish `/redfish/v1/AccountService/Accounts/root`. Non-fatal: if the PATCH fails, the fallback result is still returned.

### 4.5 rshim Install Service (`rshim_service.py`, 793 lines)
Full async rshim installation: `apt-get install rshim kernel-mft-dkms` + `mst start`, with status probing, Celery async execution (15 min timeout), and automatic chaining to Discovery on success.

### 4.6 DPU Management UI
Full DPU tab: DpuPanel, DpuFormDialog, DpuInventoryDetail, DpuInterfaceTable, DpuProjectSettingsCard, DpuTerminalDialog, RshimInstallDialog. Project-level DPU settings (BFB image, bf.conf template, VLAN subnets, DNS/NTP).

## 5. Blueprint Step State Visibility

The question was raised whether blueprint module execution can show per-step state the way PR #46 does.

**Current blueprint state model**: `ProjectModule.status` tracks overall module state (`not_initialized` → `initialized` → `planned` → `applied` → `destroyed`). The SSHEngine streams command output via `on_output` callback in real-time. However, there is no per-command stage tracking like PR #46's `flash_stage_detail`.

**PR #46 state model**: `Dpu.flash_status` + `Dpu.flash_stage_detail` + `Dpu.flash_error` give fine-grained stage tracking (preparing → rendering bf.conf → caching BFB → resetting DPU → uploading BFB+bf.conf (42%) → waiting_reboot). The UI polls this.

**Path forward**: The `ProjectModule` model already has `plan_output` (Text) and `deployment_error` (Text). We could add a `stage_detail` column (VARCHAR 255) and have the SSHEngine update it as each command runs. The streaming output already works; the DB-backed stage would enable poll-based UI updates too. This is an additive schema change.

## 6. Recommendations

### Adopt from PR #46 (ordered by priority):
1. **rshim install service** — use PR #46's `rshim_service.py` directly (it's a pre-requisite step, not topology-specific)
2. **BFB cache** — integrate `bfb_cache_service.py` for the download step in `flash_dpu.py` (avoid re-downloading on retries)
3. **bf.conf rendering** — port poc-deployer's `BfbConfigGenerator` into a blueprint module (it's topology-aware, unlike PR #46's Jinja2 version which is BMC-focused). Consider also supporting PR #46's DB-backed Jinja2 templates as an alternative source.
4. **Factory reset** — create a new `factory-reset-dpu` blueprint module wrapping PR #46's `DpuFactoryResetService` (useful for DPUs in unknown state). Must add connectivity-risk handling for in-band topology.
5. **Step stage tracking** — add `stage_detail` to `ProjectModule` for blueprint-style per-step progress

### Keep from Blueprint/poc-deployer:
- `bfb-install` as the flash method for regular/bf3 topologies (handles SW_RESET internally)
- Full K8s bootstrap pipeline (phases 2-3)
- Connectivity-risk VF netplan pre-staging
- Plan/apply/validate lifecycle with skip logic
- Post-flash recovery from poc-deployer (watcher pattern from deploy-bmc.sh)

### Future phases:
- Redfish-based discovery (extend probe module with optional BMC path)
- BMC credential resilience for modules that need BMC access
- Full BMC-managed topology support in blueprint system

## 7. What is NOT a gap (clarifications)

1. **SW_RESET in flash module**: NOT needed. Our module uses `bfb-install` which handles it internally. PR #46 needs it because it bypasses `bfb-install` for SFTP-based streaming.
2. **BMC topology**: This is a FOURTH topology, not a missing implementation of poc-deployer's BMC topology. Poc-deployer's BMC flow runs from the host; PR #46's runs from the BMC's own SSH interface. Both are valid approaches for different environments.
3. **DPU CRUD/UI**: PR #46's DPU management tab is complementary to blueprints, not competing. Blueprints handle deployment automation; the DPU tab handles per-DPU state inspection and management.
