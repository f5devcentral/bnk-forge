"""Unit tests for shared platform context normalization service."""

from types import SimpleNamespace

from services.platform_context_service import PlatformContextService


def _cluster(**overrides):
    defaults = {
        "cloud_provider": None,
        "context": None,
        "api_server": None,
        "detected_platform_profile": None,
        "detected_platform_provider": None,
        "platform_capabilities": None,
        "platform_constraints": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestPlatformDetection:
    def test_detects_eks_from_cloud_provider(self):
        context = PlatformContextService.derive_cluster_context(_cluster(cloud_provider="eks"))
        assert context.detected_platform_profile == "eks"
        assert context.detected_platform_provider == "aws"
        assert context.platform_capabilities["cloud_load_balancer"] is True
        assert context.platform_constraints["managed_control_plane"] is True

    def test_detects_roks_from_ibm_provider(self):
        context = PlatformContextService.derive_cluster_context(_cluster(cloud_provider="ibm"))
        assert context.detected_platform_profile == "roks"
        assert context.detected_platform_provider == "ibm"
        assert context.platform_capabilities["cloud_load_balancer"] is True
        assert context.platform_constraints["managed_control_plane"] is True

    def test_detects_generic_onprem_from_onprem_provider(self):
        context = PlatformContextService.derive_cluster_context(_cluster(cloud_provider="on-prem"))
        assert context.detected_platform_profile == "generic_onprem"
        assert context.detected_platform_provider == "baremetal"
        assert context.platform_constraints["cluster_lifecycle_external"] is None

    def test_detects_ocp_from_scan_distribution(self):
        scan_result = {
            "cluster_info": {"distribution": "OpenShift"},
            "crd_groups": {
                "route.openshift.io",
                "security.openshift.io",
                "machineconfiguration.openshift.io",
                "operators.coreos.com",
            },
            "prerequisites": {},
        }
        context = PlatformContextService.derive_cluster_context(_cluster(), scan_result)
        assert context.detected_platform_profile == "ocp"
        assert context.platform_capabilities["openshift_routes"] is True
        assert context.platform_capabilities["scc"] is True
        assert context.platform_constraints[
            "privileged_workloads_require_platform_security_binding"
        ] is True

    def test_detects_ocp_from_crd_groups_even_with_onprem_provider(self):
        scan_result = {
            "cluster_info": {"distribution": "generic"},
            "crd_groups": {"route.openshift.io"},
            "prerequisites": {},
        }
        context = PlatformContextService.derive_cluster_context(
            _cluster(cloud_provider="on-prem"),
            scan_result,
        )
        assert context.detected_platform_profile == "ocp"

    def test_ocp_capabilities_reflect_detected_groups(self):
        scan_result = {
            "cluster_info": {"distribution": "OpenShift"},
            "crd_groups": {"route.openshift.io", "config.openshift.io"},
            "prerequisites": {},
        }

        context = PlatformContextService.derive_cluster_context(_cluster(), scan_result)
        assert context.platform_capabilities["openshift_routes"] is True
        assert context.platform_capabilities["operator_framework"] is True
        assert context.platform_capabilities["scc"] is False
        assert context.platform_capabilities["machine_config"] is False

    def test_weak_context_only_does_not_force_ocp(self):
        context = PlatformContextService.derive_cluster_context(
            _cluster(cloud_provider="on-prem", context="team-openshift-lab"),
            {"cluster_info": {"distribution": "generic"}, "crd_groups": set(), "prerequisites": {}},
        )
        assert context.detected_platform_profile == "generic_onprem"

    def test_weak_api_server_only_does_not_force_ocp(self):
        context = PlatformContextService.derive_cluster_context(
            _cluster(cloud_provider="on-prem", api_server="https://api.openshift-like.example"),
            {"cluster_info": {"distribution": "generic"}, "crd_groups": set(), "prerequisites": {}},
        )
        assert context.detected_platform_profile == "generic_onprem"

    def test_unknown_when_signals_insufficient(self):
        context = PlatformContextService.derive_cluster_context(_cluster())
        assert context.detected_platform_profile == "unknown"
        assert context.detected_platform_provider is None

    def test_defaults_to_generic_onprem_when_kubeconfig_signals_exist(self):
        context = PlatformContextService.derive_cluster_context(
            _cluster(context="kind-dev", api_server="https://10.0.0.1:6443"),
        )
        assert context.detected_platform_profile == "generic_onprem"
        assert context.detected_platform_provider is None


class TestCapabilityNormalization:
    def test_scan_prerequisites_map_to_capabilities(self):
        scan_result = {
            "prerequisites": {
                "multus": {"nad_crd_installed": True},
                "sriov": {"nodes_with_vfs": 1},
                "hugepages": {"nodes_with_hugepages": 0},
                "gateway_api": {"status": "partial"},
            },
        }
        context = PlatformContextService.derive_cluster_context(
            _cluster(cloud_provider="on-prem"),
            scan_result,
        )

        assert context.platform_capabilities["network_attachment_definitions"] is True
        assert context.platform_capabilities["secondary_networks"] is True
        assert context.platform_capabilities["sriov"] is True
        assert context.platform_capabilities["hugepages"] is False
        assert context.platform_capabilities["gateway_api"] is True

    def test_serialize_cluster_context_uses_stored_when_valid(self):
        cluster = _cluster(
            detected_platform_profile="aks",
            detected_platform_provider="azure",
            platform_capabilities={"cloud_load_balancer": True},
            platform_constraints={"managed_control_plane": True},
        )

        context = PlatformContextService.serialize_cluster_context(cluster)
        assert context.detected_platform_profile == "aks"
        assert context.detected_platform_provider == "azure"
        assert context.platform_capabilities["cloud_load_balancer"] is True
        assert context.platform_constraints["managed_control_plane"] is True

    def test_serialize_cluster_context_falls_back_when_stored_invalid(self):
        cluster = _cluster(
            cloud_provider="gke",
            detected_platform_profile="invalid-value",
            platform_capabilities=None,
            platform_constraints=None,
        )

        context = PlatformContextService.serialize_cluster_context(cluster)
        assert context.detected_platform_profile == "gke"
        assert context.detected_platform_provider == "gcp"

        # Backfill serving path should also stage normalized values on the model.
        assert cluster.detected_platform_profile == "gke"
        assert cluster.detected_platform_provider == "gcp"

    def test_invalid_stored_provider_normalizes_to_none(self):
        cluster = _cluster(
            detected_platform_profile="eks",
            detected_platform_provider="not-valid",
            platform_capabilities={"cloud_load_balancer": True},
            platform_constraints={"managed_control_plane": True},
        )

        context = PlatformContextService.serialize_cluster_context(cluster)
        assert context.detected_platform_profile == "eks"
        assert context.detected_platform_provider is None

    def test_partial_stored_maps_default_missing_to_none(self):
        cluster = _cluster(
            detected_platform_profile="ocp",
            detected_platform_provider="vmware",
            platform_capabilities={"openshift_routes": True, "unexpected": True},
            platform_constraints={"operator_required_for_networking": True},
        )

        context = PlatformContextService.serialize_cluster_context(cluster)
        assert context.platform_capabilities["openshift_routes"] is True
        assert context.platform_capabilities["gateway_api"] is None
        assert context.platform_constraints["operator_required_for_networking"] is True
        assert context.platform_constraints["managed_control_plane"] is None
