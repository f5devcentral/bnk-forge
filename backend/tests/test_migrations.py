"""
Tests for Alembic database migrations.

Validates that migrations can be applied and rolled back without errors.
Uses SQLite in-memory database for speed — not all PostgreSQL-specific
features may be exercised, but structural integrity is verified.

Run: pytest tests/test_migrations.py -v
"""

import os
import sys
import tempfile

import pytest

# Environment setup — must happen before any backend imports
os.environ.setdefault("DATABASE_URL", "sqlite:///file::memory:?cache=shared")
os.environ.setdefault("REQUIRE_AUTH", "true")
os.environ.setdefault("ENVIRONMENT", "development")

# Ensure encryption key exists
_tmp_key_dir = tempfile.mkdtemp()
_encryption_key_file = os.path.join(_tmp_key_dir, "encryption.key")
os.environ.setdefault("ENCRYPTION_KEY_FILE", _encryption_key_file)

# Ensure backend is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)


class TestMigrationStructure:
    """Verify migration file structure and integrity."""

    def test_alembic_ini_exists(self):
        """alembic.ini must exist in the backend directory."""
        ini_path = os.path.join(backend_path, "alembic.ini")
        assert os.path.isfile(ini_path), "alembic.ini not found"

    def test_versions_directory_exists(self):
        """Migration versions directory must exist."""
        versions_dir = os.path.join(backend_path, "alembic", "versions")
        assert os.path.isdir(versions_dir), "alembic/versions/ not found"

    def test_migrations_exist(self):
        """At least one migration file should exist."""
        versions_dir = os.path.join(backend_path, "alembic", "versions")
        migration_files = [
            f for f in os.listdir(versions_dir)
            if f.endswith(".py") and not f.startswith("__")
        ]
        assert len(migration_files) > 0, "No migration files found"

    def test_single_head(self):
        """Migration chain should have a single head (no forks)."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config(os.path.join(backend_path, "alembic.ini"))
        alembic_cfg.set_main_option("script_location", os.path.join(backend_path, "alembic"))

        script = ScriptDirectory.from_config(alembic_cfg)
        heads = script.get_heads()

        assert len(heads) == 1, (
            f"Migration chain has {len(heads)} heads (expected 1). "
            f"Heads: {heads}. Fix by merging branches."
        )

    def test_no_revision_gaps(self):
        """Every revision should have a valid down_revision (except the first)."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        alembic_cfg = Config(os.path.join(backend_path, "alembic.ini"))
        alembic_cfg.set_main_option("script_location", os.path.join(backend_path, "alembic"))

        script = ScriptDirectory.from_config(alembic_cfg)
        revisions = list(script.walk_revisions())

        # All revisions except the base should have a down_revision
        base_count = sum(1 for r in revisions if r.down_revision is None)
        assert base_count == 1, f"Expected exactly 1 base migration, found {base_count}"


