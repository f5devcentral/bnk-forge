"""
BU-015: Unit tests for schemas.k8s module.

Tests all K8s response schemas — clusters, namespaces, resources,
scan, resource types, and tunnels. Real Pydantic validation, no mocks.
"""

import pytest

from schemas.k8s import (
    ClusterConnectionTestResponse,
    ClusterDetailResponse,
    ClusterListResponse,
    ClusterOperationResponse,
    ClusterScanResponse,
    ClusterSummary,
    CreateMigrationRequest,
    K8sResourceItem,
    K8sResourceListResponse,
    K8sResourceOperationResponse,
    NamespaceInfo,
    NamespaceListResponse,
    NodeCountResponse,
    ResourceTypeInfo,
    ResourceTypeListResponse,
    TunnelListResponse,
    TunnelStatus,
)

# ── Cluster schemas ──────────────────────────────────────────────────


class TestClusterSummary:
    def test_minimal(self):
        c = ClusterSummary(id=1, name="dev-cluster")
        assert c.id == 1
        assert c.name == "dev-cluster"
        assert c.status == "active"  # default
        assert c.default_namespace == "default"
        assert c.ssh_tunnel_enabled is False

    def test_full(self):
        c = ClusterSummary(
            id=2,
            name="prod-cluster",
            context="arn:aws:eks:us-west-2:123:cluster/prod",
            api_server="https://eks.us-west-2.amazonaws.com",
            version="1.28",
            status="active",
            cloud_provider="aws",
            region="us-west-2",
            default_namespace="production",
            project_id=5,
            ssh_tunnel_enabled=True,
            node_count=10,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-02-01T00:00:00",
        )
        assert c.cloud_provider == "aws"
        assert c.node_count == 10
        assert c.ssh_tunnel_enabled is True


class TestClusterListResponse:
    def test_empty_list(self):
        r = ClusterListResponse(clusters=[])
        assert r.clusters == []

    def test_with_clusters(self):
        r = ClusterListResponse(
            clusters=[
                ClusterSummary(id=1, name="dev"),
                ClusterSummary(id=2, name="prod"),
            ]
        )
        assert len(r.clusters) == 2


class TestClusterDetailResponse:
    def test_full_detail(self):
        d = ClusterDetailResponse(
            id=1,
            name="prod",
            context="prod-context",
            api_server="https://k8s.example.com",
            version="1.28",
            status="active",
            ssh_tunnel_enabled=True,
            ssh_remote_k8s_host="10.0.0.5",
            ssh_remote_k8s_port=6443,
            ssh_credential_id=3,
            meta_data={"provider": "aws", "tags": ["production"]},
        )
        assert d.ssh_remote_k8s_host == "10.0.0.5"
        assert d.meta_data["provider"] == "aws"


class TestClusterOperationResponse:
    def test_success(self):
        r = ClusterOperationResponse(message="Cluster created", cluster_id=5)
        assert r.success is True
        assert r.cluster_id == 5

    def test_without_cluster_id(self):
        r = ClusterOperationResponse(message="Deleted")
        assert r.cluster_id is None


class TestClusterConnectionTestResponse:
    def test_success(self):
        r = ClusterConnectionTestResponse(
            success=True,
            message="Connection successful",
            cluster_name="prod-cluster",
            version="1.28.3",
            api_server="https://k8s.example.com",
            cloud_provider="aws",
            region="us-west-2",
        )
        assert r.success is True
        assert r.version == "1.28.3"
        assert r.cluster_name == "prod-cluster"

    def test_failure(self):
        r = ClusterConnectionTestResponse(
            success=False,
            message="Connection refused",
            status_code=403,
        )
        assert r.success is False
        assert r.version is None
        assert r.status_code == 403


# ── Namespace / Node schemas ─────────────────────────────────────────


class TestNamespaceInfo:
    def test_minimal(self):
        n = NamespaceInfo(name="default")
        assert n.name == "default"
        assert n.status is None

    def test_full(self):
        n = NamespaceInfo(
            name="production",
            status="Active",
            labels={"env": "prod"},
            created_at="2026-01-01T00:00:00",
        )
        assert n.labels["env"] == "prod"


class TestNamespaceListResponse:
    def test_with_namespaces(self):
        r = NamespaceListResponse(
            namespaces=[NamespaceInfo(name="default"), NamespaceInfo(name="kube-system")],
            cluster_id=1,
        )
        assert len(r.namespaces) == 2
        assert r.cluster_id == 1


class TestNodeCountResponse:
    def test_node_count(self):
        r = NodeCountResponse(node_count=5, cluster_id=1)
        assert r.node_count == 5


# ── Resource schemas ─────────────────────────────────────────────────


class TestK8sResourceItem:
    def test_minimal(self):
        r = K8sResourceItem(name="my-pod")
        assert r.name == "my-pod"
        assert r.namespace is None

    def test_full(self):
        r = K8sResourceItem(
            name="nginx-deployment",
            namespace="production",
            kind="Deployment",
            api_version="apps/v1",
            status="Running",
            age="5d",
            labels={"app": "nginx"},
            annotations={"deployment.kubernetes.io/revision": "3"},
            conditions=[{"type": "Available", "status": "True"}],
        )
        assert r.kind == "Deployment"
        assert r.labels["app"] == "nginx"
        assert len(r.conditions) == 1


