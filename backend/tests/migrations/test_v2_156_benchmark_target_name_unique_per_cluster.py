"""v2_156: benchmark_targets.name uniqueness scoped to (cluster_id, name).

The global unique on `name` let cluster A's benchmark target block cluster B's
identically named target (e.g. awsbnkctl discovering "mcp-default-route" on two
different clusters).

Two harnesses:
  - SQLite, always: exercises the batch_alter_table recreate path.
  - Postgres, when TEST_POSTGRES_URL is set (CI's migration round-trip job):
    exercises drop_constraint / create_unique_constraint against the
    implicitly-named constraint or index.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

_REVISION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "v2_156_benchmark_target_name_unique_per_cluster.py"
)


def _load_revision():
    spec = importlib.util.spec_from_file_location("v2_156", _REVISION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(engine, direction: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    rev = _load_revision()
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            getattr(rev, direction)()
        conn.commit()


def _pre_migration_schema(engine) -> None:
    """The table as the ORM created it BEFORE v2_156: name globally unique."""
    cascade = " CASCADE" if engine.dialect.name == "postgresql" else ""
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS benchmark_targets{cascade}"))
        conn.execute(sa.text(
            "CREATE TABLE benchmark_targets ("
            "  id INTEGER PRIMARY KEY,"
            "  name VARCHAR(255) NOT NULL UNIQUE,"
            "  cluster_id INTEGER NOT NULL,"
            "  llm_base_url VARCHAR(512) NOT NULL,"
            "  llm_model VARCHAR(255) NOT NULL,"
            "  llm_namespace VARCHAR(128) NOT NULL,"
            "  llm_endpoint VARCHAR(255) NOT NULL,"
            "  proxy_namespace VARCHAR(128) NOT NULL,"
            "  status VARCHAR(32) NOT NULL"
            ")"
        ))


def _two_clusters_can_share_a_name(engine) -> bool:
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM benchmark_targets"))
        conn.execute(sa.text(
            "INSERT INTO benchmark_targets (id, name, cluster_id, llm_base_url, llm_model, llm_namespace, llm_endpoint, proxy_namespace, status) "
            "VALUES (1, 'mcp-route', 1, 'http://vllm:8000', 'llama', 'default', '/v1', 'perf', 'active')"
        ))
        try:
            conn.execute(sa.text(
                "INSERT INTO benchmark_targets (id, name, cluster_id, llm_base_url, llm_model, llm_namespace, llm_endpoint, proxy_namespace, status) "
                "VALUES (2, 'mcp-route', 2, 'http://vllm:8000', 'llama', 'default', '/v1', 'perf', 'active')"
            ))
            return True
        except sa.exc.IntegrityError:
            return False


def _same_cluster_duplicate_rejected(engine) -> bool:
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM benchmark_targets"))
        conn.execute(sa.text(
            "INSERT INTO benchmark_targets (id, name, cluster_id, llm_base_url, llm_model, llm_namespace, llm_endpoint, proxy_namespace, status) "
            "VALUES (1, 'mcp-route', 1, 'http://vllm:8000', 'llama', 'default', '/v1', 'perf', 'active')"
        ))
        try:
            conn.execute(sa.text(
                "INSERT INTO benchmark_targets (id, name, cluster_id, llm_base_url, llm_model, llm_namespace, llm_endpoint, proxy_namespace, status) "
                "VALUES (2, 'mcp-route', 1, 'http://vllm:8000', 'llama', 'default', '/v1', 'perf', 'active')"
            ))
            return False
        except sa.exc.IntegrityError:
            return True


# ── SQLite (always runs) ────────────────────────────────────────────────────

@pytest.fixture
def sqlite_engine(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path}/mig.db")
    _pre_migration_schema(eng)
    yield eng
    eng.dispose()


@pytest.mark.unit
class TestSqlitePath:
    def test_before_upgrade_names_are_globally_unique(self, sqlite_engine):
        """Precondition: the pre-migration schema really has the bug."""
        assert _two_clusters_can_share_a_name(sqlite_engine) is False

    def test_upgrade_scopes_uniqueness_to_cluster(self, sqlite_engine):
        _run(sqlite_engine, "upgrade")
        assert _two_clusters_can_share_a_name(sqlite_engine) is True, (
            "two clusters still cannot share a target name -- the global unique survived"
        )
        assert _same_cluster_duplicate_rejected(sqlite_engine) is True, (
            "the composite (cluster_id, name) unique is not enforced"
        )

    def test_upgrade_keeps_a_lookup_index_on_name(self, sqlite_engine):
        _run(sqlite_engine, "upgrade")
        insp = sa.inspect(sqlite_engine)
        names = {ix["name"] for ix in insp.get_indexes("benchmark_targets")}
        assert "ix_benchmark_targets_name" in names

    def test_downgrade_restores_global_unique(self, sqlite_engine):
        _run(sqlite_engine, "upgrade")
        _run(sqlite_engine, "downgrade")
        assert _two_clusters_can_share_a_name(sqlite_engine) is False

    def test_upgrade_is_idempotent_on_a_fresh_orm_schema(self, tmp_path):
        """A brand-new install creates the table from the ORM (composite already
        declared, no global unique). The migration must not blow up finding
        nothing to drop."""
        eng = sa.create_engine(f"sqlite:///{tmp_path}/fresh.db")
        with eng.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE benchmark_targets ("
                "  id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, cluster_id INTEGER NOT NULL,"
                "  llm_base_url VARCHAR(512) NOT NULL, llm_model VARCHAR(255) NOT NULL,"
                "  llm_namespace VARCHAR(128) NOT NULL, llm_endpoint VARCHAR(255) NOT NULL,"
                "  proxy_namespace VARCHAR(128) NOT NULL, status VARCHAR(32) NOT NULL,"
                "  CONSTRAINT uq_benchmark_targets_cluster_name UNIQUE (cluster_id, name))"
            ))
        _run(eng, "upgrade")
        assert _two_clusters_can_share_a_name(eng) is True
        eng.dispose()


# ── Postgres (CI migration round-trip) ──────────────────────────────────────

@pytest.mark.integration
class TestPostgresPath:
    @pytest.fixture
    def pg_engine(self, pg_scratch_engine):
        _pre_migration_schema(pg_scratch_engine)
        return pg_scratch_engine

    def test_upgrade_drops_the_implicitly_named_global_unique(self, pg_engine):
        _run(pg_engine, "upgrade")
        assert _two_clusters_can_share_a_name(pg_engine) is True
        assert _same_cluster_duplicate_rejected(pg_engine) is True
        with pg_engine.connect() as conn:
            cons = conn.execute(sa.text(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'benchmark_targets'::regclass"
            )).scalars().all()
        assert "uq_benchmark_targets_cluster_name" in cons
        assert "benchmark_targets_name_key" not in cons

    def test_downgrade_refuses_when_two_clusters_share_a_name(self, pg_engine):
        _run(pg_engine, "upgrade")
        with pg_engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO benchmark_targets (id, name, cluster_id, llm_base_url, llm_model, llm_namespace, llm_endpoint, proxy_namespace, status) "
                "VALUES (1,'mcp-route',1,'http://vllm:8000','llama','default','/v1','perf','active'),"
                "(2,'mcp-route',2,'http://vllm:8000','llama','default','/v1','perf','active')"
            ))
        with pytest.raises(Exception):
            _run(pg_engine, "downgrade")
