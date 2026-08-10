"""
Unit tests for services.scanner.recommendations — data-driven recommendation builder.

Pure functions, no I/O, no mocking needed.
"""

from types import SimpleNamespace

import pytest

from services.scanner.constants import PrerequisiteStatus
from services.scanner.recommendations import (
    _bnk_recommendations,
    _effective_status,
    _render_rec,
    build_recommendations,
)

# Tests in this module exercise build_recommendations with every prereq
# enabled, regardless of the per-cluster opt-in feature added in v2_092.
ALL_PREREQS = {"cert-manager", "multus", "sriov", "hugepages", "storage", "gateway-api"}

# ---------------------------------------------------------------------------
# Helpers — minimal analysis dicts with required fields
# ---------------------------------------------------------------------------


def _cert_manager(status=PrerequisiteStatus.DETECTED, version="1.14.0", pods_running=3, crd_count=6):
    return {
        "status": status,
        "version": version,
        "crds_installed": status != PrerequisiteStatus.MISSING,
        "crd_count": crd_count,
        "crd_kinds": [],
        "missing_crds": [],
        "pods": {"controller": 1, "webhook": 1, "cainjector": 1, "total_running": pods_running},
        "helm_release": None,
    }


def _multus(status=PrerequisiteStatus.DETECTED, running_pods=3):
    return {"status": status, "nad_crd_installed": True, "daemonset": None, "running_pods": running_pods}


def _sriov(status=PrerequisiteStatus.DETECTED, nodes_with_vfs=2, total_vfs=16, device_plugin=True):
    return {
        "status": status,
        "device_plugin": {"name": "sriov-dp"} if device_plugin else None,
        "nodes_with_vfs": nodes_with_vfs,
        "total_vfs": total_vfs,
        "node_details": [],
    }


def _hugepages(status=PrerequisiteStatus.DETECTED, nodes_with_hugepages=2):
    return {"status": status, "nodes_with_hugepages": nodes_with_hugepages, "node_details": []}


def _storage(status=PrerequisiteStatus.DETECTED, count=2, default="gp3"):
    return {
        "status": status,
        "count": count,
        "default": default,
        "has_gp3": True,
        "has_gp2": True,
        "classes": [],
    }


def _gateway_api(status=PrerequisiteStatus.DETECTED, crds_installed=3, gateways=1, gatewayclasses=1):
    return {
        "status": status,
        "crds_installed": crds_installed,
        "standard_crds_found": [],
        "standard_crds_missing": [],
        "api_versions": ["v1"],
        "gatewayclasses": gatewayclasses,
        "gateways": gateways,
    }


def _bnk(status="installed", health="healthy", flo_version="2.2.0", flo_pods=1, flo_running=1,
         tmm_pods=1, tmm_running=1, containers=None, crds_total=10):
    return {
        "status": status,
        "health": health,
        "crds": {"total": crds_total, "groups": [], "has_data_plane": True, "has_flo": True, "has_gateway_ext": True},
        "flo": {"version": flo_version, "pods": flo_pods, "running": flo_running, "helm_release": None},
        "tmm": {
            "pods": tmm_pods,
            "running": tmm_running,
            "containers": containers or {"total_containers": 5, "ready_containers": 5, "containers": "5/5"},
        },
        "controller": {"pods": 1, "running": 1},
        "analyzer": {"pods": 1, "running": 1},
        "crd_installer": {"completed": True, "pods": 1},
        "cne_instance": None,
        "vlans": [],
        "namespaces": {"f5_operator": True, "f5_bnk": True, "f5_utils": True},
    }


def _ocp_platform_context(
    *,
    scc: bool | None = None,
    machine_config: bool | None = None,
    operator_framework: bool | None = None,
    operator_required_for_networking: bool | None = None,
    privileged_binding_required: bool | None = None,
):
    return {
        "detected_platform_profile": "ocp",
        "platform_capabilities": {
            "scc": scc,
            "machine_config": machine_config,
            "operator_framework": operator_framework,
        },
        "platform_constraints": {
            "operator_required_for_networking": operator_required_for_networking,
            "privileged_workloads_require_platform_security_binding": privileged_binding_required,
        },
    }


# =========================================================================
# _effective_status
# =========================================================================


