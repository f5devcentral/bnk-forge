"""
Tests for deploy_module init→apply chain (issue #348)
and auto_approve propagation (issue #349).

Covers:
  (a) deploy_module on not_initialized dispatches init with auto_apply=True
      (not a bare apply, not a BadRequestError)
  (b) deploy_module on initialized module dispatches apply directly
  (c) deploy_module rejects already-deploying modules (APPLYING/INITIALIZING/PLANNING)
  (d) auto_approve=True propagates from route → submit_apply → dispatch_apply →
      run_opentofu_apply with force_new_plan=True
  (e) auto_approve=False leaves force_new_plan as-is (default False)
  (f) run_opentofu_init returns apply_queued=True only when deps ready, False otherwise
      (regression test for bonnyr-f5 review comment: silent stall when deps not ready)
"""

import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup (mirrors root conftest pattern)
# ---------------------------------------------------------------------------
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)


# ---------------------------------------------------------------------------
# Helpers — minimal module stub (no DB needed for unit-style tests)
# ---------------------------------------------------------------------------

def _make_module_stub(status: str = "not_initialized", module_id: int = 42):
    """Return a minimal object that looks like a ProjectModule."""
    mod = MagicMock()
    mod.id = module_id
    mod.status = status
    mod.project_id = 1
    # library_module — no explicit execution_engine → OpenTofu dispatch
    lib = MagicMock()
    lib.execution_engine = None
    lib.engine_type = None
    lib.source_kind = "git"
    lib.deploy_model = None
    lib.module_source_kind = "git"
    mod.library_module = lib
    mod.path_in_project = "modules/test"
    return mod


# ---------------------------------------------------------------------------
# Task 1: deploy_module chains init→apply for not_initialized
# ---------------------------------------------------------------------------

class TestDeployModuleChain:
    """deploy_module drives the full init→apply chain."""

    def _make_service_with_module(self, module_stub):
        """Return a ProjectModuleService whose get_module returns module_stub."""
        from services.project_module_service import ProjectModuleService

        svc = MagicMock(spec=ProjectModuleService)
        svc.get_module.return_value = module_stub
        svc.deploy_module = ProjectModuleService.deploy_module.__get__(svc, ProjectModuleService)
        return svc

    @patch("services.project_module_service.ProjectModuleService.get_module")
    @patch("services.project_module_service.ProjectModuleService.create_task")
    @patch("services.execution.task_dispatch.dispatch_init")
    @patch("services.module_state.transition_module_status")
    def test_deploy_module_not_initialized_dispatches_init_with_auto_apply(
        self, mock_transition, mock_dispatch_init, mock_create_task, mock_get_module
    ):
        """not_initialized module → dispatch_init called with auto_apply=True, not dispatch_apply."""
        from services.project_module_service import ProjectModuleService

        mod = _make_module_stub(status="not_initialized")
        mock_get_module.return_value = mod

        fake_task = MagicMock()
        fake_task.id = 10
        mock_create_task.return_value = fake_task

        celery_result = MagicMock()
        celery_result.id = "celery-init-abc"
        mock_dispatch_init.return_value = celery_result

        svc = ProjectModuleService.__new__(ProjectModuleService)
        svc.db = MagicMock()

        with patch("services.execution.task_dispatch.dispatch_apply") as mock_dispatch_apply:
            result = ProjectModuleService.deploy_module(svc, module_id=mod.id, triggered_by="testuser")

        # dispatch_init must have been called with auto_apply=True
        mock_dispatch_init.assert_called_once_with(fake_task.id, mod, auto_apply=True)
        # dispatch_apply must NOT have been called
        mock_dispatch_apply.assert_not_called()

        # Task type must be "init"
        mock_create_task.assert_called_once_with("init", mod, "testuser")

        # Return shape preserved
        assert result["task_id"] == 10
        assert result["celery_task_id"] == "celery-init-abc"
        assert result["module_id"] == mod.id
        assert result["status"] == "queued"
        assert "started_at" in result
        assert "message" in result

    @patch("services.project_module_service.ProjectModuleService.get_module")
    @patch("services.project_module_service.ProjectModuleService.create_task")
    @patch("services.execution.task_dispatch.dispatch_apply")
    @patch("services.module_state.transition_module_status")
    def test_deploy_module_initialized_dispatches_apply_directly(
        self, mock_transition, mock_dispatch_apply, mock_create_task, mock_get_module
    ):
        """Initialized module → dispatch_apply called, dispatch_init not called."""
        from services.project_module_service import ProjectModuleService

        mod = _make_module_stub(status="initialized")
        mock_get_module.return_value = mod

        fake_task = MagicMock()
        fake_task.id = 11
        mock_create_task.return_value = fake_task

        celery_result = MagicMock()
        celery_result.id = "celery-apply-xyz"
        mock_dispatch_apply.return_value = celery_result

        svc = ProjectModuleService.__new__(ProjectModuleService)
        svc.db = MagicMock()

        with patch("services.execution.task_dispatch.dispatch_init") as mock_dispatch_init:
            result = ProjectModuleService.deploy_module(svc, module_id=mod.id)

        mock_dispatch_apply.assert_called_once_with(fake_task.id, mod)
        mock_dispatch_init.assert_not_called()
        mock_create_task.assert_called_once_with("apply", mod, "user")
        assert result["celery_task_id"] == "celery-apply-xyz"

    @pytest.mark.parametrize("busy_status", ["applying", "initializing", "planning"])
    @patch("services.project_module_service.ProjectModuleService.get_module")
    def test_deploy_module_rejects_already_deploying(self, mock_get_module, busy_status):
        """Module in APPLYING/INITIALIZING/PLANNING raises BadRequestError."""
        from core.errors import BadRequestError
        from services.project_module_service import ProjectModuleService

        mod = _make_module_stub(status=busy_status)
        mock_get_module.return_value = mod

        svc = ProjectModuleService.__new__(ProjectModuleService)
        svc.db = MagicMock()

        with pytest.raises(BadRequestError, match="already deploying"):
            ProjectModuleService.deploy_module(svc, module_id=mod.id)

    @patch("services.project_module_service.ProjectModuleService.get_module")
    @patch("services.project_module_service.ProjectModuleService.create_task")
    @patch("services.execution.task_dispatch.dispatch_init")
    @patch("services.module_state.transition_module_status")
    def test_deploy_module_does_not_raise_for_not_initialized(
        self, mock_transition, mock_dispatch_init, mock_create_task, mock_get_module
    ):
        """No BadRequestError raised for not_initialized — auto-init instead."""
        from core.errors import BadRequestError
        from services.project_module_service import ProjectModuleService

        mod = _make_module_stub(status="not_initialized")
        mock_get_module.return_value = mod
        mock_create_task.return_value = MagicMock(id=99)
        mock_dispatch_init.return_value = MagicMock(id="celery-123")

        svc = ProjectModuleService.__new__(ProjectModuleService)
        svc.db = MagicMock()

        # Must not raise
        result = ProjectModuleService.deploy_module(svc, module_id=mod.id)
        assert result["status"] == "queued"


