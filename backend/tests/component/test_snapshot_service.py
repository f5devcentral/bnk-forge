"""
Tests for services.snapshot_service — config snapshots, restore, and diff.

UX-011: Snapshot service — real DB, mock only external services (K8s, config export).
"""

from unittest.mock import MagicMock, patch

import pytest

from models import ConfigSnapshot, KubernetesCluster
from services.snapshot_service import SnapshotService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_snapshot(db, project, *, cluster=None, trigger="manual", label=None,
                   created_by=None, config_data=None, resource_count=0,
                   module_count=0):
    """Insert a ConfigSnapshot directly into the DB for test setup."""
    snap = ConfigSnapshot(
        project_id=project.id,
        cluster_id=cluster.id if cluster else None,
        trigger=trigger,
        label=label,
        created_by=created_by,
        config_data=config_data or {"resources": {}, "module_config": {}},
        resource_count=resource_count,
        module_count=module_count,
    )
    db.add(snap)
    db.flush()
    db.refresh(snap)
    return snap


def _cluster_export_data(*, resources=None, module_config=None):
    """Build a realistic cluster export payload."""
    return {
        "bnk_forge_export": {
            "version": "1.0",
            "exported_at": "2025-01-01T00:00:00Z",
            "cluster": {"name": "test-cluster", "id": 1},
            "project": {"name": "test-project", "id": 1, "project_type": "kubernetes"},
            "export_metadata": {
                "resource_counts": {"Gateway": 1},
                "total_resources": 1,
                "categories": ["gateway"],
                "snapshot_type": "full",
            },
        },
        "resources": resources or {},
        "module_config": module_config or {},
    }


# ---------------------------------------------------------------------------
# create_snapshot
# ---------------------------------------------------------------------------

class TestCreateSnapshot:
    """Snapshot creation from project config state."""

    def test_create_module_only_snapshot(self, db, make_project, make_module_library, make_project_module):
        """No cluster attached — falls back to module-only export."""
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        make_project_module(project=project, library_module=lib, variables={"cidr": "10.0.0.0/16"})

        svc = SnapshotService(db)
        snap = svc.create_snapshot(project.id, trigger="manual", label="v1", username="tester")

        assert snap.id is not None
        assert snap.project_id == project.id
        assert snap.cluster_id is None
        assert snap.trigger == "manual"
        assert snap.label == "v1"
        assert snap.created_by == "tester"
        assert snap.module_count == 1
        assert snap.resource_count == 0
        assert "module_config" in snap.config_data

    @patch("services.config_export_service.export_cluster_config")
    def test_create_cluster_snapshot(self, mock_export, db, make_project, make_k8s_cluster):
        """Cluster attached — uses full cluster export."""
        project = make_project()
        cluster = make_k8s_cluster(project=project)
        export_data = _cluster_export_data(
            resources={"gateway": [{"kind": "Gateway", "metadata": {"name": "gw1"}}]},
            module_config={"modules/vpc": {"variables": {}, "status": "applied", "outputs": {}}},
        )
        mock_export.return_value = export_data

        svc = SnapshotService(db)
        snap = svc.create_snapshot(project.id)

        mock_export.assert_called_once_with(cluster.id, db)
        assert snap.cluster_id == cluster.id
        assert snap.resource_count == 1
        assert snap.module_count == 1

    @patch("services.config_export_service.export_cluster_config")
    def test_create_snapshot_with_explicit_cluster_id(self, mock_export, db, make_project, make_k8s_cluster):
        """Caller provides cluster_id directly."""
        project = make_project()
        cluster = make_k8s_cluster(project=project)
        mock_export.return_value = _cluster_export_data()

        svc = SnapshotService(db)
        snap = svc.create_snapshot(project.id, cluster_id=cluster.id)

        assert snap.cluster_id == cluster.id
        mock_export.assert_called_once_with(cluster.id, db)

    @patch("services.config_export_service.export_cluster_config", side_effect=Exception("k8s unreachable"))
    def test_create_snapshot_cluster_export_fails_falls_back(self, mock_export, db, make_project, make_k8s_cluster):
        """Cluster export failure falls back to module-only export."""
        project = make_project()
        make_k8s_cluster(project=project)

        svc = SnapshotService(db)
        snap = svc.create_snapshot(project.id)

        assert snap.config_data["bnk_forge_export"]["export_metadata"]["snapshot_type"] == "module_only"

    def test_create_snapshot_project_not_found(self, db):
        svc = SnapshotService(db)
        with pytest.raises(ValueError, match="Project 99999 not found"):
            svc.create_snapshot(99999)

    @patch("services.config_export_service.export_cluster_config")
    def test_create_snapshot_counts_multiple_resource_categories(self, mock_export, db, make_project, make_k8s_cluster):
        """resource_count sums across all categories."""
        project = make_project()
        make_k8s_cluster(project=project)
        mock_export.return_value = _cluster_export_data(
            resources={
                "gateway": [{"kind": "Gateway"}, {"kind": "Gateway"}],
                "firewall": [{"kind": "FirewallPolicy"}],
            },
            module_config={"a": {}, "b": {}, "c": {}},
        )

        svc = SnapshotService(db)
        snap = svc.create_snapshot(project.id)

        assert snap.resource_count == 3
        assert snap.module_count == 3

    def test_create_snapshot_auto_trigger(self, db, make_project):
        """Trigger values are persisted correctly."""
        project = make_project()
        svc = SnapshotService(db)
        snap = svc.create_snapshot(project.id, trigger="auto-before-apply")
        assert snap.trigger == "auto-before-apply"


