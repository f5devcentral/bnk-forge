# vm-bnk-forge

Spin up a fresh Ubuntu 24.04 KVM VM on a Linux host that auto-installs
bnk-forge from a configured git branch on first boot — fully unattended.

```
host                                  VM (bnk-forge-N)
─────────────────                     ──────────────────
make-vm.sh ─┐                         Ubuntu 24.04 minimal cloudimg
            ├─→ render user-data ──→  cloud-init (NoCloud datasource on /dev/vdb)
            ├─→ build seed.iso         ├─ install docker
            ├─→ qemu-img COW disk      ├─ deploy SSH keys + GitHub deploy key
            └─→ virt-install           └─ enable bnk-forge-bootstrap.service
                                            └─ git clone --branch <BRANCH>
                                                └─ make install   (~6 min total)
```

VMs bridge onto the host's bridge interface, get DHCP from the LAN, and
serve bnk-forge on `https://<vm-ip>:8443` (login `admin`; the password is generated on first boot — see Default credentials below).
Port 8443, not 443: `make install` deploys the server topology, where the
proxy runs with `network_mode: host` and binds 8443 (HTTPS) / 8082 (HTTP
redirect) directly — see the `proxy` service in `docker-compose.yml`.

`make-vm.sh` is **Linux + KVM only** — it refuses to run elsewhere and points
at `render-cloud-init.sh`, which runs anywhere (macOS included) and produces
user-data for a cloud provider. See [Cloud deployment](#cloud-deployment-no-local-kvm).

## Setup on a fresh host

One-time setup of the host. Skip any step you've already done.

### 1. Install KVM + libvirt + cloud-init tooling

```bash
sudo apt install -y qemu-kvm libvirt-daemon-system libvirt-clients \
                    bridge-utils virtinst libosinfo-bin \
                    cloud-image-utils genisoimage
sudo systemctl enable --now libvirtd
sudo usermod -aG libvirt,kvm "$USER"   # then log out and back in
```

Optional UI: `sudo apt install -y cockpit cockpit-machines` then visit
`https://<host>:9090`.

### 2. libvirt network in bridge mode

This VM tooling expects a libvirt network named `default` that bridges to a
real bridge interface on the host (so VMs get LAN IPs via DHCP). Replace
`br0` below with whatever bridge you already have on the LAN.

```bash
sudo virsh net-destroy default 2>/dev/null   # remove the stock NAT default
sudo virsh net-undefine default 2>/dev/null
cat > /tmp/net.xml <<'EOF'
<network>
  <name>default</name>
  <forward mode="bridge"/>
  <bridge name="br0"/>
</network>
EOF
sudo virsh net-define /tmp/net.xml
sudo virsh net-autostart default
sudo virsh net-start default
```

To use a different bridge, set `VM_NETWORK=<libvirt-net-name>` in `config.env`.

### 3. libvirt storage pool

```bash
sudo virsh pool-define-as default dir --target /var/lib/libvirt/images
sudo virsh pool-build default
sudo virsh pool-start default
sudo virsh pool-autostart default
```

### 4. Download the Ubuntu 24.04 minimal base image

Not committed in the branch (it's ~280 MB):

```bash
curl -fL -o /tmp/base.qcow2 \
  https://cloud-images.ubuntu.com/minimal/releases/noble/release/ubuntu-24.04-minimal-cloudimg-amd64.img
sudo install -o libvirt-qemu -g kvm -m 0644 /tmp/base.qcow2 \
  /var/lib/libvirt/images/ubuntu-24.04-minimal-base.qcow2
```

### 5. Generate a deploy keypair and register its public half on GitHub

Deploy keypairs are **operator-local** — nothing in `keys/` is committed.
Each operator generates their own pair and registers the public half on the
repo. This keeps key rotation and revocation in the operator's hands and
avoids an implicit "canonical" deploy key in the repo.

```bash
mkdir -p vm-bnk-forge/keys
ssh-keygen -t ed25519 -N '' \
  -f vm-bnk-forge/keys/bnk-forge-deploy \
  -C "bnk-forge-vm-deploy@$(hostname -s)"
cat vm-bnk-forge/keys/bnk-forge-deploy.pub
```

Paste the printed public line into:
**https://github.com/f5devcentral/bnk-forge/settings/keys/new**

- **Title:** anything memorable (e.g. `bnk-forge-vm-deploy-<your-host>`)
- **Allow write access:** leave **unchecked** — read-only is what we want

The private key (`keys/bnk-forge-deploy`) gets embedded into each VM's
cloud-init seed when `make-vm.sh` runs. The public key never goes into the
VM — it's only used by GitHub to authenticate the VM's clone request.

The bootstrap script **shreds `/root/.ssh/id_ed25519` inside the VM** as soon
as the clone succeeds. That removes the copy anything running as a non-root
user could hope to reach; it does not make the VM credential-free.
Consequences:

- Root can still recover the key. cloud-init caches the full user-data at
  `/var/lib/cloud/instance/user-data.txt` (`0600 root:root`) for the
  instance's life, and the seed itself stays attached as `/dev/vdb` locally.
  On the cloud path the instance metadata service serves the same user-data —
  see [Cloud deployment](#cloud-deployment-no-local-kvm). Treat the key as
  compromised if the VM is, and revoke it on the repo.
- `git pull` inside the VM no longer authenticates. These VMs are cattle:
  rebuild rather than update, or add your own key to `/root/.ssh/` if you
  really want in-place pulls (see [Day-2 ops](#day-2-ops)).

### 6. SSH access into the VM

`make-vm.sh` injects every `*.pub` from `~/.ssh/` into the VM's admin user
(`ADMIN_USER`, default `ubuntu`). If you don't have one, `ssh-keygen -t
ed25519`. To use a different set of keys, point `SSH_AUTHORIZED_KEYS_FILE` in
`config.env` at a file containing one or more public keys (one per line).

sshd inside the VM is key-only, denies root, and restricts logins to
`ADMIN_USER` alone (`AllowUsers`) — so on any image whose provider creates a
*different* account (GCP OS Login, Azure admin username), set `ADMIN_USER`
to match before rendering, or you will be locked out.

### 7. Egress and disk requirements

The VM needs network egress to:
- `archive.ubuntu.com` / `security.ubuntu.com` — apt
- `download.docker.com` — Docker engine
- `github.com:22` — git clone via SSH

Disk: ~3 GB sparse for the base image + ~5–10 GB per running VM (COW
overlay grows as bnk-forge installs). The pool at
`/var/lib/libvirt/images/` should have room.

## Quickstart

```bash
cd <bnk-forge-repo>/vm-bnk-forge
cp config.env.example config.env   # config.env is gitignored — yours to edit
$EDITOR config.env                 # set VM_NAME, sizing, BRANCH if not staging
./make-vm.sh                       # build seed.iso + virt-install
sudo tail -F /var/log/libvirt/qemu/<VM_NAME>.serial.log   # watch first-boot
```

After ~6 min: `https://<vm-ip>:8443` returns HTTP 200, all containers `healthy`.

`BRANCH` accepts anything `git clone --branch` does. A branch tracks a moving
HEAD — two VMs built an hour apart aren't the same build — so pin a release
tag (`BRANCH="v1.2.3"`) when the VM is for a demo or a repro.

## Cloud deployment (no local KVM)

To launch the same bnk-forge VM on AWS, GCP, Azure, Hetzner, DigitalOcean,
OpenStack, or any provider that accepts cloud-init user-data on boot:

```bash
./render-cloud-init.sh                        # writes ./<VM_NAME>-cloud-init.yaml
./render-cloud-init.sh /path/to/output.yaml   # custom output path
./render-cloud-init.sh -                      # write to stdout
```

The output is a self-contained `#cloud-config` YAML — paste its contents into
the provider's *user-data* / *custom data* / *cloud-config* field when
launching an Ubuntu 24.04 LTS instance. The first boot does the same Docker
install + GitHub clone + `make install` as the local KVM flow.

Skips Setup §1–4 entirely (no libvirt, no base image, no bridge). You still
need §5 (deploy keypair) and §6 (your SSH pubkey on the host you're rendering
from).

### Read this before putting one on a public IP

The local KVM flow lands on a LAN. The cloud flow can land on a public
address, where three properties of this image stack up badly:

1. **Default credentials.** The stack comes up with a generated `admin` password
   (at `/app/keys/initial_admin_password`, or set `DEFAULT_ADMIN_PASSWORD`).
   Log in and change it immediately — or don't attach a public IP until you
   have.
2. **Docker socket is mounted into the backend.** `make install` writes a
   `docker-compose.override.yml` that bind-mounts `/var/run/docker.sock`
   (tracked as SEC-004). That makes a web login effectively host root, which
   is what turns point 1 from embarrassing into serious.
3. **The rendered user-data embeds the deploy private key**, and every major
   cloud keeps user-data readable from inside the instance via the metadata
   service (`http://169.254.169.254/latest/user-data` on EC2). Any SSRF in
   anything running on that box — bnk-forge included — can read it. The
   bootstrap shreds `/root/.ssh/id_ed25519` after cloning, but it removes
   neither the metadata copy nor cloud-init's own cache of the user-data at
   `/var/lib/cloud/instance/user-data.txt`. Use a read-only deploy key per
   instance, revoke it
   after the build, and prefer IMDSv2 / metadata-access restrictions where the
   provider offers them.

The instance also enables `ufw` during cloud-init — SSH plus the ports the
host-networked proxy actually binds (22, 8443, 8082), in the spirit of
`docs/INSTALLATION.md` Step 4. That is a host firewall, not a substitute for a
tight security group / cloud firewall in front of it.

Set `ADMIN_USER` in `config.env` to whatever account your provider's image
creates before rendering — sshd's `AllowUsers` is scoped to that account.

## config.env reference

| Var | Default | Notes |
|---|---|---|
| `VM_NAME` | `bnk-forge-1` | libvirt domain name + disk filename |
| `VM_MEMORY_MB` | `16384` | matches the reference deployment VM (docs/DEPLOYMENT.md) |
| `VM_VCPUS` | `8` | builds parallelize across cores |
| `VM_DISK_GIB` | `100` | COW overlay size; cloud-init growpart expands rootfs to fill |
| `VM_NETWORK` | `default` | libvirt network name (must be a bridge-mode network, see Setup §2) |
| `REPO_URL` | `git@github.com:f5devcentral/bnk-forge.git` | clone via SSH using deploy key |
| `BRANCH` | `staging` | any `git clone --branch` ref; pin a tag for reproducible VMs |
| `ADMIN_USER` | `ubuntu` | account created in the VM; sshd `AllowUsers` is scoped to it |
| `SSH_AUTHORIZED_KEYS_FILE` | _(empty)_ | empty = use `~/.ssh/*.pub`; set to a file path to override |

Copy `config.env.example` → `config.env` first; `config.env` is gitignored so
your host-local settings never end up in a commit.

## Day-2 ops

```bash
# Tail bootstrap log (during first-boot):
ssh ubuntu@<vm-ip> 'sudo journalctl -u bnk-forge-bootstrap -f'

# Container status:
ssh ubuntu@<vm-ip> 'docker ps'

# Tear down the VM completely:
./destroy-vm.sh <VM_NAME>

# Multiple VMs: edit config.env's VM_NAME between runs, e.g. bnk-forge-2 on a feature branch.
```

**Updating in place:** the deploy key is shredded after the first-boot clone,
so `git pull` inside the VM has no credential. Rebuild the VM against a newer
`BRANCH` (the intended flow — these are disposable), or install a key of your
own on the VM first:

```bash
# On the VM, with your own key added to /root/.ssh/ and registered on the repo:
ssh ubuntu@<vm-ip> 'cd /opt/bnk-forge && sudo git pull && sudo make deploy'
```

## Troubleshooting

**Symptom: `make-vm.sh` fails with "VM already exists" or leftover files.**
Either rename `VM_NAME` or `./destroy-vm.sh <VM_NAME>` first.

**Symptom: `make-vm.sh` refuses to run / reports missing commands.**
It provisions a local KVM guest, so it requires Linux plus `virt-install`,
`virsh`, `qemu-img` and `cloud-localds` (Setup §1). On macOS or Windows use
`./render-cloud-init.sh` and boot the result at a cloud provider instead.

**Symptom: `config.env not found`.**
`cp config.env.example config.env` — the real file is gitignored on purpose.

**Symptom: first boot completes but bnk-forge isn't installed.**
`make install` failed. The unit retries on failure (up to 4 times in 2 h):

```bash
ssh ubuntu@<vm-ip> 'journalctl -u bnk-forge-bootstrap -n 100 --no-pager'   # why it failed
ssh ubuntu@<vm-ip> 'sudo systemctl start bnk-forge-bootstrap'              # retry now
```

The unit is a no-op once `/var/lib/bnk-forge/bootstrap.done` exists; delete
that file to force a full re-run.

**Symptom: VM boots but never gets an IP / cloud-init never runs.**
Check the serial log: `sudo tail -F /var/log/libvirt/qemu/<VM_NAME>.serial.log`. If
the boot reaches `ubuntu login:` with hostname `ubuntu` (not `<VM_NAME>`), cloud-init
isn't seeing the seed disk. The known cause: the minimal cloudimg's initramfs lacks
AHCI/IDE drivers, so SATA-attached CDROMs are invisible. We work around this by
attaching the cidata seed as a virtio-blk read-only disk. If you change that in
`make-vm.sh`, expect this failure to return.

**Symptom: bootstrap clones fine but `make install` fails with permission denied.**
Volume permission fixup happens in `_install-start`. If you switched the bootstrap
to call `make deploy` instead of `make install`, that fixup is skipped on a fresh
volume. First boot must use `make install`.

**Symptom: Cockpit shows VM IP as `192.168.68.x/0`.**
Cosmetic. Means libvirt is reading the IP from the host ARP cache (which lacks
prefix info). Fixed automatically once `qemu-guest-agent` starts inside the VM
(part of the cloud-init package list). Cockpit may cache the old value until a
hard refresh or a libvirtd restart.

## Files

```
vm-bnk-forge/
├── README.md                   # this file
├── .gitignore                  # ignores config.env, keys/, rendered user-data
├── config.env.example          # tracked template — copy to config.env
├── config.env                  # per-VM settings (gitignored, operator-local)
├── make-vm.sh                  # build seed.iso + virt-install (local KVM, Linux only)
├── render-cloud-init.sh        # render user-data only (cloud copy-paste, runs anywhere)
├── destroy-vm.sh               # undefine + remove disks + serial log
├── lib/
│   └── render.sh               # shared config/key/render helpers (sourced by all three)
├── templates/
│   ├── user-data.tpl           # cloud-init recipe (with @@VAR@@ placeholders)
│   └── meta-data.tpl
└── keys/                       # entire directory is gitignored — operator-local
    ├── bnk-forge-deploy        # generated locally; embedded into each VM's seed.iso
    └── bnk-forge-deploy.pub    # generated locally; pasted into the repo's Deploy keys page
```

Base image: `/var/lib/libvirt/images/ubuntu-24.04-minimal-base.qcow2`
Per-VM disks: `/var/lib/libvirt/images/<VM_NAME>.qcow2` (COW overlay)
Per-VM seed:  `/var/lib/libvirt/images/<VM_NAME>-seed.iso` (cidata)
Serial log:   `/var/log/libvirt/qemu/<VM_NAME>.serial.log`
