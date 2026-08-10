"""
Unit tests for the Adaptive Module Selector.

Self-contained — no DB, no Celery, no cluster connection.
Uses synthetic scan data to verify module selection logic.

Tests cover:
  1. Fresh cluster (nothing installed) → all modules deploy
  2. Fully installed BNK → all modules skip
  3. Partial installation → mix of skip/deploy/investigate
  4. Pre-filled variables from SR-IOV scan data
  5. Global blockers (no nodes, missing Multus)
  6. Demo apps stack needs BNK installed first
  7. Template validation (unknown template raises)
  8. Plan serialization
  9. Suggested variable extraction
  10. Edge cases: partial cert-manager, FLO helm release without pods
"""

import os
import sys
from typing import Any

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from services.adaptive_module_selector import (
    TEMPLATE_MODULES,
    AdaptiveModuleSelector,
    DeploymentPlan,
    ModuleAction,
)

# ---------------------------------------------------------------------------
# Scan data fixtures
# ---------------------------------------------------------------------------

def _base_scan(overrides: dict[str, Any] = None) -> dict[str, Any]:
    """Generate a base scan result that can be overridden per test."""
    base = {
        "cluster_id": 1,
        "cluster_name": "test-cluster",
        "cluster_info": {
            "version": "v1.28.5-eks",
            "distribution": "EKS",
            "cloud_provider": "aws",
            "region": "ap-southeast-2",
            "node_count": 3,
            "nodes_ready": 3,
            "hp_nodes": 1,
            "hp_node_details": [
                {"name": "ip-10-0-20-98", "instance_type": "c5n.4xlarge", "zone": "ap-southeast-2a", "ready": True}
            ],
            "namespaces": 25,
        },
        "prerequisites": {
            "cert_manager": {
                "status": "missing",
                "version": None,
                "crds_installed": False,
                "crd_count": 0,
                "pods": {"controller": 0, "webhook": 0, "cainjector": 0, "total_running": 0},
                "helm_release": None,
            },
            "multus": {
                "status": "detected",
                "nad_crd_installed": True,
                "daemonset": {"name": "kube-multus-ds", "namespace": "kube-system", "desired": 3, "ready": 3},
                "running_pods": 3,
            },
            "sriov": {
                "status": "detected",
                "device_plugin": {"name": "sriov-device-plugin", "namespace": "kube-system", "desired": 1, "ready": 1},
                "nodes_with_vfs": 1,
                "total_vfs": 8,
                "node_details": [
                    {
                        "name": "ip-10-0-20-98",
                        "resources": {
                            "intel.com/external_netdevice": "4",
                            "intel.com/internal_netdevice": "4",
                        },
                        "vf_count": 8,
                        "instance_type": "c5n.4xlarge",
                    }
                ],
            },
            "hugepages": {
                "status": "detected",
                "nodes_with_hugepages": 1,
                "node_details": [
                    {"name": "ip-10-0-20-98", "hugepages_2mi": "4Gi", "hugepages_1gi": "0", "is_hp_node": True}
                ],
            },
            "storage": {
                "status": "detected",
                "count": 2,
                "default": "gp3",
                "has_gp3": True,
                "has_gp2": True,
                "classes": [
                    {"name": "gp3", "provisioner": "ebs.csi.aws.com", "is_default": True},
                    {"name": "gp2", "provisioner": "kubernetes.io/aws-ebs", "is_default": False},
                ],
            },
            "gateway_api": {
                "status": "missing",
                "crds_installed": 0,
                "standard_crds_found": [],
                "standard_crds_missing": ["gatewayclasses.gateway.networking.k8s.io", "gateways.gateway.networking.k8s.io", "httproutes.gateway.networking.k8s.io"],
                "api_versions": [],
                "gatewayclasses": 0,
                "gateways": 0,
            },
        },
        "bnk_install": {
            "status": "not_installed",
            "health": None,
            "namespaces": {"f5_operator": False, "f5_utils": False},
            "crds": {"total": 0, "groups": [], "has_data_plane": False, "has_flo": False, "has_gateway_ext": False},
            "flo": {"version": None, "pods": 0, "running": 0, "helm_release": None},
            "tmm": {"pods": 0, "running": 0, "containers": None},
            "controller": {"pods": 0, "running": 0},
            "analyzer": {"pods": 0, "running": 0},
            "crd_installer": {"completed": False, "pods": 0},
            "cne_instance": None,
            "vlans": [],
        },
        "scan_metadata": {"scanned_at": "2026-02-15T14:00:00Z", "duration_ms": 1200, "api_calls": 16},
    }

    if overrides:
        _deep_merge(base, overrides)

    return base


