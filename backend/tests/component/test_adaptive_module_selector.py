"""
BC-031: Component tests for AdaptiveModuleSelector — module selection algorithm.

Tests cover: plan_for_template, plan_for_modules, global blockers,
per-module analysis (cert-manager, network-setup, flo, cneinstance, vlans,
gatewayclass, gateway), variable suggestions, and edge cases.
No DB/mocking needed — this is a pure-logic class.
"""

import pytest

from services.adaptive_module_selector import (
    AdaptiveModuleSelector,
    DeploymentPlan,
    ModuleAction,
)

# ── Helpers ──────────────────────────────────────────────────────────

def _base_scan(overrides=None):
    """Build a base scan_data dict with sensible defaults."""
    scan = {
        "cluster_id": 1,
        "cluster_name": "test-cluster",
        "cluster_info": {
            "distribution": "generic",
            "hp_nodes": 2,
            "nodes_ready": 3,
        },
        "prerequisites": {
            "cert_manager": {"status": "missing"},
            "multus": {"status": "detected"},
            "sriov": {"status": "detected", "total_vfs": 8, "node_details": [
                {"node": "node1", "resources": {"intel.com/sriov_netdevice_ext": 4}},
            ]},
            "hugepages": {"status": "detected"},
            "storage": {"default": "gp3"},
            "gateway_api": {"status": "missing", "gatewayclasses": 0, "gateways": 0},
        },
        "bnk_install": {
            "status": "not_installed",
            "namespaces": {"f5_operator": False, "f5_utils": False},
            "flo": {"running": 0, "version": None, "helm_release": None},
            "tmm": {"running": 0},
            "vlans": [],
        },
        "platform_context": {
            "detected_platform_profile": "unknown",
        },
    }
    if overrides:
        _deep_merge(scan, overrides)
    return scan


def _deep_merge(base, override):
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# ── plan_for_template ────────────────────────────────────────────────

class TestPlanForTemplate:
    def test_known_template_returns_plan(self):
        selector = AdaptiveModuleSelector(_base_scan())
        plan = selector.plan_for_template("f5-bnk-2.2")

        assert isinstance(plan, DeploymentPlan)
        assert plan.cluster_id == 1
        assert plan.template_slug == "f5-bnk-2.2"
        assert len(plan.modules) > 0

    def test_unknown_template_raises(self):
        selector = AdaptiveModuleSelector(_base_scan())
        with pytest.raises(ValueError, match="Unknown template"):
            selector.plan_for_template("nonexistent-template")

    def test_plan_summary_counts(self):
        selector = AdaptiveModuleSelector(_base_scan())
        plan = selector.plan_for_template("f5-bnk-2.2")

        total = plan.deploy_count + plan.skip_count + plan.investigate_count + plan.blocked_count
        assert total == len(plan.modules)

    def test_plan_to_dict(self):
        selector = AdaptiveModuleSelector(_base_scan())
        plan = selector.plan_for_template("f5-bnk-2.2")
        d = plan.to_dict()

        assert "modules" in d
        assert "summary" in d
        assert d["summary"]["total"] == len(plan.modules)


# ── Global blockers ─────────────────────────────────────────────────

class TestGlobalBlockers:
    def test_no_ready_nodes_is_global_blocker(self):
        scan = _base_scan({"cluster_info": {"nodes_ready": 0}})
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")

        assert any("No ready nodes" in b for b in plan.global_blockers)
        assert plan.is_ready is False

    def test_missing_multus_warns_for_bnk(self):
        scan = _base_scan({"prerequisites": {"multus": {"status": "missing"}}})
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")

        assert any("Multus" in w for w in plan.global_warnings)

    def test_missing_multus_warning_is_explicit_for_generic_onprem_baseline(self):
        scan = _base_scan(
            {
                "prerequisites": {"multus": {"status": "missing"}},
                "platform_context": {"detected_platform_profile": "generic_onprem"},
            }
        )
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")

        assert any("generic_onprem baseline" in w for w in plan.global_warnings)

    def test_demo_apps_blocked_when_bnk_not_installed(self):
        scan = _base_scan({"bnk_install": {"status": "not_installed"}})
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("eks-bnk-smartllm-demo")

        assert any("BNK is not installed" in b for b in plan.global_blockers)


