"""
Tests for services.state_decryption_service — encrypted state handling.

BC-025: State decryption — real DB + filesystem (tmp_path), mock subprocess/cache/creds.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from services.state_decryption_service import (
    RESOURCES_CACHE_PREFIX,
    STATE_CACHE_PREFIX,
    STATE_CACHE_TTL,
    get_decrypted_state_or_fallback,
    get_state_metadata,
    get_state_resources_list,
    invalidate_state_cache,
    is_state_encrypted,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UNENCRYPTED_STATE = {
    "version": 4,
    "terraform_version": "1.8.0",
    "serial": 42,
    "lineage": "abc-123-def",
    "outputs": {"vpc_id": {"value": "vpc-12345"}},
    "resources": [
        {"type": "aws_vpc", "name": "main", "provider": "aws", "instances": []},
        {"type": "aws_subnet", "name": "public", "provider": "aws", "instances": []},
    ],
}

ENCRYPTED_STATE = {
    "version": 4,
    "serial": 7,
    "lineage": "enc-456-ghi",
    "encryption_version": "v1",
    "encrypted_data": "base64-encrypted-blob...",
}


def _write_state_file(tmp_path, state_dict, filename="terraform.tfstate"):
    """Write a state dict as JSON to tmp_path."""
    state_path = os.path.join(str(tmp_path), filename)
    with open(state_path, "w") as f:
        json.dump(state_dict, f)
    return state_path


# ---------------------------------------------------------------------------
# is_state_encrypted
# ---------------------------------------------------------------------------

class TestIsStateEncrypted:
    """Detect encrypted vs unencrypted state files."""

    def test_unencrypted_state(self, tmp_path):
        path = _write_state_file(tmp_path, UNENCRYPTED_STATE)
        assert is_state_encrypted(path) is False

    def test_encrypted_state(self, tmp_path):
        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        assert is_state_encrypted(path) is True

    def test_nonexistent_file_returns_false(self):
        assert is_state_encrypted("/nonexistent/path/state.tfstate") is False

    def test_invalid_json_returns_false(self, tmp_path):
        path = os.path.join(str(tmp_path), "bad.tfstate")
        with open(path, "w") as f:
            f.write("not json {{{")
        assert is_state_encrypted(path) is False


# ---------------------------------------------------------------------------
# get_state_metadata
# ---------------------------------------------------------------------------

class TestGetStateMetadata:
    """Extract metadata from state files."""

    def test_unencrypted_metadata(self, tmp_path):
        path = _write_state_file(tmp_path, UNENCRYPTED_STATE)
        meta = get_state_metadata(path)
        assert meta["lineage"] == "abc-123-def"
        assert meta["serial"] == 42
        assert meta["is_encrypted"] is False
        assert meta["terraform_version"] == "1.8.0"
        assert meta["file_size"] > 0
        assert "last_modified" in meta

    def test_encrypted_metadata(self, tmp_path):
        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        meta = get_state_metadata(path)
        assert meta["lineage"] == "enc-456-ghi"
        assert meta["serial"] == 7
        assert meta["is_encrypted"] is True
        assert meta["terraform_version"] == "encrypted"
        assert meta["encryption_version"] == "v1"

    def test_nonexistent_returns_error(self):
        meta = get_state_metadata("/nonexistent/path")
        assert "error" in meta

    def test_invalid_json_returns_error(self, tmp_path):
        path = os.path.join(str(tmp_path), "bad.tfstate")
        with open(path, "w") as f:
            f.write("not json")
        meta = get_state_metadata(path)
        assert "error" in meta


# ---------------------------------------------------------------------------
# invalidate_state_cache
# ---------------------------------------------------------------------------

class TestInvalidateStateCache:
    """Cache invalidation for decrypted state."""

    @patch("services.state_decryption_service.cache")
    def test_invalidates_module_cache(self, mock_cache):
        mock_cache.delete_pattern.return_value = 2
        count = invalidate_state_cache(module_id=42)
        assert count == 4  # module decrypted + resources patterns
        assert mock_cache.delete_pattern.call_count == 2

    @patch("services.state_decryption_service.cache")
    def test_invalidates_project_cache_when_provided(self, mock_cache):
        mock_cache.delete_pattern.return_value = 1
        count = invalidate_state_cache(module_id=42, project_id=7)
        assert mock_cache.delete_pattern.call_count == 3  # module + resources + project

    @patch("services.state_decryption_service.cache")
    def test_zero_when_no_cache_entries(self, mock_cache):
        mock_cache.delete_pattern.return_value = 0
        count = invalidate_state_cache(module_id=99)
        assert count == 0


# ---------------------------------------------------------------------------
# get_state_resources_list (unencrypted path — no subprocess)
# ---------------------------------------------------------------------------

class TestGetStateResourcesListUnencrypted:
    """Resource listing from unencrypted state (pure file parsing)."""

    def test_lists_resources_from_unencrypted(self, tmp_path, db):
        path = _write_state_file(tmp_path, UNENCRYPTED_STATE)
        success, resources = get_state_resources_list(path, project_id=1, module_id=1, db_session=db)
        assert success is True
        assert "aws_vpc.main" in resources
        assert "aws_subnet.public" in resources
        assert len(resources) == 2

    def test_empty_state_returns_empty_list(self, tmp_path, db):
        empty_state = {"version": 4, "resources": []}
        path = _write_state_file(tmp_path, empty_state)
        success, resources = get_state_resources_list(path, project_id=1, module_id=1, db_session=db)
        assert success is True
        assert resources == []

    def test_invalid_json_returns_error(self, tmp_path, db):
        path = os.path.join(str(tmp_path), "bad.tfstate")
        with open(path, "w") as f:
            f.write("not json")
        success, resources = get_state_resources_list(path, project_id=1, module_id=1, db_session=db)
        assert success is False


# ---------------------------------------------------------------------------
# get_state_resources_list (encrypted path — needs subprocess mock)
# ---------------------------------------------------------------------------

class TestGetStateResourcesListEncrypted:
    """Resource listing from encrypted state via tofu CLI."""

    @patch("services.state_decryption_service.cache")
    @patch("services.credentials_service.get_cloud_credentials_env", return_value={})
    @patch("services.execution.config_writer.write_encryption_config")
    @patch("subprocess.run")
    def test_lists_resources_from_encrypted(
        self, mock_run, mock_write_enc, mock_creds, mock_cache,
        tmp_path, db, make_project, make_project_module, make_module_library
    ):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        mock_cache.get.return_value = None  # No cached resources

        # tofu init succeeds, tofu state list returns resources
        def run_side_effect(cmd, **kwargs):
            if "init" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "state" in cmd and "list" in cmd:
                return MagicMock(returncode=0, stdout="aws_vpc.main\naws_subnet.public\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        with patch("services.state_decryption_service.DECRYPT_WORKSPACE_BASE", str(tmp_path)):
            success, resources = get_state_resources_list(path, project.id, mod.id, db)

        assert success is True
        assert "aws_vpc.main" in resources
        assert "aws_subnet.public" in resources

    @patch("services.state_decryption_service.cache")
    def test_returns_cached_resources(self, mock_cache, tmp_path, db):
        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        mock_cache.get.return_value = ["aws_vpc.main", "aws_subnet.public"]

        success, resources = get_state_resources_list(path, project_id=1, module_id=1, db_session=db)
        assert success is True
        assert resources == ["aws_vpc.main", "aws_subnet.public"]


# ---------------------------------------------------------------------------
# get_decrypted_state_or_fallback
# ---------------------------------------------------------------------------

class TestGetDecryptedStateOrFallback:
    """Fallback behavior when decryption fails."""

    def test_unencrypted_returns_full_state(self, tmp_path, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        path = _write_state_file(tmp_path, UNENCRYPTED_STATE)
        result = get_decrypted_state_or_fallback(path, mod.id, db)
        assert result["version"] == 4
        assert "resources" in result

    def test_module_not_found(self, tmp_path, db):
        path = _write_state_file(tmp_path, UNENCRYPTED_STATE)
        result = get_decrypted_state_or_fallback(path, module_id=99999, db_session=db)
        assert "error" in result

    @patch("services.state_decryption_service.decrypt_state_with_tofu")
    def test_encrypted_decryption_success(self, mock_decrypt, tmp_path, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        mock_decrypt.return_value = (True, {"version": 4, "resources": [{"type": "aws_vpc", "name": "main"}]})

        result = get_decrypted_state_or_fallback(path, mod.id, db)
        assert "resources" in result

    @patch("services.state_decryption_service.decrypt_state_with_tofu")
    def test_encrypted_decryption_failure_returns_metadata(self, mock_decrypt, tmp_path, db, make_project, make_project_module, make_module_library):
        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied", outputs={"vpc_id": "vpc-999"})

        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        mock_decrypt.return_value = (False, {"error": "tofu not found"})

        result = get_decrypted_state_or_fallback(path, mod.id, db)
        assert result["is_encrypted"] is True
        assert "error" in result
        assert result["metadata"]["lineage"] == "enc-456-ghi"
        assert result["outputs_from_db"] == {"vpc_id": "vpc-999"}


# ---------------------------------------------------------------------------
# decrypt_state_with_tofu
# ---------------------------------------------------------------------------

class TestDecryptStateWithTofu:
    """Full decryption flow with subprocess mocking."""

    @patch("services.state_decryption_service.cache")
    @patch("services.credentials_service.get_cloud_credentials_env", return_value={})
    @patch("services.execution.config_writer.write_encryption_config")
    @patch("subprocess.run")
    def test_decrypt_success(
        self, mock_run, mock_write_enc, mock_creds, mock_cache,
        tmp_path, db, make_project, make_project_module, make_module_library
    ):
        from services.state_decryption_service import decrypt_state_with_tofu

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        mock_cache.get.return_value = None  # No cache hit

        decrypted = {"version": 4, "resources": [{"type": "aws_vpc", "name": "main"}]}

        def run_side_effect(cmd, **kwargs):
            if "init" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "state" in cmd and "pull" in cmd:
                return MagicMock(returncode=0, stdout=json.dumps(decrypted), stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = run_side_effect

        with patch("services.state_decryption_service.DECRYPT_WORKSPACE_BASE", str(tmp_path)):
            success, result = decrypt_state_with_tofu(path, project.id, mod.id, db)

        assert success is True
        assert result["resources"][0]["type"] == "aws_vpc"
        mock_cache.set.assert_called_once()

    @patch("services.state_decryption_service.cache")
    def test_returns_cached_state(self, mock_cache, tmp_path, db, make_project, make_project_module, make_module_library):
        from services.state_decryption_service import decrypt_state_with_tofu

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        cached_state = {"version": 4, "resources": [{"type": "aws_vpc", "name": "cached"}]}
        mock_cache.get.return_value = cached_state

        success, result = decrypt_state_with_tofu(path, project.id, mod.id, db)
        assert success is True
        assert result["resources"][0]["name"] == "cached"

    @patch("services.state_decryption_service.cache")
    @patch("services.credentials_service.get_cloud_credentials_env", return_value={})
    @patch("services.execution.config_writer.write_encryption_config")
    @patch("subprocess.run")
    def test_module_not_found(
        self, mock_run, mock_write_enc, mock_creds, mock_cache,
        tmp_path, db
    ):
        from services.state_decryption_service import decrypt_state_with_tofu

        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        mock_cache.get.return_value = None

        with patch("services.state_decryption_service.DECRYPT_WORKSPACE_BASE", str(tmp_path)):
            success, result = decrypt_state_with_tofu(path, 1, 99999, db)

        assert success is False
        assert "not found" in result["error"]

    @patch("services.state_decryption_service.cache")
    @patch("services.credentials_service.get_cloud_credentials_env", return_value={})
    @patch("services.execution.config_writer.write_encryption_config")
    @patch("subprocess.run")
    def test_init_failure(
        self, mock_run, mock_write_enc, mock_creds, mock_cache,
        tmp_path, db, make_project, make_project_module, make_module_library
    ):
        from services.state_decryption_service import decrypt_state_with_tofu

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        mock_cache.get.return_value = None
        mock_run.return_value = MagicMock(returncode=1, stderr="init error", stdout="")

        with patch("services.state_decryption_service.DECRYPT_WORKSPACE_BASE", str(tmp_path)):
            success, result = decrypt_state_with_tofu(path, project.id, mod.id, db)

        assert success is False
        assert "Init failed" in result["error"]

    @patch("services.state_decryption_service.cache")
    @patch("services.credentials_service.get_cloud_credentials_env", return_value={})
    @patch("services.execution.config_writer.write_encryption_config")
    @patch("subprocess.run")
    def test_timeout(
        self, mock_run, mock_write_enc, mock_creds, mock_cache,
        tmp_path, db, make_project, make_project_module, make_module_library
    ):
        import subprocess as _subprocess

        from services.state_decryption_service import decrypt_state_with_tofu

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        path = _write_state_file(tmp_path, ENCRYPTED_STATE)
        mock_cache.get.return_value = None
        mock_run.side_effect = _subprocess.TimeoutExpired(cmd=["tofu"], timeout=30)

        with patch("services.state_decryption_service.DECRYPT_WORKSPACE_BASE", str(tmp_path)):
            success, result = decrypt_state_with_tofu(path, project.id, mod.id, db)

        assert success is False
        assert "Timeout" in result["error"]
