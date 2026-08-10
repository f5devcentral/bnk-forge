"""Component tests for Ansible Celery task integration."""

from datetime import datetime
from unittest.mock import MagicMock, patch

from models import ModuleLibrary, Project, ProjectModule
from models import Task as TaskModel
from services.execution.engine_interface import ModuleContext, OperationResult, PlanResult

_MOD = "tasks.ansible_tasks"


def _now():
    return datetime.utcnow()


def _make_project(db, name="Ansible Test Project"):
    project = Project(
        name=name,
        project_type="kubernetes",
        cloud_provider="on-prem",
        environment="dev",
        backend_type="local",
        color="#000",
        icon="cloud",
        is_active=True,
    )
    db.add(project)
    db.flush()
    return project


def _make_library(db, name="ansible-module"):
    lib = ModuleLibrary(
        name=name,
        category="infrastructure",
        path=f"packs/{name}",
        provider="test",
        description="Ansible module",
        git_source="https://github.com/test/ansible-pack.git",
        is_official=False,
        is_active=True,
        engine_type="ansible",
        pack_manifest={
            "deployment_pack": {
                "engine": "ansible",
                "runner_profile": "ansible-default",
                "working_directory": ".",
                "entrypoints": {
                    "playbook": "apply.yml",
                    "outputs_file": "outputs.json",
                },
                "lifecycle": {
                    "supports_init": True,
                    "supports_plan": True,
                    "supports_apply": True,
                    "supports_destroy": False,
                },
            }
        },
    )
    db.add(lib)
    db.flush()
    return lib


def _make_module(db, project, library, status="initialized"):
    pm = ProjectModule(
        project_id=project.id,
        module_library_id=library.id,
        path_in_project=f"packs/{library.name}",
        status=status,
        variables={},
        variable_overrides={"env": "dev"},
        enabled=True,
    )
    db.add(pm)
    db.flush()
    return pm


def _make_task(db, project, module, task_type="apply"):
    task = TaskModel(
        task_type=task_type,
        status="queued",
        project_id=project.id,
        module_id=module.id,
        created_at=_now(),
    )
    db.add(task)
    db.flush()
    return task


class TestRunAnsibleApply:
    @patch(f"{_MOD}._update_stack_status_if_needed")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.normalize_module_outputs_in_place")
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}._build_context")
    @patch(f"{_MOD}._engine")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_apply_success_persists_outputs_and_clears_error(
        self,
        mock_datetime,
        mock_db_ctx,
        _mock_notify,
        mock_engine_factory,
        mock_build_context,
        _mock_check_deps,
        mock_normalize,
        _mock_update_counts,
        _mock_create_deployment,
        _mock_update_stack,
        db,
    ):
        now = _now()
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        library = _make_library(db)
        module = _make_module(db, project, library, status="planned")
        module.deployment_error = "old failure"
        task = _make_task(db, project, module, task_type="apply")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_build_context.return_value = ModuleContext(
            module_id=module.id,
            project_id=project.id,
            path=module.path_in_project,
            category="infrastructure",
            variables={"env": "dev"},
        )

        mock_engine = MagicMock()
        mock_engine.apply.return_value = OperationResult(
            success=True,
            outputs={"service_endpoint": "https://example.test"},
            stdout="ok",
        )
        mock_engine_factory.return_value = mock_engine

        from tasks.ansible_tasks import run_ansible_apply

        result = run_ansible_apply(task.id, module.id)

        assert result["success"] is True
        assert result["outputs"] == {"service_endpoint": "https://example.test"}
        db.refresh(module)
        db.refresh(task)
        assert module.status == "applied"
        assert module.outputs == {"service_endpoint": "https://example.test"}
        assert module.deployment_error is None
        assert task.status == "completed"
        mock_normalize.assert_called_once_with(module)


