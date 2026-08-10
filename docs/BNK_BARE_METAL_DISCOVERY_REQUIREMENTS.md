# BNK Bare-Metal Discovery Requirements

> Comprehensive inventory of tools, utilities, software, firmware, and infrastructure
> required for BIG-IP Next for Kubernetes (BNK) 2.2 bare-metal DPU deployments.
>
> **Source:** [F5 CloudDocs — BNK 2.2](https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/)
> **Date:** 2026-05-06
> **Purpose:** Reference for discovery/validation agents to verify environment readiness.

---

## 1. Namespace Model

BNK 2.2 uses a two-namespace architecture. Discovery should verify these exist or
can be created.

| Namespace | Purpose | Fixed Name? |
|-----------|---------|-------------|
| `f5-cne-core` | CNE core — cluster-wide control plane, shared infrastructure (FLO, CRDs, CWC, dSSM, RabbitMQ, cert-manager components, TODA Fluentd, OTEL Collector, IPAM Controller) | Yes — hardcoded in docs |
| CNE instance namespace | CNE instance — data plane (TMM), per-instance controller, AFM, Observer, NADs, VLANs | No — user-chosen. Doc examples: `f5-bnk-instance` (DPU/FLO path), `bnk-app1` (Helm path). Each instance gets its own namespace. |
| `cert-manager` | Jetstack cert-manager pods + CA secret (`arm-ca`) | Yes — upstream default |

Additional namespaces that may exist:
- Tenant/application namespaces (e.g. `app1`, `app2`) — listed in `controller.watchNamespace`
- `kube-system` — SR-IOV Device Plugin, Multus DaemonSets, NFS CSI driver
- `openshift-sriov-network-operator` — OpenShift only

---

## 2. Client / Installer Machine Tools

These must be present on the machine running installation commands.

### 2.1 Explicitly Required (Listed in Docs)

| Tool | Version Constraint | Purpose | Doc Section |
|------|-------------------|---------|-------------|
| `curl` | — | HTTP requests: licensing API, JWKS download, CWC API | Helm: "Prepare your computer" |
| `helm` | 3.8.0–3.20.x (NOT 4.0+) | Install charts, OCI pull from FAR, registry login | Both paths |
| `jq` | — | JSON processing: extract secrets, license status, asset IDs, cert data | Both paths |
| `kubectl` | Match cluster version | All K8s operations: create, apply, get, describe, exec, port-forward, patch, label, taint, annotate | Helm: "Prepare your computer" |
| `make` | — | Build configuration files | Both paths |
| `openssl` | — | Generate TLS certificates | Both paths |
| `python3` | — | Runs cert generation scripts (`gen_cert.sh`) | Both paths |
| `yq` | — | YAML processing — used by `get-cne-chart-version.sh` to parse release manifests | Helm: "Prepare your computer" |
| `wget` | — | Download files from the internet | DPU: "Client tools" |

### 2.2 Implicitly Required (Used in Commands but Not Listed)

| Tool | Purpose |
|------|---------|
| `bash` / `sh` | Shell for all scripts (`gen_cert.sh`, `get-cne-chart-version.sh`, `create-far-pull-secret-manifest.sh`) |
| `base64` | Encode/decode secrets for FAR pull secret, cert extraction. macOS `base64` lacks `-w 0` — docs note this. |
| `tar` | Extract `.tgz` archives (release manifest chart, cert-gen chart) |
| `cat` | Pipe files to commands (FAR login, cert extraction, RShim status) |
| `printf` | Format strings for dockerconfigjson construction |
| `chmod` | Make scripts executable |
| `touch` | Create empty override values files |
| `grep` / `egrep` | Filter pod lists, check kernel modules, hugepages |
| `sed` | In-place file edits (GRUB config, containerd config) |
| `tee` | Write to files with sudo (netplan, kernel modules, SF persistence) |
| `ssh` | Connect to DPU via RShim (`ssh ubuntu@192.168.100.2`) |
| `ping` | Verify RShim connectivity to DPU |
| `nslookup` | DNS resolution check for DPU hostname |
| `ip` | Address/link configuration (RShim interface, VF setup) |
| `kill` | Terminate port-forward background processes |
| `docker` | Only for private registry path: `docker login`, `docker pull` |

