"""
Unit tests for DPF detection in the cluster scanner.

Tests analyze_dpf() in services.scanner.prereqs and the DPF recommendation
in services.scanner.recommendations.

All functions are pure (dict in → dict out) with no I/O, no mocking needed.
"""

import pytest

from services.scanner.constants import (
    DPF_CORE_CRDS,
    DPF_CRD_GROUPS,
    DPF_SERVICE_CRDS,
    PrerequisiteStatus,
)
from services.scanner.prereqs import analyze_dpf
from services.scanner.recommendations import build_recommendations

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_crd(name: str, group: str, kind: str, versions=None):
    return {"name": name, "group": group, "kind": kind, "versions": versions or ["v1alpha1"]}


def _dpf_crds_all():
    """All DPF core + service CRDs."""
    crds = [
        _make_crd("dpfoperatorconfigs.operator.dpu.nvidia.com", "operator.dpu.nvidia.com", "DPFOperatorConfig"),
        _make_crd("dpudevices.provisioning.dpu.nvidia.com", "provisioning.dpu.nvidia.com", "DPUDevice"),
        _make_crd("dpusets.provisioning.dpu.nvidia.com", "provisioning.dpu.nvidia.com", "DPUSet"),
        _make_crd("dpuclusters.provisioning.dpu.nvidia.com", "provisioning.dpu.nvidia.com", "DPUCluster"),
        _make_crd("bfbs.provisioning.dpu.nvidia.com", "provisioning.dpu.nvidia.com", "BFB"),
        _make_crd("dpuservices.svc.dpu.nvidia.com", "svc.dpu.nvidia.com", "DPUService"),
        _make_crd("dpuservicechains.svc.dpu.nvidia.com", "svc.dpu.nvidia.com", "DPUServiceChain"),
        _make_crd("dpuserviceinterfaces.svc.dpu.nvidia.com", "svc.dpu.nvidia.com", "DPUServiceInterface"),
    ]
    return crds


def _crd_names(crds):
    return {c["name"] for c in crds}


def _dpf_operator_config(ready=True, version="25.10.1"):
    conditions = [
        {"type": "Ready", "status": "True" if ready else "False", "reason": "Reconciled"},
        {"type": "SystemComponentsReady", "status": "True" if ready else "False", "reason": "Ready"},
    ]
    return {
        "metadata": {"name": "dpf-config", "namespace": "dpf-operator-system"},
        "spec": {},
        "status": {
            "version": version,
            "conditions": conditions,
        },
    }


def _dpudevice(name="bf3-001", serial="MT12345", ready=True, dpu_type="BlueField3"):
    conditions = []
    if ready:
        conditions = [
            {"type": "Discovered", "status": "True"},
            {"type": "NodeAttached", "status": "True"},
            {"type": "Initialized", "status": "True"},
            {"type": "Ready", "status": "True"},
        ]
    else:
        conditions = [
            {"type": "Discovered", "status": "True"},
            {"type": "Error", "status": "True", "reason": "FlashFailed"},
        ]
    return {
        "metadata": {"name": name, "namespace": "dpf-provisioning"},
        "spec": {"serialNumber": serial},
        "status": {
            "dpuType": dpu_type,
            "conditions": conditions,
        },
    }


def _dpuset(name="prod-dpus"):
    return {"metadata": {"name": name, "namespace": "dpf-provisioning"}, "spec": {}, "status": {}}


def _dpucluster(name="dpu-cluster-1", ready=True, cluster_type="kamaji"):
    conditions = [{"type": "Ready", "status": "True" if ready else "False", "reason": "Running"}]
    return {
        "metadata": {"name": name, "namespace": "dpf-provisioning"},
        "spec": {"type": cluster_type},
        "status": {"conditions": conditions},
    }


def _dpuservice(name="hbn", ready=True):
    conditions = [{"type": "Ready", "status": "True" if ready else "False", "reason": "Deployed"}]
    return {
        "metadata": {"name": name, "namespace": "dpf-services"},
        "spec": {"helmChart": {"source": {"repoURL": "oci://nvcr.io/nvidia/doca", "version": "1.0.0"}}},
        "status": {"conditions": conditions},
    }


