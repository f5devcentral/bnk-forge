"""Component tests for services.entity_lock.

Covers:
- Whitelist enforcement (ValueError on unknown table)
- SQL safety (no f-string interpolation, parameterized binds)
- EntityLockService acquire/release/refresh/force_release/is_held over all 3 tables
- set_locked_entity_fields fence-protected UPDATE (correctness + rejection)
- Concurrent-deploy race: two tasks on same proxy_id, one blocks
- Concurrent-upgrade race: two tasks on same upgrade_id, one blocks
- Execute-vs-rollback race: execute holds lock, rollback is rejected until release
- Stale-lock reclaim: held lock reclaimable after heartbeat timeout; old fence rejected
- State-machine alias preservation: set_locked_module_fields routes status through
  transition_module_status unless _from_state_machine sentinel is set
"""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa

from services.entity_lock import (
    _ALLOWED_FIELDS_BY_TABLE,
    _ALLOWED_LOCK_TABLES,
    EntityLock,
    EntityLockError,
    EntityLockLostError,
    EntityLockService,
    entity_lock,
    set_locked_entity_fields,
)
from services.module_lock import set_locked_module_fields

# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def proxy_deploy(db, make_proxy_deployment):
    return make_proxy_deployment()


@pytest.fixture()
def bnk_upgrade(db, make_bnk_upgrade):
    return make_bnk_upgrade()


@pytest.fixture()
def project_module(db, make_project, make_module_library, make_project_module):
    project = make_project(name="entity-lock-test")
    lib = make_module_library(name="vpc", path="infra/vpc")
    return make_project_module(project=project, library_module=lib, path_in_project="infra/vpc")


# Map (table, entity) pairs for parameterized tests
@pytest.fixture(
    params=[
        pytest.param("proxy_deployments", id="proxy_deployments"),
        pytest.param("bnk_upgrades", id="bnk_upgrades"),
        pytest.param("project_modules", id="project_modules"),
    ]
)
def table_and_entity(request, proxy_deploy, bnk_upgrade, project_module):
    """Yield (table, entity) for parameterized lock tests."""
    table = request.param
    entity_map = {
        "proxy_deployments": proxy_deploy,
        "bnk_upgrades": bnk_upgrade,
        "project_modules": project_module,
    }
    return table, entity_map[table]


def _force_heartbeat_age(db, table: str, entity_id: int, seconds: int) -> None:
    """Back-date a lock's heartbeat_at to appear stale."""
    from sqlalchemy.sql import quoted_name
    aged = datetime.now(UTC) - timedelta(seconds=seconds)
    qname = quoted_name(table, quote=True)
    db.execute(
        sa.text(f"UPDATE {qname} SET heartbeat_at = :h WHERE id = :id"),
        {"h": aged, "id": entity_id},
    )
    db.commit()


# ──────────────────────────────────────────────────────────────────────────
# Whitelist enforcement
# ──────────────────────────────────────────────────────────────────────────


class TestWhitelistEnforcement:
    def test_unknown_table_raises_value_error(self, db):
        with pytest.raises(ValueError, match="not in _ALLOWED_LOCK_TABLES"):
            EntityLockService(db, "not_a_real_table")

    def test_allowed_tables_do_not_raise(self, db):
        for table in _ALLOWED_LOCK_TABLES:
            svc = EntityLockService(db, table)
            assert svc.table == table

    def test_all_allowed_tables_constant(self):
        assert "project_modules" in _ALLOWED_LOCK_TABLES
        assert "proxy_deployments" in _ALLOWED_LOCK_TABLES
        assert "bnk_upgrades" in _ALLOWED_LOCK_TABLES


# ──────────────────────────────────────────────────────────────────────────
# SQL safety
# ──────────────────────────────────────────────────────────────────────────


class TestSQLSafety:
    def test_compiled_sql_contains_only_allowed_table_names(self, db):
        """Each compiled SQL string must contain exactly one of the allowed
        table names and must use parameterized binds for IDs/tokens."""
        for table in _ALLOWED_LOCK_TABLES:
            svc = EntityLockService(db, table)
            for attr in ("_sql_acquire", "_sql_refresh", "_sql_release",
                         "_sql_is_held", "_sql_force_release",
                         "_sql_get_holder", "_sql_list_held"):
                sql_text = str(getattr(svc, attr))
                # The table name must appear in the SQL
                assert table in sql_text or f'"{table}"' in sql_text, \
                    f"{attr} for {table} does not reference the correct table"
                # No OTHER allowed table names should appear
                for other_table in _ALLOWED_LOCK_TABLES - {table}:
                    assert other_table not in sql_text, \
                        f"{attr} for {table} unexpectedly references {other_table}"

    def test_acquire_sql_uses_parameterized_binds(self, db):
        """acquire SQL must not concatenate entity_id or task_id as literals."""
        for table in _ALLOWED_LOCK_TABLES:
            svc = EntityLockService(db, table)
            sql_text = str(svc._sql_acquire)
            # SQLAlchemy text() with :name binding
            assert ":entity_id" in sql_text or ":task_id" in sql_text, \
                f"acquire SQL for {table} must use named parameter binds"


