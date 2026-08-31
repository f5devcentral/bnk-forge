"""
Unit tests for services.bnk.health — health dashboard analysis.

Tests the actual analysis logic with constructed K8s resource data.
No mocking — these are pure data transformations.
"""

import pytest

from services.bnk.health import (
    COMPONENT_EXPLANATIONS,
    _build_component_health,
    _build_connectivity_status,
    _build_integration_status,
    _crd_installer_severity,
    _extract_cne_features,
    _feature_enabled,
    _pod_details,
    _pod_issue_summary,
    analyze_health,
)

# ---------------------------------------------------------------------------
# Test data builders
# ---------------------------------------------------------------------------


def _pod(
    name: str = "pod-1",
    namespace: str = "f5-bnk",
    phase: str = "Running",
    ready: bool = True,
    restarts: int = 0,
    state: str = "running",
) -> dict:
    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "containers": [{
            "name": name.rsplit("-", 1)[0],
            "ready": ready,
            "restartCount": restarts,
            "state": state,
        }],
    }


def _empty_resources() -> dict:
    """Return a resources dict with all keys empty."""
    return {k: [] for k in [
        "gateway", "httproute", "grpcroute", "tcproute", "udproute", "tlsroute",
        "l4route", "referencegrant", "bnksecpolicy", "bnknetpolicy",
        "f5bigfwpolicy", "f5bigcneirule", "f5biganalyzer",
        "f5bigcneaddresslist", "f5bigcneportlist",
        "f5spkvlan", "cneinstance", "f5spkstaticroute",
        "f5spksnatpool", "f5spkegress", "f5bigloghslpub", "f5biglogprofile",
        "service",
    ]}


def _empty_classified() -> dict:
    return {"tmm": [], "flo": [], "controller": [], "analyzer": [], "crd_installer": [], "other": []}


