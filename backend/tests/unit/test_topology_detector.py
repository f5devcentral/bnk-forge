"""Unit tests for topology_detector — pure function, no SSH."""

import pytest

from services.bare_metal.discovery.host_probe import HostProbeResult
from services.bare_metal.discovery.topology_detector import (
    TopologyRecommendation,
    _count_dpu_pci_domains,
    _has_independent_nic,
    detect_topology,
    is_dpu_mode,
    is_supernic_mode,
    nic_mode_display,
)


@pytest.mark.unit
class TestTopologyDetection:

    def test_regular_with_independent_nic_supernic_mode(self):
        """Host with independent NIC + SuperNIC mode → regular (host survives DPU reboot)."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="0",  # SuperNIC
            pf_interfaces=[
                {"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"},
            ],
            default_route_iface="ens4f1",  # Non-Mellanox → independent NIC
            rshim_present=True,
            dpu_pci_detected=True,
        )
        rec = detect_topology(host)
        assert rec.topology == "regular"
        assert rec.confidence == "high"

    def test_regular_with_independent_nic_dpu_mode(self):
        """Host with independent NIC + DPU mode → regular."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="1",  # DPU mode
            pf_interfaces=[
                {"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"},
            ],
            default_route_iface="ens4f1",
            rshim_present=True,
        )
        rec = detect_topology(host)
        assert rec.topology == "regular"
        assert rec.confidence == "high"

    def test_bmc_with_redfish(self):
        """SuperNIC mode + no independent NIC + Redfish available → bmc."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="0",  # SuperNIC
            pf_interfaces=[{"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"}],
            default_route_iface="enp3s0f0",  # Through Mellanox = no independent NIC
        )

        class FakeBmc:
            redfish_reachable = True
            ipmi_reachable = True

        rec = detect_topology(host, bmc_result=FakeBmc())
        assert rec.topology == "bmc"
        assert rec.confidence == "high"

    def test_bf3_ipmi_no_redfish(self):
        """SuperNIC mode + IPMI only (no Redfish) → bf3_ipmi."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="0",  # SuperNIC
            pf_interfaces=[{"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"}],
            default_route_iface="enp3s0f0",
        )

        class FakeBmc:
            redfish_reachable = False
            ipmi_reachable = True

        rec = detect_topology(host, bmc_result=FakeBmc())
        assert rec.topology == "bf3_ipmi"

    def test_bf3_no_bmc(self):
        """SuperNIC mode + no BMC access → bf3 (mode change via BFB)."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="0",  # SuperNIC
            pf_interfaces=[{"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"}],
            default_route_iface="enp3s0f0",
        )
        rec = detect_topology(host, bmc_result=None)
        assert rec.topology == "bf3"
        assert rec.confidence == "medium"

    def test_bf3_from_vfs_no_independent(self):
        """VFs present but no independent NIC → bf3."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode=None,  # Unknown
            pf_interfaces=[{"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"}],
            vf_count=4,
        )
        rec = detect_topology(host)
        assert rec.topology == "bf3"

    def test_default_fallback(self):
        """No conclusive evidence → regular (safest default)."""
        host = HostProbeResult(ssh_reachable=True)
        rec = detect_topology(host)
        assert rec.topology == "regular"
        assert rec.confidence == "low"

    def test_independent_nic_overrides_supernic_for_topology(self):
        """Key fix: independent NIC + SuperNIC mode should NOT route to bf3/bmc."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="0",  # SuperNIC
            pf_interfaces=[
                {"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"},
                {"name": "ens4f0", "link_state": "up", "driver": "ice"},  # Intel NIC
            ],
            rshim_present=True,
        )
        rec = detect_topology(host)
        # Should be "regular" because independent NIC means host survives DPU reboot
        assert rec.topology == "regular"
        assert rec.topology != "bf3"
        assert rec.topology != "bmc"


@pytest.mark.unit
class TestNicModeHelpers:

    def test_is_supernic_normalized_zero(self):
        assert is_supernic_mode("0") is True

    def test_is_supernic_legacy_separated_host(self):
        assert is_supernic_mode("SEPARATED_HOST(0)") is True

    def test_is_supernic_dpu_mode_returns_false(self):
        assert is_supernic_mode("1") is False
        assert is_supernic_mode("EMBEDDED_CPU(1)") is False

    def test_is_supernic_none(self):
        assert is_supernic_mode(None) is False

    def test_is_dpu_mode_normalized_one(self):
        assert is_dpu_mode("1") is True

    def test_is_dpu_mode_legacy_embedded_cpu(self):
        assert is_dpu_mode("EMBEDDED_CPU(1)") is True

    def test_is_dpu_mode_supernic_returns_false(self):
        assert is_dpu_mode("0") is False
        assert is_dpu_mode("SEPARATED_HOST(0)") is False

    def test_is_dpu_mode_none(self):
        assert is_dpu_mode(None) is False

    def test_nic_mode_display_zero(self):
        assert "SuperNIC" in nic_mode_display("0")
        assert "INTERNAL_CPU_MODEL=0" in nic_mode_display("0")

    def test_nic_mode_display_one(self):
        assert "DPU" in nic_mode_display("1")
        assert "INTERNAL_CPU_MODEL=1" in nic_mode_display("1")

    def test_nic_mode_display_none(self):
        assert nic_mode_display(None) == "Unknown"


@pytest.mark.unit
class TestHasIndependentNic:

    def test_default_route_through_non_mellanox(self):
        """Default route through non-Mellanox interface → independent NIC."""
        host = HostProbeResult(
            pf_interfaces=[
                {"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"},
            ],
            default_route_iface="ens4f1",
        )
        rec = detect_topology(HostProbeResult(ssh_reachable=True, nic_mode="0",
                                              pf_interfaces=host.pf_interfaces,
                                              default_route_iface=host.default_route_iface))
        assert rec.topology == "regular"

    def test_default_route_through_mellanox_single_pf(self):
        """Default route through Mellanox PF + single PF → NOT independent."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="0",
            pf_interfaces=[
                {"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"},
            ],
            default_route_iface="enp3s0f0",
        )
        rec = detect_topology(host)
        # Not regular because no independent NIC — falls to SuperNIC branch
        assert rec.topology != "regular"

    def test_mixed_driver_pfs_indicates_independent(self):
        """PFs with different drivers (mlx5 + ice) → independent NIC detected."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="0",
            pf_interfaces=[
                {"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"},
                {"name": "ens4f0", "link_state": "up", "driver": "ice"},
            ],
        )
        rec = detect_topology(host)
        assert rec.topology == "regular"

    def test_dual_port_bf3_same_driver_not_independent(self):
        """Two Mellanox PFs (dual-port BF3) with same driver → NOT independent."""
        host = HostProbeResult(
            ssh_reachable=True,
            nic_mode="0",
            pf_interfaces=[
                {"name": "enp3s0f0", "link_state": "up", "driver": "mlx5_core"},
                {"name": "enp3s0f1", "link_state": "up", "driver": "mlx5_core"},
            ],
        )
        rec = detect_topology(host)
        # Both PFs are mlx5 → not independent → falls to SuperNIC branch
        assert rec.topology != "regular"


@pytest.mark.unit
class TestNicModeDetection:
    def test_dpu_mode_canonical(self):
        assert is_dpu_mode("1") is True

    def test_dpu_mode_legacy(self):
        assert is_dpu_mode("EMBEDDED_CPU(1)") is True

    def test_supernic_mode_canonical(self):
        assert is_supernic_mode("0") is True

    def test_supernic_mode_legacy(self):
        assert is_supernic_mode("SEPARATED_HOST(0)") is True

    def test_none_mode(self):
        assert is_dpu_mode(None) is False
        assert is_supernic_mode(None) is False


@pytest.mark.unit
class TestCountDpuPciDomains:
    def test_single_dpu_two_pfs(self):
        result = HostProbeResult(pf_interfaces=[
            {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
            {"name": "enp3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
        ])
        assert _count_dpu_pci_domains(result) == 1

    def test_dual_dpu_four_pfs(self):
        """MGX-style: two DPUs on different PCI domains."""
        result = HostProbeResult(pf_interfaces=[
            {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
            {"name": "enp3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
            {"name": "enP2p3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
            {"name": "enP2p3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
        ])
        assert _count_dpu_pci_domains(result) == 2

    def test_no_pfs(self):
        result = HostProbeResult(pf_interfaces=[])
        assert _count_dpu_pci_domains(result) == 0

    def test_mixed_drivers_not_counted(self):
        """Non-mlx5 interfaces are ignored."""
        result = HostProbeResult(pf_interfaces=[
            {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
            {"name": "enp3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
            {"name": "eno1", "driver": "ice", "link_state": "up"},
        ])
        assert _count_dpu_pci_domains(result) == 1


@pytest.mark.unit
class TestDetectTopologyDualDpu:
    def test_regular_independent_nic(self):
        """Host with independent NIC → regular."""
        result = HostProbeResult(
            pf_interfaces=[
                {"name": "eno1", "driver": "ice", "link_state": "up"},
                {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
            ],
            default_route_iface="eno1",
            rshim_present=True,
            dpu_pci_detected=True,
            nic_mode="1",
        )
        rec = detect_topology(result)
        assert rec.topology == "regular"
        assert rec.confidence == "high"

    def test_dual_dpu_obmc(self):
        """Two DPU PCI domains, no rshim, no independent NIC → dual_dpu_obmc."""
        result = HostProbeResult(
            pf_interfaces=[
                {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enp3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enP2p3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enP2p3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
            ],
            default_route_iface="enP2p3s0f0np0",
            rshim_present=False,
            dpu_pci_detected=True,
            nic_mode="1",
        )
        rec = detect_topology(result)
        assert rec.topology == "dual_dpu_obmc"
        assert rec.confidence == "high"

    def test_dual_dpu_with_rshim_not_obmc(self):
        """Two DPUs but rshim works on host → NOT dual_dpu_obmc (falls through to regular/other)."""
        result = HostProbeResult(
            pf_interfaces=[
                {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enp3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enP2p3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enP2p3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
            ],
            default_route_iface="enP2p3s0f0np0",
            rshim_present=True,  # rshim works
            dpu_pci_detected=True,
            nic_mode="1",
        )
        rec = detect_topology(result)
        # With rshim present, it should NOT be dual_dpu_obmc
        assert rec.topology != "dual_dpu_obmc"

    def test_supernic_mode_redfish_bmc(self):
        """SuperNIC mode + Redfish → bmc topology."""
        from unittest.mock import MagicMock
        result = HostProbeResult(
            pf_interfaces=[
                {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
            ],
            default_route_iface="enp3s0f0np0",
            rshim_present=False,
            dpu_pci_detected=True,
            nic_mode="0",
        )
        bmc = MagicMock()
        bmc.redfish_reachable = True
        bmc.ipmi_reachable = False
        rec = detect_topology(result, bmc_result=bmc)
        assert rec.topology == "bmc"