class TestSetLockedEntityFieldsSQLSafety:
    """SQL safety tests for set_locked_entity_fields.

    The function builds its UPDATE SQL dynamically per call because the SET
    clause varies with **fields. The whitelist guards column names; these tests
    verify that (a) only whitelisted table names appear in the SQL, and (b)
    field *values* are passed as parameterized binds — not interpolated into
    the SQL text.
    """

    def _capture_sql(self, db, entity, lock, table: str, **fields) -> str:
        """Call set_locked_entity_fields and return the raw SQL string emitted."""
        captured: list[str] = []

        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):  # noqa: PLR0913
            captured.append(statement)

        sa.event.listen(db.bind, "before_cursor_execute", before_cursor_execute)
        try:
            set_locked_entity_fields(db, entity, lock, table=table, **fields)
        finally:
            sa.event.remove(db.bind, "before_cursor_execute", before_cursor_execute)

        # The UPDATE statement is the last one captured (after any savepoint bookkeeping).
        update_statements = [s for s in captured if "UPDATE" in s.upper()]
        assert update_statements, "No UPDATE statement was captured"
        return update_statements[-1]

    def test_sql_contains_only_allowed_table_name(self, db, proxy_deploy):
        """The dynamically-built UPDATE SQL must reference only the target table."""
        svc = EntityLockService(db, "proxy_deployments")
        lock = svc.acquire(proxy_deploy.id, task_id=1)

        sql = self._capture_sql(db, proxy_deploy, lock,
                                 table="proxy_deployments", status_message="test-value")

        assert "proxy_deployments" in sql, "Target table not found in SQL"
        for other_table in _ALLOWED_LOCK_TABLES - {"proxy_deployments"}:
            assert other_table not in sql, \
                f"SQL unexpectedly references other table: {other_table}"

    def test_field_values_are_parameterized_not_literal(self, db, proxy_deploy):
        """Field values must appear as bind parameters (:name), not as literal
        substrings in the SQL text.

        If a value were interpolated (e.g. f"... SET status_message = 'test-value'"),
        it would appear literally in the SQL string. With proper :name binding the
        SQL string contains ':status_message' but NOT the value 'test-sentinel'.
        """
        svc = EntityLockService(db, "proxy_deployments")
        lock = svc.acquire(proxy_deploy.id, task_id=1)

        sentinel_value = "test-sentinel-value-xyzzy"
        sql = self._capture_sql(db, proxy_deploy, lock,
                                 table="proxy_deployments", status_message=sentinel_value)

        # The placeholder must be present — column name as :bind_name
        assert ":status_message" in sql or "?" in sql, \
            "Expected a parameterized bind in the UPDATE SQL"
        # The literal sentinel value must NOT appear in the SQL text
        assert sentinel_value not in sql, \
            f"Field value '{sentinel_value}' was interpolated into SQL (SQL injection risk)"

    def test_all_allowed_tables_use_parameterized_binds(self, db, proxy_deploy, bnk_upgrade, project_module):
        """For each allowed table, at least one write-able field produces
        parameterized SQL (not value-in-SQL-text)."""
        test_cases = [
            ("proxy_deployments", proxy_deploy, {"status_message": "sentinel-proxy"}),
            ("bnk_upgrades", bnk_upgrade, {"error_message": "sentinel-upgrade"}),
            ("project_modules", project_module, {"deployment_error": "sentinel-module"}),
        ]
        for table, entity, fields in test_cases:
            svc = EntityLockService(db, table)
            lock = svc.acquire(entity.id, task_id=42)
            sentinel = list(fields.values())[0]
            sql = self._capture_sql(db, entity, lock, table=table, **fields)
            assert sentinel not in sql, \
                f"Value '{sentinel}' was interpolated into SQL for table {table}"
            # Release for next iteration
            svc.release(lock)


