"""Component tests for services.module_state.transition_module_status.

DB-backed (SQLite via the test conftest). Verifies:
  - legal transition: status applied + audit row inserted
  - illegal transition: IllegalTransitionError raised, status unchanged,
    no audit row inserted
  - self-loop: no-op for status, audit row STILL inserted (observability)
  - lock-protected vs lock-less paths both work
  - extra_fields atomic with status when lock provided
  - extra_fields ignored (with warning) when lock is None
"""

from __future__ import annotations

import pytest

from models import ModuleStateTransition
from services.module_lock import ModuleLockService
from services.module_state import (
    IllegalTransitionError,
    transition_module_status,
)


@pytest.fixture()
def module(db, make_project, make_module_library, make_project_module):
    project = make_project(name="state-machine-test-project")
    lib = make_module_library(name="vpc", path="infra/vpc")
    return make_project_module(project=project, library_module=lib, path_in_project="infra/vpc")


def _audit_rows(db, module_id):
    return db.query(ModuleStateTransition).filter(
        ModuleStateTransition.module_id == module_id,
    ).order_by(ModuleStateTransition.id).all()


# ---------------------------------------------------------------------------
# Legal transitions
# ---------------------------------------------------------------------------


class TestLegalTransition:
    def test_legal_unlocked_transition_applies_and_logs(self, db, module, make_task):
        # task_id is FK-constrained to tasks(id), so create a real task row.
        # The lock's task_id field on ModuleLock is just an integer label —
        # ModuleLockService doesn't enforce a FK on it — but the audit row's
        # task_id IS a real FK.
        task = make_task(project=module.project, module=module)
        # default status is "not_initialized"
        transition_module_status(
            db, module, to_status="initializing",
            task_id=task.id, reason="user clicked Init",
        )
        db.expire(module)
        assert module.status == "initializing"

        rows = _audit_rows(db, module.id)
        assert len(rows) == 1
        assert rows[0].from_status == "not_initialized"
        assert rows[0].to_status == "initializing"
        assert rows[0].task_id == task.id
        assert rows[0].fence_token is None  # unlocked path
        assert rows[0].reason == "user clicked Init"

    def test_legal_locked_transition_applies_via_fence(self, db, module, make_task):
        task = make_task(project=module.project, module=module)
        # Set status to a state from which "applied" is reachable. Bypass the
        # validator (raw db.execute) since this is fixture setup, not a real
        # transition we want audited.
        from sqlalchemy import text
        db.execute(text("UPDATE project_modules SET status = 'applying' WHERE id = :id"),
                   {"id": module.id})
        db.commit()
        db.expire(module, ["status"])

        svc = ModuleLockService(db)
        lock = svc.acquire(module.id, task_id=task.id)

        transition_module_status(
            db, module, to_status="applied", lock=lock,
            task_id=task.id, reason="tofu apply succeeded",
            extra_fields={"deployment_error": None},
        )
        db.expire(module)
        assert module.status == "applied"
        assert module.deployment_error is None

        rows = _audit_rows(db, module.id)
        assert len(rows) == 1
        assert rows[0].from_status == "applying"
        assert rows[0].to_status == "applied"
        assert rows[0].fence_token == lock.fence_token

    def test_self_loop_logs_audit_row(self, db, module):
        """Self-loops are no-ops for the status column but still log
        (caller asked explicitly with a reason — useful for tracing)."""
        transition_module_status(
            db, module, to_status="not_initialized",
            reason="idempotent re-set on retry",
        )
        rows = _audit_rows(db, module.id)
        assert len(rows) == 1
        assert rows[0].from_status == rows[0].to_status == "not_initialized"


# ---------------------------------------------------------------------------
# Illegal transitions
# ---------------------------------------------------------------------------


class TestIllegalTransition:
    def test_kleppmann_skip_raises(self, db, module):
        """The classic illegal jump from the RFC: not_initialized -> applied
        without going through init/apply must raise."""
        with pytest.raises(IllegalTransitionError) as exc:
            transition_module_status(db, module, to_status="applied")
        assert exc.value.from_status == "not_initialized"
        assert exc.value.to_status == "applied"
        assert exc.value.module_id == module.id

    def test_illegal_transition_does_not_change_status(self, db, module):
        original = module.status
        with pytest.raises(IllegalTransitionError):
            transition_module_status(db, module, to_status="applied")
        db.expire(module)
        assert module.status == original

    def test_illegal_transition_does_not_log_row(self, db, module):
        with pytest.raises(IllegalTransitionError):
            transition_module_status(db, module, to_status="applied")
        assert _audit_rows(db, module.id) == []


# ---------------------------------------------------------------------------
# extra_fields semantics
# ---------------------------------------------------------------------------


class TestExtraFields:
    def test_extra_fields_applied_atomically_with_lock(self, db, module, make_task):
        task = make_task(project=module.project, module=module)
        from sqlalchemy import text
        db.execute(text("UPDATE project_modules SET status = 'applying' WHERE id = :id"),
                   {"id": module.id})
        db.commit()
        db.expire(module, ["status"])

        svc = ModuleLockService(db)
        lock = svc.acquire(module.id, task_id=task.id)

        transition_module_status(
            db, module, to_status="applied", lock=lock, task_id=task.id,
            extra_fields={"outputs": {"vpc_id": "vpc-123"}},
        )
        db.expire(module)
        assert module.status == "applied"
        assert module.outputs == {"vpc_id": "vpc-123"}

    def test_extra_fields_ignored_without_lock(self, db, module, caplog):
        """No-lock path can't write extra_fields atomically — they would be
        unprotected. Helper logs a warning and skips them."""
        import logging
        with caplog.at_level(logging.WARNING):
            transition_module_status(
                db, module, to_status="initializing",
                extra_fields={"outputs": {"foo": "bar"}},
            )
        db.expire(module)
        assert module.status == "initializing"
        # outputs unchanged (was None / default)
        assert module.outputs is None
        # warning emitted
        assert any("extra_fields" in r.getMessage() and "ignored" in r.getMessage()
                   for r in caplog.records)