class TestEffectiveStatus:
    def test_normal_status_passthrough(self):
        assert _effective_status("cert-manager", {"status": "detected"}) == "detected"
        assert _effective_status("multus", {"status": "missing"}) == "missing"

    def test_storage_no_default(self):
        data = {"status": PrerequisiteStatus.DETECTED, "default": None}
        assert _effective_status("storage", data) == "no_default"

    def test_storage_with_default(self):
        data = {"status": PrerequisiteStatus.DETECTED, "default": "gp3"}
        assert _effective_status("storage", data) == PrerequisiteStatus.DETECTED

    def test_storage_missing_stays_missing(self):
        data = {"status": PrerequisiteStatus.MISSING, "default": None}
        assert _effective_status("storage", data) == PrerequisiteStatus.MISSING


# =========================================================================
# _bnk_recommendations
# =========================================================================


class TestBnkRecommendations:
    def test_not_installed_produces_deployment_steps(self):
        recs = _bnk_recommendations(_bnk(status="not_installed", health=None))
        assert len(recs) == 6
        ids = [r["id"] for r in recs]
        assert "bnk-prerequisites" in ids
        assert "flo" in ids
        assert "cneinstance" in ids
        assert "bnk-vlans" in ids
        assert "bnk-gatewayclass" in ids
        assert "network-setup" in ids
        assert all(r["category"] == "bnk_component" for r in recs)
        assert all(r["status"] == "deploy" for r in recs)

    def test_partial_produces_investigate(self):
        recs = _bnk_recommendations(_bnk(status="partial", health="degraded", crds_total=5, flo_running=0, tmm_running=0))
        assert len(recs) == 1
        assert recs[0]["id"] == "bnk-install"
        assert recs[0]["status"] == "investigate"
        assert "F5 CRDs: 5" in recs[0]["description"]

    def test_installed_healthy(self):
        recs = _bnk_recommendations(_bnk())
        assert len(recs) == 1
        assert recs[0]["id"] == "bnk-install"
        assert recs[0]["status"] == "skip"
        assert "healthy" in recs[0]["title"]
        assert "FLO 2.2.0" in recs[0]["description"]

    def test_installed_no_containers_info(self):
        recs = _bnk_recommendations(_bnk(containers=None))
        # When containers is None (e.g. from a stub), just tmm_info.get("containers") is None
        # So the containers string part is omitted
        assert len(recs) == 1


# =========================================================================
# build_recommendations — full integration
# =========================================================================


