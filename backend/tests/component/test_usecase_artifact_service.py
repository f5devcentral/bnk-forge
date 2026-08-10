"""
Component tests for services.usecase_artifact_service (capture, apply) and
services.k8s_drift_service.check_usecase_drift — mocked k8s client + real
(SQLite) DB session, mirroring tests/component/test_config_export_service.py.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, NotFoundError
from models import UseCaseArtifact, UseCaseArtifactVersion
from services.k8s_drift_service import check_usecase_drift
from services.usecase_artifact_service import apply_usecase_artifact, capture_usecase_artifact


def _vlan(name: str, selfips: list[str], namespace: str = "spk") -> dict:
    return {
        "kind": "F5SPKVlan",
        "apiVersion": "k8s.f5net.com/v1",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"selfip_v4s": selfips},
    }


class TestCaptureUsecaseArtifact:
    """Capture -> lift -> store an immutable version; re-capture is idempotent."""

    @patch("services.usecase_artifact_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_capture_creates_artifact_and_version(
        self, mock_k8s, mock_k8s_svc, mock_fetch, db, make_k8s_cluster, make_project,
    ):
        project = make_project(name="uc-proj")
        cluster = make_k8s_cluster(project=project, name="uc-cluster")
        mock_fetch.return_value = [_vlan("vlan1", ["10.0.0.1/24"])]
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()

        version, created = capture_usecase_artifact(db, cluster.id, name="east-west", version="v1")
        db.flush()

        assert created is True
        assert version.version == "v1"
        assert version.source == "captured_from_cluster"
        assert version.source_cluster_id == cluster.id
        assert version.cr_templates[0]["spec"]["selfip_v4s"] == "${selfip_v4s}"
        assert version.param_schema[0]["key"] == "selfip_v4s"

        artifact = db.query(UseCaseArtifact).filter(UseCaseArtifact.id == version.artifact_id).first()
        assert artifact.name == "east-west"

    @patch("services.usecase_artifact_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_recapture_unchanged_shape_returns_existing_version(
        self, mock_k8s, mock_k8s_svc, mock_fetch, db, make_k8s_cluster, make_project,
    ):
        project = make_project(name="uc-proj-2")
        cluster = make_k8s_cluster(project=project, name="uc-cluster-2")
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()

        mock_fetch.return_value = [_vlan("vlan1", ["10.0.0.1/24"])]
        first, created_first = capture_usecase_artifact(db, cluster.id, name="east-west", version="v1")
        db.flush()

        # Same shape, different concrete selfip — must dedupe to the SAME version.
        mock_fetch.return_value = [_vlan("vlan1", ["192.168.5.5/24"])]
        second, created_second = capture_usecase_artifact(db, cluster.id, name="east-west", version="v2")

        assert created_first is True
        assert created_second is False
        assert second.id == first.id

    @patch("services.usecase_artifact_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_changed_shape_creates_new_version(
        self, mock_k8s, mock_k8s_svc, mock_fetch, db, make_k8s_cluster, make_project,
    ):
        project = make_project(name="uc-proj-3")
        cluster = make_k8s_cluster(project=project, name="uc-cluster-3")
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()

        mock_fetch.return_value = [_vlan("vlan1", ["10.0.0.1/24"])]
        first, _ = capture_usecase_artifact(db, cluster.id, name="east-west", version="v1")
        db.flush()

        mock_fetch.return_value = [_vlan("vlan1", ["10.0.0.1/24"]), _vlan("vlan2", ["10.0.0.2/24"])]
        second, created = capture_usecase_artifact(db, cluster.id, name="east-west", version="v2")

        assert created is True
        assert second.id != first.id
        assert second.version == "v2"

    def test_cluster_not_found_raises(self, db):
        with pytest.raises(NotFoundError):
            capture_usecase_artifact(db, 99999, name="east-west", version="v1")


class TestApplyUsecaseArtifact:
    """Apply renders concrete CRs and calls the shared apply_resources write path."""

    @patch("services.config_export_service.apply_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_apply_calls_shared_write_path_with_rendered_crs(
        self, mock_k8s, mock_k8s_svc, mock_apply, db, make_k8s_cluster, make_project,
    ):
        project = make_project(name="uc-proj-4")
        cluster = make_k8s_cluster(project=project, name="uc-cluster-4")
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()
        mock_apply.return_value = {"applied": [{"kind": "F5SPKVlan"}], "failed": [], "skipped": []}

        artifact = UseCaseArtifact(name="east-west")
        db.add(artifact)
        db.flush()
        version = UseCaseArtifactVersion(
            artifact_id=artifact.id,
            version="v1",
            cr_templates=[_vlan("vlan1", "${selfip_v4s}")],
            param_schema=[{
                "key": "selfip_v4s", "type": "ip", "kind": "assigned", "is_list": True,
                "required": True, "source_paths": [{"kind": "F5SPKVlan", "jsonpath": "spec.selfip_v4s"}],
            }],
            source="captured_from_cluster",
            source_cluster_id=cluster.id,
            content_hash="abc123",
        )
        db.add(version)
        db.flush()

        results, application = apply_usecase_artifact(db, cluster, version, {"selfip_v4s": ["10.9.9.9/24"]})

        assert results["applied"] == [{"kind": "F5SPKVlan"}]
        mock_apply.assert_called_once()
        called_resources = mock_apply.call_args[0][3]
        assert called_resources["bnk_data_plane"][0]["spec"]["selfip_v4s"] == ["10.9.9.9/24"]

        assert application.artifact_version_id == version.id
        assert application.cluster_id == cluster.id
        assert application.param_values == {"selfip_v4s": ["10.9.9.9/24"]}

    def test_apply_missing_required_param_raises_before_touching_k8s(self, db, make_k8s_cluster, make_project):
        project = make_project(name="uc-proj-5")
        cluster = make_k8s_cluster(project=project, name="uc-cluster-5")

        artifact = UseCaseArtifact(name="east-west-2")
        db.add(artifact)
        db.flush()
        version = UseCaseArtifactVersion(
            artifact_id=artifact.id,
            version="v1",
            cr_templates=[_vlan("vlan1", "${selfip_v4s}")],
            param_schema=[{
                "key": "selfip_v4s", "type": "ip", "kind": "assigned", "is_list": True,
                "required": True, "source_paths": [{"kind": "F5SPKVlan", "jsonpath": "spec.selfip_v4s"}],
            }],
            source="captured_from_cluster",
            source_cluster_id=cluster.id,
            content_hash="def456",
        )
        db.add(version)
        db.flush()

        with pytest.raises(BadRequestError):
            apply_usecase_artifact(db, cluster, version, {})


class TestCheckUsecaseDrift:
    """Drift closes the k8s_drift desired-state stub — real _diff_dicts, not 'not available'."""

    def _make_version(self, db, cluster_id):
        artifact = UseCaseArtifact(name="drift-artifact")
        db.add(artifact)
        db.flush()
        version = UseCaseArtifactVersion(
            artifact_id=artifact.id,
            version="v1",
            cr_templates=[_vlan("vlan1", "${selfip_v4s}")],
            param_schema=[{
                "key": "selfip_v4s", "type": "ip", "kind": "assigned", "is_list": True,
                "required": True, "source_paths": [{"kind": "F5SPKVlan", "jsonpath": "spec.selfip_v4s"}],
            }],
            source="captured_from_cluster",
            source_cluster_id=cluster_id,
            content_hash="ghi789",
        )
        db.add(version)
        db.flush()
        return version

    @patch("services.config_export_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_no_drift_when_actual_matches_rendered(
        self, mock_k8s, mock_k8s_svc, mock_fetch, db, make_k8s_cluster, make_project,
    ):
        project = make_project(name="uc-proj-6")
        cluster = make_k8s_cluster(project=project, name="uc-cluster-6")
        version = self._make_version(db, cluster.id)

        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()
        mock_fetch.return_value = [_vlan("vlan1", ["10.9.9.9/24"])]

        result = check_usecase_drift(db, cluster, version, {"selfip_v4s": ["10.9.9.9/24"]})

        assert result["drift_detected"] is False
        assert result["resource_changes"]["ok"] == 1
        assert result["resource_changes"]["change"] == 0

    @patch("services.config_export_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_drift_detected_when_actual_selfip_differs(
        self, mock_k8s, mock_k8s_svc, mock_fetch, db, make_k8s_cluster, make_project,
    ):
        project = make_project(name="uc-proj-7")
        cluster = make_k8s_cluster(project=project, name="uc-cluster-7")
        version = self._make_version(db, cluster.id)

        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()
        mock_fetch.return_value = [_vlan("vlan1", ["192.168.99.99/24"])]

        result = check_usecase_drift(db, cluster, version, {"selfip_v4s": ["10.9.9.9/24"]})

        assert result["drift_detected"] is True
        assert result["resource_changes"]["change"] == 1
        assert result["changed_resources"][0]["diffs"][0]["path"] == "spec.selfip_v4s[0]"

    @patch("services.config_export_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_drift_detected_when_resource_missing_on_cluster(
        self, mock_k8s, mock_k8s_svc, mock_fetch, db, make_k8s_cluster, make_project,
    ):
        project = make_project(name="uc-proj-8")
        cluster = make_k8s_cluster(project=project, name="uc-cluster-8")
        version = self._make_version(db, cluster.id)

        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()
        mock_fetch.return_value = []

        result = check_usecase_drift(db, cluster, version, {"selfip_v4s": ["10.9.9.9/24"]})

        assert result["drift_detected"] is True
        assert result["resource_changes"]["add"] == 1
