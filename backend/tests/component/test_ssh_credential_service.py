"""
Component tests for SSHCredentialService — CRUD, encryption, defaults, and deletion guards.

Uses real SQLite DB (from conftest) with mocked encryption. Tests exercise
the service layer directly without HTTP routing.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError, NotFoundError
from models import KubernetesCluster, Project, SSHCredential
from routes.ssh_credentials import SSHCredentialCreate, SSHCredentialUpdate
from services.ssh_credential_service import SSHCredentialService

# ── Helpers ──────────────────────────────────────────────────────────


def _insert_ssh_credential(db, **overrides):
    """Insert an SSHCredential directly into the DB (bypasses service)."""
    defaults = dict(
        name="bastion-prod",
        host="10.0.0.1",
        port=22,
        username="admin",
        auth_type="key",
        private_key_encrypted="enc_key_data",
        is_default=False,
    )
    defaults.update(overrides)
    cred = SSHCredential(**defaults)
    db.add(cred)
    db.flush()
    db.refresh(cred)
    return cred


def _make_create_data(**overrides) -> SSHCredentialCreate:
    """Build an SSHCredentialCreate with sensible defaults."""
    defaults = dict(
        name="new-ssh-cred",
        host="10.0.0.5",
        port=22,
        username="deploy",
        auth_type="key",
        private_key="-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----",
        password=None,
        key_passphrase=None,
        description="Test SSH credential",
        is_default=False,
    )
    defaults.update(overrides)
    return SSHCredentialCreate(**defaults)


# ── Serialization ────────────────────────────────────────────────────


class TestSerialize:
    """SSHCredentialService.serialize produces correct dict."""

    def test_serialize_includes_expected_keys(self, db):
        cred = _insert_ssh_credential(db)
        result = SSHCredentialService.serialize(cred)

        assert result["id"] == cred.id
        assert result["name"] == "bastion-prod"
        assert result["host"] == "10.0.0.1"
        assert result["port"] == 22
        assert result["username"] == "admin"
        assert result["auth_type"] == "key"
        assert result["is_default"] is False

    def test_serialize_never_exposes_secrets(self, db):
        cred = _insert_ssh_credential(
            db,
            password_encrypted="enc_password",
            private_key_encrypted="enc_key",
            key_passphrase_encrypted="enc_passphrase",
        )
        result = SSHCredentialService.serialize(cred)

        # Sensitive fields must be booleans, not the encrypted values
        assert result["has_password"] is True
        assert result["has_private_key"] is True
        assert "password_encrypted" not in result
        assert "private_key_encrypted" not in result
        assert "key_passphrase_encrypted" not in result

    def test_serialize_counts_relationships(self, db):
        cred = _insert_ssh_credential(db)
        result = SSHCredentialService.serialize(cred)
        assert result["clusters_count"] == 0
        assert result["projects_count"] == 0


# ── List Credentials ─────────────────────────────────────────────────


class TestListCredentials:
    def test_list_empty(self, db):
        svc = SSHCredentialService(db)
        result = svc.list_credentials()
        assert result == []

    def test_list_returns_all(self, db):
        _insert_ssh_credential(db, name="cred-a")
        _insert_ssh_credential(db, name="cred-b")
        svc = SSHCredentialService(db)
        result = svc.list_credentials()
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"cred-a", "cred-b"}

    def test_list_orders_default_first(self, db):
        _insert_ssh_credential(db, name="non-default", is_default=False)
        _insert_ssh_credential(db, name="the-default", is_default=True)
        svc = SSHCredentialService(db)
        result = svc.list_credentials()
        assert result[0]["name"] == "the-default"


# ── Get Credential ───────────────────────────────────────────────────


class TestGetCredential:
    def test_get_found(self, db):
        cred = _insert_ssh_credential(db)
        svc = SSHCredentialService(db)
        result = svc.get_credential(cred.id)
        assert result["id"] == cred.id
        assert result["name"] == "bastion-prod"

    def test_get_not_found(self, db):
        svc = SSHCredentialService(db)
        with pytest.raises(NotFoundError):
            svc.get_credential(99999)


# ── Create Credential ────────────────────────────────────────────────


class TestCreateCredential:
    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_success(self, mock_enc, db):
        svc = SSHCredentialService(db)
        data = _make_create_data()
        result = svc.create_credential(data)

        assert result["name"] == "new-ssh-cred"
        assert result["host"] == "10.0.0.5"
        assert result["username"] == "deploy"
        assert result["auth_type"] == "key"
        assert result["has_private_key"] is True
        assert result["id"] is not None

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_duplicate_name_rejected(self, mock_enc, db):
        _insert_ssh_credential(db, name="duplicate-name")
        svc = SSHCredentialService(db)
        data = _make_create_data(name="duplicate-name")

        with pytest.raises(BadRequestError, match="already exists"):
            svc.create_credential(data)

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_encrypts_password(self, mock_enc, db):
        svc = SSHCredentialService(db)
        data = _make_create_data(
            auth_type="password",
            password="my-secret-pass",
            private_key=None,
        )
        result = svc.create_credential(data)

        # Verify the encrypted value was stored in the DB
        db_cred = db.query(SSHCredential).filter(SSHCredential.id == result["id"]).first()
        assert db_cred.password_encrypted == "enc_my-secret-pass"
        assert result["has_password"] is True

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_encrypts_private_key(self, mock_enc, db):
        svc = SSHCredentialService(db)
        data = _make_create_data(private_key="KEY_CONTENT")
        result = svc.create_credential(data)

        db_cred = db.query(SSHCredential).filter(SSHCredential.id == result["id"]).first()
        assert db_cred.private_key_encrypted == "enc_KEY_CONTENT"

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_encrypts_key_passphrase(self, mock_enc, db):
        svc = SSHCredentialService(db)
        data = _make_create_data(private_key="KEY", key_passphrase="my-passphrase")
        result = svc.create_credential(data)

        db_cred = db.query(SSHCredential).filter(SSHCredential.id == result["id"]).first()
        assert db_cred.key_passphrase_encrypted == "enc_my-passphrase"

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_with_default_port(self, mock_enc, db):
        svc = SSHCredentialService(db)
        data = _make_create_data(port=None)
        result = svc.create_credential(data)
        assert result["port"] == 22


# ── Update Credential ────────────────────────────────────────────────


class TestUpdateCredential:
    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_name(self, mock_enc, db):
        cred = _insert_ssh_credential(db, name="old-name")
        svc = SSHCredentialService(db)
        data = SSHCredentialUpdate(name="new-name")
        result = svc.update_credential(cred.id, data)
        assert result["name"] == "new-name"

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_duplicate_name_rejected(self, mock_enc, db):
        _insert_ssh_credential(db, name="taken-name")
        cred = _insert_ssh_credential(db, name="my-cred")
        svc = SSHCredentialService(db)
        data = SSHCredentialUpdate(name="taken-name")

        with pytest.raises(BadRequestError, match="already exists"):
            svc.update_credential(cred.id, data)

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_encrypted_field(self, mock_enc, db):
        cred = _insert_ssh_credential(db)
        svc = SSHCredentialService(db)
        data = SSHCredentialUpdate(password="new-password")
        svc.update_credential(cred.id, data)

        db.refresh(cred)
        assert cred.password_encrypted == "enc_new-password"

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_host_and_port(self, mock_enc, db):
        cred = _insert_ssh_credential(db)
        svc = SSHCredentialService(db)
        data = SSHCredentialUpdate(host="192.168.1.1", port=2222)
        result = svc.update_credential(cred.id, data)
        assert result["host"] == "192.168.1.1"
        assert result["port"] == 2222

    def test_update_not_found(self, db):
        svc = SSHCredentialService(db)
        data = SSHCredentialUpdate(name="whatever")
        with pytest.raises(NotFoundError):
            svc.update_credential(99999, data)


# ── Delete Credential ────────────────────────────────────────────────


class TestDeleteCredential:
    def test_delete_success(self, db):
        cred = _insert_ssh_credential(db)
        cred_id = cred.id
        svc = SSHCredentialService(db)
        svc.delete_credential(cred_id)
        db.flush()

        assert db.query(SSHCredential).filter(SSHCredential.id == cred_id).first() is None

    def test_delete_not_found(self, db):
        svc = SSHCredentialService(db)
        with pytest.raises(NotFoundError):
            svc.delete_credential(99999)

    def test_delete_blocked_by_cluster_usage(self, db):
        cred = _insert_ssh_credential(db)
        cluster = KubernetesCluster(
            name="linked-cluster",
            context="ctx-linked",
            ssh_credential_id=cred.id,
        )
        db.add(cluster)
        db.flush()

        svc = SSHCredentialService(db)
        with pytest.raises(BadRequestError, match="cluster"):
            svc.delete_credential(cred.id)

    def test_delete_blocked_by_project_usage(self, db):
        cred = _insert_ssh_credential(db)
        project = Project(
            name="linked-project",
            project_type="kubernetes",
            cloud_provider="on-prem",
            environment="dev",
            backend_type="local",
            color="#000",
            icon="cloud",
            is_active=True,
            ssh_credential_id=cred.id,
        )
        db.add(project)
        db.flush()

        svc = SSHCredentialService(db)
        with pytest.raises(BadRequestError, match="project"):
            svc.delete_credential(cred.id)

    def test_delete_blocked_by_both_cluster_and_project(self, db):
        cred = _insert_ssh_credential(db)
        cluster = KubernetesCluster(
            name="both-cluster",
            context="ctx-both",
            ssh_credential_id=cred.id,
        )
        project = Project(
            name="both-project",
            project_type="kubernetes",
            cloud_provider="on-prem",
            environment="dev",
            backend_type="local",
            color="#000",
            icon="cloud",
            is_active=True,
            ssh_credential_id=cred.id,
        )
        db.add_all([cluster, project])
        db.flush()

        svc = SSHCredentialService(db)
        with pytest.raises(BadRequestError, match="cluster.*project|project.*cluster"):
            svc.delete_credential(cred.id)


# ── Test Credential (Connection Test) ────────────────────────────────


class TestTestCredential:
    @patch("services.ssh_tunnel_manager.get_tunnel_manager")
    def test_delegates_to_tunnel_manager(self, mock_get_mgr, db):
        cred = _insert_ssh_credential(
            db,
            host="10.0.0.1",
            port=22,
            username="admin",
            auth_type="key",
            private_key_encrypted="enc_key",
        )

        mock_mgr = MagicMock()
        mock_mgr.test_ssh_connection.return_value = {
            "success": True,
            "message": "SSH connection successful",
        }
        mock_get_mgr.return_value = mock_mgr

        svc = SSHCredentialService(db)
        result = svc.test_credential(cred.id)

        assert result["success"] is True
        mock_mgr.test_ssh_connection.assert_called_once_with(
            ssh_host="10.0.0.1",
            ssh_port=22,
            ssh_username="admin",
            ssh_auth_type="key",
            ssh_password_encrypted=None,
            ssh_key_encrypted="enc_key",
            ssh_key_passphrase_encrypted=None,
        )

    @patch("services.ssh_tunnel_manager.get_tunnel_manager")
    def test_test_not_found(self, mock_get_mgr, db):
        svc = SSHCredentialService(db)
        with pytest.raises(NotFoundError):
            svc.test_credential(99999)


# ── Default Management ───────────────────────────────────────────────


class TestDefaultManagement:
    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_default_clears_others(self, mock_enc, db):
        """When a new credential is created with is_default=True, existing defaults are cleared."""
        _insert_ssh_credential(db, name="old-default", is_default=True)
        svc = SSHCredentialService(db)

        data = _make_create_data(name="new-default", is_default=True)
        result = svc.create_credential(data)
        assert result["is_default"] is True

        # The old one should have been cleared
        old = db.query(SSHCredential).filter(SSHCredential.name == "old-default").first()
        assert old.is_default is False

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_update_to_default_clears_others(self, mock_enc, db):
        """When an existing credential is set to default, other defaults are cleared."""
        _insert_ssh_credential(db, name="was-default", is_default=True)
        cred = _insert_ssh_credential(db, name="now-default", is_default=False)

        svc = SSHCredentialService(db)
        data = SSHCredentialUpdate(is_default=True)
        result = svc.update_credential(cred.id, data)
        assert result["is_default"] is True

        was = db.query(SSHCredential).filter(SSHCredential.name == "was-default").first()
        assert was.is_default is False

    @patch("services.ssh_credential_service.encrypt_value", side_effect=lambda v: f"enc_{v}")
    def test_create_non_default_does_not_affect_existing(self, mock_enc, db):
        """Creating a non-default credential doesn't touch existing defaults."""
        _insert_ssh_credential(db, name="existing-default", is_default=True)

        svc = SSHCredentialService(db)
        data = _make_create_data(name="non-default", is_default=False)
        svc.create_credential(data)

        existing = db.query(SSHCredential).filter(SSHCredential.name == "existing-default").first()
        assert existing.is_default is True