def _deep_merge(base: dict, overrides: dict):
    """Recursively merge overrides into base."""
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _fully_installed_scan() -> dict[str, Any]:
    """Scan data for a cluster with BNK fully installed and healthy."""
    return _base_scan({
        "prerequisites": {
            "cert_manager": {
                "status": "detected",
                "version": "1.16.1",
                "crds_installed": True,
                "crd_count": 6,
                "pods": {"controller": 1, "webhook": 1, "cainjector": 1, "total_running": 3},
                "helm_release": {"name": "cert-manager", "namespace": "cert-manager", "version": "3", "status": "deployed"},
            },
            "gateway_api": {
                "status": "detected",
                "crds_installed": 12,
                "standard_crds_found": ["gatewayclasses.gateway.networking.k8s.io", "gateways.gateway.networking.k8s.io", "httproutes.gateway.networking.k8s.io"],
                "standard_crds_missing": [],
                "api_versions": ["v1", "v1alpha1", "v1beta1"],
                "gatewayclasses": 1,
                "gateways": 1,
            },
        },
        "bnk_install": {
            "status": "installed",
            "health": "healthy",
            "namespaces": {"f5_operator": True, "f5_utils": True},
            "crds": {"total": 28, "groups": ["k8s.f5.com", "k8s.f5net.com", "gateway.k8s.f5net.com", "fic.f5.com"], "has_data_plane": True, "has_flo": True, "has_gateway_ext": True},
            "flo": {"version": "2.2.0", "pods": 1, "running": 1, "helm_release": {"name": "f5-lifecycle-operator", "namespace": "f5-operator", "version": "1", "status": "deployed"}},
            "tmm": {"pods": 1, "running": 1, "containers": {"total_containers": 7, "ready_containers": 7, "containers": "7/7"}},
            "controller": {"pods": 1, "running": 1},
            "analyzer": {"pods": 1, "running": 1},
            "crd_installer": {"completed": True, "pods": 1},
            "cne_instance": {"name": "bnk-instance", "features": {"firewallACL": True, "intelligentLB": True, "pseudoCNI": True, "metricSubsystem": False, "loggingSubsystem": False}},
            "vlans": [
                {"name": "external", "interfaces": ["1.1"], "self_ips": ["10.0.10.240"], "mtu": 9000, "programmed": True},
                {"name": "internal", "interfaces": ["1.2"], "self_ips": ["10.0.20.240"], "mtu": 9000, "programmed": True},
            ],
        },
    })


# ---------------------------------------------------------------------------
# Tests: Template Validation
# ---------------------------------------------------------------------------