def _bfb(name="bf3-bfb-25.10"):
    return {
        "metadata": {"name": name, "namespace": "dpf-provisioning"},
        "spec": {"url": "https://content.mellanox.com/BlueField/BFB/ubuntu22.04/bf3_25.10.bfb"},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
    }


def _helm_release(name="dpf-operator", namespace="dpf-operator-system", version="1"):
    return {"name": name, "namespace": namespace, "version": version, "status": "deployed"}


# ---------------------------------------------------------------------------
# analyze_dpf — status detection
# ---------------------------------------------------------------------------


class TestAnalyzeDpfStatus:
    """Test DPF status detection logic."""

    def test_missing_no_crds(self):
        result = analyze_dpf([], set(), [], [], [], [], [], [], [])
        assert result["status"] == PrerequisiteStatus.MISSING

    def test_missing_unrelated_crds_only(self):
        crds = [_make_crd("certificates.cert-manager.io", "cert-manager.io", "Certificate")]
        result = analyze_dpf(crds, _crd_names(crds), [], [], [], [], [], [], [])
        assert result["status"] == PrerequisiteStatus.MISSING

    def test_partial_crds_but_no_operator_config(self):
        crds = _dpf_crds_all()
        result = analyze_dpf(crds, _crd_names(crds), [], [], [], [], [], [], [])
        assert result["status"] == PrerequisiteStatus.PARTIAL

    def test_partial_operator_not_ready(self):
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config(ready=False)
        result = analyze_dpf(crds, _crd_names(crds), [cfg], [], [], [], [], [], [])
        assert result["status"] == PrerequisiteStatus.PARTIAL

    def test_detected_operator_ready(self):
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config(ready=True)
        result = analyze_dpf(crds, _crd_names(crds), [cfg], [], [], [], [], [], [])
        assert result["status"] == PrerequisiteStatus.DETECTED

    def test_detected_with_full_inventory(self):
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config(ready=True, version="25.10.1")
        devices = [_dpudevice("bf3-001"), _dpudevice("bf3-002")]
        sets = [_dpuset()]
        clusters = [_dpucluster()]
        services = [_dpuservice("hbn"), _dpuservice("ovnk")]
        bfbs = [_bfb()]
        result = analyze_dpf(crds, _crd_names(crds), [cfg], devices, sets, clusters, services, bfbs, [])
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["version"] == "25.10.1"
        assert result["devices"]["total"] == 2
        assert result["devices"]["ready"] == 2
        assert result["dpusets"] == 1
        assert result["dpuclusters"] == 1
        assert result["dpuservices"] == 2
        assert result["bfbs"] == 1


# ---------------------------------------------------------------------------
# analyze_dpf — version extraction
# ---------------------------------------------------------------------------


