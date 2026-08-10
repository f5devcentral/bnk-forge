"""
Unit tests for SSH jumphost tunnelling in DiscoveryService.

Tests cover:
  - _open_jumphost_channel: happy path, auth failure, unreachable host, channel failure
  - _probe_node_ssh: jumphost path vs. direct path, log annotation, cleanup on error
  - _probe_kubeconfig_via_ssh: jumphost path vs. direct path

All SSH I/O is mocked via unittest.mock — no real network connections are made.
"""

from unittest.mock import ANY, MagicMock, patch

import paramiko
import pytest
from paramiko.ssh_exception import AuthenticationException, NoValidConnectionsError, SSHException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

JUMPHOST_CRED = {
    "host": "10.0.0.1",
    "port": 22,
    "username": "jump-user",
    "auth_type": "password",
    "password": "s3cr3t",
    "private_key": None,
    "key_passphrase": None,
}

NODE_CRED = {
    "host": "192.168.1.10",
    "username": "ubuntu",
    "port": 22,
    "auth_type": "password",
    "password": "node-pass",
    "private_key": None,
    "key_passphrase": None,
}


def _make_mock_client() -> MagicMock:
    """Return a mock SSHClient with a usable transport + exec_command stub."""
    client = MagicMock(spec=paramiko.SSHClient)
    transport = MagicMock()
    channel = MagicMock(spec=paramiko.Channel)
    transport.open_channel.return_value = channel
    client.get_transport.return_value = transport
    # exec_command returns (stdin, stdout, stderr); stdout needs recv_exit_status + read
    stdout = MagicMock()
    stdout.channel.recv_exit_status.return_value = 0
    stdout.read.return_value = b""
    client.exec_command.return_value = (MagicMock(), stdout, MagicMock())
    return client


# ---------------------------------------------------------------------------
# _open_jumphost_channel — happy path
# ---------------------------------------------------------------------------

