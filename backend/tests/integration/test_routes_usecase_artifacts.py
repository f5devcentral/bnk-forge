"""
Integration tests for use-case artifact routes (D-034 Phase 0 tracer) —
/api/clusters/{id}/usecase-artifacts/capture, .../usecase-artifact-versions/{id}/apply,
.../usecase-artifact-versions/{id}/drift.

Uses FastAPI TestClient with real SQLite DB. K8s client is mocked at the
service-module import sites; capture/render/apply/drift run for real.
"""

from unittest.mock import MagicMock, patch

from models import UseCaseArtifact, UseCaseArtifactVersion


def _vlan(name: str, selfips) -> dict:
    return {
        "kind": "F5SPKVlan",
        "apiVersion": "k8s.f5net.com/v1",
        "metadata": {"name": name, "namespace": "spk"},
        "spec": {"selfip_v4s": selfips},
    }


class TestCaptureRoute:
    """POST /api/clusters/{cluster_id}/usecase-artifacts/capture."""

    @patch("services.usecase_artifact_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_capture_creates_version(
        self, mock_k8s, mock_k8s_svc, mock_fetch,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="capture-cluster")
        mock_fetch.return_value = [_vlan("vlan1", ["10.0.0.1/24"])]
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifacts/capture",
            json={"name": "east-west", "version": "v1"},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["already_captured"] is False
        assert data["version"]["version"] == "v1"
        assert data["version"]["cr_templates"][0]["spec"]["selfip_v4s"] == "${selfip_v4s}"
        assert data["version"]["param_schema"][0]["key"] == "selfip_v4s"
        assert data["version"]["created_by"] is not None

    @patch("services.usecase_artifact_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_recapture_unchanged_shape_is_idempotent(
        self, mock_k8s, mock_k8s_svc, mock_fetch,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="capture-cluster-2")
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()

        mock_fetch.return_value = [_vlan("vlan1", ["10.0.0.1/24"])]
        first = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifacts/capture",
            json={"name": "east-west-2", "version": "v1"},
            headers=operator_headers,
        )
        assert first.json()["already_captured"] is False

        mock_fetch.return_value = [_vlan("vlan1", ["192.168.9.9/24"])]
        second = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifacts/capture",
            json={"name": "east-west-2", "version": "v2"},
            headers=operator_headers,
        )
        assert second.status_code == 200
        assert second.json()["already_captured"] is True
        assert second.json()["version"]["id"] == first.json()["version"]["id"]

    def test_capture_cluster_not_found(self, client, operator_headers, all_test_users):
        response = client.post(
            "/api/clusters/99999/usecase-artifacts/capture",
            json={"name": "east-west", "version": "v1"},
            headers=operator_headers,
        )
        assert response.status_code == 404

    def test_capture_viewer_forbidden(
        self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster
    ):
        cluster = make_k8s_cluster(project=sample_project, name="capture-cluster-rbac-1")
        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifacts/capture",
            json={"name": "east-west", "version": "v1"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_capture_unauthenticated(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="capture-cluster-rbac-2")
        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifacts/capture",
            json={"name": "east-west", "version": "v1"},
        )
        assert response.status_code == 401


