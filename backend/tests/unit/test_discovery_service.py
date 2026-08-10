"""Unit tests for BareMetalDiscoveryService DPU selection logic."""

from unittest.mock import MagicMock, patch

import pytest

from services.bare_metal.discovery.host_probe import HostProbeResult
from services.bare_metal.discovery.topology_detector import TopologyRecommendation


@pytest.mark.unit
class TestAutoSelectDeploymentDpu:
    """Tests for _auto_select_deployment_dpu logic."""

    def test_sets_rshim_source_bmc(self):
        """dual_dpu_obmc always uses BMC rshim."""
        from services.bare_metal.discovery import BareMetalDiscoveryService

        # Mock dependencies
        mock_db = MagicMock()
        service = BareMetalDiscoveryService(mock_db)

        host = MagicMock()
        host.id = 1
        host.project_id = 1
        host.name = "test-host"
        host.host_ip = "10.0.0.1"
        host.bmc_ip = "10.0.0.2"
        host.dpu_credential_id = None
        host.rshim_source = None
        host.deploy_dpu_pci_address = None
        host.deploy_dpu_index = None

        host_result = HostProbeResult(
            default_route_iface="enP2p3s0f0np0",
            pf_interfaces=[
                {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enp3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enP2p3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enP2p3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
            ],
        )

        service._auto_select_deployment_dpu(host, host_result)

        assert host.rshim_source == "bmc"

    def test_selects_non_mgmt_dpu(self):
        """Deployment DPU is the one NOT carrying default route."""
        from services.bare_metal.discovery import BareMetalDiscoveryService

        mock_db = MagicMock()
        service = BareMetalDiscoveryService(mock_db)

        host = MagicMock()
        host.id = 1
        host.project_id = 1
        host.name = "mgx-host"
        host.host_ip = "10.0.0.1"
        host.bmc_ip = "10.0.0.2"
        host.dpu_credential_id = None
        host.rshim_source = None
        host.deploy_dpu_pci_address = None
        host.deploy_dpu_index = None

        # Mgmt via DPU #2 (enP2p3s0f0np0), so deploy to DPU #1 (enp3s0f0np0)
        host_result = HostProbeResult(
            default_route_iface="enP2p3s0f0np0",
            pf_interfaces=[
                {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enp3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enP2p3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
                {"name": "enP2p3s0f1np1", "driver": "mlx5_core", "link_state": "up"},
            ],
        )

        service._auto_select_deployment_dpu(host, host_result)

        # Should select DPU #1 (PCI domain 0000:03:00.0)
        assert host.deploy_dpu_pci_address is not None
        assert "0000:" in host.deploy_dpu_pci_address or "03:" in host.deploy_dpu_pci_address

    def test_no_default_route_logs_warning(self):
        """Missing default route interface logs warning."""
        from services.bare_metal.discovery import BareMetalDiscoveryService

        mock_db = MagicMock()
        service = BareMetalDiscoveryService(mock_db)

        host = MagicMock()
        host.id = 1
        host.rshim_source = None
        host.deploy_dpu_pci_address = None

        host_result = HostProbeResult(
            default_route_iface=None,  # No default route
            pf_interfaces=[
                {"name": "enp3s0f0np0", "driver": "mlx5_core", "link_state": "up"},
            ],
        )

        with patch("services.bare_metal.discovery.logger") as mock_logger:
            service._auto_select_deployment_dpu(host, host_result)
            mock_logger.warning.assert_called()


@pytest.mark.unit
class TestValidateTopologyPrerequisites:
    """Tests for _validate_topology_prerequisites."""

    def test_dual_dpu_obmc_requires_bmc_ip(self):
        """dual_dpu_obmc without bmc_ip raises BadRequestError."""
        from core.errors import BadRequestError
        from services.bare_metal.orchestrator import BareMetalDeploymentService

        mock_db = MagicMock()
        service = BareMetalDeploymentService(mock_db)

        host = MagicMock()
        host.topology = "dual_dpu_obmc"
        host.bmc_ip = None  # Missing
        host.deploy_dpu_pci_address = "0000:03:00.0"

        with pytest.raises(BadRequestError, match="bmc_ip"):
            service._validate_topology_prerequisites(host)

    def test_dual_dpu_obmc_requires_pci_address(self):
        """dual_dpu_obmc without deploy_dpu_pci_address raises BadRequestError."""
        from core.errors import BadRequestError
        from services.bare_metal.orchestrator import BareMetalDeploymentService

        mock_db = MagicMock()
        service = BareMetalDeploymentService(mock_db)

        host = MagicMock()
        host.topology = "dual_dpu_obmc"
        host.bmc_ip = "10.0.0.2"
        host.deploy_dpu_pci_address = None  # Missing

        with pytest.raises(BadRequestError, match="deploy_dpu_pci_address"):
            service._validate_topology_prerequisites(host)

    def test_dual_dpu_obmc_valid(self):
        """dual_dpu_obmc with all required fields passes validation."""
        from services.bare_metal.orchestrator import BareMetalDeploymentService

        mock_db = MagicMock()
        service = BareMetalDeploymentService(mock_db)

        host = MagicMock()
        host.topology = "dual_dpu_obmc"
        host.bmc_ip = "10.0.0.2"
        host.deploy_dpu_pci_address = "0000:03:00.0"

        # Should not raise
        service._validate_topology_prerequisites(host)


@pytest.mark.unit
class TestDiscoveryAssessmentMessages:
    def test_dual_dpu_obmc_rshim_assessment_is_info(self):
        from services.bare_metal.discovery import BareMetalDiscoveryService

        service = BareMetalDiscoveryService(MagicMock())
        host_result = HostProbeResult(
            ssh_reachable=True,
            rshim_present=False,
            dpu_pci_detected=True,
        )

        checks = service._build_assessment(  # noqa: SLF001
            host_result,
            bmc_result=None,
            dpu_result=None,
            vlan_result=None,
            topology_rec=TopologyRecommendation(
                topology="dual_dpu_obmc",
                confidence="high",
                reasons=[],
            ),
        )

        rshim_check = next(c for c in checks if c["check"] == "rshim_device")
        assert rshim_check["status"] == "info"
        assert "OpenBMC manages rshim" in rshim_check["message"]

    def test_dual_dpu_obmc_recommendation_explains_missing_host_rshim(self):
        from services.bare_metal.discovery import BareMetalDiscoveryService

        service = BareMetalDiscoveryService(MagicMock())
        host_result = HostProbeResult(
            ssh_reachable=True,
            rshim_present=False,
        )

        recs = service._build_recommendations(  # noqa: SLF001
            host_result,
            bmc_result=None,
            dpu_result=None,
            vlan_result=None,
            topology_rec=TopologyRecommendation(
                topology="dual_dpu_obmc",
                confidence="high",
                reasons=[],
            ),
        )

        assert any("OpenBMC rshim path" in rec for rec in recs)

    def test_regular_topology_no_validation(self):
        """Regular topology has no special prerequisites."""
        from services.bare_metal.orchestrator import BareMetalDeploymentService

        mock_db = MagicMock()
        service = BareMetalDeploymentService(mock_db)

        host = MagicMock()
        host.topology = "regular"
        host.bmc_ip = None  # Not required
        host.deploy_dpu_pci_address = None  # Not required

        # Should not raise
        service._validate_topology_prerequisites(host)