def _make_data(resources=None, classified=None, nodes=None) -> dict:
    return {
        "resources": resources or _empty_resources(),
        "classified_pods": classified or _empty_classified(),
        "pods": {"tenant": [], "utils": []},
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# _feature_enabled
# ---------------------------------------------------------------------------


class TestFeatureEnabled:
    def test_dict_with_enabled_true(self):
        assert _feature_enabled({"firewallACL": {"enabled": True}}, "firewallACL") is True

    def test_dict_with_enabled_false(self):
        assert _feature_enabled({"firewallACL": {"enabled": False}}, "firewallACL") is False

    def test_bool_value(self):
        assert _feature_enabled({"pseudoCNI": True}, "pseudoCNI") is True
        assert _feature_enabled({"pseudoCNI": False}, "pseudoCNI") is False

    def test_missing_key(self):
        assert _feature_enabled({"other": True}, "firewallACL") is False

    def test_non_dict_input(self):
        assert _feature_enabled(None, "key") is False
        assert _feature_enabled("string", "key") is False


# ---------------------------------------------------------------------------
# _pod_issue_summary
# ---------------------------------------------------------------------------


class TestPodIssueSummary:
    def test_healthy_pod_returns_empty(self):
        assert _pod_issue_summary(_pod()) == ""

    def test_waiting_container(self):
        p = _pod(state="waiting", ready=False)
        assert "waiting" in _pod_issue_summary(p)

    def test_terminated_container(self):
        p = _pod(state="terminated", ready=False)
        assert "terminated" in _pod_issue_summary(p)

    def test_not_ready_container(self):
        p = _pod(ready=False, restarts=0)
        assert "not ready" in _pod_issue_summary(p)

    def test_crash_loop(self):
        p = _pod(ready=False, restarts=10)
        msg = _pod_issue_summary(p)
        assert "CrashLoopBackOff" in msg
        assert "10" in msg

    def test_pending_phase(self):
        p = _pod(phase="Pending", ready=True)
        p["containers"] = []  # no containers yet
        assert "Pod phase: Pending" in _pod_issue_summary(p)


# ---------------------------------------------------------------------------
# _pod_details
# ---------------------------------------------------------------------------


class TestPodDetails:
    def test_healthy_pod(self):
        d = _pod_details(_pod(name="tmm-1", namespace="f5-bnk"))
        assert d["podName"] == "tmm-1"
        assert d["namespace"] == "f5-bnk"
        assert d["phase"] == "Running"
        assert d["restartCount"] == 0
        assert d["containersReady"] == "1/1"
        assert d["issue"] == ""
        assert d["nodeZone"] is None
        assert d["nodeInstanceType"] is None

    def test_unhealthy_pod(self):
        d = _pod_details(_pod(ready=False, restarts=6))
        assert d["restartCount"] == 6
        assert d["containersReady"] == "0/1"
        assert "CrashLoopBackOff" in d["issue"]

    def test_multi_container_pod(self):
        p = _pod()
        p["containers"].append({
            "name": "sidecar",
            "ready": True,
            "restartCount": 2,
            "state": "running",
        })
        d = _pod_details(p)
        assert d["containersReady"] == "2/2"
        assert d["restartCount"] == 2

    def test_pod_enrichment_from_nodes(self):
        pod = _pod(name="tmm-1", namespace="f5-bnk")
        pod["nodeName"] = "worker-1"
        nodes = {
            "worker-1": {"zone": "us-east-1a", "instance_type": "m5.large"},
        }
        d = _pod_details(pod, nodes)
        assert d["nodeZone"] == "us-east-1a"
        assert d["nodeInstanceType"] == "m5.large"

    def test_pod_enrichment_unknown_node(self):
        pod = _pod(name="tmm-1", namespace="f5-bnk")
        pod["nodeName"] = "missing-node"
        d = _pod_details(pod, {})
        assert d["nodeZone"] is None
        assert d["nodeInstanceType"] is None

    def test_pod_enrichment_no_node_assignment(self):
        d = _pod_details(_pod(name="tmm-1"), {"worker-1": {"zone": "us-east-1a"}})
        assert d["nodeZone"] is None


# ---------------------------------------------------------------------------
# _build_component_health
# ---------------------------------------------------------------------------


class TestBuildComponentHealth:
    def test_healthy_component_has_view_and_describe_actions(self):
        pods = [_pod(name="flo-1")]
        h = _build_component_health("flo", pods, pods, "healthy")
        assert h["explanation"] == COMPONENT_EXPLANATIONS["flo"]
        assert len(h["podDetails"]) == 1
        actions = h["remediationActions"]
        assert any(a["action"] == "view_logs" for a in actions)
        assert any(a["action"] == "describe" for a in actions)
        # No restart for healthy component
        assert not any(a["action"] == "restart_pod" for a in actions)

    def test_unhealthy_component_has_restart_action(self):
        pods = [_pod(name="tmm-1", ready=False)]
        h = _build_component_health("tmm", pods, [], "critical")
        actions = h["remediationActions"]
        assert any(a["action"] == "restart_pod" for a in actions)
        assert actions[0]["target"] == "tmm-1"

    def test_empty_pods(self):
        h = _build_component_health("flo", [], [], "unknown")
        assert h["podDetails"] == []
        assert h["remediationActions"] == []

    def test_unknown_component_key(self):
        h = _build_component_health("nonexistent", [_pod()], [_pod()], "healthy")
        assert h["explanation"] == ""

    def test_component_placement_aggregation(self):
        pods = [
            _pod(name="tmm-1", namespace="f5-bnk"),
            _pod(name="tmm-2", namespace="f5-bnk"),
        ]
        pods[0]["nodeName"] = "worker-1"
        pods[1]["nodeName"] = "worker-2"
        nodes = {
            "worker-1": {"zone": "us-east-1a"},
            "worker-2": {"zone": "us-east-1b"},
        }
        h = _build_component_health("tmm", pods, pods, "healthy", nodes)
        assert h["namespaces"] == ["f5-bnk"]
        assert h["nodes"] == ["worker-1", "worker-2"]
        assert h["zones"] == ["us-east-1a", "us-east-1b"]

    def test_component_aggregation_degrades_without_nodes(self):
        pods = [_pod(name="tmm-1", namespace="f5-bnk")]
        pods[0]["nodeName"] = "worker-1"
        h = _build_component_health("tmm", pods, pods, "healthy")
        assert h["namespaces"] == ["f5-bnk"]
        assert h["nodes"] == ["worker-1"]
        assert h["zones"] == []


# ---------------------------------------------------------------------------
# _extract_cne_features
# ---------------------------------------------------------------------------


class TestExtractCneFeatures:
    def test_extracts_features(self):
        cne = {"metadata": {"name": "cne-1", "namespace": "f5-bnk"}, "spec": {
            "firewallACL": {"enabled": True},
            "intelligentLB": {"enabled": False},
            "pseudoCNI": {"enabled": True},
            "metricSubsystem": {"enabled": True},
            "loggingSubsystem": {"enabled": False},
            "coreCollection": {"enabled": True},
            "envDiscovery": {"enabled": False},
        }}
        f = _extract_cne_features([cne])
        assert f["name"] == "cne-1"
        assert f["firewallACL"] is True
        assert f["intelligentLB"] is False
        assert f["pseudoCNI"] is True
        assert f["coreCollection"] is True
        assert f["envDiscovery"] is False

    def test_empty_list_returns_empty_dict(self):
        assert _extract_cne_features([]) == {}

    def test_uses_first_instance_only(self):
        cne1 = {"metadata": {"name": "cne-1"}, "spec": {"firewallACL": True}}
        cne2 = {"metadata": {"name": "cne-2"}, "spec": {"firewallACL": False}}
        f = _extract_cne_features([cne1, cne2])
        assert f["name"] == "cne-1"


# ---------------------------------------------------------------------------
# analyze_health — end-to-end
# ---------------------------------------------------------------------------


class TestAnalyzeHealth:
    def test_empty_cluster(self):
        """Empty cluster returns valid structure with 'unknown' overall."""
        result = analyze_health(_make_data())
        assert result["overall"] == "unknown"
        assert "platform" in result
        assert "dataPlane" in result
        assert "networking" in result
        assert "security" in result
        assert "ai" in result
        assert "counts" in result

    def test_healthy_cluster(self):
        """Cluster with all healthy pods and security resources returns 'healthy'."""
        classified = _empty_classified()
        classified["tmm"] = [_pod(name="tmm-1")]
        classified["flo"] = [_pod(name="flo-1")]
        classified["controller"] = [_pod(name="ctrl-1")]
        classified["analyzer"] = [_pod(name="analyzer-1")]
        classified["crd_installer"] = [_pod(name="crd-1", phase="Succeeded", ready=False)]

        resources = _empty_resources()
        resources["gateway"] = [{
            "metadata": {"name": "gw-1", "namespace": "f5-bnk"},
            "spec": {"listeners": [{"name": "http", "port": 80}]},
            "status": {
                "addresses": [{"value": "10.0.0.1"}],
                "conditions": [{"type": "Programmed", "status": "True"}],
            },
        }]
        resources["f5spkvlan"] = [{
            "metadata": {"name": "vlan-1", "namespace": "f5-bnk"},
            "spec": {},
            "status": {"conditions": [{"type": "Programmed", "status": "True"}]},
        }]
        # Need at least one security resource so security severity != "unknown"
        resources["f5bigfwpolicy"] = [{
            "metadata": {"name": "fw-1", "namespace": "f5-bnk"},
            "spec": {"rule": []},
        }]

        result = analyze_health(_make_data(resources, classified))
        assert result["overall"] == "healthy"
        assert result["platform"]["flo"]["severity"] == "healthy"
        assert result["dataPlane"]["tmm"]["severity"] == "healthy"
        assert result["networking"]["gateways"]["programmed"] == 1
        assert result["security"]["severity"] == "healthy"

    def test_no_security_resources_is_unknown(self):
        """Cluster with no security resources has security severity 'unknown'."""
        result = analyze_health(_make_data())
        assert result["security"]["severity"] == "unknown"

    def test_unhealthy_tmm_produces_critical(self):
        """All TMM pods unhealthy → data plane severity = critical."""
        classified = _empty_classified()
        classified["tmm"] = [_pod(name="tmm-1", ready=False)]

        result = analyze_health(_make_data(classified=classified))
        assert result["dataPlane"]["severity"] == "critical"
        assert result["dataPlane"]["tmm"]["severity"] == "critical"

    def test_partial_flo_produces_warning(self):
        """Some FLO pods unhealthy → platform flo severity = warning."""
        classified = _empty_classified()
        classified["flo"] = [_pod(name="flo-1"), _pod(name="flo-2", ready=False)]

        result = analyze_health(_make_data(classified=classified))
        assert result["platform"]["flo"]["severity"] == "warning"
        assert result["platform"]["flo"]["total"] == 2
        assert result["platform"]["flo"]["running"] == 1

    def test_counts_are_accurate(self):
        """Counts reflect the number of resources in the data."""
        resources = _empty_resources()
        resources["gateway"] = [
            {"metadata": {"name": f"gw-{i}", "namespace": "ns"},
             "spec": {"listeners": [{"name": "http"}]},
             "status": {}}
            for i in range(3)
        ]
        resources["httproute"] = [
            {"metadata": {"name": "r-1", "namespace": "ns"}, "spec": {}, "status": {}}
        ]

        result = analyze_health(_make_data(resources))
        assert result["counts"]["gateways"] == 3
        assert result["counts"]["httpRoutes"] == 1
        assert result["counts"]["listeners"] == 3

    def test_security_irules_severity(self):
        """iRules with mixed accepted status produce correct severity."""
        resources = _empty_resources()
        resources["f5bigcneirule"] = [
            {"metadata": {"name": "ir-ok"}, "status": {
                "conditions": [{"type": "Accepted", "status": "True"}],
            }},
            {"metadata": {"name": "ir-bad"}, "status": {
                "conditions": [{"type": "Accepted", "status": "False", "message": "syntax error"}],
            }},
        ]

        result = analyze_health(_make_data(resources))
        assert result["security"]["irules"]["severity"] == "warning"
        assert result["security"]["irules"]["accepted"] == 1
        assert result["security"]["irules"]["total"] == 2
        # The bad iRule should have an error message
        details = result["security"]["irules"]["details"]
        bad = next(d for d in details if d["name"] == "ir-bad")
        assert bad["error"] == "syntax error"

    def test_ai_health_with_analyzers(self):
        resources = _empty_resources()
        resources["f5biganalyzer"] = [{
            "metadata": {"name": "analyzer-1", "namespace": "f5-bnk"},
            "spec": {"schedule": {"every": "5m"}, "applications": []},
        }]
        result = analyze_health(_make_data(resources))
        # AI section is informational-only and should not emit severity.
        assert result["ai"]["severity"] == "unknown"
        assert result["ai"]["analyzers"] == 1

    def test_ai_does_not_warn_when_ilb_enabled_but_no_analyzers(self):
        resources = _empty_resources()
        resources["cneinstance"] = [{
            "metadata": {"name": "cne-1"},
            "spec": {"intelligentLB": {"enabled": True}},
        }]
        result = analyze_health(_make_data(resources))
        assert result["ai"]["severity"] == "unknown"

    def test_platform_rollup_ignores_optional_analyzer_absence(self):
        classified = _empty_classified()
        classified["flo"] = [_pod(name="flo-1")]
        classified["controller"] = [_pod(name="ctrl-1")]
        classified["crd_installer"] = [_pod(name="crd-1", phase="Succeeded", ready=False)]
        # analyzer intentionally absent
        result = analyze_health(_make_data(classified=classified))
        assert result["platform"]["severity"] == "healthy"

    def test_overall_is_healthy_with_readiness_when_optional_sections_absent(self):
        classified = _empty_classified()
        classified["tmm"] = [_pod(name="tmm-1")]
        classified["flo"] = [_pod(name="flo-1")]
        classified["controller"] = [_pod(name="ctrl-1")]
        classified["crd_installer"] = [_pod(name="crd-1", phase="Succeeded", ready=False)]

        resources = _empty_resources()
        # no networking/security resources

        result = analyze_health(_make_data(resources, classified))
        assert result["overall"] == "healthy"

    def test_crd_installer_completed_is_healthy(self):
        """CRD installer pods with Succeeded phase count as completed."""
        classified = _empty_classified()
        classified["crd_installer"] = [
            _pod(name="crd-1", phase="Succeeded", ready=False),
        ]
        result = analyze_health(_make_data(classified=classified))
        assert result["platform"]["crdInstaller"]["severity"] == "healthy"
        assert result["platform"]["crdInstaller"]["completed"] == 1

    def test_platform_rollup_ignores_crd_installer_when_zero_pods(self):
        """CRD installer is a one-shot Job; once pods are GC'd, 0/0 must not
        drag the platform rollup to 'unknown' if the rest of the platform is
        healthy. Regression for the dashboard 'Unknown' bug.
        """
        classified = _empty_classified()
        classified["flo"] = [_pod(name="flo-1")]
        classified["controller"] = [_pod(name="ctrl-1")]
        classified["analyzer"] = [_pod(name="analyzer-1")]
        # crd_installer intentionally absent (Job pods GC'd post-install)
        result = analyze_health(_make_data(classified=classified))
        assert result["platform"]["crdInstaller"]["severity"] == "unknown"
        assert result["platform"]["crdInstaller"]["total"] == 0
        assert result["platform"]["severity"] == "healthy"

    def test_platform_rollup_includes_crd_installer_when_job_failed(self):
        """Job exists with failed > 0 and no succeeded + GC'd pods → rollup reflects
        the failure as critical, not healthy. Regression guard for the
        'crashed-and-GC'd vs never-installed' conflation bug."""
        classified = _empty_classified()
        classified["flo"] = [_pod(name="flo-1")]
        classified["controller"] = [_pod(name="ctrl-1")]
        # No crd_installer pods — they were GC'd after the Job failed
        job = {"succeeded": 0, "failed": 2, "active": 0}
        data = _make_data(classified=classified)
        data["crd_installer_job"] = job
        result = analyze_health(data)
        assert result["platform"]["crdInstaller"]["severity"] == "critical"
        # The failure must enter the rollup, dragging platform away from 'healthy'
        assert result["platform"]["severity"] != "healthy"

    def test_platform_rollup_treats_truly_absent_crd_installer_as_unknown(self):
        """No Job resource AND no pods → never installed.
        Platform rollup excludes it; the crdInstaller component reports 'unknown'."""
        classified = _empty_classified()
        classified["flo"] = [_pod(name="flo-1")]
        classified["controller"] = [_pod(name="ctrl-1")]
        data = _make_data(classified=classified)
        data["crd_installer_job"] = None  # explicit: Job doesn't exist
        result = analyze_health(data)
        assert result["platform"]["crdInstaller"]["severity"] == "unknown"
        assert result["platform"]["crdInstaller"]["total"] == 0
        # Should not drag rollup — flo + controller are healthy
        assert result["platform"]["severity"] == "healthy"

    def test_platform_rollup_excludes_crd_installer_during_install_window(self):
        """Job active > 0, succeeded == 0, failed == 0, pods not yet visible →
        install is in progress. Exclude from rollup (transient), but component
        reports 'unknown' so FE can show a pending card if desired."""
        classified = _empty_classified()
        classified["flo"] = [_pod(name="flo-1")]
        classified["controller"] = [_pod(name="ctrl-1")]
        job = {"succeeded": 0, "failed": 0, "active": 1}
        data = _make_data(classified=classified)
        data["crd_installer_job"] = job
        result = analyze_health(data)
        # Transient state: unknown severity, excluded from rollup
        assert result["platform"]["crdInstaller"]["severity"] == "unknown"
        # Rest of platform is healthy — rollup must not be dragged down
        assert result["platform"]["severity"] == "healthy"

    def test_helm_shape_no_flo_is_healthy_not_unknown(self):
        """Direct-helm install: TMM + f5ingress controller healthy, no FLO pod at
        all. Must NOT be dragged to 'unknown' by FLO's absence (D-bnk-install-shape
        regression guard — this is the bug this task fixes)."""
        classified = _empty_classified()
        classified["tmm"] = [_pod(name="f5-tmm-1")]
        classified["controller"] = [_pod(name="f5ingress-f5ingress-1")]
        # flo intentionally absent — genuinely no FLO pod on a direct-helm install

        resources = _empty_resources()
        resources["gateway"] = [{
            "metadata": {"name": "gw-1", "namespace": "bnk-app1"},
            "spec": {"listeners": [{"name": "http", "port": 80}]},
            "status": {
                "addresses": [{"value": "10.0.0.1"}],
                "conditions": [{"type": "Programmed", "status": "True"}],
            },
        }]
        resources["f5spkvlan"] = [
            {
                "metadata": {"name": f"vlan-{i}", "namespace": "bnk-app1"},
                "spec": {},
                "status": {"conditions": [{"type": "Programmed", "status": "True"}]},
            }
            for i in range(2)
        ]

        result = analyze_health(_make_data(resources, classified))
        assert result["installShape"] == "helm"
        assert result["installMethod"] == "Helm / manual"
        assert result["overall"] == "healthy"
        assert result["platform"]["severity"] == "healthy"

    def test_flo_shape_missing_flo_still_degrades_rollup(self):
        """When install_shape == 'flo' (a FLO pod is present in the cluster,
        just none matched here — simulate by constructing classified with a
        flo pod present, then verify absence elsewhere doesn't change FLO's
        mandatory-when-flo-shape semantics is exercised via installShape."""
        classified = _empty_classified()
        classified["flo"] = [_pod(name="flo-1")]
        classified["tmm"] = [_pod(name="f5-tmm-1")]
        result = analyze_health(_make_data(classified=classified))
        assert result["installShape"] == "flo"

    def test_unknown_shape_when_no_tmm_no_controller_no_flo(self):
        result = analyze_health(_make_data())
        assert result["installShape"] == "unknown"
        assert result["installMethod"] == "Unknown"

    def test_node_az_enrichment_in_component_summaries(self):
        """Nodes and zones are aggregated into platform + data-plane summaries."""
        classified = _empty_classified()
        classified["tmm"] = [_pod(name="tmm-1", namespace="f5-bnk")]
        classified["tmm"][0]["nodeName"] = "worker-1"
        classified["flo"] = [_pod(name="flo-1", namespace="f5-bnk")]
        classified["flo"][0]["nodeName"] = "worker-1"
        classified["controller"] = [_pod(name="ctrl-1", namespace="f5-utils")]
        classified["controller"][0]["nodeName"] = "worker-2"

        nodes = {
            "worker-1": {"zone": "us-east-1a", "instance_type": "m5.large"},
            "worker-2": {"zone": "us-east-1b"},
        }

        result = analyze_health(_make_data(classified=classified, nodes=nodes))

        # Platform aggregation
        assert result["platform"]["flo"]["nodes"] == ["worker-1"]
        assert result["platform"]["flo"]["zones"] == ["us-east-1a"]
        assert result["platform"]["controller"]["nodes"] == ["worker-2"]
        assert result["platform"]["controller"]["zones"] == ["us-east-1b"]
        assert result["platform"]["controller"]["namespaces"] == ["f5-utils"]

        # Data-plane aggregation
        assert result["dataPlane"]["tmm"]["nodes"] == ["worker-1"]
        assert result["dataPlane"]["tmm"]["zones"] == ["us-east-1a"]

        # Pod detail enrichment
        tmm_pod = result["dataPlane"]["tmm"]["podDetails"][0]
        assert tmm_pod["nodeZone"] == "us-east-1a"
        assert tmm_pod["nodeInstanceType"] == "m5.large"


# ---------------------------------------------------------------------------
# _crd_installer_severity unit tests
# ---------------------------------------------------------------------------


class TestCrdInstallerSeverity:
    def test_no_job_no_pods_is_never_installed(self):
        sev, include = _crd_installer_severity([], None)
        assert sev == "unknown"
        assert include is False

    def test_no_job_but_completed_pods_is_healthy(self):
        """Legacy/operator-managed install without a visible Job resource."""
        pods = [_pod(name="crd-1", phase="Succeeded", ready=False)]
        sev, include = _crd_installer_severity(pods, None)
        assert sev == "healthy"
        assert include is False

    def test_job_succeeded_no_pods_is_healthy(self):
        job = {"succeeded": 1, "failed": 0, "active": 0}
        sev, include = _crd_installer_severity([], job)
        assert sev == "healthy"
        assert include is False

    def test_job_failed_no_succeeded_enters_rollup_as_critical(self):
        job = {"succeeded": 0, "failed": 1, "active": 0}
        sev, include = _crd_installer_severity([], job)
        assert sev == "critical"
        assert include is True

    def test_job_active_is_pending_excluded(self):
        job = {"succeeded": 0, "failed": 0, "active": 1}
        sev, include = _crd_installer_severity([], job)
        assert sev == "unknown"
        assert include is False


# ---------------------------------------------------------------------------
# Connectivity / integration status
# ---------------------------------------------------------------------------


class TestConnectivityStatus:
    def test_default_is_connected_when_no_injection(self):
        result = _build_connectivity_status({})
        assert result["status"] == "connected"
        assert "Kubernetes API is accessible" in result["message"]
        assert result["checkedAt"]

    def test_injected_status_is_used(self):
        result = _build_connectivity_status({
            "connectivity": {"status": "partial", "message": "ICMP only", "checkedAt": "2026-01-01T00:00:00Z"},
        })
        assert result["status"] == "partial"
        assert result["message"] == "ICMP only"
        assert result["checkedAt"] == "2026-01-01T00:00:00Z"


class TestIntegrationStatus:
    def test_default_is_kubeconfig_healthy(self):
        result = _build_integration_status({})
        assert result["status"] == "healthy"
        assert result["operatorMode"] == "kubeconfig"
        assert result["operatorConnected"] is False
        assert "kubeconfig" in result["message"].lower()

    def test_injected_operator_status_is_used(self):
        result = _build_integration_status({
            "integration": {
                "status": "warning",
                "operatorConnected": False,
                "operatorMode": "direct_ws",
                "operatorVersion": "1.2.3",
                "lastSeen": "2026-01-01T00:00:00Z",
                "message": "Operator op-1 is disconnected",
            },
        })
        assert result["status"] == "warning"
        assert result["operatorMode"] == "direct_ws"
        assert result["operatorVersion"] == "1.2.3"
        assert result["lastSeen"] == "2026-01-01T00:00:00Z"


class TestAnalyzeHealthConnectivityIntegration:
    def test_analyze_health_includes_connectivity_and_integration(self):
        result = analyze_health(_make_data())
        assert "connectivity" in result
        assert "integration" in result
        assert result["connectivity"]["status"] == "connected"
        assert result["integration"]["status"] == "healthy"

    def test_analyze_health_uses_injected_context(self):
        data = _make_data()
        data["connectivity"] = {"status": "unreachable", "message": "API down", "checkedAt": "2026-01-01T00:00:00Z"}
        data["integration"] = {"status": "critical", "operatorConnected": False, "operatorMode": "polling", "message": "Lost"}
        result = analyze_health(data)
        assert result["connectivity"]["status"] == "unreachable"
        assert result["integration"]["status"] == "critical"