# ---------------------------------------------------------------------------
# _export_modules_only
# ---------------------------------------------------------------------------

class TestExportModulesOnly:
    """Module-only export format."""

    def test_empty_project(self, db, make_project):
        project = make_project()
        svc = SnapshotService(db)
        data = svc._export_modules_only(project.id)

        assert data["bnk_forge_export"]["export_metadata"]["snapshot_type"] == "module_only"
        assert data["resources"] == {}
        assert data["module_config"] == {}

    def test_exports_module_variables_and_status(self, db, make_project, make_module_library, make_project_module):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        make_project_module(
            project=project, library_module=lib,
            variables={"cidr": "10.0.0.0/16"}, status="applied",
            outputs={"vpc_id": "vpc-123"},
        )

        svc = SnapshotService(db)
        data = svc._export_modules_only(project.id)

        assert "modules/vpc" in data["module_config"]
        mod = data["module_config"]["modules/vpc"]
        assert mod["variables"] == {"cidr": "10.0.0.0/16"}
        assert mod["status"] == "applied"
        assert mod["outputs"] == {"vpc_id": "vpc-123"}

    def test_includes_project_metadata(self, db, make_project):
        project = make_project(name="my-proj", project_type="kubernetes")
        svc = SnapshotService(db)
        data = svc._export_modules_only(project.id)

        proj_meta = data["bnk_forge_export"]["project"]
        assert proj_meta["name"] == "my-proj"
        assert proj_meta["id"] == project.id
        assert proj_meta["project_type"] == "kubernetes"


# ---------------------------------------------------------------------------
# list_snapshots
# ---------------------------------------------------------------------------

class TestListSnapshots:
    """Paginated snapshot listing."""

    def test_list_empty(self, db, make_project):
        project = make_project()
        svc = SnapshotService(db)
        snapshots, total = svc.list_snapshots(project.id)
        assert snapshots == []
        assert total == 0

    def test_list_returns_project_snapshots(self, db, make_project):
        project = make_project()
        _make_snapshot(db, project, label="s1")
        _make_snapshot(db, project, label="s2")

        svc = SnapshotService(db)
        snapshots, total = svc.list_snapshots(project.id)
        assert total == 2
        assert len(snapshots) == 2

    def test_list_filters_by_project(self, db, make_project):
        p1 = make_project()
        p2 = make_project()
        _make_snapshot(db, p1, label="p1-snap")
        _make_snapshot(db, p2, label="p2-snap")

        svc = SnapshotService(db)
        snapshots, total = svc.list_snapshots(p1.id)
        assert total == 1
        assert snapshots[0].label == "p1-snap"

    def test_list_ordered_newest_first(self, db, make_project):
        project = make_project()
        s1 = _make_snapshot(db, project, label="first")
        s2 = _make_snapshot(db, project, label="second")

        svc = SnapshotService(db)
        snapshots, _ = svc.list_snapshots(project.id)
        # Most recent (highest id in same txn) should come first
        assert snapshots[0].id >= snapshots[1].id

    def test_list_pagination_limit(self, db, make_project):
        project = make_project()
        for i in range(5):
            _make_snapshot(db, project, label=f"snap-{i}")

        svc = SnapshotService(db)
        snapshots, total = svc.list_snapshots(project.id, limit=2)
        assert total == 5
        assert len(snapshots) == 2

    def test_list_pagination_offset(self, db, make_project):
        project = make_project()
        for i in range(5):
            _make_snapshot(db, project, label=f"snap-{i}")

        svc = SnapshotService(db)
        snapshots, total = svc.list_snapshots(project.id, limit=2, offset=3)
        assert total == 5
        assert len(snapshots) == 2


