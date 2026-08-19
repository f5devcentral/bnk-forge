#!/usr/bin/env bash
# Build a cloud-init seed and provision a bnk-forge VM via virt-install.
# Linux + KVM only. For cloud providers, use ./render-cloud-init.sh instead.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/render.sh
source "$HERE/lib/render.sh"

# --- 0. Preflight: this path needs Linux + a local KVM/libvirt toolchain ---
if [ "$(uname -s)" != "Linux" ]; then
  echo "ERROR: make-vm.sh provisions a local KVM VM and only runs on Linux (found: $(uname -s))." >&2
  echo "On macOS/Windows, render the cloud-init and boot it at a cloud provider:" >&2
  echo "  ./render-cloud-init.sh" >&2
  exit 1
fi
require_tools virt-install virsh qemu-img cloud-localds ssh-keyscan python3 || {
  echo "Install them with:" >&2
  echo "  sudo apt install -y qemu-system-x86 libvirt-daemon-system libvirt-clients \\" >&2
  echo "                     virtinst libosinfo-bin cloud-image-utils genisoimage" >&2
  exit 1
}

load_config

# Script-relative defaults (override in config.env if needed).
: "${DEPLOY_KEY_PRIVATE:=$HERE/keys/bnk-forge-deploy}"
: "${TEMPLATES_DIR:=$HERE/templates}"

: "${VM_NAME:?missing VM_NAME}"
: "${BRANCH:?missing BRANCH}"
: "${REPO_URL:?missing REPO_URL}"
: "${BASE_IMAGE:?}"
: "${IMAGES_DIR:?}"

VM_DISK="${IMAGES_DIR}/${VM_NAME}.qcow2"
SEED_ISO="${IMAGES_DIR}/${VM_NAME}-seed.iso"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[ -r "$BASE_IMAGE" ] || { echo "ERROR: base image not readable: $BASE_IMAGE (see README §4)" >&2; exit 1; }
[ -r "$DEPLOY_KEY_PRIVATE" ] || { echo "ERROR: deploy private key missing: $DEPLOY_KEY_PRIVATE (see README §5)" >&2; exit 1; }
if sudo virsh dominfo "$VM_NAME" >/dev/null 2>&1; then
  echo "ERROR: VM '$VM_NAME' already exists. Run ./destroy-vm.sh $VM_NAME first." >&2
  exit 1
fi
if [ -e "$VM_DISK" ] || [ -e "$SEED_ISO" ]; then
  echo "ERROR: leftover files present:" >&2
  [ -e "$VM_DISK" ] && echo "  $VM_DISK" >&2
  [ -e "$SEED_ISO" ] && echo "  $SEED_ISO" >&2
  echo "Run ./destroy-vm.sh $VM_NAME first." >&2
  exit 1
fi

# --- 1. Resolve template variables (SSH keys, deploy key, GitHub host keys) ---
export_render_env

# --- 2. Render templates ---
umask 077   # rendered user-data embeds the deploy private key
render_template "$TEMPLATES_DIR/user-data.tpl" "$WORK/user-data"
render_template "$TEMPLATES_DIR/meta-data.tpl" "$WORK/meta-data"
echo "[+] Rendered cloud-init seed inputs in $WORK"

# --- 3. Build seed ISO and place it in the libvirt pool ---
# 0600: the cidata image embeds the deploy private key, and qemu reads it as
# libvirt-qemu — no other local user needs access.
cloud-localds "$WORK/seed.iso" "$WORK/user-data" "$WORK/meta-data"
sudo install -o libvirt-qemu -g kvm -m 0600 "$WORK/seed.iso" "$SEED_ISO"
echo "[+] Seed ISO: $SEED_ISO"

# --- 4. Create COW overlay disk ---
sudo qemu-img create -F qcow2 -b "$BASE_IMAGE" -f qcow2 "$VM_DISK" "${VM_DISK_GIB}G" >/dev/null
sudo chown libvirt-qemu:kvm "$VM_DISK"
echo "[+] VM disk: $VM_DISK (${VM_DISK_GIB}G COW over $BASE_IMAGE)"

# --- 5. Provision via virt-install ---
# NOTE on seed disk: we attach the cidata ISO as a *virtio-blk* read-only disk
# (not a SATA/IDE CDROM). The Ubuntu 24.04 minimal cloudimg ships an initramfs
# without AHCI/IDE drivers, so a CDROM seed never enumerates and cloud-init
# can't find its NoCloud datasource. virtio-blk is always present.
# The guest-agent channel is explicit rather than relying on virt-install's
# default, since `virsh domifaddr --source agent` and Cockpit's IP display
# depend on it.
SERIAL_LOG="/var/log/libvirt/qemu/${VM_NAME}.serial.log"
sudo virt-install \
  --name "$VM_NAME" \
  --memory "$VM_MEMORY_MB" \
  --vcpus "$VM_VCPUS" \
  --osinfo ubuntu24.04 \
  --disk "path=$VM_DISK,bus=virtio" \
  --disk "path=$SEED_ISO,bus=virtio,readonly=on,format=raw" \
  --network "network=$VM_NETWORK,model=virtio" \
  --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
  --graphics none \
  --serial "pty,log.file=$SERIAL_LOG,log.append=off" \
  --console pty,target_type=serial \
  --boot hd \
  --noautoconsole \
  --import

echo
echo "VM '$VM_NAME' created."
echo
echo "Watch first-boot:"
echo "  sudo tail -F $SERIAL_LOG               # full boot log"
echo "  sudo virsh console $VM_NAME            # interactive serial (Ctrl-] to detach)"
echo "  sudo virsh domifaddr $VM_NAME          # show LAN IP once DHCP completes"
echo
echo "Once SSH is reachable:"
echo "  ssh ${ADMIN_USER}@<vm-ip> 'sudo journalctl -u bnk-forge-bootstrap -f'"
