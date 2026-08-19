"""Component tests for services.module_lock.

Covers the Kleppmann fence-token correctness scenario: a paused worker that
resumes after the lock has been reclaimed must NOT be able to commit writes
to the module's state — even though it still believes it holds the lock.

The lock SQL uses Python-bound timestamps (not Postgres-specific now() /
make_interval), so these tests run unmodified against the SQLite test DB.
"""

import time
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from services.module_lock import (
    ModuleLockError,
    ModuleLockLostError,
    ModuleLockService,
    module_lock,
    set_locked_module_fields,
)


@pytest.fixture()
def module(db, make_project, make_module_library, make_project_module):
    project = make_project(name="lock-test-project")
    lib = make_module_library(name="vpc", path="infra/vpc")
    return make_project_module(project=project, library_module=lib, path_in_project="infra/vpc")


def _force_heartbeat_age(db, module_id: int, seconds: int) -> None:
    """Make the row's heartbeat_at appear `seconds` old."""
    aged = datetime.now(UTC) - timedelta(seconds=seconds)
    db.execute(
        sa.text("UPDATE project_modules SET heartbeat_at = :h WHERE id = :id"),
        {"h": aged, "id": module_id},
    )
    db.commit()


# ──────────────────────────────────────────────────────────────────────────
# Basic acquire / release
# ──────────────────────────────────────────────────────────────────────────


class TestAcquireRelease:
    def test_acquire_on_unheld_module_succeeds(self, db, module):
        svc = ModuleLockService(db)
        lock = svc.acquire(module.id, task_id=1)
        assert lock is not None
        assert lock.module_id == module.id
        assert lock.task_id == 1
        assert lock.fence_token == 1

    def test_acquire_increments_fence_token(self, db, module):
        svc = ModuleLockService(db)
        first = svc.acquire(module.id, task_id=1)
        svc.release(first)
        second = svc.acquire(module.id, task_id=2)
        assert second.fence_token == first.fence_token + 1

    def test_release_clears_holder(self, db, module):
        svc = ModuleLockService(db)
        lock = svc.acquire(module.id, task_id=1)
        assert svc.is_held(module.id) is True
        svc.release(lock)
        assert svc.is_held(module.id) is False

    def test_acquire_on_held_module_returns_none(self, db, module):
        svc = ModuleLockService(db)
        first = svc.acquire(module.id, task_id=1)
        assert first is not None
        second = svc.acquire(module.id, task_id=2)
        assert second is None


# ──────────────────────────────────────────────────────────────────────────
# Stale-lock reclaim
# ──────────────────────────────────────────────────────────────────────────


class TestReclaim:
    def test_stale_lock_can_be_reclaimed(self, db, module):
        svc = ModuleLockService(db)
        first = svc.acquire(module.id, task_id=1)
        assert first is not None
        # Worker A pauses past the reclaim window
        _force_heartbeat_age(db, module.id, seconds=60)
        # Worker B reclaims successfully and gets a NEW (higher) fence token
        second = svc.acquire(module.id, task_id=2)
        assert second is not None
        assert second.fence_token > first.fence_token

    def test_is_held_treats_stale_as_unheld(self, db, module):
        svc = ModuleLockService(db)
        svc.acquire(module.id, task_id=1)
        _force_heartbeat_age(db, module.id, seconds=60)
        # heartbeat is older than the reclaim window — for gate purposes, it's free
        assert svc.is_held(module.id) is False


# ──────────────────────────────────────────────────────────────────────────
# Refresh keeps the lock alive
# ──────────────────────────────────────────────────────────────────────────


class TestRefresh:
    def test_refresh_returns_true_for_held_lock(self, db, module):
        svc = ModuleLockService(db)
        lock = svc.acquire(module.id, task_id=1)
        assert svc.refresh(lock) is True

    def test_refresh_returns_false_after_reclaim(self, db, module):
        svc = ModuleLockService(db)
        lock_a = svc.acquire(module.id, task_id=1)
        _force_heartbeat_age(db, module.id, seconds=60)
        svc.acquire(module.id, task_id=2)  # B reclaims
        assert svc.refresh(lock_a) is False  # A's heartbeat is rejected


# ──────────────────────────────────────────────────────────────────────────
# Kleppmann fencing — the correctness invariant the whole design exists for
# ──────────────────────────────────────────────────────────────────────────


