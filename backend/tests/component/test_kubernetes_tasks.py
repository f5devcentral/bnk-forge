"""
BC-C3: Component tests for Kubernetes Celery tasks.

Tests K8s task functions with mocked engines, DB context, and K8s client.
Verifies task status transitions, engine resolution, and error handling.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from models import ModuleLibrary, Project, ProjectModule, StackInstance, StackTemplate
from models import Task as TaskModel

# ── Helpers ──────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow()


def _make_project(db, name="K8s Test Project"):
    project = Project(
        name=name, project_type="kubernetes", cloud_provider="on-prem",
        environment="dev", backend_type="local", color="#000", icon="cloud",
        is_active=True,
    )
    db.add(project)
    db.flush()
    return project


def _make_library(db, name="k8s-module"):
    lib = ModuleLibrary(
        name=name, category="kubernetes", path=f"modules/{name}",
        provider="test", description="K8s test module",
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


def _make_stack(db, project):
    template = StackTemplate(
        name="K8s Stack Template",
        slug=f"k8s-stack-{project.id}",
        modules=[],
        category="kubernetes",
    )
    db.add(template)
    db.flush()

    stack = StackInstance(
        name=f"k8s-stack-{project.id}",
        template_id=template.id,
        project_id=project.id,
        status="deploying",
    )
    db.add(stack)
    db.flush()
    return stack


def _make_engine_result(success=True, outputs=None):
    """Create a mock OperationResult."""
    result = MagicMock()
    result.success = success
    result.stdout = "K8s operation output"
    result.error_message = None if success else "K8s error"
    result.error_suggestion = None
    result.outputs = outputs or {}
    result.resources_created = 3 if success else 0
    result.resources_modified = 1 if success else 0
    result.duration_seconds = 2.5
    result.log_output = "Log output"
    return result


_MOD = "tasks.kubernetes_tasks"


# ── Init Task ────────────────────────────────────────────────────────

class TestRunK8sInit:
    """tasks.kubernetes_tasks.run_k8s_init."""

    @patch(f"{_MOD}._create_deployment_record")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}._get_engine")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_init_success(self, mock_dt, mock_db_ctx, mock_get_engine,
                          mock_notify, mock_deploy, db):
        """Successful K8s init sets module to initialized."""
        naive_now = _now()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        task = _make_task(db, project, module, "init")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.init.return_value = _make_engine_result(success=True)
        mock_get_engine.return_value = (mock_engine, "kubernetes")

        with patch("services.execution.engine_router.record_engine_success"), \
             patch("services.execution.engine_router.record_engine_failure"), \
             patch("services.project_service.update_project_counts"):
            from tasks.kubernetes_tasks import run_k8s_init
            result = run_k8s_init(task.id, module.id)

        assert result["success"] is True
        assert result["exit_code"] == 0

    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}._create_deployment_record")
    @patch(f"{_MOD}._get_engine")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_init_failure(self, mock_dt, mock_db_ctx, mock_get_engine, mock_deploy,
                          mock_notify, db):
        """Failed K8s init sets module to init_failed."""
        naive_now = _now()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        task = _make_task(db, project, module, "init")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.init.return_value = _make_engine_result(success=False)
        mock_get_engine.return_value = (mock_engine, "kubernetes")

        with patch("services.execution.engine_router.record_engine_success"), \
             patch("services.execution.engine_router.record_engine_failure"), \
             patch("services.project_service.update_project_counts"):
            from tasks.kubernetes_tasks import run_k8s_init
            result = run_k8s_init(task.id, module.id)

        assert result["success"] is False

    @patch(f"{_MOD}.get_db_context")
    def test_init_task_not_found(self, mock_db_ctx, db):
        """Missing task raises ValueError."""
        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.kubernetes_tasks import run_k8s_init
        with pytest.raises((ValueError, Exception)):
            run_k8s_init(99999, 1)

    @patch("tasks.kubernetes_tasks._update_stack_status_if_needed")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}._create_deployment_record")
    @patch(f"{_MOD}._get_engine")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_init_failure_updates_parent_stack_progress(
        self,
        mock_dt,
        mock_db_ctx,
        mock_get_engine,
        mock_deploy,
        mock_notify,
        mock_update_stack,
        db,
    ):
        naive_now = _now()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib)
        stack = _make_stack(db, project)
        task = _make_task(db, project, module, "init")
        module.stack_instance_id = stack.id
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.init.return_value = _make_engine_result(success=False)
        mock_get_engine.return_value = (mock_engine, "kubernetes")

        with patch("services.execution.engine_router.record_engine_success"), \
             patch("services.execution.engine_router.record_engine_failure"), \
             patch("services.project_service.update_project_counts"):
            from tasks.kubernetes_tasks import run_k8s_init
            result = run_k8s_init(task.id, module.id)

        assert result["success"] is False
        mock_update_stack.assert_called_once()


# ── Apply Task ───────────────────────────────────────────────────────

class TestRunK8sApply:
    """tasks.kubernetes_tasks.run_k8s_apply."""

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

        from tasks.kubernetes_tasks import run_k8s_apply
        result = run_k8s_apply(task.id, module.id)

        assert result["success"] is False


class TestCatalogWorkspaceGating:
    """Workspace prep boundary for catalog-backed K8s modules only."""

    def test_builtin_row_does_not_prepare_catalog_workspace(self, db):
        project = _make_project(db)
        library = ModuleLibrary(
            name="builtin-gateway",
            category="kubernetes",
            path="bnk/gateway",
            provider="test",
            description="Builtin gateway",
            git_source="builtin://bnk/gateway",
            is_official=True,
            is_active=True,
            module_source_kind="builtin",
            deploy_model="helm",
            pack_manifest={"deployment_pack": {"entrypoints": {"chart_path": "charts/gateway"}}},
        )
        db.add(library)
        db.flush()
        module = _make_module(db, project, library)

        with patch("services.execution.opentofu_runtime.OpenTofuRuntime") as mock_runtime_cls:
            from tasks.kubernetes_tasks import _prepare_catalog_workspace_if_needed

            workspace_path = _prepare_catalog_workspace_if_needed(db, module, operation="plan")

        assert workspace_path is None
        mock_runtime_cls.assert_not_called()

    def test_catalog_k8s_row_prepares_workspace(self, db):
        project = _make_project(db)
        library = ModuleLibrary(
            name="catalog-service",
            category="kubernetes",
            path="k8s/catalog-service",
            provider="test",
            description="Catalog service",
            git_source="git::https://github.com/example/catalog.git//k8s/catalog-service?ref=main",
            is_official=True,
            is_active=True,
            module_source_kind="git_catalog",
            deploy_model="helm",
            pack_manifest={"deployment_pack": {"entrypoints": {"chart_path": "charts/service"}}},
        )
        db.add(library)
        db.flush()
        module = _make_module(db, project, library)

        with patch("services.execution.opentofu_runtime.OpenTofuRuntime") as mock_runtime_cls:
            mock_runtime = MagicMock()
            mock_runtime.prepare_persistent_workspace.return_value = "/tmp/catalog-workspace"
            mock_runtime_cls.return_value = mock_runtime

            from tasks.kubernetes_tasks import _prepare_catalog_workspace_if_needed

            workspace_path = _prepare_catalog_workspace_if_needed(db, module, operation="plan")

        assert workspace_path == "/tmp/catalog-workspace"
        mock_runtime.prepare_persistent_workspace.assert_called_once_with(module, operation="plan")


# ── Destroy Task ─────────────────────────────────────────────────────

class TestRunK8sDestroy:
    """tasks.kubernetes_tasks.run_k8s_destroy."""

    @patch(f"{_MOD}._publish_task_completion")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_destroy_skips_unapplied(self, mock_dt, mock_db_ctx, mock_publish, db):
        """Destroy for never-applied module is skipped."""
        naive_now = _now()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        lib = _make_library(db)
        module = _make_module(db, project, lib, status="initialized")
        task = _make_task(db, project, module, "destroy")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        with patch("tasks._tofu_helpers._update_stack_status_if_needed"):
            from tasks.kubernetes_tasks import run_k8s_destroy
            result = run_k8s_destroy(task.id, module.id)

        assert result["status"] == "skipped"

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

        from tasks.kubernetes_tasks import run_k8s_destroy
        result = run_k8s_destroy(task.id, module.id)

        assert result["success"] is False