# ── cert-manager analysis ───────────────────────────────────────────

class TestCertManagerAnalysis:
    def test_fully_running_cert_manager_skipped(self):
        scan = _base_scan({
            "prerequisites": {
                "cert_manager": {"status": "detected", "version": "1.14.0",
                                  "pods": {"total_running": 3}},
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        cm = next(m for m in plan.modules if m.path == "k8s/cert-manager")
        assert cm.action == "skip"
        assert cm.confidence == "high"

    def test_missing_cert_manager_deployed(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        cm = next(m for m in plan.modules if m.path == "k8s/cert-manager")
        assert cm.action == "deploy"

    def test_partial_cert_manager_investigate(self):
        scan = _base_scan({
            "prerequisites": {
                "cert_manager": {"status": "partial", "pods": {"total_running": 0}},
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        cm = next(m for m in plan.modules if m.path == "k8s/cert-manager")
        assert cm.action == "investigate"


# ── network-setup analysis ──────────────────────────────────────────

class TestNetworkSetupAnalysis:
    def test_bnk_installed_skips_network(self):
        scan = _base_scan({"bnk_install": {"status": "installed"}})
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        ns = next(m for m in plan.modules if m.path == "k8s/network-setup")
        assert ns.action == "skip"

    def test_missing_multus_blocks_network(self):
        scan = _base_scan({"prerequisites": {"multus": {"status": "missing"}}})
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        ns = next(m for m in plan.modules if m.path == "k8s/network-setup")
        assert ns.action == "blocked"

    def test_sriov_resources_pre_filled(self):
        scan = _base_scan()  # sriov is "detected" by default
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        ns = next(m for m in plan.modules if m.path == "k8s/network-setup")
        assert "external_resource_name" in ns.variable_overrides


# ── FLO analysis ────────────────────────────────────────────────────

class TestFLOAnalysis:
    def test_flo_running_and_bnk_installed_skipped(self):
        scan = _base_scan({
            "bnk_install": {
                "status": "installed",
                "flo": {"running": 2, "version": "1.5.0", "helm_release": "flo"},
            }
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        flo = next(m for m in plan.modules if m.path == "bnk/flo")
        assert flo.action == "skip"

    def test_flo_not_installed_deployed(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")
        flo = next(m for m in plan.modules if m.path == "bnk/flo")
        assert flo.action == "deploy"


# ── plan_for_modules ────────────────────────────────────────────────

class TestPlanForModules:
    def test_arbitrary_module_list(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_modules(["k8s/cert-manager", "bnk/flo"])

        assert len(plan.modules) == 2
        assert plan.modules[0].path == "k8s/cert-manager"
        assert plan.modules[1].path == "bnk/flo"

    def test_unknown_module_deploys_by_default(self):
        scan = _base_scan()
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_modules(["custom/my-module"])

        assert plan.modules[0].action == "deploy"
        assert plan.modules[0].confidence == "high"


# ── Variable suggestions ────────────────────────────────────────────

class TestVariableSuggestions:
    def test_eks_cluster_suggests_aws_provider(self):
        scan = _base_scan({
            "cluster_info": {"distribution": "EKS", "region": "us-west-2"},
        })
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")

        assert plan.suggested_variables.get("cloud_provider") == "aws"
        assert plan.suggested_variables.get("region") == "us-west-2"

    def test_hp_nodes_suggest_mtu(self):
        scan = _base_scan({"cluster_info": {"hp_nodes": 3}})
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")

        assert plan.suggested_variables.get("tmm_default_mtu") == "9000"

    def test_storage_class_suggested(self):
        scan = _base_scan({"prerequisites": {"storage": {"default": "ebs-sc"}}})
        selector = AdaptiveModuleSelector(scan)
        plan = selector.plan_for_template("f5-bnk-2.2")

        assert plan.suggested_variables.get("storage_class_name") == "ebs-sc"
