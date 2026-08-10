"""
Tests for services.cluster_management_service — Kubernetes cluster CRUD + EKS detection.

BC-009: Cluster management — real DB, mock subprocess/eks_service.
"""

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.errors import AuthenticationError, BadRequestError, ConflictError, InternalError, NotFoundError
from services.cluster_management_service import ClusterManagementService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_KUBECONFIG_YAML = """\
apiVersion: v1
clusters:
- cluster:
    server: https://k8s.example.com:6443
    certificate-authority-data: dGVzdA==
  name: test-cluster
contexts:
- context:
    cluster: test-cluster
    user: admin
  name: test-context
current-context: test-context
kind: Config
users:
- name: admin
  user:
    token: test-token
"""

SAMPLE_KUBECONFIG_B64 = base64.b64encode(SAMPLE_KUBECONFIG_YAML.encode()).decode()


def _make_create_data(**overrides):
    defaults = {
        "name": "my-cluster",
        "kubeconfig": SAMPLE_KUBECONFIG_B64,
        "cloud_provider": "aws",
        "region": "us-east-1",
        "context": None,
        "default_namespace": "default",
        "ssh_tunnel_enabled": False,
        "ssh_remote_k8s_host": "localhost",
        "ssh_remote_k8s_port": 6443,
        "ssh_credential_id": None,
        "ssh_host_override": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_update_data(**overrides):
    defaults = {
        "name": None,
        "kubeconfig": None,
        "cloud_provider": None,
        "region": None,
        "context": None,
        "default_namespace": None,
        "ssh_tunnel_enabled": None,
        "ssh_remote_k8s_host": None,
        "ssh_remote_k8s_port": None,
        "ssh_credential_id": None,
        "ssh_host_override": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# create_cluster
# ---------------------------------------------------------------------------

class TestCreateCluster:
    """Cluster creation with kubeconfig parsing and encryption."""

    def test_creates_cluster_with_valid_kubeconfig(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data())
        assert result["name"] == "my-cluster"
        assert result["project_id"] == project.id
        assert result["cloud_provider"] == "aws"
        assert result["api_server"] == "https://k8s.example.com:6443"
        assert result["status"] == "active"
        assert result["context"] == "test-context"  # auto-detected from kubeconfig

    def test_auto_detects_context_from_kubeconfig(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data(context=None))
        assert result["context"] == "test-context"

    def test_uses_explicit_context(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data(context="custom-ctx"))
        assert result["context"] == "custom-ctx"

    def test_invalid_base64_raises(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        with pytest.raises(BadRequestError, match="Invalid base64"):
            svc.create_cluster(project.id, _make_create_data(kubeconfig="not-valid-b64!!!"))

    def test_duplicate_name_raises(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        svc.create_cluster(project.id, _make_create_data(name="dup-cluster"))
        with pytest.raises(ConflictError):
            svc.create_cluster(project.id, _make_create_data(name="dup-cluster"))

    def test_nonexistent_project_raises(self, db):
        svc = ClusterManagementService(db)
        with pytest.raises(NotFoundError):
            svc.create_cluster(99999, _make_create_data())

    def test_kubeconfig_is_encrypted(self, db, make_project):
        """The stored kubeconfig should be encrypted, not plaintext."""
        from models import KubernetesCluster
        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data())
        cluster = db.query(KubernetesCluster).filter(KubernetesCluster.id == result["id"]).first()
        assert cluster.kubeconfig_encrypted is not None
        assert "server:" not in cluster.kubeconfig_encrypted  # not plaintext

    def test_ssh_tunnel_fields_stored(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data(
            ssh_tunnel_enabled=True,
            ssh_remote_k8s_host="192.168.1.100",
            ssh_remote_k8s_port=16443,
        ))
        assert result["id"] is not None

    def test_creates_cluster_with_ssh_credential_id(self, db, make_project):
        """K8S-014: ssh_credential_id is stored on cluster creation."""
        from models.kubernetes import KubernetesCluster
        from models.ssh_credential import SSHCredential

        project = make_project()
        cred = SSHCredential(name="cluster-ssh", host="10.0.0.10", port=22, username="admin", auth_type="key")
        db.add(cred)
        db.flush()

        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data(
            ssh_credential_id=cred.id,
            ssh_tunnel_enabled=True,
        ))

        cluster = db.query(KubernetesCluster).get(result["id"])
        assert cluster.ssh_credential_id == cred.id
        assert cluster.ssh_tunnel_enabled is True

    def test_ssh_credential_id_in_serialized_response(self, db, make_project):
        """K8S-014: ssh_credential_id appears in cluster list/detail responses."""
        from models.ssh_credential import SSHCredential

        project = make_project()
        cred = SSHCredential(name="serial-ssh", host="10.0.0.11", port=22, username="u", auth_type="key")
        db.add(cred)
        db.flush()

        svc = ClusterManagementService(db)
        svc.create_cluster(project.id, _make_create_data(
            name="ssh-cluster",
            ssh_credential_id=cred.id,
        ))

        clusters = svc.list_project_clusters(project.id)
        cluster_data = clusters["clusters"][0]
        assert cluster_data["ssh_credential_id"] == cred.id

    def test_create_sets_platform_context(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data(cloud_provider="eks"))
        assert result["detected_platform_profile"] == "eks"
        assert result["detected_platform_provider"] == "aws"
        assert "platform_capabilities" in result
        assert "platform_constraints" in result
        assert result["platform_capabilities"]["cloud_load_balancer"] is True
        assert result["platform_constraints"]["managed_control_plane"] is True

    def test_create_ibm_cluster_sets_roks_platform_context(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data(cloud_provider="ibm", region="us-south"))
        assert result["detected_platform_profile"] == "roks"
        assert result["detected_platform_provider"] == "ibm"
        assert result["platform_capabilities"]["cloud_load_balancer"] is True
        assert result["platform_constraints"]["managed_control_plane"] is True


# ---------------------------------------------------------------------------
# list_all_clusters / list_project_clusters
# ---------------------------------------------------------------------------

class TestListClusters:
    """Cluster listing — global and per-project."""

    def test_list_all_empty(self, db):
        svc = ClusterManagementService(db)
        result = svc.list_all_clusters()
        assert result["clusters"] == []
        assert result["count"] == 0

    def test_list_all_returns_clusters(self, db, make_project, make_k8s_cluster):
        p = make_project()
        make_k8s_cluster(project=p, name="c1")
        make_k8s_cluster(project=p, name="c2")
        svc = ClusterManagementService(db)
        result = svc.list_all_clusters()
        assert result["count"] == 2

    def test_list_project_clusters_filters(self, db, make_project, make_k8s_cluster):
        p1 = make_project(name="P1")
        p2 = make_project(name="P2")
        make_k8s_cluster(project=p1, name="c1")
        make_k8s_cluster(project=p2, name="c2")
        svc = ClusterManagementService(db)
        result = svc.list_project_clusters(p1.id)
        assert result["count"] == 1
        assert result["clusters"][0]["name"] == "c1"

    def test_list_project_clusters_nonexistent_project(self, db):
        svc = ClusterManagementService(db)
        with pytest.raises(NotFoundError):
            svc.list_project_clusters(99999)


# ---------------------------------------------------------------------------
# get_cluster_details
# ---------------------------------------------------------------------------

class TestGetClusterDetails:
    """Retrieve single cluster details."""

    def test_returns_full_details(self, db, make_project, make_k8s_cluster):
        p = make_project()
        c = make_k8s_cluster(project=p, name="detail-cluster")
        svc = ClusterManagementService(db)
        result = svc.get_cluster_details(c.id)
        assert result["name"] == "detail-cluster"
        assert result["id"] == c.id
        assert result["project_id"] == p.id
        assert "created_at" in result

    def test_nonexistent_raises(self, db):
        svc = ClusterManagementService(db)
        with pytest.raises(NotFoundError):
            svc.get_cluster_details(99999)

    def test_detail_includes_platform_context(self, db, make_project, make_k8s_cluster):
        p = make_project()
        c = make_k8s_cluster(project=p, name="detail-platform", cloud_provider="on-prem")
        svc = ClusterManagementService(db)
        result = svc.get_cluster_details(c.id)
        assert result["detected_platform_profile"] == "generic_onprem"
        assert result["detected_platform_provider"] == "baremetal"
        assert "platform_capabilities" in result
        assert "platform_constraints" in result

    def test_detail_exposes_running_release_id(self, db, make_project, make_k8s_cluster):
        """get_cluster_details serializes running_release_id (ADR-494 Phase B read path)."""
        from models.bnk_release import BnkRelease
        from models.enums import ReleaseSourceType

        p = make_project()
        rel = BnkRelease(
            ga_label="BNK 2.3 GA", product_line="BNK",
            flo_version_prefix="2.21", source_type=ReleaseSourceType.CLOUDDOCS, is_active=True,
        )
        db.add(rel)
        db.flush()

        c = make_k8s_cluster(project=p, name="rel-cluster", running_release_id=rel.id)
        svc = ClusterManagementService(db)
        result = svc.get_cluster_details(c.id)

        assert result["running_release_id"] == rel.id
        assert result["deployable_release_id"] is None  # not set on this cluster

    def test_detail_running_release_id_null_when_not_set(self, db, make_project, make_k8s_cluster):
        """get_cluster_details returns running_release_id=None for undiscovered clusters."""
        p = make_project()
        c = make_k8s_cluster(project=p, name="no-rel-cluster")
        svc = ClusterManagementService(db)
        result = svc.get_cluster_details(c.id)

        assert result["running_release_id"] is None
        assert result["deployable_release_id"] is None

    def test_list_all_clusters_exposes_running_release_id(self, db, make_project, make_k8s_cluster):
        """list_all_clusters (serialize_cluster) serializes running_release_id."""
        from models.bnk_release import BnkRelease
        from models.enums import ReleaseSourceType

        p = make_project()
        rel = BnkRelease(
            ga_label="BNK 2.3 GA", product_line="BNK",
            flo_version_prefix="2.21", source_type=ReleaseSourceType.CLOUDDOCS, is_active=True,
        )
        db.add(rel)
        db.flush()

        make_k8s_cluster(project=p, name="list-rel-cluster", running_release_id=rel.id)
        svc = ClusterManagementService(db)
        result = svc.list_all_clusters()

        cluster_dict = result["clusters"][0]
        assert cluster_dict["running_release_id"] == rel.id
        assert cluster_dict["deployable_release_id"] is None


# ---------------------------------------------------------------------------
# update_cluster
# ---------------------------------------------------------------------------

class TestUpdateCluster:
    """Update cluster configuration fields."""

    def test_update_name(self, db, make_project, make_k8s_cluster):
        p = make_project()
        c = make_k8s_cluster(project=p, name="old-name")
        svc = ClusterManagementService(db)
        result = svc.update_cluster(c.id, _make_update_data(name="new-name"))
        assert result["name"] == "new-name"

    def test_update_duplicate_name_raises(self, db, make_project, make_k8s_cluster):
        p = make_project()
        make_k8s_cluster(project=p, name="existing")
        c2 = make_k8s_cluster(project=p, name="to-rename")
        svc = ClusterManagementService(db)
        with pytest.raises(ConflictError):
            svc.update_cluster(c2.id, _make_update_data(name="existing"))

    def test_update_kubeconfig_encrypts(self, db, make_project, make_k8s_cluster):
        from models import KubernetesCluster
        p = make_project()
        c = make_k8s_cluster(project=p, name="update-kube")
        svc = ClusterManagementService(db)
        svc.update_cluster(c.id, _make_update_data(kubeconfig=SAMPLE_KUBECONFIG_B64))
        cluster = db.query(KubernetesCluster).filter(KubernetesCluster.id == c.id).first()
        assert cluster.kubeconfig_encrypted is not None

    def test_update_region_and_provider(self, db, make_project, make_k8s_cluster):
        p = make_project()
        c = make_k8s_cluster(project=p, name="region-test")
        svc = ClusterManagementService(db)
        result = svc.update_cluster(c.id, _make_update_data(
            region="eu-west-1", cloud_provider="eks"
        ))
        assert result["region"] == "eu-west-1"
        assert result["cloud_provider"] == "eks"

    def test_update_nonexistent_raises(self, db):
        svc = ClusterManagementService(db)
        with pytest.raises(NotFoundError):
            svc.update_cluster(99999, _make_update_data(name="x"))

    def test_update_enabling_tunnel_auto_derives_remote_target_from_api_server(
        self, db, make_project, make_k8s_cluster
    ):
        p = make_project()
        c = make_k8s_cluster(
            project=p,
            name="tunnel-auto-derive",
            api_server="https://127.0.0.1:40253",
            ssh_tunnel_enabled=False,
            ssh_remote_k8s_host="localhost",
            ssh_remote_k8s_port=6443,
        )

        svc = ClusterManagementService(db)
        result = svc.update_cluster(c.id, _make_update_data(ssh_tunnel_enabled=True))

        assert result["ssh_tunnel_enabled"] is True
        assert result["ssh_remote_k8s_host"] == "127.0.0.1"
        assert result["ssh_remote_k8s_port"] == 40253

    def test_update_enabling_tunnel_preserves_explicit_remote_target(
        self, db, make_project, make_k8s_cluster
    ):
        p = make_project()
        c = make_k8s_cluster(
            project=p,
            name="tunnel-explicit-target",
            api_server="https://127.0.0.1:40253",
            ssh_tunnel_enabled=False,
            ssh_remote_k8s_host="localhost",
            ssh_remote_k8s_port=6443,
        )

        svc = ClusterManagementService(db)
        result = svc.update_cluster(
            c.id,
            _make_update_data(
                ssh_tunnel_enabled=True,
                ssh_remote_k8s_host="kind-control-plane",
                ssh_remote_k8s_port=6443,
            ),
        )

        assert result["ssh_tunnel_enabled"] is True
        assert result["ssh_remote_k8s_host"] == "kind-control-plane"
        assert result["ssh_remote_k8s_port"] == 6443


# ---------------------------------------------------------------------------
# delete_cluster
# ---------------------------------------------------------------------------

class TestDeleteCluster:
    """Cluster deletion."""

    def test_delete_removes_cluster(self, db, make_project, make_k8s_cluster):
        from models import KubernetesCluster
        p = make_project()
        c = make_k8s_cluster(project=p, name="to-delete")
        svc = ClusterManagementService(db)
        result = svc.delete_cluster(c.id)
        assert "deleted successfully" in result["message"]
        assert db.query(KubernetesCluster).filter(KubernetesCluster.id == c.id).first() is None

    def test_delete_nonexistent_raises(self, db):
        svc = ClusterManagementService(db)
        with pytest.raises(NotFoundError):
            svc.delete_cluster(99999)

    def test_delete_releases_dpu_tmfifo_allocations(self, db, make_project, make_k8s_cluster):
        """Deleting a cluster must clear tmfifo IPs on all member DPUs (ADR-424 cold audit A1).

        Flow: assign DPU to cluster (gets a /30) → delete_cluster →
          assert dpu_tmfifo_ip is None AND derive_tmfifo_dpu_ip returns the formula.
        Without the fix, dpu_tmfifo_ip survives and a re-flash bakes the stale /30.
        """
        from models.bare_metal import BareMetalHost
        from models.dpu import Dpu
        from services.bf_conf_renderer import derive_tmfifo_dpu_ip
        from services.bnk_cluster_service import BnkClusterService

        p = make_project()
        c = make_k8s_cluster(project=p, name="del-ipam")

        host = BareMetalHost(project_id=p.id, name="h-del", host_ip="10.200.0.1")
        db.add(host)
        db.flush()

        dpu = Dpu(
            project_id=p.id,
            name="dpu-del",
            access_mode="in-band",
            host_node_ip="10.200.0.1",
            rshim_device="rshim0",
            oob0_ipv4="dhcp",
        )
        db.add(dpu)
        db.flush()

        # Assign DPU to cluster — triggers tmfifo IPAM allocation.
        BnkClusterService(db).assign_members(
            cluster_id=c.id,
            control_plane_host_id=host.id,
            host_ids=[host.id],
            dpu_ids=[dpu.id],
        )
        db.flush()

        # Verify allocation was made.
        db.expire_all()
        assert dpu.dpu_tmfifo_ip is not None, "DPU must have a tmfifo IP after assign_members"
        stale_ip = dpu.dpu_tmfifo_ip

        # Delete the cluster.
        ClusterManagementService(db).delete_cluster(c.id)
        db.flush()

        # Allocation must be released.
        db.expire_all()
        assert dpu.dpu_tmfifo_ip is None, (
            f"dpu_tmfifo_ip={stale_ip!r} was not cleared on cluster delete"
        )
        assert dpu.host_tmfifo_ip is None, "host_tmfifo_ip must be cleared on cluster delete"
        assert dpu.kubernetes_cluster_id is None

        # derive_tmfifo_dpu_ip must fall back to the rshim formula, not the stale IP.
        formula_ip = derive_tmfifo_dpu_ip("rshim0", dpu=dpu)
        assert formula_ip == "192.168.100.2/30", (
            f"Expected formula IP 192.168.100.2/30, got {formula_ip!r}"
        )

    def test_delete_cluster_clears_is_control_plane(self, db, make_project, make_k8s_cluster):
        """Deleting a cluster must clear is_control_plane on the former CP host (ADR-424 W-2).

        Flow: assign host as control_plane_host_id → assert is_control_plane=True →
          delete_cluster → assert is_control_plane=False AND kubernetes_cluster_id=None.
        Without the fix, is_control_plane survives as True after the cluster is gone.
        """
        from models.bare_metal import BareMetalHost
        from models.dpu import Dpu
        from services.bnk_cluster_service import BnkClusterService

        p = make_project()
        c = make_k8s_cluster(project=p, name="del-cp-flag")

        host = BareMetalHost(project_id=p.id, name="h-cp", host_ip="10.201.0.1")
        db.add(host)
        db.flush()

        dpu = Dpu(
            project_id=p.id,
            name="dpu-cp",
            access_mode="in-band",
            host_node_ip="10.201.0.1",
            rshim_device="rshim0",
            oob0_ipv4="dhcp",
        )
        db.add(dpu)
        db.flush()

        BnkClusterService(db).assign_members(
            cluster_id=c.id,
            control_plane_host_id=host.id,
            host_ids=[host.id],
            dpu_ids=[dpu.id],
        )
        db.flush()
        db.expire_all()

        assert host.is_control_plane is True, "assign_members must set is_control_plane=True"

        ClusterManagementService(db).delete_cluster(c.id)
        db.flush()
        db.expire_all()

        assert host.is_control_plane is False, (
            "is_control_plane must be cleared to False after cluster delete"
        )
        assert host.kubernetes_cluster_id is None, (
            "kubernetes_cluster_id must be NULL after cluster delete"
        )


# ---------------------------------------------------------------------------
# detect_eks_clusters
# ---------------------------------------------------------------------------

class TestDetectEksClusters:
    """Auto-detect and register EKS clusters from applied modules."""

    def test_no_eks_modules_returns_empty(self, db, make_project):
        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.detect_eks_clusters(project.id)
        assert result["success"] is True
        assert result["registered"] == []
        assert "No deployed EKS" in result["message"]

    @patch("services.eks_service.register_eks_cluster")
    def test_registers_found_eks_module(self, mock_register, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="eks", path="bnk/eks")
        make_project_module(project=project, library_module=lib, status="applied", outputs={"cluster_name": "my-eks"})

        mock_cluster = MagicMock()
        mock_cluster.id = 1
        mock_cluster.name = "my-eks"
        mock_register.return_value = mock_cluster

        svc = ClusterManagementService(db)
        result = svc.detect_eks_clusters(project.id)
        assert result["success"] is True
        assert len(result["registered"]) == 1
        mock_register.assert_called_once()

    @patch("services.eks_service.register_eks_cluster")
    def test_skips_already_registered(self, mock_register, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="eks", path="bnk/eks")
        make_project_module(project=project, library_module=lib, status="applied", outputs={"cluster_name": "my-eks"})

        mock_register.side_effect = Exception("already registered")

        svc = ClusterManagementService(db)
        result = svc.detect_eks_clusters(project.id)
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "already_registered"

    @patch("services.eks_service.register_eks_cluster")
    def test_handles_registration_error(self, mock_register, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="eks", path="bnk/eks")
        make_project_module(project=project, library_module=lib, status="applied", outputs={"cluster_name": "my-eks"})

        mock_register.side_effect = ValueError("Invalid EKS outputs")

        svc = ClusterManagementService(db)
        result = svc.detect_eks_clusters(project.id)
        assert len(result["errors"]) == 1

    def test_nonexistent_project_raises(self, db):
        svc = ClusterManagementService(db)
        with pytest.raises(NotFoundError):
            svc.detect_eks_clusters(99999)

    def test_registers_found_roks_module(self, db, make_project, make_project_module, make_module_library):
        project = make_project(cloud_provider="ibm", region="us-south")
        lib = make_module_library(name="roks", path="bnk/roks")
        make_project_module(
            project=project,
            library_module=lib,
            status="applied",
            outputs={
                "cluster_name": "my-roks",
                "cluster_endpoint": "https://my-roks.example.com:6443",
                "region": "us-south",
                "cluster_id": "cluster-123",
            },
        )

        svc = ClusterManagementService(db)
        result = svc.detect_managed_clusters(project.id)
        assert result["success"] is True
        assert len(result["registered"]) == 1
        assert result["registered"][0]["name"] == "my-roks"

    def test_registers_ibm_wrapper_module_by_output_shape(self, db, make_project, make_project_module, make_module_library):
        project = make_project(cloud_provider="ibm", region="us-south")
        lib = make_module_library(
            name="ibm_roks_single_nic",
            path="modules/cluster",
            provider="ibm",
            category="infra",
            outputs_metadata=[
                {"name": "cluster_id", "type": "string"},
                {"name": "cluster_name", "type": "string"},
                {"name": "openshift_cluster_public_endpoint", "type": "string"},
                {"name": "region", "type": "string"},
            ],
        )
        make_project_module(
            project=project,
            library_module=lib,
            status="applied",
            outputs={
                "cluster_name": "my-wrapper-roks",
                "openshift_cluster_public_endpoint": "https://my-wrapper-roks.example.com:6443",
                "region": "us-south",
                "cluster_id": "cluster-456",
                "openshift_cluster_crn": "crn:v1:bluemix:public:containers-kubernetes:us-south:a/test::cluster:cluster-456",
            },
        )

        svc = ClusterManagementService(db)
        result = svc.detect_managed_clusters(project.id)

        assert result["success"] is True
        assert len(result["registered"]) == 1
        assert result["registered"][0]["name"] == "my-wrapper-roks"

    def test_ignores_ibm_infra_modules_without_cluster_outputs(self, db, make_project, make_project_module, make_module_library):
        project = make_project(cloud_provider="ibm", region="us-south")
        lib = make_module_library(
            name="ibm_misc_networking",
            path="modules/network",
            provider="ibm",
            category="infra",
            outputs_metadata=[
                {"name": "vpc_id", "type": "string"},
                {"name": "subnet_id", "type": "string"},
            ],
        )
        make_project_module(
            project=project,
            library_module=lib,
            status="applied",
            outputs={"vpc_id": "vpc-123"},
        )

        svc = ClusterManagementService(db)
        result = svc.detect_managed_clusters(project.id)

        assert result["success"] is True
        assert result["registered"] == []
        assert "No deployed managed clusters found" in result["message"]

    # -----------------------------------------------------------------------
    # Output-contract-driven EKS detection (D-019 "dynamic-by-default")
    # -----------------------------------------------------------------------

    @patch("services.eks_service.register_eks_cluster")
    def test_registers_module_by_output_contract_when_name_is_not_eks(
        self, mock_register, db, make_project, make_project_module, make_module_library
    ):
        """Core regression: aws_eks_cluster_create (name != "eks") with full EKS
        contract outputs must be detected and registered as an EKS cluster."""
        project = make_project()
        lib = make_module_library(name="aws_eks_cluster_create", path="modules/eks-create")
        make_project_module(
            project=project,
            library_module=lib,
            status="applied",
            outputs={
                "cluster_name": "my-eks-cluster",
                "cluster_endpoint": "https://ABCDEF.gr7.ap-southeast-2.eks.amazonaws.com",
                "cluster_certificate_authority_data": "LS0tLS1CRUdJTi...",
                "region": "ap-southeast-2",
                "eks_cluster_arn": "arn:aws:eks:ap-southeast-2:123456789012:cluster/my-eks-cluster",
            },
        )

        mock_cluster = MagicMock()
        mock_cluster.id = 42
        mock_cluster.name = "my-eks-cluster"
        mock_register.return_value = mock_cluster

        svc = ClusterManagementService(db)
        result = svc.detect_managed_clusters(project.id)

        assert result["success"] is True
        assert len(result["registered"]) == 1
        assert result["registered"][0]["name"] == "my-eks-cluster"
        mock_register.assert_called_once()

    @patch("services.eks_service.register_eks_cluster")
    def test_idempotent_module_with_contract_outputs_not_double_registered(
        self, mock_register, db, make_project, make_project_module, make_module_library
    ):
        """A module that satisfies both the name-based check ("eks") AND the output
        contract must not trigger double registration — register_eks_cluster is
        called exactly once (its own idempotency skip path handles duplicates)."""
        project = make_project()
        lib = make_module_library(name="eks", path="bnk/eks")
        make_project_module(
            project=project,
            library_module=lib,
            status="applied",
            outputs={
                "cluster_name": "already-registered",
                "cluster_endpoint": "https://endpoint.eks.amazonaws.com",
                "cluster_certificate_authority_data": "ca-data",
                "region": "us-east-1",
            },
        )

        mock_cluster = MagicMock()
        mock_cluster.id = 7
        mock_cluster.name = "already-registered"
        mock_register.return_value = mock_cluster

        svc = ClusterManagementService(db)
        result = svc.detect_managed_clusters(project.id)

        assert result["success"] is True
        assert len(result["registered"]) == 1
        mock_register.assert_called_once()

    def test_module_with_partial_eks_outputs_not_registered(
        self, db, make_project, make_project_module, make_module_library
    ):
        """A module whose outputs are missing any one required EKS contract key
        must NOT be detected as an EKS cluster."""
        project = make_project()
        lib = make_module_library(name="aws_eks_cluster_create", path="modules/eks-create")
        make_project_module(
            project=project,
            library_module=lib,
            status="applied",
            outputs={
                # Missing cluster_certificate_authority_data and region
                "cluster_name": "partial-cluster",
                "cluster_endpoint": "https://endpoint.eks.amazonaws.com",
            },
        )

        svc = ClusterManagementService(db)
        result = svc.detect_managed_clusters(project.id)

        assert result["success"] is True
        assert result["registered"] == []
        assert "No deployed managed clusters found" in result["message"]


# ---------------------------------------------------------------------------
# refresh_kubeconfig
# ---------------------------------------------------------------------------

class TestRefreshKubeconfig:
    """Kubeconfig refresh for EKS clusters via AWS CLI."""

    @patch("services.credentials_service.get_cloud_credentials_env")
    @patch("subprocess.run")
    def test_refresh_success(self, mock_run, mock_creds, db, make_project, make_k8s_cluster):
        project = make_project()
        cluster = make_k8s_cluster(project=project, name="my-eks", cloud_provider="eks", region="us-east-1")
        mock_creds.return_value = {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "secret"}

        # Simulate aws eks update-kubeconfig writing a kubeconfig file
        def mock_subprocess_run(cmd, **kwargs):
            # Write a kubeconfig to the temp file path
            kubeconfig_path = cmd[cmd.index("--kubeconfig") + 1]
            with open(kubeconfig_path, "w") as f:
                f.write(SAMPLE_KUBECONFIG_YAML)
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_subprocess_run

        svc = ClusterManagementService(db)
        result = svc.refresh_kubeconfig(cluster.id)
        assert "refreshed successfully" in result["message"]
        assert result["cluster_name"] == "my-eks"

    def _make_ibm_project_and_cluster(self, db, make_project, make_k8s_cluster):
        """IBM project + credential template + ROKS cluster with a stored kubeconfig."""
        from core.encryption import encrypt_value
        from models import CloudCredentialTemplate

        template = CloudCredentialTemplate(
            name="ibm-default",
            provider="ibm",
            ibmcloud_api_key_encrypted=encrypt_value("fake-ibm-api-key"),
        )
        db.add(template)
        db.flush()
        project = make_project(cloud_provider="ibm", region="us-south", credential_template_id=template.id)
        cluster = make_k8s_cluster(
            project=project, name="my-roks", cloud_provider="ibm", region="us-south",
            kubeconfig_encrypted=encrypt_value(SAMPLE_KUBECONFIG_YAML),
        )
        return project, cluster

    @patch("services.roks_service.fetch_iam_bearer_token")
    def test_refresh_ibm_cluster_rewrites_embedded_token(self, mock_fetch, db, make_project, make_k8s_cluster):
        """IBM refresh re-derives the kubeconfig with a fresh IAM bearer token and persists it."""
        from core.encryption import decrypt_value

        mock_fetch.return_value = "fresh-iam-token"
        _, cluster = self._make_ibm_project_and_cluster(db, make_project, make_k8s_cluster)

        svc = ClusterManagementService(db)
        result = svc.refresh_kubeconfig(cluster.id)

        assert result["refresh_method"] == "ibm"
        assert "refreshed successfully" in result["message"]
        mock_fetch.assert_called_once_with("fake-ibm-api-key")
        stored = decrypt_value(cluster.kubeconfig_encrypted)
        assert "fresh-iam-token" in stored
        assert "test-token" not in stored

    def test_refresh_ibm_cluster_without_kubeconfig_raises(self, db, make_project, make_k8s_cluster):
        project = make_project(cloud_provider="ibm", region="us-south")
        cluster = make_k8s_cluster(project=project, name="bare-roks", cloud_provider="ibm", region="us-south")
        svc = ClusterManagementService(db)
        with pytest.raises(BadRequestError, match="no stored kubeconfig"):
            svc.refresh_kubeconfig(cluster.id)

    @patch("services.roks_service.fetch_iam_bearer_token")
    def test_refresh_ibm_cluster_iam_exchange_failure_raises(self, mock_fetch, db, make_project, make_k8s_cluster):
        mock_fetch.return_value = None
        _, cluster = self._make_ibm_project_and_cluster(db, make_project, make_k8s_cluster)
        svc = ClusterManagementService(db)
        with pytest.raises(InternalError, match="Failed to refresh IBM IAM bearer token"):
            svc.refresh_kubeconfig(cluster.id)

    def test_refresh_non_eks_raises(self, db, make_project, make_k8s_cluster):
        project = make_project()
        cluster = make_k8s_cluster(project=project, name="generic", cloud_provider="on-prem")
        svc = ClusterManagementService(db)
        with pytest.raises(BadRequestError, match="only supported for EKS"):
            svc.refresh_kubeconfig(cluster.id)

    def test_refresh_missing_name_or_region_raises(self, db, make_project, make_k8s_cluster):
        project = make_project()
        cluster = make_k8s_cluster(project=project, name=None, cloud_provider="eks", region=None)
        svc = ClusterManagementService(db)
        with pytest.raises(BadRequestError, match="name and region required"):
            svc.refresh_kubeconfig(cluster.id)

    @patch("services.credentials_service.get_cloud_credentials_env")
    @patch("subprocess.run")
    def test_refresh_aws_cli_failure(self, mock_run, mock_creds, db, make_project, make_k8s_cluster):
        project = make_project()
        cluster = make_k8s_cluster(project=project, name="fail-eks", cloud_provider="aws", region="us-east-1")
        mock_creds.return_value = {"AWS_ACCESS_KEY_ID": "test"}
        mock_run.return_value = MagicMock(returncode=1, stderr="InvalidParameterException", stdout="")

        svc = ClusterManagementService(db)
        with pytest.raises(InternalError, match="Failed to generate kubeconfig"):
            svc.refresh_kubeconfig(cluster.id)

    @patch("services.credentials_service.get_cloud_credentials_env")
    @patch("subprocess.run")
    def test_refresh_expired_token_raises_authentication_error(self, mock_run, mock_creds, db, make_project, make_k8s_cluster):
        """Expired SSO token in aws eks update-kubeconfig stderr raises AuthenticationError, not InternalError."""
        project = make_project()
        cluster = make_k8s_cluster(project=project, name="sso-eks", cloud_provider="eks", region="us-east-1")
        mock_creds.return_value = {"AWS_ACCESS_KEY_ID": "test"}
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="An error occurred (ExpiredToken) when calling the GetCallerIdentity operation: The security token included in the request is expired",
            stdout="",
        )

        svc = ClusterManagementService(db)
        with pytest.raises(AuthenticationError):
            svc.refresh_kubeconfig(cluster.id)

    @patch("services.credentials_service.get_cloud_credentials_env")
    @patch("subprocess.run")
    def test_refresh_invalid_token_raises_authentication_error(self, mock_run, mock_creds, db, make_project, make_k8s_cluster):
        """InvalidClientTokenId in aws eks update-kubeconfig stderr raises AuthenticationError."""
        project = make_project()
        cluster = make_k8s_cluster(project=project, name="invalid-token-eks", cloud_provider="eks", region="us-east-1")
        mock_creds.return_value = {"AWS_ACCESS_KEY_ID": "test"}
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="An error occurred (InvalidClientTokenId) when calling GetCallerIdentity: The security token included in the request is invalid.",
            stdout="",
        )

        svc = ClusterManagementService(db)
        with pytest.raises(AuthenticationError):
            svc.refresh_kubeconfig(cluster.id)

    def test_refresh_nonexistent_cluster_raises(self, db):
        svc = ClusterManagementService(db)
        with pytest.raises(NotFoundError):
            svc.refresh_kubeconfig(99999)

    @patch("services.ssh_credential_service.SSHCredentialService")
    def test_refresh_ssh_cluster_success(self, mock_ssh_svc, db, make_project, make_k8s_cluster):
        """SSH-based refresh: probes remote host, stores kubeconfig, updates context + API server."""
        from models.ssh_credential import SSHCredential

        project = make_project()
        # Create an SSH credential
        cred = SSHCredential(name="test-ssh", host="10.0.0.5", port=22, username="ubuntu", auth_type="key")
        db.add(cred)
        db.flush()

        cluster = make_k8s_cluster(
            project=project, name="on-prem", cloud_provider="on-prem",
            ssh_credential_id=cred.id, ssh_tunnel_enabled=True,
            ssh_remote_k8s_host="localhost", ssh_remote_k8s_port=6443,
        )

        # Mock the SSH probe returning a valid kubeconfig
        mock_probe = mock_ssh_svc.return_value.probe_kubeconfig
        mock_probe.return_value = {
            "success": True,
            "kubeconfig": SAMPLE_KUBECONFIG_B64,
            "contexts": [{"name": "test-context", "cluster": "test-cluster", "api_server": "https://k8s.example.com:6443"}],
            "host": "10.0.0.5",
        }

        svc = ClusterManagementService(db)
        result = svc.refresh_kubeconfig(cluster.id)

        assert "via SSH" in result["message"]
        assert result["refresh_method"] == "ssh"
        assert cluster.api_server == "https://k8s.example.com:6443"
        # Tunnel target auto-updated from kubeconfig API server
        assert cluster.ssh_remote_k8s_host == "k8s.example.com"
        assert cluster.ssh_remote_k8s_port == 6443

    @patch("services.ssh_credential_service.SSHCredentialService")
    def test_refresh_ssh_preserves_localhost_api_server_for_tunneled_kind_kubeconfig(
        self,
        mock_ssh_svc,
        db,
        make_project,
        make_k8s_cluster,
    ):
        """SSH refresh should keep localhost kind kubeconfig auth intact while updating tunnel target."""
        from models.ssh_credential import SSHCredential

        project = make_project()
        cred = SSHCredential(name="kind-ssh", host="10.176.11.143", port=22, username="ubuntu", auth_type="key")
        db.add(cred)
        db.flush()

        cluster = make_k8s_cluster(
            project=project,
            name="kind-cluster",
            cloud_provider="on-prem",
            context="kubernetes-admin@kubernetes",
            api_server="https://10.176.11.143:6443",
            ssh_credential_id=cred.id,
            ssh_tunnel_enabled=True,
            ssh_remote_k8s_host="10.176.11.143",
            ssh_remote_k8s_port=6443,
        )

        kind_like_kubeconfig = """
apiVersion: v1
kind: Config
clusters:
- name: kubernetes
  cluster:
    server: https://127.0.0.1:40253
    certificate-authority-data: TESTDATA
contexts:
- name: kubernetes-admin@kubernetes
  context:
    cluster: kubernetes
    user: kubernetes-admin
current-context: kubernetes-admin@kubernetes
users:
- name: kubernetes-admin
  user:
    client-certificate-data: CERTDATA
    client-key-data: KEYDATA
""".strip()

        mock_probe = mock_ssh_svc.return_value.probe_kubeconfig
        mock_probe.return_value = {
            "success": True,
            "kubeconfig": base64.b64encode(kind_like_kubeconfig.encode("utf-8")).decode("ascii"),
            "contexts": [{
                "name": "kubernetes-admin@kubernetes",
                "cluster": "kubernetes",
                "api_server": "https://127.0.0.1:40253",
            }],
            "host": "10.176.11.143",
        }

        svc = ClusterManagementService(db)
        result = svc.refresh_kubeconfig(cluster.id)

        assert "via SSH" in result["message"]
        assert cluster.api_server == "https://127.0.0.1:40253"
        assert cluster.ssh_remote_k8s_host == "127.0.0.1"
        assert cluster.ssh_remote_k8s_port == 40253

    @patch("services.ssh_credential_service.SSHCredentialService")
    def test_refresh_ssh_falls_back_to_project_credential(self, mock_ssh_svc, db, make_project, make_k8s_cluster):
        """When cluster has no ssh_credential_id, falls back to project.ssh_credential_id."""
        from models.ssh_credential import SSHCredential

        project = make_project()
        cred = SSHCredential(name="proj-ssh", host="10.0.0.9", port=22, username="admin", auth_type="key")
        db.add(cred)
        db.flush()
        project.ssh_credential_id = cred.id
        db.flush()

        cluster = make_k8s_cluster(project=project, name="no-cred-cluster", cloud_provider="on-prem")

        mock_probe = mock_ssh_svc.return_value.probe_kubeconfig
        mock_probe.return_value = {"success": True, "kubeconfig": SAMPLE_KUBECONFIG_B64, "contexts": [], "host": "10.0.0.9"}

        svc = ClusterManagementService(db)
        result = svc.refresh_kubeconfig(cluster.id)
        assert "via SSH" in result["message"]
        mock_probe.assert_called_once_with(cred.id)

    @patch("services.ssh_credential_service.SSHCredentialService")
    def test_refresh_ssh_probe_failure_raises(self, mock_ssh_svc, db, make_project, make_k8s_cluster):
        """SSH probe failure is surfaced as BadRequestError."""
        from models.ssh_credential import SSHCredential

        project = make_project()
        cred = SSHCredential(name="fail-ssh", host="10.0.0.5", port=22, username="ubuntu", auth_type="key")
        db.add(cred)
        db.flush()

        cluster = make_k8s_cluster(project=project, name="fail-cluster", cloud_provider="on-prem", ssh_credential_id=cred.id)

        mock_ssh_svc.return_value.probe_kubeconfig.return_value = {"success": False, "error": "Connection refused"}

        svc = ClusterManagementService(db)
        with pytest.raises(BadRequestError, match="Connection refused"):
            svc.refresh_kubeconfig(cluster.id)

    @patch("services.ssh_credential_service.SSHCredentialService")
    def test_refresh_ssh_updates_context_when_old_removed(self, mock_ssh_svc, db, make_project, make_k8s_cluster):
        """When the existing context is no longer in the refreshed kubeconfig, update to current-context."""
        from models.ssh_credential import SSHCredential

        project = make_project()
        cred = SSHCredential(name="ctx-ssh", host="10.0.0.5", port=22, username="ubuntu", auth_type="key")
        db.add(cred)
        db.flush()

        cluster = make_k8s_cluster(
            project=project, name="stale-ctx", cloud_provider="on-prem",
            context="old-context-that-no-longer-exists", ssh_credential_id=cred.id,
        )

        mock_ssh_svc.return_value.probe_kubeconfig.return_value = {
            "success": True, "kubeconfig": SAMPLE_KUBECONFIG_B64, "contexts": [], "host": "10.0.0.5",
        }

        svc = ClusterManagementService(db)
        svc.refresh_kubeconfig(cluster.id)
        # Context should update to current-context from the kubeconfig
        assert cluster.context == "test-context"


