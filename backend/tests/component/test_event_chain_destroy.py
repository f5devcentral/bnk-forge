"""
Tests for event-chain destroy orchestration (D-001 Phase 3 — S1/S3b).

Mechanism: reverse-DAG self-triggering chain mirroring the deploy path.
  - First destroy wave: dispatches leaf modules (no deployed dependents).
  - _trigger_next_destroy_module: queued after each engine destroy completes.
  - Fail-closed barrier: dependency queued only when ALL its dependents are terminal.
  - _finalize_destroy: called when all modules are terminal (no failures).

Covers:
- First-wave selection (no deployed dependents), no-infra skip, idempotency guard
- Trigger predicate: dependency queued only when all dependents terminal (destroyed)
- Trigger predicate fail-closed: destroy_failed dependent halts descent
- Trigger predicate double-dispatch guard (S15-008)
- Terminal detection: all-destroyed → finalize (stack deletes modules / project doesn't)
- Terminal detection: any destroy_failed → entity failed
- _finalize_destroy: stack-entry deletes modules + sets destroyed
- _finalize_destroy: project-entry does NOT delete modules (no PE record; table dropped v2_121)
- _finalize_destroy: handles missing stack gracefully
- _mark_entity_failed: stack path; project path logs only (no PE record)
- Worker-death backstop: relies on reset_stale_tasks (no chord_unlock dependency)
- C1 fix: exception-path (generic + lock) fires trigger → entity reaches terminal failed
- C2 fix: reset_stale_destroys sweeps stuck DESTROYING stacks to failed
- Terminal-detection idempotency: double-finalize is a safe no-op

D-001 Phase 3 S3b: ParallelExecution table dropped. Tests that previously verified
PE record updates have been reworked to verify behavior via Task rows and entity state.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models import ProjectModule, StackInstance
from models import Task as TaskModel

_PT_MOD = "tasks.parallel_tasks"
_TH_MOD = "tasks._tofu_helpers"


# ---------------------------------------------------------------------------
# _finalize_destroy helper (plain function, not a Celery task)
# ---------------------------------------------------------------------------

class TestFinalizeDestroy:
    """_finalize_destroy — stack deletes modules, project doesn't."""

    def test_stack_entry_deletes_modules_and_sets_destroyed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """_finalize_destroy with entry_kind=stack deletes deployed module records and sets destroyed."""
        from tasks.parallel_tasks import _finalize_destroy

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroyed",
            stack_instance_id=si.id,
        )
        db.add(mod)
        db.flush()
        mod_id = mod.id
        si.deployed_modules = [mod_id]
        db.commit()

        _finalize_destroy(db, "stack", si.id)

        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        assert stack is not None
        assert stack.status == "destroyed"
        assert stack.deployed_modules == []
        assert db.query(ProjectModule).filter_by(id=mod_id).first() is None

    def test_project_entry_does_not_delete_modules(
        self, db, make_project, make_project_module, make_module_library
    ):
        """_finalize_destroy with entry_kind=project does NOT delete ProjectModule records.

        D-001 Phase 3 S3b: PE table dropped; we only verify module records are not deleted.
        """
        from tasks.parallel_tasks import _finalize_destroy

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="destroyed")
        mod_id = mod.id
        db.commit()

        _finalize_destroy(db, "project", project.id)

        db.expire_all()
        # Module should NOT be deleted
        assert db.query(ProjectModule).filter_by(id=mod_id).first() is not None

    def test_stack_not_found_does_not_raise(self, db):
        """_finalize_destroy handles missing stack gracefully (no crash)."""
        from tasks.parallel_tasks import _finalize_destroy
        # Should not raise; stack 99999 doesn't exist
        _finalize_destroy(db, "stack", 99999)

    def test_project_entry_completes_without_error(
        self, db, make_project, make_project_module, make_module_library
    ):
        """_finalize_destroy for project-entry completes without error (no PE to update)."""
        from tasks.parallel_tasks import _finalize_destroy

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        make_project_module(project=project, library_module=lib, status="destroyed")
        db.commit()

        # Should complete without raising — no PE record to update (table dropped)
        _finalize_destroy(db, "project", project.id)
        # No assertions needed; absence of exception is the contract


# ---------------------------------------------------------------------------
# _mark_entity_failed
# ---------------------------------------------------------------------------

class TestMarkEntityFailed:
    """_mark_entity_failed — sets stack entity to failed state.

    D-001 Phase 3 S3b: PE table dropped. Project-entry now logs only (no PE record to update).
    """

    def test_marks_stack_failed(self, db, make_project, make_stack_template, make_stack_instance):
        """Stack-entry: sets stack.status = failed."""
        from tasks.parallel_tasks import _mark_entity_failed

        project = make_project()
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        _mark_entity_failed(db, "stack", si.id, "Destroy stopped: 1 module(s) failed")

        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        assert stack.status == "failed"
        assert "failed" in stack.error_message

    def test_marks_project_failed_does_not_raise(self, db, make_project):
        """Project-entry: no PE to update (table dropped); must complete without error."""
        from tasks.parallel_tasks import _mark_entity_failed

        project = make_project()
        db.commit()

        # Should complete without error — no PE record to update (table dropped)
        _mark_entity_failed(db, "project", project.id, "Destroy stopped: 2 module(s) failed")

    def test_handles_missing_stack_gracefully(self, db):
        """No crash when stack doesn't exist."""
        from tasks.parallel_tasks import _mark_entity_failed
        _mark_entity_failed(db, "stack", 99999, "error")


# ---------------------------------------------------------------------------
# _dispatch_first_destroy_wave (ParallelExecutionService)
# ---------------------------------------------------------------------------

class TestDispatchFirstDestroyWave:
    """First destroy wave: leaf modules dispatched, non-leaf and no-infra skipped."""

    def test_dispatches_leaf_module(self, db, make_project, make_project_module, make_module_library):
        """Module with no deployed dependents → dispatch queued."""
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        mock_result = MagicMock()
        mock_result.id = "test-celery-id-123"
        mock_sig = MagicMock()
        mock_sig.apply_async.return_value = mock_result

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", return_value=mock_sig):
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = []  # No dependents
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            first_id = svc._dispatch_first_destroy_wave(project.id)

        assert first_id == "test-celery-id-123"
        db.expire_all()
        task = db.query(TaskModel).filter(
            TaskModel.module_id == mod.id,
            TaskModel.task_type == "destroy",
        ).first()
        assert task is not None
        assert task.celery_task_id == "test-celery-id-123"

    def test_skips_no_infra_module(self, db, make_project, make_project_module, make_module_library):
        """Module with no-infra status → not dispatched."""
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        make_project_module(project=project, library_module=lib, status="not_initialized")

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature") as mock_dispatch:
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = []
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            first_id = svc._dispatch_first_destroy_wave(project.id)

        assert first_id is None
        mock_dispatch.assert_not_called()

    def test_skips_module_with_deployed_dependent(self, db, make_project, make_project_module, make_module_library):
        """Module whose dependent is still deployed → not in first wave."""
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib_root = make_module_library(name="vpc", path="bnk/vpc")
        lib_leaf = make_module_library(name="eks", path="bnk/eks")
        root_mod = make_project_module(project=project, library_module=lib_root, status="applied")
        leaf_mod = make_project_module(project=project, library_module=lib_leaf, status="applied")

        dispatched_module_ids = []

        def capture_dispatch(task_id, module):
            dispatched_module_ids.append(module.id)
            mock = MagicMock()
            mock.apply_async.return_value.id = f"celery-{module.id}"
            return mock

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", side_effect=capture_dispatch):
            mock_graph = MagicMock()

            def get_reverse_deps(module_id, project_id):
                # root_mod is depended on by leaf_mod
                if module_id == root_mod.id:
                    return [leaf_mod]
                return []

            mock_graph.get_reverse_dependencies.side_effect = get_reverse_deps
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            svc._dispatch_first_destroy_wave(project.id)

        # Only leaf_mod (no dependents) should be dispatched
        assert leaf_mod.id in dispatched_module_ids
        assert root_mod.id not in dispatched_module_ids

    def test_idempotency_skips_existing_task(self, db, make_project, make_project_module, make_module_library):
        """Module with existing non-terminal destroy task → skipped (double-dispatch guard)."""
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        # Pre-create a queued destroy task
        existing = TaskModel(
            task_type="destroy",
            status="queued",
            project_id=project.id,
            module_id=mod.id,
            celery_task_id="existing-celery-id",
        )
        db.add(existing)
        db.commit()

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature") as mock_dispatch:
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = []
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            first_id = svc._dispatch_first_destroy_wave(project.id)

        # No new task dispatched — uses existing
        mock_dispatch.assert_not_called()
        # first_id is set from existing task's celery id
        assert first_id == "existing-celery-id"