class TestK8sResourceListResponse:
    def test_response(self):
        r = K8sResourceListResponse(
            resources=[K8sResourceItem(name="pod1"), K8sResourceItem(name="pod2")],
            resource_type="pods",
            namespace="default",
            cluster_id=1,
        )
        assert len(r.resources) == 2
        assert r.resource_type == "pods"


class TestK8sResourceOperationResponse:
    def test_success(self):
        r = K8sResourceOperationResponse(
            message="Resource created",
            resource_name="my-configmap",
            namespace="default",
        )
        assert r.success is True
        assert r.resource_name == "my-configmap"


# ── Scan / Resource Type / Tunnel schemas ────────────────────────────


class TestClusterScanResponse:
    def test_success(self):
        r = ClusterScanResponse(
            success=True,
            message="Scan complete",
            scan_results={"deployments": 5, "services": 3},
        )
        assert r.scan_results["deployments"] == 5

    def test_no_results(self):
        r = ClusterScanResponse(success=True, message="No results")
        assert r.scan_results is None


class TestResourceTypeInfo:
    def test_resource_type(self):
        r = ResourceTypeInfo(
            name="deployments",
            api_version="apps/v1",
            kind="Deployment",
            namespaced=True,
            verbs=["get", "list", "create", "delete"],
        )
        assert r.namespaced is True
        assert "create" in r.verbs


class TestResourceTypeListResponse:
    def test_list(self):
        r = ResourceTypeListResponse(
            resource_types=[
                ResourceTypeInfo(name="pods", api_version="v1", kind="Pod", namespaced=True),
                ResourceTypeInfo(name="nodes", api_version="v1", kind="Node", namespaced=False),
            ]
        )
        assert len(r.resource_types) == 2


class TestTunnelStatus:
    def test_active_tunnel(self):
        t = TunnelStatus(
            cluster_id=1,
            cluster_name="prod",
            active=True,
            local_port=6443,
            ssh_host="bastion.example.com",
            uptime_seconds=3600.5,
        )
        assert t.active is True
        assert t.local_port == 6443

    def test_inactive_tunnel(self):
        t = TunnelStatus(cluster_id=2)
        assert t.active is False
        assert t.local_port is None


class TestTunnelListResponse:
    def test_list(self):
        r = TunnelListResponse(
            tunnels=[
                TunnelStatus(cluster_id=1, active=True, local_port=6443),
                TunnelStatus(cluster_id=2, active=False),
            ]
        )
        assert len(r.tunnels) == 2


# ── CreateMigrationRequest — kubectl_resources validation (mwiget audit) ──────

MINIMAL_MIGRATION = {
    "source_descriptor": {"proxy_type": "cis-bigip", "class_name": "my-vs", "namespace": "default"},
    "combined_yaml": "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: test\n",
}


class TestCreateMigrationRequestKubectlResources:
    """Validate that kubectl_resources elements are safe 'kind/name [namespace]' strings.

    Regression for mwiget audit finding: operator-supplied strings were passed
    toward kubectl delete with no format validation, allowing flag injection
    (e.g. '--all' would cause unintended mass deletion).
    """

    def test_valid_resources_accepted(self):
        """Well-formed 'kind/name' and 'kind/name namespace' strings pass."""
        req = CreateMigrationRequest(
            **MINIMAL_MIGRATION,
            teardown_info={
                "kubectl_resources": [
                    "virtualserver/my-vs",
                    "ingressclass/nginx default",
                    "crd.group.io/my-name kube-system",
                ],
            },
        )
        resources = req.teardown_info["kubectl_resources"]
        assert len(resources) == 3

    def test_none_teardown_info_accepted(self):
        """No teardown_info → no validation (optional field)."""
        req = CreateMigrationRequest(**MINIMAL_MIGRATION, teardown_info=None)
        assert req.teardown_info is None

    def test_teardown_info_without_kubectl_resources_accepted(self):
        """teardown_info with helm_release but no kubectl_resources → accepted."""
        req = CreateMigrationRequest(
            **MINIMAL_MIGRATION,
            teardown_info={"helm_release": "my-release", "helm_namespace": "default"},
        )
        assert req.teardown_info["helm_release"] == "my-release"

    @pytest.mark.parametrize("bad_item", [
        "--all",
        "-n default",
        "--delete-all",
        "",
        "kind/name; rm -rf /",
        "kind/name\nnewline",
        "kind/name extra extra",
    ])
    def test_malformed_resource_rejected(self, bad_item: str):
        """Malformed kubectl_resources element is rejected at schema boundary."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CreateMigrationRequest(
                **MINIMAL_MIGRATION,
                teardown_info={"kubectl_resources": [bad_item]},
            )
