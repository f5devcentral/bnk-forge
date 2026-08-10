# DPU Provisioning Guide

End-to-end workflow for discovering, registering, flashing, and provisioning
NVIDIA BlueField-3 DPUs in bnk-forge. Covers both access modes:

- **BMC (out-of-band)** — DPU is managed through its own dedicated BMC
  (OpenBMC on a secondary ARM core, Redfish on TCP/443). Classic path; the
  BMC has its own management IP and SSH.
- **In-band** — DPU has no separate BMC management path, is reached
  entirely through the host OS it's plugged into via the `rshim` kernel
  module + userspace daemon on that host (`/dev/rshim0/*` character
  devices). The DPU OS becomes reachable on `tmfifo_net0` at the fixed
  IP `192.168.100.2` on the host side.

All operations honour the project's optional jumphost — every SSH or
Redfish call tunnels through it via paramiko `direct-tcpip` when one is
configured, with no per-action configuration.

---

## 1. Prerequisites

Before discovery:

1. **Project SSH credentials**, either saved (`SSHCredential` table; edit in
   *Settings → SSH Credentials*) or entered inline at the moment of
   discovery. These let bnk-forge reach either the DPU BMCs directly or
   the hosts where in-band DPUs live.
2. **Jumphost credential** (optional) — set on the project as
   `project.ssh_credential_id`. All discovery / register / probe / flash /
   reset / console actions automatically tunnel through it when set.
3. **DPU Project Settings** (DPU tab → *DPU Project Settings*):
   - A BlueField software image (BFB).
   - A bf.conf template (LAG or non-LAG variant).
   - DPU OS default username + password (auto-seeded to `ubuntu` +
     random 12-char password on new projects).
   - DNS + NTP servers, VLAN plan.

   **Pre-seeded on every fresh install**: the NVIDIA DOCA **2.2.2**
   BFB image and both bf.conf templates (LAG + non-LAG) are created in
   the database on `init_db` so new projects can flash immediately
   without a manual BFB upload. Replace the pre-seeded BFB via *DPU
   Project Settings → BFB image* to target a different DOCA release.

---

## 2. Node Discovery (project-wide IP scan)

*Project → Discovery tab → New Discovery*.

> **Not the same as the per-DPU "Discover" action** (section 4 below).
> Node Discovery runs **once per project**, classifies raw IPs into
> BMC / DPU-OS / host-with-DPU, and populates the **DiscoveredNode**
> table so you can click **+ Register DPU** and put those IPs under
> management. The per-DPU **Discover** button in section 4 runs
> against DPUs that are *already registered* in the DPU tab and
> collects rich inventory (Redfish for BMC DPUs, rshim+lspci+mlxconfig
> for in-band DPUs).

### 2.1 What gets discovered

Node Discovery hits each IP in the provided list / range on **two
transports** in parallel:

1. **HTTPS / Redfish service-root** on TCP/443 — if the target
   responds with a BlueField `Manager`, the IP is a DPU BMC. Works on
   port-443-reachable IPs even before any SSH credential is valid, and
   is what lets Node Discovery classify an IP as `is_dpu_bmc` without
   needing SSH access to the BMC.
2. **SSH** on TCP/22 (or the port you specify) — used to log into the
   target and collect OS / host facts. If the target is an x86 host,
   bnk-forge runs `lspci` to look for BlueField-3 PCIe device IDs and
   captures a `dpu_details` entry per DPU found.

The combined result is recorded on the `DiscoveredNode` row as three
independent flags:

| Flag | Signal | Meaning |
|---|---|---|
| `is_dpu_bmc` | Redfish service-root responds on TCP/443 with a BlueField manager | The IP *is* the DPU BMC. |
| `is_dpu_node` | aarch64 + `/etc/mlnx-release` or `/etc/bf-release` | The IP *is* the DPU's own ARM OS (running standalone). |
| `is_dpu_host` | x86_64 + BF3 PCIe device IDs via `lspci` | The IP is an **x86 host with one or more DPUs plugged in** — candidates for in-band management. |

Host-side DPU detail (per entry in `dpu_details`):
- `pci_address` — e.g. `00:10`
- `description` — lspci description
- `soc_mgmt_available` — whether the DPU exposes the SoC Management Interface

Both transports honour the project jumphost: Redfish tunnels via
`direct-tcpip` + TLS-over-MemoryBIO, SSH via the standard paramiko
`sock=chan` pattern. Configure the jumphost once at the project level
and every IP in the scan inherits it.

### 2.2 Registering DPUs to the project