# ---------------------------------------------------------------------------
# _trigger_next_destroy_module predicate
# ---------------------------------------------------------------------------

class TestTriggerNextDestroyModule:
    """Trigger predicate: fail-closed barrier, double-dispatch guard."""

    def test_queues_dependency_when_all_dependents_destroyed(
        self, db, make_project, make_project_module, make_module_library
    ):
        """When all dependents of D are destroyed, D's destroy is queued."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib_root = make_module_library(name="vpc", path="bnk/vpc")
        lib_leaf = make_module_library(name="eks", path="bnk/eks")

        root_mod = make_project_module(project=project, library_module=lib_root, status="applied")
        leaf_mod = make_project_module(
            project=project, library_module=lib_leaf, status="destroyed",
            dependencies=[root_mod.id],
        )

        # leaf_mod just completed destroy; root_mod is its dependency
        dispatched = []

        def capture_dispatch(task_id, module):
            dispatched.append(module.id)
            mock = MagicMock()
            mock.apply_async.return_value.id = f"celery-{module.id}"
            return mock

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", side_effect=capture_dispatch), \
             patch("tasks._tofu_helpers._run_terminal_detection"):
            mock_graph = MagicMock()
            # leaf_mod is the only dependent of root_mod
            mock_graph.get_reverse_dependencies.return_value = [leaf_mod]
            mock_gs.return_value = mock_graph

            _trigger_next_destroy_module(leaf_mod, db)

        # root_mod should be dispatched since its only dependent (leaf_mod) is destroyed
        assert root_mod.id in dispatched

    def test_does_not_queue_if_dependent_is_destroy_failed(
        self, db, make_project, make_project_module, make_module_library
    ):
        """Fail-closed: if any dependent is destroy_failed, D is never queued."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib_root = make_module_library(name="vpc", path="bnk/vpc")
        lib_leaf = make_module_library(name="eks", path="bnk/eks")

        root_mod = make_project_module(project=project, library_module=lib_root, status="applied")
        leaf_mod = make_project_module(
            project=project, library_module=lib_leaf, status="destroy_failed",
            dependencies=[root_mod.id],
        )

        dispatched = []

        def capture_dispatch(task_id, module):
            dispatched.append(module.id)
            return MagicMock()

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", side_effect=capture_dispatch), \
             patch("tasks._tofu_helpers._run_terminal_detection"):
            mock_graph = MagicMock()
            # leaf_mod is destroy_failed — not terminal-destroyed (fail-closed)
            mock_graph.get_reverse_dependencies.return_value = [leaf_mod]
            mock_gs.return_value = mock_graph

            _trigger_next_destroy_module(leaf_mod, db)

        # root_mod should NOT be dispatched — fail-closed barrier
        assert root_mod.id not in dispatched

    def test_does_not_queue_if_dependent_still_destroying(
        self, db, make_project, make_project_module, make_module_library
    ):
        """Fail-closed: if any dependent is still destroying, D is never queued."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib_root = make_module_library(name="vpc", path="bnk/vpc")
        lib_leaf_a = make_module_library(name="eks", path="bnk/eks")
        lib_leaf_b = make_module_library(name="security", path="bnk/security")

        root_mod = make_project_module(project=project, library_module=lib_root, status="applied")
        leaf_a = make_project_module(
            project=project, library_module=lib_leaf_a, status="destroyed",
            dependencies=[root_mod.id],
        )
        leaf_b = make_project_module(
            project=project, library_module=lib_leaf_b, status="destroying",  # still active!
            dependencies=[root_mod.id],
        )

        dispatched = []

        def capture_dispatch(task_id, module):
            dispatched.append(module.id)
            return MagicMock()

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", side_effect=capture_dispatch), \
             patch("tasks._tofu_helpers._run_terminal_detection"):
            mock_graph = MagicMock()
            # Both leaf_a and leaf_b depend on root_mod
            mock_graph.get_reverse_dependencies.return_value = [leaf_a, leaf_b]
            mock_gs.return_value = mock_graph

            _trigger_next_destroy_module(leaf_a, db)

        # root_mod NOT dispatched — leaf_b still destroying
        assert root_mod.id not in dispatched

    def test_double_dispatch_guard_skips_existing_task(
        self, db, make_project, make_project_module, make_module_library
    ):
        """Double-dispatch guard: if D already has non-terminal destroy task, skip."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib_root = make_module_library(name="vpc", path="bnk/vpc")
        lib_leaf = make_module_library(name="eks", path="bnk/eks")

        root_mod = make_project_module(project=project, library_module=lib_root, status="applied")
        leaf_mod = make_project_module(
            project=project, library_module=lib_leaf, status="destroyed",
            dependencies=[root_mod.id],
        )

        # Pre-create a queued destroy task for root_mod (simulate concurrent trigger)
        existing = TaskModel(
            task_type="destroy",
            status="in_progress",
            project_id=project.id,
            module_id=root_mod.id,
            celery_task_id="existing-task",
        )
        db.add(existing)
        db.commit()

        dispatched = []

        def capture_dispatch(task_id, module):
            dispatched.append(module.id)
            return MagicMock()

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", side_effect=capture_dispatch), \
             patch("tasks._tofu_helpers._run_terminal_detection"):
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = [leaf_mod]
            mock_gs.return_value = mock_graph

            _trigger_next_destroy_module(leaf_mod, db)

        # root_mod should NOT be dispatched again (double-dispatch guard)
        assert root_mod.id not in dispatched

    def test_skips_no_infra_dependency(
        self, db, make_project, make_project_module, make_module_library
    ):
        """Dependency with no-infra status is skipped — nothing to destroy."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib_root = make_module_library(name="vpc", path="bnk/vpc")
        lib_leaf = make_module_library(name="eks", path="bnk/eks")

        root_mod = make_project_module(
            project=project, library_module=lib_root, status="not_initialized"
        )
        leaf_mod = make_project_module(
            project=project, library_module=lib_leaf, status="destroyed",
            dependencies=[root_mod.id],
        )

        dispatched = []

        def capture_dispatch(task_id, module):
            dispatched.append(module.id)
            return MagicMock()

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", side_effect=capture_dispatch), \
             patch("tasks._tofu_helpers._run_terminal_detection"):
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = [leaf_mod]
            mock_gs.return_value = mock_graph

            _trigger_next_destroy_module(leaf_mod, db)

        # root_mod not dispatched — no infra
        assert root_mod.id not in dispatched


# ---------------------------------------------------------------------------
# Terminal detection: stack scope
# ---------------------------------------------------------------------------

class TestTerminalDetectionStack:
    """_run_terminal_detection_stack: finalize or fail."""

    def test_all_destroyed_triggers_finalize(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """All stack modules destroyed → finalize called."""
        from tasks._tofu_helpers import _run_terminal_detection_stack

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroyed",
            stack_instance_id=si.id,
        )
        db.add(mod)
        db.flush()
        si.deployed_modules = [mod.id]
        db.commit()

        with patch(f"{_PT_MOD}._finalize_destroy") as mock_finalize, \
             patch(f"{_PT_MOD}._mark_entity_failed") as mock_fail:
            _run_terminal_detection_stack(mod, db)

        mock_finalize.assert_called_once_with(db, "stack", si.id)
        mock_fail.assert_not_called()

    def test_destroy_failed_marks_entity_failed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Any destroy_failed module → _mark_entity_failed called."""
        from tasks._tofu_helpers import _run_terminal_detection_stack

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        mod = ProjectModule(
            project_id=project.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroy_failed",
            stack_instance_id=si.id,
        )
        db.add(mod)
        db.flush()
        si.deployed_modules = [mod.id]
        db.commit()

        with patch(f"{_PT_MOD}._finalize_destroy") as mock_finalize, \
             patch(f"{_PT_MOD}._mark_entity_failed") as mock_fail:
            _run_terminal_detection_stack(mod, db)

        mock_fail.assert_called_once()
        args = mock_fail.call_args[0]
        assert args[1] == "stack"
        assert args[2] == si.id
        assert "1 module(s) failed" in args[3]
        mock_finalize.assert_not_called()

    def test_non_terminal_module_defers(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Module still destroying → neither finalize nor fail called."""
        from tasks._tofu_helpers import _run_terminal_detection_stack

        project = make_project()
        lib_a = make_module_library(name="vpc", path="bnk/vpc")
        lib_b = make_module_library(name="eks", path="bnk/eks")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        mod_a = ProjectModule(
            project_id=project.id,
            module_library_id=lib_a.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroyed",
            stack_instance_id=si.id,
        )
        mod_b = ProjectModule(
            project_id=project.id,
            module_library_id=lib_b.id,
            path_in_project=f"stack-{si.id}/bnk/eks",
            status="destroying",  # still in progress
            stack_instance_id=si.id,
        )
        db.add_all([mod_a, mod_b])
        db.flush()
        si.deployed_modules = [mod_a.id, mod_b.id]
        db.commit()

        with patch(f"{_PT_MOD}._finalize_destroy") as mock_finalize, \
             patch(f"{_PT_MOD}._mark_entity_failed") as mock_fail:
            _run_terminal_detection_stack(mod_a, db)

        mock_finalize.assert_not_called()
        mock_fail.assert_not_called()


# ---------------------------------------------------------------------------
# Worker-death backstop (reset_stale_tasks)
# ---------------------------------------------------------------------------

class TestWorkerDeathBackstop:
    """
    Worker-death recovery via reset_stale_tasks (no chord_unlock dependency).

    The event-chain destroy has NO chord_unlock dependency. Worker death leaves
    a non-terminal Task row whose celery_task_id is no longer live. The
    reset_stale_tasks janitor finds it (any non-terminal task not in live set →
    failed) AND, for destroy tasks, drives the owning module to destroy_failed
    and re-runs _trigger_next_destroy_module so the reverse-DAG chain resumes
    (C-1/H-1 fix) — without any new infrastructure.

    This test confirms:
    1. reset_stale_tasks marks a stale destroy task as failed (no broker needed).
    2. The module LEAVES 'destroying' (→ destroy_failed) and terminal detection
       re-fires, driving the run to a terminal failed state.
    3. The chain advances to a queued dependency once its dependent is terminal.
    4. The destroy path creates standard TaskModel rows with celery_task_id set.
    5. There is no chord_unlock or chord-specific artifact — plain task rows only.
    """

    def test_reset_stale_destroy_task_recovers_module_and_run(
        self, db, make_project, make_project_module, make_module_library
    ):
        """A stale destroy task whose worker died must:
          - flip the Task row to failed (existing behaviour), AND
          - move the module out of 'destroying' to 'destroy_failed', AND
          - re-run terminal detection so the project run reaches terminal failed.
        This genuinely exercises resume-after-death, not just the task-row flip.
        """
        from services.execution_janitor import reset_stale_tasks

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        # Single-module project destroy with no dependencies — the module is the
        # whole run, so once it is terminal the run is terminal.
        mod = make_project_module(project=project, library_module=lib, status="destroying")

        # Simulate a destroy task in_progress whose worker died (OOM kill before
        # any Python handler ran — module never left 'destroying').
        stuck_task = TaskModel(
            task_type="destroy",
            status="in_progress",
            project_id=project.id,
            module_id=mod.id,
            celery_task_id="dead-worker-task-id",
            created_at=datetime.now(UTC),
        )
        db.add(stuck_task)
        db.commit()

        # Spy on the project terminal handler to prove terminal detection re-ran
        # and drove the run to failed (project has no top-level status field).
        with patch(f"{_PT_MOD}._mark_entity_failed") as mock_fail:
            reset_ids = reset_stale_tasks(db, live_task_ids=set())
            db.commit()

        # (1) Task row flipped to failed
        assert stuck_task.id in reset_ids
        db.refresh(stuck_task)
        assert stuck_task.status == "failed"
        assert "janitor" in (stuck_task.error or "")

        # (2) Module left 'destroying' → 'destroy_failed'
        db.refresh(mod)
        assert mod.status == "destroy_failed"

        # (3) Terminal detection re-ran and drove the project run to failed.
        mock_fail.assert_called_once()
        args = mock_fail.call_args.args
        assert args[1] == "project"
        assert args[2] == project.id

    def test_reset_stale_destroy_chain_holds_fail_closed(
        self, db, make_project, make_project_module, make_module_library
    ):
        """When the dead worker's module had a deeper dependency, recovery re-runs
        the reverse-DAG chain. Because worker-death recovery sets destroy_failed
        (NOT destroyed), the fail-closed barrier must HOLD: the dependency's infra
        is left intact rather than being silently destroyed past a failed dependent.
        This proves the janitor re-drives terminal detection AND makes the correct
        fail-closed decision (the whole point of the event-chain pivot).
        """
        from services.execution_janitor import reset_stale_tasks

        project = make_project()
        lib_leaf = make_module_library(name="eks", path="bnk/eks")
        lib_root = make_module_library(name="vpc", path="bnk/vpc")

        # root (vpc) has live infra; leaf (eks) depends on root and was destroying
        # when its worker died. eks is the only dependent of vpc.
        root = make_project_module(project=project, library_module=lib_root, status="applied")
        leaf = make_project_module(
            project=project,
            library_module=lib_leaf,
            status="destroying",
            dependencies=[root.id],
        )
        db.commit()

        stuck_task = TaskModel(
            task_type="destroy",
            status="in_progress",
            project_id=project.id,
            module_id=leaf.id,
            celery_task_id="dead-worker-task-id",
            created_at=datetime.now(UTC),
        )
        db.add(stuck_task)
        db.commit()

        # Stub the actual dispatch so no broker is touched; capture the queued task.
        mock_result = MagicMock()
        mock_result.id = "celery-root-destroy"
        mock_sig = MagicMock()
        mock_sig.apply_async.return_value = mock_result

        with patch(
            "services.execution.task_dispatch.dispatch_destroy_signature",
            return_value=mock_sig,
        ):
            reset_ids = reset_stale_tasks(db, live_task_ids=set())
            db.commit()

        assert stuck_task.id in reset_ids

        # leaf moved to terminal destroy_failed (left 'destroying')
        db.refresh(leaf)
        assert leaf.status == "destroy_failed"

        # NOTE: fail-closed barrier — leaf is destroy_failed (NOT destroyed), which
        # does NOT clear the barrier, so root is intentionally NOT queued. This
        # asserts the chain re-ran terminal detection and made the correct
        # fail-closed decision rather than orphaning by silently advancing.
        root_destroy_task = (
            db.query(TaskModel)
            .filter(
                TaskModel.module_id == root.id,
                TaskModel.task_type == "destroy",
            )
            .first()
        )
        assert root_destroy_task is None
        db.refresh(root)
        assert root.status == "applied"  # untouched — barrier held

    def test_destroy_task_has_celery_task_id(
        self, db, make_project, make_project_module, make_module_library
    ):
        """
        Dispatch first destroy wave creates tasks with celery_task_id set —
        prerequisite for janitor recovery. Confirms event-chain uses plain task rows
        with no chord_unlock or chord-specific fields.
        """
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        mock_result = MagicMock()
        mock_result.id = "celery-task-xyz"
        mock_sig = MagicMock()
        mock_sig.apply_async.return_value = mock_result

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", return_value=mock_sig):
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = []
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            svc._dispatch_first_destroy_wave(project.id)

        task = db.query(TaskModel).filter(
            TaskModel.module_id == mod.id,
            TaskModel.task_type == "destroy",
        ).first()
        assert task is not None
        assert task.celery_task_id == "celery-task-xyz"
        # No chord_unlock, no chord-related fields — plain task row
        assert not hasattr(task, "chord_id")


# ---------------------------------------------------------------------------
# C1: Exception-path fires trigger → entity reaches terminal failed (r1 fix)
# ---------------------------------------------------------------------------

class TestExceptionPathFiresTrigger:
    """C1 fix: _trigger_next_destroy_module is called even when a destroy task raises.

    These tests verify that after an exception (generic or lock), the module's status
    is set to destroy_failed and terminal detection runs, driving the stack to 'failed'
    rather than staying stuck in 'destroying'.

    We test the _trigger_next_destroy_module + terminal-detection path directly (not
    the Celery task bodies, which require broker infrastructure) to prove the contract:
    module.status = destroy_failed → trigger fires → entity transitions to terminal.
    """

    def test_destroy_failed_last_module_drives_stack_to_failed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """If the last module in a stack destroy reaches destroy_failed and trigger fires,
        the stack transitions to 'failed' (not stuck in 'destroying')."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        # Single module in the destroy scope, simulate exception-path result
        mod = ProjectModule(
            project_id=project.id,
            module_library_id=lib.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroy_failed",  # exception handler set this before calling trigger
            stack_instance_id=si.id,
        )
        db.add(mod)
        db.flush()
        si.deployed_modules = [mod.id]
        db.commit()

        # No dependencies → trigger skips dependency loop and goes straight to terminal detection.
        # No mock — let the real terminal detection run.
        _trigger_next_destroy_module(mod, db)

        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        # Stack should be terminal (failed), NOT stuck in 'destroying'
        assert stack.status == "failed", f"Expected 'failed', got '{stack.status}'"

    def test_destroy_failed_last_module_drives_project_to_failed_state(
        self, db, make_project, make_project_module, make_module_library
    ):
        """If the last project module reaches destroy_failed and trigger fires,
        _mark_entity_failed is called (project-entry, no PE to update in S3b).

        We verify by confirming the terminal detection path completes without error
        and that module state is unchanged (no cascading deletion).

        D-001 Phase 3 S3b: PE table dropped; project failure is recorded via Task
        row statuses (all failed tasks). No PE record to assert on.
        """
        from models import Task as TaskModel
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="destroy_failed")

        # Create a destroy Task with run_handle so the run-handle-scoped detection fires
        task = TaskModel(
            task_type="destroy",
            status="failed",
            project_id=project.id,
            module_id=mod.id,
            run_handle="test-run-handle-project-fail",
            created_at=datetime.now(UTC),
        )
        db.add(task)
        db.commit()

        # Should complete without error — terminal detection fires, logs failure
        _trigger_next_destroy_module(mod, db)

        db.expire_all()
        # Module should still exist (project-entry doesn't delete modules)
        assert db.query(type(mod)).filter_by(id=mod.id).first() is not None

    def test_module_with_no_deps_fires_terminal_detection_after_exception(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Equivalent of generic-exception path: module set to destroy_failed, trigger called,
        terminal detection fires. Verifies the C1 fix contract without needing a real worker."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib_a = make_module_library(name="vpc", path="bnk/vpc")
        lib_b = make_module_library(name="eks", path="bnk/eks")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        # mod_a = already destroyed; mod_b = just failed (exception path)
        mod_a = ProjectModule(
            project_id=project.id,
            module_library_id=lib_a.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroyed",
            stack_instance_id=si.id,
        )
        mod_b = ProjectModule(
            project_id=project.id,
            module_library_id=lib_b.id,
            path_in_project=f"stack-{si.id}/bnk/eks",
            status="destroy_failed",  # exception handler set this
            stack_instance_id=si.id,
        )
        db.add_all([mod_a, mod_b])
        db.flush()
        si.deployed_modules = [mod_a.id, mod_b.id]
        db.commit()

        # Simulate the trigger being called from the exception handler (C1 fix)
        _trigger_next_destroy_module(mod_b, db)

        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        # Both modules terminal → should be failed (not stuck)
        assert stack.status == "failed", f"Expected 'failed', got '{stack.status}'"


# ---------------------------------------------------------------------------
# C2: reset_stale_destroys backstop (r1 fix)
# ---------------------------------------------------------------------------

class TestResetStaleDestroys:
    """C2 fix: reset_stale_destroys sweeps StackInstance entities stuck in 'destroying'.

    D-001 Phase 3 S3b: PE table dropped. Only StackInstance entities are swept.
    Project-scope destroys are recovered via reset_stale_tasks (Task rows).
    """

    def test_stack_stuck_destroying_with_no_live_tasks_swept_to_failed(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Stack in DESTROYING with no live destroy tasks → swept to failed."""
        from services.execution_janitor import reset_stale_destroys

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

        # No live task IDs → worker is dead
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
        """Stack in DESTROYING with a live destroy task → NOT swept (worker still active)."""
        from services.execution_janitor import reset_stale_destroys

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

        # Create a live destroy task
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

        # Live task id is present → NOT swept
        result = reset_stale_destroys(db, live_task_ids={"live-celery-task-id"})

        assert si.id not in result["stale_stack_ids"]

        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        assert stack.status == "destroying", "Live worker's stack should not be swept"


# ---------------------------------------------------------------------------
# Terminal-detection idempotency (r1 warning fix)
# ---------------------------------------------------------------------------

class TestTerminalDetectionIdempotency:
    """Double-finalize is a safe no-op.

    When two modules complete near-simultaneously, both call _trigger_next_destroy_module
    → both run _run_terminal_detection_stack. The second call should be a no-op because
    the stack is no longer in DESTROYING state after the first finalize commits.
    """

    def test_double_terminal_detection_is_safe_noop_for_stack(
        self, db, make_project, make_stack_template, make_stack_instance, make_module_library
    ):
        """Calling _run_terminal_detection_stack twice on an already-finalized stack is safe."""
        from tasks._tofu_helpers import _run_terminal_detection_stack

        project = make_project()
        lib_a = make_module_library(name="vpc", path="bnk/vpc")
        lib_b = make_module_library(name="eks", path="bnk/eks")
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")

        mod_a = ProjectModule(
            project_id=project.id,
            module_library_id=lib_a.id,
            path_in_project=f"stack-{si.id}/bnk/vpc",
            status="destroyed",
            stack_instance_id=si.id,
        )
        mod_b = ProjectModule(
            project_id=project.id,
            module_library_id=lib_b.id,
            path_in_project=f"stack-{si.id}/bnk/eks",
            status="destroyed",
            stack_instance_id=si.id,
        )
        db.add_all([mod_a, mod_b])
        db.flush()
        si.deployed_modules = [mod_a.id, mod_b.id]
        db.commit()

        # First call — should finalize
        _run_terminal_detection_stack(mod_a, db)
        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        assert stack.status == "destroyed"

        # Second call (race simulation) — should be a safe no-op
        # Stack is no longer DESTROYING, so detection returns early
        _run_terminal_detection_stack(mod_b, db)

        db.expire_all()
        stack = db.query(StackInstance).filter_by(id=si.id).first()
        # Still destroyed, not double-errored
        assert stack.status == "destroyed"


# ---------------------------------------------------------------------------
# Terminal detection: project scope — parallel-leaf DAG correctness (F1 fix)
# ---------------------------------------------------------------------------

class TestTerminalDetectionProjectParallelLeaf:
    """_run_terminal_detection_project must NOT finalize prematurely when sibling
    leaves or undispatched roots are still live.

    Scenario: two independent leaves A and C both depend on shared root B.
    Destroy order: A and C first (leaves), then B (root).
    When A completes, B has no Task row yet (not dispatched); C is still destroying.
    The old Task-row-only scope would see only {A} → non_terminal empty → premature finalize.
    The fixed code queries ALL project modules and defers while C is destroying.
    """

    def test_parallel_leaf_does_not_finalize_while_sibling_destroying(
        self,
        db,
        make_project,
        make_module_library,
        make_project_module,
        make_task,
    ):
        """Leaf A completes; sibling leaf C still destroying; root B not dispatched.
        Terminal detection must DEFER — not finalize the project destroy."""
        from tasks._tofu_helpers import _run_terminal_detection_project

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")

        # Root B: has infra (applied), no Task row yet (not dispatched)
        mod_b = make_project_module(project=project, library_module=lib, status="applied",
                                    path_in_project="p1/root-b")
        # Leaf A: completed destroy (has Task row with run_handle)
        mod_a = make_project_module(project=project, library_module=lib, status="destroyed",
                                    path_in_project="p1/leaf-a")
        # Leaf C: still destroying (no Task row — dispatched but not finished yet,
        # or has a Task row in 'destroying' — either way the module status is "destroying")
        mod_c = make_project_module(project=project, library_module=lib, status="destroying",
                                    path_in_project="p1/leaf-c")

        run_handle = "deadbeef" * 4  # 32-char handle

        # Only A has a Task row for this run; B and C have none (B not dispatched)
        make_task(
            project=project,
            module=mod_a,
            task_type="destroy",
            status="completed",
            run_handle=run_handle,
        )
        db.commit()

        with patch(f"{_PT_MOD}._finalize_destroy") as mock_finalize, \
             patch(f"{_PT_MOD}._mark_entity_failed") as mock_fail:
            _run_terminal_detection_project(mod_a, db)

        # Must NOT have finalized — C is still destroying, B still has infra
        mock_finalize.assert_not_called()
        mock_fail.assert_not_called()

    def test_parallel_leaf_finalizes_when_all_modules_terminal(
        self,
        db,
        make_project,
        make_module_library,
        make_project_module,
        make_task,
    ):
        """All project modules in terminal destroy states → finalize called.

        A, B, C all destroyed. A's Task has the run_handle. Detection should finalize.
        """
        from tasks._tofu_helpers import _run_terminal_detection_project

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")

        mod_a = make_project_module(project=project, library_module=lib, status="destroyed",
                                    path_in_project="p2/leaf-a")
        mod_b = make_project_module(project=project, library_module=lib, status="destroyed",
                                    path_in_project="p2/root-b")
        mod_c = make_project_module(project=project, library_module=lib, status="destroyed",
                                    path_in_project="p2/leaf-c")

        run_handle = "cafebabe" * 4

        make_task(
            project=project,
            module=mod_a,
            task_type="destroy",
            status="completed",
            run_handle=run_handle,
        )
        db.commit()

        with patch(f"{_PT_MOD}._finalize_destroy") as mock_finalize, \
             patch(f"{_PT_MOD}._mark_entity_failed") as mock_fail:
            _run_terminal_detection_project(mod_a, db)

        mock_finalize.assert_called_once_with(db, "project", project.id)
        mock_fail.assert_not_called()

    def test_parallel_leaf_defers_while_root_still_has_infra(
        self,
        db,
        make_project,
        make_module_library,
        make_project_module,
        make_task,
    ):
        """Root B is still applied (not yet dispatched); leaf A destroyed.
        Detection must DEFER even though no module is actively 'destroying'.
        This is the exact premature-finalize scenario from reviewer finding F1.
        """
        from tasks._tofu_helpers import _run_terminal_detection_project

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")

        # Root B: has infra (applied), no Task row (not yet dispatched)
        mod_b = make_project_module(project=project, library_module=lib, status="applied",
                                    path_in_project="p3/root-b")
        # Leaf A: destroyed, has Task row
        mod_a = make_project_module(project=project, library_module=lib, status="destroyed",
                                    path_in_project="p3/leaf-a")

        run_handle = "a1b2c3d4" * 4

        make_task(
            project=project,
            module=mod_a,
            task_type="destroy",
            status="completed",
            run_handle=run_handle,
        )
        db.commit()

        with patch(f"{_PT_MOD}._finalize_destroy") as mock_finalize, \
             patch(f"{_PT_MOD}._mark_entity_failed") as mock_fail:
            _run_terminal_detection_project(mod_a, db)

        # Root B still has infra — MUST defer
        mock_finalize.assert_not_called()
        mock_fail.assert_not_called()

    def test_parallel_leaf_marks_failed_when_any_module_destroy_failed(
        self,
        db,
        make_project,
        make_module_library,
        make_project_module,
        make_task,
    ):
        """All modules terminal but one has destroy_failed → _mark_entity_failed."""
        from tasks._tofu_helpers import _run_terminal_detection_project

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")

        mod_a = make_project_module(project=project, library_module=lib, status="destroyed",
                                    path_in_project="p4/leaf-a")
        mod_b = make_project_module(project=project, library_module=lib, status="destroy_failed",
                                    path_in_project="p4/leaf-b")

        run_handle = "11223344" * 4

        make_task(
            project=project,
            module=mod_a,
            task_type="destroy",
            status="completed",
            run_handle=run_handle,
        )
        db.commit()

        with patch(f"{_PT_MOD}._finalize_destroy") as mock_finalize, \
             patch(f"{_PT_MOD}._mark_entity_failed") as mock_fail:
            _run_terminal_detection_project(mod_a, db)

        mock_fail.assert_called_once()
        args = mock_fail.call_args[0]
        assert args[1] == "project"
        assert args[2] == project.id
        assert "1 module(s) failed" in args[3]
        mock_finalize.assert_not_called()


# ---------------------------------------------------------------------------
# Issue #2: destroy_scope persisted in meta_data — project destroy not
# misclassified as stack-scope when blueprint modules have stack_instance_id
# ---------------------------------------------------------------------------

class TestDestroyScopeMetaData:
    """Verify that meta_data["destroy_scope"] = "project" is stamped on first-wave tasks
    and propagated to downstream tasks, ensuring blueprint modules (which have
    stack_instance_id) are treated as project-scope not stack-scope during project destroy.
    """

    def test_first_wave_stamps_destroy_scope_project(
        self, db, make_project, make_project_module, make_module_library
    ):
        """First-wave project destroy tasks must have meta_data={"destroy_scope":"project"}."""
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        mod = make_project_module(project=project, library_module=lib, status="applied")

        mock_result = MagicMock()
        mock_result.id = "celery-xyz"
        mock_sig = MagicMock()
        mock_sig.apply_async.return_value = mock_result

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", return_value=mock_sig):
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = []
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            svc._dispatch_first_destroy_wave(project.id, run_handle="handle123")

        db.expire_all()
        task = db.query(TaskModel).filter(
            TaskModel.module_id == mod.id,
            TaskModel.task_type == "destroy",
        ).first()
        assert task is not None
        assert task.meta_data is not None
        assert task.meta_data.get("destroy_scope") == "project"

    def test_blueprint_module_with_stack_instance_id_clears_barrier_in_project_destroy(
        self, db, make_project, make_module_library, make_project_module,
        make_stack_template, make_stack_instance,
    ):
        """In a project destroy, a blueprint module (has stack_instance_id) that is
        in-cluster and in destroy_failed state should clear the barrier for its infra
        dependency — i.e. fail-soft project-scope logic applies, not fail-closed stack-scope.

        Setup:
          - infra_mod (opentofu, applied): the dependency we want dispatched
          - bnk_mod (operator, destroy_failed, has stack_instance_id): blueprint module
          - bnk_mod.dependencies = [infra_mod.id]  → bnk_mod depends on infra_mod

        With the old stack_instance_id-based scope detection, the barrier for infra_mod
        would be evaluated in stack-scope (fail-closed), blocking dispatch.
        With the fix (meta_data["destroy_scope"]="project"), it is evaluated in
        project-scope (fail-soft for in-cluster), allowing infra_mod to be dispatched.
        """
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()

        # Infra module (opentofu — not in-cluster)
        lib_infra = make_module_library(
            name="eks-infra", path="bnk/eks",
            execution_engine="opentofu",
        )
        infra_mod = make_project_module(
            project=project, library_module=lib_infra, status="applied",
            path_in_project="infra/eks",
        )

        # Blueprint BNK module (operator — in-cluster), with stack_instance_id
        lib_bnk = make_module_library(
            name="bnk", path="bnk/bnk",
            execution_engine="operator",
        )
        t = make_stack_template()
        si = make_stack_instance(project=project, template=t, status="destroying")
        bnk_mod = make_project_module(
            project=project, library_module=lib_bnk, status="destroy_failed",
            path_in_project="stack-1/bnk",
            dependencies=[infra_mod.id],
            stack_instance_id=si.id,
        )
        db.commit()

        # Simulate: bnk_mod had a destroy task with destroy_scope=project
        destroy_task = TaskModel(
            task_type="destroy",
            status="failed",
            project_id=project.id,
            module_id=bnk_mod.id,
            run_handle="test-run-abc",
            meta_data={"destroy_scope": "project"},
            created_at=datetime.now(UTC),
        )
        db.add(destroy_task)
        db.commit()

        dispatched = []

        def capture_dispatch(task_id, module):
            dispatched.append(module.id)
            mock = MagicMock()
            mock.apply_async.return_value.id = f"celery-{module.id}"
            return mock

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature",
                   side_effect=capture_dispatch), \
             patch("tasks._tofu_helpers._run_terminal_detection"):
            mock_graph = MagicMock()
            # infra_mod's only dependent is bnk_mod (the in-cluster blueprint module)
            mock_graph.get_reverse_dependencies.return_value = [bnk_mod]
            mock_gs.return_value = mock_graph

            _trigger_next_destroy_module(bnk_mod, db)

        # infra_mod MUST be dispatched: bnk_mod is in-cluster + destroy_failed,
        # and we are in project-scope → fail-soft clears the barrier.
        assert infra_mod.id in dispatched, (
            "infra_mod should be dispatched: in-cluster destroy_failed clears "
            "barrier in project-scope (fail-soft)"
        )

    def test_downstream_task_inherits_destroy_scope(
        self, db, make_project, make_project_module, make_module_library
    ):
        """_trigger_next_destroy_module must stamp destroy_scope onto the downstream task."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        project = make_project()
        lib_root = make_module_library(name="vpc", path="bnk/vpc")
        lib_leaf = make_module_library(name="eks", path="bnk/eks")

        root_mod = make_project_module(project=project, library_module=lib_root, status="applied")
        leaf_mod = make_project_module(
            project=project, library_module=lib_leaf, status="destroyed",
            dependencies=[root_mod.id],
        )

        # Predecessor task with destroy_scope=project
        pred_task = TaskModel(
            task_type="destroy",
            status="completed",
            project_id=project.id,
            module_id=leaf_mod.id,
            run_handle="handle-abc",
            meta_data={"destroy_scope": "project"},
            created_at=datetime.now(UTC),
        )
        db.add(pred_task)
        db.commit()

        mock_result = MagicMock()
        mock_result.id = "celery-root"
        mock_sig = MagicMock()
        mock_sig.apply_async.return_value = mock_result

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature",
                   return_value=mock_sig), \
             patch("tasks._tofu_helpers._run_terminal_detection"):
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = [leaf_mod]
            mock_gs.return_value = mock_graph

            _trigger_next_destroy_module(leaf_mod, db)

        db.expire_all()
        downstream = db.query(TaskModel).filter(
            TaskModel.module_id == root_mod.id,
            TaskModel.task_type == "destroy",
        ).first()
        assert downstream is not None
        assert downstream.meta_data is not None
        assert downstream.meta_data.get("destroy_scope") == "project"


# ---------------------------------------------------------------------------
# Issue #3: force_destroy correctness — all-in-cluster and mixed graphs
# ---------------------------------------------------------------------------

class TestForceDestroyBehavior:
    """force_destroy: pre-marks all in-cluster modules before wave dispatch.
    Tests cover all-in-cluster (immediate finalization) and mixed graphs.
    """

    def test_force_destroy_all_in_cluster_finalizes_immediately(
        self, db, make_project, make_project_module, make_module_library
    ):
        """When all modules are in-cluster and force_destroy=True, no infra tasks
        are dispatched and the project is finalized immediately (not left with zero rows)."""
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib = make_module_library(name="bnk", path="bnk/bnk", execution_engine="operator")
        mod_a = make_project_module(
            project=project, library_module=lib, status="applied", path_in_project="p/bnk-a"
        )
        mod_b = make_project_module(
            project=project, library_module=lib, status="applied", path_in_project="p/bnk-b"
        )
        db.commit()

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature") as mock_dispatch, \
             patch("tasks.parallel_tasks._finalize_destroy") as mock_finalize:
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = []
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            result = svc._dispatch_first_destroy_wave(
                project.id, run_handle="handle-force", force_destroy=True
            )

        # No infra tasks dispatched
        mock_dispatch.assert_not_called()
        assert result is None

        # All in-cluster modules marked destroyed
        db.expire_all()
        for mod in [mod_a, mod_b]:
            db.refresh(mod)
            assert mod.status == "destroyed", f"Module {mod.id} should be destroyed"

        # Finalize was called immediately since no infra tasks remain
        mock_finalize.assert_called_once_with(db, "project", project.id)

    def test_force_destroy_mixed_graph_dispatches_infra_skips_in_cluster(
        self, db, make_project, make_project_module, make_module_library
    ):
        """Mixed graph: in-cluster module depends on infra module.
        force_destroy=True pre-marks in-cluster as destroyed, then infra (now a leaf)
        is dispatched normally.  No immediate finalization (infra task was dispatched).
        """
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib_infra = make_module_library(
            name="eks", path="bnk/eks", execution_engine="opentofu"
        )
        lib_bnk = make_module_library(
            name="bnk", path="bnk/bnk", execution_engine="operator"
        )
        infra_mod = make_project_module(
            project=project, library_module=lib_infra, status="applied",
            path_in_project="p/eks",
        )
        bnk_mod = make_project_module(
            project=project, library_module=lib_bnk, status="applied",
            path_in_project="p/bnk",
            dependencies=[infra_mod.id],
        )
        db.commit()

        mock_result = MagicMock()
        mock_result.id = "celery-infra"
        mock_sig = MagicMock()
        mock_sig.apply_async.return_value = mock_result

        dispatched_ids = []

        def capture_dispatch(task_id, module):
            dispatched_ids.append(module.id)
            return mock_sig

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature",
                   side_effect=capture_dispatch), \
             patch("tasks.parallel_tasks._finalize_destroy") as mock_finalize:
            mock_graph = MagicMock()

            def get_reverse_deps(module_id, project_id):
                # bnk_mod depends on infra_mod, so infra_mod has bnk_mod as dependent
                if module_id == infra_mod.id:
                    return [bnk_mod]
                return []

            mock_graph.get_reverse_dependencies.side_effect = get_reverse_deps
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            result = svc._dispatch_first_destroy_wave(
                project.id, run_handle="handle-mixed", force_destroy=True
            )

        # bnk_mod (in-cluster) must be pre-marked destroyed
        db.expire_all()
        db.refresh(bnk_mod)
        assert bnk_mod.status == "destroyed"

        # infra_mod must be dispatched (it's now a leaf after bnk_mod pre-marked)
        assert infra_mod.id in dispatched_ids

        # No immediate finalization — infra task was dispatched
        mock_finalize.assert_not_called()
        assert result == "celery-infra"

    def test_force_destroy_in_cluster_not_first_wave_but_still_pre_marked(
        self, db, make_project, make_project_module, make_module_library
    ):
        """In-cluster module that is NOT a first-wave leaf (has deployed non-in-cluster
        dependents) is still pre-marked destroyed by force_destroy.  This tests that the
        pre-pass marks ALL in-cluster modules, not just first-wave ones.
        """
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib_infra = make_module_library(
            name="eks", path="bnk/eks", execution_engine="opentofu"
        )
        lib_bnk = make_module_library(
            name="bnk", path="bnk/bnk", execution_engine="operator"
        )
        # bnk_mod depends on infra_mod → in normal destroy, bnk_mod is leaf, infra_mod is root.
        # With force_destroy: bnk_mod (in-cluster) is pre-marked destroyed.
        # infra_mod (now a leaf with no deployed dependents) is dispatched.
        infra_mod = make_project_module(
            project=project, library_module=lib_infra, status="applied",
            path_in_project="p2/eks",
        )
        bnk_mod = make_project_module(
            project=project, library_module=lib_bnk, status="applied",
            path_in_project="p2/bnk",
            dependencies=[infra_mod.id],  # bnk depends on infra
        )
        db.commit()

        mock_result = MagicMock()
        mock_result.id = "celery-infra-2"
        mock_sig = MagicMock()
        mock_sig.apply_async.return_value = mock_result

        dispatched_ids = []

        def capture_dispatch(task_id, module):
            dispatched_ids.append(module.id)
            return mock_sig

        with patch("services.parallel_execution_service.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature",
                   side_effect=capture_dispatch), \
             patch("tasks.parallel_tasks._finalize_destroy"):
            mock_graph = MagicMock()

            def get_reverse_deps(module_id, project_id):
                if module_id == infra_mod.id:
                    return [bnk_mod]
                return []

            mock_graph.get_reverse_dependencies.side_effect = get_reverse_deps
            mock_gs.return_value = mock_graph

            svc = ParallelExecutionService(db)
            svc._dispatch_first_destroy_wave(
                project.id, run_handle="handle-mixed2", force_destroy=True
            )

        # bnk_mod pre-marked (it was in-cluster regardless of wave position)
        db.expire_all()
        db.refresh(bnk_mod)
        assert bnk_mod.status == "destroyed", "in-cluster module must be pre-marked"

        # infra_mod dispatched (became a leaf after bnk pre-marked)
        assert infra_mod.id in dispatched_ids