# ──────────────────────────────────────────────────────────────────────────
# Acquire / release / refresh over all three tables
# ──────────────────────────────────────────────────────────────────────────


class TestAcquireRelease:
    def test_acquire_on_unheld_entity_succeeds(self, db, table_and_entity):
        table, entity = table_and_entity
        svc = EntityLockService(db, table)
        lock = svc.acquire(entity.id, task_id=1)
        assert lock is not None
        assert lock.entity_id == entity.id
        assert lock.task_id == 1
        assert lock.fence_token == 1
        assert lock.table == table

    def test_acquire_increments_fence_token(self, db, table_and_entity):
        table, entity = table_and_entity
        svc = EntityLockService(db, table)
        first = svc.acquire(entity.id, task_id=1)
        svc.release(first)
        second = svc.acquire(entity.id, task_id=2)
        assert second.fence_token == first.fence_token + 1

    def test_release_clears_holder(self, db, table_and_entity):
        table, entity = table_and_entity
        svc = EntityLockService(db, table)
        lock = svc.acquire(entity.id, task_id=1)
        assert svc.is_held(entity.id) is True
        svc.release(lock)
        assert svc.is_held(entity.id) is False

    def test_acquire_on_held_entity_returns_none(self, db, table_and_entity):
        table, entity = table_and_entity
        svc = EntityLockService(db, table)
        first = svc.acquire(entity.id, task_id=1)
        assert first is not None
        second = svc.acquire(entity.id, task_id=2)
        assert second is None


# ──────────────────────────────────────────────────────────────────────────
# Stale-lock reclaim
# ──────────────────────────────────────────────────────────────────────────


class TestReclaim:
    def test_stale_lock_can_be_reclaimed(self, db, table_and_entity):
        table, entity = table_and_entity
        svc = EntityLockService(db, table)
        first = svc.acquire(entity.id, task_id=1)
        assert first is not None
        _force_heartbeat_age(db, table, entity.id, seconds=60)
        second = svc.acquire(entity.id, task_id=2)
        assert second is not None
        assert second.fence_token > first.fence_token

    def test_is_held_treats_stale_as_unheld(self, db, table_and_entity):
        table, entity = table_and_entity
        svc = EntityLockService(db, table)
        svc.acquire(entity.id, task_id=1)
        _force_heartbeat_age(db, table, entity.id, seconds=60)
        assert svc.is_held(entity.id) is False

    def test_old_fence_rejected_after_reclaim(self, db, table_and_entity):
        """After reclaim, the original fence token produces EntityLockLostError."""
        table, entity = table_and_entity
        # Use a non-status field to avoid state-machine routing for project_modules.
        field_map = {
            "proxy_deployments": {"status_message": "A was here"},
            "bnk_upgrades": {"error_message": "A was here"},
            "project_modules": {"deployment_error": "A was here"},
        }
        svc = EntityLockService(db, table)
        lock_a = svc.acquire(entity.id, task_id=1)
        _force_heartbeat_age(db, table, entity.id, seconds=60)
        svc.acquire(entity.id, task_id=2)  # B reclaims
        with pytest.raises(EntityLockLostError):
            set_locked_entity_fields(db, entity, lock_a, table=table, **field_map[table])


# ──────────────────────────────────────────────────────────────────────────
# Field-write helper
# ──────────────────────────────────────────────────────────────────────────


class TestSetLockedEntityFields:
    def test_rejects_disallowed_columns(self, db, proxy_deploy):
        svc = EntityLockService(db, "proxy_deployments")
        lock = svc.acquire(proxy_deploy.id, task_id=1)
        with pytest.raises(ValueError, match="disallowed"):
            set_locked_entity_fields(db, proxy_deploy, lock,
                                     table="proxy_deployments", target_id=999)

    def test_write_succeeds_with_allowed_field(self, db, proxy_deploy):
        svc = EntityLockService(db, "proxy_deployments")
        lock = svc.acquire(proxy_deploy.id, task_id=1)
        set_locked_entity_fields(db, proxy_deploy, lock,
                                 table="proxy_deployments", status_message="ok")
        db.expire(proxy_deploy)
        assert proxy_deploy.status_message == "ok"

    def test_unknown_table_raises_value_error(self, db, proxy_deploy):
        svc = EntityLockService(db, "proxy_deployments")
        lock = svc.acquire(proxy_deploy.id, task_id=1)
        with pytest.raises(ValueError):
            set_locked_entity_fields(db, proxy_deploy, lock,
                                     table="bad_table", status_message="x")