---

## 3. DPU Infrastructure Tools (On DPU Node)

These must be present or installable on the NVIDIA BlueField-3 DPU.

| Tool | Installed Via | Purpose | Key Commands |
|------|-------------|---------|--------------|
| `mst` (Mellanox Software Tools) | DOCA package | Discover PCI device addresses, verify RDMA/NET columns | `mst start`, `mst status -v` |
| `mlnx-sf` | DOCA package | Create and manage Scalable Functions (SFs) | `/sbin/mlnx-sf --action create --device <PCI> --sfnum <N> --hwaddr <MAC>`, `mlnx-sf -a show` |
| `ovs-vsctl` | DOCA / OVS package | Create/manage OVS bridges, add ports (PF, SF representor, VF representor) | `ovs-vsctl add-br`, `ovs-vsctl add-port`, `ovs-vsctl show`, `ovs-vsctl del-br` |
| `modprobe` | Linux kernel | Load kernel modules (`vfio_pci`, `br_netfilter`, `overlay`) | `modprobe vfio_pci` |
| `lsmod` | Linux kernel | Verify kernel modules are loaded | `lsmod \| grep vfio_pci` |
| `sysctl` | Linux kernel | Apply networking kernel parameters | `sysctl --system` |
| `systemctl` | systemd | Enable/start/restart services (kubelet, containerd) | `systemctl enable --now containerd` |
| `hostnamectl` | systemd | Set DPU hostname before K8s join | `hostnamectl set-hostname <name>` |
| `update-grub` | GRUB | Apply hugepage persistence config | After editing `/etc/default/grub` |
| `kubeadm` | K8s package | Reset standalone mode, join cluster | `kubeadm reset --force`, `kubeadm join` |
| `containerd` | Container runtime | Re-render containerd config if upgrading | `containerd config default` |
| `kubelet` | K8s package | Node agent — must match cluster version | DPU ships with v1.30.10 |

---

## 4. Host Server Tools (On Server with DPU Card)

These must be present on the x86 server where the BlueField-3 card is physically installed.

| Tool | Installed Via | Purpose | Key Commands |
|------|-------------|---------|--------------|
| `mst` (Mellanox Software Tools) | DOCA `doca-all` package | Find DPU NIC interface names, verify RDMA column | `mst start`, `mst status -v` |
| `modprobe` | Linux kernel | Load `mst_pci` module | `modprobe mst_pci` |
| `lsmod` | Linux kernel | Verify `mst_pci` loaded | `lsmod \| grep mst_pci` |
| `ip` | iproute2 | Configure RShim interface (`tmfifo_net0`), create VFs, set VF IP | `ip address add`, `ip link set` |
| `systemctl` | systemd | Enable RShim service | `systemctl enable --now rshim` |
| `ping` | iputils | Verify DPU connectivity via RShim | `ping -c 2 192.168.100.2` |
| `ssh` | OpenSSH | Access DPU via RShim | `ssh ubuntu@192.168.100.2` |
| `pv` | `apt install pv` | Optional — show progress during BFB image push to DPU | Used with `/dev/rshim0/boot` |
| `kubeadm` | K8s package | Join server to cluster | `kubeadm join`, `kubeadm token create --print-join-command` |

---

## 5. Kubernetes Cluster Software

These are cluster-level components that must be installed and running.

