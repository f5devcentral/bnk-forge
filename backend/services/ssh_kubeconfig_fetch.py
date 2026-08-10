"""
Remote kubeconfig fetch helper with flatten ladder.

Single authoritative location for the "oc → kubectl → raw cat → sudo cat" probe
used by both ssh_credential_service.probe_kubeconfig and
cluster_auto_registration_service._fetch_remote_kubeconfig.

The ladder ensures that:
  - OCP/RHEL hosts (which have ``oc`` but produce path-ref kubeconfigs from
    ~/.kube/config) get flattened before the YAML ever lands in Forge's DB.
  - kubeadm-init'd hosts where the SSH user has no ~/.kube/config still
    succeed via the sudo cat /etc/kubernetes/admin.conf fallback (rung 4).
    Rung 4 was the universal fallback before PR #110 (kubeconfig portability
    normalizer) replaced the old sudo-cat path. Restored after the regression
    was identified in
    docs/audit/FORENSIC_REGRESSION_AUDIT_2026_05_28.md (Issue 13).
"""
import logging

import paramiko

logger = logging.getLogger(__name__)


def fetch_flattened_kubeconfig_over_ssh(
    client: paramiko.SSHClient,
    *,
    sudo_password: str | None = None,
) -> str:
    """Run the remote-flatten ladder and return YAML text.

    Ladder order:
      1. ``command -v oc``  → if found, ``oc config view --flatten --minify --raw``
      2. ``command -v kubectl`` → if found, ``kubectl config view --flatten --minify --raw``
      3. Fallback: ``cat ~/.kube/config``
      4. Fallback: ``sudo cat /etc/kubernetes/admin.conf`` — universal recovery
         for kubeadm-init'd hosts where the SSH user has no ~/.kube/config but
         can sudo. Uses ``sudo -S`` with the supplied password, or ``sudo -n``
         (non-interactive) when no password is supplied to fail fast instead
         of hanging on a password prompt.

    Args:
        client: connected paramiko.SSHClient.
        sudo_password: optional password to feed to ``sudo -S`` for rung 4.
            If None, rung 4 tries ``sudo -n`` (NOPASSWD only).

    Returns raw YAML text (not base64). The caller is responsible for passing
    this through ``normalize_kubeconfig`` before persisting.

    Raises:
        ValueError: if no kubeconfig could be retrieved from the remote host.
    """
    for tool in ("oc", "kubectl"):
        rc = _exec_exit_code(client, f"command -v {tool}")
        if rc == 0:
            yaml_text = _exec_stdout(
                client, f"{tool} config view --flatten --minify --raw"
            )
            if yaml_text and ("apiVersion" in yaml_text or "clusters:" in yaml_text):
                logger.info(
                    "fetch_flattened_kubeconfig_over_ssh: flattened via %s", tool
                )
                return yaml_text
            logger.warning(
                "fetch_flattened_kubeconfig_over_ssh: %s found but "
                "produced empty/invalid output; continuing ladder",
                tool,
            )

    # Rung 3: raw read from SSH user's home.
    yaml_text = _exec_stdout(client, "cat ~/.kube/config")
    if yaml_text and ("apiVersion" in yaml_text or "clusters:" in yaml_text):
        logger.info(
            "fetch_flattened_kubeconfig_over_ssh: fell back to raw cat ~/.kube/config"
        )
        return yaml_text

    # Rung 4: sudo cat /etc/kubernetes/admin.conf — universal kubeadm fallback.
    # See module docstring for the regression context.
    yaml_text = _exec_sudo_stdout(
        client,
        "cat /etc/kubernetes/admin.conf",
        sudo_password=sudo_password,
    )
    if yaml_text and ("apiVersion" in yaml_text or "clusters:" in yaml_text):
        logger.info(
            "fetch_flattened_kubeconfig_over_ssh: fell back to "
            "sudo cat /etc/kubernetes/admin.conf"
        )
        return yaml_text

    raise ValueError(
        "No kubeconfig found on remote host. "
        "Tried oc, kubectl, cat ~/.kube/config, and "
        "sudo cat /etc/kubernetes/admin.conf."
    )


def _exec_exit_code(client: paramiko.SSHClient, cmd: str) -> int:
    """Run cmd on remote, return exit code. Ignores stdout/stderr."""
    _stdin, stdout, _stderr = client.exec_command(cmd, timeout=10)
    return stdout.channel.recv_exit_status()


def _exec_stdout(client: paramiko.SSHClient, cmd: str) -> str:
    """Run cmd on remote, return decoded stdout stripped of whitespace."""
    _stdin, stdout, _stderr = client.exec_command(cmd, timeout=15)
    stdout.channel.recv_exit_status()  # wait for completion
    return stdout.read().decode("utf-8").strip()


def _exec_sudo_stdout(
    client: paramiko.SSHClient,
    cmd: str,
    *,
    sudo_password: str | None,
) -> str:
    """Run ``sudo cmd`` on remote, optionally piping a password via stdin.

    When ``sudo_password`` is None, uses ``sudo -n`` (non-interactive). That
    flag makes sudo fail fast if the SSH user lacks NOPASSWD sudo, which
    prevents the channel from blocking on a password prompt that never gets
    answered.

    When ``sudo_password`` is provided, uses ``sudo -S`` and writes the
    password to stdin followed by an explicit shutdown_write() (EOF). Without
    the EOF signal, ``sudo`` can hang waiting for additional input on hosts
    where the user is not in sudoers and the prompt loops.

    Returns decoded stdout on exit code 0 with non-empty output, or an empty
    string on any failure (caller checks for empty / 'apiVersion' marker).
    """
    if sudo_password is None:
        full_cmd = f"sudo -n {cmd}"
        _stdin, stdout, _stderr = client.exec_command(full_cmd, timeout=15)
        if stdout.channel.recv_exit_status() == 0:
            return stdout.read().decode("utf-8").strip()
        return ""

    full_cmd = f"sudo -S {cmd}"
    stdin, stdout, _stderr = client.exec_command(full_cmd, timeout=15)
    try:
        stdin.write(sudo_password + "\n")
        stdin.flush()
        try:
            stdin.channel.shutdown_write()
        except Exception:
            pass
    except Exception:
        return ""
    if stdout.channel.recv_exit_status() == 0:
        return stdout.read().decode("utf-8").strip()
    return ""