class TestFencing:
    def test_paused_worker_resumed_write_is_rejected(
        self, db, module
    ):
        """Worker A acquires → pauses → Worker B reclaims → Worker A's
        fence-protected UPDATE is rejected. Without fencing this would
        silently corrupt module state owned by the newer holder.

        Uses non-status fields (deployment_error) so the test exercises
        ONLY the lock-fence mechanism, not the Phase 2 state-machine
        validator that auto-routes status writes (covered separately in
        tests/component/test_module_state.py)."""
        svc = ModuleLockService(db)

        lock_a = svc.acquire(module.id, task_id=1)
        assert lock_a is not None

        # A pauses (GC, network wait, OS scheduler glitch). Heartbeat goes stale.
        _force_heartbeat_age(db, module.id, seconds=60)

        # B reclaims and bumps the fence
        lock_b = svc.acquire(module.id, task_id=2)
        assert lock_b is not None
        assert lock_b.fence_token > lock_a.fence_token

        # B writes its state (non-status field — pure lock-fence test)
        set_locked_module_fields(db, module, lock_b, deployment_error="B was here")

        # A resumes and tries to commit its own state — must be rejected
        with pytest.raises(ModuleLockLostError):
            set_locked_module_fields(db, module, lock_a, deployment_error="A overwrote B")

        # The module's deployment_error is what B wrote, not A's stale write
        db.expire(module)
        assert module.deployment_error == "B was here"

    def test_failure_transition_records_the_cause_as_reason(self, db, module, make_task):
        """#101: every engine writes the cause into deployment_error in the same
        call as the *_failed status, but the hand-off to the state machine
        never forwarded it -- so module_state_transitions recorded THAT a
        module failed and never WHY. The reason is now derived from
        deployment_error at the one shared hand-off, so no call site has to
        remember."""
        from models import ModuleStateTransition

        svc = ModuleLockService(db)
        module.status = "initializing"
        db.commit()
        task = make_task(project=module.project, module=module)
        lock = svc.acquire(module.id, task_id=task.id)

        set_locked_module_fields(
            db, module, lock,
            status="init_failed",
            deployment_error="step 'init-poc' failed (exit 1): refusing to overwrite /state/poc",
        )
        db.expire(module)

        row = (
            db.query(ModuleStateTransition)
            .filter(ModuleStateTransition.module_id == module.id)
            .order_by(ModuleStateTransition.id.desc())
            .first()
        )
        assert row is not None
        assert row.to_status == "init_failed"
        assert row.reason, "failure transition was audited with an empty reason"
        assert "init-poc" in row.reason

    def test_explicit_reason_wins_over_deployment_error(self, db, module, make_task):
        from models import ModuleStateTransition

        svc = ModuleLockService(db)
        module.status = "applying"
        db.commit()
        task = make_task(project=module.project, module=module)
        lock = svc.acquire(module.id, task_id=task.id)

        set_locked_module_fields(
            db, module, lock,
            status="apply_failed",
            deployment_error="the long log tail",
            reason="quota exceeded",
        )
        row = (
            db.query(ModuleStateTransition)
            .filter(ModuleStateTransition.module_id == module.id)
            .order_by(ModuleStateTransition.id.desc())
            .first()
        )
        assert row.reason == "quota exceeded"

    def test_reason_is_clamped_to_the_column_width(self, db, module, make_task):
        """deployment_error can be a 2000-char log tail; reason is String(500)."""
        from models import ModuleStateTransition

        svc = ModuleLockService(db)
        module.status = "applying"
        db.commit()
        task = make_task(project=module.project, module=module)
        lock = svc.acquire(module.id, task_id=task.id)

        set_locked_module_fields(
            db, module, lock, status="apply_failed", deployment_error="x" * 2000,
        )
        row = (
            db.query(ModuleStateTransition)
            .filter(ModuleStateTransition.module_id == module.id)
            .order_by(ModuleStateTransition.id.desc())
            .first()
        )
        assert len(row.reason) == 500

    def test_set_locked_fields_succeeds_with_correct_fence(
        self, db, module
    ):
        """Tests the fence-protected raw UPDATE itself. Uses non-status
        fields to bypass the state-machine validator (status changes are
        covered by tests/component/test_module_state.py)."""
        svc = ModuleLockService(db)
        lock = svc.acquire(module.id, task_id=1)
        set_locked_module_fields(db, module, lock, deployment_error="set", workspace_path="/tmp/ws")
        db.expire(module)
        assert module.deployment_error == "set"
        assert module.workspace_path == "/tmp/ws"

    def test_set_locked_fields_rejects_disallowed_columns(
        self, db, module
    ):
        svc = ModuleLockService(db)
        lock = svc.acquire(module.id, task_id=1)
        with pytest.raises(ValueError, match="disallowed"):
            set_locked_module_fields(db, module, lock, project_id=999)


