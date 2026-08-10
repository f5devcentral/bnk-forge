"""
BR-035: Unit tests for services.backup_service.BackupService.

All tests run without a real DB or Docker:
- subprocess.run is mocked to avoid calling pg_dump
- file I/O is mocked to avoid touching the real key file
- maintenance / is_maintenance_mode are mocked at the module level
- DB session is a MagicMock
"""
import gzip
import json
import os
import tarfile
import tempfile
from datetime import UTC, datetime, timezone
from unittest.mock import MagicMock, patch

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


def _mock_db_for_metadata(db: MagicMock) -> None:
    """Configure the mock DB so alembic + table-count queries return fixture data."""

    def _execute_side_effect(stmt, *args, **kwargs):
        sql = str(stmt).lower()
        mock_result = MagicMock()
        if "alembic_version" in sql:
            mock_result.fetchone.return_value = ("abc123def456",)
        else:
            mock_result.scalar.return_value = 5
        return mock_result

    db.execute.side_effect = _execute_side_effect


# ---------------------------------------------------------------------------
# BR-035 test 1: parse DATABASE_URL
# ---------------------------------------------------------------------------

class TestParseDatabaseUrl:
    @pytest.mark.unit
    def test_parse_standard_postgresql_url(self):
        """Standard postgresql:// URL is parsed correctly."""
        svc = _make_service()
        with patch("services.backup_service.settings") as mock_settings:
            mock_settings.DATABASE_URL = "postgresql://myuser:mypass@dbhost:5433/mydb"
            parsed = svc._parse_database_url()

        assert parsed["host"] == "dbhost"
        assert parsed["port"] == "5433"
        assert parsed["user"] == "myuser"
        assert parsed["password"] == "mypass"
        assert parsed["dbname"] == "mydb"

    @pytest.mark.unit
    def test_parse_psycopg2_scheme_url(self):
        """postgresql+psycopg2:// scheme is normalised before parsing."""
        svc = _make_service()
        with patch("services.backup_service.settings") as mock_settings:
            mock_settings.DATABASE_URL = "postgresql+psycopg2://bnkforge:secret@postgres:5432/bnkforge"
            parsed = svc._parse_database_url()

        assert parsed["host"] == "postgres"
        assert parsed["port"] == "5432"
        assert parsed["user"] == "bnkforge"
        assert parsed["password"] == "secret"
        assert parsed["dbname"] == "bnkforge"

    @pytest.mark.unit
    def test_parse_url_with_defaults(self):
        """Missing host/port/dbname fall back to sensible defaults."""
        svc = _make_service()
        with patch("services.backup_service.settings") as mock_settings:
            mock_settings.DATABASE_URL = "postgresql://user:pw@/db"
            parsed = svc._parse_database_url()

        assert parsed["dbname"] == "db"


# ---------------------------------------------------------------------------
# BR-035 test 2: create_backup produces a valid archive
# ---------------------------------------------------------------------------

