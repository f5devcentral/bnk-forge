#!/usr/bin/env bash
# Shared helpers for make-vm.sh and render-cloud-init.sh. Sourced, never executed.
# Callers set `set -euo pipefail` and define HERE.

# Source config.env, erroring with the copy-the-example hint when it's missing.
load_config() {
  if [ ! -r "$HERE/config.env" ]; then
    echo "ERROR: $HERE/config.env not found." >&2
    echo "Copy the example and edit it:  cp config.env.example config.env" >&2
    exit 1
  fi
  # shellcheck source=/dev/null
  source "$HERE/config.env"
}

# Fail early when a required command is missing.
require_tools() {
  local missing=()
  for t in "$@"; do
    command -v "$t" >/dev/null 2>&1 || missing+=("$t")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    echo "ERROR: missing required command(s): ${missing[*]}" >&2
    return 1
  fi
}

# Admin SSH authorized keys, indented for the cloud-init users list (6 spaces + "- ").
ssh_authorized_keys_block() {
  local keys
  if [ -n "${SSH_AUTHORIZED_KEYS_FILE:-}" ]; then
    [ -r "$SSH_AUTHORIZED_KEYS_FILE" ] || { echo "ERROR: SSH_AUTHORIZED_KEYS_FILE not readable: $SSH_AUTHORIZED_KEYS_FILE" >&2; return 1; }
    keys="$(cat "$SSH_AUTHORIZED_KEYS_FILE")"
  else
    keys=""
    for f in "$HOME"/.ssh/*.pub; do
      [ -f "$f" ] || continue
      keys+="$(cat "$f")"$'\n'
    done
  fi
  keys="$(printf '%s' "$keys" | sed '/^[[:space:]]*$/d')"
  [ -n "$keys" ] || { echo "ERROR: no SSH pubkeys found in \$HOME/.ssh/*.pub" >&2; return 1; }
  printf '%s\n' "$keys" | sed 's/^/      - /'
}

# Deploy private key, indented for a YAML block scalar (6 spaces).
deploy_key_block() {
  [ -r "$DEPLOY_KEY_PRIVATE" ] || {
    echo "ERROR: deploy private key not found at $DEPLOY_KEY_PRIVATE" >&2
    echo "Generate one and register the .pub on GitHub — see README §5." >&2
    return 1
  }
  sed 's/^/      /' "$DEPLOY_KEY_PRIVATE"
}

# GitHub host keys, indented for a YAML block scalar (6 spaces).
github_known_hosts_block() {
  local keys
  if ! keys="$(ssh-keyscan -t rsa,ecdsa,ed25519 github.com 2>/dev/null)"; then
    echo "ERROR: ssh-keyscan github.com failed (no internet?)" >&2
    return 1
  fi
  [ -n "$keys" ] || { echo "ERROR: empty github host keys" >&2; return 1; }
  printf '%s\n' "$keys" | sed 's/^/      /'
}

# Export every @@VAR@@ the templates substitute. Call once before render_template.
export_render_env() {
  local ssh_block deploy_block hostkeys_block instance_id
  ssh_block="$(ssh_authorized_keys_block)"
  deploy_block="$(deploy_key_block)"
  hostkeys_block="$(github_known_hosts_block)"
  instance_id="iid-${VM_NAME}-$(date +%s)"

  export VM_HOSTNAME="$VM_NAME" \
         BRANCH REPO_URL \
         ADMIN_USER="${ADMIN_USER:-ubuntu}" \
         INSTANCE_ID="$instance_id" \
         SSH_AUTHORIZED_KEYS_BLOCK="$ssh_block" \
         DEPLOY_KEY_PRIVATE_BLOCK="$deploy_block" \
         GITHUB_KNOWN_HOSTS_BLOCK="$hostkeys_block"
}

# render_template <template> <output>  ("-" writes to stdout)
render_template() {
  python3 - "$1" "$2" <<'PY'
import os, sys, pathlib
tpl, out = sys.argv[1], sys.argv[2]
keys = ('VM_HOSTNAME','BRANCH','REPO_URL','INSTANCE_ID','ADMIN_USER',
        'SSH_AUTHORIZED_KEYS_BLOCK','DEPLOY_KEY_PRIVATE_BLOCK','GITHUB_KNOWN_HOSTS_BLOCK')
text = pathlib.Path(tpl).read_text()
for k in keys:
    text = text.replace(f'@@{k}@@', os.environ.get(k, ''))
if out == '-':
    sys.stdout.write(text)
else:
    pathlib.Path(out).write_text(text)
PY
}