# ---------------------------------------------------------------------------
# Module-scope destroy — issue #525
# ---------------------------------------------------------------------------

class TestModuleScopeDestroyDoesNotCascade:
    """A single-module destroy must not tear down the modules it depends on.

    Reported as issue #525: destroying `bnk-install` in a two-module blueprint
    cascaded into `cluster-create` and deleted the ROKS cluster (plus its VPC and
    Transit Gateway) that BNK had been installed on. The chain is correct for a
    project/stack teardown and wrong for one named module.
    """

    def _two_module_stack(self, db, make_project, make_project_module, make_module_library,
                          make_stack_template, make_stack_instance):
        """cluster-create <- bnk-install, both applied, inside a stack instance.

        The stack matters: with no destroy_scope stamp the chain falls back to the
        stack_instance_id heuristic, which is the path that produced the cascade.
        """
        project = make_project()
        lib_cluster = make_module_library(name="cluster-create", path="bnk/cluster-create")
        lib_bnk = make_module_library(name="bnk-install", path="bnk/bnk-install")
        template = make_stack_template()
        stack = make_stack_instance(project=project, template=template, status="deployed")

        cluster_mod = make_project_module(
            project=project, library_module=lib_cluster, status="applied",
            stack_instance_id=stack.id,
        )
        bnk_mod = make_project_module(
            project=project, library_module=lib_bnk, status="destroyed",
            dependencies=[cluster_mod.id], stack_instance_id=stack.id,
        )
        stack.deployed_modules = [cluster_mod.id, bnk_mod.id]
        db.flush()
        return project, cluster_mod, bnk_mod

    def _run_trigger(self, db, completed_mod, dependency_mod):
        """Run the post-destroy trigger, returning the module ids it dispatched."""
        from tasks._tofu_helpers import _trigger_next_destroy_module

        dispatched = []

        def capture_dispatch(task_id, module):
            dispatched.append(module.id)
            mock = MagicMock()
            mock.apply_async.return_value.id = f"celery-{module.id}"
            return mock

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature",
                   side_effect=capture_dispatch), \
             patch("tasks._tofu_helpers._run_terminal_detection"):
            mock_graph = MagicMock()
            mock_graph.get_reverse_dependencies.return_value = [completed_mod]
            mock_gs.return_value = mock_graph

            _trigger_next_destroy_module(completed_mod, db)

        return dispatched

    def test_module_scope_destroy_does_not_queue_its_dependency(
        self, db, make_project, make_project_module, make_module_library,
        make_stack_template, make_stack_instance, make_task,
    ):
        """destroy_scope=module stops the chain — the dependency survives."""
        project, cluster_mod, bnk_mod = self._two_module_stack(
            db, make_project, make_project_module, make_module_library,
            make_stack_template, make_stack_instance,
        )

        # This is what submit_destroy stamps for a single-module destroy.
        make_task(
            project=project, module=bnk_mod, task_type="destroy", status="completed",
            meta_data={"destroy_scope": "module"},
        )

        dispatched = self._run_trigger(db, bnk_mod, cluster_mod)

        assert cluster_mod.id not in dispatched, (
            "destroying bnk-install cascaded into cluster-create — the ROKS cluster "
            "the user asked to keep would be deleted (issue #525)"
        )
        assert dispatched == []

    def test_project_scope_destroy_still_queues_the_dependency(
        self, db, make_project, make_project_module, make_module_library,
        make_stack_template, make_stack_instance, make_task,
    ):
        """The guard is scope-specific: a real teardown must still walk the DAG.

        Without this, 'nothing was dispatched' above would also pass if the chain
        were broken outright.
        """
        project, cluster_mod, bnk_mod = self._two_module_stack(
            db, make_project, make_project_module, make_module_library,
            make_stack_template, make_stack_instance,
        )

        make_task(
            project=project, module=bnk_mod, task_type="destroy", status="completed",
            meta_data={"destroy_scope": "project"},
        )

        dispatched = self._run_trigger(db, bnk_mod, cluster_mod)

        assert cluster_mod.id in dispatched, (
            "project-scope destroy must still tear down dependencies"
        )

    def test_submit_destroy_stamps_module_scope(
        self, db, make_project, make_project_module, make_module_library,
    ):
        """submit_destroy is what puts the scope on the Task row.

        Ties the service to the trigger guard above — if this stamp regresses,
        the cascade comes back even though the guard still works.
        """
        from models import Task as TaskModel
        from services.project_module_service import ProjectModuleService

        project = make_project()
        lib = make_module_library(name="bnk-install", path="bnk/bnk-install")
        module = make_project_module(project=project, library_module=lib, status="applied")

        with patch("services.execution.task_dispatch.dispatch_destroy") as mock_dispatch, \
             patch.object(ProjectModuleService, "_create_snapshot"):
            mock_dispatch.return_value = MagicMock(id="celery-destroy-1")
            ProjectModuleService(db).submit_destroy(module.id, triggered_by="tester")

        task = (
            db.query(TaskModel)
            .filter(TaskModel.module_id == module.id, TaskModel.task_type == "destroy")
            .order_by(TaskModel.id.desc())
            .first()
        )
        assert task is not None
        assert (task.meta_data or {}).get("destroy_scope") == "module"


