"""Unit tests for host_probe — mocked SSH."""

from unittest.mock import MagicMock

import pytest

from services.bare_metal.discovery.host_probe import HostProbeResult, probe_host
from services.bare_metal.ssh_session import SSHResult, SSHSession


@pytest.fixture
def mock_ssh():
    session = MagicMock(spec=SSHSession)
    session.host = "10.0.0.1"
    session.port = 22
    session.is_reachable.return_value = True
    return session


def _make_result(stdout="", exit_code=0, stderr=""):
    return SSHResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration_seconds=0.1)


@pytest.mark.unit
class TestHostProbe:

    def test_unreachable_host(self, mock_ssh):
        mock_ssh.is_reachable.return_value = False
        result = probe_host(mock_ssh)
        assert result.ssh_reachable is False
        assert result.error is not None

    def test_os_info_parsing(self, mock_ssh):
        mock_ssh.execute.return_value = _make_result(
            'ID="ubuntu"\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04.3 LTS"\n'
        )
        result = probe_host(mock_ssh)
        assert result.os_type == "ubuntu"
        assert result.os_version == "22.04"
        assert "Ubuntu" in result.os_pretty_name

    def test_kernel_and_arch(self, mock_ssh):
        responses = [
            _make_result('ID="ubuntu"\nVERSION_ID="22.04"\n'),  # os-release
            _make_result("5.15.0-91-generic"),  # uname -r
            _make_result("x86_64"),  # uname -m
        ]
        mock_ssh.execute.side_effect = responses + [_make_result(exit_code=1)] * 30
        result = probe_host(mock_ssh)
        assert result.kernel_version == "5.15.0-91-generic"
        assert result.architecture == "x86_64"

    def test_nic_mode_supernic(self, mock_ssh):
        # Simulate mlxconfig output with EMBEDDED_CPU
        responses = [
            _make_result('ID="ubuntu"\n'),  # os-release
            _make_result("5.15.0"),  # uname -r
            _make_result("x86_64"),  # uname -m
            _make_result(exit_code=1),  # lspci (no DPU detected)
            _make_result(exit_code=1),  # which mst (prereq: mst-tools absent)
            _make_result(exit_code=1),  # which bfb-install (prereq: rshim absent)
            _make_result("INTERNAL_CPU_MODEL       EMBEDDED_CPU(1)"),  # mlxconfig
        ]
        mock_ssh.execute.side_effect = responses + [_make_result(exit_code=1)] * 20
        result = probe_host(mock_ssh)
        # Parser normalizes EMBEDDED_CPU(1) → "1" (canonical form)
        assert result.nic_mode == "1"

    def test_rshim_present(self, mock_ssh):
        responses = [_make_result(exit_code=1)] * 7  # Skip: os, uname -r, uname -m, lspci, which mst, which bfb-install, mlxconfig
        responses.extend([
            _make_result(exit_code=1),  # interfaces
            _make_result(exit_code=1),  # VF count
            _make_result(exit_code=1),  # hugepages
            _make_result("/dev/rshim0/misc"),  # rshim present
        ])
        mock_ssh.execute.side_effect = responses + [_make_result(exit_code=1)] * 20
        result = probe_host(mock_ssh)
        assert result.rshim_present is True

    def test_k8s_detected(self, mock_ssh):
        responses = [_make_result(exit_code=1)] * 11  # Skip earlier probes (os, uname -r, uname -m, lspci, which mst, which bfb-install, mlxconfig, interfaces, VF count, hugepages, rshim)
        responses.extend([
            _make_result("/usr/bin/kubeadm"),  # which kubeadm || which kubelet
            _make_result('"gitVersion": "v1.29.8"'),  # kubeadm/kubectl version
            _make_result("node1   Ready   control-plane   10d   v1.29.8"),  # kubectl get nodes
        ])
        mock_ssh.execute.side_effect = responses + [_make_result(exit_code=1)] * 15
        result = probe_host(mock_ssh)
        assert result.k8s_installed is True
        assert result.k8s_version == "v1.29.8"
        assert result.k8s_running is True

    def test_default_route_parsed(self, mock_ssh):
        """Default route probe extracts route string and interface name.

        Call order (all returning exit_code=1 to skip earlier probes):
          1: os-release, 2: uname -r, 3: uname -m, 4: lspci,
          5: which mst (prereqs), 6: which bfb-install (prereqs),
          7: mlxconfig, 8: ls mlx5 interfaces, 9: cat sriov_numvfs,
          10: cat HugePages_Total, 11: ls rshim, 12: which kubeadm/kubelet,
          13: kubeadm/kubectl version, 14: kubectl get nodes
          → then default route call (#15).
        """
        responses = [_make_result(exit_code=1)] * 14
        responses.append(_make_result("default via 10.176.11.1 dev ens4f1 proto dhcp src 10.176.11.91 metric 100"))
        mock_ssh.execute.side_effect = responses + [_make_result(exit_code=1)] * 15
        result = probe_host(mock_ssh)
        assert result.default_route == "default via 10.176.11.1 dev ens4f1 proto dhcp src 10.176.11.91 metric 100"
        assert result.default_route_iface == "ens4f1"

    def test_default_route_not_set_on_failure(self, mock_ssh):
        """Default route stays None if probe command fails."""
        mock_ssh.execute.return_value = _make_result(exit_code=1)
        result = probe_host(mock_ssh)
        assert result.default_route is None
        assert result.default_route_iface is None

    def test_nic_mode_uses_sudo(self, mock_ssh):
        """mlxconfig probe command must include sudo prefix."""
        executed_commands = []

        def capture_execute(cmd, timeout=None):
            executed_commands.append(cmd)
            if "sudo mlxconfig" in cmd:
                return _make_result("INTERNAL_CPU_MODEL       SEPARATED_HOST(0)")
            return _make_result(exit_code=1)

        mock_ssh.execute.side_effect = capture_execute
        probe_host(mock_ssh)
        # Only the NIC-mode probe uses `sudo mlxconfig` — exclude DOCA's
        # `command -v mlxconfig` availability check.
        mlx_calls = [c for c in executed_commands if "sudo mlxconfig" in c]
        assert mlx_calls, "Expected at least one sudo mlxconfig call"
        assert all(c.lstrip().startswith("sudo") for c in mlx_calls), \
            f"mlxconfig call missing sudo: {mlx_calls}"


