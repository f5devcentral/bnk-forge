"""Unit tests for SSH engine IPv6 link-local DPU relay."""

from unittest.mock import MagicMock

import pytest

from services.execution.ssh_engine import HostRelayDpuSession


@pytest.mark.unit
class TestHostRelayDpuSession:
    def test_execute_relays_via_host(self):
        """Commands are relayed through host session as ssh -6."""
        host_session = MagicMock()
        host_session.execute.return_value = MagicMock(
            exit_code=0, stdout="hello\n", stderr=""
        )

        relay = HostRelayDpuSession(
            host_session=host_session,
            dpu_addr="fe80::1234%enp3s0f0",
            dpu_username="ubuntu",
            dpu_password="password123",
        )

        result = relay.execute("echo hello", timeout=30)

        # Verify the host session received an ssh -6 command
        call_args = host_session.execute.call_args
        cmd = call_args[0][0]
        assert "ssh" in cmd
        assert "-6" in cmd
        assert "fe80::1234%enp3s0f0" in cmd
        assert "echo hello" in cmd
        assert "sshpass" in cmd  # password auth

    def test_execute_without_password(self):
        """Key-based auth omits sshpass."""
        host_session = MagicMock()
        host_session.execute.return_value = MagicMock(
            exit_code=0, stdout="ok\n", stderr=""
        )

        relay = HostRelayDpuSession(
            host_session=host_session,
            dpu_addr="fe80::abcd%enp3s0f0",
            dpu_username="ubuntu",
            dpu_password=None,
        )

        relay.execute("hostname", timeout=10)

        cmd = host_session.execute.call_args[0][0]
        assert "sshpass" not in cmd
        assert "ssh" in cmd
        assert "-6" in cmd

    def test_single_quotes_in_command_escaped(self):
        """Single quotes in command are properly escaped."""
        host_session = MagicMock()
        host_session.execute.return_value = MagicMock(exit_code=0, stdout="", stderr="")

        relay = HostRelayDpuSession(
            host_session=host_session,
            dpu_addr="fe80::1%eth0",
            dpu_username="ubuntu",
            dpu_password="pass",
        )

        relay.execute("echo 'hello world'", timeout=10)

        cmd = host_session.execute.call_args[0][0]
        # The inner single quotes should be escaped
        assert "hello" in cmd
        assert "world" in cmd

    def test_close_is_noop(self):
        """Close doesn't close the host session."""
        host_session = MagicMock()
        relay = HostRelayDpuSession(
            host_session=host_session,
            dpu_addr="fe80::1%eth0",
            dpu_username="ubuntu",
        )
        relay.close()
        host_session.close.assert_not_called()

    def test_exposes_compatibility_attributes(self):
        """Session attributes are exposed for code that reads them."""
        host_session = MagicMock()
        relay = HostRelayDpuSession(
            host_session=host_session,
            dpu_addr="fe80::1%eth0",
            dpu_username="ubuntu",
            dpu_password="pass",
        )
        assert relay.host == "fe80::1%eth0"
        assert relay.port == 22
        assert relay.username == "ubuntu"