# ---------------------------------------------------------------------------
# get_snapshot
# ---------------------------------------------------------------------------

class TestGetSnapshot:
    """Single snapshot retrieval."""

    def test_get_existing(self, db, make_project):
        project = make_project()
        snap = _make_snapshot(db, project, label="found-me")

        svc = SnapshotService(db)
        result = svc.get_snapshot(snap.id)
        assert result is not None
        assert result.label == "found-me"

    def test_get_not_found(self, db):
        svc = SnapshotService(db)
        result = svc.get_snapshot(99999)
        assert result is None


# ---------------------------------------------------------------------------
# restore_snapshot
# ---------------------------------------------------------------------------

class TestRestoreSnapshot:
    """Snapshot restore via K8s custom-object patching."""

    def test_restore_not_found(self, db):
        svc = SnapshotService(db)
        with pytest.raises(ValueError, match="not found"):
            svc.restore_snapshot(99999)

    def test_restore_no_cluster_raises(self, db, make_project):
        """Module-only snapshots cannot be restored."""
        project = make_project()
        snap = _make_snapshot(db, project, cluster=None)

        svc = SnapshotService(db)
        with pytest.raises(ValueError, match="no associated cluster"):
            svc.restore_snapshot(snap.id)

    def test_restore_empty_resources(self, db, make_project, make_k8s_cluster):
        """Snapshot with no resources returns early."""
        project = make_project()
        cluster = make_k8s_cluster(project=project)
        snap = _make_snapshot(db, project, cluster=cluster, config_data={
            "resources": {},
            "module_config": {},
        })

        svc = SnapshotService(db)
        result = svc.restore_snapshot(snap.id)
        assert "no cluster resources" in result["message"].lower()
        assert result["results"]["applied"] == []

    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_restore_applies_namespaced_resource(self, mock_k8s_client, mock_k8s_svc_cls,
                                                  db, make_project, make_k8s_cluster):
        """Namespaced CRD resource is patched via CustomObjectsApi."""
        project = make_project()
        cluster = make_k8s_cluster(project=project)

        resource = {
            "apiVersion": "k8s.f5net.com/v1",
            "kind": "F5BigIpCtlr",
            "metadata": {"name": "ctlr1", "namespace": "bigip"},
        }
        snap = _make_snapshot(db, project, cluster=cluster, config_data={
            "resources": {"controller": [resource]},
            "module_config": {},
        })

        mock_api_client = MagicMock()
        mock_k8s_svc_cls.return_value.load_kubeconfig.return_value = mock_api_client
        mock_custom_api = MagicMock()
        mock_k8s_client.CustomObjectsApi.return_value = mock_custom_api

        svc = SnapshotService(db)
        result = svc.restore_snapshot(snap.id)

        assert len(result["results"]["applied"]) == 1
        assert result["results"]["applied"][0]["name"] == "ctlr1"
        mock_custom_api.patch_namespaced_custom_object.assert_called_once()

    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_restore_applies_cluster_scoped_resource(self, mock_k8s_client, mock_k8s_svc_cls,
                                                      db, make_project, make_k8s_cluster):
        """Cluster-scoped CRD (no namespace) uses patch_cluster_custom_object."""
        project = make_project()
        cluster = make_k8s_cluster(project=project)

        resource = {
            "apiVersion": "crd.projectcalico.org/v1",
            "kind": "BGPConfiguration",
            "metadata": {"name": "default"},
        }
        snap = _make_snapshot(db, project, cluster=cluster, config_data={
            "resources": {"calico": [resource]},
            "module_config": {},
        })

        mock_api_client = MagicMock()
        mock_k8s_svc_cls.return_value.load_kubeconfig.return_value = mock_api_client
        mock_custom_api = MagicMock()
        mock_k8s_client.CustomObjectsApi.return_value = mock_custom_api

        svc = SnapshotService(db)
        result = svc.restore_snapshot(snap.id)

        assert len(result["results"]["applied"]) == 1
        mock_custom_api.patch_cluster_custom_object.assert_called_once()

    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_restore_skips_core_api_resources(self, mock_k8s_client, mock_k8s_svc_cls,
                                               db, make_project, make_k8s_cluster):
        """Resources with no group (core API like v1) are skipped."""
        project = make_project()
        cluster = make_k8s_cluster(project=project)

        resource = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "cm1", "namespace": "default"},
        }
        snap = _make_snapshot(db, project, cluster=cluster, config_data={
            "resources": {"core": [resource]},
            "module_config": {},
        })

        mock_api_client = MagicMock()
        mock_k8s_svc_cls.return_value.load_kubeconfig.return_value = mock_api_client
        mock_k8s_client.CustomObjectsApi.return_value = MagicMock()

        svc = SnapshotService(db)
        result = svc.restore_snapshot(snap.id)

        assert len(result["results"]["skipped"]) == 1
        assert "Core API" in result["results"]["skipped"][0]["reason"]

    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_restore_handles_api_exception_404(self, mock_k8s_client, mock_k8s_svc_cls,
                                                db, make_project, make_k8s_cluster):
        """ApiException with 404 status is reported as skipped (CRD not installed)."""
        from kubernetes.client.rest import ApiException

        project = make_project()
        cluster = make_k8s_cluster(project=project)

        resource = {
            "apiVersion": "k8s.f5net.com/v1",
            "kind": "TransportServer",
            "metadata": {"name": "ts1", "namespace": "f5"},
        }
        snap = _make_snapshot(db, project, cluster=cluster, config_data={
            "resources": {"transport": [resource]},
            "module_config": {},
        })

        mock_api_client = MagicMock()
        mock_k8s_svc_cls.return_value.load_kubeconfig.return_value = mock_api_client
        mock_custom_api = MagicMock()
        mock_custom_api.patch_namespaced_custom_object.side_effect = ApiException(status=404, reason="Not Found")
        mock_k8s_client.CustomObjectsApi.return_value = mock_custom_api

        svc = SnapshotService(db)
        result = svc.restore_snapshot(snap.id)

        assert len(result["results"]["skipped"]) == 1
        assert "CRD not installed" in result["results"]["skipped"][0]["reason"]

    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_restore_handles_api_exception_other(self, mock_k8s_client, mock_k8s_svc_cls,
                                                  db, make_project, make_k8s_cluster):
        """Non-404 ApiException is reported as failed."""
        from kubernetes.client.rest import ApiException

        project = make_project()
        cluster = make_k8s_cluster(project=project)

        resource = {
            "apiVersion": "k8s.f5net.com/v1",
            "kind": "VirtualServer",
            "metadata": {"name": "vs1", "namespace": "f5"},
        }
        snap = _make_snapshot(db, project, cluster=cluster, config_data={
            "resources": {"virtual": [resource]},
            "module_config": {},
        })

        mock_api_client = MagicMock()
        mock_k8s_svc_cls.return_value.load_kubeconfig.return_value = mock_api_client
        mock_custom_api = MagicMock()
        mock_custom_api.patch_namespaced_custom_object.side_effect = ApiException(status=403, reason="Forbidden")
        mock_k8s_client.CustomObjectsApi.return_value = mock_custom_api

        svc = SnapshotService(db)
        result = svc.restore_snapshot(snap.id)

        assert len(result["results"]["failed"]) == 1
        assert "Forbidden" in result["results"]["failed"][0]["error"]

    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_restore_returns_summary_and_metadata(self, mock_k8s_client, mock_k8s_svc_cls,
                                                   db, make_project, make_k8s_cluster):
        """Result includes message, snapshot_id, and target_cluster."""
        project = make_project()
        cluster = make_k8s_cluster(project=project, name="prod-cluster")

        resource = {
            "apiVersion": "k8s.f5net.com/v1",
            "kind": "F5BigIpCtlr",
            "metadata": {"name": "ctlr", "namespace": "bigip"},
        }
        snap = _make_snapshot(db, project, cluster=cluster, config_data={
            "resources": {"ctrl": [resource]},
            "module_config": {},
        })

        mock_api_client = MagicMock()
        mock_k8s_svc_cls.return_value.load_kubeconfig.return_value = mock_api_client
        mock_k8s_client.CustomObjectsApi.return_value = MagicMock()

        svc = SnapshotService(db)
        result = svc.restore_snapshot(snap.id)

        assert result["snapshot_id"] == snap.id
        assert result["target_cluster"] == "prod-cluster"
        assert "1 applied" in result["message"]

    def test_restore_cluster_deleted(self, db, make_project, make_k8s_cluster):
        """Cluster row removed after snapshot was taken raises ValueError."""
        project = make_project()
        cluster = make_k8s_cluster(project=project)
        snap = _make_snapshot(db, project, cluster=cluster, config_data={
            "resources": {"gw": [{"apiVersion": "x/v1", "kind": "GW", "metadata": {"name": "g"}}]},
            "module_config": {},
        })

        # Delete the cluster row (simulate cluster removal)
        db.query(KubernetesCluster).filter(KubernetesCluster.id == cluster.id).delete()
        db.flush()

        svc = SnapshotService(db)
        with pytest.raises(ValueError, match="no longer exists"):
            svc.restore_snapshot(snap.id)


