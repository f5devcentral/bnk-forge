"""
Unit tests for services.dpf.health — DPF health analysis.

Pure functions (dict in → dict out) with no I/O, no mocking needed.
"""

import pytest

from services.dpf.health import (
    _analyze_bfbs,
    _analyze_clusters,
    _analyze_devices,
    _analyze_dpus,
    _analyze_operator,
    _analyze_services,
    _determine_overall_status,
    analyze_dpf_health,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _operator_config(ready=True, version="25.10.1"):
    conditions = [
        {"type": "Ready", "status": "True" if ready else "False", "reason": "Reconciled", "message": ""},
        {"type": "SystemComponentsReady", "status": "True" if ready else "False", "reason": "Ready", "message": ""},
    ]
    return {
        "metadata": {"name": "dpf-config", "namespace": "dpf-operator-system"},
        "spec": {},
        "status": {"version": version, "conditions": conditions},
    }


def _device(name="bf3-001", dpu_type="BlueField3", ready=True):
    conditions = []
    if ready:
        conditions = [
            {"type": "Discovered", "status": "True"},
            {"type": "NodeAttached", "status": "True"},
            {"type": "Ready", "status": "True"},
        ]
    else:
        conditions = [
            {"type": "Discovered", "status": "True"},
            {"type": "Error", "status": "True"},
        ]
    return {
        "metadata": {"name": name},
        "spec": {},
        "status": {"dpuType": dpu_type, "conditions": conditions},
    }


def _dpu(name="dpu-001", phase="Ready"):
    return {
        "metadata": {"name": name},
        "spec": {},
        "status": {"phase": phase},
    }


def _cluster(name="dpu-cluster-1", ready=True, cluster_type="kamaji"):
    conditions = [{"type": "Ready", "status": "True" if ready else "False", "reason": "Running"}]
    return {
        "metadata": {"name": name, "namespace": "dpf-provisioning"},
        "spec": {"type": cluster_type},
        "status": {"conditions": conditions},
    }


def _bfb(name="bf3-bfb", ready=True, url="https://example.com/bfb"):
    conditions = [{"type": "Ready", "status": "True" if ready else "False"}]
    return {
        "metadata": {"name": name},
        "spec": {"url": url},
        "status": {"conditions": conditions},
    }


def _service(name="hbn", ready=True):
    conditions = [{"type": "Ready", "status": "True" if ready else "False"}]
    return {
        "metadata": {"name": name, "namespace": "dpf-services"},
        "spec": {},
        "status": {"conditions": conditions},
    }


def _full_data(**overrides):
    """Full DPF data dict with reasonable defaults."""
    defaults = {
        "dpfoperatorconfig": [_operator_config()],
        "dpudevice": [_device("bf3-001"), _device("bf3-002")],
        "dpuset": [{"metadata": {"name": "prod-dpus"}}],
        "dpucluster": [_cluster()],
        "dpu": [_dpu("dpu-001"), _dpu("dpu-002")],
        "bfb": [_bfb()],
        "dpuflavor": [{"metadata": {"name": "default"}}],
        "dpuservice": [_service("hbn"), _service("ovnk")],
        "dpudeployment": [],
        "dpuservicechain": [{"metadata": {"name": "chain-1"}}],
        "dpuserviceinterface": [{"metadata": {"name": "iface-1"}}],
        "dpuserviceipam": [],
        "dpuservicecredreq": [],
        "servicechain": [],
        "serviceinterface": [],
        "dpunode": [],
        "dpudiscovery": [],
        "dpunodemaintenance": [],
    }
    defaults.update(overrides)
    return {"resources": defaults, "cluster_id": 1}


# =========================================================================
# _analyze_operator
# =========================================================================


class TestAnalyzeOperator:

    def test_no_configs(self):
        result = _analyze_operator([])
        assert result["configured"] is False
        assert result["ready"] is False
        assert result["version"] is None

    def test_ready_operator(self):
        result = _analyze_operator([_operator_config(ready=True, version="25.10.1")])
        assert result["configured"] is True
        assert result["ready"] is True
        assert result["version"] == "25.10.1"

    def test_not_ready_operator(self):
        result = _analyze_operator([_operator_config(ready=False)])
        assert result["configured"] is True
        assert result["ready"] is False

    def test_conditions_include_message(self):
        result = _analyze_operator([_operator_config()])
        for cond in result["conditions"]:
            assert "type" in cond
            assert "status" in cond
            assert "reason" in cond
            assert "message" in cond

    def test_null_conditions(self):
        """K8s API returns null for optional fields."""
        cfg = {"metadata": {"name": "x"}, "spec": {}, "status": {"conditions": None, "version": "1.0"}}
        result = _analyze_operator([cfg])
        assert result["configured"] is True
        assert result["ready"] is False
        assert result["version"] == "1.0"


# =========================================================================
# _analyze_devices
# =========================================================================


class TestAnalyzeDevices:

    def test_empty(self):
        result = _analyze_devices([])
        assert result["total"] == 0
        assert result["ready"] == 0

    def test_mixed_readiness(self):
        devices = [_device("bf3-001", ready=True), _device("bf3-002", ready=False), _device("bf3-003", ready=True)]
        result = _analyze_devices(devices)
        assert result["total"] == 3
        assert result["ready"] == 2

    def test_by_type(self):
        devices = [
            _device("bf3-001", dpu_type="BlueField3"),
            _device("bf3-002", dpu_type="BlueField3"),
            _device("bf4-001", dpu_type="BlueField4"),
        ]
        result = _analyze_devices(devices)
        assert result["byType"]["BlueField3"] == 2
        assert result["byType"]["BlueField4"] == 1

    def test_by_condition(self):
        devices = [_device("bf3-001", ready=True)]
        result = _analyze_devices(devices)
        assert result["byCondition"]["Discovered"] == 1
        assert result["byCondition"]["Ready"] == 1

    def test_no_conditions(self):
        """Freshly created device with empty status."""
        device = {"metadata": {"name": "new"}, "spec": {}, "status": {}}
        result = _analyze_devices([device])
        assert result["total"] == 1
        assert result["ready"] == 0
        assert result["byType"]["Unknown"] == 1


# =========================================================================
# _analyze_dpus
# =========================================================================


class TestAnalyzeDpus:

    def test_empty(self):
        result = _analyze_dpus([])
        assert result["total"] == 0
        assert result["byPhase"] == {}

    def test_by_phase(self):
        dpus = [_dpu("dpu-1", "Ready"), _dpu("dpu-2", "Provisioning"), _dpu("dpu-3", "Ready")]
        result = _analyze_dpus(dpus)
        assert result["total"] == 3
        assert result["byPhase"]["Ready"] == 2
        assert result["byPhase"]["Provisioning"] == 1

    def test_missing_phase(self):
        dpu = {"metadata": {"name": "x"}, "spec": {}, "status": {}}
        result = _analyze_dpus([dpu])
        assert result["byPhase"]["Unknown"] == 1


# =========================================================================
# _analyze_clusters
# =========================================================================


class TestAnalyzeClusters:

    def test_empty(self):
        result = _analyze_clusters([])
        assert result["total"] == 0
        assert result["ready"] == 0

    def test_mixed_readiness(self):
        clusters = [_cluster("c1", ready=True), _cluster("c2", ready=False)]
        result = _analyze_clusters(clusters)
        assert result["total"] == 2
        assert result["ready"] == 1

    def test_cluster_details(self):
        result = _analyze_clusters([_cluster("dpu-c", cluster_type="static")])
        assert len(result["clusters"]) == 1
        assert result["clusters"][0]["name"] == "dpu-c"
        assert result["clusters"][0]["type"] == "static"
        assert result["clusters"][0]["ready"] is True


# =========================================================================
# _analyze_bfbs
# =========================================================================


class TestAnalyzeBfbs:

    def test_empty(self):
        result = _analyze_bfbs([])
        assert result["total"] == 0
        assert result["ready"] == 0

    def test_bfb_details(self):
        result = _analyze_bfbs([_bfb("bf3-bfb", url="https://example.com/bfb")])
        assert result["total"] == 1
        assert result["ready"] == 1
        assert result["images"][0]["name"] == "bf3-bfb"
        assert result["images"][0]["url"] == "https://example.com/bfb"

    def test_not_ready_bfb(self):
        result = _analyze_bfbs([_bfb("downloading", ready=False)])
        assert result["total"] == 1
        assert result["ready"] == 0


# =========================================================================
# _analyze_services
# =========================================================================


class TestAnalyzeServices:

    def test_empty(self):
        result = _analyze_services([])
        assert result["total"] == 0
        assert result["ready"] == 0

    def test_mixed_readiness(self):
        svcs = [_service("hbn", ready=True), _service("ovnk", ready=False), _service("snap", ready=True)]
        result = _analyze_services(svcs)
        assert result["total"] == 3
        assert result["ready"] == 2


# =========================================================================
# _determine_overall_status
# =========================================================================


class TestDetermineOverallStatus:

    def test_not_installed(self):
        op = {"configured": False, "ready": False}
        assert _determine_overall_status(op, {"total": 0, "ready": 0}, {}, {"total": 0, "ready": 0}) == "not_installed"

    def test_degraded_operator_not_ready(self):
        op = {"configured": True, "ready": False}
        assert _determine_overall_status(op, {"total": 0, "ready": 0}, {}, {"total": 0, "ready": 0}) == "degraded"

    def test_no_devices(self):
        op = {"configured": True, "ready": True}
        assert _determine_overall_status(op, {"total": 0, "ready": 0}, {}, {"total": 0, "ready": 0}) == "no_devices"

    def test_partial_devices(self):
        op = {"configured": True, "ready": True}
        assert _determine_overall_status(op, {"total": 3, "ready": 2}, {}, {"total": 0, "ready": 0}) == "partial"

    def test_partial_clusters(self):
        op = {"configured": True, "ready": True}
        assert _determine_overall_status(op, {"total": 2, "ready": 2}, {}, {"total": 2, "ready": 1}) == "partial"

    def test_healthy(self):
        op = {"configured": True, "ready": True}
        assert _determine_overall_status(op, {"total": 2, "ready": 2}, {}, {"total": 1, "ready": 1}) == "healthy"

    def test_healthy_no_clusters(self):
        op = {"configured": True, "ready": True}
        assert _determine_overall_status(op, {"total": 2, "ready": 2}, {}, {"total": 0, "ready": 0}) == "healthy"


# =========================================================================
# analyze_dpf_health — integration
# =========================================================================


class TestAnalyzeDpfHealthIntegration:

    def test_full_healthy_deployment(self):
        data = _full_data()
        result = analyze_dpf_health(data)
        assert result["status"] == "healthy"
        assert result["operator"]["ready"] is True
        assert result["devices"]["total"] == 2
        assert result["devices"]["ready"] == 2
        assert result["dpus"]["total"] == 2
        assert result["clusters"]["total"] == 1
        assert result["bfbs"]["total"] == 1
        assert result["services"]["total"] == 2
        assert result["dpusets"]["total"] == 1
        assert result["serviceChains"]["total"] == 1
        assert result["serviceInterfaces"]["total"] == 1

    def test_empty_cluster(self):
        data = {"resources": {}, "cluster_id": 1}
        result = analyze_dpf_health(data)
        assert result["status"] == "not_installed"
        assert result["operator"]["configured"] is False

    def test_degraded_operator(self):
        data = _full_data(dpfoperatorconfig=[_operator_config(ready=False)])
        result = analyze_dpf_health(data)
        assert result["status"] == "degraded"

    def test_partial_devices(self):
        data = _full_data(dpudevice=[_device("bf3-001", ready=True), _device("bf3-002", ready=False)])
        result = analyze_dpf_health(data)
        assert result["status"] == "partial"
        assert result["devices"]["ready"] == 1
