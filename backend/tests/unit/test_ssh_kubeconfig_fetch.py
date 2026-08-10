"""
Unit tests for services/ssh_kubeconfig_fetch.py.

Mocks paramiko.SSHClient to verify the flatten-ladder probe order:
  1. command -v oc
  2. command -v kubectl
  3. fallback cat ~/.kube/config
  4. fallback sudo cat /etc/kubernetes/admin.conf  (sudo -n if no password,
     sudo -S if a password is supplied)

Tests verify that:
  - When oc is available, oc config view --flatten is used.
  - When only kubectl is available, kubectl config view --flatten is used.
  - When neither is available, cat ~/.kube/config is used.
  - When ~/.kube/config is missing too, sudo cat /etc/kubernetes/admin.conf
    is the universal fallback (covers kubeadm-init'd hosts where the SSH user
    has no kubeconfig — see audit FORENSIC_REGRESSION_AUDIT_2026_05_28.md Issue 13).
  - The sudo rung uses ``-n`` when no password supplied, ``-S`` when supplied.
  - The sudo password is written to stdin and stdin is closed for EOF.
  - When all four rungs fail, ValueError is raised with a message that lists
    every rung tried.
"""
from unittest.mock import MagicMock

import pytest

from services.ssh_kubeconfig_fetch import fetch_flattened_kubeconfig_over_ssh

FLAT_KUBECONFIG = """apiVersion: v1
kind: Config
clusters:
- name: c
  cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCg==
    server: https://c:6443
contexts:
- name: c
  context:
    cluster: c
    user: u
users:
- name: u
  user:
    token: fake-token
"""


def _make_channel(exit_code: int) -> MagicMock:
    ch = MagicMock()
    ch.recv_exit_status.return_value = exit_code
    return ch


def _make_stdout(exit_code: int, output: str = "") -> MagicMock:
    stdout = MagicMock()
    stdout.channel = _make_channel(exit_code)
    stdout.read.return_value = output.encode()
    return stdout


def _make_stdin() -> MagicMock:
    """Build a stdin mock that records writes and exposes a channel for shutdown_write."""
    stdin = MagicMock()
    stdin.channel = MagicMock()
    return stdin


def _make_client(side_effects: list) -> MagicMock:
    """Build a paramiko.SSHClient mock whose exec_command returns side_effects in order.

    Each element of ``side_effects`` is a stdout MagicMock. stdin and stderr are
    created fresh per call so callers can inspect write/shutdown_write on stdin.
    """
    client = MagicMock()
    client._stdins: list[MagicMock] = []  # captured for assertions on rung-4

    def _exec(*_args, **_kwargs):
        stdin = _make_stdin()
        client._stdins.append(stdin)
        stdout = side_effects[_exec.call_count]
        _exec.call_count += 1
        return (stdin, stdout, MagicMock())

    _exec.call_count = 0
    client.exec_command.side_effect = _exec
    return client


class TestFlattenLadder:
    """Verify the probe order and fallback behavior."""

    def test_oc_used_first_when_available(self):
        """When oc is available, oc config view --flatten --minify --raw is called."""
        stdouts = [
            _make_stdout(0),  # command -v oc → found
            _make_stdout(0, FLAT_KUBECONFIG),  # oc config view
        ]
        client = _make_client(stdouts)

        result = fetch_flattened_kubeconfig_over_ssh(client)

        assert "apiVersion" in result
        calls = [c[0][0] for c in client.exec_command.call_args_list]
        assert calls[0] == "command -v oc"
        assert "oc config view" in calls[1]
        assert "--flatten" in calls[1]

    def test_kubectl_used_when_oc_absent(self):
        """When oc is absent, kubectl is tried next."""
        stdouts = [
            _make_stdout(1),  # command -v oc → not found
            _make_stdout(0),  # command -v kubectl → found
            _make_stdout(0, FLAT_KUBECONFIG),  # kubectl config view
        ]
        client = _make_client(stdouts)

        result = fetch_flattened_kubeconfig_over_ssh(client)

        assert "apiVersion" in result
        calls = [c[0][0] for c in client.exec_command.call_args_list]
        assert calls[0] == "command -v oc"
        assert calls[1] == "command -v kubectl"
        assert "kubectl config view" in calls[2]
        assert "--flatten" in calls[2]

    def test_fallback_cat_when_neither_tool_available(self):
        """When neither oc nor kubectl found, cat ~/.kube/config is tried before sudo."""
        stdouts = [
            _make_stdout(1),  # command -v oc → not found
            _make_stdout(1),  # command -v kubectl → not found
            _make_stdout(0, FLAT_KUBECONFIG),  # cat ~/.kube/config → ok
        ]
        client = _make_client(stdouts)

        result = fetch_flattened_kubeconfig_over_ssh(client)

        assert "apiVersion" in result
        calls = [c[0][0] for c in client.exec_command.call_args_list]
        assert "cat ~/.kube/config" in calls[2]
        # Rung 4 must NOT have been reached because rung 3 succeeded.
        assert len(calls) == 3

    def test_falls_through_to_kubectl_when_oc_produces_empty(self):
        """If oc is found but produces empty/invalid output, ladder continues to kubectl."""
        stdouts = [
            _make_stdout(0),           # command -v oc → found
            _make_stdout(0, ""),       # oc config view → empty (invalid)
            _make_stdout(0),           # command -v kubectl → found
            _make_stdout(0, FLAT_KUBECONFIG),  # kubectl config view → ok
        ]
        client = _make_client(stdouts)

        result = fetch_flattened_kubeconfig_over_ssh(client)

        assert "apiVersion" in result
        calls = [c[0][0] for c in client.exec_command.call_args_list]
        assert "kubectl config view" in calls[3]


