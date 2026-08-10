"""
Unit tests for services.scanner.prereqs — prerequisite analyzers.

All functions are pure (dict in → dict out) with no I/O, no mocking needed.
"""

import pytest

from services.scanner.constants import PrerequisiteStatus
from services.scanner.prereqs import (
    analyze_cert_manager,
    analyze_cluster_info,
    analyze_gateway_api,
    analyze_hugepages,
    analyze_multus,
    analyze_sriov,
    analyze_storage,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cluster(cloud_provider="on-prem", region=None, context=None, api_server=None):
    """Minimal cluster-like object."""
    class FakeCluster:
        pass

    c = FakeCluster()
    c.cloud_provider = cloud_provider
    c.region = region
    c.context = context
    c.api_server = api_server
    c.name = "test-cluster"
    return c


def _make_crd(name, group, kind, versions=None):
    return {
        "name": name,
        "group": group,
        "kind": kind,
        "versions": versions or ["v1"],
    }


def _make_pod(name, phase="Running", namespace="default", containers=None):
    return {
        "name": name,
        "namespace": namespace,
        "phase": phase,
        "labels": {},
        "containers": containers or [],
    }


def _make_ds(name, namespace="kube-system", desired=3, ready=3, images=None):
    return {
        "name": name,
        "namespace": namespace,
        "labels": {},
        "desired": desired,
        "ready": ready,
        "images": images or [],
    }


def _make_node(name, allocatable=None, capacity=None, instance_type=None, is_hp_node=False):
    return {
        "name": name,
        "ready": True,
        "labels": {},
        "instance_type": instance_type,
        "is_hp_node": is_hp_node,
        "allocatable": allocatable or {},
        "capacity": capacity or {},
    }


# =========================================================================
# analyze_cluster_info
# =========================================================================


class TestAnalyzeClusterInfo:
    def test_basic_cluster_info(self):
        cluster = _make_cluster("on-prem", "us-west-2")
        version = {"git_version": "v1.28.3", "major": "1", "minor": "28", "platform": "linux/amd64"}
        nodes = [
            {"name": "n1", "ready": True, "labels": {}, "is_hp_node": False},
            {"name": "n2", "ready": True, "labels": {}, "is_hp_node": True, "instance_type": "m5.xlarge", "zone": "us-west-2a"},
            {"name": "n3", "ready": False, "labels": {}, "is_hp_node": False},
        ]
        result = analyze_cluster_info(cluster, version, nodes, ["default", "kube-system"])
        assert result["version"] == "v1.28.3"
        assert result["distribution"] == "on-prem"
        assert result["node_count"] == 3
        assert result["nodes_ready"] == 2
        assert result["hp_nodes"] == 1
        assert result["namespaces"] == 2
        assert len(result["hp_node_details"]) == 1

    def test_eks_distribution_from_cloud_provider(self):
        result = analyze_cluster_info(_make_cluster("eks"), None, [], [])
        assert result["distribution"] == "EKS"

    def test_aks_distribution_from_cloud_provider(self):
        result = analyze_cluster_info(_make_cluster("azure"), None, [], [])
        assert result["distribution"] == "AKS"

    def test_gke_distribution_from_cloud_provider(self):
        result = analyze_cluster_info(_make_cluster("gcp"), None, [], [])
        assert result["distribution"] == "GKE"

    def test_distribution_detected_from_node_labels(self):
        nodes = [{"name": "n1", "ready": True, "labels": {"eks.amazonaws.com/nodegroup": "ng-1"}, "is_hp_node": False}]
        result = analyze_cluster_info(_make_cluster(""), None, nodes, [])
        assert result["distribution"] == "EKS"

    def test_openshift_detected_from_node_labels(self):
        nodes = [{"name": "n1", "ready": True, "labels": {"node.openshift.io/os_id": "rhcos"}, "is_hp_node": False}]
        result = analyze_cluster_info(_make_cluster(""), None, nodes, [])
        assert result["distribution"] == "OpenShift"

    def test_openshift_detected_from_context(self):
        result = analyze_cluster_info(
            _make_cluster("on-prem", context="my-openshift-admin", api_server="https://api.example:6443"),
            None,
            [],
            [],
        )
        assert result["distribution"] == "OpenShift"

    def test_openshift_detected_from_api_server(self):
        result = analyze_cluster_info(
            _make_cluster("on-prem", context="prod", api_server="https://api.openshift.example:6443"),
            None,
            [],
            [],
        )
        assert result["distribution"] == "OpenShift"

    def test_gke_detected_from_node_labels(self):
        nodes = [{"name": "n1", "ready": True, "labels": {"cloud.google.com/gke-nodepool": "default"}, "is_hp_node": False}]
        result = analyze_cluster_info(_make_cluster(""), None, nodes, [])
        assert result["distribution"] == "GKE"

    def test_aks_detected_from_node_labels(self):
        nodes = [{"name": "n1", "ready": True, "labels": {"kubernetes.azure.com/agentpool": "nodepool1"}, "is_hp_node": False}]
        result = analyze_cluster_info(_make_cluster(""), None, nodes, [])
        assert result["distribution"] == "AKS"

    def test_no_version_info(self):
        result = analyze_cluster_info(_make_cluster(), None, [], [])
        assert result["version"] is None
        assert result["major"] is None


# =========================================================================
# analyze_cert_manager
# =========================================================================


class TestAnalyzeCertManager:
    def test_fully_installed(self):
        crds = [
            _make_crd("certificates.cert-manager.io", "cert-manager.io", "Certificate"),
            _make_crd("clusterissuers.cert-manager.io", "cert-manager.io", "ClusterIssuer"),
            _make_crd("issuers.cert-manager.io", "cert-manager.io", "Issuer"),
        ]
        pods = [
            _make_pod("cert-manager-abc", "Running", containers=[{"image": "quay.io/jetstack/cert-manager-controller:v1.14.0", "name": "controller"}]),
            _make_pod("cert-manager-webhook-xyz", "Running"),
            _make_pod("cert-manager-cainjector-def", "Running"),
        ]
        result = analyze_cert_manager(crds, pods, [])
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["version"] == "1.14.0"
        assert result["crds_installed"] is True
        assert result["crd_count"] == 3
        assert result["pods"]["controller"] == 1
        assert result["pods"]["webhook"] == 1
        assert result["pods"]["cainjector"] == 1

    def test_crds_only_no_pods(self):
        crds = [_make_crd("certificates.cert-manager.io", "cert-manager.io", "Certificate")]
        result = analyze_cert_manager(crds, [], [])
        assert result["status"] == PrerequisiteStatus.PARTIAL

    def test_pods_only_no_crds(self):
        pods = [_make_pod("cert-manager-abc", "Pending")]
        result = analyze_cert_manager([], pods, [])
        assert result["status"] == PrerequisiteStatus.PARTIAL

    def test_completely_missing(self):
        result = analyze_cert_manager([], [], [])
        assert result["status"] == PrerequisiteStatus.MISSING

    def test_helm_release_detected(self):
        releases = [{"name": "cert-manager", "namespace": "cert-manager", "version": "1", "status": "deployed"}]
        result = analyze_cert_manager([], [], releases)
        assert result["helm_release"] is not None
        assert result["helm_release"]["name"] == "cert-manager"

    def test_missing_crds_reported(self):
        crds = [
            _make_crd("certificates.cert-manager.io", "cert-manager.io", "Certificate"),
        ]
        pods = [_make_pod("cert-manager-abc", "Running")]
        result = analyze_cert_manager(crds, pods, [])
        assert "ClusterIssuer" in result["missing_crds"]
        assert "Issuer" in result["missing_crds"]

    def test_version_extraction_from_image(self):
        pods = [_make_pod("cert-manager-abc", "Running", containers=[
            {"image": "quay.io/jetstack/cert-manager-controller:v1.12.5", "name": "cm"}
        ])]
        result = analyze_cert_manager([], pods, [])
        assert result["version"] == "1.12.5"


# =========================================================================
# analyze_multus
# =========================================================================


class TestAnalyzeMultus:
    def test_fully_installed(self):
        crds = [_make_crd("network-attachment-definitions.k8s.cni.cncf.io", "k8s.cni.cncf.io", "NetworkAttachmentDefinition")]
        crd_names = {"network-attachment-definitions.k8s.cni.cncf.io"}
        pods = [_make_pod("kube-multus-ds-abc", "Running", "kube-system")]
        ds = [_make_ds("kube-multus-ds")]
        result = analyze_multus(crds, crd_names, pods, ds)
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["nad_crd_installed"] is True
        assert result["running_pods"] == 1
        assert result["daemonset"]["name"] == "kube-multus-ds"

    def test_crd_only_partial(self):
        crd_names = {"network-attachment-definitions.k8s.cni.cncf.io"}
        result = analyze_multus([], crd_names, [], [])
        assert result["status"] == PrerequisiteStatus.PARTIAL

    def test_completely_missing(self):
        result = analyze_multus([], set(), [], [])
        assert result["status"] == PrerequisiteStatus.MISSING

    def test_detected_with_daemonset_no_pods(self):
        crd_names = {"network-attachment-definitions.k8s.cni.cncf.io"}
        ds = [_make_ds("kube-multus-ds")]
        result = analyze_multus([], crd_names, [], ds)
        assert result["status"] == PrerequisiteStatus.DETECTED


# =========================================================================
# analyze_sriov
# =========================================================================


class TestAnalyzeSriov:
    def test_fully_detected(self):
        ds = [_make_ds("sriov-device-plugin", images=["k8s.gcr.io/sriov-dp:v3.5"])]
        nodes = [_make_node("n1", capacity={"sriov_resources": {"intel.com/sriov_vf": "8"}})]
        result = analyze_sriov(nodes, ds, [])
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["total_vfs"] == 8
        assert result["nodes_with_vfs"] == 1

    def test_daemonset_only_partial(self):
        ds = [_make_ds("sriov-device-plugin")]
        nodes = [_make_node("n1")]
        result = analyze_sriov(nodes, ds, [])
        assert result["status"] == PrerequisiteStatus.PARTIAL

    def test_vfs_only_partial(self):
        nodes = [_make_node("n1", capacity={"sriov_resources": {"intel.com/sriov_vf": "4"}})]
        result = analyze_sriov(nodes, [], [])
        assert result["status"] == PrerequisiteStatus.PARTIAL

    def test_completely_missing(self):
        result = analyze_sriov([_make_node("n1")], [], [])
        assert result["status"] == PrerequisiteStatus.MISSING

    def test_device_plugin_matches_name_pattern(self):
        ds = [_make_ds("my-device-plugin-ds")]
        nodes = [_make_node("n1", capacity={"sriov_resources": {"vf": "2"}})]
        result = analyze_sriov(nodes, ds, [])
        assert result["status"] == PrerequisiteStatus.DETECTED

    def test_multiple_nodes_with_vfs(self):
        ds = [_make_ds("sriov-dp")]
        nodes = [
            _make_node("n1", capacity={"sriov_resources": {"vf": "4"}}),
            _make_node("n2", capacity={"sriov_resources": {"vf": "6"}}),
        ]
        result = analyze_sriov(nodes, ds, [])
        assert result["total_vfs"] == 10
        assert result["nodes_with_vfs"] == 2


# =========================================================================
# analyze_hugepages
# =========================================================================


class TestAnalyzeHugepages:
    def test_hugepages_2mi_detected(self):
        nodes = [_make_node("n1", allocatable={"hugepages_2mi": "4Gi"})]
        result = analyze_hugepages(nodes)
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["nodes_with_hugepages"] == 1

    def test_hugepages_1gi_detected(self):
        nodes = [_make_node("n1", allocatable={"hugepages_1gi": "2Gi"})]
        result = analyze_hugepages(nodes)
        assert result["status"] == PrerequisiteStatus.DETECTED

    def test_hugepages_both_sizes(self):
        nodes = [
            _make_node("n1", allocatable={"hugepages_2mi": "4Gi", "hugepages_1gi": "2Gi"}),
            _make_node("n2", allocatable={"hugepages_2mi": "8Gi"}),
        ]
        result = analyze_hugepages(nodes)
        assert result["nodes_with_hugepages"] == 2

    def test_hugepages_zero_value_is_missing(self):
        nodes = [_make_node("n1", allocatable={"hugepages_2mi": "0", "hugepages_1gi": "0"})]
        result = analyze_hugepages(nodes)
        assert result["status"] == PrerequisiteStatus.MISSING

    def test_hugepages_missing(self):
        nodes = [_make_node("n1")]
        result = analyze_hugepages(nodes)
        assert result["status"] == PrerequisiteStatus.MISSING

    def test_empty_nodes_is_missing(self):
        result = analyze_hugepages([])
        assert result["status"] == PrerequisiteStatus.MISSING

    def test_hp_node_flag_included(self):
        nodes = [_make_node("n1", allocatable={"hugepages_2mi": "4Gi"}, is_hp_node=True)]
        result = analyze_hugepages(nodes)
        assert result["node_details"][0]["is_hp_node"] is True


# =========================================================================
# analyze_storage
# =========================================================================


class TestAnalyzeStorage:
    def test_storage_with_default_class(self):
        classes = [
            {"name": "gp3", "provisioner": "ebs.csi.aws.com", "is_default": True, "reclaim_policy": "Delete", "volume_binding_mode": "WaitForFirstConsumer"},
            {"name": "gp2", "provisioner": "ebs.csi.aws.com", "is_default": False, "reclaim_policy": "Delete", "volume_binding_mode": "WaitForFirstConsumer"},
        ]
        result = analyze_storage(classes)
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["count"] == 2
        assert result["default"] == "gp3"
        assert result["has_gp3"] is True
        assert result["has_gp2"] is True

    def test_storage_no_default(self):
        classes = [
            {"name": "standard", "provisioner": "rancher.io/local-path", "is_default": False, "reclaim_policy": "Delete", "volume_binding_mode": "Immediate"},
        ]
        result = analyze_storage(classes)
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["default"] is None

    def test_no_storage_classes(self):
        result = analyze_storage([])
        assert result["status"] == PrerequisiteStatus.MISSING
        assert result["count"] == 0

    def test_gp3_detected_by_provisioner(self):
        classes = [{"name": "fast", "provisioner": "ebs.gp3.driver", "is_default": False, "reclaim_policy": "Delete", "volume_binding_mode": "Immediate"}]
        result = analyze_storage(classes)
        assert result["has_gp3"] is True


# =========================================================================
# analyze_gateway_api
# =========================================================================


class TestAnalyzeGatewayApi:
    def test_fully_installed(self):
        crds = [
            _make_crd("gatewayclasses.gateway.networking.k8s.io", "gateway.networking.k8s.io", "GatewayClass", ["v1", "v1beta1"]),
            _make_crd("gateways.gateway.networking.k8s.io", "gateway.networking.k8s.io", "Gateway", ["v1"]),
            _make_crd("httproutes.gateway.networking.k8s.io", "gateway.networking.k8s.io", "HTTPRoute", ["v1"]),
        ]
        crd_names = {c["name"] for c in crds}
        result = analyze_gateway_api(crds, crd_names, [{"name": "gw1"}], [{"name": "gc1"}])
        assert result["status"] == PrerequisiteStatus.DETECTED
        assert result["crds_installed"] == 3
        assert result["gateways"] == 1
        assert result["gatewayclasses"] == 1
        assert "v1" in result["api_versions"]

    def test_partial_crds(self):
        crds = [
            _make_crd("gateways.gateway.networking.k8s.io", "gateway.networking.k8s.io", "Gateway"),
        ]
        crd_names = {c["name"] for c in crds}
        result = analyze_gateway_api(crds, crd_names, [], [])
        assert result["status"] == PrerequisiteStatus.PARTIAL
        assert len(result["standard_crds_missing"]) == 2

    def test_completely_missing(self):
        result = analyze_gateway_api([], set(), [], [])
        assert result["status"] == PrerequisiteStatus.MISSING
        assert len(result["standard_crds_missing"]) == 3

    def test_f5_gateway_crds_included(self):
        crds = [
            _make_crd("gatewayclasses.gateway.networking.k8s.io", "gateway.networking.k8s.io", "GatewayClass"),
            _make_crd("gateways.gateway.networking.k8s.io", "gateway.networking.k8s.io", "Gateway"),
            _make_crd("httproutes.gateway.networking.k8s.io", "gateway.networking.k8s.io", "HTTPRoute"),
            _make_crd("tcproutes.gateway.k8s.f5net.com", "gateway.k8s.f5net.com", "TCPRoute"),
        ]
        crd_names = {c["name"] for c in crds}
        result = analyze_gateway_api(crds, crd_names, [], [])
        assert result["crds_installed"] == 4  # includes F5 extension
