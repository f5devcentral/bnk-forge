"""
Tests for services.secrets_service — project secret management.

Covers:
  - Creating value secrets (encrypt + store)
  - Creating file secrets (base64 + encrypt + store)
  - Listing secrets (with/without values)
  - Getting single secrets by ID and name
  - Updating file and value secrets
  - Soft-deleting secrets
  - Decrypting file content and values
  - Preparing secrets for execution (filesystem writes + variable injection)
  - Duplicate name detection
  - Type mismatch validation
  - Module path targeting/filtering
"""

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestCreateValueSecret:
    """Test creating and retrieving value-type secrets."""

    def test_create_value_secret(self, db, make_project):
        """create_value_secret encrypts and stores the value."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        secret = svc.create_value_secret(
            project_id=project.id,
            name="api_key",
            value="sk-test-12345",
            description="Test API key",
        )

        assert secret.id is not None
        assert secret.name == "api_key"
        assert secret.secret_type == "value"
        assert secret.value_encrypted is not None
        assert secret.value_encrypted != "sk-test-12345"  # encrypted
        assert secret.description == "Test API key"
        assert secret.is_active is True

    def test_create_value_secret_with_target(self, db, make_project):
        """create_value_secret stores target module and variable info."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        secret = svc.create_value_secret(
            project_id=project.id,
            name="db_password",
            value="p@ssw0rd",
            target_module_path="infra/aws/rds",
            target_variable_name="master_password",
        )

        assert secret.target_module_path == "infra/aws/rds"
        assert secret.target_variable_name == "master_password"

    def test_create_duplicate_name_raises(self, db, make_project):
        """Creating a secret with a duplicate name raises ValueError."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        svc.create_value_secret(project_id=project.id, name="api_key", value="v1")

        with pytest.raises(ValueError, match="already exists"):
            svc.create_value_secret(project_id=project.id, name="api_key", value="v2")


class TestCreateFileSecret:
    """Test creating file-type secrets."""

    @pytest.mark.asyncio
    async def test_create_file_secret(self, db, make_project):
        """create_file_secret reads, base64-encodes, and encrypts file content."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        # Create a mock UploadFile
        file = AsyncMock()
        file.filename = "cert.pem"
        file.content_type = "application/x-pem-file"
        file.read = AsyncMock(return_value=b"-----BEGIN CERTIFICATE-----\nMIIB...\n-----END CERTIFICATE-----")

        secret = await svc.create_file_secret(
            project_id=project.id,
            name="tls_cert",
            file=file,
            description="TLS certificate",
        )

        assert secret.id is not None
        assert secret.name == "tls_cert"
        assert secret.secret_type == "file"
        assert secret.filename == "cert.pem"
        assert secret.file_content_encrypted is not None
        assert secret.file_size > 0
        assert secret.mime_type == "application/x-pem-file"


class TestGetSecrets:
    """Test secret retrieval methods."""

    def test_get_secret_by_id(self, db, make_project):
        """get_secret returns a secret by ID and project."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        created = svc.create_value_secret(project_id=project.id, name="key", value="val")

        found = svc.get_secret(project.id, created.id)
        assert found is not None
        assert found.id == created.id

    def test_get_secret_wrong_project(self, db, make_project):
        """get_secret returns None for wrong project ID."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        created = svc.create_value_secret(project_id=project.id, name="key", value="val")

        found = svc.get_secret(99999, created.id)
        assert found is None

    def test_get_secret_by_name(self, db, make_project):
        """get_secret_by_name finds a secret by name."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        svc.create_value_secret(project_id=project.id, name="my_secret", value="val")

        found = svc.get_secret_by_name(project.id, "my_secret")
        assert found is not None
        assert found.name == "my_secret"

    def test_get_secret_by_name_not_found(self, db, make_project):
        """get_secret_by_name returns None for nonexistent name."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        found = svc.get_secret_by_name(project.id, "nonexistent")
        assert found is None