class TestTemplateValidation:
    """Test template lookup and validation."""

    def test_known_templates_exist(self):
        assert "f5-bnk-2.2" in TEMPLATE_MODULES
        assert "eks-bnk-smartllm-demo" in TEMPLATE_MODULES

    def test_bnk_template_has_7_modules(self):
        assert len(TEMPLATE_MODULES["f5-bnk-2.2"]) == 7

    def test_smartllm_demo_template_has_3_modules(self):
        assert len(TEMPLATE_MODULES["eks-bnk-smartllm-demo"]) == 3

    def test_unknown_template_raises(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        with pytest.raises(ValueError, match="Unknown template"):
            selector.plan_for_template("nonexistent-template")

    def test_all_template_modules_have_path(self):
        for slug, modules in TEMPLATE_MODULES.items():
            for mod in modules:
                assert "path" in mod, f"Module in {slug} missing 'path'"
                assert "name" in mod, f"Module in {slug} missing 'name'"


# ---------------------------------------------------------------------------
# Tests: Fresh Cluster (Nothing Installed)
# ---------------------------------------------------------------------------

class TestFreshCluster:
    """Test plan when cluster has prerequisites but no BNK."""

    @pytest.fixture
    def plan(self):
        scan = _base_scan()  # Fresh cluster, no BNK
        selector = AdaptiveModuleSelector(scan)
        return selector.plan_for_template("f5-bnk-2.2")

    def test_plan_type(self, plan):
        assert isinstance(plan, DeploymentPlan)

    def test_plan_has_7_modules(self, plan):
        assert len(plan.modules) == 7

    def test_all_bnk_modules_deploy(self, plan):
        """On a fresh cluster, all BNK modules should be action=deploy."""
        deploy_paths = [m.path for m in plan.modules if m.action == "deploy"]
        assert "k8s/bnk-prerequisites" in deploy_paths
        assert "k8s/network-setup" in deploy_paths
        assert "k8s/cert-manager" in deploy_paths
        assert "bnk/flo" in deploy_paths
        assert "bnk/cneinstance" in deploy_paths
        assert "bnk/bnk-vlans" in deploy_paths
        assert "bnk/bnk-gatewayclass" in deploy_paths

    def test_deploy_count(self, plan):
        assert plan.deploy_count == 7
        assert plan.skip_count == 0

    def test_is_ready(self, plan):
        assert plan.is_ready is True

    def test_no_global_blockers(self, plan):
        assert len(plan.global_blockers) == 0

    def test_sriov_warnings_not_present(self, plan):
        """SR-IOV is detected in base scan, so no SR-IOV warnings."""
        for m in plan.modules:
            for w in m.warnings:
                assert "SR-IOV" not in w or "not detected" not in w

    def test_network_setup_has_sriov_overrides(self, plan):
        """SR-IOV resource names should be pre-filled from scan data."""
        net = next(m for m in plan.modules if m.path == "k8s/network-setup")
        assert net.variable_overrides.get("external_resource_name") == "intel.com/external_netdevice"
        assert net.variable_overrides.get("internal_resource_name") == "intel.com/internal_netdevice"

    def test_network_setup_cni_type(self, plan):
        """EKS cluster should suggest host-device CNI."""
        net = next(m for m in plan.modules if m.path == "k8s/network-setup")
        assert net.variable_overrides.get("cni_type") == "host-device"

    def test_cert_manager_deploys(self, plan):
        """cert-manager is missing in base scan, so should deploy."""
        cm = next(m for m in plan.modules if m.path == "k8s/cert-manager")
        assert cm.action == "deploy"
        assert cm.confidence == "high"


# ---------------------------------------------------------------------------
# Tests: Fully Installed Cluster
# ---------------------------------------------------------------------------

class TestFullyInstalledCluster:
    """Test plan when cluster has BNK fully installed."""

    @pytest.fixture
    def plan(self):
        scan = _fully_installed_scan()
        selector = AdaptiveModuleSelector(scan)
        return selector.plan_for_template("f5-bnk-2.2")

    def test_all_modules_skip(self, plan):
        """All BNK modules should be skipped when already installed."""
        for m in plan.modules:
            assert m.action == "skip", f"{m.path} should be skip, got {m.action}: {m.reason}"

    def test_skip_count(self, plan):
        assert plan.skip_count == 7
        assert plan.deploy_count == 0

    def test_is_ready(self, plan):
        """Still ready (no blockers), just nothing to deploy."""
        assert plan.is_ready is True

    def test_cert_manager_shows_version(self, plan):
        cm = next(m for m in plan.modules if m.path == "k8s/cert-manager")
        assert "1.16.1" in cm.reason

    def test_flo_shows_version(self, plan):
        flo = next(m for m in plan.modules if m.path == "bnk/flo")
        assert "2.2.0" in flo.reason

    def test_cneinstance_captures_name(self, plan):
        cne = next(m for m in plan.modules if m.path == "bnk/cneinstance")
        assert cne.variable_overrides.get("instance_name") == "bnk-instance"

    def test_vlans_shows_programmed(self, plan):
        vlans = next(m for m in plan.modules if m.path == "bnk/bnk-vlans")
        assert "2 VLAN(s)" in vlans.reason
        assert "Programmed=True" in vlans.reason

    def test_gatewayclass_skip(self, plan):
        gwc = next(m for m in plan.modules if m.path == "bnk/bnk-gatewayclass")
        assert gwc.action == "skip"
        assert "GatewayClass" in gwc.reason


# ---------------------------------------------------------------------------
# Tests: Partial Installation
# ---------------------------------------------------------------------------

class TestPartialInstallation:
    """Test plan when cluster has some components but not all."""

    @pytest.fixture
    def scan(self):
        """Cluster with cert-manager and FLO running, but no TMM/CNEInstance."""
        return _base_scan({
            "prerequisites": {
                "cert_manager": {
                    "status": "detected",
                    "version": "1.16.1",
                    "crds_installed": True,
                    "crd_count": 6,
                    "pods": {"controller": 1, "webhook": 1, "cainjector": 1, "total_running": 3},
                    "helm_release": {"name": "cert-manager", "namespace": "cert-manager", "version": "1", "status": "deployed"},
                },
            },
            "bnk_install": {
                "status": "partial",
                "health": "degraded",
                "namespaces": {"f5_operator": True, "f5_utils": True},
                "crds": {"total": 5, "groups": ["k8s.f5.com"], "has_data_plane": False, "has_flo": True, "has_gateway_ext": False},
                "flo": {"version": "2.2.0", "pods": 1, "running": 1, "helm_release": {"name": "flo", "namespace": "f5-operator", "version": "1", "status": "deployed"}},
                "tmm": {"pods": 0, "running": 0, "containers": None},
                "controller": {"pods": 0, "running": 0},
                "analyzer": {"pods": 0, "running": 0},
                "crd_installer": {"completed": False, "pods": 0},
                "cne_instance": None,
                "vlans": [],
            },
        })

    @pytest.fixture
    def plan(self, scan):
        selector = AdaptiveModuleSelector(scan)
        return selector.plan_for_template("f5-bnk-2.2")

    def test_cert_manager_skipped(self, plan):
        cm = next(m for m in plan.modules if m.path == "k8s/cert-manager")
        assert cm.action == "skip"

    def test_flo_skipped(self, plan):
        flo = next(m for m in plan.modules if m.path == "bnk/flo")
        # FLO is running even though BNK is partial
        assert flo.action == "skip"

    def test_cneinstance_deploys(self, plan):
        cne = next(m for m in plan.modules if m.path == "bnk/cneinstance")
        assert cne.action == "deploy"

    def test_vlans_deploy(self, plan):
        vlans = next(m for m in plan.modules if m.path == "bnk/bnk-vlans")
        assert vlans.action == "deploy"

    def test_prerequisites_investigate(self, plan):
        prereqs = next(m for m in plan.modules if m.path == "k8s/bnk-prerequisites")
        assert prereqs.action == "investigate"  # Namespaces exist but BNK partial

    def test_mixed_actions(self, plan):
        """Should have a mix of actions."""
        actions = {m.action for m in plan.modules}
        assert len(actions) > 1  # Not all the same


# ---------------------------------------------------------------------------
# Tests: Missing Prerequisites
# ---------------------------------------------------------------------------

class TestMissingPrerequisites:
    """Test plan when cluster is missing Multus/SR-IOV."""

    @pytest.fixture
    def plan_no_multus(self):
        scan = _base_scan({
            "prerequisites": {
                "multus": {"status": "missing", "nad_crd_installed": False, "daemonset": None, "running_pods": 0},
            }
        })
        selector = AdaptiveModuleSelector(scan)
        return selector.plan_for_template("f5-bnk-2.2")

    def test_multus_warning(self, plan_no_multus):
        """Missing Multus should produce a global warning."""
        warnings = plan_no_multus.global_warnings
        assert any("Multus" in w for w in warnings)

    def test_network_setup_blocked(self, plan_no_multus):
        net = next(m for m in plan_no_multus.modules if m.path == "k8s/network-setup")
        assert net.action == "blocked"
        assert len(net.blockers) > 0

    @pytest.fixture
    def plan_no_sriov(self):
        scan = _base_scan({
            "prerequisites": {
                "sriov": {"status": "missing", "device_plugin": None, "nodes_with_vfs": 0, "total_vfs": 0, "node_details": []},
            }
        })
        selector = AdaptiveModuleSelector(scan)
        return selector.plan_for_template("f5-bnk-2.2")

    def test_sriov_warning(self, plan_no_sriov):
        warnings = plan_no_sriov.global_warnings
        assert any("SR-IOV" in w for w in warnings)

    def test_network_setup_no_sriov_overrides(self, plan_no_sriov):
        """Without SR-IOV, no resource name overrides."""
        net = next(m for m in plan_no_sriov.modules if m.path == "k8s/network-setup")
        assert "external_resource_name" not in net.variable_overrides

    @pytest.fixture
    def plan_no_hp_nodes(self):
        scan = _base_scan({
            "cluster_info": {
                "hp_nodes": 0,
                "hp_node_details": [],
            }
        })
        selector = AdaptiveModuleSelector(scan)
        return selector.plan_for_template("f5-bnk-2.2")

    def test_no_hp_nodes_warning(self, plan_no_hp_nodes):
        warnings = plan_no_hp_nodes.global_warnings
        assert any("high-performance nodes" in w for w in warnings)


# ---------------------------------------------------------------------------
# Tests: No Nodes (Global Blocker)
# ---------------------------------------------------------------------------

class TestNoNodes:
    """Test plan when cluster has no ready nodes."""

    @pytest.fixture
    def plan(self):
        scan = _base_scan({
            "cluster_info": {"nodes_ready": 0, "node_count": 3, "hp_nodes": 0, "hp_node_details": []},
        })
        selector = AdaptiveModuleSelector(scan)
        return selector.plan_for_template("f5-bnk-2.2")

    def test_global_blocker(self, plan):
        assert len(plan.global_blockers) > 0
        assert any("No ready nodes" in b for b in plan.global_blockers)

    def test_not_ready(self, plan):
        # plan.is_ready should be False because of blocked modules
        # (network-setup is blocked due to no multus when we have no nodes)
        # Actually is_ready is based on global_blockers
        assert plan.is_ready is False


# ---------------------------------------------------------------------------
# Tests: Demo Apps Stack
# ---------------------------------------------------------------------------

class TestDemoAppsStack:
    """Test plan for demo apps stack."""

    def test_demo_apps_blocked_without_bnk(self):
        """Demo apps stack needs BNK installed first."""
        scan = _base_scan()  # BNK not installed
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("eks-bnk-smartllm-demo")

        assert len(plan.global_blockers) > 0
        assert any("BNK is not installed" in b for b in plan.global_blockers)
        assert plan.is_ready is False

    def test_demo_apps_ready_with_bnk(self):
        """Demo apps stack should be ready when BNK is installed."""
        scan = _fully_installed_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("eks-bnk-smartllm-demo")

        assert plan.is_ready is True
        assert plan.deploy_count > 0

    def test_demo_apps_module_count(self):
        scan = _fully_installed_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("eks-bnk-smartllm-demo")
        assert len(plan.modules) == 3


# ---------------------------------------------------------------------------
# Tests: Suggested Variables
# ---------------------------------------------------------------------------

class TestSuggestedVariables:
    """Test variable suggestion extraction from scan data."""

    def test_eks_cloud_provider(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert plan.suggested_variables.get("cloud_provider") == "aws"

    def test_region_detected(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert plan.suggested_variables.get("region") == "ap-southeast-2"

    def test_storage_class_detected(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert plan.suggested_variables.get("storage_class_name") == "gp3"

    def test_sriov_resources_detected(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        sriov = plan.suggested_variables.get("detected_sriov_resources", [])
        assert "intel.com/external_netdevice" in sriov
        assert "intel.com/internal_netdevice" in sriov

    def test_installed_cluster_captures_vlan_ips(self):
        scan = _fully_installed_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert plan.suggested_variables.get("external_self_ips") == ["10.0.10.240"]
        assert plan.suggested_variables.get("internal_self_ips") == ["10.0.20.240"]

    def test_installed_cluster_captures_instance_name(self):
        scan = _fully_installed_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert plan.suggested_variables.get("instance_name") == "bnk-instance"

    def test_hp_node_mtu(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert plan.suggested_variables.get("tmm_default_mtu") == "9000"


# ---------------------------------------------------------------------------
# Tests: Lab sizing profile (issue #387 part C)
# ---------------------------------------------------------------------------

class TestLabSizingProfile:
    """Test the opt-in `sizing_profile="lab"` overrides on plan_for_template."""

    def test_no_profile_no_lab_overrides(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert "f5-tmm" not in plan.suggested_variables
        assert not any("NON-PRODUCTION" in w for w in plan.global_warnings)

    def test_lab_profile_adds_f5_tmm_overrides(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2", sizing_profile="lab")

        tmm = plan.suggested_variables["f5-tmm"]
        assert tmm["tmm"]["resources"]["requests"] == {
            "cpu": "1", "memory": "2Gi", "hugepages-2Mi": "2Gi",
        }
        assert tmm["tmm"]["resources"]["limits"] == {
            "cpu": "1", "memory": "2Gi", "hugepages-2Mi": "2Gi",
        }
        assert tmm["blobd"]["resources"]["requests"] == {"cpu": "100m", "memory": "512Mi"}
        assert tmm["blobd"]["resources"]["limits"] == {"cpu": "200m", "memory": "512Mi"}
        assert tmm["debug"]["resources"]["requests"] == {"cpu": "100m", "memory": "256Mi"}
        assert tmm["debug"]["resources"]["limits"] == {"cpu": "100m", "memory": "256Mi"}
        assert tmm["observer"]["resources"]["requests"] == {"cpu": "100m", "memory": "256Mi"}
        assert tmm["observer"]["resources"]["limits"] == {"cpu": "100m", "memory": "256Mi"}

    def test_lab_profile_adds_warning(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2", sizing_profile="lab")
        assert any("NON-PRODUCTION" in w for w in plan.global_warnings)

    def test_lab_profile_preserves_other_suggested_variables(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2", sizing_profile="lab")
        assert plan.suggested_variables.get("cloud_provider") == "aws"


# ---------------------------------------------------------------------------
# Tests: Serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    """Test plan serialization to dict."""

    @pytest.fixture
    def plan_dict(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        return plan.to_dict()

    def test_has_required_fields(self, plan_dict):
        assert "cluster_id" in plan_dict
        assert "cluster_name" in plan_dict
        assert "template_slug" in plan_dict
        assert "modules" in plan_dict
        assert "summary" in plan_dict
        assert "global_blockers" in plan_dict
        assert "global_warnings" in plan_dict
        assert "suggested_variables" in plan_dict
        assert "is_ready" in plan_dict

    def test_summary_counts(self, plan_dict):
        summary = plan_dict["summary"]
        assert summary["total"] == 7
        assert summary["deploy"] + summary["skip"] + summary["investigate"] + summary["blocked"] == summary["total"]

    def test_module_fields(self, plan_dict):
        for mod in plan_dict["modules"]:
            assert "path" in mod
            assert "name" in mod
            assert "action" in mod
            assert "reason" in mod
            assert "confidence" in mod
            assert "variable_overrides" in mod
            assert "warnings" in mod
            assert "blockers" in mod

    def test_template_info(self, plan_dict):
        assert plan_dict["template_slug"] == "f5-bnk-2.2"
        assert plan_dict["template_name"] == "F5 BNK 2.2"


# ---------------------------------------------------------------------------
# Tests: Custom Module List
# ---------------------------------------------------------------------------

class TestCustomModuleList:
    """Test plan_for_modules with arbitrary paths."""

    def test_custom_module_list(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_modules(["k8s/cert-manager", "bnk/flo"])
        assert len(plan.modules) == 2

    def test_custom_cert_manager_deploys(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_modules(["k8s/cert-manager"])
        assert plan.modules[0].action == "deploy"

    def test_custom_unknown_module(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_modules(["some/unknown-module"])
        assert plan.modules[0].action == "deploy"  # Generic analyzer defaults to deploy

    def test_no_template_slug(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_modules(["k8s/cert-manager"])
        assert plan.template_slug is None


# ---------------------------------------------------------------------------
# Tests: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Test various edge cases."""

    def test_cert_manager_partial_few_pods(self):
        """cert-manager CRDs exist but only 1 pod running."""
        scan = _base_scan({
            "prerequisites": {
                "cert_manager": {
                    "status": "detected",
                    "version": "1.14.0",
                    "crds_installed": True,
                    "crd_count": 6,
                    "pods": {"controller": 1, "webhook": 0, "cainjector": 0, "total_running": 1},
                },
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        cm = next(m for m in plan.modules if m.path == "k8s/cert-manager")
        assert cm.action == "investigate"
        assert cm.confidence == "medium"

    def test_flo_helm_release_no_pods(self):
        """FLO has a Helm release but no running pods."""
        scan = _base_scan({
            "bnk_install": {
                "status": "partial",
                "flo": {
                    "version": None,
                    "pods": 0,
                    "running": 0,
                    "helm_release": {"name": "flo", "namespace": "f5-operator", "version": "1", "status": "deployed"},
                },
                "tmm": {"pods": 0, "running": 0, "containers": None},
                "namespaces": {"f5_operator": True, "f5_utils": False},
                "cne_instance": None,
                "vlans": [],
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        flo = next(m for m in plan.modules if m.path == "bnk/flo")
        assert flo.action == "investigate"
        assert "Helm release" in flo.reason

    def test_flo_skipped_when_helm_installed_no_flo_pods(self):
        """BNK installed via direct Helm/manual install (F5-supported) has no FLO —
        FLO should be skipped, not flagged for deploy."""
        scan = _base_scan({
            "bnk_install": {
                "status": "installed",
                "health": "healthy",
                "install_shape": "helm",
                "namespaces": {"f5_operator": True, "f5_utils": True},
                "flo": {"version": None, "pods": 0, "running": 0, "helm_release": None},
                "tmm": {"pods": 1, "running": 1, "containers": {"total_containers": 7, "ready_containers": 7, "containers": "7/7"}},
                "cne_instance": {"name": "bnk-instance", "features": {}},
                "vlans": [],
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        flo = next(m for m in plan.modules if m.path == "bnk/flo")
        assert flo.action == "skip"
        assert flo.confidence == "high"
        assert "Helm" in flo.reason

    def test_flo_deploys_when_nothing_installed(self):
        """Fresh cluster (no BNK, no FLO) should still favour deploying FLO."""
        scan = _base_scan()  # not_installed, flo running=0, no helm_release
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        flo = next(m for m in plan.modules if m.path == "bnk/flo")
        assert flo.action == "deploy"

    def test_flo_skipped_when_flo_installed_and_running(self):
        """No regression: BNK installed via FLO with FLO pods running still skips."""
        scan = _fully_installed_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        flo = next(m for m in plan.modules if m.path == "bnk/flo")
        assert flo.action == "skip"

    def test_cneinstance_skipped_when_helm_installed_no_cr(self):
        """BNK installed via direct Helm/manual install (F5-supported) has no CNEInstance
        CR yet is fully running — cneinstance should be skipped, not flagged for deploy."""
        scan = _base_scan({
            "bnk_install": {
                "status": "installed",
                "health": "healthy",
                "install_shape": "helm",
                "namespaces": {"f5_operator": True, "f5_utils": True},
                "flo": {"version": None, "pods": 0, "running": 0, "helm_release": None},
                "tmm": {"pods": 1, "running": 1, "containers": {"total_containers": 7, "ready_containers": 7, "containers": "7/7"}},
                "cne_instance": None,
                "vlans": [],
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        cne = next(m for m in plan.modules if m.path == "bnk/cneinstance")
        assert cne.action == "skip"
        assert cne.confidence == "high"
        assert "already installed" in cne.reason

    def test_cneinstance_deploys_when_nothing_installed(self):
        """Fresh cluster (no BNK, no CNEInstance CR) should still favour deploying it."""
        scan = _base_scan()  # not_installed, cne_instance None
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        cne = next(m for m in plan.modules if m.path == "bnk/cneinstance")
        assert cne.action == "deploy"

    def test_vlans_unprogrammed(self):
        """VLANs exist but are not programmed."""
        scan = _base_scan({
            "bnk_install": {
                "status": "partial",
                "vlans": [
                    {"name": "external", "interfaces": ["1.1"], "self_ips": ["10.0.10.240"], "mtu": 9000, "programmed": True},
                    {"name": "internal", "interfaces": ["1.2"], "self_ips": ["10.0.20.240"], "mtu": 9000, "programmed": False},
                ],
                "namespaces": {"f5_operator": True, "f5_utils": True},
                "flo": {"version": "2.2.0", "pods": 1, "running": 1, "helm_release": None},
                "tmm": {"pods": 1, "running": 1, "containers": {"total_containers": 7, "ready_containers": 7, "containers": "7/7"}},
                "cne_instance": {"name": "bnk-instance", "features": {}},
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        vlans = next(m for m in plan.modules if m.path == "bnk/bnk-vlans")
        assert vlans.action == "investigate"
        assert "not programmed" in vlans.reason

    def test_cneinstance_exists_tmm_not_running(self):
        """CNEInstance exists but TMM is not running (starting up)."""
        scan = _base_scan({
            "bnk_install": {
                "status": "partial",
                "cne_instance": {"name": "bnk-instance", "features": {"firewallACL": True}},
                "tmm": {"pods": 1, "running": 0, "containers": None},
                "namespaces": {"f5_operator": True, "f5_utils": True},
                "flo": {"version": "2.2.0", "pods": 1, "running": 1, "helm_release": None},
                "vlans": [],
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        cne = next(m for m in plan.modules if m.path == "bnk/cneinstance")
        assert cne.action == "investigate"

    def test_empty_scan_data(self):
        """Minimal scan data shouldn't crash."""
        scan = {
            "cluster_id": 99,
            "cluster_name": "empty",
            "cluster_info": {},
            "prerequisites": {},
            "bnk_install": {},
        }
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert len(plan.modules) == 7

    def test_module_order_preserved(self):
        """Module order should match template definition."""
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        orders = [m.order for m in plan.modules]
        assert orders == sorted(orders)

    def test_confidence_levels(self):
        """All modules should have a valid confidence level."""
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        for m in plan.modules:
            assert m.confidence in ("high", "medium", "low"), f"{m.path} has invalid confidence: {m.confidence}"

    def test_action_values(self):
        """All modules should have a valid action."""
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        for m in plan.modules:
            assert m.action in ("deploy", "skip", "upgrade", "investigate", "blocked"), \
                f"{m.path} has invalid action: {m.action}"


# ---------------------------------------------------------------------------
# Tests: F5 BNK 2.3 template (mirrors f5-bnk-2.2 module set/behavior)
# ---------------------------------------------------------------------------

class TestBnk23Template:
    """f5-bnk-2.3 should mirror f5-bnk-2.2's module set and global-blocker behavior."""

    def test_known_template_exists(self):
        assert "f5-bnk-2.3" in TEMPLATE_MODULES

    def test_same_module_count_as_2_2(self):
        assert len(TEMPLATE_MODULES["f5-bnk-2.3"]) == len(TEMPLATE_MODULES["f5-bnk-2.2"])

    def test_same_module_paths_as_2_2(self):
        paths_22 = [m["path"] for m in TEMPLATE_MODULES["f5-bnk-2.2"]]
        paths_23 = [m["path"] for m in TEMPLATE_MODULES["f5-bnk-2.3"]]
        assert paths_23 == paths_22

    def test_display_name(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.3")
        assert plan.template_name == "F5 BNK 2.3"

    def test_plan_has_same_module_count_as_2_2(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan_22 = selector.plan_for_template("f5-bnk-2.2")
        plan_23 = selector.plan_for_template("f5-bnk-2.3")
        assert len(plan_23.modules) == len(plan_22.modules)

    def test_fresh_cluster_all_deploy(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.3")
        assert plan.deploy_count == 7
        assert plan.skip_count == 0
        assert plan.is_ready is True

    def test_no_hp_nodes_warning_fires_same_as_2_2(self):
        """The HP-node / TMM global-blocker logic must fire for 2.3 exactly as it does for 2.2."""
        scan = _base_scan({
            "cluster_info": {
                "hp_nodes": 0,
                "hp_node_details": [],
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan_22 = selector.plan_for_template("f5-bnk-2.2")
        plan_23 = selector.plan_for_template("f5-bnk-2.3")

        assert any("high-performance nodes" in w for w in plan_23.global_warnings)
        # Same warning text fires for both templates.
        assert plan_22.global_warnings == plan_23.global_warnings

    def test_multus_missing_warning_fires_same_as_2_2(self):
        scan = _base_scan({"prerequisites": {"multus": {"status": "missing"}}})
        selector = AdaptiveModuleSelector(scan)
        plan_22 = selector.plan_for_template("f5-bnk-2.2")
        plan_23 = selector.plan_for_template("f5-bnk-2.3")

        assert any("Multus" in w for w in plan_23.global_warnings)
        assert plan_22.global_warnings == plan_23.global_warnings

    def test_sriov_missing_warning_fires_same_as_2_2(self):
        scan = _base_scan({"prerequisites": {"sriov": {"status": "missing"}}})
        selector = AdaptiveModuleSelector(scan)
        plan_22 = selector.plan_for_template("f5-bnk-2.2")
        plan_23 = selector.plan_for_template("f5-bnk-2.3")

        assert any("SR-IOV" in w for w in plan_23.global_warnings)
        assert plan_22.global_warnings == plan_23.global_warnings

    def test_2_2_behavior_unchanged(self):
        """Regression guard: f5-bnk-2.2 plan output is unaffected by adding 2.3."""
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        assert plan.template_name == "F5 BNK 2.2"
        assert len(plan.modules) == 7
        assert plan.deploy_count == 7
        assert plan.is_ready is True
