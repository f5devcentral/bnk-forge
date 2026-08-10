"""
BC-024: Component tests for SecretsService — secret CRUD, encryption,
file secrets, project secrets.

Uses real SQLite DB. Mocks encryption module.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models import ProjectSecret
from services.secrets_service import SecretsService

# ── Helpers ──────────────────────────────────────────────────────────

def _make_project(db, name="Secret Project"):
    from models import Project
    project = Project(
        name=name, project_type="kubernetes", cloud_provider="on-prem",
        environment="dev", backend_type="local", color="#000", icon="lock",
        is_active=True,
    )
    db.add(project)
    db.flush()
    return project


# ── Create Value Secret ──────────────────────────────────────────────

class TestCreateValueSecret:
    @patch("services.secrets_service.encrypt_value", return_value="encrypted_val")
    def test_create_value_secret(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)

        secret = svc.create_value_secret(
            project_id=project.id,
            name="db_password",
            value="super_secret",
            description="Database password",
        )

        assert secret.name == "db_password"
        assert secret.secret_type == "value"
        assert secret.value_encrypted == "encrypted_val"
        assert secret.is_active is True
        mock_enc.assert_called_once_with("super_secret")

    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_create_duplicate_name_raises(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)
        svc.create_value_secret(project.id, "unique_name", "val1")

        with pytest.raises(ValueError, match="already exists"):
            svc.create_value_secret(project.id, "unique_name", "val2")

    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_create_with_target(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)
        secret = svc.create_value_secret(
            project.id, "api_key", "key123",
            target_module_path="modules/vpc",
            target_variable_name="api_key_var",
        )
        assert secret.target_module_path == "modules/vpc"
        assert secret.target_variable_name == "api_key_var"


# ── List Secrets ─────────────────────────────────────────────────────

class TestListSecrets:
    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_list_secrets(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)
        svc.create_value_secret(project.id, "secret_a", "val_a")
        svc.create_value_secret(project.id, "secret_b", "val_b")

        result = svc.list_secrets(project.id)
        assert len(result) == 2
        names = [s["name"] for s in result]
        assert "secret_a" in names
        assert "secret_b" in names
        # Values should NOT be included by default
        for s in result:
            assert "value" not in s

    @patch("services.secrets_service.decrypt_value", return_value="decrypted_val")
    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_list_secrets_include_values(self, mock_enc, mock_dec, db):
        project = _make_project(db)
        svc = SecretsService(db)
        svc.create_value_secret(project.id, "visible", "val")

        result = svc.list_secrets(project.id, include_values=True)
        assert len(result) == 1
        assert result[0]["value"] == "decrypted_val"

    def test_list_empty_project(self, db):
        project = _make_project(db)
        svc = SecretsService(db)
        result = svc.list_secrets(project.id)
        assert result == []


# ── Get Secret ───────────────────────────────────────────────────────

class TestGetSecret:
    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_get_by_id(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)
        created = svc.create_value_secret(project.id, "lookup", "val")

        found = svc.get_secret(project.id, created.id)
        assert found is not None
        assert found.name == "lookup"

    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_get_by_name(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)
        svc.create_value_secret(project.id, "by_name", "val")

        found = svc.get_secret_by_name(project.id, "by_name")
        assert found is not None

    def test_get_nonexistent_returns_none(self, db):
        project = _make_project(db)
        svc = SecretsService(db)
        assert svc.get_secret(project.id, 99999) is None
        assert svc.get_secret_by_name(project.id, "nope") is None


# ── Delete Secret ────────────────────────────────────────────────────

class TestDeleteSecret:
    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_soft_delete(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)
        created = svc.create_value_secret(project.id, "del_me", "val")

        result = svc.delete_secret(project.id, created.id)
        assert result is True

        # Should not appear in list
        secrets = svc.list_secrets(project.id)
        assert len(secrets) == 0

    def test_delete_nonexistent_returns_false(self, db):
        project = _make_project(db)
        svc = SecretsService(db)
        result = svc.delete_secret(project.id, 99999)
        assert result is False


# ── Update Value Secret ──────────────────────────────────────────────

class TestUpdateValueSecret:
    @patch("services.secrets_service.encrypt_value", return_value="enc_updated")
    def test_update_value(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)

        # Create with initial encryption
        with patch("services.secrets_service.encrypt_value", return_value="enc_initial"):
            created = svc.create_value_secret(project.id, "update_me", "old_val")

        updated = svc.update_value_secret(project.id, created.id, value="new_val")
        assert updated.value_encrypted == "enc_updated"

    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_update_not_found_raises(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)
        with pytest.raises(ValueError, match="Secret not found"):
            svc.update_value_secret(project.id, 99999, value="x")

    @patch("services.secrets_service.encrypt_value", return_value="enc")
    def test_update_wrong_type_raises(self, mock_enc, db):
        project = _make_project(db)
        svc = SecretsService(db)
        # Create a file-type secret directly
        secret = ProjectSecret(
            project_id=project.id, name="file_secret", secret_type="file",
            is_active=True,
        )
        db.add(secret)
        db.flush()

        with pytest.raises(ValueError, match="not a value secret"):
            svc.update_value_secret(project.id, secret.id, value="x")


# ── Decrypt Helpers ──────────────────────────────────────────────────

class TestDecryptHelpers:
    def test_get_decrypted_value(self, db):
        project = _make_project(db)
        svc = SecretsService(db)

        secret = ProjectSecret(
            project_id=project.id, name="dec_val", secret_type="value",
            value_encrypted="encrypted_data", is_active=True,
        )
        db.add(secret)
        db.flush()

        with patch("services.secrets_service.decrypt_value", return_value="the_value"):
            result = svc.get_decrypted_value(secret)
        assert result == "the_value"

    def test_get_decrypted_value_wrong_type_raises(self, db):
        project = _make_project(db)
        svc = SecretsService(db)

        secret = ProjectSecret(
            project_id=project.id, name="file_s", secret_type="file", is_active=True,
        )
        db.add(secret)
        db.flush()

        with pytest.raises(ValueError, match="not a value secret"):
            svc.get_decrypted_value(secret)

    def test_get_decrypted_file_wrong_type_raises(self, db):
        project = _make_project(db)
        svc = SecretsService(db)

        secret = ProjectSecret(
            project_id=project.id, name="val_s", secret_type="value", is_active=True,
        )
        db.add(secret)
        db.flush()

        with pytest.raises(ValueError, match="not a file secret"):
            svc.get_decrypted_file_content(secret)


# ── Required Secrets Analysis ────────────────────────────────────────

class TestRequiredSecrets:
    def test_detects_file_path_inputs(self, db):
        svc = SecretsService(db)
        result = svc.get_required_secrets_for_module("modules/vpc", {
            "required": [
                {"name": "ssh_key", "description": "SSH key file", "validation": {"type": "file_path"}},
            ],
            "optional": [],
        })
        assert len(result) == 1
        assert result[0]["type"] == "file"
        assert result[0]["name"] == "ssh_key"

    def test_detects_sensitive_inputs(self, db):
        svc = SecretsService(db)
        result = svc.get_required_secrets_for_module("modules/rds", {
            "required": [
                {"name": "db_pass", "description": "DB Password", "sensitive": True, "validation": {}},
            ],
            "optional": [],
        })
        assert len(result) == 1
        assert result[0]["type"] == "value"

    def test_detects_bnk_existing_cluster_secrets_as_required(self, db):
        svc = SecretsService(db)
        result = svc.get_required_secrets_for_module("bnk/flo", {
            "required": [
                {"name": "jwt_token", "description": "License JWT", "sensitive": True, "validation": {}},
                {"name": "cne_pull_secret", "description": "FAR pull secret", "sensitive": True, "validation": {}},
            ],
            "optional": [],
        })
        names = {secret["name"] for secret in result}
        assert names == {"jwt_token", "cne_pull_secret"}
        assert all(secret["required"] is True for secret in result)

    def test_skips_context_resolved_sources_returns_only_project_secrets(self, db):
        """
        Inputs whose source is in CONTEXT_RESOLVED_SECRET_SOURCES must NOT be
        reported as required user-provided secrets.

        - aws_access_key_id   (source: credential_template, sensitive) → SKIP
        - aws_secret_access_key (source: credential_template, sensitive) → SKIP
        - aws_session_token   (source: credential_template, sensitive) → SKIP
        - forge_kubeconfig_content (source: auto, sensitive) → SKIP
        - jwt_token           (source: project_secret, sensitive) → KEEP
        - cne_pull_secret     (source: project_secret, sensitive) → KEEP
        """
        svc = SecretsService(db)
        result = svc.get_required_secrets_for_module("bnk/far-setup", {
            "required": [
                {
                    "name": "aws_access_key_id",
                    "description": "AWS access key",
                    "sensitive": True,
                    "source": "credential_template",
                    "validation": {},
                },
                {
                    "name": "aws_secret_access_key",
                    "description": "AWS secret key",
                    "sensitive": True,
                    "source": "credential_template",
                    "validation": {},
                },
                {
                    "name": "aws_session_token",
                    "description": "AWS session token",
                    "sensitive": True,
                    "source": "credential_template",
                    "validation": {},
                },
                {
                    "name": "forge_kubeconfig_content",
                    "description": "Cluster kubeconfig injected by forge",
                    "sensitive": True,
                    "source": "auto",
                    "validation": {},
                },
                {
                    "name": "jwt_token",
                    "description": "License JWT",
                    "sensitive": True,
                    "source": "project_secret",
                    "validation": {},
                },
                {
                    "name": "cne_pull_secret",
                    "description": "FAR pull secret",
                    "sensitive": True,
                    "source": "project_secret",
                    "validation": {},
                },
            ],
            "optional": [],
        })
        names = {secret["name"] for secret in result}
        assert names == {"jwt_token", "cne_pull_secret"}, (
            f"Expected only jwt_token and cne_pull_secret, got: {names}"
        )

    def test_skips_context_resolved_file_path_inputs(self, db):
        """file_path inputs with a context-resolved source must also be skipped."""
        svc = SecretsService(db)
        result = svc.get_required_secrets_for_module("bnk/infra", {
            "required": [
                {
                    "name": "kubeconfig_file",
                    "description": "Auto-mounted kubeconfig",
                    "source": "auto",
                    "validation": {"type": "file_path"},
                },
                {
                    "name": "user_cert",
                    "description": "User-supplied certificate",
                    "validation": {"type": "file_path"},
                },
            ],
            "optional": [],
        })
        names = {secret["name"] for secret in result}
        assert names == {"user_cert"}, (
            f"Expected only user_cert, got: {names}"
        )
