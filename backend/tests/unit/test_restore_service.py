"""
BR-046: Unit tests for restore methods in services.backup_service.BackupService.

All tests run without a real DB, subprocess, or file system access:
- subprocess.run is mocked to avoid calling psql
- file I/O is mocked where needed
- alembic is mocked to avoid needing alembic.ini
- maintenance functions are mocked at module level
- DB session is a MagicMock
"""
import gzip
import json
import os
import tarfile
import tempfile
from unittest.mock import MagicMock, call, patch

import pytest

from core.errors import BackupError, BackupErrorCode
from services.backup_service import BackupService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(db: MagicMock | None = None) -> BackupService:
    if db is None:
        db = MagicMock()
    return BackupService(db)


def _make_valid_archive(tmp_path, format_version: int = 1) -> str:
    """Build a minimal valid .tar.gz backup archive in tmp_path and return its path."""
    archive_path = str(tmp_path / "backup.tar.gz")

    metadata = {
        "format_version": format_version,
        "forge_version": "2.0.0",
        "alembic_head": "abc123",
        "created_at": "2026-04-13T00:00:00Z",
        "table_counts": {"projects": 3, "users": 2},
        "includes": ["database", "encryption_key"],
    }

    # Minimal gzipped SQL dump
    dump_gz_path = str(tmp_path / "dump.sql.gz")
    with gzip.open(dump_gz_path, "wb") as f:
        f.write(b"-- fake sql dump\n")

    # Minimal wrapped_key.enc JSON
    wrapped_key = {
        "salt": "AAAAAAAAAAAAAAAAAAAAAA==",
        "nonce": "AAAAAAAAAAAAAAAA",
        "ciphertext": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    }
    wrapped_key_path = str(tmp_path / "wrapped_key.enc")
    with open(wrapped_key_path, "w") as f:
        json.dump(wrapped_key, f)

    metadata_path = str(tmp_path / "metadata.json")
    with open(metadata_path, "w") as f:
        json.dump(metadata, f)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(metadata_path, arcname="metadata.json")
        tar.add(dump_gz_path, arcname="dump.sql.gz")
        tar.add(wrapped_key_path, arcname="wrapped_key.enc")

    return archive_path


# ---------------------------------------------------------------------------
# BR-046 test 1: validate_archive — success
# ---------------------------------------------------------------------------

class TestValidateArchiveSuccess:
    @pytest.mark.unit
    def test_validate_archive_returns_metadata(self, tmp_path):
        """validate_archive() returns parsed metadata for a well-formed archive."""
        archive_path = _make_valid_archive(tmp_path)
        svc = _make_service()

        metadata = svc.validate_archive(archive_path)

        assert metadata["format_version"] == 1
        assert metadata["forge_version"] == "2.0.0"
        assert metadata["alembic_head"] == "abc123"
        assert "table_counts" in metadata


# ---------------------------------------------------------------------------
# BR-046 test 2: validate_archive — missing required files
# ---------------------------------------------------------------------------