class TestRunAnsibleDestroy:
    @patch(f"{_MOD}._update_stack_status_if_needed")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}._build_context")
    @patch(f"{_MOD}._engine")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_destroy_unsupported_fails_truthfully(
        self,
        mock_datetime,
        mock_db_ctx,
        _mock_notify,
        mock_engine_factory,
        mock_build_context,
        _mock_update_counts,
        _mock_create_deployment,
        _mock_update_stack,
        db,
    ):
        now = _now()
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        library = _make_library(db)
        module = _make_module(db, project, library, status="applied")
        task = _make_task(db, project, module, task_type="destroy")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_build_context.return_value = ModuleContext(
            module_id=module.id,
            project_id=project.id,
            path=module.path_in_project,
            category="infrastructure",
            variables={},
        )

        mock_engine = MagicMock()
        mock_engine.destroy.return_value = OperationResult(
            success=False,
            error_message="Destroy is not supported for this ansible pack (lifecycle.supports_destroy=false)",
        )
        mock_engine_factory.return_value = mock_engine

        from tasks.ansible_tasks import run_ansible_destroy

        result = run_ansible_destroy(task.id, module.id)

        assert result["success"] is False
        db.refresh(module)
        db.refresh(task)
        assert module.status == "destroy_failed"
        assert "supports_destroy=false" in (module.deployment_error or "")
        assert task.status == "failed"

    @patch(f"{_MOD}.cleanup_durable_infra_private_key")
    @patch(f"{_MOD}._update_stack_status_if_needed")
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}._build_context")
    @patch(f"{_MOD}._engine")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_destroy_success_clears_stale_plan_and_init_metadata(
        self,
        mock_datetime,
        mock_db_ctx,
        _mock_notify,
        mock_engine_factory,
        mock_build_context,
        _mock_update_counts,
        _mock_create_deployment,
        _mock_update_stack,
        mock_cleanup_key,
        db,
    ):
        now = _now()
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        library = _make_library(db)
        module = _make_module(db, project, library, status="applied")
        module.outputs = {"old": "value"}
        module.last_deployed_at = now
        module.last_init_at = now
        module.plan_output = "old plan"
        module.plan_serial = 4
        module.vars_hash = "abc123"
        task = _make_task(db, project, module, task_type="destroy")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_build_context.return_value = ModuleContext(
            module_id=module.id,
            project_id=project.id,
            path=module.path_in_project,
            category="infrastructure",
            variables={},
        )

        mock_engine = MagicMock()
        mock_engine.destroy.return_value = OperationResult(success=True, stdout="destroyed")
        mock_engine_factory.return_value = mock_engine

        from tasks.ansible_tasks import run_ansible_destroy

        result = run_ansible_destroy(task.id, module.id)

        assert result["success"] is True
        db.refresh(module)
        db.refresh(task)
        assert task.status == "completed"
        assert module.status == "destroyed"
        assert module.outputs is None
        assert module.last_deployed_at is None
        assert module.last_init_at is None
        assert module.plan_output is None
        assert module.plan_serial == 0
        assert module.vars_hash is None
        mock_cleanup_key.assert_called_once_with(module.project_id, module.id)


class TestRunAnsiblePlan:
    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}._build_context")
    @patch(f"{_MOD}._engine")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_plan_success_sets_planned_status(
        self,
        mock_datetime,
        mock_db_ctx,
        _mock_notify,
        mock_engine_factory,
        mock_build_context,
        _mock_check_deps,
        _mock_update_counts,
        _mock_create_deployment,
        db,
    ):
        now = _now()
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        library = _make_library(db)
        module = _make_module(db, project, library, status="initialized")
        task = _make_task(db, project, module, task_type="plan")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_build_context.return_value = ModuleContext(
            module_id=module.id,
            project_id=project.id,
            path=module.path_in_project,
            category="infrastructure",
            variables={},
        )

        mock_engine = MagicMock()
        mock_engine.plan.return_value = PlanResult(
            has_changes=True,
            details="Ansible check-mode completed",
        )
        mock_engine_factory.return_value = mock_engine

        from tasks.ansible_tasks import run_ansible_plan

        result = run_ansible_plan(task.id, module.id)

        assert result["success"] is True
        db.refresh(module)
        db.refresh(task)
        assert module.status == "planned"
        assert "check-mode" in (module.plan_output or "")
        assert task.status == "completed"

    @patch(f"{_MOD}.create_deployment_record")
    @patch(f"{_MOD}.update_project_counts")
    @patch(f"{_MOD}.check_dependencies", return_value=(True, []))
    @patch(f"{_MOD}._build_context")
    @patch(f"{_MOD}._engine")
    @patch(f"{_MOD}._notify_task_started")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_plan_no_changes_is_successful(
        self,
        mock_datetime,
        mock_db_ctx,
        _mock_notify,
        mock_engine_factory,
        mock_build_context,
        _mock_check_deps,
        _mock_update_counts,
        _mock_create_deployment,
        db,
    ):
        now = _now()
        mock_datetime.now.return_value = now
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project = _make_project(db)
        library = _make_library(db)
        module = _make_module(db, project, library, status="initialized")
        module.deployment_error = "stale error"
        task = _make_task(db, project, module, task_type="plan")
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_build_context.return_value = ModuleContext(
            module_id=module.id,
            project_id=project.id,
            path=module.path_in_project,
            category="infrastructure",
            variables={},
        )

        mock_engine = MagicMock()
        mock_engine.plan.return_value = PlanResult(
            has_changes=False,
            details="Ansible check-mode completed. No changes detected.",
        )
        mock_engine_factory.return_value = mock_engine

        from tasks.ansible_tasks import run_ansible_plan

        result = run_ansible_plan(task.id, module.id)

        assert result["success"] is True
        assert result["has_changes"] is False
        db.refresh(module)
        db.refresh(task)
        assert task.status == "completed"
        assert module.status == "planned"
        assert module.deployment_error is None
