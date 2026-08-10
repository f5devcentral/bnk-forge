"""
Unit tests for services.scanner.nodes — V1Node parsing.

Pure function, no I/O, no mocking needed.
Uses SimpleNamespace to create lightweight node-like objects.
"""

from types import SimpleNamespace

from services.scanner.nodes import is_kind_cluster, is_local_cluster, parse_node


def _make_node(
    name="test-node",
    labels=None,
    conditions=None,
    allocatable=None,
    capacity=None,
    os_image="Ubuntu 22.04",
    kernel="5.15.0",
    container_runtime="containerd://1.7.2",
    kubelet="v1.28.3",
    architecture="amd64",
    provider_id=None,
):
    """Build a minimal V1Node-like object for testing."""
    labels = labels or {}
    conditions = [
        SimpleNamespace(type=c["type"], status=c["status"])
        for c in (conditions or [{"type": "Ready", "status": "True"}])
    ]
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels=labels),
        spec=SimpleNamespace(provider_id=provider_id),
        status=SimpleNamespace(
            node_info=SimpleNamespace(
                os_image=os_image,
                kernel_version=kernel,
                container_runtime_version=container_runtime,
                kubelet_version=kubelet,
                architecture=architecture,
            ),
            allocatable=allocatable or {},
            capacity=capacity or {},
            conditions=conditions,
        ),
    )


class TestParseNodeBasic:
    def test_basic_worker_node(self):
        result = parse_node(_make_node(name="worker-1"))
        assert result["name"] == "worker-1"
        assert result["roles"] == ["worker"]
        assert result["ready"] is True
        assert result["os_image"] == "Ubuntu 22.04"
        assert result["kernel_version"] == "5.15.0"
        assert result["architecture"] == "amd64"

    def test_node_with_explicit_role(self):
        result = parse_node(_make_node(
            labels={"node-role.kubernetes.io/control-plane": ""}
        ))
        assert result["roles"] == ["control-plane"]

    def test_node_with_multiple_roles(self):
        result = parse_node(_make_node(
            labels={
                "node-role.kubernetes.io/control-plane": "",
                "node-role.kubernetes.io/master": "",
            }
        ))
        assert sorted(result["roles"]) == ["control-plane", "master"]

    def test_no_role_labels_defaults_to_worker(self):
        result = parse_node(_make_node(labels={"foo": "bar"}))
        assert result["roles"] == ["worker"]


class TestParseNodeReadiness:
    def test_ready_node(self):
        result = parse_node(_make_node(
            conditions=[{"type": "Ready", "status": "True"}]
        ))
        assert result["ready"] is True

    def test_not_ready_node(self):
        result = parse_node(_make_node(
            conditions=[{"type": "Ready", "status": "False"}]
        ))
        assert result["ready"] is False

    def test_no_ready_condition(self):
        result = parse_node(_make_node(
            conditions=[{"type": "MemoryPressure", "status": "False"}]
        ))
        assert result["ready"] is False

    def test_no_conditions(self):
        node = _make_node()
        node.status.conditions = None
        result = parse_node(node)
        assert result["ready"] is False

    def test_no_status(self):
        node = _make_node()
        node.status = None
        result = parse_node(node)
        assert result["ready"] is False
        assert result["os_image"] is None
        assert result["allocatable"]["cpu"] is None


class TestParseNodeCloudLabels:
    def test_instance_type_standard_label(self):
        result = parse_node(_make_node(
            labels={"node.kubernetes.io/instance-type": "m5.xlarge"}
        ))
        assert result["instance_type"] == "m5.xlarge"

    def test_instance_type_beta_label(self):
        result = parse_node(_make_node(
            labels={"beta.kubernetes.io/instance-type": "c5.2xlarge"}
        ))
        assert result["instance_type"] == "c5.2xlarge"

    def test_zone_standard_label(self):
        result = parse_node(_make_node(
            labels={"topology.kubernetes.io/zone": "us-east-1a"}
        ))
        assert result["zone"] == "us-east-1a"

    def test_zone_beta_label(self):
        result = parse_node(_make_node(
            labels={"failure-domain.beta.kubernetes.io/zone": "eu-west-1b"}
        ))
        assert result["zone"] == "eu-west-1b"

    def test_no_cloud_labels(self):
        result = parse_node(_make_node())
        assert result["instance_type"] is None
        assert result["zone"] is None


class TestParseNodeHugePages:
    def test_hugepages_2mi(self):
        result = parse_node(_make_node(
            allocatable={"hugepages-2Mi": "4Gi"},
            capacity={"hugepages-2Mi": "4Gi"},
        ))
        assert result["allocatable"]["hugepages_2mi"] == "4Gi"
        assert result["capacity"]["hugepages_2mi"] == "4Gi"

    def test_hugepages_1gi(self):
        result = parse_node(_make_node(
            allocatable={"hugepages-1Gi": "2Gi"},
            capacity={"hugepages-1Gi": "2Gi"},
        ))
        assert result["allocatable"]["hugepages_1gi"] == "2Gi"
        assert result["capacity"]["hugepages_1gi"] == "2Gi"

    def test_no_hugepages(self):
        result = parse_node(_make_node())
        assert result["allocatable"]["hugepages_2mi"] is None
        assert result["allocatable"]["hugepages_1gi"] is None


