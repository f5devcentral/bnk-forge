"""Component tests for services.execution_janitor.

D-001 Phase 3 S3b: reset_stale_parallel_executions removed (table dropped).
TestResetStaleParallelExecutions class and related fixtures deleted.
reset_stale_destroys reworked to stack-entity-only (no PE records).
"""

from unittest.mock import patch

import pytest

from services.execution_janitor import (
    reset_stale_destroys,
    reset_stale_executions,
    reset_stale_tasks,
)


class TestResetStaleTasks:
    def test_resets_queued_with_dead_celery_id(self, db, make_task):
        task = make_task(status="queued", celery_task_id="dead-task")
        reset_ids = reset_stale_tasks(db, live_task_ids=set())
        db.flush()
        assert task.id in reset_ids
        assert task.status == "failed"
        assert "Worker no longer alive" in (task.error or "")

    def test_resets_pending_and_in_progress(self, db, make_task):
        pending = make_task(status="pending", celery_task_id="t1")
        in_progress = make_task(status="in_progress", celery_task_id="t2")
        reset_ids = reset_stale_tasks(db, live_task_ids=set())
        assert pending.id in reset_ids
        assert in_progress.id in reset_ids

    def test_skips_live_celery_id(self, db, make_task):
        task = make_task(status="queued", celery_task_id="live-task")
        reset_ids = reset_stale_tasks(db, live_task_ids={"live-task"})
        assert task.id not in reset_ids
        assert task.status == "queued"

    @pytest.mark.parametrize(
        ("module_status", "expected"),
        [
            ("applying", "apply_failed"),
            ("planning", "plan_failed"),
            ("initializing", "init_failed"),
        ],
    )
    def test_resets_module_stuck_in_transient_state(
        self, db, make_task, make_project_module, module_status, expected
    ):
        """#6: a dead deploy worker must not leave the module transient forever.

        Flipping only the Task row left the UI showing a perpetually-applying
        module with no error and no Retry button (Retry only renders for *_failed),
        recoverable only by a manual UPDATE against the DB.
        """
        module = make_project_module(status=module_status)
        task = make_task(
            status="in_progress",
            task_type="apply",
            module=module,
            celery_task_id="dead-worker",
        )

        reset_ids = reset_stale_tasks(db, live_task_ids=set())
        db.flush()

        assert task.id in reset_ids
        assert module.status == expected
        assert "Worker no longer alive" in (module.deployment_error or "")

    def test_live_task_leaves_module_alone(self, db, make_task, make_project_module):
        """A running deploy must never be reset out from under itself."""
        module = make_project_module(status="applying")
        make_task(
            status="in_progress",
            task_type="apply",
            module=module,
            celery_task_id="live-task",
        )

        reset_stale_tasks(db, live_task_ids={"live-task"})
        db.flush()

        assert module.status == "applying"

    def test_terminal_module_status_is_not_rewritten(
        self, db, make_task, make_project_module
    ):
        """A module that already reached a terminal state keeps it."""
        module = make_project_module(status="applied")
        make_task(
            status="in_progress",
            task_type="apply",
            module=module,
            celery_task_id="dead-worker",
        )

        reset_stale_tasks(db, live_task_ids=set())
        db.flush()

        assert module.status == "applied"

    def test_existing_deployment_error_is_preserved(
        self, db, make_task, make_project_module
    ):
        """The real error, if one was recorded, must win over the janitor's note."""
        module = make_project_module(status="applying")
        module.deployment_error = "terraform: quota exceeded"
        make_task(
            status="in_progress",
            task_type="apply",
            module=module,
            celery_task_id="dead-worker",
        )

        reset_stale_tasks(db, live_task_ids=set())
        db.flush()

        assert module.status == "apply_failed"
        assert module.deployment_error == "terraform: quota exceeded"

    def test_skips_terminal_tasks(self, db, make_task):
        completed = make_task(status="completed", celery_task_id="x")
        failed = make_task(status="failed", celery_task_id="y")
        cancelled = make_task(status="cancelled", celery_task_id="z")
        reset_ids = reset_stale_tasks(db, live_task_ids=set())
        assert completed.id not in reset_ids
        assert failed.id not in reset_ids
        assert cancelled.id not in reset_ids


class TestResetStaleDestroys:
    """reset_stale_destroys sweeps StackInstance entities stuck in 'destroying'."""

    def test_stack_stuck_destroying_no_live_tasks_swept_to_failed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """StackInstance in DESTROYING with no live destroy tasks → swept to failed."""
        from models import ProjectModule, StackInstance

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroying",
            stack_instance_id=si.id,
        )
        db.add(mod)
        db.flush()
        si.deployed_modules = [mod.id]
        db.commit()

        result = reset_stale_destroys(db, live_task_ids=set())
        db.commit()

        assert si.id in result["stale_stack_ids"]
        # stale_pe_ids is always empty (table dropped)
        assert result["stale_pe_ids"] == []

        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        assert stack.status == "failed", f"Expected 'failed', got '{stack.status}'"

    def test_stack_stuck_destroying_with_live_task_not_swept(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """StackInstance in DESTROYING with a live destroy task → NOT swept."""
        from datetime import UTC, datetime

        from models import ProjectModule, StackInstance
        from models import Task as TaskModel

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroying",
            stack_instance_id=si.id,
        )
        db.add(mod)
        db.flush()
        si.deployed_modules = [mod.id]

        live_task = TaskModel(
            task_type="destroy",
            status="in_progress",
            project_id=project.id,
            module_id=mod.id,
            celery_task_id="live-celery-task-id",
            created_at=datetime.now(UTC),
        )
        db.add(live_task)
        db.commit()

        result = reset_stale_destroys(db, live_task_ids={"live-celery-task-id"})

        assert si.id not in result["stale_stack_ids"]

        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        assert stack.status == "destroying", "Live worker's stack should not be swept"


class TestResetStaleExecutionsEndToEnd:
    def test_one_pass_resets_tasks_and_stacks(
        self, db, make_task, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Single-pass janitor resets dead tasks and stuck stacks."""
        from models import ProjectModule

        stale_task = make_task(status="queued", celery_task_id="dead-task")
        live_task = make_task(status="queued", celery_task_id="live-task")

        project = make_project(name="janitor-e2e-project")
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")
        mod = ProjectModule(
            project_id=project.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroying",
            stack_instance_id=si.id,
        )
        db.add(mod)
        db.flush()
        si.deployed_modules = [mod.id]
        db.commit()

        with patch(
            "services.execution_janitor.get_live_task_ids",
            return_value={"live-task"},
        ):
            result = reset_stale_executions(db)

        # parallel_executions_reset always 0 (table dropped)
        assert result["parallel_executions_reset"] == 0
        assert result["tasks_reset"] == 1  # dead-task reset
        assert result["stale_destroy_stacks_reset"] == 1
        assert result["stale_destroy_pes_reset"] == 0  # table dropped
        assert result["live_task_ids"] == 1

        db.refresh(stale_task)
        db.refresh(live_task)
        assert stale_task.status == "failed"
        assert live_task.status == "queued"

    def test_no_op_when_nothing_stale(self, db):
        with patch("services.execution_janitor.get_live_task_ids", return_value=set()):
            result = reset_stale_executions(db)
        assert result["parallel_executions_reset"] == 0
        assert result["tasks_reset"] == 0
        assert result["live_task_ids"] == 0
        # C2 backstop counters are present
        assert result["stale_destroy_stacks_reset"] == 0
        assert result["stale_destroy_pes_reset"] == 0