class TestOpenJumphostChannel:
    """Tests for the _open_jumphost_channel module-level function."""

    def test_opens_direct_tcpip_channel_to_target(self):
        """Happy path: connects to jumphost, opens direct-tcpip channel, returns both."""
        from services.discovery_service import _open_jumphost_channel

        mock_jumphost_client = _make_mock_client()

        with patch("services.discovery_service._ssh_connect", return_value=mock_jumphost_client) as mock_connect:
            jh_client, sock = _open_jumphost_channel(JUMPHOST_CRED, "192.168.1.10", 22)

        # Should have connected to the jumphost with the right credentials
        mock_connect.assert_called_once_with(
            host=JUMPHOST_CRED["host"],
            username=JUMPHOST_CRED["username"],
            port=JUMPHOST_CRED["port"],
            auth_type=JUMPHOST_CRED["auth_type"],
            password=JUMPHOST_CRED["password"],
            private_key=JUMPHOST_CRED["private_key"],
            key_passphrase=JUMPHOST_CRED["key_passphrase"],
        )

        # Should have opened a direct-tcpip channel to the target
        transport = mock_jumphost_client.get_transport.return_value
        transport.open_channel.assert_called_once_with(
            "direct-tcpip",
            ("192.168.1.10", 22),
            ("", 0),
            timeout=ANY,
        )

        assert jh_client is mock_jumphost_client
        assert sock is transport.open_channel.return_value

    def test_passes_non_default_port_to_jumphost(self):
        """Port from credential dict is forwarded correctly."""
        from services.discovery_service import _open_jumphost_channel

        cred = {**JUMPHOST_CRED, "port": 2222}
        mock_client = _make_mock_client()

        with patch("services.discovery_service._ssh_connect", return_value=mock_client) as mock_connect:
            _open_jumphost_channel(cred, "192.168.1.10", 22)

        mock_connect.assert_called_once_with(
            host=cred["host"],
            username=cred["username"],
            port=2222,
            auth_type=ANY,
            password=ANY,
            private_key=ANY,
            key_passphrase=ANY,
        )

    # ------------------------------------------------------------------
    # Error paths
    # ------------------------------------------------------------------

    def test_authentication_failure_raises_ssh_exception_with_meaningful_message(self):
        """Jumphost auth failure → SSHException mentioning the jumphost host."""
        from services.discovery_service import _open_jumphost_channel

        with patch(
            "services.discovery_service._ssh_connect",
            side_effect=AuthenticationException("bad credentials"),
        ):
            with pytest.raises(SSHException, match=r"authentication failed"):
                _open_jumphost_channel(JUMPHOST_CRED, "192.168.1.10", 22)

    def test_unreachable_host_raises_ssh_exception(self):
        """Jumphost unreachable → SSHException mentioning the jumphost host."""
        from services.discovery_service import _open_jumphost_channel

        with patch(
            "services.discovery_service._ssh_connect",
            side_effect=NoValidConnectionsError({("10.0.0.1", 22): Exception("refused")}),
        ):
            with pytest.raises(SSHException, match=r"host unreachable"):
                _open_jumphost_channel(JUMPHOST_CRED, "192.168.1.10", 22)

    def test_channel_open_failure_closes_jumphost_client_and_raises(self):
        """If direct-tcpip channel fails, jumphost_client is closed before raising."""
        from services.discovery_service import _open_jumphost_channel

        mock_client = _make_mock_client()
        transport = mock_client.get_transport.return_value
        transport.open_channel.side_effect = Exception("channel refused")

        with patch("services.discovery_service._ssh_connect", return_value=mock_client):
            with pytest.raises(SSHException, match=r"failed to open channel"):
                _open_jumphost_channel(JUMPHOST_CRED, "192.168.1.10", 22)

        # jumphost connection must be closed even when channel open fails
        mock_client.close.assert_called_once()

    def test_null_transport_raises_ssh_exception(self):
        """If get_transport() returns None, an SSHException with a clear message is raised."""
        from services.discovery_service import _open_jumphost_channel

        mock_client = _make_mock_client()
        mock_client.get_transport.return_value = None

        with patch("services.discovery_service._ssh_connect", return_value=mock_client):
            with pytest.raises(SSHException, match=r"failed to open channel"):
                _open_jumphost_channel(JUMPHOST_CRED, "192.168.1.10", 22)

        mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# _probe_node_ssh — jumphost routing
# ---------------------------------------------------------------------------