| Component | Required Version | Purpose | Install Method |
|-----------|-----------------|---------|----------------|
| **Calico CNI** | v3.27.0 | Primary CNI | Standard Calico install |
| **Multus** | Latest | Secondary CNI — attach SR-IOV/SF interfaces to TMM pods | `kubectl apply -f` from upstream |
| **SR-IOV Network Device Plugin** | Latest | Exposes DPU scalable functions to K8s scheduler | `kubectl create -f` from upstream |
| **NFS CSI Driver** | v4.10.0 | Storage provisioner for PVs (dSSM, OTEL, core files). RWX access mode required. | Helm install `csi-driver-nfs` |
| **cert-manager** (Jetstack) | v1.16.1 (tested) | TLS certificate management, ClusterIssuer for mTLS | `kubectl apply -f` from upstream |
| **kubelet** | Match cluster version | Node agent | DPU ships with v1.30.10, may need upgrade |
| **containerd** | Match cluster version | Container runtime | DPU ships with v1.7.24, may need upgrade |

---

## 6. NVIDIA Firmware & Drivers

| Component | Required Version | Scope | Notes |
|-----------|-----------------|-------|-------|
| **DOCA** | v2.9.2 | Both DPU and server | Includes drivers, firmware, runtime, RShim, mst tools. Must be same version on both. |
| **RShim** | Included with DOCA | Server only | PCI connectivity between server and DPU. Must be enabled (`systemctl enable --now rshim`). |
| **BFB Image** | Bundled with DOCA 2.9.2 | DPU | `bf-bundle-2.9.2-*.bfb`. Pushed to DPU via `/dev/rshim0/boot`. Takes 10-15 min. |
| **BlueField-3** | Model B3220 | Hardware | Only 1 DPU per chassis supported for BNK. Must be dedicated to BNK. |

---

## 7. Hardware Requirements

| Category | Requirement | Notes |
|----------|-------------|-------|
| DPU | NVIDIA BlueField-3 (B3220) | Dedicated to BNK — no other software on DPU |
| DPU NICs | Minimum 2 physical NICs | Separate internal and external networks |
| Host Architecture | x86_64 or ARM | SR-IOV must be enabled in BIOS |
| DPU Memory | Minimum 16 GB | TMM requires hugepages |
| Host Memory | Minimum 32 GB | For application workloads |
| Storage | NFS Server | Required for persistent volumes (dSSM, OTEL, core files) |
| Network | Dual NIC (internal + external) | Two OVS bridges on DPU |

---

## 8. Kernel Module Requirements

### 8.1 DPU Node

These kernel modules must be loaded on the DPU:

```
overlay
br_netfilter
vfio_pci
```

Persist via `/etc/modules-load.d/custom.conf`.

### 8.2 DPU Sysctl Settings

```
net.bridge.bridge-nf-call-ip6tables = 1
net.bridge.bridge-nf-call-iptables = 1
net.ipv4.ip_forward = 1
```

Persist via `/etc/sysctl.d/kubernetes.conf`.

### 8.3 Host Server

- `mst_pci` kernel module must be loaded
- Persist via `/etc/modules-load.d/mst_pci.conf`
- SR-IOV must be enabled in BIOS

---

## 9. Hugepages Requirements

Hugepages must be configured on the DPU node. The amount depends on the CNEInstance
`deploymentSize`:

| Deployment Size | Hugepages-2Mi Page Count | Total Memory |
|----------------|------------------------|--------------|
| Small | 2048 | 4 GiB |
| Medium | 4096 | 8 GiB |
| Large | 8192 | 16 GiB |
| Max | 12300 | ~24 GiB |

Persist via GRUB: `default_hugepagesz=2MB hugepagesz=2M hugepages=<count>`

---

## 10. Network Requirements

