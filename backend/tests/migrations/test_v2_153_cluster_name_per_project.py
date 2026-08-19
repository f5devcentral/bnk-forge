"""v2_153: kubernetes_clusters.name uniqueness scoped to (project_id, name).

The global unique on `name` let project A's "prod" block project B's "prod"
and leak A's cluster name to B via the 409 (#113). The app-level duplicate
check is scoped in the same change, but fixing only that would turn the 409
into a raw IntegrityError at commit -- so the constraint must move too.

Two harnesses:
  - SQLite, always: exercises the batch_alter_table recreate path, which is
    the one most likely to be wrong (SQLite cannot alter a column's
    unique-ness in place).
  - Postgres, when TEST_POSTGRES_URL is set (CI's migration round-trip job):
    exercises the real drop_constraint / create_unique_constraint path
    against the implicitly-named constraint.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa

_REVISION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "v2_153_cluster_name_unique_per_project.py"
)
PG_URL = os.environ.get("TEST_POSTGRES_URL")


def _load_revision():
    spec = importlib.util.spec_from_file_location("v2_153", _REVISION)
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
    """The table as the ORM created it BEFORE v2_153: name globally unique."""
    # CASCADE only where the dialect has it: kubernetes_clusters is an FK target,
    # so a bare DROP fails on Postgres the moment anything references it. SQLite
    # has no CASCADE keyword here and also no such dependency to clear.
    cascade = " CASCADE" if engine.dialect.name == "postgresql" else ""
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS kubernetes_clusters{cascade}"))
        conn.execute(sa.text(
            "CREATE TABLE kubernetes_clusters ("
            "  id INTEGER PRIMARY KEY,"
            "  name VARCHAR(255) NOT NULL UNIQUE,"
            "  project_id INTEGER,"
            "  context VARCHAR(255)"
            ")"
        ))


def _two_projects_can_share_a_name(engine) -> bool:
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM kubernetes_clusters"))
        conn.execute(sa.text("INSERT INTO kubernetes_clusters (id, name, project_id) VALUES (1, 'prod', 1)"))
        try:
            conn.execute(sa.text("INSERT INTO kubernetes_clusters (id, name, project_id) VALUES (2, 'prod', 2)"))
            return True
        except sa.exc.IntegrityError:
            return False


def _same_project_duplicate_rejected(engine) -> bool:
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM kubernetes_clusters"))
        conn.execute(sa.text("INSERT INTO kubernetes_clusters (id, name, project_id) VALUES (1, 'prod', 1)"))
        try:
            conn.execute(sa.text("INSERT INTO kubernetes_clusters (id, name, project_id) VALUES (2, 'prod', 1)"))
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
        assert _two_projects_can_share_a_name(sqlite_engine) is False

    def test_upgrade_scopes_uniqueness_to_project(self, sqlite_engine):
        _run(sqlite_engine, "upgrade")
        assert _two_projects_can_share_a_name(sqlite_engine) is True, (
            "two projects still cannot share a cluster name -- the global unique survived"
        )
        assert _same_project_duplicate_rejected(sqlite_engine) is True, (
            "the composite (project_id, name) unique is not enforced"
        )

    def test_upgrade_keeps_a_lookup_index_on_name(self, sqlite_engine):
        _run(sqlite_engine, "upgrade")
        insp = sa.inspect(sqlite_engine)
        names = {ix["name"] for ix in insp.get_indexes("kubernetes_clusters")}
        assert "ix_kubernetes_clusters_name" in names

    def test_downgrade_restores_global_unique(self, sqlite_engine):
        _run(sqlite_engine, "upgrade")
        _run(sqlite_engine, "downgrade")
        assert _two_projects_can_share_a_name(sqlite_engine) is False

    def test_upgrade_is_idempotent_on_a_fresh_orm_schema(self, tmp_path):
        """A brand-new install creates the table from the ORM (composite already
        declared, no global unique). The migration must not blow up finding
        nothing to drop."""
        eng = sa.create_engine(f"sqlite:///{tmp_path}/fresh.db")
        with eng.begin() as conn:
            conn.execute(sa.text(
                "CREATE TABLE kubernetes_clusters ("
                "  id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL, project_id INTEGER,"
                "  CONSTRAINT uq_kubernetes_clusters_project_name UNIQUE (project_id, name))"
            ))
        # Alembic's batch recreate re-declares the composite; if that raised on
        # an existing same-named constraint, a fresh install would fail here.
        _run(eng, "upgrade")
        assert _two_projects_can_share_a_name(eng) is True
        eng.dispose()


# ── Postgres (CI migration round-trip) ──────────────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(not PG_URL, reason="TEST_POSTGRES_URL not set")
class TestPostgresPath:
    @pytest.fixture
    def pg_engine(self, pg_scratch_engine):
        """A throwaway database, not the one CI hands us via TEST_POSTGRES_URL.

        That URL points at the full ORM schema built by init_db.py, where other
        tables carry foreign keys to kubernetes_clusters -- so dropping it there
        raises DependentObjectsStillExist, and succeeding would have corrupted
        the schema the rest of the job depends on. See conftest.py.
        """
        _pre_migration_schema(pg_scratch_engine)
        return pg_scratch_engine

    def test_upgrade_drops_the_implicitly_named_global_unique(self, pg_engine):
        """On Postgres the column-level UNIQUE becomes kubernetes_clusters_name_key;
        the migration must find it by inspection, not by a hardcoded name."""
        _run(pg_engine, "upgrade")
        assert _two_projects_can_share_a_name(pg_engine) is True
        assert _same_project_duplicate_rejected(pg_engine) is True
        with pg_engine.connect() as conn:
            cons = conn.execute(sa.text(
                "SELECT conname FROM pg_constraint WHERE conrelid = 'kubernetes_clusters'::regclass"
            )).scalars().all()
        assert "uq_kubernetes_clusters_project_name" in cons
        assert "kubernetes_clusters_name_key" not in cons

    def test_downgrade_refuses_when_two_projects_share_a_name(self, pg_engine):
        """Restoring the global unique over real cross-project duplicates would
        silently reintroduce the leak; it must raise instead."""
        _run(pg_engine, "upgrade")
        with pg_engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO kubernetes_clusters (id, name, project_id) VALUES (1,'prod',1),(2,'prod',2)"))
        with pytest.raises(Exception):
            _run(pg_engine, "downgrade")