@pytest.mark.unit
class TestDocaStatusProbe:

    def test_doca_fully_installed(self, mock_ssh):
        """All DOCA components present → ready=True."""
        # 17 calls happen before _probe_doca_status (all failure path):
        # 1: cat /etc/os-release, 2: uname -r, 3: uname -m, 4: lspci,
        # 5: which mst (prereqs), 6: which bfb-install (prereqs),
        # 7: mlxconfig, 8: ls mlx5 interfaces, 9: cat sriov_numvfs,
        # 10: cat HugePages_Total, 11: ls /dev/rshim*/misc,
        # 12: which kubeadm || which kubelet, 13: kubeadm/kubectl version,
        # 14: kubectl get nodes, 15: ip route show default,
        # 16: test -d /opt/bnk/phase-c, 17: test -f /opt/bnk/phase-c/.completed
        responses = [_make_result(exit_code=1)] * 17
        doca_responses = [
            _make_result("ok"),          # repo_configured
            _make_result("ok"),          # profile_installed
            _make_result("2.9.2-0.1"),   # doca_version
            _make_result("ok"),          # mst_present
            _make_result("ok"),          # mlxconfig_present
            _make_result("ok"),          # rshim_present
            _make_result("ok"),          # bfb_install_present
            _make_result("active"),      # openibd_loaded
            _make_result("/dev/mst/mt41692_pciconf0"),  # mst_devices
        ]
        mock_ssh.execute.side_effect = responses + doca_responses
        result = probe_host(mock_ssh)
        assert result.doca_status is not None
        assert result.doca_status["ready"] is True
        assert result.doca_status["repo_configured"] is True
        assert result.doca_status["profile_installed"] is True
        assert result.doca_status["doca_version"] == "2.9.2-0.1"
        assert result.doca_status["mst_present"] is True
        assert result.doca_status["bfb_install_present"] is True
        assert result.doca_status["openibd_loaded"] is True
        assert result.doca_status["mst_devices"] == ["/dev/mst/mt41692_pciconf0"]

    def test_doca_not_installed(self, mock_ssh):
        """No DOCA at all → ready=False, all flags false."""
        responses = [_make_result(exit_code=1)] * 17  # Skip earlier probes
        doca_responses = [
            _make_result("missing"),     # repo_configured
            _make_result("missing"),     # profile_installed
            _make_result(""),            # doca_version
            _make_result("missing"),     # mst_present
            _make_result("missing"),     # mlxconfig_present
            _make_result("missing"),     # rshim_present
            _make_result("missing"),     # bfb_install_present
            _make_result("inactive"),    # openibd_loaded
            _make_result(""),            # mst_devices
        ]
        mock_ssh.execute.side_effect = responses + doca_responses
        result = probe_host(mock_ssh)
        assert result.doca_status is not None
        assert result.doca_status["ready"] is False
        assert result.doca_status["repo_configured"] is False
        assert result.doca_status["profile_installed"] is False
        assert result.doca_status["doca_version"] is None
        assert result.doca_status["mst_devices"] == []

    def test_doca_partial_install(self, mock_ssh):
        """Repo configured but binaries missing → ready=False."""
        responses = [_make_result(exit_code=1)] * 17
        doca_responses = [
            _make_result("ok"),          # repo
            _make_result("missing"),     # profile not installed
            _make_result(""),            # no version
            _make_result("missing"),     # no mst
            _make_result("missing"),     # no mlxconfig
            _make_result("ok"),          # rshim present (from earlier rshim install)
            _make_result("missing"),     # no bfb-install
            _make_result("inactive"),    # openibd not loaded
            _make_result(""),            # no mst devices
        ]
        mock_ssh.execute.side_effect = responses + doca_responses
        result = probe_host(mock_ssh)
        assert result.doca_status is not None
        assert result.doca_status["ready"] is False
        assert result.doca_status["repo_configured"] is True
        assert result.doca_status["rshim_present"] is True

    def test_unreachable_host_has_no_doca_status(self, mock_ssh):
        """SSH unreachable → doca_status stays None."""
        mock_ssh.is_reachable.return_value = False
        result = probe_host(mock_ssh)
        assert result.doca_status is None
