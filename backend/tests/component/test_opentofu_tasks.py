"""
BC-C3: Component tests for OpenTofu Celery tasks.

Tests task functions with mocked Celery, DB context, and subprocess.
Verifies task status transitions, error handling, and module status updates.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models import ModuleLibrary, Project, ProjectModule, StackInstance, StackTemplate
from models import Task as TaskModel
from services.module_lock import ModuleLock

# ── Helpers ──────────────────────────────────────────────────────────

def _now():
    """Return a naive UTC datetime compatible with SQLite."""
    return datetime.utcnow()


def _make_project(db, name="Tofu Test Project"):
    project = Project(
        name=name, project_type="kubernetes", cloud_provider="on-prem",
        environment="dev", backend_type="local", color="#000", icon="cloud",
        is_active=True,
    )
    db.add(project)
    db.flush()
    return project


def _make_library(db, name="test-module"):
    lib = ModuleLibrary(
        name=name, category="kubernetes", path=f"modules/{name}",
        provider="test", description="Test module",
        git_source="https://github.com/test/test.git",
        is_official=True, is_active=True,
    )
    db.add(lib)
    db.flush()
    return lib


def _make_module(db, project, library, status="not_initialized"):
    pm = ProjectModule(
        project_id=project.id, module_library_id=library.id,
        path_in_project=f"modules/{library.name}",
        status=status, variables={}, enabled=True,
    )
    db.add(pm)
    db.flush()
    return pm


def _make_task(db, project, module, task_type="init"):
    task = TaskModel(
        task_type=task_type, status="queued",
        project_id=project.id, module_id=module.id,
        created_at=_now(),
    )
    db.add(task)
    db.flush()
    return task


def _make_stack_instance(db, project):
    template = StackTemplate(
        name="test-stack-template",
        slug=f"test-stack-template-{project.id}",
        modules=[],
        cloud_provider="aws",
        is_public=True,
        is_active=True,
    )
    db.add(template)
    db.flush()

    stack = StackInstance(
        project_id=project.id,
        template_id=template.id,
        name="test-stack-instance",
        status="deployed",
    )
    db.add(stack)
    db.flush()
    return stack


_MOD = "tasks.opentofu_tasks"


def _mock_datetime_now(monkeypatch_target=None):
    """Patch datetime.now(UTC) inside tasks module to return naive datetimes.

    Instead of patching, we just let it use naive datetimes from SQLite
    and handle the mismatch by patching the datetime class used in the module.
    """
    pass  # We'll use a different approach


# ── Init Task ────────────────────────────────────────────────────────

class TestRunOpenTofuInit:
    """tasks.opentofu_tasks.run_opentofu_init."""

    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_init_success(self, mock_dt, mock_db_ctx, mock_runtime_cls, mock_creds,
                          mock_lock, mock_notify, mock_deploy, mock_counts, db):
        """Successful init sets task to completed and module to initialized."""
        # Make datetime.now() return naive UTC datetimes matching SQLite
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        task = _make_task(db, project, module, "init")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.run_init.return_value = (0, "Init success")
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.needs_reinit.return_value = (True, "first init")
        mock_workspace.is_initialized.return_value = False

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace):
            from tasks.opentofu_tasks import run_opentofu_init
            result = run_opentofu_init(task.id, module.id)

        assert result["success"] is True
        assert result["exit_code"] == 0

    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_init_failure(self, mock_dt, mock_db_ctx, mock_runtime_cls, mock_creds,
                          mock_lock, mock_notify, db):
        """Failed init sets task/module to failed status."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        task = _make_task(db, project, module, "init")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.run_init.return_value = (1, "Init error: missing provider")
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.needs_reinit.return_value = (True, "first init")
        mock_workspace.is_initialized.return_value = False

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace), \
             patch(f"{_MOD}.update_project_counts"), \
             patch(f"{_MOD}.create_deployment_record"), \
             patch(f"{_MOD}._update_stack_status_if_needed"):
            from tasks.opentofu_tasks import run_opentofu_init
            result = run_opentofu_init(task.id, module.id)

        assert result["success"] is False
        assert result["exit_code"] == 1

    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_init_skip_when_initialized(self, mock_dt, mock_db_ctx, mock_runtime_cls,
                                        mock_lock, mock_notify, db):
        """Already-initialized workspace skips init and returns success."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib, status="initialized")
        task = _make_task(db, project, module, "init")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.is_initialized.return_value = True
        mock_workspace.needs_reinit.return_value = (False, None)

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace), \
             patch(f"{_MOD}.create_deployment_record"):
            from tasks.opentofu_tasks import run_opentofu_init
            result = run_opentofu_init(task.id, module.id)

        assert result["success"] is True
        assert result["skipped"] is True

    @patch(f"{_MOD}.get_db_context")
    def test_init_task_not_found_raises(self, mock_db_ctx, db):
        """Missing task record raises ValueError."""
        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.opentofu_tasks import run_opentofu_init
        with pytest.raises((ValueError, Exception)):
            run_opentofu_init(99999, 1)

    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_init_cache_hit_does_not_bypass_reinit(
        self,
        mock_dt,
        mock_db_ctx,
        mock_runtime_cls,
        _mock_creds,
        mock_lock,
        _mock_notify,
        _mock_deploy,
        _mock_counts,
        db,
    ):
        """Cache shortcut must be skipped when workspace still needs reinit."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        stack = _make_stack_instance(db, project)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        module.stack_instance_id = stack.id
        task = _make_task(db, project, module, "init")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.run_init.return_value = (0, "Init success")
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.needs_reinit.return_value = (True, "Backend configuration changed")
        mock_workspace.is_initialized.return_value = True
        mock_workspace.is_blueprint_eligible.return_value = True
        mock_workspace.attach_cached_init.return_value = True

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace):
            from tasks.opentofu_tasks import run_opentofu_init

            result = run_opentofu_init(task.id, module.id)

        assert result["success"] is True
        assert result["skipped"] is False
        mock_workspace.attach_cached_init.assert_not_called()
        mock_engine.run_init.assert_called_once()