class TestListSecrets:
    """Test listing secrets with and without values."""

    def test_list_secrets_metadata_only(self, db, make_project):
        """list_secrets returns metadata without decrypted values by default."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        svc.create_value_secret(project_id=project.id, name="key1", value="val1")
        svc.create_value_secret(project_id=project.id, name="key2", value="val2")

        secrets = svc.list_secrets(project.id)
        assert len(secrets) == 2
        # Values should NOT be exposed
        for s in secrets:
            assert "value" not in s or s.get("value") is None

    def test_list_secrets_with_values(self, db, make_project):
        """list_secrets with include_values=True decrypts and includes values."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        svc.create_value_secret(project_id=project.id, name="key1", value="secret_val")

        secrets = svc.list_secrets(project.id, include_values=True)
        assert len(secrets) == 1
        assert secrets[0]["value"] == "secret_val"

    def test_list_secrets_excludes_inactive(self, db, make_project):
        """list_secrets does not return soft-deleted secrets."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        secret = svc.create_value_secret(project_id=project.id, name="key1", value="val1")
        svc.delete_secret(project.id, secret.id)
        db.flush()

        secrets = svc.list_secrets(project.id)
        assert len(secrets) == 0


class TestDecryptSecrets:
    """Test decrypting secret values and file contents."""

    def test_decrypt_value(self, db, make_project):
        """get_decrypted_value returns the original plaintext."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        secret = svc.create_value_secret(
            project_id=project.id, name="key", value="my-secret-value"
        )

        decrypted = svc.get_decrypted_value(secret)
        assert decrypted == "my-secret-value"

    def test_decrypt_value_wrong_type_raises(self, db, make_project):
        """get_decrypted_value raises ValueError for file-type secrets."""
        from models import ProjectSecret
        from services.secrets_service import SecretsService

        project = make_project()
        # Create a file-type secret manually
        secret = ProjectSecret(
            project_id=project.id,
            name="file_secret",
            secret_type="file",
            filename="test.txt",
        )
        db.add(secret)
        db.flush()

        svc = SecretsService(db)
        with pytest.raises(ValueError, match="value"):
            svc.get_decrypted_value(secret)


class TestDeleteSecret:
    """Test soft-deleting secrets."""

    def test_delete_secret_soft_deletes(self, db, make_project):
        """delete_secret sets is_active=False."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        secret = svc.create_value_secret(project_id=project.id, name="key", value="val")

        result = svc.delete_secret(project.id, secret.id)
        assert result is True

        # Should be invisible to get
        found = svc.get_secret(project.id, secret.id)
        assert found is None

    def test_delete_nonexistent_returns_false(self, db, make_project):
        """delete_secret returns False for nonexistent secret."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        result = svc.delete_secret(project.id, 99999)
        assert result is False


