"""
Integration tests for SSH credential routes — /api/ssh-credentials.

Covers: CRUD cycle, RBAC enforcement, duplicate name handling,
delete-blocked-by-usage, test endpoint, and 404 handling.
Uses FastAPI TestClient with real SQLite DB. The service is mocked
at the route level to isolate HTTP behavior from service internals.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models import KubernetesCluster, SSHCredential

# ── Helpers ──────────────────────────────────────────────────────────


def _make_ssh_response(**overrides):
    """Return a dict that looks like an SSHCredentialResponse."""
    base = {
        "id": 1,
        "name": "bastion-prod",
        "description": "Production bastion host",
        "host": "10.0.0.1",
        "port": 22,
        "username": "admin",
        "auth_type": "key",
        "has_password": False,
        "has_private_key": True,
        "is_default": False,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "created_by": None,
        "clusters_count": 0,
        "projects_count": 0,
    }
    base.update(overrides)
    return base


_SSH_CREATE_PAYLOAD = {
    "name": "bastion-prod",
    "host": "10.0.0.1",
    "port": 22,
    "username": "admin",
    "auth_type": "key",
    "private_key": "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
    "description": "Production bastion",
    "is_default": False,
}


# ── CRUD Cycle ───────────────────────────────────────────────────────


class TestSSHCredentialCRUD:
    """Full CRUD cycle: create → get → list → update → delete."""

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_create_credential(self, mock_svc_cls, client, admin_headers, sample_user):
        """Admin can create an SSH credential — returns 201."""
        mock_svc = MagicMock()
        mock_svc.create_credential.return_value = _make_ssh_response(id=10, name="bastion-prod")
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/ssh-credentials",
            json=_SSH_CREATE_PAYLOAD,
            headers=admin_headers,
        )
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == 10
        assert data["name"] == "bastion-prod"
        assert data["host"] == "10.0.0.1"
        mock_svc.create_credential.assert_called_once()

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_get_credential(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can get a single SSH credential by ID."""
        mock_svc = MagicMock()
        mock_svc.get_credential.return_value = _make_ssh_response(id=5, name="staging-bastion")
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/ssh-credentials/5", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == 5
        assert data["name"] == "staging-bastion"

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_list_credentials(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can list all SSH credentials."""
        mock_svc = MagicMock()
        mock_svc.list_credentials.return_value = [
            _make_ssh_response(id=1, name="bastion-prod"),
            _make_ssh_response(id=2, name="bastion-staging"),
        ]
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/ssh-credentials", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["name"] == "bastion-prod"

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_update_credential(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Operator can update an SSH credential."""
        mock_svc = MagicMock()
        mock_svc.update_credential.return_value = _make_ssh_response(
            id=5, name="renamed-bastion", host="192.168.1.1"
        )
        mock_svc_cls.return_value = mock_svc

        response = client.put(
            "/api/ssh-credentials/5",
            json={"name": "renamed-bastion", "host": "192.168.1.1"},
            headers=operator_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "renamed-bastion"
        assert data["host"] == "192.168.1.1"
        mock_svc.update_credential.assert_called_once()

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_delete_credential(self, mock_svc_cls, client, admin_headers, sample_user):
        """Admin can delete an SSH credential — returns 204."""
        mock_svc = MagicMock()
        mock_svc.delete_credential.return_value = None
        mock_svc_cls.return_value = mock_svc

        response = client.delete("/api/ssh-credentials/5", headers=admin_headers)
        assert response.status_code == 204
        mock_svc.delete_credential.assert_called_once_with(5)


# ── RBAC Enforcement ─────────────────────────────────────────────────


class TestSSHCredentialRBAC:
    """RBAC: admin/operator can mutate, viewer can only read."""

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_operator_can_create(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Operator can create SSH credentials."""
        mock_svc = MagicMock()
        mock_svc.create_credential.return_value = _make_ssh_response(id=20, name="op-cred")
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/ssh-credentials",
            json=_SSH_CREATE_PAYLOAD,
            headers=operator_headers,
        )
        assert response.status_code == 201

    def test_viewer_cannot_create(self, client, viewer_headers, all_test_users):
        """Viewer cannot create SSH credentials — returns 403."""
        response = client.post(
            "/api/ssh-credentials",
            json=_SSH_CREATE_PAYLOAD,
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_update(self, client, viewer_headers, all_test_users):
        """Viewer cannot update SSH credentials — returns 403."""
        response = client.put(
            "/api/ssh-credentials/1",
            json={"name": "sneaky-rename"},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_viewer_cannot_delete(self, client, viewer_headers, all_test_users):
        """Viewer cannot delete SSH credentials — returns 403."""
        response = client.delete("/api/ssh-credentials/1", headers=viewer_headers)
        assert response.status_code == 403

    def test_viewer_cannot_test(self, client, viewer_headers, all_test_users):
        """Viewer cannot test SSH credentials — returns 403."""
        response = client.post("/api/ssh-credentials/1/test", headers=viewer_headers)
        assert response.status_code == 403

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_viewer_can_list(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can list SSH credentials."""
        mock_svc = MagicMock()
        mock_svc.list_credentials.return_value = []
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/ssh-credentials", headers=viewer_headers)
        assert response.status_code == 200

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_viewer_can_get(self, mock_svc_cls, client, viewer_headers, all_test_users):
        """Viewer can get a specific SSH credential."""
        mock_svc = MagicMock()
        mock_svc.get_credential.return_value = _make_ssh_response()
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/ssh-credentials/1", headers=viewer_headers)
        assert response.status_code == 200

    def test_unauthenticated_list(self, client):
        """Unauthenticated request to list returns 401."""
        response = client.get("/api/ssh-credentials")
        assert response.status_code == 401

    def test_unauthenticated_create(self, client):
        """Unauthenticated request to create returns 401."""
        response = client.post("/api/ssh-credentials", json=_SSH_CREATE_PAYLOAD)
        assert response.status_code == 401


# ── Error Handling ───────────────────────────────────────────────────


class TestSSHCredentialErrors:
    """Error cases: 404, 400 duplicate, 400 delete-in-use."""

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_get_not_found(self, mock_svc_cls, client, admin_headers, sample_user):
        """GET nonexistent credential returns 404."""
        from core.errors import NotFoundError

        mock_svc = MagicMock()
        mock_svc.get_credential.side_effect = NotFoundError("ssh_credential", 99999)
        mock_svc_cls.return_value = mock_svc

        response = client.get("/api/ssh-credentials/99999", headers=admin_headers)
        assert response.status_code == 404

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_create_duplicate_name(self, mock_svc_cls, client, admin_headers, sample_user):
        """POST with duplicate name returns 400."""
        from core.errors import BadRequestError

        mock_svc = MagicMock()
        mock_svc.create_credential.side_effect = BadRequestError(
            "SSH credential 'bastion-prod' already exists"
        )
        mock_svc_cls.return_value = mock_svc

        response = client.post(
            "/api/ssh-credentials",
            json=_SSH_CREATE_PAYLOAD,
            headers=admin_headers,
        )
        assert response.status_code == 400
        data = response.json()
        assert "already exists" in data["error"]["message"]

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_update_not_found(self, mock_svc_cls, client, admin_headers, sample_user):
        """PUT nonexistent credential returns 404."""
        from core.errors import NotFoundError

        mock_svc = MagicMock()
        mock_svc.update_credential.side_effect = NotFoundError("ssh_credential", 99999)
        mock_svc_cls.return_value = mock_svc

        response = client.put(
            "/api/ssh-credentials/99999",
            json={"name": "new-name"},
            headers=admin_headers,
        )
        assert response.status_code == 404

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_delete_not_found(self, mock_svc_cls, client, admin_headers, sample_user):
        """DELETE nonexistent credential returns 404."""
        from core.errors import NotFoundError

        mock_svc = MagicMock()
        mock_svc.delete_credential.side_effect = NotFoundError("ssh_credential", 99999)
        mock_svc_cls.return_value = mock_svc

        response = client.delete("/api/ssh-credentials/99999", headers=admin_headers)
        assert response.status_code == 404

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_delete_blocked_by_usage(self, mock_svc_cls, client, admin_headers, sample_user):
        """DELETE credential in use returns 400."""
        from core.errors import BadRequestError

        mock_svc = MagicMock()
        mock_svc.delete_credential.side_effect = BadRequestError(
            "Cannot delete SSH credential. 1 cluster(s) are using it. "
            "Please update them to use a different credential first."
        )
        mock_svc_cls.return_value = mock_svc

        response = client.delete("/api/ssh-credentials/1", headers=admin_headers)
        assert response.status_code == 400
        data = response.json()
        assert "Cannot delete" in data["error"]["message"]

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_update_duplicate_name(self, mock_svc_cls, client, operator_headers, all_test_users):
        """PUT with name that collides with another credential returns 400."""
        from core.errors import BadRequestError

        mock_svc = MagicMock()
        mock_svc.update_credential.side_effect = BadRequestError(
            "SSH credential 'taken-name' already exists"
        )
        mock_svc_cls.return_value = mock_svc

        response = client.put(
            "/api/ssh-credentials/1",
            json={"name": "taken-name"},
            headers=operator_headers,
        )
        assert response.status_code == 400
        data = response.json()
        assert "already exists" in data["error"]["message"]


# ── Test Endpoint ────────────────────────────────────────────────────


class TestSSHCredentialTestEndpoint:
    """POST /api/ssh-credentials/{id}/test."""

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_test_success(self, mock_svc_cls, client, admin_headers, sample_user):
        """Admin can test SSH credential connectivity."""
        mock_svc = MagicMock()
        mock_svc.test_credential.return_value = {
            "success": True,
            "message": "SSH connection successful",
            "host": "10.0.0.1",
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/ssh-credentials/1/test", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_svc.test_credential.assert_called_once_with(1)

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_test_failure(self, mock_svc_cls, client, operator_headers, all_test_users):
        """Test endpoint returns failure result when connection fails."""
        mock_svc = MagicMock()
        mock_svc.test_credential.return_value = {
            "success": False,
            "message": "Connection refused",
        }
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/ssh-credentials/1/test", headers=operator_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    @patch("routes.ssh_credentials.SSHCredentialService")
    def test_test_not_found(self, mock_svc_cls, client, admin_headers, sample_user):
        """Test nonexistent credential returns 404."""
        from core.errors import NotFoundError

        mock_svc = MagicMock()
        mock_svc.test_credential.side_effect = NotFoundError("ssh_credential", 99999)
        mock_svc_cls.return_value = mock_svc

        response = client.post("/api/ssh-credentials/99999/test", headers=admin_headers)
        assert response.status_code == 404
