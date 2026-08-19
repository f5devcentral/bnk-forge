"""Shared Postgres isolation for the migration regression tests.

These tests provision tables in specific historical shapes, which means
dropping and recreating them. CI points TEST_POSTGRES_URL at the ORM database
built by init_db.py — the full create_all schema — so dropping a table there
fails the moment another table holds a foreign key to it:

    psycopg2.errors.DependentObjectsStillExist:
    cannot drop table kubernetes_clusters because other objects depend on it

Each test therefore gets its own throwaway database rather than sharing the
one CI hands us. Isolation is the point: a migration test that mutates the
shared schema also corrupts whatever runs after it.
"""
import os
import uuid

import pytest
import sqlalchemy as sa

PG_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.fixture()
def pg_scratch_engine():
    """An engine bound to a fresh, disposable Postgres database.

    Skips when no Postgres is configured — EXCEPT in the job that exists to run
    these tests, which sets BNK_REQUIRE_MIGRATION_TESTS. There, a skip is a gate
    reporting green while asserting nothing, so it fails instead. That applies
    to an unreachable server or a role without CREATEDB too, not just a missing
    URL: every path that would end in "no assertions ran" has to be loud in that
    job.
    """
    if not PG_URL:
        if os.environ.get("BNK_REQUIRE_MIGRATION_TESTS"):
            pytest.fail(
                "TEST_POSTGRES_URL is unset in the job that requires the "
                "migration tests — they would skip and the gate would pass "
                "without asserting anything"
            )
        pytest.skip("TEST_POSTGRES_URL not set; these semantics need Postgres")

    url = sa.engine.make_url(PG_URL)
    scratch_name = f"bnkforge_migtest_{uuid.uuid4().hex[:12]}"

    # CREATE/DROP DATABASE cannot run inside a transaction.
    admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{scratch_name}"'))
    except sa.exc.OperationalError as exc:
        if os.environ.get("BNK_REQUIRE_MIGRATION_TESTS"):
            pytest.fail(
                f"cannot create a scratch database in the job that requires the "
                f"migration tests: {exc}"
            )
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