# ---------------------------------------------------------------------------
# Task 2: auto_approve propagation
# ---------------------------------------------------------------------------

class TestAutoApproveFlow:
    """auto_approve must NOT discard a saved plan the user already reviewed (review #362 blocker)."""

    def test_dispatch_apply_auto_approve_does_not_force_new_plan(self):
        """dispatch_apply with auto_approve=True must NOT force a new plan.

        Regression guard: auto_approve previously forced force_new_plan=True,
        which discarded the plan the user just reviewed and approved before
        running a fresh, unreviewed plan. auto_approve only means "don't prompt",
        never "replan".
        """
        from services.execution.task_dispatch import dispatch_apply

        mod = _make_module_stub(status="initialized")

        with patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-apply-1")
            dispatch_apply(task_id=1, module=mod, auto_approve=True)

        mock_task.delay.assert_called_once_with(1, mod.id, force_new_plan=False)

    def test_dispatch_apply_auto_approve_false_does_not_force_new_plan(self):
        """dispatch_apply with auto_approve=False (default) does not force_new_plan."""
        from services.execution.task_dispatch import dispatch_apply

        mod = _make_module_stub(status="initialized")

        with patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-apply-2")
            dispatch_apply(task_id=2, module=mod, auto_approve=False)

        mock_task.delay.assert_called_once_with(2, mod.id, force_new_plan=False)

    def test_dispatch_apply_explicit_force_new_plan_still_honored(self):
        """Explicit force_new_plan=True (the genuine replan path) is unaffected by auto_approve."""
        from services.execution.task_dispatch import dispatch_apply

        mod = _make_module_stub(status="initialized")

        with patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-apply-3")
            dispatch_apply(task_id=3, module=mod, force_new_plan=True, auto_approve=False)

        mock_task.delay.assert_called_once_with(3, mod.id, force_new_plan=True)

    def test_dispatch_apply_auto_approve_true_and_force_new_plan_true(self):
        """force_new_plan=True combined with auto_approve=True still replans (force_new_plan drives it, not auto_approve)."""
        from services.execution.task_dispatch import dispatch_apply

        mod = _make_module_stub(status="initialized")

        with patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_task:
            mock_task.delay.return_value = MagicMock(id="celery-apply-4")
            dispatch_apply(task_id=4, module=mod, force_new_plan=True, auto_approve=True)

        mock_task.delay.assert_called_once_with(4, mod.id, force_new_plan=True)

    def test_run_opentofu_apply_uses_saved_plan_when_force_new_plan_false(self):
        """End-to-end: with a valid saved plan and force_new_plan=False (auto_approve path),
        run_opentofu_apply applies the saved plan.out directly instead of regenerating it.
        """
        import contextlib

        module = MagicMock()
        module.id = 77
        module.project_id = 1
        module.plan_serial = 3
        module.vars_hash = "abc123"
        project = MagicMock()
        module.project = project

        db = MagicMock()
        task = MagicMock()
        task.id = 200

        workspace = MagicMock()
        workspace.has_saved_plan.return_value = True
        workspace.plan_is_valid.return_value = (True, None)
        workspace.is_initialized.return_value = True
        workspace.needs_reinit.return_value = (False, "")

        @contextlib.contextmanager
        def _fake_lock(*a, **kw):
            yield MagicMock()

        with (
            patch("tasks.opentofu_tasks.get_db_context") as mock_db_ctx,
            patch("tasks.opentofu_tasks.fetch_task_or_raise", return_value=task),
            patch("tasks.opentofu_tasks.OpenTofuRuntime") as MockRuntime,
            patch("services.workspace_manager.WorkspaceManager", return_value=workspace),
            patch("tasks.opentofu_tasks.module_lock", _fake_lock),
            patch("tasks.opentofu_tasks.check_dependencies", return_value=(True, [])),
            patch("tasks.opentofu_tasks.get_cloud_credentials_env", return_value={}),
            patch("tasks.opentofu_tasks.set_locked_module_fields"),
        ):
            mock_db_ctx.return_value.__enter__ = lambda s, *a: db
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
            db.query.return_value.filter.return_value.first.return_value = module
            MockRuntime.return_value.prepare_persistent_workspace.return_value = "/tmp/fake-work"
            MockRuntime.return_value.reconcile_known_existing_resources.return_value = 0
            MockRuntime.return_value.run_apply.return_value = (0, "apply ok", {})

            from tasks.opentofu_tasks import run_opentofu_apply

            run_opentofu_apply.__wrapped__(
                MagicMock(), task.id, module.id, force_new_plan=False
            )

        # Saved plan must be used as-is: no re-plan before apply — the reviewed
        # plan.out is applied directly instead of a freshly generated one.
        MockRuntime.return_value.run_plan.assert_not_called()
        assert task.command == "tofu apply plan.out"

    @patch("services.project_module_service.ProjectModuleService.get_module")
    @patch("services.project_module_service.ProjectModuleService.create_task")
    @patch("services.project_module_service.ProjectModuleService._validate_for_operation")
    @patch("services.project_module_service.ProjectModuleService.check_module_dependencies")
    @patch("services.project_module_service.ProjectModuleService._create_snapshot")
    @patch("services.project_module_service.ProjectModuleService._commit_before_dispatch")
    @patch("services.project_module_service.ProjectModuleService._record_celery_id")
    @patch("services.execution.task_dispatch.dispatch_apply")
    @patch("services.module_state.transition_module_status")
    def test_submit_apply_passes_auto_approve_to_dispatch(
        self,
        mock_transition,
        mock_dispatch_apply,
        mock_record,
        mock_commit,
        mock_snapshot,
        mock_check_deps,
        mock_validate,
        mock_create_task,
        mock_get_module,
    ):
        """submit_apply(auto_approve=True) calls dispatch_apply with auto_approve=True."""
        from services.project_module_service import ProjectModuleService

        mod = _make_module_stub(status="planned")
        mock_get_module.return_value = mod
        mock_validate.return_value = {"valid": True, "errors": [], "warnings": []}
        mock_check_deps.return_value = (True, [])
        fake_task = MagicMock()
        fake_task.id = 20
        mock_create_task.return_value = fake_task
        celery_result = MagicMock(id="celery-apply-submit")
        mock_dispatch_apply.return_value = celery_result
        mock_record.return_value = {"task_id": 20, "celery_task_id": "celery-apply-submit"}

        # Need a real project mock for validation path
        project = MagicMock()
        mod.project = project

        svc = ProjectModuleService.__new__(ProjectModuleService)
        svc.db = MagicMock()

        ProjectModuleService.submit_apply(svc, module_id=mod.id, auto_approve=True)

        mock_dispatch_apply.assert_called_once_with(fake_task.id, mod, auto_approve=True)

    @patch("services.project_module_service.ProjectModuleService.get_module")
    @patch("services.project_module_service.ProjectModuleService.create_task")
    @patch("services.project_module_service.ProjectModuleService._validate_for_operation")
    @patch("services.project_module_service.ProjectModuleService.check_module_dependencies")
    @patch("services.project_module_service.ProjectModuleService._create_snapshot")
    @patch("services.project_module_service.ProjectModuleService._commit_before_dispatch")
    @patch("services.project_module_service.ProjectModuleService._record_celery_id")
    @patch("services.execution.task_dispatch.dispatch_apply")
    @patch("services.module_state.transition_module_status")
    def test_submit_apply_default_auto_approve_false(
        self,
        mock_transition,
        mock_dispatch_apply,
        mock_record,
        mock_commit,
        mock_snapshot,
        mock_check_deps,
        mock_validate,
        mock_create_task,
        mock_get_module,
    ):
        """submit_apply without auto_approve defaults to False (backward-compatible)."""
        from services.project_module_service import ProjectModuleService

        mod = _make_module_stub(status="planned")
        mock_get_module.return_value = mod
        mock_validate.return_value = {"valid": True, "errors": [], "warnings": []}
        mock_check_deps.return_value = (True, [])
        fake_task = MagicMock()
        fake_task.id = 21
        mock_create_task.return_value = fake_task
        celery_result = MagicMock(id="celery-apply-default")
        mock_dispatch_apply.return_value = celery_result
        mock_record.return_value = {"task_id": 21, "celery_task_id": "celery-apply-default"}

        project = MagicMock()
        mod.project = project

        svc = ProjectModuleService.__new__(ProjectModuleService)
        svc.db = MagicMock()

        ProjectModuleService.submit_apply(svc, module_id=mod.id)

        mock_dispatch_apply.assert_called_once_with(fake_task.id, mod, auto_approve=False)


