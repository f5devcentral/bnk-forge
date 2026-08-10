"""Postgres heartbeat-based entity lock with fencing tokens.

Generalized from ModuleLockService (Phase 1) to support any whitelisted
entity table. Lock semantics (heartbeat interval, reclaim window, fence
contract) are identical across all entities — divergence is expressed
as constructor parameters, never as code forks.

See memory: rfc_robust_module_locking.md, handoff_phase1_6_proxy_upgrade_locking.md.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.sql import quoted_name

from database import SessionLocal

logger = logging.getLogger(__name__)


RECLAIM_AFTER_SECONDS = 30
HEARTBEAT_INTERVAL_SECONDS = 5
ACQUIRE_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_CONTEXT_WAIT_TIMEOUT = 60.0

# Single source of truth for allowed table names.
# Import this in tests to drive parameterized regression.
_ALLOWED_LOCK_TABLES: frozenset[str] = frozenset(
    {
        "project_modules",
        "proxy_deployments",
        "bnk_upgrades",
    }
)

# Per-table column whitelists for set_locked_entity_fields.
# Cross-checked against actual writes in proxy_deploy_service.py and
# bnk_upgrade_execution_service.py.
_ALLOWED_FIELDS_BY_TABLE: dict[str, frozenset[str]] = {
    "project_modules": frozenset(
        {
            "status",
            "outputs",
            "deployment_error",
            "stage_detail",
            "last_deployed_at",
            "workspace_path",
            "last_init_at",
            "plan_output",
            "plan_serial",
            "vars_hash",
        }
    ),
    "proxy_deployments": frozenset(
        {
            "status",
            "status_message",
            "proxy_url",
            "external_url",
            "helm_release",
            "helm_values",
            "deployed_at",
        }
    ),
    "bnk_upgrades": frozenset(
        {
            "status",
            "started_at",
            "completed_at",
            "current_step",
            "step_results",
            "error_message",
            "error_step",
            "duration_seconds",
            "pre_health",
            "post_health",
            "rollback_info",
            "rollback_available",
            "rollback_reason",
            "health_gate_passed",
        }
    ),
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class EntityLockError(Exception):
    """Raised when an entity lock cannot be acquired."""


class EntityLockLostError(Exception):
    """Raised when an under-lock write is rejected because the fence token
    no longer matches — i.e. another worker reclaimed the lock while this
    worker was paused (GC, network wait, etc.)."""


# Backward-compat aliases used by Phase 1/1.5 code (module_lock imports these).
ModuleLockError = EntityLockError
ModuleLockLostError = EntityLockLostError


# ---------------------------------------------------------------------------
# Lock dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityLock:
    entity_id: int
    task_id: int
    fence_token: int
    table: str

    @property
    def module_id(self) -> int:
        """Backward-compat alias for entity_id (used by Phase 1/1.5 test suite)."""
        return self.entity_id


def ModuleLock(
    module_id: int | None = None,
    task_id: int = 0,
    fence_token: int = 0,
    *,
    entity_id: int | None = None,
    table: str = "project_modules",
) -> EntityLock:
    """Backward-compat constructor alias for EntityLock with project_modules defaults.

    Accepts old-style `module_id=` keyword (Phase 1/1.5 tests) and new-style
    `entity_id=` keyword. One of module_id or entity_id must be provided.

    DEPRECATED: Use EntityLock(entity_id=..., task_id=..., fence_token=...,
    table='project_modules') for new code.
    """
    if entity_id is None and module_id is None:
        raise ValueError("ModuleLock requires module_id or entity_id")
    eid = entity_id if entity_id is not None else module_id
    return EntityLock(entity_id=eid, task_id=task_id, fence_token=fence_token, table=table)


# ---------------------------------------------------------------------------
# EntityLockService
# ---------------------------------------------------------------------------


class EntityLockService:
    """Postgres heartbeat lock with fencing tokens for whitelisted entity rows.

    Constructor validates ``table`` against ``_ALLOWED_LOCK_TABLES`` and
    compiles all SQL statements once (using ``sqlalchemy.sql.quoted_name``
    as belt-and-braces identifier protection). Method bodies contain no
    f-string interpolation of ``self.table``.
    """

    def __init__(
        self,
        db: Session,
        table: str,
        *,
        reclaim_after_seconds: int = RECLAIM_AFTER_SECONDS,
        heartbeat_interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        if table not in _ALLOWED_LOCK_TABLES:
            raise ValueError(
                f"EntityLockService: table {table!r} is not in _ALLOWED_LOCK_TABLES. "
                f"Allowed: {sorted(_ALLOWED_LOCK_TABLES)}"
            )
        self.db = db
        self.table = table
        self.reclaim_after_seconds = reclaim_after_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

        # Compile SQL once at construction time. quoted_name wraps the
        # table identifier so future whitelist bypasses cannot inject SQL.
        qname = quoted_name(table, quote=True)

        self._sql_acquire = sa.text(
            f"""
            UPDATE {qname}
            SET holding_task_id   = :task_id,
                heartbeat_at      = :now,
                lock_fence_token  = lock_fence_token + 1,
                lock_acquired_at  = :now
            WHERE id = :entity_id
              AND (
                holding_task_id IS NULL
                OR heartbeat_at < :cutoff
              )
            RETURNING lock_fence_token
            """
        )

        self._sql_refresh = sa.text(
            f"""
            UPDATE {qname}
            SET heartbeat_at = :now
            WHERE id = :entity_id
              AND holding_task_id = :task_id
              AND lock_fence_token = :fence
            """
        )

        self._sql_release = sa.text(
            f"""
            UPDATE {qname}
            SET holding_task_id = NULL,
                heartbeat_at    = NULL
            WHERE id = :entity_id
              AND holding_task_id = :task_id
              AND lock_fence_token = :fence
            """
        )

        self._sql_is_held = sa.text(
            f"SELECT holding_task_id, heartbeat_at FROM {qname} WHERE id = :entity_id"
        ).columns(
            holding_task_id=sa.Integer,
            heartbeat_at=sa.DateTime(timezone=True),
        )

        self._sql_force_release = sa.text(
            f"""
            UPDATE {qname}
            SET holding_task_id = NULL,
                heartbeat_at    = NULL
            WHERE id = :entity_id
              AND holding_task_id IS NOT NULL
            """
        )

        self._sql_get_holder = sa.text(
            f"""
            SELECT holding_task_id, heartbeat_at, lock_fence_token
            FROM {qname}
            WHERE id = :entity_id
            """
        )

        self._sql_list_held = sa.text(
            f"""
            SELECT id AS entity_id, holding_task_id, heartbeat_at, lock_fence_token
            FROM {qname}
            WHERE holding_task_id IS NOT NULL
            """
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(
        self,
        entity_id: int,
        task_id: int,
        *,
        wait_timeout: float = 0,
    ) -> EntityLock | None:
        """Try to acquire the lock. Returns EntityLock on success, None on contention.

        wait_timeout > 0 polls every ACQUIRE_POLL_INTERVAL_SECONDS up to the
        deadline before giving up.
        """
        deadline = time.monotonic() + wait_timeout if wait_timeout > 0 else None
        while True:
            lock = self._try_acquire(entity_id, task_id)
            if lock is not None:
                return lock
            if deadline is None or time.monotonic() >= deadline:
                return None
            time.sleep(ACQUIRE_POLL_INTERVAL_SECONDS)

    def _try_acquire(self, entity_id: int, task_id: int) -> EntityLock | None:
        # Compute timestamps Python-side for SQLite portability. Both
        # timestamps come from the same Python clock so the comparison
        # is internally consistent.
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self.reclaim_after_seconds)
        result = self.db.execute(
            self._sql_acquire,
            {
                "task_id": task_id,
                "entity_id": entity_id,
                "now": now,
                "cutoff": cutoff,
            },
        )
        row = result.fetchone()
        self.db.commit()
        if row is None:
            return None
        logger.info(
            "Acquired entity lock table=%s entity_id=%s task_id=%s fence=%s",
            self.table,
            entity_id,
            task_id,
            row.lock_fence_token,
        )
        return EntityLock(
            entity_id=entity_id,
            task_id=task_id,
            fence_token=row.lock_fence_token,
            table=self.table,
        )

    def refresh(self, lock: EntityLock) -> bool:
        """Update heartbeat. Returns False if this fence is no longer the holder."""
        result = self.db.execute(
            self._sql_refresh,
            {
                "now": datetime.now(UTC),
                "entity_id": lock.entity_id,
                "task_id": lock.task_id,
                "fence": lock.fence_token,
            },
        )
        self.db.commit()
        return result.rowcount == 1

    def release(self, lock: EntityLock) -> None:
        """Clear holding_task_id + heartbeat_at. No-op if not the holder."""
        self.db.execute(
            self._sql_release,
            {
                "entity_id": lock.entity_id,
                "task_id": lock.task_id,
                "fence": lock.fence_token,
            },
        )
        self.db.commit()
        logger.info(
            "Released entity lock table=%s entity_id=%s task_id=%s fence=%s",
            self.table,
            lock.entity_id,
            lock.task_id,
            lock.fence_token,
        )

    def is_held(self, entity_id: int) -> bool:
        """Return True if a live holder owns the lock (heartbeat within window)."""
        row = self.db.execute(self._sql_is_held, {"entity_id": entity_id}).fetchone()
        if row is None or row.holding_task_id is None or row.heartbeat_at is None:
            return False
        heartbeat = row.heartbeat_at
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        cutoff = datetime.now(UTC) - timedelta(seconds=self.reclaim_after_seconds)
        return heartbeat >= cutoff

    def force_release(self, entity_id: int) -> bool:
        """Admin escape hatch. Returns True if a lock was cleared."""
        result = self.db.execute(self._sql_force_release, {"entity_id": entity_id})
        self.db.commit()
        cleared = result.rowcount == 1
        if cleared:
            logger.warning(
                "Force-released entity lock table=%s entity_id=%s",
                self.table,
                entity_id,
            )
        return cleared

    def get_holder(self, entity_id: int) -> dict | None:
        """Return holder info for admin/debug surfaces. None if no holder."""
        row = self.db.execute(self._sql_get_holder, {"entity_id": entity_id}).fetchone()
        if row is None or row.holding_task_id is None:
            return None
        return {
            "entity_id": entity_id,
            "table": self.table,
            "holding_task_id": row.holding_task_id,
            "heartbeat_at": row.heartbeat_at,
            "fence_token": row.lock_fence_token,
        }

    def list_held(self) -> list[dict]:
        """List all currently-held locks."""
        rows = self.db.execute(self._sql_list_held).fetchall()
        return [
            {
                "entity_id": r.entity_id,
                "table": self.table,
                "holding_task_id": r.holding_task_id,
                "heartbeat_at": r.heartbeat_at,
                "fence_token": r.lock_fence_token,
            }
            for r in rows
        ]


# ---------------------------------------------------------------------------
# HeartbeatRefresher — daemon thread per held lock
# ---------------------------------------------------------------------------


class HeartbeatRefresher:
    """Daemon thread that refreshes an EntityLock's heartbeat_at every
    heartbeat_interval_seconds. Uses its own Session — SQLAlchemy sessions
    are not thread-safe, so the parent task's session is never shared.
    """

    def __init__(
        self,
        lock: EntityLock,
        interval: float = HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self.lock = lock
        self.interval = interval
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"entity-lock-heartbeat-{lock.table}-{lock.entity_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def _run(self) -> None:
        db = SessionLocal()
        try:
            svc = EntityLockService(db, self.lock.table)
            while not self._stop.wait(self.interval):
                try:
                    if not svc.refresh(self.lock):
                        logger.warning(
                            "Entity lock heartbeat lost table=%s entity_id=%s task_id=%s fence=%s",
                            self.lock.table,
                            self.lock.entity_id,
                            self.lock.task_id,
                            self.lock.fence_token,
                        )
                        self._lost.set()
                        return
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "Entity lock heartbeat error table=%s entity_id=%s: %s",
                        self.lock.table,
                        self.lock.entity_id,
                        e,
                    )
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@contextmanager
def entity_lock(
    db: Session,
    *,
    table: str,
    entity_id: int,
    task_id: int,
    wait_timeout: float = DEFAULT_CONTEXT_WAIT_TIMEOUT,
):
    """Acquire an entity lock with an automatic heartbeat refresher.

    Raises EntityLockError if the lock cannot be acquired within wait_timeout.
    On exit, stops the refresher and releases the lock.
    """
    svc = EntityLockService(db, table)
    lock = svc.acquire(entity_id, task_id, wait_timeout=wait_timeout)
    if lock is None:
        raise EntityLockError(
            f"Could not acquire lock on {table}[{entity_id}] within {wait_timeout}s"
        )
    refresher = HeartbeatRefresher(lock)
    refresher.start()
    try:
        yield lock
    finally:
        refresher.stop()
        if not refresher.lost:
            try:
                svc.release(lock)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "Entity lock release error table=%s entity_id=%s: %s",
                    lock.table,
                    lock.entity_id,
                    e,
                )


# ---------------------------------------------------------------------------
# Dumb fence-protected field update helper
# ---------------------------------------------------------------------------


def set_locked_entity_fields(
    db: Session,
    entity,
    lock: EntityLock,
    *,
    table: str,
    **fields,
) -> None:
    """Apply field updates to an entity row under fence-token guard.

    Dumb fence-protected UPDATE — NO state-machine awareness. The Phase 2
    state-machine auto-routing (status writes through transition_module_status)
    lives in the set_locked_module_fields alias in module_lock.py only, and
    only for project_modules rows.

    Raises EntityLockLostError if the fence no longer matches.
    Raises ValueError if any field is not in the per-table allow-list.
    """
    if not fields:
        return

    allowed = _ALLOWED_FIELDS_BY_TABLE.get(table)
    if allowed is None:
        raise ValueError(f"set_locked_entity_fields: unknown table {table!r}")

    bad = set(fields) - allowed
    if bad:
        raise ValueError(
            f"set_locked_entity_fields called with disallowed columns for {table}: "
            f"{sorted(bad)}"
        )

    # Callers MUST NOT have pending ORM dirty state on `entity` for the columns
    # they intend to fence-protect. Use db.expire(entity, [...]) first.

    qname = quoted_name(table, quote=True)
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    sql = sa.text(
        f"UPDATE {qname} SET {set_clause} "
        f"WHERE id = :entity_id AND lock_fence_token = :fence"  # noqa: S608
    )

    # JSON columns need an explicit bind type for SQLite portability.
    json_cols = {"outputs", "step_results", "rollback_info", "pre_health", "post_health", "helm_values"}
    for col in json_cols:
        if col in fields:
            sql = sql.bindparams(sa.bindparam(col, type_=sa.JSON))

    entity_id = entity.id
    bind = {**fields, "entity_id": entity_id, "fence": lock.fence_token}
    result = db.execute(sql, bind)
    if result.rowcount == 0:
        raise EntityLockLostError(
            f"Lock on {table}[{entity_id}] was reclaimed mid-operation "
            f"(fence={lock.fence_token}); under-lock write rejected."
        )
    db.commit()
    db.expire(entity, list(fields.keys()))


# ---------------------------------------------------------------------------
# Stale-lock sweeper (called by APScheduler job in startup_steps.py)
# ---------------------------------------------------------------------------


def sweep_stale_entity_locks(db: Session) -> dict:
    """Reclaim stale locks across all allowed tables.

    A lock is stale when heartbeat_at is older than RECLAIM_AFTER_SECONDS
    (same reclaim window used by acquire). This is belt-and-suspenders for
    the auto-reclaim path in acquire — it clears the holding_task_id so
    admin list-held queries don't surface zombie entries.

    Returns counts per table.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=RECLAIM_AFTER_SECONDS)
    counts: dict[str, int] = {}
    for table in _ALLOWED_LOCK_TABLES:
        qname = quoted_name(table, quote=True)
        result = db.execute(
            sa.text(
                f"""
                UPDATE {qname}
                SET holding_task_id = NULL,
                    heartbeat_at    = NULL
                WHERE holding_task_id IS NOT NULL
                  AND heartbeat_at < :cutoff
                """
            ),
            {"cutoff": cutoff},
        )
        counts[table] = result.rowcount
    db.commit()
    total = sum(counts.values())
    if total > 0:
        logger.info("Stale entity lock sweep cleared %d rows: %s", total, counts)
    return counts