class TestValidateArchiveMissingFiles:
    @pytest.mark.unit
    def test_validate_archive_raises_on_missing_dump(self, tmp_path):
        """Archive without dump.sql.gz raises BackupError(INVALID_ARCHIVE)."""
        archive_path = str(tmp_path / "incomplete.tar.gz")

        metadata = {"format_version": 1, "alembic_head": "abc"}
        metadata_path = str(tmp_path / "metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f)

        # Archive has metadata.json but is missing dump.sql.gz and wrapped_key.enc
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(metadata_path, arcname="metadata.json")

        svc = _make_service()
        with pytest.raises(BackupError) as exc_info:
            svc.validate_archive(archive_path)

        assert exc_info.value.code == BackupErrorCode.INVALID_ARCHIVE.value
        assert exc_info.value.status_code == 400

    @pytest.mark.unit
    def test_validate_archive_raises_on_completely_empty(self, tmp_path):
        """Archive with no required files raises BackupError(INVALID_ARCHIVE) listing all missing."""
        archive_path = str(tmp_path / "empty.tar.gz")
        with tarfile.open(archive_path, "w:gz"):
            pass  # empty archive

        svc = _make_service()
        with pytest.raises(BackupError) as exc_info:
            svc.validate_archive(archive_path)

        assert exc_info.value.code == BackupErrorCode.INVALID_ARCHIVE.value
        # All three required files should be mentioned in the message
        assert "metadata.json" in exc_info.value.message
        assert "dump.sql.gz" in exc_info.value.message
        assert "wrapped_key.enc" in exc_info.value.message


# ---------------------------------------------------------------------------
# BR-046 test 3: validate_archive — incompatible format version
# ---------------------------------------------------------------------------

class TestValidateArchiveIncompatibleVersion:
    @pytest.mark.unit
    def test_validate_archive_raises_on_format_version_2(self, tmp_path):
        """format_version > 1 raises BackupError(INCOMPATIBLE_FORMAT)."""
        archive_path = _make_valid_archive(tmp_path, format_version=2)
        svc = _make_service()

        with pytest.raises(BackupError) as exc_info:
            svc.validate_archive(archive_path)

        assert exc_info.value.code == BackupErrorCode.INCOMPATIBLE_FORMAT.value
        assert exc_info.value.status_code == 400
        assert "format version 2" in exc_info.value.message


# ---------------------------------------------------------------------------
# BR-046 test 4: validate_archive — not a valid tar.gz
# ---------------------------------------------------------------------------

class TestValidateArchiveInvalidFormat:
    @pytest.mark.unit
    def test_validate_archive_raises_on_garbage_file(self, tmp_path):
        """A non-tar file raises BackupError(INVALID_ARCHIVE)."""
        garbage_path = str(tmp_path / "garbage.tar.gz")
        with open(garbage_path, "wb") as f:
            f.write(b"this is not a tar file at all")

        svc = _make_service()
        with pytest.raises(BackupError) as exc_info:
            svc.validate_archive(garbage_path)

        assert exc_info.value.code == BackupErrorCode.INVALID_ARCHIVE.value
        assert exc_info.value.status_code == 400


class TestExtractArchiveSafety:
    @pytest.mark.unit
    def test_extract_archive_raises_on_path_traversal_member(self, tmp_path):
        """Archives containing traversal paths are rejected before extraction."""
        archive_path = str(tmp_path / "unsafe.tar.gz")
        payload_path = tmp_path / "payload.txt"
        payload_path.write_text("unsafe")

        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(payload_path, arcname="../payload.txt")

        svc = _make_service()

        with pytest.raises(BackupError) as exc_info:
            svc._extract_archive(archive_path, str(tmp_path / "extract"))

        assert exc_info.value.code == BackupErrorCode.INVALID_ARCHIVE.value
        assert "unsafe path" in exc_info.value.message


# ---------------------------------------------------------------------------
# BR-046 test 4b: _restore_database — sanitises transaction_timeout
# ---------------------------------------------------------------------------

class TestRestoreDatabaseSanitisesDump:
    @pytest.mark.unit
    def test_restore_database_strips_transaction_timeout(self, tmp_path):
        """_restore_database() strips 'SET transaction_timeout = 0;' from SQL before replay."""
        svc = _make_service()

        # Create a gzipped dump containing transaction_timeout among other SET lines
        dump_gz = str(tmp_path / "dump.sql.gz")
        sql_content = b"""-- PostgreSQL database dump
SET statement_timeout = 0;
SET lock_timeout = 0;
SET transaction_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
CREATE TABLE users (id INTEGER);
"""
        with gzip.open(dump_gz, "wb") as f:
            f.write(sql_content)

        success_proc = MagicMock()
        success_proc.returncode = 0
        success_proc.stderr = b""

        # Capture the file content when subprocess.run is called
        captured_sql_content = None

        def capture_subprocess_call(*args, **kwargs):
            nonlocal captured_sql_content
            # The 4th call is the dump replay with --file
            cmd = args[0]
            if "--file" in cmd:
                file_idx = cmd.index("--file") + 1
                sql_path = cmd[file_idx]
                with open(sql_path, encoding="utf-8") as f:
                    captured_sql_content = f.read()
            return success_proc

        with (
            patch("services.backup_service.subprocess.run", side_effect=capture_subprocess_call),
            patch("services.backup_service.settings") as mock_settings,
            patch("services.backup_service.shutil.which", return_value="psql"),
        ):
            mock_settings.DATABASE_URL = "postgresql://u:p@h:5432/db"
            svc._restore_database(dump_gz)

        # Verify the dump replay call was made and content was captured
        assert captured_sql_content is not None

        # Verify "transaction_timeout" was stripped
        assert "transaction_timeout" not in captured_sql_content

        # Verify other SET commands remain
        assert "SET statement_timeout = 0;" in captured_sql_content
        assert "SET lock_timeout = 0;" in captured_sql_content
        assert "CREATE TABLE users" in captured_sql_content


# ---------------------------------------------------------------------------
# BR-046 test 5: restore_backup — rejects during maintenance mode
# ---------------------------------------------------------------------------

class TestRestoreRejectsDuringMaintenance:
    @pytest.mark.unit
    def test_restore_raises_when_already_in_maintenance(self, tmp_path):
        """If maintenance mode is already active, restore_backup raises BackupError(RESTORE_IN_PROGRESS)."""
        svc = _make_service()
        archive_path = _make_valid_archive(tmp_path)

        with patch("services.backup_service.is_maintenance_mode", return_value=True):
            with pytest.raises(BackupError) as exc_info:
                svc.restore_backup(archive_path, "strongpassphrase123")

        assert exc_info.value.code == BackupErrorCode.RESTORE_IN_PROGRESS.value
        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# BR-046 test 6: restore_backup — rejects invalid passphrase (pre-maintenance)
# ---------------------------------------------------------------------------

class TestRestoreRejectsInvalidPassphrase:
    @pytest.mark.unit
    def test_restore_raises_before_maintenance_on_bad_passphrase(self, tmp_path):
        """Invalid passphrase is rejected BEFORE entering maintenance mode."""
        svc = _make_service()
        archive_path = _make_valid_archive(tmp_path)

        set_maintenance_mock = MagicMock()

        with (
            patch("services.backup_service.is_maintenance_mode", return_value=False),
            # Make unwrap_fernet_key raise ValueError (wrong passphrase)
            # unwrap_fernet_key is now imported at module level in backup_service
            patch(
                "services.backup_service.unwrap_fernet_key",
                side_effect=ValueError("Invalid passphrase or corrupted data"),
            ),
            # Patch the module-level set_maintenance_mode in backup_service
            patch("services.backup_service.set_maintenance_mode", set_maintenance_mock),
        ):
            with pytest.raises(BackupError) as exc_info:
                svc.restore_backup(archive_path, "wrongpassword123")

        assert exc_info.value.code == BackupErrorCode.INVALID_PASSPHRASE.value
        assert exc_info.value.status_code == 400
        # Maintenance mode must NOT have been set — passphrase check is pre-maintenance
        set_maintenance_mock.assert_not_called()


# ---------------------------------------------------------------------------
# BR-046 test 7: _restore_database — success
# ---------------------------------------------------------------------------

class TestRestoreDatabaseSuccess:
    @pytest.mark.unit
    def test_restore_database_calls_psql_with_correct_args(self, tmp_path):
        """_restore_database() calls psql with the right arguments and cleans up."""
        svc = _make_service()

        # Create a minimal gzipped SQL dump
        dump_gz = str(tmp_path / "dump.sql.gz")
        with gzip.open(dump_gz, "wb") as f:
            f.write(b"-- restore sql\n")

        success_proc = MagicMock()
        success_proc.returncode = 0
        success_proc.stderr = b""
        success_proc.stdout = b""

        with (
            patch("services.backup_service.subprocess.run", return_value=success_proc) as mock_run,
            patch("services.backup_service.settings") as mock_settings,
            patch("services.backup_service.shutil.which", return_value="psql"),
        ):
            mock_settings.DATABASE_URL = "postgresql://bnkforge:secret@postgres:5432/bnkforge"
            svc._restore_database(dump_gz)

        # Verify subprocess.run was called at least 4 times (3 reset + 1 replay)
        assert mock_run.call_count >= 4

        # Verify the LAST call includes --file (the dump replay)
        last_call_args = mock_run.call_args_list[-1][0][0]
        assert "--file" in last_call_args

        # Uncompressed SQL file should be cleaned up
        sql_path = dump_gz.replace(".gz", "")
        assert not os.path.exists(sql_path)

    @pytest.mark.unit
    def test_restore_database_cleans_up_sql_file_on_success(self, tmp_path):
        """The decompressed .sql file is always removed, even on success."""
        svc = _make_service()

        dump_gz = str(tmp_path / "dump.sql.gz")
        with gzip.open(dump_gz, "wb") as f:
            f.write(b"-- sql\n")

        success_proc = MagicMock()
        success_proc.returncode = 0
        success_proc.stderr = b""

        with (
            patch("services.backup_service.subprocess.run", return_value=success_proc),
            patch("services.backup_service.settings") as mock_settings,
            patch("services.backup_service.shutil.which", return_value="psql"),
        ):
            mock_settings.DATABASE_URL = "postgresql://u:p@h:5432/db"
            svc._restore_database(dump_gz)

        sql_path = dump_gz.replace(".gz", "")
        assert not os.path.exists(sql_path)


# ---------------------------------------------------------------------------
# BR-046 test 8: _restore_database — failure
# ---------------------------------------------------------------------------

class TestRestoreDatabaseFailure:
    @pytest.mark.unit
    def test_restore_database_raises_on_psql_error(self, tmp_path):
        """When psql returns non-zero with ERROR in stderr, BackupError(RESTORE_FAILED) is raised."""
        svc = _make_service()

        dump_gz = str(tmp_path / "dump.sql.gz")
        with gzip.open(dump_gz, "wb") as f:
            f.write(b"-- sql\n")

        success_proc = MagicMock()
        success_proc.returncode = 0
        success_proc.stderr = b""

        failed_proc = MagicMock()
        failed_proc.returncode = 1
        failed_proc.stderr = b"psql: ERROR: relation 'users' does not exist"

        # First 3 subprocess.run calls (reset) succeed, 4th (dump replay) fails
        with (
            patch("services.backup_service.subprocess.run", side_effect=[success_proc, success_proc, success_proc, failed_proc]),
            patch("services.backup_service.settings") as mock_settings,
            patch("services.backup_service.shutil.which", return_value="psql"),
        ):
            mock_settings.DATABASE_URL = "postgresql://u:p@h:5432/db"
            with pytest.raises(BackupError) as exc_info:
                svc._restore_database(dump_gz)

        assert exc_info.value.code == BackupErrorCode.RESTORE_FAILED.value
        assert exc_info.value.status_code == 500

    @pytest.mark.unit
    def test_restore_database_cleans_up_sql_file_on_failure(self, tmp_path):
        """The decompressed .sql file is removed even when psql fails."""
        svc = _make_service()

        dump_gz = str(tmp_path / "dump.sql.gz")
        with gzip.open(dump_gz, "wb") as f:
            f.write(b"-- sql\n")

        success_proc = MagicMock()
        success_proc.returncode = 0
        success_proc.stderr = b""

        failed_proc = MagicMock()
        failed_proc.returncode = 1
        failed_proc.stderr = b"ERROR: fatal failure"

        # First 3 subprocess.run calls (reset) succeed, 4th (dump replay) fails
        with (
            patch("services.backup_service.subprocess.run", side_effect=[success_proc, success_proc, success_proc, failed_proc]),
            patch("services.backup_service.settings") as mock_settings,
            patch("services.backup_service.shutil.which", return_value="psql"),
        ):
            mock_settings.DATABASE_URL = "postgresql://u:p@h:5432/db"
            with pytest.raises(BackupError):
                svc._restore_database(dump_gz)

        sql_path = dump_gz.replace(".gz", "")
        assert not os.path.exists(sql_path)


# ---------------------------------------------------------------------------
# BR-046 test 9: _check_and_run_migrations — none needed (same head)
# ---------------------------------------------------------------------------

class TestCheckAndRunMigrationsNoneNeeded:
    @pytest.mark.unit
    def test_check_and_run_migrations_same_head_returns_empty(self):
        """When archive_alembic_head == code_head, returns [] without calling upgrade."""
        svc = _make_service()

        mock_cfg = MagicMock()
        mock_script = MagicMock()
        mock_script.get_current_head.return_value = "samerev123"
        mock_script.walk_revisions.return_value = []
        mock_upgrade = MagicMock()

        with (
            patch("alembic.config.Config", return_value=mock_cfg),
            patch("alembic.script.ScriptDirectory.from_config", return_value=mock_script),
            patch("alembic.command.upgrade", mock_upgrade),
        ):
            result = svc._check_and_run_migrations("samerev123")

        assert result == []
        # upgrade must NOT be called when heads match
        mock_upgrade.assert_not_called()

    @pytest.mark.unit
    def test_check_and_run_migrations_different_head_runs_upgrade(self):
        """When archive_alembic_head != code_head, alembic upgrade head is called."""
        svc = _make_service()

        mock_cfg = MagicMock()
        mock_script = MagicMock()
        mock_script.get_current_head.return_value = "newrev456"
        mock_script.walk_revisions.return_value = []
        mock_upgrade = MagicMock()

        with (
            patch("alembic.config.Config", return_value=mock_cfg),
            patch("alembic.script.ScriptDirectory.from_config", return_value=mock_script),
            patch("alembic.command.upgrade", mock_upgrade),
        ):
            result = svc._check_and_run_migrations("oldrev123")

        # upgrade must have been called
        mock_upgrade.assert_called_once_with(mock_cfg, "head")
        assert isinstance(result, list)

    @pytest.mark.unit
    def test_check_and_run_migrations_wraps_exception_as_backup_error(self):
        """Unexpected exceptions inside _check_and_run_migrations are wrapped as MIGRATION_FAILED."""
        svc = _make_service()

        with patch("alembic.config.Config", side_effect=Exception("alembic.ini not found")):
            with pytest.raises(BackupError) as exc_info:
                svc._check_and_run_migrations("somerev")

        assert exc_info.value.code == BackupErrorCode.MIGRATION_FAILED.value
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# BR-046 test 10: _replace_encryption_key — success
# ---------------------------------------------------------------------------

class TestReplaceEncryptionKeySuccess:
    @pytest.mark.unit
    def test_replace_encryption_key_writes_unwrapped_key(self, tmp_path):
        """_replace_encryption_key() writes the unwrapped key bytes to ENCRYPTION_KEY_FILE."""
        svc = _make_service()

        # Create a wrapped_key.enc file
        wrapped_data = {"salt": "abc", "nonce": "def", "ciphertext": "ghi"}
        wrapped_key_path = str(tmp_path / "wrapped_key.enc")
        with open(wrapped_key_path, "w") as f:
            json.dump(wrapped_data, f)

        # The key file will be written here
        key_output_path = str(tmp_path / "encryption.key")
        fake_raw_key = b"fake-fernet-key-bytes-32-chars-12"

        # unwrap_fernet_key is imported at module level — patch at that location
        with (
            patch("services.backup_service.unwrap_fernet_key", return_value=fake_raw_key),
            patch("services.backup_service.ENCRYPTION_KEY_FILE", key_output_path),
        ):
            svc._replace_encryption_key(wrapped_key_path, "correctpassphrase123")

        # Verify the key was written
        assert os.path.exists(key_output_path)
        with open(key_output_path, "rb") as f:
            written = f.read()
        assert written == fake_raw_key

    @pytest.mark.unit
    def test_replace_encryption_key_raises_on_invalid_passphrase(self, tmp_path):
        """Wrong passphrase causes unwrap_fernet_key to raise ValueError → BackupError(INVALID_PASSPHRASE)."""
        svc = _make_service()

        wrapped_data = {"salt": "abc", "nonce": "def", "ciphertext": "ghi"}
        wrapped_key_path = str(tmp_path / "wrapped_key.enc")
        with open(wrapped_key_path, "w") as f:
            json.dump(wrapped_data, f)

        with patch(
            "services.backup_service.unwrap_fernet_key",
            side_effect=ValueError("Invalid passphrase or corrupted data"),
        ):
            with pytest.raises(BackupError) as exc_info:
                svc._replace_encryption_key(wrapped_key_path, "wrongpassword123")

        assert exc_info.value.code == BackupErrorCode.INVALID_PASSPHRASE.value
        assert exc_info.value.status_code == 400

    @pytest.mark.unit
    def test_replace_encryption_key_creates_parent_directory(self, tmp_path):
        """_replace_encryption_key() creates the key directory if it doesn't exist."""
        svc = _make_service()

        wrapped_data = {"salt": "abc", "nonce": "def", "ciphertext": "ghi"}
        wrapped_key_path = str(tmp_path / "wrapped_key.enc")
        with open(wrapped_key_path, "w") as f:
            json.dump(wrapped_data, f)

        # Use a nested path that doesn't exist yet
        key_output_path = str(tmp_path / "keys" / "sub" / "encryption.key")
        fake_raw_key = b"fake-key-bytes"

        with (
            patch("services.backup_service.unwrap_fernet_key", return_value=fake_raw_key),
            patch("services.backup_service.ENCRYPTION_KEY_FILE", key_output_path),
        ):
            svc._replace_encryption_key(wrapped_key_path, "correctpassphrase123")

        assert os.path.exists(key_output_path)