# ──────────────────────────────────────────────────────────────────────────
# Concurrent-deploy race
# ──────────────────────────────────────────────────────────────────────────


class TestConcurrentDeployRace:
    def test_second_deploy_blocked_by_lock(self, db, proxy_deploy):
        """Worker A holds the lock; Worker B's acquire returns None immediately."""
        svc = EntityLockService(db, "proxy_deployments")
        lock_a = svc.acquire(proxy_deploy.id, task_id=1)
        assert lock_a is not None
        lock_b = svc.acquire(proxy_deploy.id, task_id=2, wait_timeout=0)
        assert lock_b is None  # B cannot acquire while A holds

    def test_entity_lock_context_raises_on_contention(self, db, proxy_deploy):
        """entity_lock() raises EntityLockError if the lock is already held."""
        EntityLockService(db, "proxy_deployments").acquire(proxy_deploy.id, task_id=1)
        with pytest.raises(EntityLockError):
            with entity_lock(db, table="proxy_deployments",
                             entity_id=proxy_deploy.id, task_id=2, wait_timeout=0):
                pass


# ──────────────────────────────────────────────────────────────────────────
# Concurrent-upgrade race
# ──────────────────────────────────────────────────────────────────────────


class TestConcurrentUpgradeRace:
    def test_second_execute_blocked(self, db, bnk_upgrade):
        svc = EntityLockService(db, "bnk_upgrades")
        lock_a = svc.acquire(bnk_upgrade.id, task_id=1)
        assert lock_a is not None
        lock_b = svc.acquire(bnk_upgrade.id, task_id=2, wait_timeout=0)
        assert lock_b is None


# ──────────────────────────────────────────────────────────────────────────
# Execute-vs-rollback race
# ──────────────────────────────────────────────────────────────────────────


class TestExecuteVsRollbackRace:
    def test_rollback_blocked_while_execute_holds_lock(self, db, bnk_upgrade):
        """Execute holds the lock; rollback task's acquire fails."""
        svc = EntityLockService(db, "bnk_upgrades")
        lock_execute = svc.acquire(bnk_upgrade.id, task_id=100)
        assert lock_execute is not None

        lock_rollback = svc.acquire(bnk_upgrade.id, task_id=200, wait_timeout=0)
        assert lock_rollback is None

        svc.release(lock_execute)
        # After release rollback can acquire
        lock_rollback2 = svc.acquire(bnk_upgrade.id, task_id=200)
        assert lock_rollback2 is not None


# ──────────────────────────────────────────────────────────────────────────
# State-machine alias preservation
# ──────────────────────────────────────────────────────────────────────────


class TestStateMachineAliasPreservation:
    def test_status_write_without_sentinel_routes_through_state_machine(
        self, db, project_module
    ):
        """set_locked_module_fields with status= (no _from_state_machine) must
        call transition_module_status, not the direct fence-protected UPDATE.

        transition_module_status is lazily imported inside set_locked_module_fields
        from services.module_state, so we patch it at its definition site.
        """
        from services.module_lock import (
            ModuleLockService,
            set_locked_module_fields,
        )

        svc = ModuleLockService(db)
        lock = svc.acquire(project_module.id, task_id=1)

        with patch("services.module_state.transition_module_status") as mock_tsm:
            set_locked_module_fields(db, project_module, lock, status="applying")
            mock_tsm.assert_called_once()
            # Verify it was called with to_status="applying"
            assert "applying" in str(mock_tsm.call_args)

    def test_status_write_with_sentinel_bypasses_state_machine(
        self, db, project_module
    ):
        """set_locked_module_fields with status= AND _from_state_machine=True skips
        the state-machine call and goes directly through the fence-protected UPDATE.

        The previous version omitted status= so the state machine was never attempted
        regardless of the sentinel — this version proves the sentinel actually bypasses
        a status write path that would otherwise route through transition_module_status.
        """
        from services.module_lock import ModuleLockService, set_locked_module_fields

        svc = ModuleLockService(db)
        lock = svc.acquire(project_module.id, task_id=1)

        with patch("services.module_state.transition_module_status") as mock_tsm:
            # status= is present AND sentinel is set → direct fence-protected UPDATE,
            # bypassing transition_module_status entirely.
            set_locked_module_fields(
                db, project_module, lock,
                status="applying",
                _from_state_machine=True,
            )
            mock_tsm.assert_not_called()