class TestBuildRecommendations:
    def test_all_detected_healthy(self):
        """When everything is installed and healthy, all recs should be 'skip' or 'info'."""
        recs = build_recommendations(
            _cert_manager(), _multus(), _sriov(), _hugepages(),
            _storage(), _gateway_api(), _bnk(),
            enabled_prerequisites=ALL_PREREQS,
        )
        # 6 prereq recs + 1 bnk rec = 7
        assert len(recs) == 7
        assert all(r["severity"] == "info" for r in recs)
        assert all(r["status"] == "skip" for r in recs)

    def test_all_missing_not_installed(self):
        """When nothing is installed, produce deployment-oriented recs."""
        recs = build_recommendations(
            _cert_manager(PrerequisiteStatus.MISSING),
            _multus(PrerequisiteStatus.MISSING),
            _sriov(PrerequisiteStatus.MISSING),
            _hugepages(PrerequisiteStatus.MISSING),
            _storage(PrerequisiteStatus.MISSING),
            _gateway_api(PrerequisiteStatus.MISSING),
            _bnk(status="not_installed", health=None),
            enabled_prerequisites=ALL_PREREQS,
        )
        # 6 prereq recs + 6 BNK deployment steps = 12
        assert len(recs) == 12
        deploy_recs = [r for r in recs if r["status"] == "deploy"]
        assert len(deploy_recs) >= 9  # cert-manager + multus + sriov + hugepages + storage + 6 bnk steps (minus info ones)

    def test_partial_prereqs(self):
        recs = build_recommendations(
            _cert_manager(PrerequisiteStatus.PARTIAL, pods_running=1),
            _multus(PrerequisiteStatus.PARTIAL),
            _sriov(PrerequisiteStatus.PARTIAL, device_plugin=False),
            _hugepages(PrerequisiteStatus.DETECTED),
            _storage(PrerequisiteStatus.DETECTED, default=None),  # no_default pseudo-status
            _gateway_api(PrerequisiteStatus.PARTIAL),
            _bnk(status="partial", health="degraded"),
            enabled_prerequisites=ALL_PREREQS,
        )
        statuses = {r["id"]: r["status"] for r in recs}
        assert statuses["cert-manager"] == "investigate"
        assert statuses["multus"] == "investigate"
        assert statuses["sriov"] == "investigate"
        assert statuses["storage"] == "investigate"

    def test_cert_manager_description_uses_real_data(self):
        recs = build_recommendations(
            _cert_manager(PrerequisiteStatus.DETECTED, version="1.14.0", pods_running=3, crd_count=6),
            _multus(), _sriov(), _hugepages(), _storage(), _gateway_api(), _bnk(),
            enabled_prerequisites=ALL_PREREQS,
        )
        cm_rec = next(r for r in recs if r["id"] == "cert-manager")
        assert "1.14.0" in cm_rec["title"]
        assert "3 pods running" in cm_rec["description"]
        assert "6 CRDs" in cm_rec["description"]

    def test_sriov_description_uses_real_data(self):
        recs = build_recommendations(
            _cert_manager(), _multus(),
            _sriov(PrerequisiteStatus.DETECTED, nodes_with_vfs=3, total_vfs=24),
            _hugepages(), _storage(), _gateway_api(), _bnk(),
            enabled_prerequisites=ALL_PREREQS,
        )
        sr_rec = next(r for r in recs if r["id"] == "sriov")
        assert "3 node(s)" in sr_rec["description"]
        assert "24 total" in sr_rec["description"]

    def test_storage_no_default_shows_count(self):
        recs = build_recommendations(
            _cert_manager(), _multus(), _sriov(), _hugepages(),
            _storage(PrerequisiteStatus.DETECTED, count=3, default=None),
            _gateway_api(), _bnk(),
            enabled_prerequisites=ALL_PREREQS,
        )
        st_rec = next(r for r in recs if r["id"] == "storage")
        assert st_rec["status"] == "investigate"
        assert "3 storage class(es)" in st_rec["description"]

    def test_every_rec_has_required_fields(self):
        """All recommendations must have all required fields."""
        recs = build_recommendations(
            _cert_manager(), _multus(), _sriov(), _hugepages(),
            _storage(), _gateway_api(), _bnk(),
            enabled_prerequisites=ALL_PREREQS,
        )
        required_fields = {"id", "category", "severity", "title", "description", "module", "status"}
        for rec in recs:
            assert required_fields <= set(rec.keys()), f"Missing fields in rec {rec['id']}: {required_fields - set(rec.keys())}"

    def test_categories_are_valid(self):
        recs = build_recommendations(
            _cert_manager(PrerequisiteStatus.MISSING),
            _multus(PrerequisiteStatus.MISSING),
            _sriov(PrerequisiteStatus.MISSING),
            _hugepages(PrerequisiteStatus.MISSING),
            _storage(PrerequisiteStatus.MISSING),
            _gateway_api(PrerequisiteStatus.MISSING),
            _bnk(status="not_installed"),
            enabled_prerequisites=ALL_PREREQS,
        )
        valid_categories = {"prerequisite", "bnk_component", "optimization"}
        for rec in recs:
            assert rec["category"] in valid_categories

    def test_gateway_api_detected_shows_counts(self):
        recs = build_recommendations(
            _cert_manager(), _multus(), _sriov(), _hugepages(), _storage(),
            _gateway_api(PrerequisiteStatus.DETECTED, crds_installed=5, gateways=2, gatewayclasses=1),
            _bnk(),
            enabled_prerequisites=ALL_PREREQS,
        )
        gw_rec = next(r for r in recs if r["id"] == "gateway-api")
        assert "5 CRDs" in gw_rec["title"]
        assert "2 Gateway(s)" in gw_rec["description"]

    def test_hugepages_detected_shows_node_count(self):
        recs = build_recommendations(
            _cert_manager(), _multus(), _sriov(),
            _hugepages(PrerequisiteStatus.DETECTED, nodes_with_hugepages=4),
            _storage(), _gateway_api(), _bnk(),
            enabled_prerequisites=ALL_PREREQS,
        )
        hp_rec = next(r for r in recs if r["id"] == "hugepages")
        assert "4 node(s)" in hp_rec["title"]

    def test_bnk_installed_shows_flo_version_and_tmm_pods(self):
        recs = build_recommendations(
            _cert_manager(), _multus(), _sriov(), _hugepages(), _storage(), _gateway_api(),
            _bnk(status="installed", health="healthy", flo_version="2.2.0", tmm_pods=3, tmm_running=3),
            enabled_prerequisites=ALL_PREREQS,
        )
        bnk_rec = next(r for r in recs if r["id"] == "bnk-install")
        assert "FLO 2.2.0" in bnk_rec["description"]
        assert "3/3 pods" in bnk_rec["description"]

    def test_generic_onprem_adds_explicit_baseline_context_for_core_manual_prereqs(self):
        recs = build_recommendations(
            _cert_manager(PrerequisiteStatus.MISSING),
            _multus(PrerequisiteStatus.MISSING),
            _sriov(PrerequisiteStatus.MISSING),
            _hugepages(PrerequisiteStatus.MISSING),
            _storage(),
            _gateway_api(),
            _bnk(),
            platform_profile="generic_onprem",
            enabled_prerequisites=ALL_PREREQS,
        )

        by_id = {r["id"]: r for r in recs}
        assert "generic_onprem baseline" in by_id["multus"]["description"]
        assert "generic_onprem baseline" in by_id["sriov"]["description"]
        assert "generic_onprem baseline" in by_id["hugepages"]["description"]
        # Non-manual prerequisite text should remain unchanged.
        assert "generic_onprem baseline" not in by_id["cert-manager"]["description"]

    def test_ocp_adds_platform_readiness_guidance(self):
        recs = build_recommendations(
            _cert_manager(),
            _multus(PrerequisiteStatus.PARTIAL),
            _sriov(PrerequisiteStatus.PARTIAL),
            _hugepages(PrerequisiteStatus.MISSING, nodes_with_hugepages=0),
            _storage(),
            _gateway_api(),
            _bnk(),
            platform_profile="ocp",
            platform_context=_ocp_platform_context(
                scc=True,
                machine_config=True,
                operator_framework=True,
                operator_required_for_networking=True,
                privileged_binding_required=True,
            ),
            enabled_prerequisites=ALL_PREREQS,
        )

        by_id = {r["id"]: r for r in recs}
        assert by_id["ocp-security-binding-awareness"]["category"] == "optimization"
        assert by_id["ocp-security-binding-awareness"]["severity"] == "info"
        assert by_id["ocp-network-operator-path"]["status"] == "investigate"
        assert by_id["ocp-node-config-awareness"]["status"] == "investigate"

    def test_non_ocp_does_not_add_platform_readiness_guidance(self):
        recs = build_recommendations(
            _cert_manager(),
            _multus(),
            _sriov(),
            _hugepages(),
            _storage(),
            _gateway_api(),
            _bnk(),
            platform_profile="generic_onprem",
            enabled_prerequisites=ALL_PREREQS,
        )
        ids = {r["id"] for r in recs}
        assert "ocp-security-binding-awareness" not in ids
        assert "ocp-network-operator-path" not in ids
        assert "ocp-node-config-awareness" not in ids

    def test_ocp_security_guidance_only_when_scc_or_security_constraint_present(self):
        recs = build_recommendations(
            _cert_manager(),
            _multus(),
            _sriov(),
            _hugepages(),
            _storage(),
            _gateway_api(),
            _bnk(),
            platform_profile="ocp",
            platform_context=_ocp_platform_context(
                scc=False,
                machine_config=False,
                operator_framework=False,
                operator_required_for_networking=False,
                privileged_binding_required=False,
            ),
            enabled_prerequisites=ALL_PREREQS,
        )
        ids = {r["id"] for r in recs}
        assert "ocp-security-binding-awareness" not in ids

    def test_ocp_node_config_guidance_requires_machine_config_truth(self):
        recs = build_recommendations(
            _cert_manager(),
            _multus(),
            _sriov(),
            _hugepages(PrerequisiteStatus.MISSING, nodes_with_hugepages=0),
            _storage(),
            _gateway_api(),
            _bnk(),
            platform_profile="ocp",
            platform_context=_ocp_platform_context(
                scc=True,
                machine_config=False,
                operator_framework=True,
                operator_required_for_networking=True,
                privileged_binding_required=True,
            ),
            enabled_prerequisites=ALL_PREREQS,
        )
        ids = {r["id"] for r in recs}
        assert "ocp-node-config-awareness" not in ids