Every discovered node with at least one DPU signal exposes a
**+ Register DPU** button. A batch **+ Register all DPUs** above the
list handles every eligible node in one click.

Behaviour by node type:

- **BMC node** → creates a `Dpu` row with `access_mode="bmc"` and
  `bmc_ip = node.ip_address`. The BMC password captured by discovery is
  seeded as the project's default OOB password (first time only).
- **Host node** → creates **one in-band `Dpu` row per entry in
  `dpu_details`**, keyed by `(project_id, host_node_ip, pci_address)`
  so re-registration is idempotent. The discovery SSH credentials are
  **snapshotted inline** onto each DPU row
  (`host_ssh_username` + encrypted password / private key) so later
  actions don't require the operator to pick a saved SSHCredential.

### 2.3 In-band DPU identity

In-band DPUs show an amber **in-band** badge and the host's hostname
(falling back to the host IP when the hostname wasn't captured) on the
DPU tab. The Serial# column shows `(pci_address)` since there's no
BMC-reported serial. The Uplink Interfaces table shows
`hostname (pci)` for readability.

---

## 3. Installing rshim + MFT (in-band only)

In-band DPUs need the rshim kernel module + userspace daemon and the
Mellanox Firmware Tools (MFT) package installed on their host before
any in-band probe / flash / reset / console action works.

*DPU tab → row Actions → Install rshim + MFT…*

The dialog runs a Celery task asynchronously — it closes immediately
so you can queue installs on multiple DPUs in parallel. Step-by-step
progress is written back to the DPU row and appears under the
**running…** badge as:

```
Installing kernel headers (for DKMS)…
Installing MFT + kernel-mft-dkms (compiles kernel module, ~2–5 min)…
Starting mst (creates /dev/mst/*)…
```

Commands run on the host (via SSH, through the jumphost when
configured, as the project-saved credential — passwordless sudo
required):

```bash
sudo apt-get install -y linux-headers-$(uname -r)
sudo apt-get install -y rshim
sudo systemctl enable --now rshim
# wait for /dev/rshim0/misc to appear
sudo apt-get install -y mft kernel-mft-dkms    # best-effort
sudo mst start                                  # best-effort
```

On success the task immediately chains a **Discover** so the row flips
`running → ok` automatically once rshim is up.

**If no saved SSH credential is linked to the DPU** (e.g. discovery
ran with inline credentials), the dialog opens a credential picker:
either pick an existing saved credential or **Create new credential**
(pre-filled with the DPU's stored host user + port; you supply the
password or private key; bnk-forge creates a new `SSHCredential` and
links it to this DPU in one click).

---

## 4. Per-DPU Discover (inventory probe)

*DPU tab → Actions → Discover* (or **Discover All** in the header).

> Different from *Node Discovery* (section 2). This action runs
> against a single `Dpu` row that's already registered under the
> project — it doesn't scan IP ranges. Its job is to populate the
> expanded "inventory" view (serial, firmware, interfaces, MACs,
> NicMode / HostPrivilegeLevel badges, mlxconfig settings) so the UI
> shows at-a-glance "what does this DPU actually have?"

### 4.1 BMC DPUs

Runs the BlueField Redfish probe against `https://<bmc_ip>/redfish/v1/…`.
Captures full Redfish inventory: system identity, BIOS attributes
(NicMode, HostPrivilegeLevel, InternalCPUModel, FieldMode, …), manager
info, firmware versions, and network interface list with MACs + link
status. Surfaces NicMode + HostPrivilegeLevel as badges, serial #,
`oob0_mac`.

### 4.2 In-band DPUs

SSHes to the host, collects via three sources (all run in one SSH
session):

- `/dev/rshim0/misc` (DISPLAY_LEVEL 2) → OPN, DEV_INFO, BF mode, boot
  mode, up-time, UUID.
- `lspci -vvv -s <pci>` → PCI description, VPD Part Number + Serial if
  exposed, subsystem line ID.
- `/sys/bus/pci/devices/<pci>.0/{vendor,device,subsystem_*}` → numeric
  PCI + subsystem IDs.
- `mlxconfig -d <mst-device> q` → Device type, Description, and every
  **Configurations: KEY → value** row the DPU firmware exposes
  (INTERNAL_CPU_MODEL, LINK_TYPE_P*, SRIOV_EN, NUM_OF_VFS,
  LAG_RESOURCE_ALLOCATION, HOST_CHAINING_*, NVME emulation, …).

Surfaces:
- **Identity section**: SKU / OPN, Description, Device type, Serial
  number, Part number (VPD), MST device, PCI ID, Subsystem ID, PCIe
  description.
- **Runtime / firmware** section.
- **mlxconfig settings** table — full KEY/value list, scrollable.
- **Raw command output** blocks (collapsible): rshim misc, lspci,
  mlxconfig, sysfs.

Since rshim working is itself proof that the host has privileged +
trusted DPU access, in-band rows derive `NicMode = Trusted`,
`HostPrivilegeLevel = Privileged` automatically (no Redfish needed).

---

## 5. Preview bf.conf

*DPU tab → Actions → Preview bf.conf…*

Renders the selected bf.conf template (LAG or non-LAG) with the
project's VLAN + credential + NTP + DNS context. Access-mode-agnostic —
the file itself is identical for BMC and in-band DPUs; only the
transport to `/dev/rshim0/boot` differs at flash time.

Error messages name the exact missing field (e.g. `No bf.conf template
selected`, `DPU has no uplink IP addresses yet — click Auto-assign`)
and point at the UI action to fix it.

---

## 6. Flash BFB + FW + CFG

*DPU tab → Actions → Flash BFB+FW+CFG* (confirm dialog).

Five stages, all persisted on the DPU row so the UI shows progress:

1. **Rendering bf.conf** — Jinja2 + bash `-n` + YAML heredoc lint gate.
2. **Caching BFB** — downloads from the project's configured base URL
   into a container-local cache the first time.
3. **Resetting into BFB-receive mode** — `echo 'BOOT_MODE 1' …` +
   `echo 'SW_RESET 1' > /dev/rshim0/misc`.
4. **Uploading** — streams BFB + rendered bf.cfg to `/dev/rshim0/boot`.
5. **Waiting for reboot** — polls the DPU OS reachability.

### 6.1 Transport per access mode

| Stage | BMC DPU | In-band DPU |
|---|---|---|
| SSH target | `bmc_ip` (OpenBMC root user) | `host_node_ip` (saved SSH cred) |
| SW_RESET | `echo … > /dev/rshim0/misc` (root on BMC) | `sudo -n sh -c 'echo … > /dev/rshim0/misc'` on host |
| BFB upload | SFTP → `/dev/rshim0/boot` (BMC is root, SFTP works) | `sudo -n dd of=/dev/rshim0/boot bs=1M` with byte stream piped to stdin — SFTP would hit EACCES on the root-only rshim device |
| Post-flash poll | BMC ARP-sweeps its own subnet to find the DPU OS IP, then SSH-verifies | Opens a nested SSH from the host through a `direct-tcpip` channel to `192.168.100.2:22` over tmfifo, verifies there |

### 6.2 bf.conf hostname

The rendered bf.conf bakes a hostname onto the DPU:

- BMC DPUs: `dpu.serial_number` (Redfish-reported).
- In-band DPUs: `{host_hostname}-dpu` (e.g. `worker1-dpu`, `worker2-dpu`),
  derived from `Dpu.host_hostname` populated by register-from-discovery.
  Fallback: `dpu-<id>`.

### 6.3 Jumphost

All five stages use `open_inband_host_ssh()` (in-band) or the BMC SSH
tunnel helper (BMC), both of which consume the project's jumphost
credential via `resolve_project_jumphost_cred()` when set. Flashing
through a jumphost is a 2-hop tunnel (forge → jumphost → host) with
the SFTP / exec channels riding the paramiko Transport — no extra
configuration.

---

## 7. Probe DPU OS

*DPU tab → Actions → Probe DPU OS*.

### 7.1 BMC DPUs

1. SSH into the BMC with BMC credentials.
2. Read the BMC's own eth0 MAC + /24 subnet.
3. Ping-sweep the subnet; match the BlueField-3 sequential MAC layout
   (BMC_MAC - 1 = DPU-OS MAC) or any Mellanox OUI MAC ≠ BMC's.
4. SSH into the discovered DPU-OS IP with project DPU-OS credentials.
5. Capture `uname -a`, `hostname`, `ip -br addr show`, IPv4/IPv6
   routes; update `dpu_os_ip`, `dpu_os_status`, `last_discovery_payload.dpu_os`.

### 7.2 In-band DPUs

1. SSH to the host (via jumphost if configured).
2. Open a paramiko `direct-tcpip` channel from the host SSH Transport
   to `192.168.100.2:22` — the fixed tmfifo IP baked into the bf.conf
   netplan template.
3. Nested SSH as `ubuntu` (project DPU-OS credentials) via that
   channel. No ARP sweep is needed — the tmfifo address is fixed.
4. Run the same 5-section probe (`uname`, `hostname`, `ip addr`,
   routes).
5. Extract `oob_net0`'s MAC from `ip -br addr show` → populates
   `Dpu.oob0_mac` from the DPU itself.

On reboot the same probe is used by the post-flash poller — the row
flips `waiting_reboot → os_online` automatically once the new DPU OS
is up.

---

## 8. Reset options

*DPU tab → Actions → Reset → …*

| Label | BMC DPU | In-band DPU |
|---|---|---|
| Graceful Restart | Redfish `ComputerSystem.Reset` type `GracefulRestart` | Nested SSH to `192.168.100.2` → `sudo reboot` |
| Force Restart | Redfish `ForceRestart` | `sudo -n sh -c 'echo SW_RESET 1 > /dev/rshim0/misc'` on host (rshim SoC reset) |
| Power Cycle | Redfish `PowerCycle` (off → on, flaps PCIe) | `sudo -n mlxfwreset -d <mst> -l 3 --sync 1 -y reset` — PCI reset, flaps PCIe link to host |
| Reset to Defaults… | Layered: Redfish `Bios.ResetBios` + `Manager.ResetToDefaults` + DPU-OS `mlxconfig reset` | `sudo mlxconfig -d <mst> -y reset` on host + rshim SW_RESET. BIOS + BMC rows are hidden (no equivalent in-band) — only the NIC firmware layer is available, defaulting to **OFF** because it's disruptive |

> **Note**: mlxfwreset may refuse with `"The tool is not supported on
> virtual machines"` on BF3 hosts with
> `INTERNAL_CPU_MODEL=EMBEDDED_CPU` (it misreads some PCIe flags).
> This is a known BlueField-3 quirk, not a bnk-forge bug; use Force
> Restart instead, or change the CPU model via mlxconfig.

---

## 9. Terminal access

Three modes in the Actions menu — all use xterm.js + WebSocket backed
by paramiko PTYs:

### 9.1 Serial Console (rshim)

Streams `/dev/rshim0/console` — the BlueField U-Boot / UEFI /
pre-login serial stream. Useful for watching the installer after Flash,
or catching a hang.

- **BMC DPUs**: SSH to BMC → raw PTY with `stty raw; exec 7<>/dev/rshim0/console`
  (kicks stale holders with `fuser -k`, retries the open until the rshim
  driver releases the device).
- **In-band DPUs**: same pipeline wrapped in `sudo -n bash -c '…'` on
  the host.

### 9.2 SSH to BMC

Plain interactive SSH shell into the DPU BMC (OpenBMC on the ARMv7
secondary core). Not available for in-band DPUs (no BMC). Nvidia
default password fallback (`0penBmc`) on authentication failure.

### 9.3 SSH to DPU (DPU OS)

Interactive SSH shell into the DPU's main-core Linux (Ubuntu).

- **BMC DPUs**: direct SSH to the discovered `dpu_os_ip`, via jumphost
  if configured.
- **In-band DPUs**: **nested 2-hop (or 3-hop with jumphost) tunnel**
  — forge → [jumphost →] host → `192.168.100.2` over tmfifo.
  `open_inband_host_ssh` provides the first leg; a second paramiko
  `direct-tcpip` channel opens the second leg on port 22 of the DPU
  tmfifo IP; the final SSH runs as the project's DPU-OS user through
  that channel.

Requires Probe DPU OS to have populated `dpu_os_ip` + `dpu_os_status =
reachable` — otherwise the menu item is disabled.

---

## 10. Jumphost model

Every SSH / Redfish / SFTP / WebSocket action respects
`project.ssh_credential_id` automatically:

- **Redfish via jumphost** — `services/dpu_tunnel.py:bmc_http_request()`
  opens an SSH to the jumphost, issues an HTTP/1.1 request on a
  paramiko `direct-tcpip` channel, wraps it in TLS via `ssl.MemoryBIO`
  (because paramiko Channels don't satisfy `ssl.wrap_socket()`'s
  socket protocol), and returns the decoded response.
- **SSH via jumphost** — jumphost SSH session + `direct-tcpip` channel
  to the target; the target SSH runs over that channel with `sock=chan`.
- **Nested tunnel (in-band SSH to DPU)** — jumphost → host (first
  channel) → DPU OS 192.168.100.2 (second channel), paramiko
  Transports nest cleanly.

No per-action configuration is needed. The jumphost applies to
discovery, rshim install, inventory probe, flash, OS probe, reset,
serial console, SSH-to-BMC, and SSH-to-DPU — one switch at the
project level.

---

## 11. Troubleshooting

### 11.1 Discover fails with "running…" stuck

Row status stuck on `running`. Celery worker logs:

```
docker logs bnk-forge-celery-worker --tail 100
docker logs bnk-forge-celery-worker-2 --tail 100
```

Hot reset the row:

```sql
UPDATE dpus SET last_discovery_status = NULL WHERE id = <id>;
```

### 11.2 In-band Discover reports "rshim is not ready"

The host doesn't have `/dev/rshim0/*` yet. Click **Actions → Install
rshim + MFT** on the DPU row. If that fails:

- **"does not use apt"** — host is not Ubuntu/Debian. Install rshim
  manually for your distro, then Discover again.
- **"apt install rshim failed"** — usually either no passwordless sudo
  for the SSH user, or the NVIDIA DOCA apt repo isn't configured so
  `rshim` isn't in the package index. Add the DOCA repo per
  NVIDIA's documentation, then retry.
- **"systemctl enable --now rshim failed"** — passwordless sudo again,
  or the kernel module failed to load (check `dmesg` on the host).

### 11.3 In-band Flash fails with "Permission denied"

If you see `[Errno 13] Permission denied: '/dev/rshim0/boot'`, you've
likely installed a bnk-forge build before the sudo-dd fix landed. The
in-band flash path writes via `sudo -n dd of=/dev/rshim0/boot bs=1M`
and requires passwordless sudo for the SSH user on the host.

### 11.4 mlxconfig returns empty data

Even after **Install rshim + MFT** completes, the inventory probe may
show empty FW fields because `mst start` failed (e.g. DKMS build
failed for the running kernel). Check the raw `mst status -v` block
in the expanded inventory view — if the MST PCI kernel module isn't
loaded, reinstall `kernel-mft-dkms` after confirming
`linux-headers-$(uname -r)` is installed.

### 11.5 Preview bf.conf fails

Every failure names the exact missing field and the UI action to fix
it. Common cases:

- "No bf.conf template selected" → DPU Project Settings → pick the
  LAG or non-LAG template → Save defaults.
- "DPU has no uplink IP addresses yet" → DPU Uplink Interfaces table
  → **Auto-assign**.
- "DPU Project Settings is missing DPU OS username/password" — seed
  them (ubuntu/random is the default on fresh projects).

### 11.6 Power Cycle on in-band fails

`mlxfwreset` refuses with "not supported on virtual machines" on BF3
hosts with `INTERNAL_CPU_MODEL=EMBEDDED_CPU`. Known NVIDIA tool quirk
(misreads PCIe flags). Workarounds:

- Use **Force Restart** — does a clean rshim SW_RESET of the DPU
  without flapping the host PCIe.
- Change `INTERNAL_CPU_MODEL` via `mlxconfig` and reboot.

### 11.7 Serial console stays blank

The rshim console device is exclusively-opened — any stale `cat` or
`screen` left over from a crashed session holds it. The bnk-forge
serial-console pipeline already tries `fuser -k /dev/rshim0/console`
to clear stale holders, but if it persists, SSH to the host/BMC and
run it manually:

```bash
sudo fuser -k /dev/rshim0/console
```

### 11.8 Viewing the raw commands bnk-forge ran

The expanded inventory view of any in-band DPU always shows **four
collapsible raw blocks** with the literal output from:

- `/dev/rshim0/misc` (DISPLAY_LEVEL 2)
- `lspci -vvv`
- `mlxconfig -d <mst> q`
- `/sys/bus/pci/devices/<pci>`

These are your fastest path to diagnosing "why isn't field X showing
up?".

---

## 12. End-to-end cheat sheet (in-band)

```text
  Discovery tab
    ├── Enter host IPs + SSH cred (or pick saved)
    └── + Register all DPUs
            │
            ▼ creates 1 DPU row per entry in dpu_details
  DPU tab
    ├── (optional) Install rshim + MFT     ← if rshim isn't on the host yet
    ├── Discover                           ← populates SKU + mlxconfig settings + oob0 MAC
    ├── DPU Project Settings → Save       ← BFB + bf.conf + VLAN plan + OS creds
    ├── DPU Uplink Interfaces → Auto-assign
    ├── Preview bf.conf…                   ← optional, verify the rendered file
    ├── Flash BFB+FW+CFG                   ← host SFTP via sudo-dd, SW_RESET via sudo sh
    │      └── post-flash poller walks through tmfifo to verify DPU OS came up
    └── Serial Console / SSH to DPU        ← rshim PTY + nested 2-hop SSH tunnel
```

And for BMC DPUs the same flow works — just skip the rshim install step and
everything transparently uses Redfish + SFTP-to-BMC instead of host SSH +
sudo-dd.