| Resource | Requirement | Notes |
|----------|-------------|-------|
| External VLAN IPs | 1 Self IP per TMM instance (IPv4 + IPv6) | Example: 11.19.1.80–82 |
| Internal VLAN IPs | 1 Self IP per TMM instance (IPv4 + IPv6) | Example: 10.19.1.80–82 |
| SNAT Pool IPs | For egress SNAT | 1 pool per egress setup |
| Virtual Server IPs | For ingress virtual servers | Can be auto-assigned by F5 IPAM Controller |
| OVS Bridges | 2 per DPU: `sf-external` + `sf-internal` | Each bridges PF + SF (+ VF representor for internal) |
| Scalable Functions | Minimum 2 SFs per DPU | 1 internal + 1 external, created via `mlnx-sf` |
| SR-IOV VFs | 1 VF on server for internal connectivity | On the DPU NIC port designated for internal traffic |
| RShim Network | `192.168.100.1/30` (server) ↔ `192.168.100.2/30` (DPU) | Via `tmfifo_net0` interface |
| MTU | Up to 9000, consistent across VLANs | Set in F5SPKVlan CR |

---

## 11. OVS Bridge Topology

The DPU requires two OVS bridges:

```
Bridge sf-external
    Port p0                  # Physical port 0 (external-facing NIC)
    Port en3f0pf0sf1         # Scalable Function representor for port 0
    Port sf-external         # Bridge internal port

Bridge sf-internal
    Port p1                  # Physical port 1 (internal-facing NIC)
    Port en3f1pf1sf1         # Scalable Function representor for port 1
    Port pf1vf0              # Virtual Function representor (links to server VF)
    Port sf-internal         # Bridge internal port
```

---

## 12. Kubernetes Node Configuration

### 12.1 DPU Node

- **Label:** `app=f5-tmm` (schedules TMM pods)
- **Taint:** `dpu=true:NoSchedule` (prevents non-TMM workloads)
- **kubelet/kubeadm:** Must remove default BlueField standalone config before joining cluster
- **Files to remove before join:**
  - `/lib/systemd/system/kubelet.service.d/90-kubelet-bluefield.conf`
  - `/etc/kubelet.d/doca_telemetry_standalone.yaml`
  - `/etc/cni/net.d/99-loopback.conf`
- **cloud-init:** Should be disabled (`touch /etc/cloud/cloud-init.disabled`)

### 12.2 Server Node (Host)

- Joins cluster as worker or control node
- 1 SR-IOV VF on internal-facing DPU NIC port with IP from CNE internal network
- Standard K8s prerequisites (containerd, kubelet, kernel modules, sysctl)

---

## 13. SR-IOV Device Plugin ConfigMap

The SR-IOV Network Device Plugin needs a ConfigMap (`sriovdp-config` in `kube-system`)
mapping DPU scalable functions:

```json
{
  "resourceList": [
    {
      "resourceName": "bf3_p0_sf",
      "resourcePrefix": "nvidia.com",
      "deviceType": "auxNetDevice",
      "selectors": [{
        "vendors": ["15b3"],
        "devices": ["a2dc"],
        "pciAddresses": ["0000:03:00.0"],
        "pfNames": ["p0#1"],
        "auxTypes": ["sf"]
      }]
    },
    {
      "resourceName": "bf3_p1_sf",
      "resourcePrefix": "nvidia.com",
      "deviceType": "auxNetDevice",
      "selectors": [{
        "vendors": ["15b3"],
        "devices": ["a2dc"],
        "pciAddresses": ["0000:03:00.1"],
        "pfNames": ["p1#1"],
        "auxTypes": ["sf"]
      }]
    }
  ]
}
```

NADs in the CNE instance namespace reference these resources:
- `nvidia.com/bf3_p0_sf` → `sf-external` NAD
- `nvidia.com/bf3_p1_sf` → `sf-internal` NAD

---

## 14. cert-manager Configuration

### ClusterIssuer Chain

```yaml
# 1. Self-signed root
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: selfsigned-cluster-issuer
spec:
  selfSigned: {}

# 2. CA certificate (in cert-manager namespace)
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: arm-ca
  namespace: cert-manager
spec:
  isCA: true
  commonName: arm-ca
  secretName: arm-ca
  issuerRef:
    name: selfsigned-cluster-issuer
    kind: ClusterIssuer

# 3. CA-backed ClusterIssuer (used by BNK components)
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: arm-ca-cluster-issuer
spec:
  ca:
    secretName: arm-ca
```