class TestClusterScannerOrderingRegression:
    def test_scan_applies_platform_context_before_building_recommendations(self, monkeypatch):
        """Regression guard: recommendations use platform context from apply step."""
        from services.scanner import ClusterScanner

        class _FakeDB:
            def flush(self):
                return None

        fake_cluster = SimpleNamespace(
            name="aws-eks-cluster",
            enabled_prerequisites=None,
            discovered_namespaces=None,
        )

        class _FakeK8sService:
            def __init__(self, _db):
                pass

            def get_cluster(self, _cluster_id):
                return fake_cluster

            def load_kubeconfig(self, _cluster):
                return object()

        class _FakePlatformContext:
            detected_platform_profile = "eks"

            def to_dict(self):
                return {
                    "detected_platform_profile": "eks",
                    "detected_platform_provider": "aws",
                    "platform_capabilities": {},
                    "platform_constraints": {},
                }

        captured = {}

        def _fake_build_recommendations(*args, **kwargs):
            captured["platform_profile"] = args[9]
            return []

        monkeypatch.setattr("services.scanner.KubernetesService", _FakeK8sService)
        monkeypatch.setattr(
            "services.scanner.fetch_scan_data",
            lambda _api_client, _k8s_service, _cluster_id, **_kw: {
                "version_info": {},
                "nodes": [],
                "namespaces": [],
                "crds": [],
                "crd_names": set(),
                "crd_groups": set(),
                "cert_manager_pods": [],
                "helm_releases": [],
                "kube_system_pods": [],
                "daemonsets": [],
                "storage_classes": [],
                "gateways": [],
                "gatewayclasses": [],
                "dpf_operator_configs": [],
                "dpudevices": [],
                "dpusets": [],
                "dpuclusters": [],
                "dpuservices": [],
                "bfbs": [],
                "kamaji_pods": [],
                "kamaji_tcps": [],
                "f5_tenant_pods": [],
                "f5_utils_pods": [],
                "cneinstances": [],
                "vlans": [],
                # CIS (D-023 P1) — empty on non-CIS clusters
                "cis_controllers": [],
                "cis_virtualservers": [],
                "cis_transportservers": [],
                "cis_ingresslinks": [],
            },
        )
        monkeypatch.setattr(
            "services.scanner.analyze_cluster_info",
            lambda *_args, **_kwargs: {"distribution": "EKS"},
        )
        monkeypatch.setattr(
            "services.scanner.analyze_cert_manager",
            lambda *_args, **_kwargs: _cert_manager(),
        )
        monkeypatch.setattr(
            "services.scanner.analyze_multus",
            lambda *_args, **_kwargs: _multus(),
        )
        monkeypatch.setattr(
            "services.scanner.analyze_sriov",
            lambda *_args, **_kwargs: _sriov(),
        )
        monkeypatch.setattr(
            "services.scanner.analyze_hugepages",
            lambda *_args, **_kwargs: _hugepages(),
        )
        monkeypatch.setattr(
            "services.scanner.analyze_storage",
            lambda *_args, **_kwargs: _storage(),
        )
        monkeypatch.setattr(
            "services.scanner.analyze_gateway_api",
            lambda *_args, **_kwargs: _gateway_api(),
        )
        monkeypatch.setattr(
            "services.scanner.analyze_dpf",
            lambda *_args, **_kwargs: {"status": "missing"},
        )
        monkeypatch.setattr(
            "services.scanner.analyze_kamaji",
            lambda *_args, **_kwargs: {"status": "missing"},
        )
        monkeypatch.setattr(
            "services.scanner.analyze_bnk_install",
            lambda *_args, **_kwargs: _bnk(),
        )
        monkeypatch.setattr(
            "services.scanner.PlatformContextService.apply_cluster_context",
            lambda *_args, **_kwargs: _FakePlatformContext(),
        )
        monkeypatch.setattr(
            "services.scanner.build_recommendations",
            _fake_build_recommendations,
        )

        result = ClusterScanner(_FakeDB()).scan(123)

        assert captured["platform_profile"] == "eks"
        assert result["platform_context"]["detected_platform_profile"] == "eks"