# ---------------------------------------------------------------------------
# Disabled modules are not dispatched by the deploy chain — issue #527
# ---------------------------------------------------------------------------

class TestDisabledModuleNotDispatchedByChain:
    """The dependency chain must skip a disabled module.

    This is the exact path from issue #527: module 166 was set `enabled: false`,
    module 165 was applied on its own, and the moment 165 completed the chain
    dispatched 166 anyway.
    """

    def test_trigger_next_stack_module_skips_disabled(
        self, db, make_project, make_project_module, make_module_library,
        make_stack_template, make_stack_instance,
    ):
        from tasks._tofu_helpers import _trigger_next_stack_module

        project = make_project()
        lib_a = make_module_library(name="cluster-create", path="bnk/cluster-create")
        lib_b = make_module_library(name="bnk-install", path="bnk/bnk-install")
        template = make_stack_template()
        stack = make_stack_instance(project=project, template=template, status="deploying")

        done = make_project_module(
            project=project, library_module=lib_a, status="applied",
            stack_instance_id=stack.id,
        )
        disabled = make_project_module(
            project=project, library_module=lib_b, status="initialized",
            dependencies=[done.id], stack_instance_id=stack.id, enabled=False,
        )
        stack.deployed_modules = [done.id, disabled.id]
        db.flush()

        with patch("services.execution.task_dispatch.dispatch_apply") as mock_apply, \
             patch("services.execution.task_dispatch.dispatch_init") as mock_init:
            _trigger_next_stack_module(stack, done, db)

        mock_apply.assert_not_called()
        mock_init.assert_not_called()
        db.refresh(disabled)
        assert disabled.status == "initialized", (
            f"disabled module was dispatched anyway (status={disabled.status}) — "
            "there is no way to deploy only part of a blueprint (issue #527)"
        )

    def test_trigger_next_stack_module_still_dispatches_enabled(
        self, db, make_project, make_project_module, make_module_library,
        make_stack_template, make_stack_instance,
    ):
        """Contrast: the same wiring with enabled=True does dispatch.

        Proves the skip above comes from the enabled flag, not from the chain
        being inert in this fixture.
        """
        from tasks._tofu_helpers import _trigger_next_stack_module

        project = make_project()
        lib_a = make_module_library(name="cluster-create", path="bnk/cluster-create")
        lib_b = make_module_library(name="bnk-install", path="bnk/bnk-install")
        template = make_stack_template()
        stack = make_stack_instance(project=project, template=template, status="deploying")

        done = make_project_module(
            project=project, library_module=lib_a, status="applied",
            stack_instance_id=stack.id,
        )
        enabled = make_project_module(
            project=project, library_module=lib_b, status="initialized",
            dependencies=[done.id], stack_instance_id=stack.id, enabled=True,
        )
        stack.deployed_modules = [done.id, enabled.id]
        db.flush()

        with patch("services.execution.task_dispatch.dispatch_apply") as mock_apply:
            mock_apply.return_value = MagicMock(id="celery-apply-1")
            _trigger_next_stack_module(stack, done, db)

        assert mock_apply.called, "enabled module should still be dispatched"

    def test_first_wave_skips_disabled(
        self, db, make_project, make_project_module, make_module_library,
    ):
        """The project-scope wave (blueprint-imported projects) skips it too."""
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib = make_module_library(name="bnk-install", path="bnk/bnk-install")
        disabled = make_project_module(
            project=project, library_module=lib, status="initialized", enabled=False,
        )
        db.flush()

        with patch("services.execution.task_dispatch.dispatch_apply") as mock_apply, \
             patch("services.execution.task_dispatch.dispatch_init") as mock_init, \
             patch("services.workspace_manager.WorkspaceManager"):
            ParallelExecutionService(db)._dispatch_first_wave(project.id, run_handle="h1")

        mock_apply.assert_not_called()
        mock_init.assert_not_called()
        db.refresh(disabled)
        assert disabled.status == "initialized"