Referenced by:
- FLO override values: `global.certmgr.clusterIssuer: arm-ca-cluster-issuer`
- CNEInstance CR: `certificate.clusterIssuer: arm-ca-cluster-issuer`
- OTEL certificates: `issuerRef.name: arm-ca-cluster-issuer`

---

## 15. Discovery Validation Checklist

Use this checklist to validate environment readiness. Each item maps to a section above.

### Client Machine
- [ ] `curl` available
- [ ] `helm` v3.8.0–3.20.x (not 4.0+)
- [ ] `jq` available
- [ ] `kubectl` available and configured for target cluster
- [ ] `make` available
- [ ] `openssl` available
- [ ] `python3` available
- [ ] `yq` available
- [ ] `wget` available
- [ ] `base64` available (check macOS `-w 0` compatibility)
- [ ] `tar` available
- [ ] `ssh` available

### Host Server
- [ ] DOCA v2.9.2 installed
- [ ] RShim enabled and running (`systemctl status rshim`)
- [ ] `mst` tools functional (`mst start && mst status -v` shows valid RDMA + NET columns)
- [ ] `mst_pci` kernel module loaded and persisted
- [ ] SR-IOV enabled in BIOS
- [ ] DPU reachable via RShim (`ping 192.168.100.2`)
- [ ] 1 VF created on internal-facing DPU NIC port with IP assigned
- [ ] VF network config persisted (netplan)
- [ ] Node joined K8s cluster (or ready to join)

### DPU Node
- [ ] BFB image flashed (DOCA v2.9.2)
- [ ] `oob_net0` configured with routable IP
- [ ] Default route via `oob_net0` (not `tmfifo_net0`)
- [ ] `mst` tools functional
- [ ] Scalable Functions created (minimum 2: 1 per PF)
- [ ] SFs persisted in `/etc/mellanox/mlnx-sf.conf`
- [ ] OVS bridges created (`sf-external`, `sf-internal`) with correct ports
- [ ] Hugepages configured and persisted (GRUB)
- [ ] Kernel modules loaded: `overlay`, `br_netfilter`, `vfio_pci`
- [ ] Sysctl: `ip_forward=1`, `bridge-nf-call-iptables=1`, `bridge-nf-call-ip6tables=1`
- [ ] Hostname set and DNS-resolvable
- [ ] BlueField standalone kubelet config removed
- [ ] cloud-init disabled
- [ ] kubelet + containerd versions match cluster
- [ ] Node joined K8s cluster with label `app=f5-tmm` and taint `dpu=true:NoSchedule`

### Kubernetes Cluster
- [ ] Calico CNI v3.27.0 installed
- [ ] Multus installed (thick plugin DaemonSet) with DPU toleration
- [ ] SR-IOV Network Device Plugin installed with DPU toleration
- [ ] `sriovdp-config` ConfigMap in `kube-system` with SF entries
- [ ] NFS CSI Driver v4.10.0 installed
- [ ] NFS StorageClass created and set as default
- [ ] cert-manager installed (v1.16.1 tested)
- [ ] ClusterIssuer chain created (`selfsigned-cluster-issuer` → `arm-ca` → `arm-ca-cluster-issuer`)
- [ ] ClusterIssuers in READY state
- [ ] Minimum 3 worker nodes (for dSSM replicas)
- [ ] `f5-cne-core` namespace exists (or can be created)
- [ ] CNE instance namespace exists (or can be created)
- [ ] FAR pull secret applied in both namespaces

### Licensing
- [ ] FAR service account key (`cne_pull_64.json`) available
- [ ] License key (JWT) available from MyF5
- [ ] Licensing mode chosen: connected or disconnected
- [ ] If connected: cluster has internet access to `product.apis.f5.com` and `product-s.apis.f5.com`