class TestCreateBackupProducesValidArchive:
    @pytest.mark.unit
    def test_archive_contains_required_members(self, tmp_path):
        """create_backup() returns a .tar.gz with metadata.json, dump.sql.gz, wrapped_key.enc.

        Strategy: mock the three private helpers (_run_pg_dump, _wrap_encryption_key,
        _build_metadata) so the integration loop can run against real tempfiles
        without invoking subprocess or touching the filesystem key.
        """
        db = MagicMock()
        svc = _make_service(db)

        def fake_run_pg_dump(output_path: str) -> None:
            """Write a minimal gzip file to satisfy the archive step."""
            with gzip.open(output_path, "wb") as f:
                f.write(b"-- fake pg_dump\nSELECT 1;\n")

        def fake_wrap_key(passphrase: str, output_path: str) -> None:
            """Write a minimal JSON file in place of the wrapped key."""
            with open(output_path, "w") as f:
                json.dump({"salt": "abc", "nonce": "def", "ciphertext": "ghi"}, f)

        def fake_build_metadata(output_path: str) -> None:
            """Write minimal metadata JSON."""
            with open(output_path, "w") as f:
                json.dump(
                    {
                        "format_version": 1,
                        "forge_version": "1.0.0",
                        "alembic_head": "abc123",
                        "created_at": datetime.now(UTC).isoformat(),
                        "table_counts": {},
                        "includes": ["database", "encryption_key"],
                    },
                    f,
                )

        with (
            patch("services.backup_service.is_maintenance_mode", return_value=False),
            patch.object(svc, "_run_pg_dump", side_effect=fake_run_pg_dump),
            patch.object(svc, "_wrap_encryption_key", side_effect=fake_wrap_key),
            patch.object(svc, "_build_metadata", side_effect=fake_build_metadata),
        ):
            archive_path = svc.create_backup("strongpassphrase123")

        try:
            assert archive_path.endswith(".tar.gz")
            assert os.path.exists(archive_path)

            with tarfile.open(archive_path, "r:gz") as tar:
                names = tar.getnames()

            assert "metadata.json" in names
            assert "dump.sql.gz" in names
            assert "wrapped_key.enc" in names
        finally:
            if os.path.exists(archive_path):
                os.unlink(archive_path)


# ---------------------------------------------------------------------------
# BR-035 test 3: create_backup rejects during maintenance mode
# ---------------------------------------------------------------------------

class TestCreateBackupRejectsDuringMaintenance:
    @pytest.mark.unit
    def test_raises_backup_in_progress_when_maintenance_active(self):
        """If maintenance mode is active, create_backup raises BackupError (409)."""
        svc = _make_service()
        with patch("services.backup_service.is_maintenance_mode", return_value=True):
            with pytest.raises(BackupError) as exc_info:
                svc.create_backup("strongpassphrase123")

        assert exc_info.value.code == BackupErrorCode.BACKUP_IN_PROGRESS.value
        assert exc_info.value.status_code == 409


# ---------------------------------------------------------------------------
# BR-035 test 4: pg_dump failure raises BackupError
# ---------------------------------------------------------------------------

class TestPgDumpFailureRaisesBackupError:
    @pytest.mark.unit
    def test_nonzero_returncode_raises_dump_failed(self):
        """If pg_dump returns non-zero, BackupError(DUMP_FAILED) is raised."""
        svc = _make_service()

        failed_proc = MagicMock()
        failed_proc.returncode = 1
        failed_proc.stdout = b""
        failed_proc.stderr = b"pg_dump: error: connection to server failed"

        with (
            patch("services.backup_service.is_maintenance_mode", return_value=False),
            patch("services.backup_service.subprocess.run", return_value=failed_proc),
            patch("services.backup_service.settings") as mock_settings,
        ):
            mock_settings.DATABASE_URL = "postgresql://u:p@h:5432/db"
            mock_settings.VERSION = "1.0.0"
            with pytest.raises(BackupError) as exc_info:
                svc.create_backup("strongpassphrase123")

        assert exc_info.value.code == BackupErrorCode.DUMP_FAILED.value
        assert exc_info.value.status_code == 500


# ---------------------------------------------------------------------------
# BR-035 test 5: missing encryption key raises error
# ---------------------------------------------------------------------------

class TestMissingEncryptionKeyRaisesError:
    @pytest.mark.unit
    def test_raises_dump_failed_when_key_file_absent(self):
        """If the encryption key file does not exist, BackupError is raised."""
        db = MagicMock()
        _mock_db_for_metadata(db)
        svc = _make_service(db)

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = b"-- sql"
        fake_proc.stderr = b""

        with (
            patch("services.backup_service.is_maintenance_mode", return_value=False),
            patch("services.backup_service.subprocess.run", return_value=fake_proc),
            # Key file does NOT exist
            patch("services.backup_service.os.path.exists", return_value=False),
            patch("services.backup_service.settings") as mock_settings,
        ):
            mock_settings.DATABASE_URL = "postgresql://u:p@h:5432/db"
            mock_settings.VERSION = "1.0.0"
            with pytest.raises(BackupError) as exc_info:
                svc.create_backup("strongpassphrase123")

        assert exc_info.value.code == BackupErrorCode.DUMP_FAILED.value