class TestAnalyzeDpfVersion:
    """Test DPF version extraction."""

    def test_version_from_operator_config(self):
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config(version="25.10.1")
        result = analyze_dpf(crds, _crd_names(crds), [cfg], [], [], [], [], [], [])
        assert result["version"] == "25.10.1"

    def test_version_from_helm_release(self):
        crds = _dpf_crds_all()
        cfg_no_version = {
            "metadata": {"name": "dpf-config"},
            "spec": {},
            "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        }
        helm = [_helm_release("dpf-operator", version="3")]
        result = analyze_dpf(crds, _crd_names(crds), [cfg_no_version], [], [], [], [], [], helm)
        assert result["version"] == "3"

    def test_no_version_available(self):
        result = analyze_dpf([], set(), [], [], [], [], [], [], [])
        assert result["version"] is None


# ---------------------------------------------------------------------------
# analyze_dpf — CRD counts
# ---------------------------------------------------------------------------


class TestAnalyzeDpfCrds:
    """Test CRD detection and counting."""

    def test_all_core_crds_found(self):
        crds = _dpf_crds_all()
        result = analyze_dpf(crds, _crd_names(crds), [], [], [], [], [], [], [])
        assert len(result["core_crds_found"]) == len(DPF_CORE_CRDS)
        assert result["core_crds_missing"] == []

    def test_partial_core_crds(self):
        crds = [
            _make_crd("dpfoperatorconfigs.operator.dpu.nvidia.com", "operator.dpu.nvidia.com", "DPFOperatorConfig"),
            _make_crd("dpudevices.provisioning.dpu.nvidia.com", "provisioning.dpu.nvidia.com", "DPUDevice"),
        ]
        result = analyze_dpf(crds, _crd_names(crds), [], [], [], [], [], [], [])
        assert len(result["core_crds_found"]) == 2
        assert len(result["core_crds_missing"]) == 3  # 5 core - 2 found

    def test_service_crds_found(self):
        crds = _dpf_crds_all()
        result = analyze_dpf(crds, _crd_names(crds), [], [], [], [], [], [], [])
        assert len(result["service_crds_found"]) == len(DPF_SERVICE_CRDS)

    def test_total_crd_count(self):
        crds = _dpf_crds_all()
        result = analyze_dpf(crds, _crd_names(crds), [], [], [], [], [], [], [])
        assert result["crds_installed"] == 8  # 8 DPF CRDs in _dpf_crds_all


# ---------------------------------------------------------------------------
# analyze_dpf — operator details
# ---------------------------------------------------------------------------


class TestAnalyzeDpfOperator:
    """Test operator condition parsing."""

    def test_operator_conditions_parsed(self):
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config(ready=True)
        result = analyze_dpf(crds, _crd_names(crds), [cfg], [], [], [], [], [], [])
        assert result["operator"]["configured"] is True
        assert result["operator"]["ready"] is True
        assert len(result["operator"]["conditions"]) == 2
        assert result["operator"]["conditions"][0]["type"] == "Ready"

    def test_no_operator_config(self):
        result = analyze_dpf([], set(), [], [], [], [], [], [], [])
        assert result["operator"]["configured"] is False
        assert result["operator"]["ready"] is False
        assert result["operator"]["conditions"] == []

    def test_operator_with_null_conditions(self):
        """K8s API returns null for optional fields."""
        cfg = {
            "metadata": {"name": "dpf-config"},
            "spec": {},
            "status": {"conditions": None},
        }
        crds = _dpf_crds_all()
        result = analyze_dpf(crds, _crd_names(crds), [cfg], [], [], [], [], [], [])
        assert result["operator"]["configured"] is True
        assert result["operator"]["ready"] is False


# ---------------------------------------------------------------------------
# analyze_dpf — device counting
# ---------------------------------------------------------------------------


class TestAnalyzeDpfDevices:
    """Test device inventory analysis."""

    def test_counts_ready_devices(self):
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config()
        devices = [
            _dpudevice("bf3-001", ready=True),
            _dpudevice("bf3-002", ready=True),
            _dpudevice("bf3-003", ready=False),
        ]
        result = analyze_dpf(crds, _crd_names(crds), [cfg], devices, [], [], [], [], [])
        assert result["devices"]["total"] == 3
        assert result["devices"]["ready"] == 2

    def test_zero_devices(self):
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config()
        result = analyze_dpf(crds, _crd_names(crds), [cfg], [], [], [], [], [], [])
        assert result["devices"]["total"] == 0
        assert result["devices"]["ready"] == 0

    def test_device_with_no_conditions(self):
        """Device just created, no conditions yet."""
        device = {"metadata": {"name": "new-dpu"}, "spec": {}, "status": {}}
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config()
        result = analyze_dpf(crds, _crd_names(crds), [cfg], [device], [], [], [], [], [])
        assert result["devices"]["total"] == 1
        assert result["devices"]["ready"] == 0


# ---------------------------------------------------------------------------
# analyze_dpf — helm release detection
# ---------------------------------------------------------------------------


class TestAnalyzeDpfHelm:
    """Test Helm release detection."""

    def test_dpf_helm_release_found(self):
        helm = [_helm_release("dpf-operator")]
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config()
        result = analyze_dpf(crds, _crd_names(crds), [cfg], [], [], [], [], [], helm)
        assert result["helm_release"] is not None
        assert result["helm_release"]["name"] == "dpf-operator"

    def test_no_dpf_helm_release(self):
        helm = [_helm_release("cert-manager")]
        result = analyze_dpf([], set(), [], [], [], [], [], [], helm)
        assert result["helm_release"] is None


# ---------------------------------------------------------------------------
# DPF in recommendations
# ---------------------------------------------------------------------------


def _all_prereqs_detected():
    """Return minimal detected prereqs for build_recommendations."""
    return {
        "cert-manager": {"status": PrerequisiteStatus.DETECTED, "version": "1.14", "crd_count": 6,
                         "pods": {"total_running": 3}, "crds_installed": True, "crd_kinds": [],
                         "missing_crds": [], "helm_release": None},
        "multus": {"status": PrerequisiteStatus.DETECTED, "nad_crd_installed": True, "daemonset": None, "running_pods": 3},
        "sriov": {"status": PrerequisiteStatus.DETECTED, "device_plugin": {"name": "dp"}, "nodes_with_vfs": 2, "total_vfs": 16, "node_details": []},
        "hugepages": {"status": PrerequisiteStatus.DETECTED, "nodes_with_hugepages": 2, "node_details": []},
        "storage": {"status": PrerequisiteStatus.DETECTED, "count": 2, "default": "gp3", "has_gp3": True, "has_gp2": True, "classes": []},
        "gateway-api": {"status": PrerequisiteStatus.DETECTED, "crds_installed": 3, "standard_crds_found": [], "standard_crds_missing": [], "api_versions": ["v1"], "gatewayclasses": 1, "gateways": 1},
    }


def _bnk_installed():
    return {
        "status": "installed", "health": "healthy",
        "flo": {"version": "2.2.0", "pods": 1, "running": 1},
        "tmm": {"running": 1, "pods": 1, "containers": {"containers": 3}},
        "crds": {"total": 10},
    }


class TestDpfRecommendation:
    """Test DPF appears in recommendations."""

    def test_dpf_missing_shows_info(self):
        dpf = analyze_dpf([], set(), [], [], [], [], [], [], [])
        prereqs = _all_prereqs_detected()
        recs = build_recommendations(
            prereqs["cert-manager"], prereqs["multus"], prereqs["sriov"],
            prereqs["hugepages"], prereqs["storage"], prereqs["gateway-api"],
            _bnk_installed(), dpf,
        )
        dpf_recs = [r for r in recs if r["id"] == "dpf"]
        assert len(dpf_recs) == 1
        assert dpf_recs[0]["severity"] == "info"
        assert dpf_recs[0]["status"] == "skip"

    def test_dpf_partial_shows_recommended(self):
        crds = _dpf_crds_all()
        dpf = analyze_dpf(crds, _crd_names(crds), [], [], [], [], [], [], [])
        assert dpf["status"] == PrerequisiteStatus.PARTIAL
        prereqs = _all_prereqs_detected()
        recs = build_recommendations(
            prereqs["cert-manager"], prereqs["multus"], prereqs["sriov"],
            prereqs["hugepages"], prereqs["storage"], prereqs["gateway-api"],
            _bnk_installed(), dpf,
        )
        dpf_recs = [r for r in recs if r["id"] == "dpf"]
        assert len(dpf_recs) == 1
        assert dpf_recs[0]["severity"] == "recommended"
        assert dpf_recs[0]["status"] == "investigate"

    def test_dpf_detected_shows_info_with_counts(self):
        crds = _dpf_crds_all()
        cfg = _dpf_operator_config(version="25.10.1")
        devices = [_dpudevice("bf3-001"), _dpudevice("bf3-002")]
        dpf = analyze_dpf(crds, _crd_names(crds), [cfg], devices, [_dpuset()], [_dpucluster()], [_dpuservice()], [_bfb()], [])
        prereqs = _all_prereqs_detected()
        recs = build_recommendations(
            prereqs["cert-manager"], prereqs["multus"], prereqs["sriov"],
            prereqs["hugepages"], prereqs["storage"], prereqs["gateway-api"],
            _bnk_installed(), dpf,
        )
        dpf_recs = [r for r in recs if r["id"] == "dpf"]
        assert len(dpf_recs) == 1
        assert dpf_recs[0]["severity"] == "info"
        assert "v25.10.1" in dpf_recs[0]["title"]
        assert "2 DPU device" in dpf_recs[0]["description"]

    def test_dpf_none_backward_compat(self):
        """build_recommendations works when dpf is None (backward compat)."""
        prereqs = _all_prereqs_detected()
        recs = build_recommendations(
            prereqs["cert-manager"], prereqs["multus"], prereqs["sriov"],
            prereqs["hugepages"], prereqs["storage"], prereqs["gateway-api"],
            _bnk_installed(), dpf=None,
        )
        dpf_recs = [r for r in recs if r["id"] == "dpf"]
        assert len(dpf_recs) == 0  # No DPF rec when dpf=None