---

## 16. FAR (F5 Artifact Registry) Access

| Item | Value |
|------|-------|
| Registry URL | `repo.f5.com` |
| Helm login | `cat cne_pull_64.json \| helm registry login --username _json_key_base64 --password-stdin repo.f5.com` |
| Docker login | `cat cne_pull_64.json \| docker login --username _json_key_base64 --password-stdin repo.f5.com` |
| Release manifest chart | `oci://repo.f5.com/release/f5-bigip-k8s-manifest` |
| BNK 2.2.1 manifest version | `2.2.1-3.2226.0-0.0.511` |
| BNK 2.2.0 manifest version | `2.2.0-3.2226.0-0.0.385` |

---

## 17. InfiniBand NIC Detection & rshim Device Selection

> **Status:** Future phase — captured for implementation planning.

### Problem

BlueField NICs can operate in InfiniBand (IB) mode instead of Ethernet mode. BNK requires
Ethernet-mode NICs. When multiple NICs are present, some may be IB and some Ethernet, and
each NIC maps to a different rshim device with a distinct subnet.

### Constraints

| Condition | Discovery Behavior | Probe Behavior |
|-----------|-------------------|----------------|
| Single NIC in IB mode | Warn: "NIC is in InfiniBand mode — not usable for BNK" | Hard fail with clear error |
| Multiple NICs, all IB | Same as single NIC — no usable NIC | Hard fail |
| Multiple NICs, mixed IB+Ethernet | Report which NICs are IB vs Ethernet | Select rshim device for an Ethernet NIC |

### rshim Device Addressing

Each rshim device has a different management subnet:

| Device | Host-side IP | DPU-side IP | Subnet |
|--------|-------------|-------------|--------|
| `rshim0` | `192.168.100.1` | `192.168.100.2` | `/30` |
| `rshim1` | `192.168.101.1` | `192.168.101.2` | `/30` |
| `rshim2` | `192.168.102.1` | `192.168.102.2` | `/30` |

The numbering maps to the physical NIC ordering. When IB NICs are present, the rshim
device numbers include both IB and Ethernet NICs. The probe must:

1. Enumerate all rshim devices and their associated NIC ports
2. Determine link type per port (`mlxconfig` or `ibstat` / `rdma link` output)
3. Filter to Ethernet-mode ports
4. Select the rshim device that corresponds to an Ethernet NIC
5. Use that rshim device's management IP for DPU SSH access

### Detection Methods

```bash
# Check NIC port link type via mlxconfig
sudo mlxconfig -d /dev/mst/mt41692_pciconf0 q | grep LINK_TYPE
# Output: LINK_TYPE_P1=ETH(2) or LINK_TYPE_P1=IB(1)

# Or via rdma link (if rdma-core installed)
rdma link show
# Output shows link/infiniband or link/ether per port

# Or via sysfs
cat /sys/class/infiniband/*/ports/*/link_layer
# Output: Ethernet or InfiniBand
```

### Implementation Plan

1. **Discovery (`host_probe.py`):** Add `_probe_nic_link_types()` that enumerates NIC
   ports and their link type. Add `nic_link_types: list[dict]` to `HostProbeResult`.
   Each entry: `{ "device": "mlx5_0", "port": 1, "link_type": "Ethernet"|"InfiniBand", "rshim": "rshim0" }`.

2. **Assessment:** Add a check: if any NIC is IB, warn. If all NICs are IB, fail.

3. **Probe (`probe_dpu.py`):** Before SSH to DPU via rshim, check the NIC link types
   from discovery results. If the default rshim0 maps to an IB NIC, switch to the
   first rshim device that maps to an Ethernet NIC. Hard fail if no Ethernet NIC found.

4. **Frontend:** Show NIC link types in discovery results panel. Warn badge for IB NICs.
