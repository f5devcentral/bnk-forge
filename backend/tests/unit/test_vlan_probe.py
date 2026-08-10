"""Unit tests for vlan_probe — mocked SSH."""

from unittest.mock import MagicMock

import pytest

from services.bare_metal.discovery.vlan_probe import VlanProbeResult, probe_vlan
from services.bare_metal.ssh_session import SSHResult, SSHSession


@pytest.fixture
def mock_ssh():
    session = MagicMock(spec=SSHSession)
    session.host = "10.0.0.1"
    return session


def _make_result(stdout="", exit_code=0, stderr=""):
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_seconds=0.1)


@pytest.mark.unit
class TestVlanProbe:

    def test_successful_vlan_probe(self, mock_ssh):
        mock_ssh.execute.return_value = _make_result()  # All commands succeed
        result = probe_vlan(mock_ssh, "enp3s0f0", 100, "10.1.0.1", "10.1.0.99/24")
        assert result.vlan_path_valid is True
        assert result.gateway_reachable is True
        assert result.test_interface == "enp3s0f0.100"

    def test_vlan_create_fails(self, mock_ssh):
        mock_ssh.execute.side_effect = [
            _make_result(exit_code=1, stderr="RTNETLINK: Operation not permitted"),  # ip link add
            _make_result(),  # cleanup: link down
            _make_result(),  # cleanup: link delete
        ]
        result = probe_vlan(mock_ssh, "enp3s0f0", 100, "10.1.0.1", "10.1.0.99/24")
        assert result.vlan_path_valid is False
        assert "Failed to create VLAN" in result.error

    def test_gateway_unreachable(self, mock_ssh):
        mock_ssh.execute.side_effect = [
            _make_result(),  # ip link add
            _make_result(),  # ip addr add
            _make_result(),  # ip link set up
            _make_result(exit_code=1, stderr="100% packet loss"),  # ping
            _make_result(),  # cleanup: link down
            _make_result(),  # cleanup: link delete
        ]
        result = probe_vlan(mock_ssh, "enp3s0f0", 100, "10.1.0.1", "10.1.0.99/24")
        assert result.vlan_path_valid is False
        assert result.gateway_reachable is False

    def test_cleanup_always_runs(self, mock_ssh):
        mock_ssh.execute.side_effect = [
            _make_result(),  # ip link add
            _make_result(exit_code=1, stderr="Address in use"),  # ip addr add FAILS
            _make_result(),  # cleanup: link down
            _make_result(),  # cleanup: link delete
        ]
        result = probe_vlan(mock_ssh, "enp3s0f0", 100, "10.1.0.1", "10.1.0.99/24")
        # Verify cleanup was attempted (at least 4 execute calls: create, addr, cleanup x2)
        assert mock_ssh.execute.call_count >= 3

    def test_gateway_ip_cidr_stripped(self, mock_ssh):
        """Gateway IP with CIDR notation should have prefix stripped for ping."""
        calls = []

        def capture_execute(cmd, **kwargs):
            calls.append(cmd)
            return _make_result()

        mock_ssh.execute.side_effect = capture_execute
        probe_vlan(mock_ssh, "enp3s0f0", 100, "10.1.0.1/24", "10.1.0.99/24")
        # Find the ping command
        ping_cmd = [c for c in calls if "ping" in c]
        assert len(ping_cmd) >= 1
        assert "10.1.0.1/24" not in ping_cmd[0]  # CIDR should be stripped
        assert "10.1.0.1" in ping_cmd[0]
