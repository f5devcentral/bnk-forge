#!/usr/bin/env bash
# Render the cloud-init user-data for the configured VM and emit it as a
# standalone YAML file (or to stdout) — for copy-paste into a cloud
# provider's "user-data" / "metadata" / "cloud-config" field.
#
# Does NOT create a VM, does NOT need sudo. Runs anywhere (including macOS).
# Inputs are the same as make-vm.sh: config.env + ~/.ssh pubkeys + the deploy
# private key.
#
# Usage:
#   ./render-cloud-init.sh                 # writes ./<VM_NAME>-cloud-init.yaml
#   ./render-cloud-init.sh path/out.yaml   # writes to a custom path
#   ./render-cloud-init.sh -                # writes to stdout

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=lib/render.sh
source "$HERE/lib/render.sh"

require_tools ssh-keyscan python3

load_config

# Script-relative defaults (override in config.env if needed).
: "${DEPLOY_KEY_PRIVATE:=$HERE/keys/bnk-forge-deploy}"
: "${TEMPLATES_DIR:=$HERE/templates}"

: "${VM_NAME:?missing VM_NAME}"
: "${BRANCH:?missing BRANCH}"
: "${REPO_URL:?missing REPO_URL}"

OUT="${1:-${VM_NAME}-cloud-init.yaml}"

# --- 1. Resolve template variables (SSH keys, deploy key, GitHub host keys) ---
export_render_env

# --- 2. Render ---
# 0600: the output embeds the deploy private key.
umask 077
render_template "$TEMPLATES_DIR/user-data.tpl" "$OUT"

if [ "$OUT" != "-" ]; then
  chmod 600 "$OUT"
  echo "Rendered cloud-init user-data → $OUT (mode 0600)" >&2
  echo >&2
  echo "Paste contents into the cloud provider's user-data / cloud-config field:" >&2
  echo "  AWS EC2:        Advanced details → User data" >&2
  echo "  GCP:            Metadata → user-data" >&2
  echo "  Azure:          Custom data" >&2
  echo "  Hetzner Cloud:  --user-data-from-file <path>  (hcloud CLI)" >&2
  echo "  DigitalOcean:   User data (during droplet creation)" >&2
  echo "  OpenStack:      --user-data <path>            (openstack CLI)" >&2
  echo >&2
  echo "Use an Ubuntu 24.04 LTS image, and set ADMIN_USER in config.env to the" >&2
  echo "account your provider creates if it isn't 'ubuntu' — the sshd hardening" >&2
  echo "drop-in restricts logins to that user." >&2
  echo >&2
  echo "The file embeds a private SSH key. Treat it as a secret in transit, and" >&2
  echo "note that user-data stays readable via the instance metadata service on" >&2
  echo "most clouds — see README 'Cloud deployment' for the exposure this implies." >&2
fi
