#!/usr/bin/env bash
# Tear down a bnk-forge VM and remove its disks.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/render.sh
source "$HERE/lib/render.sh"

load_config

NAME="${1:-${VM_NAME:-}}"
[ -n "$NAME" ] || { echo "Usage: $0 [vm-name]" >&2; exit 1; }

VM_DISK="${IMAGES_DIR}/${NAME}.qcow2"
SEED_ISO="${IMAGES_DIR}/${NAME}-seed.iso"
SERIAL_LOG="/var/log/libvirt/qemu/${NAME}.serial.log"

# Nothing to do — and, more usefully, a typo'd name no longer reports success.
if ! sudo virsh dominfo "$NAME" >/dev/null 2>&1 \
   && [ ! -e "$VM_DISK" ] && [ ! -e "$SEED_ISO" ]; then
  echo "ERROR: no VM, disk, or seed found for '$NAME'." >&2
  echo "Known domains:" >&2
  sudo virsh list --all --name | sed '/^$/d;s/^/  /' >&2
  exit 1
fi

echo "Tearing down VM '$NAME'..."
sudo virsh destroy "$NAME" 2>/dev/null || true
sudo virsh undefine "$NAME" --nvram 2>/dev/null || true
sudo rm -f "$VM_DISK" "$SEED_ISO" "$SERIAL_LOG"
echo "Removed VM '$NAME', its disks, and its serial log."