# ──────────────────────────────────────────────────────────────────────────
# Force-release (admin escape hatch)
# ──────────────────────────────────────────────────────────────────────────


class TestForceRelease:
    def test_force_release_clears_holder(self, db, module):
        svc = ModuleLockService(db)
        svc.acquire(module.id, task_id=1)
        assert svc.is_held(module.id) is True
        cleared = svc.force_release(module.id)
        assert cleared is True
        assert svc.is_held(module.id) is False

    def test_force_release_no_op_when_unheld(self, db, module):
        svc = ModuleLockService(db)
        cleared = svc.force_release(module.id)
        assert cleared is False

    def test_force_release_does_not_reset_fence_token(
        self, db, module
    ):
        svc = ModuleLockService(db)
        first = svc.acquire(module.id, task_id=1)
        svc.force_release(module.id)
        second = svc.acquire(module.id, task_id=2)
        assert second.fence_token > first.fence_token


# ──────────────────────────────────────────────────────────────────────────
# Context manager
# ──────────────────────────────────────────────────────────────────────────


class TestContextManager:
    def test_module_lock_acquires_and_releases(self, db, module):
        with module_lock(db, module.id, task_id=1) as lock:
            assert lock.fence_token >= 1
            assert ModuleLockService(db).is_held(module.id) is True
        assert ModuleLockService(db).is_held(module.id) is False

    def test_module_lock_raises_when_already_held(self, db, module):
        ModuleLockService(db).acquire(module.id, task_id=1)
        with pytest.raises(ModuleLockError):
            with module_lock(db, module.id, task_id=2, wait_timeout=0):
                pass

    def test_module_lock_propagates_lost_lock_error(self, db, module):
        """If a fence-protected write fails inside the with block, the error
        propagates and the context manager still cleans up gracefully.
        Uses a non-status field to bypass the state-machine validator."""
        with pytest.raises(ModuleLockLostError):
            with module_lock(db, module.id, task_id=1) as lock:
                # Simulate someone else reclaiming the lock mid-flight
                _force_heartbeat_age(db, module.id, seconds=60)
                ModuleLockService(db).acquire(module.id, task_id=2)
                # This write should be rejected — fence is stale
                set_locked_module_fields(db, module, lock, deployment_error="A's stale write")


# ──────────────────────────────────────────────────────────────────────────
# acquire(wait_timeout)
# ──────────────────────────────────────────────────────────────────────────


class TestAcquireWaitTimeout:
    def test_acquire_with_zero_timeout_fails_fast(self, db, module):
        svc = ModuleLockService(db)
        svc.acquire(module.id, task_id=1)
        start = time.monotonic()
        result = svc.acquire(module.id, task_id=2, wait_timeout=0)
        assert result is None
        assert time.monotonic() - start < 1.0

    def test_acquire_with_short_timeout_polls_then_gives_up(
        self, db, module
    ):
        svc = ModuleLockService(db)
        svc.acquire(module.id, task_id=1)
        start = time.monotonic()
        result = svc.acquire(module.id, task_id=2, wait_timeout=1.5)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed >= 1.0  # waited for at least one poll cycle


# ──────────────────────────────────────────────────────────────────────────
# Holder query / list
# ──────────────────────────────────────────────────────────────────────────


class TestGetHolderListHeld:
    def test_get_holder_returns_none_when_unheld(self, db, module):
        svc = ModuleLockService(db)
        assert svc.get_holder(module.id) is None

    def test_get_holder_returns_dict_when_held(self, db, module):
        svc = ModuleLockService(db)
        svc.acquire(module.id, task_id=42)
        holder = svc.get_holder(module.id)
        assert holder is not None
        assert holder["module_id"] == module.id
        assert holder["holding_task_id"] == 42
        assert holder["fence_token"] == 1

    def test_list_held_includes_held_modules(self, db, module):
        svc = ModuleLockService(db)
        assert svc.list_held() == []
        svc.acquire(module.id, task_id=1)
        held = svc.list_held()
        assert len(held) == 1
        assert held[0]["module_id"] == module.id

    def test_list_held_excludes_released_modules(self, db, module):
        svc = ModuleLockService(db)
        lock = svc.acquire(module.id, task_id=1)
        svc.release(lock)
        assert svc.list_held() == []


# HeartbeatRefresher coverage: the refresher uses module-level SessionLocal
# (so each thread gets its own DB session — sessions are not thread-safe), but
# the test conftest binds a separate engine for transactional isolation. The
# refresher therefore cannot see the test schema. End-to-end behavior is
# covered by the live-Postgres smoke test in the PR description.
