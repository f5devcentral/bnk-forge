"""
Backup and restore service.

Creates backup archives containing:
- PostgreSQL database dump (plain SQL, gzipped)
- Fernet encryption key (wrapped with user passphrase)
- Metadata (version, alembic head, timestamps)
"""
import gzip
import json
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings
from core.encryption import ENCRYPTION_KEY_FILE, unwrap_fernet_key, wrap_fernet_key
from core.errors import BackupError, BackupErrorCode
from core.maintenance import clear_maintenance_mode, get_maintenance_status, is_maintenance_mode, set_maintenance_mode
from services.audit_service import create_audit_log

logger = logging.getLogger(__name__)

# Tables to count for metadata
METADATA_TABLES = [
    "projects",
    "kubernetes_clusters",
    "ssh_credentials",
    "users",
    "bare_metal_hosts",
    "bare_metal_deployments",
    "bnk_deployable_release",
    "helm_charts",
    "stack_templates",
    "stack_instances",
]


class BackupService:
    """Service for creating and managing database backups."""

    def __init__(self, db: Session):
        self.db = db

    def create_backup(self, passphrase: str) -> str:
        """
        Create a backup archive.

        Returns:
            Path to the created .tar.gz archive (caller must clean up)

        Raises:
            BackupError: If backup operation fails
        """
        if is_maintenance_mode():
            raise BackupError(
                BackupErrorCode.BACKUP_IN_PROGRESS,
                "Another backup/restore operation is in progress",
            )

        logger.info("Starting backup creation")

        tmp_dir = tempfile.mkdtemp(prefix="bnkforge-backup-")
        try:
            # 1. Run pg_dump
            dump_path = os.path.join(tmp_dir, "dump.sql.gz")
            self._run_pg_dump(dump_path)

            # 2. Wrap encryption key
            wrapped_key_path = os.path.join(tmp_dir, "wrapped_key.enc")
            self._wrap_encryption_key(passphrase, wrapped_key_path)

            # 3. Build metadata
            metadata_path = os.path.join(tmp_dir, "metadata.json")
            self._build_metadata(metadata_path)

            # Read metadata back for audit logging
            with open(metadata_path) as f:
                metadata = json.load(f)

            # 4. Package into tar.gz
            timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            archive_name = f"bnkforge-backup-{timestamp}.tar.gz"
            archive_path = os.path.join(tempfile.gettempdir(), archive_name)

            with tarfile.open(archive_path, "w:gz") as tar:
                # wrapped_key.enc first — enables fast client-side passphrase
                # verification without reading the entire archive
                tar.add(wrapped_key_path, arcname="wrapped_key.enc")
                tar.add(metadata_path, arcname="metadata.json")
                tar.add(dump_path, arcname="dump.sql.gz")

            logger.info("Backup archive created: %s", archive_path)

            create_audit_log(
                self.db,
                action="backup_created",
                resource_type="system",
                status="success",
                details={
                    "archive_name": os.path.basename(archive_path),
                    "alembic_head": metadata.get("alembic_head"),
                    "table_counts": metadata.get("table_counts"),
                },
            )

            return archive_path

        except BackupError:
            raise
        except Exception as e:
            logger.error("Backup creation failed: %s", e)
            raise BackupError(
                BackupErrorCode.DUMP_FAILED,
                f"Backup creation failed: {e}",
            ) from e
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def get_backup_status(self) -> dict[str, object]:
        """Get current backup/restore operation status."""
        status = get_maintenance_status()
        if status:
            return {
                "in_progress": True,
                "operation": "restore",  # maintenance mode is only set during restore
                "started_at": status.get("started_at"),
                "message": status.get("message"),
            }
        return {
            "in_progress": False,
            "operation": None,
            "started_at": None,
            "message": None,
        }

    def _parse_database_url(self) -> dict[str, str]:
        """Parse DATABASE_URL into components for pg_dump.

        Handles both ``postgresql://`` and ``postgresql+psycopg2://`` schemes.
        """
        url = settings.DATABASE_URL
        # Strip driver suffix so urlparse handles the scheme correctly
        normalised = url.replace("postgresql+psycopg2://", "postgresql://")
        parsed = urlparse(normalised)
        return {
            "host": parsed.hostname or "localhost",
            "port": str(parsed.port or 5432),
            "user": parsed.username or "bnkforge",
            "password": parsed.password or "",
            "dbname": parsed.path.lstrip("/") or "bnkforge",
        }

    def _get_server_major_version(self) -> str:
        """Return the connected PostgreSQL server major version."""
        version = self.db.execute(text("SHOW server_version")).scalar()
        match = re.match(r"(\d+)", str(version))
        if not match:
            raise BackupError(
                BackupErrorCode.DUMP_FAILED,
                f"Could not determine PostgreSQL server version from: {version}",
            )
        return match.group(1)

    def _pg_client_command(self, base_command: str) -> str:
        """Prefer version-matched PostgreSQL client binaries when available."""
        try:
            server_major = self._get_server_major_version()
            versioned = shutil.which(f"{base_command}-{server_major}")
            if versioned:
                return versioned
        except BackupError:
            logger.warning("Falling back to generic %s client; could not determine DB server major version", base_command)

        generic = shutil.which(base_command)
        if generic:
            return generic

        raise BackupError(
            BackupErrorCode.DUMP_FAILED,
            f"Required PostgreSQL client not found: {base_command}",
        )

    def _run_pg_dump(self, output_path: str) -> None:
        """Run pg_dump and gzip the output to *output_path*."""
        db_config = self._parse_database_url()

        env = os.environ.copy()
        env["PGPASSWORD"] = db_config["password"]

        cmd = [
            self._pg_client_command("pg_dump"),
            "--host", db_config["host"],
            "--port", db_config["port"],
            "--username", db_config["user"],
            "--dbname", db_config["dbname"],
            "--format=plain",
            "--no-owner",
            "--no-privileges",
        ]

        logger.info("Running pg_dump for database %s", db_config["dbname"])

        result = subprocess.run(
            cmd,
            capture_output=True,
            env=env,
            timeout=300,  # 5-minute timeout
        )

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            logger.error("pg_dump failed: %s", stderr)
            raise BackupError(
                BackupErrorCode.DUMP_FAILED,
                f"pg_dump failed: {stderr[:500]}",
            )

        # Gzip the output
        with gzip.open(output_path, "wb") as f:
            f.write(result.stdout)

        logger.info(
            "Database dump completed: %s (%d bytes)",
            output_path,
            os.path.getsize(output_path),
        )

    def _wrap_encryption_key(self, passphrase: str, output_path: str) -> None:
        """Read the Fernet key from disk and wrap it with *passphrase*."""
        key_path = ENCRYPTION_KEY_FILE

        if not os.path.exists(key_path):
            raise BackupError(
                BackupErrorCode.DUMP_FAILED,
                f"Encryption key not found at {key_path}",
            )

        with open(key_path, "rb") as f:
            raw_key = f.read().strip()

        wrapped = wrap_fernet_key(raw_key, passphrase)

        with open(output_path, "w") as f:
            json.dump(wrapped, f, indent=2)

        logger.info("Encryption key wrapped successfully")

    # ------------------------------------------------------------------ #
    # BR-040: Archive validation                                          #
    # ------------------------------------------------------------------ #

    def validate_archive(self, archive_path: str) -> dict:
        """
        Validate and extract metadata from a backup archive.

        Args:
            archive_path: Path to the .tar.gz archive

        Returns:
            dict containing the parsed metadata.json

        Raises:
            BackupError: If archive is invalid or incompatible
        """
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                # Check required files exist
                names = tar.getnames()
                required = ["metadata.json", "dump.sql.gz", "wrapped_key.enc"]
                missing = [f for f in required if f not in names]
                if missing:
                    raise BackupError(
                        BackupErrorCode.INVALID_ARCHIVE,
                        f"Archive missing required files: {missing}",
                    )

                # Extract and parse metadata
                metadata_file = tar.extractfile("metadata.json")
                if not metadata_file:
                    raise BackupError(
                        BackupErrorCode.INVALID_ARCHIVE,
                        "Could not read metadata.json from archive",
                    )
                metadata = json.load(metadata_file)

                # Validate format version
                format_version = metadata.get("format_version", 0)
                if format_version > 1:
                    raise BackupError(
                        BackupErrorCode.INCOMPATIBLE_FORMAT,
                        f"Archive format version {format_version} not supported (max: 1)",
                    )

                return metadata

        except tarfile.TarError as e:
            raise BackupError(
                BackupErrorCode.INVALID_ARCHIVE,
                f"Invalid archive format: {e}",
            )

    def _extract_archive(self, archive_path: str, target_dir: str) -> None:
        """Extract a validated backup archive without allowing path traversal."""
        target_root = Path(target_dir).resolve()

        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                for member in tar.getmembers():
                    member_path = (target_root / member.name).resolve()
                    if os.path.commonpath([str(target_root), str(member_path)]) != str(target_root):
                        raise BackupError(
                            BackupErrorCode.INVALID_ARCHIVE,
                            f"Archive contains unsafe path: {member.name}",
                        )

                tar.extractall(target_dir)  # noqa: S202
        except tarfile.TarError as e:
            raise BackupError(
                BackupErrorCode.INVALID_ARCHIVE,
                f"Invalid archive format: {e}",
            ) from e

    # ------------------------------------------------------------------ #
    # BR-041: Database restore                                            #
    # ------------------------------------------------------------------ #

    def _restore_database(self, dump_path: str) -> None:
        """
        Restore database from a SQL dump file.

        The restore is destructive: it terminates other sessions, drops the
        target database, recreates it, then replays the plain-SQL dump.
        This avoids conflicts with existing tables, extensions, or migration
        state left behind by a schema-only reset.

        Args:
            dump_path: Path to the gzipped SQL dump

        Raises:
            BackupError: If restore fails
        """
        db_config = self._parse_database_url()
        dbname = db_config["dbname"]

        env = os.environ.copy()
        env["PGPASSWORD"] = db_config["password"]

        psql_bin = self._pg_client_command("psql")

        def _run_psql(database: str, command: str, *, timeout: int = 120) -> None:
            """Run a single psql command against *database*, raising on failure."""
            cmd = [
                psql_bin,
                "--host", db_config["host"],
                "--port", db_config["port"],
                "--username", db_config["user"],
                "--dbname", database,
                "--quiet",
                "--set", "ON_ERROR_STOP=on",
                "--command", command,
            ]
            result = subprocess.run(cmd, capture_output=True, env=env, timeout=timeout)
            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                raise BackupError(
                    BackupErrorCode.RESTORE_FAILED,
                    f"Database command failed: {stderr[:500]}",
                )

        # ------------------------------------------------------------------
        # 1. Terminate active connections and drop/recreate the target DB
        # ------------------------------------------------------------------
        logger.info("Dropping and recreating database %s", dbname)
        try:
            _run_psql(
                "postgres",
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{dbname}' AND pid <> pg_backend_pid();",
            )
            _run_psql("postgres", f'DROP DATABASE IF EXISTS "{dbname}";')
            _run_psql("postgres", f'CREATE DATABASE "{dbname}" OWNER "{db_config["user"]}";')
        except BackupError:
            raise
        except Exception as e:
            raise BackupError(
                BackupErrorCode.RESTORE_FAILED,
                f"Database reset failed: {e}",
            ) from e

        # ------------------------------------------------------------------
        # 2. Decompress and sanitise the SQL dump
        # ------------------------------------------------------------------
        sql_path = dump_path.replace(".gz", "")
        with gzip.open(dump_path, "rb") as f_in:
            with open(sql_path, "wb") as f_out:
                f_out.write(f_in.read())

        try:
            # pg_dump from newer majors can emit SET commands unsupported by
            # older servers (e.g. transaction_timeout from PG17 dumps).
            with open(sql_path, encoding="utf-8") as f:
                sql_lines = f.readlines()

            with open(sql_path, "w", encoding="utf-8") as f:
                for line in sql_lines:
                    if line.lstrip().startswith("SET transaction_timeout ="):
                        continue
                    f.write(line)

            # ------------------------------------------------------------------
            # 3. Replay the dump into the fresh database
            # ------------------------------------------------------------------
            cmd = [
                psql_bin,
                "--host", db_config["host"],
                "--port", db_config["port"],
                "--username", db_config["user"],
                "--dbname", dbname,
                "--file", sql_path,
                "--quiet",
                "--set", "ON_ERROR_STOP=on",
            ]

            logger.info("Restoring database %s from %s", dbname, dump_path)

            result = subprocess.run(
                cmd,
                capture_output=True,
                env=env,
                timeout=600,  # 10-minute timeout for large restores
            )

            if result.returncode != 0:
                stderr = result.stderr.decode("utf-8", errors="replace")
                if "ERROR:" in stderr.upper():
                    logger.error("psql restore failed: %s", stderr)
                    raise BackupError(
                        BackupErrorCode.RESTORE_FAILED,
                        f"Database restore failed: {stderr[:500]}",
                    )
                else:
                    logger.warning("psql restore completed with warnings: %s", stderr[:200])

            logger.info("Database restore completed")

        finally:
            if os.path.exists(sql_path):
                os.unlink(sql_path)

    # ------------------------------------------------------------------ #
    # BR-042: Migration check and upgrade                                 #
    # ------------------------------------------------------------------ #

    def _check_and_run_migrations(self, archive_alembic_head: str) -> list[str]:
        """
        Check if migrations need to run and execute them if needed.

        Args:
            archive_alembic_head: The alembic version from the backup archive

        Returns:
            List of migration IDs that were applied

        Raises:
            BackupError: If migration fails
        """
        from alembic.config import Config  # noqa: PLC0415
        from alembic.script import ScriptDirectory  # noqa: PLC0415

        from alembic import command

        try:
            # Get current code's alembic head
            alembic_cfg = Config("alembic.ini")
            script = ScriptDirectory.from_config(alembic_cfg)
            code_head = script.get_current_head()

            if archive_alembic_head == code_head:
                logger.info("Database already at current schema version: %s", code_head)
                return []

            # Check if archive head is in the revision history
            revisions = list(script.walk_revisions())
            revision_ids = [r.revision for r in revisions]

            if archive_alembic_head not in revision_ids and archive_alembic_head != "unknown":
                logger.warning(
                    "Archive alembic head %s not found in revision history. "
                    "This may be from a newer version of the software.",
                    archive_alembic_head,
                )

            # Run alembic upgrade head — use a FRESH Config object to avoid
            # shared state with the ScriptDirectory created above.  Reusing
            # the same Config causes env.py / run_migrations_online() to
            # execute twice, leading to DuplicateTable on non-idempotent DDL.
            logger.info("Running migrations from %s to %s", archive_alembic_head, code_head)
            upgrade_cfg = Config("alembic.ini")
            command.upgrade(upgrade_cfg, "head")

            # Determine which migrations were applied
            applied = []
            found_archive_head = False
            for rev in reversed(revisions):  # Walk from oldest to newest
                if rev.revision == archive_alembic_head:
                    found_archive_head = True
                    continue
                if found_archive_head or archive_alembic_head == "unknown":
                    applied.append(rev.revision)
                if rev.revision == code_head:
                    break

            logger.info("Applied %d migrations", len(applied))
            return applied

        except BackupError:
            raise
        except Exception as e:
            logger.error("Migration failed: %s", e)
            raise BackupError(
                BackupErrorCode.MIGRATION_FAILED,
                f"Database migration failed: {e}",
            ) from e

    # ------------------------------------------------------------------ #
    # BR-043: Encryption key replacement                                  #
    # ------------------------------------------------------------------ #

    def _replace_encryption_key(self, wrapped_key_path: str, passphrase: str) -> None:
        """
        Unwrap and replace the encryption key.

        Args:
            wrapped_key_path: Path to the wrapped_key.enc file
            passphrase: User-provided passphrase

        Raises:
            BackupError: If passphrase is wrong or key is corrupted
        """
        with open(wrapped_key_path) as f:
            wrapped = json.load(f)

        try:
            raw_key = unwrap_fernet_key(wrapped, passphrase)
        except ValueError as e:
            raise BackupError(
                BackupErrorCode.INVALID_PASSPHRASE,
                f"Invalid passphrase: {e}",
            ) from e

        # Write the unwrapped key to the key file
        key_path = ENCRYPTION_KEY_FILE

        # Create directory if needed
        os.makedirs(os.path.dirname(key_path), exist_ok=True)

        with open(key_path, "wb") as f:
            f.write(raw_key)

        logger.info("Encryption key replaced at %s", key_path)

    # ------------------------------------------------------------------ #
    # BR-044: Full restore orchestration                                  #
    # ------------------------------------------------------------------ #

    def restore_backup(self, archive_path: str, passphrase: str) -> dict:
        """
        Restore a complete backup archive.

        This is a destructive operation that:
        1. Validates the archive
        2. Verifies the passphrase can unwrap the key
        3. Enters maintenance mode
        4. Restores the database
        5. Clears Redis cache (to avoid serving stale data)
        6. Runs any needed migrations
        7. Replaces the encryption key
        8. Triggers backend restart (clears maintenance mode)

        Args:
            archive_path: Path to the uploaded archive
            passphrase: Passphrase to unwrap the encryption key

        Returns:
            dict with restore results (tables_restored, migrations_applied, warnings)

        Raises:
            BackupError: If restore fails at any stage
        """
        if is_maintenance_mode():
            raise BackupError(
                BackupErrorCode.RESTORE_IN_PROGRESS,
                "Another restore operation is in progress",
            )

        create_audit_log(
            self.db,
            action="restore_started",
            resource_type="system",
            status="success",
            details={"archive_name": os.path.basename(archive_path)},
        )

        warnings: list[str] = []
        migrations_applied: list[str] = []

        # Create temp directory for extraction
        tmp_dir = tempfile.mkdtemp(prefix="bnkforge-restore-")

        try:
            # 1. Validate archive structure
            logger.info("Validating backup archive")
            metadata = self.validate_archive(archive_path)

            # 2. Extract archive
            logger.info("Extracting archive")
            self._extract_archive(archive_path, tmp_dir)

            # 3. Verify passphrase BEFORE entering maintenance mode
            logger.info("Verifying passphrase")
            wrapped_key_path = os.path.join(tmp_dir, "wrapped_key.enc")
            with open(wrapped_key_path) as f:
                wrapped = json.load(f)
            try:
                unwrap_fernet_key(wrapped, passphrase)  # Just verify, don't save yet
            except ValueError:
                raise BackupError(
                    BackupErrorCode.INVALID_PASSPHRASE,
                    "Invalid passphrase — cannot decrypt the encryption key",
                )

            # 4. Check version compatibility
            archive_version = metadata.get("forge_version", "unknown")
            current_version = settings.VERSION
            if archive_version != current_version:
                warnings.append(
                    f"Archive from version {archive_version}, restoring to {current_version}"
                )

            # 5. Enter maintenance mode
            logger.info("Entering maintenance mode")
            set_maintenance_mode("Database restore in progress. Please wait...")

            try:
                # 6. Close our own DB session so pg_terminate + DROP DATABASE
                #    are not blocked by this process's connection.
                self.db.close()

                # 7. Restore database (drops and recreates the target DB)
                dump_path = os.path.join(tmp_dir, "dump.sql.gz")
                self._restore_database(dump_path)

                # 7b. Purge stale connection pool — all pooled connections
                # were opened against the old database that was just dropped
                # and recreated.  Without this, subsequent DB operations
                # (migrations, audit log) may use dead connections.
                from database import engine
                engine.dispose()

                # 8. Clear Redis cache after database restore to avoid serving
                #    stale data from before the restore
                logger.info("Clearing Redis cache after database restore")
                from core.cache import cache
                if not cache.clear_all():
                    warnings.append("Failed to clear Redis cache - manual flush may be needed")
                    logger.warning("Redis cache clear failed - stale data may be served")

                # 9. Run migrations if needed
                archive_head = metadata.get("alembic_head", "unknown")
                migrations_applied = self._check_and_run_migrations(archive_head)

                # 10. Replace encryption key
                self._replace_encryption_key(wrapped_key_path, passphrase)

                # Get table counts from metadata
                table_counts = metadata.get("table_counts", {})

                result = {
                    "status": "success",
                    "table_counts": table_counts,
                    "migrations_applied": migrations_applied,
                    "warnings": warnings,
                    "restart_triggered": True,
                }

                logger.info("Restore completed successfully: %s", result)

                # Clear maintenance mode now — the backend restart that
                # follows will bring the app back clean.  The 10-minute
                # TTL is only a safety net for crash scenarios.
                clear_maintenance_mode()

                create_audit_log(
                    self.db,
                    action="restore_completed",
                    resource_type="system",
                    status="success",
                    details={
                        "table_counts": table_counts,
                        "migrations_applied": migrations_applied,
                        "warnings": warnings,
                    },
                )

                return result

            except BackupError as e:
                # Clear maintenance mode on failure
                clear_maintenance_mode()
                create_audit_log(
                    self.db,
                    action="restore_failed",
                    resource_type="system",
                    status="error",
                    details={"error": str(e)},
                )
                raise
            except Exception as e:
                clear_maintenance_mode()
                create_audit_log(
                    self.db,
                    action="restore_failed",
                    resource_type="system",
                    status="error",
                    details={"error": str(e)},
                )
                raise BackupError(
                    BackupErrorCode.RESTORE_FAILED,
                    f"Restore failed: {e}",
                ) from e

        finally:
            # Clean up temp directory
            if os.path.exists(tmp_dir):
                shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # Private helpers (original)                                          #
    # ------------------------------------------------------------------ #

    def _build_metadata(self, output_path: str) -> None:
        """Build metadata.json with version info and table counts."""
        # Get alembic head
        try:
            result = self.db.execute(text("SELECT version_num FROM alembic_version"))
            row = result.fetchone()
            alembic_head = row[0] if row else "unknown"
        except Exception as exc:
            logger.warning("Could not read alembic version: %s", exc)
            alembic_head = "unknown"

        # Get table counts — table names are module-level constants, not user input
        table_counts: dict[str, int] = {}
        for table in METADATA_TABLES:
            try:
                count_result = self.db.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
                table_counts[table] = count_result.scalar() or 0
            except Exception:
                table_counts[table] = -1  # Table may not exist in this schema version

        metadata = {
            "format_version": 1,
            "forge_version": settings.VERSION,
            "alembic_head": alembic_head,
            "created_at": datetime.now(UTC).isoformat(),
            "table_counts": table_counts,
            "includes": ["database", "encryption_key"],
        }

        with open(output_path, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.info(
            "Metadata built: alembic_head=%s, tables=%d",
            alembic_head,
            len(table_counts),
        )