# ---------------------------------------------------------------------------
# Task 3: run_opentofu_init surfaces apply_queued state (regression #bonnyr-f5)
#
# When auto_apply=True but dependencies are not satisfied, the init task must
# return apply_queued=False so callers can detect the stall — not silently
# return {"success": True} with an implicit promise that apply will follow.
# ---------------------------------------------------------------------------

class TestAutoApplyQueuedSurfacing:
    """run_opentofu_init returns apply_queued reflecting whether apply was queued."""

    def _make_init_harness(self, already_initialized: bool, can_exec: bool):
        """
        Build a minimal patch harness for run_opentofu_init.

        Returns (module, db_context) with the workspace and lock mocks wired.
        The harness exercises the 'already initialized, skip init' fast-path so
        we don't need a real tofu binary.
        """
        import contextlib

        module = MagicMock()
        module.id = 55
        module.project_id = 1
        module.stack_instance_id = None
        module.last_init_at = "2025-01-01T00:00:00"
        project = MagicMock()
        module.project = project

        db = MagicMock()

        task = MagicMock()
        task.id = 100
        task.started_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)

        workspace = MagicMock()
        workspace.is_initialized.return_value = already_initialized
        workspace.needs_reinit.return_value = (False, "")
        workspace.is_blueprint_eligible.return_value = False  # skip blueprint cache path

        @contextlib.contextmanager
        def _fake_lock(*a, **kw):
            yield MagicMock()

        return module, db, task, workspace, _fake_lock, can_exec

    def test_apply_queued_true_when_deps_ready(self):
        """apply_queued=True when auto_apply=True and dependencies are satisfied."""
        module, db, task, workspace, fake_lock, _ = self._make_init_harness(
            already_initialized=True, can_exec=True
        )

        apply_celery = MagicMock()
        apply_celery.id = "celery-apply-ok"

        with (
            patch("tasks.opentofu_tasks.get_db_context") as mock_db_ctx,
            patch("tasks.opentofu_tasks.fetch_task_or_raise", return_value=task),
            patch("tasks.opentofu_tasks.OpenTofuRuntime") as MockRuntime,
            patch("services.workspace_manager.WorkspaceManager", return_value=workspace),
            patch("tasks.opentofu_tasks.module_lock", fake_lock),
            patch("tasks.opentofu_tasks.check_dependencies", return_value=(True, [])),
            patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_apply_task,
            patch("tasks.opentofu_tasks.set_locked_module_fields"),
            patch("tasks.opentofu_tasks.create_deployment_record"),
        ):
            mock_db_ctx.return_value.__enter__ = lambda s, *a: db
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
            MockRuntime.return_value.prepare_persistent_workspace.return_value = "/tmp/fake-work"
            db.query.return_value.filter.return_value.first.return_value = module
            mock_apply_task.delay.return_value = apply_celery

            from tasks.opentofu_tasks import run_opentofu_init

            # bind=True tasks receive `self` as first positional arg via __wrapped__
            result = run_opentofu_init.__wrapped__(
                MagicMock(), task.id, module.id, auto_apply=True
            )

        assert result["success"] is True
        assert result["apply_queued"] is True

    def test_apply_queued_false_when_deps_not_ready(self):
        """apply_queued=False when auto_apply=True but dependencies are not satisfied.

        Regression: previously the function returned {"success": True, "skipped": True}
        with no apply_queued field, giving callers no way to detect the stall.
        """
        module, db, task, workspace, fake_lock, _ = self._make_init_harness(
            already_initialized=True, can_exec=False
        )

        with (
            patch("tasks.opentofu_tasks.get_db_context") as mock_db_ctx,
            patch("tasks.opentofu_tasks.fetch_task_or_raise", return_value=task),
            patch("tasks.opentofu_tasks.OpenTofuRuntime") as MockRuntime,
            patch("services.workspace_manager.WorkspaceManager", return_value=workspace),
            patch("tasks.opentofu_tasks.module_lock", fake_lock),
            patch("tasks.opentofu_tasks.check_dependencies", return_value=(False, ["dep-a"])),
            patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_apply_task,
            patch("tasks.opentofu_tasks.set_locked_module_fields"),
            patch("tasks.opentofu_tasks.create_deployment_record"),
        ):
            mock_db_ctx.return_value.__enter__ = lambda s, *a: db
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
            MockRuntime.return_value.prepare_persistent_workspace.return_value = "/tmp/fake-work"
            db.query.return_value.filter.return_value.first.return_value = module

            from tasks.opentofu_tasks import run_opentofu_init

            result = run_opentofu_init.__wrapped__(
                MagicMock(), task.id, module.id, auto_apply=True
            )

        assert result["success"] is True
        # apply_queued must be present and False — not missing (stale promise).
        assert "apply_queued" in result, "apply_queued key missing — silent stall risk"
        assert result["apply_queued"] is False
        # run_opentofu_apply must NOT have been called
        mock_apply_task.delay.assert_not_called()

    def test_apply_queued_absent_when_auto_apply_false(self):
        """When auto_apply=False, apply_queued=False (no apply attempted at all)."""
        module, db, task, workspace, fake_lock, _ = self._make_init_harness(
            already_initialized=True, can_exec=True
        )

        with (
            patch("tasks.opentofu_tasks.get_db_context") as mock_db_ctx,
            patch("tasks.opentofu_tasks.fetch_task_or_raise", return_value=task),
            patch("tasks.opentofu_tasks.OpenTofuRuntime") as MockRuntime,
            patch("services.workspace_manager.WorkspaceManager", return_value=workspace),
            patch("tasks.opentofu_tasks.module_lock", fake_lock),
            patch("tasks.opentofu_tasks.check_dependencies") as mock_check_deps,
            patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_apply_task,
            patch("tasks.opentofu_tasks.set_locked_module_fields"),
            patch("tasks.opentofu_tasks.create_deployment_record"),
        ):
            mock_db_ctx.return_value.__enter__ = lambda s, *a: db
            mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
            MockRuntime.return_value.prepare_persistent_workspace.return_value = "/tmp/fake-work"
            db.query.return_value.filter.return_value.first.return_value = module

            from tasks.opentofu_tasks import run_opentofu_init

            result = run_opentofu_init.__wrapped__(
                MagicMock(), task.id, module.id, auto_apply=False
            )

        assert result["success"] is True
        assert result["apply_queued"] is False
        # check_dependencies should NOT have been called when auto_apply=False
        mock_check_deps.assert_not_called()
        mock_apply_task.delay.assert_not_called()
