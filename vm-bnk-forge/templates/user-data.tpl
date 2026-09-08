#cloud-config
# bnk-forge VM cloud-init — rendered from vm-bnk-forge/templates/user-data.tpl
# (by either make-vm.sh or render-cloud-init.sh). Placeholders @@VAR@@ are
# substituted at render time. Anything else (e.g. $UPTIME) is interpreted by
# cloud-init / the runtime shell.

hostname: @@VM_HOSTNAME@@
fqdn: @@VM_HOSTNAME@@.lan
manage_etc_hosts: true
preserve_hostname: false

# === SSH access for the admin user (config.env::ADMIN_USER, default `ubuntu`) ===
# Keys are pulled from the host at render time (see config.env::SSH_AUTHORIZED_KEYS_FILE).
# To override, edit config.env or replace the block below with literal keys.
users:
  - name: @@ADMIN_USER@@
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: [adm, sudo]
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
@@SSH_AUTHORIZED_KEYS_BLOCK@@

write_files:
  # ---- GitHub deploy key (read-only access to the bnk-forge repo) ----
  # Shredded by the bootstrap script once the clone succeeds — see below.
  - path: /root/.ssh/id_ed25519
    permissions: '0600'
    owner: root:root
    content: |
@@DEPLOY_KEY_PRIVATE_BLOCK@@

  - path: /root/.ssh/known_hosts
    permissions: '0644'
    owner: root:root
    content: |
@@GITHUB_KNOWN_HOSTS_BLOCK@@

  - path: /root/.ssh/config
    permissions: '0600'
    owner: root:root
    content: |
      Host github.com
        IdentityFile /root/.ssh/id_ed25519
        IdentitiesOnly yes
        UserKnownHostsFile /root/.ssh/known_hosts
        StrictHostKeyChecking yes

  # ---- SSHd hardening (drop-in sorts last, overrides image defaults) ----
  - path: /etc/ssh/sshd_config.d/99-hardening.conf
    permissions: '0644'
    owner: root:root
    content: |
      # Asserted rather than assumed: the Ubuntu cloudimg already disables
      # password auth, but provider marketplace images don't always.
      PasswordAuthentication no
      KbdInteractiveAuthentication no
      PermitRootLogin no
      MaxAuthTries 3
      LoginGraceTime 30
      ClientAliveInterval 300
      ClientAliveCountMax 2
      # Restricted to the admin account this cloud-init creates. On clouds that
      # create their own account (GCP OS Login, Azure admin username), set
      # ADMIN_USER in config.env to match or you will be locked out.
      AllowUsers @@ADMIN_USER@@

  # ---- First-boot bootstrap unit (runs git clone + make install) ----
  - path: /etc/systemd/system/bnk-forge-bootstrap.service
    permissions: '0644'
    owner: root:root
    content: |
      [Unit]
      Description=First-boot bnk-forge bootstrap (clone + make install)
      After=network-online.target docker.service
      Wants=network-online.target docker.service
      ConditionPathExists=!/var/lib/bnk-forge/bootstrap.done
      StartLimitIntervalSec=2h
      StartLimitBurst=4

      [Service]
      Type=oneshot
      ExecStart=/usr/local/bin/bnk-forge-bootstrap
      RemainAfterExit=yes
      StandardOutput=journal+console
      StandardError=journal+console
      TimeoutStartSec=30min
      # Retry transient failures (registry hiccup, OOM during build) a few
      # times before giving up; retry manually with `systemctl start`.
      Restart=on-failure
      RestartSec=30s

      [Install]
      WantedBy=multi-user.target

  - path: /usr/local/bin/bnk-forge-bootstrap
    permissions: '0755'
    owner: root:root
    content: |
      #!/usr/bin/env bash
      set -euxo pipefail
      mkdir -p /var/lib/bnk-forge /opt
      chmod 700 /root/.ssh
      cd /opt
      if [ ! -d bnk-forge ]; then
        git clone --branch @@BRANCH@@ @@REPO_URL@@ bnk-forge
      fi
      # The clone is done — the deploy key has no further use inside the VM, and
      # leaving it around means a VM compromise (or, on clouds, an SSRF against
      # the metadata service) yields repo read access. Remove it.
      shred -u /root/.ssh/id_ed25519 2>/dev/null || rm -f /root/.ssh/id_ed25519
      cd /opt/bnk-forge
      make install
      touch /var/lib/bnk-forge/bootstrap.done
      echo "[bnk-forge-bootstrap] complete — bnk-forge installed on $(hostname)"

package_update: true
package_upgrade: false
packages:
  - ca-certificates
  - curl
  - git
  - make
  - jq
  - ufw
  - qemu-guest-agent   # lets libvirt/Cockpit see real IP+prefix and supports clean shutdown
  - fail2ban           # drops scanning bots; key-only SSH already prevents brute force, this just keeps logs quiet

runcmd:
  # ---- Lock down /root/.ssh perms (write_files left it 0755) ----
  - chmod 700 /root/.ssh
  # ---- Apply sshd hardening drop-in ----
  - systemctl reload ssh.service || systemctl reload sshd.service || true
  # ---- Host firewall: SSH + the ports bnk-forge's proxy serves ----
  # 8443/8082, not 443/80: with network_mode: host the proxy binds those
  # directly on the host (docker-compose.yml, proxy service).
  - ufw allow 22/tcp
  - ufw allow 8443/tcp
  - ufw allow 8082/tcp
  - ufw --force enable
  # ---- Docker apt repo, then Docker engine ----
  # The source list is written here, not in write_files: it must land after the
  # keyring exists (otherwise the packages: stage runs `apt-get update` against
  # a repo whose signed-by file is missing and cloud-init reports a failure),
  # and the arch/codename need shell expansion to stay correct on arm64 hosts
  # and on later Ubuntu LTS images.
  - install -m 0755 -d /etc/apt/keyrings
  - curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  - chmod a+r /etc/apt/keyrings/docker.asc
  - 'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list'
  - apt-get update
  - DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  - usermod -aG docker @@ADMIN_USER@@
  # ---- Kick off the first-boot bootstrap ----
  - systemctl daemon-reload
  - systemctl enable --now bnk-forge-bootstrap.service

final_message: "cloud-init done in $UPTIME seconds. Bootstrap continues in bnk-forge-bootstrap.service — tail with: journalctl -u bnk-forge-bootstrap -f"