# ── Plan Task ────────────────────────────────────────────────────────

class TestRunOpenTofuPlan:
    """tasks.opentofu_tasks.run_opentofu_plan."""

    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_plan_success(self, mock_dt, mock_db_ctx, mock_runtime_cls, mock_deps,
                          mock_creds, mock_lock, mock_notify, mock_deploy,
                          mock_counts, db):
        """Successful plan sets status to completed/planned."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib, status="initialized")
        task = _make_task(db, project, module, "plan")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.run_plan.return_value = (0, "Plan: 3 to add")
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.is_initialized.return_value = True
        mock_workspace.needs_reinit.return_value = (False, None)

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace):
            from tasks.opentofu_tasks import run_opentofu_plan
            result = run_opentofu_plan(task.id, module.id)

        assert result["success"] is True
        assert result["exit_code"] == 0

    @patch(f"{_MOD}.get_db_context")
    def test_plan_module_not_found(self, mock_db_ctx, db):
        """Plan with missing module fails cleanly."""
        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        task = _make_task(db, project, module, "plan")
        db.commit()

        db.delete(module)
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.opentofu_tasks import run_opentofu_plan
        result = run_opentofu_plan(task.id, module.id)

        assert result["success"] is False


# ── Apply Task ───────────────────────────────────────────────────────

class TestRunOpenTofuApply:
    """tasks.opentofu_tasks.run_opentofu_apply."""

    @patch(f"{_MOD}.get_db_context")
    def test_apply_module_not_found(self, mock_db_ctx, db):
        """Apply with missing module fails cleanly."""
        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        task = _make_task(db, project, module, "apply")
        db.commit()

        db.delete(module)
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.opentofu_tasks import run_opentofu_apply
        result = run_opentofu_apply(task.id, module.id)

        assert result["success"] is False

    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}.normalize_module_outputs_in_place")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_apply_auto_recovers_stale_saved_plan_once(
        self,
        mock_dt,
        mock_db_ctx,
        mock_runtime_cls,
        _mock_deps,
        _mock_creds,
        mock_lock,
        _mock_notify,
        _mock_normalize,
        _mock_deploy,
        _mock_counts,
        db,
    ):
        """Stale saved plan is cleared/replanned and retried once."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib, status="planned")
        task = _make_task(db, project, module, "apply")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.reconcile_known_existing_resources.return_value = 0
        mock_engine.run_apply.side_effect = [
            (1, "Error: Saved plan is stale", {}),
            (0, "Apply complete", {"ok": True}),
        ]
        mock_engine.run_plan.return_value = (0, "Plan: 1 to add")
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.has_saved_plan.return_value = True
        mock_workspace.plan_is_valid.return_value = (True, None)
        mock_workspace.is_initialized.return_value = True
        mock_workspace.needs_reinit.return_value = (False, None)

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace), \
             patch(f"{_MOD}._update_stack_status_if_needed"):
            from tasks.opentofu_tasks import run_opentofu_apply

            result = run_opentofu_apply(task.id, module.id)

        assert result["success"] is True
        assert result["used_saved_plan"] is True
        assert mock_engine.run_apply.call_count == 2
        mock_engine.reconcile_known_existing_resources.assert_called_once()
        mock_workspace.clear_plan.assert_called()

        db.refresh(task)
        assert "=== PRE-APPLY STATE RECONCILIATION ===" in (task.logs or "")

    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}.normalize_module_outputs_in_place")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_apply_reconcile_imports_clear_saved_plan_before_plan_reuse(
        self,
        mock_dt,
        mock_db_ctx,
        mock_runtime_cls,
        _mock_deps,
        _mock_creds,
        mock_lock,
        _mock_notify,
        _mock_normalize,
        _mock_deploy,
        _mock_counts,
        db,
    ):
        """State imports invalidate saved plan before apply reuses it."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib, status="planned")
        task = _make_task(db, project, module, "apply")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.reconcile_known_existing_resources.return_value = 2
        mock_engine.run_plan.return_value = (0, "Plan: 0 to add")
        mock_engine.run_apply.return_value = (0, "Apply complete", {"ok": True})
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.has_saved_plan.side_effect = [True, False]
        mock_workspace.plan_is_valid.return_value = (True, None)
        mock_workspace.is_initialized.return_value = True
        mock_workspace.needs_reinit.return_value = (False, None)

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace), \
             patch(f"{_MOD}._update_stack_status_if_needed"):
            from tasks.opentofu_tasks import run_opentofu_apply

            result = run_opentofu_apply(task.id, module.id)

        assert result["success"] is True
        assert mock_workspace.clear_plan.call_count == 2
        assert all(call.args == (module,) for call in mock_workspace.clear_plan.call_args_list)
        # run_plan is now called with an additive on_output= streaming callback
        # (#195); assert on the positional args and ignore the sink kwarg.
        mock_engine.run_plan.assert_called_once()
        plan_args, plan_kwargs = mock_engine.run_plan.call_args
        assert plan_args == ("/tmp/workspace", {})
        assert set(plan_kwargs) <= {"on_output"}

        db.refresh(task)
        assert "=== RECONCILIATION INVALIDATED SAVED PLAN ===" in (task.logs or "")

    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}.normalize_module_outputs_in_place")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_apply_runs_reconcile_after_init_before_plan_when_new_plan_required(
        self,
        mock_dt,
        mock_db_ctx,
        mock_runtime_cls,
        _mock_deps,
        _mock_creds,
        mock_lock,
        _mock_notify,
        _mock_normalize,
        _mock_deploy,
        _mock_counts,
        db,
    ):
        """When creating a new plan, init completes before reconcile runs."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        # Phase 2 state machine rejects not_initialized -> applied (the
        # exact egregious skip the RFC wants to catch). In production
        # submit_apply blocks this transition at the HTTP layer; the test
        # bypasses that gate, so we set up the precondition state directly.
        module = _make_module(db, project, lib, status="applying")
        task = _make_task(db, project, module, "apply")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.run_init.return_value = (0, "Init success")
        mock_engine.reconcile_known_existing_resources.return_value = 0
        mock_engine.run_plan.return_value = (0, "Plan: 0 to add")
        mock_engine.run_apply.return_value = (0, "Apply complete", {"ok": True})
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.has_saved_plan.return_value = False
        mock_workspace.is_initialized.return_value = False
        mock_workspace.needs_reinit.return_value = (True, "first init")

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace), \
             patch(f"{_MOD}._update_stack_status_if_needed"):
            from tasks.opentofu_tasks import run_opentofu_apply

            result = run_opentofu_apply(task.id, module.id)

        assert result["success"] is True
        assert mock_engine.run_init.call_count == 1
        assert mock_engine.reconcile_known_existing_resources.call_count == 1
        assert mock_engine.run_plan.call_count == 1

        call_names = [call[0] for call in mock_engine.method_calls]
        assert call_names.index("run_init") < call_names.index("reconcile_known_existing_resources")
        assert call_names.index("reconcile_known_existing_resources") < call_names.index("run_plan")