class TestUpdateSecrets:
    """Test updating existing secrets."""

    def test_update_value_secret(self, db, make_project):
        """update_value_secret changes the encrypted value."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        secret = svc.create_value_secret(
            project_id=project.id, name="key", value="original"
        )

        updated = svc.update_value_secret(
            project_id=project.id,
            secret_id=secret.id,
            value="updated_value",
            description="Updated desc",
        )

        decrypted = svc.get_decrypted_value(updated)
        assert decrypted == "updated_value"
        assert updated.description == "Updated desc"

    def test_update_value_secret_type_mismatch_raises(self, db, make_project):
        """update_value_secret raises ValueError if secret is file type."""
        from models import ProjectSecret
        from services.secrets_service import SecretsService

        project = make_project()
        file_secret = ProjectSecret(
            project_id=project.id,
            name="cert",
            secret_type="file",
            filename="cert.pem",
            is_active=True,
        )
        db.add(file_secret)
        db.flush()

        svc = SecretsService(db)
        with pytest.raises(ValueError):
            svc.update_value_secret(project.id, file_secret.id, value="new")


class TestPrepareSecretsForExecution:
    """Test preparing secrets for module execution."""

    def test_prepare_value_secrets(self, db, make_project):
        """prepare_secrets_for_execution returns value secrets as a dict."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)
        svc.create_value_secret(
            project_id=project.id,
            name="api_key",
            value="sk-test-123",
            target_variable_name="api_key_var",
        )

        with tempfile.TemporaryDirectory() as work_dir:
            values, file_paths = svc.prepare_secrets_for_execution(
                project.id, work_dir
            )

        assert values["api_key_var"] == "sk-test-123"
        assert file_paths == []

    def test_prepare_file_secrets_writes_to_disk(self, db, make_project):
        """prepare_secrets_for_execution writes file secrets to workspace."""
        import base64

        from core.encryption import encrypt_value
        from services.secrets_service import SecretsService

        project = make_project()

        # Create a file secret manually to avoid async
        from models import ProjectSecret
        file_content = b"certificate content here"
        encoded = base64.b64encode(file_content).decode()
        encrypted = encrypt_value(encoded)

        secret = ProjectSecret(
            project_id=project.id,
            name="cert",
            secret_type="file",
            filename="cert.pem",
            file_content_encrypted=encrypted,
            file_size=len(file_content),
            mime_type="application/x-pem-file",
            is_active=True,
        )
        db.add(secret)
        db.flush()

        svc = SecretsService(db)
        with tempfile.TemporaryDirectory() as work_dir:
            values, file_paths = svc.prepare_secrets_for_execution(
                project.id, work_dir
            )

            assert len(file_paths) == 1
            assert os.path.exists(file_paths[0])
            with open(file_paths[0], "rb") as f:
                assert f.read() == file_content

    def test_prepare_secrets_respects_module_targeting(self, db, make_project):
        """prepare_secrets_for_execution filters by target_module_path."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        # Global secret (no target)
        svc.create_value_secret(
            project_id=project.id,
            name="global_key",
            value="global_val",
            target_variable_name="global_var",
        )

        # Targeted to a specific module
        svc.create_value_secret(
            project_id=project.id,
            name="targeted_key",
            value="targeted_val",
            target_module_path="infra/aws/vpc",
            target_variable_name="targeted_var",
        )

        with tempfile.TemporaryDirectory() as work_dir:
            # Request for a different module — targeted secret should be excluded
            values, _ = svc.prepare_secrets_for_execution(
                project.id, work_dir, module_path="infra/aws/eks"
            )

        assert "global_var" in values
        assert "targeted_var" not in values  # filtered out

    def test_prepare_secrets_includes_matching_target(self, db, make_project):
        """Targeted secrets ARE included when module_path matches."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        svc.create_value_secret(
            project_id=project.id,
            name="targeted_key",
            value="targeted_val",
            target_module_path="infra/aws/vpc",
            target_variable_name="targeted_var",
        )

        with tempfile.TemporaryDirectory() as work_dir:
            values, _ = svc.prepare_secrets_for_execution(
                project.id, work_dir, module_path="infra/aws/vpc"
            )

        assert "targeted_var" in values
        assert values["targeted_var"] == "targeted_val"

    def test_prepare_secrets_uses_name_as_fallback_key(self, db, make_project):
        """Value secret without target_variable_name uses secret.name as key."""
        from services.secrets_service import SecretsService

        project = make_project()
        svc = SecretsService(db)

        svc.create_value_secret(
            project_id=project.id,
            name="my_secret_name",
            value="my_value",
            # No target_variable_name
        )

        with tempfile.TemporaryDirectory() as work_dir:
            values, _ = svc.prepare_secrets_for_execution(
                project.id, work_dir
            )

        assert values["my_secret_name"] == "my_value"


class TestGetRequiredSecretsForModule:
    """Test static analysis of required secrets from inputs_metadata."""

    def test_detects_file_path_input(self):
        """Inputs with validation.type='file_path' are detected as file secrets."""
        from services.secrets_service import SecretsService

        svc = SecretsService.__new__(SecretsService)

        inputs_metadata = {
            "required": [{
                "name": "kubeconfig",
                "validation": {"type": "file_path"},
                "description": "Path to kubeconfig file",
            }],
            "optional": [],
        }

        result = svc.get_required_secrets_for_module("infra/k8s", inputs_metadata)
        assert len(result) == 1
        assert result[0]["name"] == "kubeconfig"
        assert result[0]["type"] == "file"
        assert result[0]["required"] is True

    def test_detects_secret_input(self):
        """Inputs with validation.type='secret' are detected as value secrets."""
        from services.secrets_service import SecretsService

        svc = SecretsService.__new__(SecretsService)

        inputs_metadata = {
            "required": [],
            "optional": [{
                "name": "api_token",
                "validation": {"type": "secret"},
                "description": "API token",
            }],
        }

        result = svc.get_required_secrets_for_module("infra/api", inputs_metadata)
        assert len(result) == 1
        assert result[0]["name"] == "api_token"
        assert result[0]["type"] == "value"
        assert result[0]["required"] is False

    def test_detects_sensitive_input(self):
        """Inputs with sensitive=True are detected as value secrets."""
        from services.secrets_service import SecretsService

        svc = SecretsService.__new__(SecretsService)

        inputs_metadata = {
            "required": [{
                "name": "password",
                "sensitive": True,
                "validation": {},
            }],
            "optional": [],
        }

        result = svc.get_required_secrets_for_module("infra/db", inputs_metadata)
        assert len(result) == 1
        assert result[0]["type"] == "value"