class TestParseNodeSRIOV:
    def test_sriov_resources_in_allocatable(self):
        result = parse_node(_make_node(
            allocatable={
                "intel.com/sriov_vf": "8",
                "cpu": "16",
            }
        ))
        assert result["allocatable"]["sriov_resources"] == {
            "intel.com/sriov_vf": "8"
        }

    def test_sriov_netdevice_resources(self):
        result = parse_node(_make_node(
            capacity={"intel.com/netdevice": "4"}
        ))
        assert result["capacity"]["sriov_resources"] == {
            "intel.com/netdevice": "4"
        }

    def test_no_sriov_resources(self):
        result = parse_node(_make_node(
            allocatable={"cpu": "16", "memory": "64Gi"}
        ))
        assert result["allocatable"]["sriov_resources"] == {}


class TestParseNodeHPNode:
    def test_f5_tmm_app_label(self):
        result = parse_node(_make_node(labels={"app": "f5-tmm"}))
        assert result["is_hp_node"] is True

    def test_f5_tmm_role_label(self):
        result = parse_node(_make_node(
            labels={"node-role.kubernetes.io/f5-tmm": ""}
        ))
        assert result["is_hp_node"] is True

    def test_f5_tmm_in_app_label(self):
        result = parse_node(_make_node(labels={"app": "f5-tmm-debug"}))
        assert result["is_hp_node"] is True

    def test_not_hp_node(self):
        result = parse_node(_make_node(labels={"app": "nginx"}))
        assert result["is_hp_node"] is False

    def test_no_labels(self):
        result = parse_node(_make_node())
        assert result["is_hp_node"] is False

    def test_hugepages_capacity_no_labels(self):
        """A node reporting hugepages-2Mi capacity counts as HP-capable even
        without the f5-tmm app/role label (e.g. a direct-helm kind cluster)."""
        result = parse_node(_make_node(capacity={"hugepages-2Mi": "1024"}))
        assert result["is_hp_node"] is True

    def test_hugepages_capacity_zero(self):
        result = parse_node(_make_node(capacity={"hugepages-2Mi": "0"}))
        assert result["is_hp_node"] is False

    def test_hugepages_capacity_quantity_suffix(self):
        result = parse_node(_make_node(capacity={"hugepages-2Mi": "2Gi"}))
        assert result["is_hp_node"] is True


class TestParseNodeProviderId:
    def test_provider_id_surfaced(self):
        result = parse_node(_make_node(
            provider_id="kind://docker/bnkfull/bnkfull-control-plane"
        ))
        assert result["provider_id"] == "kind://docker/bnkfull/bnkfull-control-plane"

    def test_provider_id_none_when_no_spec(self):
        node = _make_node()
        node.spec = None
        result = parse_node(node)
        assert result["provider_id"] is None


class TestIsKindCluster:
    def test_kind_provider_id_detected(self):
        nodes = [
            parse_node(_make_node(
                name="bnkfull-control-plane",
                provider_id="kind://docker/bnkfull/bnkfull-control-plane",
            ))
        ]
        assert is_kind_cluster(nodes) is True

    def test_aws_provider_id_not_kind(self):
        nodes = [
            parse_node(_make_node(
                name="ip-10-0-1-2.ec2.internal",
                provider_id="aws:///us-east-1a/i-0123456789abcdef0",
            ))
        ]
        assert is_kind_cluster(nodes) is False

    def test_mixed_cluster_any_kind_node_wins(self):
        nodes = [
            parse_node(_make_node(name="worker-1", provider_id="aws:///us-east-1a/i-1")),
            parse_node(_make_node(
                name="kind-control-plane",
                provider_id="kind://docker/kind/kind-control-plane",
            )),
        ]
        assert is_kind_cluster(nodes) is True

    def test_no_nodes(self):
        assert is_kind_cluster([]) is False


class TestIsLocalCluster:
    def test_kind_cluster_is_local(self):
        nodes = [
            parse_node(_make_node(
                name="bnkfull-control-plane",
                provider_id="kind://docker/bnkfull/bnkfull-control-plane",
            ))
        ]
        assert is_local_cluster(nodes) is True

    def test_minikube_provider_id_is_local(self):
        nodes = [parse_node(_make_node(name="minikube", provider_id="minikube//host"))]
        assert is_local_cluster(nodes) is True

    def test_docker_desktop_name_is_local(self):
        nodes = [parse_node(_make_node(name="docker-desktop", provider_id=None))]
        assert is_local_cluster(nodes) is True

    def test_aws_cluster_not_local(self):
        nodes = [
            parse_node(_make_node(
                name="ip-10-0-1-2.ec2.internal",
                provider_id="aws:///us-east-1a/i-0123456789abcdef0",
            ))
        ]
        assert is_local_cluster(nodes) is False