# ── Destroy Task ─────────────────────────────────────────────────────

class TestRunOpenTofuDestroy:
    """tasks.opentofu_tasks.run_opentofu_destroy."""

    @patch(f"{_MOD}._publish_task_completion")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}._update_stack_status_if_needed")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_destroy_skips_unapplied_module(self, mock_dt, mock_db_ctx, mock_stack,
                                             mock_notify, mock_publish, db):
        """Destroy for never-applied module is skipped with success."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib, status="initialized")
        task = _make_task(db, project, module, "destroy")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.opentofu_tasks import run_opentofu_destroy
        result = run_opentofu_destroy(task.id, module.id)

        assert result["status"] == "skipped"
        db.refresh(module)
        assert module.status == "destroyed"

    @patch(f"{_MOD}.get_db_context")
    def test_destroy_module_not_found(self, mock_db_ctx, db):
        """Destroy with missing module fails cleanly."""
        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        task = _make_task(db, project, module, "destroy")
        db.commit()

        db.delete(module)
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.opentofu_tasks import run_opentofu_destroy
        result = run_opentofu_destroy(task.id, module.id)

        assert result["success"] is False


# ── Managed Cluster Scan Enqueue ──────────────────────────────────────

class TestManagedClusterScanEnqueue:
    """After EKS/ROKS register in apply, enqueue_cluster_scan is called."""

    def test_eks_register_enqueues_scan(self, db):
        """Successful EKS auto-registration enqueues a background scan.

        Simulates the path inside ``run_opentofu_apply``: register_eks_cluster
        returns a cluster, then ``enqueue_cluster_scan(cluster.id)`` fires.
        """
        mock_cluster = MagicMock()
        mock_cluster.id = 42
        mock_cluster.name = "eks-cluster"

        with patch("services.eks_service.register_eks_cluster", return_value=mock_cluster), \
             patch("tasks.cluster_scan_task.scan_cluster_async") as mock_scan_task:
            from services.eks_service import register_eks_cluster
            from tasks.cluster_scan_task import enqueue_cluster_scan

            cluster = register_eks_cluster(db, MagicMock())
            db.commit()
            enqueue_cluster_scan(cluster.id)

        mock_scan_task.delay.assert_called_once_with(42)

    def test_roks_register_enqueues_scan(self, db):
        """Successful ROKS auto-registration enqueues a background scan."""
        mock_cluster = MagicMock()
        mock_cluster.id = 55
        mock_cluster.name = "roks-cluster"

        with patch("services.roks_service.register_roks_cluster", return_value=mock_cluster), \
             patch("tasks.cluster_scan_task.scan_cluster_async") as mock_scan_task:
            from services.roks_service import register_roks_cluster
            from tasks.cluster_scan_task import enqueue_cluster_scan

            cluster = register_roks_cluster(db, MagicMock())
            db.commit()
            enqueue_cluster_scan(cluster.id)

        mock_scan_task.delay.assert_called_once_with(55)


# ── #195: Incremental log streaming during a run ─────────────────────────────

class TestOpenTofuTaskLogStreaming:
    """logs_full_size must grow while the task is in_progress, not jump from 0
    to its final value only at completion (issue #195)."""

    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_plan_persists_growing_logs_before_completion(
        self, mock_dt, mock_db_ctx, mock_runtime_cls, _mock_deps, _mock_creds,
        mock_lock, _mock_notify, _mock_deploy, _mock_counts, db,
    ):
        from sqlalchemy import func

        from tasks._tofu_helpers import TofuLogStreamer

        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib, status="initialized")
        task = _make_task(db, project, module, "plan")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        # As each line streams, record what a concurrent GET /api/tasks/{id}
        # would observe: the persisted logs_full_size (SQL length, exactly the
        # detail endpoint's computation) and the task status. This is measured
        # from the DB, not the in-memory buffer, so it proves the flush.
        observed_sizes: list[int] = []
        observed_status: list[str] = []

        def streaming_run_plan(work_dir, env, on_output=None, timeout=None):
            assert on_output is not None, "plan task must pass an on_output sink (#195)"
            for i in range(1, 6):
                on_output(f"tofu progress line {i}")
                size = db.query(func.length(TaskModel.logs)).filter(
                    TaskModel.id == task.id
                ).scalar()
                status = db.query(TaskModel.status).filter(
                    TaskModel.id == task.id
                ).scalar()
                observed_sizes.append(size or 0)
                observed_status.append(status)
            return (0, "".join(f"tofu progress line {i}\n" for i in range(1, 6)))

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.run_plan.side_effect = streaming_run_plan
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.is_initialized.return_value = True
        mock_workspace.needs_reinit.return_value = (False, None)

        # Force interval=0 so every streamed line flushes — otherwise the 2s
        # throttle would coalesce this fast test's lines into a single flush.
        def _fast_streamer(t, d, **_kw):
            return TofuLogStreamer(t, d, interval=0)

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace), \
             patch(f"{_MOD}.TofuLogStreamer", _fast_streamer):
            from tasks.opentofu_tasks import run_opentofu_plan
            result = run_opentofu_plan(task.id, module.id)

        assert result["success"] is True

        # (a) The bug reproduced: without streaming every one of these would be
        #     0. Instead the size is non-zero from the first line and grows.
        assert len(observed_sizes) == 5
        assert all(s > 0 for s in observed_sizes), observed_sizes
        assert observed_sizes == sorted(observed_sizes)
        assert len(set(observed_sizes)) == 5, f"not strictly growing: {observed_sizes}"
        # (b) …and this all happened while the task was still running.
        assert observed_status == ["in_progress"] * 5

        # Final state intact: complete log persisted, size == detail's len().
        db.refresh(task)
        assert task.status == "completed"
        assert "tofu progress line 5" in task.logs
        assert len(task.logs) >= observed_sizes[-1]

    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.module_lock")
    @patch(f"{_MOD}.get_cloud_credentials_env", return_value={})
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}.OpenTofuRuntime")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_plan_passes_on_output_sink_to_runtime(
        self, mock_dt, mock_db_ctx, mock_runtime_cls, _mock_deps, _mock_creds,
        mock_lock, _mock_notify, _mock_deploy, _mock_counts, db,
    ):
        """Guard: the plan task must hand run_plan a callable on_output sink."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib, status="initialized")
        task = _make_task(db, project, module, "plan")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_lock.return_value.__enter__ = MagicMock(
            return_value=ModuleLock(module_id=module.id, task_id=task.id, fence_token=0),
        )
        mock_lock.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.prepare_persistent_workspace.return_value = "/tmp/workspace"
        mock_engine.run_plan.return_value = (0, "Plan: 1 to add")
        mock_runtime_cls.return_value = mock_engine

        mock_workspace = MagicMock()
        mock_workspace.is_initialized.return_value = True
        mock_workspace.needs_reinit.return_value = (False, None)

        with patch("services.workspace_manager.WorkspaceManager", return_value=mock_workspace):
            from tasks.opentofu_tasks import run_opentofu_plan
            run_opentofu_plan(task.id, module.id)

        _args, kwargs = mock_engine.run_plan.call_args
        assert callable(kwargs.get("on_output"))