class TestMigrationExecution:
    """Test that migrations can be applied to a database.

    Note: BNK-Forge v2 migrations are incremental — they start from v2_001
    which assumes tables already exist (ALTER TABLE). So we test by first
    creating the schema from models, then stamping as base, then verifying
    the model-based schema is consistent.
    """

    def test_models_create_all_succeeds(self):
        """SQLAlchemy Base.metadata.create_all() should succeed on a fresh DB.

        This verifies all model definitions are consistent and can create
        a valid schema — which is what the initial deployment uses.
        """
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool

        import models  # noqa: F401 — triggers all model imports
        from database import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # This should not raise
        Base.metadata.create_all(bind=engine)

        # Verify tables were created
        from sqlalchemy import inspect
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        assert len(tables) > 10, f"Expected 10+ tables, got {len(tables)}: {tables}"

        engine.dispose()

    def test_all_model_tables_have_primary_keys(self):
        """Every model table should have a primary key defined."""
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.pool import StaticPool

        import models  # noqa: F401
        from database import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)

        inspector = inspect(engine)
        for table_name in inspector.get_table_names():
            pk = inspector.get_pk_constraint(table_name)
            # Association tables may use composite PKs — that's fine
            # But every table should have at least one PK column
            assert pk and (pk.get("constrained_columns") or pk.get("name")), (
                f"Table '{table_name}' has no primary key"
            )

        engine.dispose()

    def test_migration_scripts_are_importable(self):
        """Every migration script should be importable (no syntax errors)."""
        versions_dir = os.path.join(backend_path, "alembic", "versions")

        import importlib.util

        for filename in sorted(os.listdir(versions_dir)):
            if not filename.endswith(".py") or filename.startswith("__"):
                continue

            filepath = os.path.join(versions_dir, filename)
            spec = importlib.util.spec_from_file_location(
                f"migration_{filename}", filepath
            )
            assert spec is not None, f"Cannot create spec for {filename}"

            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as e:
                pytest.fail(f"Migration {filename} failed to import: {e}")

            # Every migration should have upgrade and downgrade functions
            assert hasattr(module, "upgrade"), f"{filename} missing upgrade()"
            assert hasattr(module, "downgrade"), f"{filename} missing downgrade()"

    def test_v2_053_discovery_contract_tables_present(self):
        """DISCOVERY-PORT-001: model/migration contract for v2_053 is present.

        Repo convention here validates migration viability through model-based schema
        creation + structural inspection, rather than live per-revision migration runs.
        """
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.pool import StaticPool

        import models  # noqa: F401
        from database import Base

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)

        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert "discovery_jobs" in tables
        assert "discovered_nodes" in tables

        discovery_job_columns = {col["name"] for col in inspector.get_columns("discovery_jobs")}
        assert {
            "project_id",
            "ssh_credential_id",
            "status",
            "total_nodes",
            "completed_nodes",
            "failed_nodes",
            "celery_task_id",
            "ssh_password_encrypted",
            "ssh_key_encrypted",
        }.issubset(discovery_job_columns)

        discovered_node_columns = {col["name"] for col in inspector.get_columns("discovered_nodes")}
        assert {
            "discovery_job_id",
            "ip_address",
            "ssh_credential_id",
            "status",
            "ssh_password_encrypted",
            "ssh_key_encrypted",
            "is_dpu_host",
            "is_dpu_node",
            "dpu_count",
            "k8s_installed",
            "k8s_running",
        }.issubset(discovered_node_columns)

        discovery_job_indexes = {idx["name"] for idx in inspector.get_indexes("discovery_jobs")}
        assert "idx_discovery_job_project_status" in discovery_job_indexes

        discovered_node_indexes = {idx["name"] for idx in inspector.get_indexes("discovered_nodes")}
        assert "idx_discovered_node_job_ip" in discovered_node_indexes

        discovered_node_foreign_keys = inspector.get_foreign_keys("discovered_nodes")
        referred_tables = {fk["referred_table"] for fk in discovered_node_foreign_keys}
        assert "discovery_jobs" in referred_tables
        assert "ssh_credentials" in referred_tables

        engine.dispose()

    def test_no_orm_vs_db_drift_on_provisioned_schema(self):
        """Provision the production-faithful way, then assert the drift gate finds nothing.

        WO3 keystone (structural half). BNK-Forge v2's migration chain is NOT
        self-contained from empty (base revision v2_001 is an ``ALTER TABLE`` that
        presupposes a v1 schema), so ``alembic upgrade head`` from empty cannot
        run on any dialect — and SQLite cannot perform the constraint-ALTER
        downgrades either. Fresh installs therefore provision via
        ``Base.metadata.create_all`` + stamp head (what init_db.py does), then
        existing DBs run ``alembic upgrade head``.

        This test mirrors that: build the schema from the ORM, then run the SAME
        drift detector the runtime gate uses (``_detect_missing_schema``). It must
        report ZERO missing-from-DB drift. The full migration chain is executed
        against real Postgres in the CI ``migration-roundtrip`` job (SQLite cannot
        exercise the real migration DDL).
        """
        from sqlalchemy import create_engine

        import models  # noqa: F401 — register all tables on Base.metadata
        from database import Base
        from startup_steps import _detect_missing_schema

        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Base.metadata.create_all(bind=engine)
            with engine.connect() as conn:
                missing = _detect_missing_schema(conn)
            assert missing == [], (
                "ORM declares schema the provisioned DB is MISSING — models and the "
                f"create_all schema diverged. Missing: {missing}"
            )
        finally:
            engine.dispose()
            os.unlink(db_path)

    def test_drift_gate_flags_missing_column(self):
        """The drift detector must FLAG a column the ORM expects but the DB lacks.

        Builds a DB from the full ORM schema, then drops one column, and asserts
        ``_detect_missing_schema`` reports exactly that column as missing. Proves
        the gate fails on synthetic drift (not just passes on a matched schema).
        """
        from sqlalchemy import create_engine, inspect, text

        import models  # noqa: F401
        from database import Base
        from startup_steps import _detect_missing_schema

        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        db_url = f"sqlite:///{db_path}"
        engine = create_engine(db_url)
        try:
            Base.metadata.create_all(bind=engine)

            # Pick a real table with a droppable (non-PK) column.
            inspector = inspect(engine)
            target_table = None
            target_col = None
            for tbl in inspector.get_table_names():
                pk_cols = set(inspector.get_pk_constraint(tbl).get("constrained_columns") or [])
                for col in inspector.get_columns(tbl):
                    if col["name"] not in pk_cols:
                        target_table, target_col = tbl, col["name"]
                        break
                if target_table:
                    break
            assert target_table and target_col, "No droppable column found to simulate drift"

            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {target_table} DROP COLUMN {target_col}'))  # noqa: S608

            with engine.connect() as conn:
                missing = _detect_missing_schema(conn)

            assert any(target_col in m and target_table in m for m in missing), (
                f"Drift gate failed to flag dropped column {target_table}.{target_col}; "
                f"got: {missing}"
            )
        finally:
            engine.dispose()
            os.unlink(db_path)

    def test_drift_gate_passes_on_matched_schema(self):
        """The drift detector must report NO missing-from-DB drift on a matched schema.

        Creates the DB straight from ``Base.metadata.create_all`` and asserts the
        gate finds nothing missing — the matched-schema half of the proof.
        """
        from sqlalchemy import create_engine

        import models  # noqa: F401
        from database import Base
        from startup_steps import _detect_missing_schema

        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            Base.metadata.create_all(bind=engine)
            with engine.connect() as conn:
                missing = _detect_missing_schema(conn)
            assert missing == [], f"Expected no missing-from-DB drift, got: {missing}"
        finally:
            engine.dispose()
            os.unlink(db_path)

    def test_v2_053_upgrade_and_downgrade_execute_cleanly(self):
        """DISCOVERY-PORT-001: explicit upgrade/downgrade viability for v2_053.

        This complements the repository's model+inspector migration checks with a
        focused execution test that runs v2_053's ``upgrade()`` and ``downgrade()``
        against SQLite using Alembic Operations.
        """
        import importlib.util

        import sqlalchemy as sa
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.pool import StaticPool

        migration_path = os.path.join(
            backend_path,
            "alembic",
            "versions",
            "v2_053_add_discovery_job_and_discovered_node.py",
        )
        spec = importlib.util.spec_from_file_location("migration_v2_053", migration_path)
        assert spec is not None and spec.loader is not None
        migration_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration_module)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        metadata = sa.MetaData()
        sa.Table("projects", metadata, sa.Column("id", sa.Integer, primary_key=True))
        sa.Table("ssh_credentials", metadata, sa.Column("id", sa.Integer, primary_key=True))
        metadata.create_all(engine)

        with engine.begin() as connection:
            migration_context = MigrationContext.configure(connection)
            migration_module.op = Operations(migration_context)

            migration_module.upgrade()

            inspector = inspect(connection)
            tables_after_upgrade = set(inspector.get_table_names())
            assert "discovery_jobs" in tables_after_upgrade
            assert "discovered_nodes" in tables_after_upgrade

            migration_module.downgrade()

            inspector = inspect(connection)
            tables_after_downgrade = set(inspector.get_table_names())
            assert "discovery_jobs" not in tables_after_downgrade
            assert "discovered_nodes" not in tables_after_downgrade
            assert "projects" in tables_after_downgrade
            assert "ssh_credentials" in tables_after_downgrade

    def test_v2_135_tasks_archived_upgrade_downgrade_roundtrip(self):
        """#21: v2_135 adds tasks.archived; verify the full up→down→up round-trip."""
        import importlib.util

        import sqlalchemy as sa
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine, inspect
        from sqlalchemy.pool import StaticPool

        migration_path = os.path.join(
            backend_path, "alembic", "versions", "v2_135_add_task_archived_column.py"
        )
        spec = importlib.util.spec_from_file_location("migration_v2_135", migration_path)
        assert spec is not None and spec.loader is not None
        migration_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration_module)

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        metadata = sa.MetaData()
        sa.Table("tasks", metadata, sa.Column("id", sa.Integer, primary_key=True))
        metadata.create_all(engine)

        def task_columns(conn):
            return {c["name"] for c in inspect(conn).get_columns("tasks")}

        with engine.begin() as connection:
            migration_context = MigrationContext.configure(connection)
            migration_module.op = Operations(migration_context)

            migration_module.upgrade()
            assert "archived" in task_columns(connection)

            migration_module.downgrade()
            assert "archived" not in task_columns(connection)

            # Re-apply: the upgrade must be idempotently re-runnable (CI round-trip gate).
            migration_module.upgrade()
            assert "archived" in task_columns(connection)

        engine.dispose()

    def test_v2_136_downgrade_blocks_when_template_id_is_null(self):
        """v2_136 downgrade must fail safely if NULL template_id rows exist."""
        import importlib.util

        migration_path = os.path.join(
            backend_path,
            "alembic",
            "versions",
            "v2_136_stack_instance_blueprint_release.py",
        )
        spec = importlib.util.spec_from_file_location("migration_v2_136", migration_path)
        assert spec is not None and spec.loader is not None
        migration_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration_module)

        class _ScalarResult:
            def scalar(self):
                return 1

        class _Bind:
            def execute(self, _stmt):
                return _ScalarResult()

        class _FakeOp:
            def get_bind(self):
                return _Bind()

            def __getattr__(self, name):
                raise AssertionError(f"downgrade should fail before calling op.{name}()")

        migration_module.op = _FakeOp()

        with pytest.raises(RuntimeError, match="NULL template_id"):
            migration_module.downgrade()

    def test_drift_gate_ignores_out_of_band_tables(self):
        """The drift gate must NOT flag tables that exist in the DB but NOT in ORM metadata.

        Regression test for bonnyr-f5 review comment: Alembic's compare_metadata
        can surface out-of-band DB tables (extension schemas, legacy tables, or
        tables created by Postgres extensions) as false-positive ``remove_table``
        diffs.  The ``include_object`` filter in ``_detect_missing_schema`` restricts
        the scan to ORM-declared tables only, so extra DB tables never produce false
        positive startup failures.  This test creates an extra table in the DB that
        the ORM doesn't know about and asserts the gate still reports no drift.
        """
        import os
        import tempfile

        from sqlalchemy import Column, Integer, String, create_engine

        import models  # noqa: F401
        from database import Base
        from startup_steps import _detect_missing_schema

        db_fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(db_fd)
        engine = create_engine(f"sqlite:///{db_path}")
        try:
            # Create the full ORM schema
            Base.metadata.create_all(bind=engine)
            # Also create an out-of-band table (not in ORM — simulates an extension table)
            from sqlalchemy import MetaData, Table
            extra_meta = MetaData()
            Table(
                "pg_extension_shadow",
                extra_meta,
                Column("id", Integer, primary_key=True),
                Column("name", String),
            )
            extra_meta.create_all(bind=engine)

            with engine.connect() as conn:
                missing = _detect_missing_schema(conn)

            # The extra table is NOT in ORM — it must not affect the result
            assert missing == [], (
                f"Out-of-band table triggered false positive: {missing}"
            )
        finally:
            engine.dispose()
            os.unlink(db_path)

    def test_v2_155_backfills_only_the_legacy_mcp_service_row(self):
        """bonnyr-f5 #188 r4 (INV-7): the backfill lives in a NEW revision v2_155,
        NOT appended to the already-shipped v2_154.

        v2_154 (shipped in earlier RCs) only adds the column with server_default
        false — appending a backfill there would never run for an install already
        stamped v2_154 (an applied revision is immutable), i.e. exactly the existing
        installs the backfill must fix. v2_155 chains from v2_154 and does the
        backfill, so any install at v2_154 applies it on the next upgrade.

        This test drives the two revisions in sequence and asserts v2_155 flips
        ONLY the row with the legacy creation fingerprint (username 'mcp' + the
        synthesised email 'mcp@bnk-forge.local'), never a human — including a human
        named 'admin', a normal human, or a human who merely happens to be named
        'mcp' with a real email. It also proves the exact BLOCKER-2 scenario: a DB
        already at v2_154 with the mcp row still False gets it backfilled by v2_155.
        """
        import importlib.util

        import sqlalchemy as sa
        from alembic.operations import Operations
        from alembic.runtime.migration import MigrationContext
        from sqlalchemy import create_engine, inspect, text
        from sqlalchemy.pool import StaticPool

        def _load(basename, modname):
            path = os.path.join(backend_path, "alembic", "versions", basename)
            spec = importlib.util.spec_from_file_location(modname, path)
            assert spec is not None and spec.loader is not None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        v2_154 = _load("v2_154_user_is_service_account.py", "migration_v2_154")
        v2_155 = _load("v2_155_backfill_is_service_account.py", "migration_v2_155")

        # v2_155 must chain directly from v2_154 (single linear head).
        assert v2_155.down_revision == "v2_154"
        assert v2_154.down_revision == "v2_153"

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        # users table in the pre-v2_154 shape (no is_service_account column).
        metadata = sa.MetaData()
        sa.Table(
            "users", metadata,
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("username", sa.String(255), unique=True, nullable=False),
            sa.Column("email", sa.String(255), unique=True, nullable=False),
            sa.Column("hashed_password", sa.String(255), nullable=False),
            sa.Column("role", sa.String(50), nullable=False, server_default="operator"),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.false()),
        )
        metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO users (username,email,hashed_password,role,is_active,must_change_password) VALUES "
                "('mcp','mcp@bnk-forge.local','x','admin',1,0),"        # legacy service acct
                "('admin','admin@corp.com','y','admin',1,0),"          # human admin
                "('alice','alice@corp.com','z','operator',1,0),"        # ordinary human
                "('mcp2','mcp@real-human.com','w','operator',1,0)"      # human named 'mcp' w/ real email
            ))

        def flags(conn):
            return {
                r._mapping["username"]: r._mapping["is_service_account"]
                for r in conn.execute(text("SELECT username, is_service_account FROM users"))
            }

        with engine.begin() as connection:
            migration_context = MigrationContext.configure(connection)
            ops = Operations(migration_context)

            # v2_154 adds the column only — every pre-existing row is False. This is
            # the exact state of an install stamped v2_154 at the earlier commit.
            v2_154.op = ops
            v2_154.upgrade()
            f0 = flags(connection)
            assert all(v in (0, False) for v in f0.values()), (
                "v2_154 must NOT backfill — that would resurrect the immutable-migration hole"
            )

            # v2_155 backfills exactly the legacy mcp row (the BLOCKER-2 fix path).
            v2_155.op = ops
            v2_155.upgrade()
            f = flags(connection)
            assert f["mcp"] in (1, True), "legacy mcp service row must be backfilled True by v2_155"
            assert f["admin"] in (0, False), "human admin must NOT be reclassified"
            assert f["alice"] in (0, False), "ordinary human must NOT be reclassified"
            assert f["mcp2"] in (0, False), "human named 'mcp' with a real email must NOT be reclassified"

            # Round-trip: v2_155 down clears only the flag; v2_154 down drops the column.
            v2_155.downgrade()
            assert flags(connection)["mcp"] in (0, False), "v2_155 downgrade must clear the flag"
            v2_154.downgrade()
            cols = {c["name"] for c in inspect(connection).get_columns("users")}
            assert "is_service_account" not in cols

            # Idempotent re-apply (CI round-trip gate).
            v2_154.upgrade()
            v2_155.upgrade()
            assert flags(connection)["mcp"] in (1, True)

        engine.dispose()