# ---------------------------------------------------------------------------
# BR-035 test 6: _build_metadata includes required fields
# ---------------------------------------------------------------------------

class TestBuildMetadataIncludesRequiredFields:
    @pytest.mark.unit
    def test_metadata_has_required_top_level_keys(self, tmp_path):
        """_build_metadata() writes JSON with all required fields."""
        db = MagicMock()
        _mock_db_for_metadata(db)
        svc = _make_service(db)

        metadata_path = str(tmp_path / "metadata.json")
        with patch("services.backup_service.settings") as mock_settings:
            mock_settings.VERSION = "2.0.1"
            svc._build_metadata(metadata_path)

        with open(metadata_path) as f:
            meta = json.load(f)

        assert meta["format_version"] == 1
        assert meta["forge_version"] == "2.0.1"
        assert meta["alembic_head"] == "abc123def456"
        assert "created_at" in meta
        assert "table_counts" in meta
        assert "includes" in meta
        assert "database" in meta["includes"]
        assert "encryption_key" in meta["includes"]

    @pytest.mark.unit
    def test_metadata_created_at_is_iso8601(self, tmp_path):
        """created_at is a valid ISO-8601 datetime string."""
        db = MagicMock()
        _mock_db_for_metadata(db)
        svc = _make_service(db)

        metadata_path = str(tmp_path / "metadata.json")
        with patch("services.backup_service.settings") as mock_settings:
            mock_settings.VERSION = "1.0.0"
            svc._build_metadata(metadata_path)

        with open(metadata_path) as f:
            meta = json.load(f)

        # Should not raise
        datetime.fromisoformat(meta["created_at"])

    @pytest.mark.unit
    def test_metadata_falls_back_to_unknown_alembic_head_on_db_error(self, tmp_path):
        """If the DB query fails, alembic_head is 'unknown' (not an exception)."""
        db = MagicMock()
        db.execute.side_effect = Exception("DB connection lost")
        svc = _make_service(db)

        metadata_path = str(tmp_path / "metadata.json")
        with patch("services.backup_service.settings") as mock_settings:
            mock_settings.VERSION = "1.0.0"
            svc._build_metadata(metadata_path)  # Must not raise

        with open(metadata_path) as f:
            meta = json.load(f)

        assert meta["alembic_head"] == "unknown"


# ---------------------------------------------------------------------------
# BR-035 test 7: get_backup_status — no operation in progress
# ---------------------------------------------------------------------------

class TestGetBackupStatusNoOperation:
    @pytest.mark.unit
    def test_returns_not_in_progress_when_no_maintenance(self):
        """When maintenance mode is off, status reports in_progress=False."""
        svc = _make_service()
        with patch("services.backup_service.get_maintenance_status", return_value=None):
            result = svc.get_backup_status()

        assert result["in_progress"] is False
        assert result["operation"] is None
        assert result["started_at"] is None
        assert result["message"] is None


# ---------------------------------------------------------------------------
# BR-035 test 8: get_backup_status — during maintenance mode
# ---------------------------------------------------------------------------

class TestGetBackupStatusDuringMaintenance:
    @pytest.mark.unit
    def test_returns_in_progress_with_operation_restore(self):
        """During maintenance mode, status reports in_progress=True and operation='restore'."""
        svc = _make_service()
        fake_status = {
            "message": "System restore in progress. Please wait.",
            "started_at": datetime.now(UTC).isoformat(),
        }
        with patch("services.backup_service.get_maintenance_status", return_value=fake_status):
            result = svc.get_backup_status()

        assert result["in_progress"] is True
        assert result["operation"] == "restore"
        assert result["started_at"] == fake_status["started_at"]
        assert result["message"] == fake_status["message"]