# ---------------------------------------------------------------------------
# diff_snapshots
# ---------------------------------------------------------------------------

class TestDiffSnapshots:
    """Snapshot comparison via config diff."""

    @patch("services.config_export_service.diff_configs")
    def test_diff_returns_enhanced_result(self, mock_diff, db, make_project):
        project = make_project()
        s1 = _make_snapshot(db, project, trigger="manual", label="before")
        s2 = _make_snapshot(db, project, trigger="auto-before-apply", label="after")

        mock_diff.return_value = {"added": [], "removed": [], "changed": []}

        svc = SnapshotService(db)
        result = svc.diff_snapshots(s1.id, s2.id)

        mock_diff.assert_called_once_with(s1.config_data, s2.config_data)
        assert result["snapshot_a"]["id"] == s1.id
        assert result["snapshot_a"]["trigger"] == "manual"
        assert result["snapshot_a"]["label"] == "before"
        assert result["snapshot_b"]["id"] == s2.id
        assert result["snapshot_b"]["trigger"] == "auto-before-apply"
        assert result["snapshot_b"]["label"] == "after"

    @patch("services.config_export_service.diff_configs")
    def test_diff_passes_config_data(self, mock_diff, db, make_project):
        project = make_project()
        data_a = {"resources": {"gw": [1]}, "module_config": {"a": {}}}
        data_b = {"resources": {"gw": [1, 2]}, "module_config": {"a": {}, "b": {}}}
        s1 = _make_snapshot(db, project, config_data=data_a)
        s2 = _make_snapshot(db, project, config_data=data_b)

        mock_diff.return_value = {"added": ["b"], "removed": [], "changed": []}

        svc = SnapshotService(db)
        svc.diff_snapshots(s1.id, s2.id)

        mock_diff.assert_called_once_with(data_a, data_b)

    def test_diff_snapshot_a_not_found(self, db, make_project):
        project = make_project()
        s2 = _make_snapshot(db, project)

        svc = SnapshotService(db)
        with pytest.raises(ValueError, match="99999 not found"):
            svc.diff_snapshots(99999, s2.id)

    def test_diff_snapshot_b_not_found(self, db, make_project):
        project = make_project()
        s1 = _make_snapshot(db, project)

        svc = SnapshotService(db)
        with pytest.raises(ValueError, match="99999 not found"):
            svc.diff_snapshots(s1.id, 99999)


# ---------------------------------------------------------------------------
# delete_snapshot
# ---------------------------------------------------------------------------

class TestDeleteSnapshot:
    """Snapshot deletion."""

    def test_delete_existing(self, db, make_project):
        project = make_project()
        snap = _make_snapshot(db, project, label="doomed")

        svc = SnapshotService(db)
        result = svc.delete_snapshot(snap.id)

        assert result is True
        assert svc.get_snapshot(snap.id) is None

    def test_delete_not_found(self, db):
        svc = SnapshotService(db)
        result = svc.delete_snapshot(99999)
        assert result is False

    def test_delete_does_not_affect_other_snapshots(self, db, make_project):
        project = make_project()
        s1 = _make_snapshot(db, project, label="keep")
        s2 = _make_snapshot(db, project, label="delete")

        svc = SnapshotService(db)
        svc.delete_snapshot(s2.id)

        assert svc.get_snapshot(s1.id) is not None
        assert svc.get_snapshot(s2.id) is None
