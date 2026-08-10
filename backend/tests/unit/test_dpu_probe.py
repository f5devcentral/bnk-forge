"""Unit tests for dpu_probe — mocked SSH."""

from unittest.mock import MagicMock

import pytest

from services.bare_metal.discovery.dpu_probe import DpuProbeResult, probe_dpu
from services.bare_metal.ssh_session import SSHResult, SSHSession


@pytest.fixture
def mock_ssh():
    """Mock that works for both SSHSession and RemoteSSHSession."""
    session = MagicMock()
    session.host = "192.168.100.2"
    session.port = 22
    session.is_reachable.return_value = True
    return session


def _make_result(stdout="", exit_code=0, stderr=""):
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_seconds=0.1)


@pytest.mark.unit
class TestDpuProbe:

    def test_unreachable_dpu(self, mock_ssh):
        mock_ssh.is_reachable.return_value = False
        result = probe_dpu(mock_ssh)
        assert result.ssh_reachable is False
        assert result.error is not None

    def test_doca_version_from_dpkg(self, mock_ssh):
        mock_ssh.execute.return_value = _make_result("ii  doca-runtime  2.7.0-1  arm64  DOCA runtime")
        result = probe_dpu(mock_ssh)
        assert result.doca_version == "2.7.0-1"

    def test_containerd_version(self, mock_ssh):
        responses = [
            _make_result(exit_code=1),  # doca dpkg
            _make_result(exit_code=1),  # doca mlnx-release
            _make_result("containerd github.com/containerd/containerd v1.7.12 abc123"),
        ]
        mock_ssh.execute.side_effect = responses + [_make_result(exit_code=1)] * 20
        result = probe_dpu(mock_ssh)
        assert result.containerd_version == "1.7.12"

    def test_runc_version(self, mock_ssh):
        responses = [_make_result(exit_code=1)] * 3  # Skip earlier
        responses.append(_make_result("runc version 1.1.12"))
        mock_ssh.execute.side_effect = responses + [_make_result(exit_code=1)] * 20
        result = probe_dpu(mock_ssh)
        assert result.runc_version == "1.1.12"

    def test_ovs_detected(self, mock_ssh):
        # Call order: doca dpkg, doca mlnx-release, containerd, runc, kubeadm, kubelet → 6 skips
        responses = [_make_result(exit_code=1)] * 6  # Skip to OVS
        responses.extend([
            _make_result("/usr/bin/ovs-vsctl"),  # which ovs-vsctl
            _make_result("p0\npf0hpf\nvf0\nvf1"),  # bridge ports
            _make_result("pf0hpf\nen3f0pf0sf0"),  # representors
        ])
        mock_ssh.execute.side_effect = responses + [_make_result(exit_code=1)] * 10
        result = probe_dpu(mock_ssh)
        assert result.ovs_installed is True
        assert "p0" in result.ovs_bridge_ports
        assert len(result.vf_representors) >= 1