class TestDestroyWaveIgnoresEnabled:
    """A project teardown must destroy DISABLED modules too.

    The deploy paths gained an `enabled` gate (issue #527). The destroy wave
    deliberately did NOT, and that asymmetry is easy to "tidy up" later into a
    data-loss bug: a disabled module can still hold live infrastructure, so
    skipping it in a project destroy strands whatever it already built, with no
    UI affordance left to reach it.

    Pinning the invariant so the omission reads as intentional.
    """

    def test_disabled_module_is_still_dispatched_by_the_destroy_wave(
        self, db, make_project, make_project_module, make_module_library,
    ):
        from services.parallel_execution_service import ParallelExecutionService

        project = make_project()
        lib = make_module_library(name="vpc", path="bnk/vpc")
        disabled = make_project_module(
            project=project, library_module=lib, status="applied", enabled=False,
        )
        db.flush()

        dispatched = []

        def capture(task_id, module):
            dispatched.append(module.id)
            mock = MagicMock()
            mock.apply_async.return_value.id = f"celery-{module.id}"
            return mock

        with patch("tasks._tofu_helpers.DependencyGraphService") as mock_gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature", side_effect=capture):
            graph = MagicMock()
            graph.get_reverse_dependencies.return_value = []
            mock_gs.return_value = graph
            ParallelExecutionService(db)._dispatch_first_destroy_wave(
                project.id, run_handle="h-destroy", force_destroy=True
            )

        assert disabled.id in dispatched, (
            "a disabled module was skipped by the project destroy wave — its "
            "infrastructure would be stranded with no way to reach it"
        )