class TestSudoFallback:
    """Verify rung 4 — sudo cat /etc/kubernetes/admin.conf.

    Regression coverage for the kubeadm-init / non-root-SSH-user case
    documented in docs/audit/FORENSIC_REGRESSION_AUDIT_2026_05_28.md Issue 13.
    """

    def test_sudo_n_used_when_no_password_supplied(self):
        """Without a sudo_password, rung 4 uses ``sudo -n`` (non-interactive)."""
        stdouts = [
            _make_stdout(1),                       # command -v oc → not found
            _make_stdout(1),                       # command -v kubectl → not found
            _make_stdout(0, ""),                   # cat ~/.kube/config → empty
            _make_stdout(0, FLAT_KUBECONFIG),      # sudo -n cat admin.conf → ok
        ]
        client = _make_client(stdouts)

        result = fetch_flattened_kubeconfig_over_ssh(client)

        assert "apiVersion" in result
        calls = [c[0][0] for c in client.exec_command.call_args_list]
        assert calls[3] == "sudo -n cat /etc/kubernetes/admin.conf"

    def test_sudo_S_used_when_password_supplied(self):
        """With a sudo_password, rung 4 uses ``sudo -S`` and pipes the password."""
        stdouts = [
            _make_stdout(1),                       # command -v oc → not found
            _make_stdout(1),                       # command -v kubectl → not found
            _make_stdout(0, ""),                   # cat ~/.kube/config → empty
            _make_stdout(0, FLAT_KUBECONFIG),      # sudo -S cat admin.conf → ok
        ]
        client = _make_client(stdouts)

        result = fetch_flattened_kubeconfig_over_ssh(
            client, sudo_password="hunter2",
        )

        assert "apiVersion" in result
        calls = [c[0][0] for c in client.exec_command.call_args_list]
        assert calls[3] == "sudo -S cat /etc/kubernetes/admin.conf"

        # Password must have been written to the rung-4 stdin, followed by a
        # shutdown_write() to signal EOF.
        rung4_stdin = client._stdins[3]
        write_calls = [c[0][0] for c in rung4_stdin.write.call_args_list]
        assert write_calls == ["hunter2\n"]
        rung4_stdin.flush.assert_called_once()
        rung4_stdin.channel.shutdown_write.assert_called_once()

    def test_sudo_S_failure_does_not_crash_when_shutdown_write_unsupported(self):
        """If shutdown_write raises (some paramiko transports don't support it),
        the ladder still completes — the exception is swallowed locally."""
        stdouts = [
            _make_stdout(1),
            _make_stdout(1),
            _make_stdout(0, ""),
            _make_stdout(0, FLAT_KUBECONFIG),
        ]
        client = _make_client(stdouts)
        # Mark the rung-4 stdin to throw on shutdown_write. We need to install
        # this on the stdin BEFORE exec_command is called, so wire it via the
        # factory: any stdin returned for call #4 (index 3) raises.
        original_side_effect = client.exec_command.side_effect

        def _exec_with_throwing_stdin(*args, **kwargs):
            stdin, stdout, stderr = original_side_effect(*args, **kwargs)
            # Identify rung 4 by call index — _stdins is appended-to per call.
            if len(client._stdins) == 4:
                stdin.channel.shutdown_write.side_effect = OSError("transport closed")
            return stdin, stdout, stderr

        client.exec_command.side_effect = _exec_with_throwing_stdin

        result = fetch_flattened_kubeconfig_over_ssh(
            client, sudo_password="hunter2",
        )

        assert "apiVersion" in result

    def test_raises_when_all_four_rungs_fail(self):
        """ValueError raised when no path yields a valid kubeconfig.

        Error message must reference all four rungs so operators can diagnose
        which fallback to fix on the host.
        """
        stdouts = [
            _make_stdout(1),       # command -v oc → not found
            _make_stdout(1),       # command -v kubectl → not found
            _make_stdout(0, ""),   # cat ~/.kube/config → empty
            _make_stdout(1, ""),   # sudo -n cat admin.conf → fail
        ]
        client = _make_client(stdouts)

        with pytest.raises(ValueError) as exc_info:
            fetch_flattened_kubeconfig_over_ssh(client)

        msg = str(exc_info.value)
        assert "No kubeconfig found" in msg
        assert "oc" in msg
        assert "kubectl" in msg
        assert "cat ~/.kube/config" in msg
        assert "sudo cat /etc/kubernetes/admin.conf" in msg

    def test_sudo_rung_skipped_when_rung3_succeeds(self):
        """If cat ~/.kube/config returns a valid kubeconfig, the sudo rung
        is NOT attempted (avoids superfluous sudo prompts/auth)."""
        stdouts = [
            _make_stdout(1),                       # command -v oc → not found
            _make_stdout(1),                       # command -v kubectl → not found
            _make_stdout(0, FLAT_KUBECONFIG),      # cat ~/.kube/config → ok
        ]
        client = _make_client(stdouts)

        result = fetch_flattened_kubeconfig_over_ssh(
            client, sudo_password="hunter2",
        )

        assert "apiVersion" in result
        assert client.exec_command.call_count == 3  # rung 4 not invoked