# ---------------------------------------------------------------------------
# Legacy SSH cleanup (K8S-013)
# ---------------------------------------------------------------------------

class TestLegacySSHCleanup:
    """Verify legacy ssh_credential_template_id DB column is never populated by new code."""

    def test_create_never_sets_legacy_template_id(self, db, make_project):
        """New cluster creation never populates the legacy ssh_credential_template_id column."""
        from models.kubernetes import KubernetesCluster

        project = make_project()
        svc = ClusterManagementService(db)
        result = svc.create_cluster(project.id, _make_create_data())

        cluster = db.query(KubernetesCluster).get(result["id"])
        assert cluster.ssh_credential_template_id is None

    def test_update_sets_ssh_credential_id(self, db, make_project, make_k8s_cluster):
        """Updating ssh_credential_id works without legacy template involvement."""
        from models.ssh_credential import SSHCredential

        project = make_project()
        cred = SSHCredential(name="new-ssh", host="10.0.0.1", port=22, username="u", auth_type="key")
        db.add(cred)
        db.flush()

        cluster = make_k8s_cluster(project=project, name="test-cluster", cloud_provider="on-prem")

        svc = ClusterManagementService(db)
        svc.update_cluster(cluster.id, _make_update_data(ssh_credential_id=cred.id))

        db.refresh(cluster)
        assert cluster.ssh_credential_id == cred.id
