"""Component tests for tasks.container_tasks.run_container_action (D-034).

Verifies the post-apply status gate, that a run never mutates module.status,
and that the engine's run_action is driven with the requested action + inputs.
DB is real (in-memory); engine build, lock, and notification I/O are mocked.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from services.execution.engine_interface import OperationResult
from tests.factories import (
    ModuleLibraryFactory,
    ProjectFactory,
    ProjectModuleFactory,
    TaskFactory,
)

_MOD = "tasks.container_tasks"


def _make_action_module(db, status="applied"):
    project = ProjectFactory(db)
    lib = ModuleLibraryFactory(
        db,
        category="container",
        execution_engine="container",
        pack_manifest={
            "container_image": {"registry_host": "ghcr.io"},
            # A declared run-scenario action so the task-level input re-validation
            # (N1) has a manifest to check the invocation inputs against.
            "actions": {
                "run-scenario": {
                    "title": "Run one scenario",
                    "steps": [{"name": "run", "args": ["ocibnkctl", "scenario", "run", "{{inputs.scenario}}"]}],
                    "inputs": [
                        {"name": "scenario", "type": "string", "choices": ["tcpl4lb", "bgppeer"]}
                    ],
                }
            },
        },
    )
    module = ProjectModuleFactory(db, project=project, library_module=lib, status=status)
    task = TaskFactory(db, project=project, module=module, task_type="action")
    db.commit()
    return module, task


def _db_ctx(db):
    ctx = MagicMock()
    ctx.return_value.__enter__ = MagicMock(return_value=db)
    ctx.return_value.__exit__ = MagicMock(return_value=False)
    return ctx


def _naive_datetime(mock_dt):
    """Make the task's datetime.now() return naive UTC datetimes matching SQLite.

    SQLite round-trips DateTime columns as naive; mixing a fresh tz-aware
    completed_at with a reloaded naive started_at raises TypeError (same
    workaround as tests/component/test_opentofu_tasks.py).
    """
    mock_dt.now.return_value = datetime.utcnow()
    mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)


@pytest.mark.component
class TestRunContainerAction:
    @patch(f"{_MOD}._publish_task_completion")
    @patch(f"{_MOD}.get_db_context")
    def test_action_failsFastWhenModuleNotApplied(self, mock_db_ctx, _mock_publish, db):
        # Arrange
        module, task = _make_action_module(db, status="initialized")
        mock_db_ctx.return_value = _db_ctx(db).return_value

        # Act
        from tasks.container_tasks import run_container_action
        result = run_container_action(task.id, module.id, "run-scenario")

        # Assert
        assert result["success"] is False
        assert "initialized" in result["error"]
        db.refresh(task)
        assert task.status == "failed"
        db.refresh(module)
        assert module.status == "initialized"  # untouched

    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._publish_task_completion")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}._build_engine_and_ctx")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_action_succeeds_withoutChangingModuleStatus(
        self, mock_dt, mock_db_ctx, mock_build, mock_lock, _notify, _publish, mock_deploy_rec, db
    ):
        # Arrange
        _naive_datetime(mock_dt)
        module, task = _make_action_module(db, status="applied")
        mock_db_ctx.return_value = _db_ctx(db).return_value
        mock_lock.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        engine = MagicMock()
        engine.run_action.return_value = OperationResult(success=True, stdout="scenario ok")
        ctx = MagicMock()
        ctx.path = "tools/ocibnkctl"
        mock_build.return_value = (engine, ctx)

        # Act
        from tasks.container_tasks import run_container_action
        result = run_container_action(
            task.id, module.id, "run-scenario", action_inputs={"scenario": "tcpl4lb"}
        )

        # Assert
        assert result == {"success": True, "exit_code": 0}
        db.refresh(task)
        assert task.status == "completed"
        assert task.meta_data == {"action": "run-scenario"}
        db.refresh(module)
        assert module.status == "applied"  # actions never mutate module status

        args, kwargs = engine.run_action.call_args
        assert args[1] == "run-scenario"
        assert kwargs["action_inputs"] == {"scenario": "tcpl4lb"}

        # Deployment record labeled with the action name.
        rec_args = mock_deploy_rec.call_args[0]
        assert rec_args[3] == "action:run-scenario"

    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._publish_task_completion")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}._build_engine_and_ctx")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_action_stepFailure_failsTaskButKeepsModuleApplied(
        self, mock_dt, mock_db_ctx, mock_build, mock_lock, _notify, _publish, _deploy_rec, db
    ):
        # Arrange
        _naive_datetime(mock_dt)
        module, task = _make_action_module(db, status="applied")
        mock_db_ctx.return_value = _db_ctx(db).return_value
        mock_lock.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        engine = MagicMock()
        engine.run_action.return_value = OperationResult(
            success=False, error_message="step 'run' failed (exit 3)"
        )
        ctx = MagicMock()
        ctx.path = "tools/ocibnkctl"
        mock_build.return_value = (engine, ctx)

        # Act
        from tasks.container_tasks import run_container_action
        result = run_container_action(task.id, module.id, "run-scenario")

        # Assert
        assert result == {"success": False, "exit_code": 1}
        db.refresh(task)
        assert task.status == "failed"
        assert "exit 3" in task.error
        db.refresh(module)
        assert module.status == "applied"  # a failed action is not apply_failed

    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._publish_task_completion")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}._build_engine_and_ctx")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_action_inLockStatusChange_failsWithoutRunningSteps(
        self, mock_dt, mock_db_ctx, mock_build, mock_lock, _notify, _publish, _deploy_rec, db
    ):
        """TOCTOU (D-034 F3): status flips away from 'applied' after the pre-lock
        check but the in-lock re-check catches it and refuses to run steps."""
        # Arrange
        _naive_datetime(mock_dt)
        module, task = _make_action_module(db, status="applied")
        mock_db_ctx.return_value = _db_ctx(db).return_value

        # Simulate a concurrent destroy winning the lock: by the time this action
        # acquires it, the module is no longer 'applied'.
        from sqlalchemy import text

        def _flip_on_lock():
            db.execute(
                text("UPDATE project_modules SET status = 'destroying' WHERE id = :id"),
                {"id": module.id},
            )
            db.commit()
            return MagicMock()

        mock_lock.return_value.__enter__ = MagicMock(side_effect=_flip_on_lock)
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        # Act
        from tasks.container_tasks import run_container_action
        result = run_container_action(task.id, module.id, "run-scenario")

        # Assert
        assert result["success"] is False
        assert "cluster state changed" in result["error"]
        mock_build.assert_not_called()  # steps were never built or run
        db.refresh(task)
        assert task.status == "failed"

    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._publish_task_completion")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}._build_engine_and_ctx")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_action_taskLevel_rejectsInvalidInputsBeforeRunning(
        self, mock_dt, mock_db_ctx, mock_build, mock_lock, _notify, _publish, _deploy_rec, db
    ):
        """N1 (#457 re-review): a caller reaching the task directly with an
        out-of-choices input is rejected in-task, before the engine runs —
        the submit_action filter is re-asserted at the dispatch entry point."""
        _naive_datetime(mock_dt)
        module, task = _make_action_module(db, status="applied")
        mock_db_ctx.return_value = _db_ctx(db).return_value
        mock_lock.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.container_tasks import run_container_action
        result = run_container_action(
            task.id, module.id, "run-scenario", action_inputs={"scenario": "not-a-choice"}
        )

        assert result["success"] is False
        assert "Invalid inputs" in result["error"]
        mock_build.assert_not_called()  # rejected before the engine was built
        db.refresh(task)
        assert task.status == "failed"
