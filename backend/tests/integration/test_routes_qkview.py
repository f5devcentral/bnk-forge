"""
Integration tests for QKView routes — /api/qkview/*.

Tests auth (viewer vs operator), request validation, and response shape.
All qkview_service functions are mocked at the route level.
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.qkview_service import QKViewError

# ── Sample response data ─────────────────────────────────────────────

_SAMPLE_CWC_CHECK = {"available": True, "message": "CWC is available"}
_SAMPLE_CWC_UNAVAIL = {"available": False, "message": "Namespace 'f5-utils' not found"}

_SAMPLE_SETUP_STATUS = {
    "setup_complete": True,
    "cert_manager_available": True,
    "server_cert_exists": True,
    "client_cert_exists": True,
    "message": "CWC API mTLS setup is complete",
}

_SAMPLE_SETUP_RESULT = {
    "success": True,
    "message": "CWC API mTLS setup complete.",
    "steps": [
        {"step": "cert_manager_check", "status": "ok"},
        {"step": "server_cert_create", "status": "ok"},
    ],
}

_SAMPLE_QKVIEW = {"id": "qk-abc123", "status": "Completed", "filename": "qkview.tar.gz"}
_SAMPLE_QKVIEW_LIST = [_SAMPLE_QKVIEW]
_SAMPLE_QKVIEW_STATUS = {"status": "Completed", "progress": 100}


# ═══════════════════════════════════════════════════════════════════════
# Check CWC Availability
# ═══════════════════════════════════════════════════════════════════════


class TestCheckCwcEndpoint:
    """GET /api/qkview/check?cluster_id=N."""

    @patch("routes.qkview.check_cwc_available", return_value=_SAMPLE_CWC_CHECK)
    @patch("routes.qkview.KubernetesService")
    def test_viewer_can_check(
        self, mock_k8s_cls, mock_check,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should be able to check CWC availability."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-check")

        resp = client.get(
            f"/api/qkview/check?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"available", "message", "operator_dispatch"}
        assert data["available"] is True
        mock_check.assert_called_once()

    def test_unauthenticated_rejected(self, client):
        """Unauthenticated access should be rejected."""
        resp = client.get("/api/qkview/check?cluster_id=1")
        assert resp.status_code == 401


class TestSetupStatusEndpoint:
    """GET /api/licensing/{cluster_id}/cwc-setup-status."""

    @patch("routes.licensing.legacy_check_cwc_api_setup_status", return_value=_SAMPLE_SETUP_STATUS)
    @patch("routes.licensing.KubernetesService")
    def test_viewer_can_check_setup(
        self, mock_k8s_cls, mock_status,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should be able to check setup status."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-setup-status")

        resp = client.get(
            f"/api/licensing/{cluster.id}/cwc-setup-status",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {
            "setup_complete",
            "cert_manager_available",
            "server_cert_exists",
            "client_cert_exists",
            "message",
            "certs_mounted",
            "operator_dispatch",
        }
        assert data["setup_complete"] is True
        assert data["cert_manager_available"] is True

    def test_unauthenticated_rejected(self, client):
        """Unauthenticated access should be rejected."""
        resp = client.get("/api/licensing/1/cwc-setup-status")
        assert resp.status_code == 401


class TestRunSetupEndpoint:
    """POST /api/licensing/{cluster_id}/cwc-setup."""

    @patch("routes.licensing.legacy_setup_cwc_api_certs", return_value=_SAMPLE_SETUP_RESULT)
    @patch("routes.licensing.KubernetesService")
    def test_operator_can_run_setup(
        self, mock_k8s_cls, mock_setup,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Operator should be able to run cert-manager setup."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-setup-run")

        resp = client.post(
            f"/api/licensing/{cluster.id}/cwc-setup",
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"success", "message", "steps", "operator_dispatch"}
        assert data["success"] is True

    @patch("routes.licensing.KubernetesService")
    def test_viewer_cannot_run_setup(
        self, mock_k8s_cls,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should NOT be able to run setup (operator-only)."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-setup-deny")

        resp = client.post(
            f"/api/licensing/{cluster.id}/cwc-setup",
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, client):
        """Unauthenticated access should be rejected."""
        resp = client.post("/api/licensing/1/cwc-setup")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════
# List QKViews
# ═══════════════════════════════════════════════════════════════════════


class TestListQkviewsEndpoint:
    """GET /api/qkview/list?cluster_id=N."""

    @patch("routes.qkview.list_qkviews", return_value=_SAMPLE_QKVIEW_LIST)
    @patch("routes.qkview.KubernetesService")
    def test_viewer_can_list(
        self, mock_k8s_cls, mock_list,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should be able to list qkviews."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-list")

        resp = client.get(
            f"/api/qkview/list?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"qkviews", "error", "operator_dispatch"}
        assert len(data["qkviews"]) == 1
        assert data["qkviews"][0]["id"] == "qk-abc123"

    @patch("routes.qkview.list_qkviews", side_effect=QKViewError("CWC connection failed"))
    @patch("routes.qkview.KubernetesService")
    def test_list_error_returns_empty_with_error(
        self, mock_k8s_cls, mock_list,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Should return empty list with error message when CWC fails."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-list-err")

        resp = client.get(
            f"/api/qkview/list?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"qkviews", "error", "operator_dispatch"}
        assert data["qkviews"] == []
        assert "error" in data


# ═══════════════════════════════════════════════════════════════════════
# Create QKView
# ═══════════════════════════════════════════════════════════════════════


class TestCreateQkviewEndpoint:
    """POST /api/qkview/create."""

    @patch("routes.qkview.create_qkview", return_value=_SAMPLE_QKVIEW)
    @patch("routes.qkview.KubernetesService")
    def test_operator_can_create(
        self, mock_k8s_cls, mock_create,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Operator should be able to create a qkview."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-create")

        resp = client.post(
            "/api/qkview/create",
            json={"cluster_id": cluster.id},
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"id", "filename", "status", "operator_dispatch"}
        assert data["id"] == "qk-abc123"

    @patch("routes.qkview.create_qkview", return_value=_SAMPLE_QKVIEW)
    @patch("routes.qkview.KubernetesService")
    def test_create_with_options(
        self, mock_k8s_cls, mock_create,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Should accept optional fields in request body."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-create-opts")

        resp = client.post(
            "/api/qkview/create",
            json={
                "cluster_id": cluster.id,
                "namespace": "f5-utils",
                "filename": "test.tar.gz",
                "pod_patterns": ["tmm-*"],
                "include_core_files": True,
                "core_files_max": 5,
            },
            headers=operator_headers,
        )
        assert resp.status_code == 200

    @patch("routes.qkview.KubernetesService")
    def test_viewer_cannot_create(
        self, mock_k8s_cls,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should NOT be able to create a qkview (operator-only)."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-create-deny")

        resp = client.post(
            "/api/qkview/create",
            json={"cluster_id": cluster.id},
            headers=viewer_headers,
        )
        assert resp.status_code == 403

    def test_unauthenticated_rejected(self, client):
        """Unauthenticated access should be rejected."""
        resp = client.post("/api/qkview/create", json={"cluster_id": 1})
        assert resp.status_code == 401

    def test_missing_cluster_id_rejected(self, client, operator_headers, all_test_users):
        """Should reject request missing cluster_id."""
        resp = client.post(
            "/api/qkview/create",
            json={},
            headers=operator_headers,
        )
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════
# Get / Status / Download
# ═══════════════════════════════════════════════════════════════════════


class TestGetQkviewEndpoint:
    """GET /api/qkview/{qkview_id}?cluster_id=N."""

    @patch("routes.qkview.get_qkview", return_value=_SAMPLE_QKVIEW)
    @patch("routes.qkview.KubernetesService")
    def test_viewer_can_get(
        self, mock_k8s_cls, mock_get,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should be able to get qkview details."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-get")

        resp = client.get(
            f"/api/qkview/qk-abc123?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"id", "status", "filename", "operator_dispatch"}
        assert data["id"] == "qk-abc123"


class TestGetQkviewStatusEndpoint:
    """GET /api/qkview/{qkview_id}/status?cluster_id=N."""

    @patch("routes.qkview.get_qkview_status", return_value=_SAMPLE_QKVIEW_STATUS)
    @patch("routes.qkview.KubernetesService")
    def test_viewer_can_get_status(
        self, mock_k8s_cls, mock_status,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should be able to get qkview status."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-status")

        resp = client.get(
            f"/api/qkview/qk-abc123/status?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"status", "progress", "operator_dispatch"}
        assert data["status"] == "Completed"


class TestDownloadQkviewEndpoint:
    """GET /api/qkview/{qkview_id}/download?cluster_id=N."""

    @patch("routes.qkview.download_qkview", return_value=b"\x1f\x8b\x08\x00")
    @patch("routes.qkview.KubernetesService")
    def test_viewer_can_download(
        self, mock_k8s_cls, mock_download,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should be able to download qkview tarball."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-download")

        resp = client.get(
            f"/api/qkview/qk-abc123/download?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/gzip"
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.content == b"\x1f\x8b\x08\x00"


# ═══════════════════════════════════════════════════════════════════════
# Delete / Cancel
# ═══════════════════════════════════════════════════════════════════════


class TestDeleteQkviewEndpoint:
    """DELETE /api/qkview/{qkview_id}?cluster_id=N."""

    @patch("routes.qkview.delete_qkview", return_value={"deleted": True})
    @patch("routes.qkview.KubernetesService")
    def test_operator_can_delete(
        self, mock_k8s_cls, mock_delete,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Operator should be able to delete a qkview."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-delete")

        resp = client.delete(
            f"/api/qkview/qk-abc123?cluster_id={cluster.id}",
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"deleted", "success", "operator_dispatch"}
        assert data["deleted"] is True

    @patch("routes.qkview.KubernetesService")
    def test_viewer_cannot_delete(
        self, mock_k8s_cls,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should NOT be able to delete (operator-only)."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-delete-deny")

        resp = client.delete(
            f"/api/qkview/qk-abc123?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestCancelQkviewEndpoint:
    """POST /api/qkview/{qkview_id}/cancel?cluster_id=N."""

    @patch("routes.qkview.cancel_qkview", return_value={"cancelled": True})
    @patch("routes.qkview.KubernetesService")
    def test_operator_can_cancel(
        self, mock_k8s_cls, mock_cancel,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Operator should be able to cancel a qkview."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-cancel")

        resp = client.post(
            f"/api/qkview/qk-abc123/cancel?cluster_id={cluster.id}",
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"cancelled", "success", "operator_dispatch"}
        assert data["cancelled"] is True

    @patch("routes.qkview.KubernetesService")
    def test_viewer_cannot_cancel(
        self, mock_k8s_cls,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should NOT be able to cancel (operator-only)."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-cancel-deny")

        resp = client.post(
            f"/api/qkview/qk-abc123/cancel?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════
# Cleanup Pods
# ═══════════════════════════════════════════════════════════════════════


class TestCleanupPodsEndpoint:
    """POST /api/qkview/cleanup-pods?cluster_id=N."""

    @patch("routes.qkview.cleanup_client_pods", return_value={"cleaned": True})
    @patch("routes.qkview.KubernetesService")
    def test_operator_can_cleanup(
        self, mock_k8s_cls, mock_cleanup,
        client, operator_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Operator should be able to clean up client pods."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-cleanup")

        resp = client.post(
            f"/api/qkview/cleanup-pods?cluster_id={cluster.id}",
            headers=operator_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"cleaned", "operator_dispatch"}
        assert data["cleaned"] is True

    @patch("routes.qkview.KubernetesService")
    def test_viewer_cannot_cleanup(
        self, mock_k8s_cls,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer should NOT be able to cleanup pods (operator-only)."""
        cluster = make_k8s_cluster(project=sample_project, name="qk-cleanup-deny")

        resp = client.post(
            f"/api/qkview/cleanup-pods?cluster_id={cluster.id}",
            headers=viewer_headers,
        )
        assert resp.status_code == 403