class TestApplyRoute:
    """POST /api/clusters/{cluster_id}/usecase-artifact-versions/{version_id}/apply."""

    @staticmethod
    def _make_version(db, cluster_id):
        artifact = UseCaseArtifact(name="apply-artifact")
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
            content_hash="apply-hash",
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version

    @patch("services.config_export_service.apply_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_apply_sends_rendered_concrete_selfips(
        self, mock_k8s, mock_k8s_svc, mock_apply,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster, db,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="apply-cluster")
        version = self._make_version(db, cluster.id)
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()
        mock_apply.return_value = {"applied": [{"kind": "F5SPKVlan"}], "failed": [], "skipped": []}

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifact-versions/{version.id}/apply",
            json={"param_values": {"selfip_v4s": ["10.42.42.1/24"]}},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["results"]["applied"] == [{"kind": "F5SPKVlan"}]
        assert data["application"]["param_values"] == {"selfip_v4s": ["10.42.42.1/24"]}

        # The shared write path must have received RENDERED concrete selfips, not the token.
        mock_apply.assert_called_once()
        applied_resources = mock_apply.call_args[0][3]
        assert applied_resources["bnk_data_plane"][0]["spec"]["selfip_v4s"] == ["10.42.42.1/24"]

    @patch("services.config_export_service.apply_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_apply_sets_applied_by(
        self, mock_k8s, mock_k8s_svc, mock_apply,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster, db,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="apply-cluster-applied-by")
        version = self._make_version(db, cluster.id)
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()
        mock_apply.return_value = {"applied": [{"kind": "F5SPKVlan"}], "failed": [], "skipped": []}

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifact-versions/{version.id}/apply",
            json={"param_values": {"selfip_v4s": ["10.42.42.1/24"]}},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["application"]["applied_by"] is not None

    def test_apply_missing_required_param_returns_400(
        self, client, operator_headers, all_test_users, sample_project, make_k8s_cluster, db,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="apply-cluster-2")
        version = self._make_version(db, cluster.id)

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifact-versions/{version.id}/apply",
            json={"param_values": {}},
            headers=operator_headers,
        )
        assert response.status_code == 400

    def test_apply_viewer_forbidden(
        self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster, db,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="apply-cluster-3")
        version = self._make_version(db, cluster.id)

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifact-versions/{version.id}/apply",
            json={"param_values": {"selfip_v4s": ["10.0.0.1/24"]}},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_apply_unauthenticated(self, client, sample_project, make_k8s_cluster, db):
        cluster = make_k8s_cluster(project=sample_project, name="apply-cluster-4")
        version = self._make_version(db, cluster.id)

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifact-versions/{version.id}/apply",
            json={"param_values": {"selfip_v4s": ["10.0.0.1/24"]}},
        )
        assert response.status_code == 401


class TestDriftRoute:
    """POST /api/clusters/{cluster_id}/usecase-artifact-versions/{version_id}/drift."""

    @staticmethod
    def _make_version(db, cluster_id):
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
            content_hash="drift-hash",
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version

    @patch("services.config_export_service._fetch_resources")
    @patch("services.kubernetes_service.KubernetesService")
    @patch("kubernetes.client")
    def test_drift_detected_returns_real_report(
        self, mock_k8s, mock_k8s_svc, mock_fetch,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster, db,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="drift-cluster")
        version = self._make_version(db, cluster.id)
        mock_k8s_svc.return_value.load_kubeconfig.return_value = MagicMock()
        mock_k8s.CustomObjectsApi.return_value = MagicMock()
        mock_fetch.return_value = [_vlan("vlan1", ["192.168.1.1/24"])]

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifact-versions/{version.id}/drift",
            json={"param_values": {"selfip_v4s": ["10.0.0.1/24"]}},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["drift_detected"] is True
        assert data["resource_changes"]["change"] == 1
        assert data["summary"] != "K8s drift check not available"

    def test_drift_viewer_forbidden(
        self, client, viewer_headers, all_test_users, sample_project, make_k8s_cluster, db,
    ):
        cluster = make_k8s_cluster(project=sample_project, name="drift-cluster-2")
        version = self._make_version(db, cluster.id)

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifact-versions/{version.id}/drift",
            json={"param_values": {"selfip_v4s": ["10.0.0.1/24"]}},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_drift_unauthenticated(self, client, sample_project, make_k8s_cluster, db):
        cluster = make_k8s_cluster(project=sample_project, name="drift-cluster-3")
        version = self._make_version(db, cluster.id)

        response = client.post(
            f"/api/clusters/{cluster.id}/usecase-artifact-versions/{version.id}/drift",
            json={"param_values": {"selfip_v4s": ["10.0.0.1/24"]}},
        )
        assert response.status_code == 401
