"""v2_152 must not drop the UNIQUE index on container_registries.name.

`ix_container_registries_name` means opposite things on the two provisioning
paths, and the revision has to tell them apart:

  * chain-built  — the redundant PLAIN index from v2_138. Uniqueness lives on
    the separate `container_registries_name_key` constraint. Safe to drop.
  * create_all   — the ONE index the model's `unique=True, index=True` renders
    to, and it is UNIQUE. It is the only thing enforcing uniqueness on `name`.
    Dropping it makes duplicate registry names insertable.

An `if_exists=True` guard cannot distinguish them: the index is present either
way. That is the bug this revision was corrected for, and nothing pinned it —
the only detector was the whole-schema parity job, which is path-filtered and
had been failing to reach its assertion for unrelated reasons.

Requires Postgres: the distinction is between a UNIQUE and a plain index, which
SQLite does not model the same way. Set TEST_POSTGRES_URL to run; skipped
otherwise so the default suite is unaffected.
"""

import importlib.util
import os
from pathlib import Path

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

PG_URL = os.environ.get("TEST_POSTGRES_URL")
_REVISION = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "v2_152_heal_stamped_head_drift.py"
)

_TABLE_DDL = """
CREATE TABLE container_registries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(50),
    registry_host VARCHAR(255),
    username VARCHAR(255),
    token_encrypted TEXT,
    far_service_account_encrypted TEXT,
    credential_template_id INTEGER,
    description TEXT,
    created_by VARCHAR(255),
    last_test_status VARCHAR(50),
    last_test_at TIMESTAMPTZ,
    last_test_message TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
)
"""


# These tests DROP AND RECREATE container_registries, so they must never run
# against a database anyone else owns.
#
# A name-shaped guard was tried first and was not enough: a substring check for
# "_test"/"_ci" admits `bnk_forge_test`, which is THIS REPO'S OWN
# docker-compose.test.yml database, and `bnkforge_orm_ci`, which is the artifact
# the parity gate in the same CI job reads. Dropping a table there destroys
# token_encrypted / far_service_account_encrypted — unrecoverable secrets, not
# regenerable schema — or silently breaks a sibling gate.
#
# So the fixture creates its OWN database per run and drops it afterwards.
# Nothing pre-existing is touched, which makes the whole question moot rather
# than merely constrained.


@pytest.fixture()
def engine():
    if not PG_URL:
        # Skipping locally is fine; skipping in CI is a gate that reports green
        # while asserting nothing, so fail there instead.
        if os.environ.get("CI"):
            pytest.fail(
                "TEST_POSTGRES_URL is unset under CI — these tests would skip "
                "and the gate would pass without asserting anything"
            )
        pytest.skip("TEST_POSTGRES_URL not set; v2_152 index semantics need Postgres")

    import uuid

    url = sa.engine.make_url(PG_URL)
    scratch_name = f"bnkforge_migtest_{uuid.uuid4().hex[:12]}"

    # CREATE/DROP DATABASE cannot run inside a transaction.
    admin = sa.create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{scratch_name}"'))
    except sa.exc.OperationalError as exc:
        pytest.skip(f"cannot create a scratch database on this server: {exc}")

    scratch = sa.create_engine(url.set(database=scratch_name))
    try:
        yield scratch
    finally:
        scratch.dispose()
        with admin.connect() as conn:
            conn.execute(sa.text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"
            ), {"d": scratch_name})
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{scratch_name}"'))
        admin.dispose()


def _provision(engine, *, unique_index: bool) -> None:
    """Build the container_registries table in one of the two real shapes."""
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS container_registries CASCADE"))
        conn.execute(sa.text(_TABLE_DDL))
        if unique_index:
            # create_all: model renders unique=True, index=True to ONE unique index
            conn.execute(sa.text(
                "CREATE UNIQUE INDEX ix_container_registries_name "
                "ON container_registries (name)"))
        else:
            # chain: UniqueConstraint + the redundant plain index from v2_138
            conn.execute(sa.text(
                "ALTER TABLE container_registries "
                "ADD CONSTRAINT container_registries_name_key UNIQUE (name)"))
            conn.execute(sa.text(
                "CREATE INDEX ix_container_registries_name "
                "ON container_registries (name)"))


def _run_upgrade(engine) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    spec = importlib.util.spec_from_file_location("v2_152", _REVISION)
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)

    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            revision.upgrade()
        conn.commit()


def _name_indexes(engine) -> dict[str, bool]:
    """{index_name: is_unique} for indexes on container_registries.name."""
    with engine.connect() as conn:
        rows = conn.execute(sa.text(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE tablename = 'container_registries' AND indexname LIKE '%name%'"
        )).fetchall()
    return {r[0]: "UNIQUE" in r[1] for r in rows}


def _duplicate_names_rejected(engine) -> bool:
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM container_registries"))
        conn.execute(sa.text(
            "INSERT INTO container_registries (name, type, registry_host) "
            "VALUES ('dup', 'harbor', 'h1')"))
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO container_registries (name, type, registry_host) "
                "VALUES ('dup', 'harbor', 'h2')"))
    except sa.exc.IntegrityError:
        return True
    return False


class TestV2152IndexGuard:
    def test_create_all_unique_index_survives(self, engine):
        """The stamped-head install path: the unique index must NOT be dropped.

        v3.1.6 stamps at v2_141, below this revision, so every install upgrading
        from the current floor runs it — this is the ordinary path, not an edge
        case.
        """
        _provision(engine, unique_index=True)
        assert _name_indexes(engine) == {"ix_container_registries_name": True}

        _run_upgrade(engine)

        assert _name_indexes(engine).get("ix_container_registries_name") is True, (
            "v2_152 dropped the UNIQUE index on container_registries.name — this "
            "is the only thing enforcing uniqueness on the create_all path, so "
            "duplicate registry names become insertable"
        )
        assert _duplicate_names_rejected(engine), "uniqueness on name is gone"

    def test_chain_built_plain_index_is_dropped(self, engine):
        """Contrast: the redundant plain index SHOULD still be removed.

        Without this, the test above would also pass if the revision simply
        stopped dropping anything.
        """
        _provision(engine, unique_index=False)
        before = _name_indexes(engine)
        assert before.get("ix_container_registries_name") is False
        assert before.get("container_registries_name_key") is True

        _run_upgrade(engine)

        after = _name_indexes(engine)
        assert "ix_container_registries_name" not in after, (
            "the redundant plain index survived; create_all and the chain would "
            "diverge and the parity gate would fail"
        )
        assert after.get("container_registries_name_key") is True
        assert _duplicate_names_rejected(engine)

    def test_upgrade_is_idempotent_on_both_shapes(self, engine):
        """A re-run must not fail or change the outcome."""
        for unique in (True, False):
            _provision(engine, unique_index=unique)
            _run_upgrade(engine)
            first = _name_indexes(engine)
            _run_upgrade(engine)
            assert _name_indexes(engine) == first