class TestProbeNodeSshJumphost:
    """Tests that _probe_node_ssh routes traffic through the jumphost when provided."""

    def _minimal_exec_side_effect(self):
        """Return a side_effect list that satisfies all _ssh_exec calls in _probe_node_ssh."""
        stdout = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = b""
        return (MagicMock(), stdout, MagicMock())

    def test_with_jumphost_cred_calls_open_jumphost_channel(self):
        """When jumphost_cred is provided, _open_jumphost_channel is called before target connect."""
        from services.discovery_service import _probe_node_ssh

        mock_jumphost_client = _make_mock_client()
        mock_channel = MagicMock(spec=paramiko.Channel)
        mock_target_client = _make_mock_client()

        with (
            patch(
                "services.discovery_service._open_jumphost_channel",
                return_value=(mock_jumphost_client, mock_channel),
            ) as mock_open_jh,
            patch(
                "services.discovery_service._ssh_connect",
                return_value=mock_target_client,
            ) as mock_connect,
        ):
            _probe_node_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=JUMPHOST_CRED,
            )

        # jumphost channel must be opened
        mock_open_jh.assert_called_once_with(JUMPHOST_CRED, NODE_CRED["host"], NODE_CRED["port"])

        # target must be connected through the returned sock (the channel)
        mock_connect.assert_called_once_with(
            host=NODE_CRED["host"],
            username=NODE_CRED["username"],
            port=NODE_CRED["port"],
            auth_type=NODE_CRED["auth_type"],
            password=NODE_CRED["password"],
            private_key=None,
            key_passphrase=None,
            sock=mock_channel,          # <-- the tunnel channel
        )

    def test_without_jumphost_cred_does_not_call_open_jumphost_channel(self):
        """Direct connection: _open_jumphost_channel must NOT be called."""
        from services.discovery_service import _probe_node_ssh

        mock_target_client = _make_mock_client()

        with (
            patch(
                "services.discovery_service._open_jumphost_channel",
            ) as mock_open_jh,
            patch(
                "services.discovery_service._ssh_connect",
                return_value=mock_target_client,
            ) as mock_connect,
        ):
            _probe_node_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=None,     # <-- no jumphost
            )

        mock_open_jh.assert_not_called()

        # target connected directly — sock must be None
        mock_connect.assert_called_once_with(
            host=NODE_CRED["host"],
            username=NODE_CRED["username"],
            port=NODE_CRED["port"],
            auth_type=NODE_CRED["auth_type"],
            password=NODE_CRED["password"],
            private_key=None,
            key_passphrase=None,
            sock=None,
        )

    def test_log_contains_via_jumphost_annotation_when_tunnelled(self):
        """Result log must mention the jumphost host and username when tunnelling."""
        from services.discovery_service import _probe_node_ssh

        mock_jumphost_client = _make_mock_client()
        mock_channel = MagicMock(spec=paramiko.Channel)
        mock_target_client = _make_mock_client()

        with (
            patch(
                "services.discovery_service._open_jumphost_channel",
                return_value=(mock_jumphost_client, mock_channel),
            ),
            patch(
                "services.discovery_service._ssh_connect",
                return_value=mock_target_client,
            ),
        ):
            result = _probe_node_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=JUMPHOST_CRED,
            )

        assert "log" in result
        assert "via jumphost" in result["log"]
        assert JUMPHOST_CRED["host"] in result["log"]
        assert JUMPHOST_CRED["username"] in result["log"]

    def test_log_has_no_jumphost_annotation_for_direct_connection(self):
        """Log must NOT contain jumphost annotation for direct connections."""
        from services.discovery_service import _probe_node_ssh

        mock_target_client = _make_mock_client()

        with (
            patch("services.discovery_service._open_jumphost_channel"),
            patch("services.discovery_service._ssh_connect", return_value=mock_target_client),
        ):
            result = _probe_node_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=None,
            )

        assert "via jumphost" not in result.get("log", "")

    def test_jumphost_client_is_closed_after_successful_probe(self):
        """Both target and jumphost clients must be closed after a successful probe."""
        from services.discovery_service import _probe_node_ssh

        mock_jumphost_client = _make_mock_client()
        mock_channel = MagicMock(spec=paramiko.Channel)
        mock_target_client = _make_mock_client()

        with (
            patch(
                "services.discovery_service._open_jumphost_channel",
                return_value=(mock_jumphost_client, mock_channel),
            ),
            patch(
                "services.discovery_service._ssh_connect",
                return_value=mock_target_client,
            ),
        ):
            _probe_node_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=JUMPHOST_CRED,
            )

        mock_target_client.close.assert_called_once()
        mock_jumphost_client.close.assert_called_once()

    def test_jumphost_client_is_closed_even_when_probe_raises(self):
        """jumphost_client.close() must be called in the finally block on error."""
        from services.discovery_service import _probe_node_ssh

        mock_jumphost_client = _make_mock_client()
        mock_channel = MagicMock(spec=paramiko.Channel)
        # Simulate exec_command raising mid-probe
        mock_target_client = _make_mock_client()
        mock_target_client.exec_command.side_effect = SSHException("connection lost")

        with (
            patch(
                "services.discovery_service._open_jumphost_channel",
                return_value=(mock_jumphost_client, mock_channel),
            ),
            patch(
                "services.discovery_service._ssh_connect",
                return_value=mock_target_client,
            ),
            pytest.raises(SSHException),
        ):
            _probe_node_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=JUMPHOST_CRED,
            )

        mock_jumphost_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# _probe_kubeconfig_via_ssh — jumphost routing
# ---------------------------------------------------------------------------

# Minimal but valid kubeconfig that satisfies _probe_kubeconfig_via_ssh.
# The function calls ``kubectl config view --raw`` and raises BadRequestError
# if the output is empty, so the mock must return real YAML content.
_MINIMAL_KUBECONFIG = """\
apiVersion: v1
kind: Config
current-context: test-ctx
clusters:
- name: test-cluster
  cluster:
    server: https://192.168.1.10:6443
contexts:
- name: test-ctx
  context:
    cluster: test-cluster
    user: admin
users:
- name: admin
  user:
    token: fake-token
"""


class TestProbeKubeconfigViaSSHJumphost:
    """Tests that _probe_kubeconfig_via_ssh routes through the jumphost when provided."""

    def _make_kubeconfig_exec(self) -> MagicMock:
        """Return a client mock whose exec_command returns a valid kubeconfig YAML."""
        client = _make_mock_client()
        stdout = MagicMock()
        stdout.channel.recv_exit_status.return_value = 0
        stdout.read.return_value = _MINIMAL_KUBECONFIG.encode()
        client.exec_command.return_value = (MagicMock(), stdout, MagicMock())
        return client

    def test_with_jumphost_cred_passes_channel_as_sock(self):
        """When jumphost_cred provided, the direct-tcpip channel is passed as sock= to target."""
        from services.discovery_service import _probe_kubeconfig_via_ssh

        mock_jumphost_client = _make_mock_client()
        mock_channel = MagicMock(spec=paramiko.Channel)
        mock_target_client = self._make_kubeconfig_exec()

        with (
            patch(
                "services.discovery_service._open_jumphost_channel",
                return_value=(mock_jumphost_client, mock_channel),
            ) as mock_open_jh,
            patch(
                "services.discovery_service._ssh_connect",
                return_value=mock_target_client,
            ) as mock_connect,
        ):
            _probe_kubeconfig_via_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=JUMPHOST_CRED,
            )

        mock_open_jh.assert_called_once_with(JUMPHOST_CRED, NODE_CRED["host"], NODE_CRED["port"])
        mock_connect.assert_called_once_with(
            host=NODE_CRED["host"],
            username=NODE_CRED["username"],
            port=NODE_CRED["port"],
            auth_type=NODE_CRED["auth_type"],
            password=NODE_CRED["password"],
            private_key=None,
            key_passphrase=None,
            sock=mock_channel,
        )

    def test_without_jumphost_cred_connects_directly(self):
        """Direct path: _open_jumphost_channel not called, sock=None."""
        from services.discovery_service import _probe_kubeconfig_via_ssh

        mock_target_client = self._make_kubeconfig_exec()

        with (
            patch("services.discovery_service._open_jumphost_channel") as mock_open_jh,
            patch("services.discovery_service._ssh_connect", return_value=mock_target_client) as mock_connect,
        ):
            _probe_kubeconfig_via_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=None,
            )

        mock_open_jh.assert_not_called()
        mock_connect.assert_called_once_with(
            host=ANY,
            username=ANY,
            port=ANY,
            auth_type=ANY,
            password=ANY,
            private_key=ANY,
            key_passphrase=ANY,
            sock=None,
        )

    def test_jumphost_client_closed_after_kubeconfig_probe(self):
        """Jumphost client is closed after kubeconfig probe completes."""
        from services.discovery_service import _probe_kubeconfig_via_ssh

        mock_jumphost_client = _make_mock_client()
        mock_channel = MagicMock(spec=paramiko.Channel)
        mock_target_client = self._make_kubeconfig_exec()

        with (
            patch(
                "services.discovery_service._open_jumphost_channel",
                return_value=(mock_jumphost_client, mock_channel),
            ),
            patch("services.discovery_service._ssh_connect", return_value=mock_target_client),
        ):
            _probe_kubeconfig_via_ssh(
                host=NODE_CRED["host"],
                username=NODE_CRED["username"],
                port=NODE_CRED["port"],
                auth_type=NODE_CRED["auth_type"],
                password=NODE_CRED["password"],
                private_key=None,
                jumphost_cred=JUMPHOST_CRED,
            )

        mock_jumphost_client.close.assert_called_once()
        mock_target_client.close.assert_called_once()
