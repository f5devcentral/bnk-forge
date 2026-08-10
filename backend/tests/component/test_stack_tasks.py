"""
BC-C3: Component tests for stack deployment Celery tasks.

Tests deploy_stack_async and destroy_stack_async with mocked DB context and services.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from models import (
    ModuleLibrary,
    Project,
    ProjectModule,
    StackInstance,
    StackTemplate,
)

_MOD = "tasks.stack_tasks"


# ── Helpers ──────────────────────────────────────────────────────────

def _make_stack_env(db):
    """Create project, template, stack, and modules for testing."""
    project = Project(
        name="Stack Test Project", project_type="kubernetes",
        cloud_provider="on-prem", environment="dev",
        backend_type="local", color="#000", icon="cloud",
        is_active=True,
    )
    db.add(project)
    db.flush()

    template = StackTemplate(
        name="Test Stack Template", slug="test-stack-template",
        description="Test", modules=["vpc", "eks"],
        category="infrastructure",
    )
    db.add(template)
    db.flush()

    lib = ModuleLibrary(
        name="vpc", category="networking", path="modules/vpc",
        provider="test", description="VPC",
        git_source="https://github.com/test/test.git",
        is_official=True, is_active=True,
    )
    db.add(lib)
    db.flush()

    stack = StackInstance(
        name="test-stack", template_id=template.id,
        project_id=project.id, status="pending",
    )
    db.add(stack)
    db.flush()

    module = ProjectModule(
        project_id=project.id, module_library_id=lib.id,
        path_in_project="modules/vpc", status="not_initialized",
        variables={}, enabled=True, deployment_order=0,
        stack_instance_id=stack.id,
    )
    db.add(module)
    db.commit()
    db.refresh(stack)
    db.refresh(module)

    return project, template, stack, module


# ── deploy_stack_async ───────────────────────────────────────────────

class TestDeployStackAsync:
    """tasks.stack_tasks.deploy_stack_async."""

    @patch(f"{_MOD}.StackDeploymentService")
    @patch(f"{_MOD}.get_db_context")
    @patch(f"{_MOD}.datetime")
    def test_deploy_success(self, mock_dt, mock_db_ctx, mock_svc_cls, db):
        """Successful stack deployment returns success status."""
        naive_now = datetime.utcnow()
        mock_dt.now.return_value = naive_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        project, template, stack, module = _make_stack_env(db)

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_svc = MagicMock()
        mock_svc.update_stack_progress.return_value = None
        mock_svc_cls.return_value = mock_svc

        # publish_message is imported locally inside the function via
        # `from services.websocket_service import publish_message`
        # We temporarily add it to the module so the local import works
        import services.websocket_service as ws_mod
        mock_publish = MagicMock()
        ws_mod.publish_message = mock_publish
        try:
            from tasks.stack_tasks import deploy_stack_async
            result = deploy_stack_async(stack.id, project.id)
        finally:
            if hasattr(ws_mod, 'publish_message'):
                delattr(ws_mod, 'publish_message')

        assert result["status"] == "success"
        assert result["modules_deployed"] == 1

    @patch(f"{_MOD}.get_db_context")
    def test_deploy_stack_not_found(self, mock_db_ctx, db):
        """Deploy nonexistent stack raises ValueError."""
        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.stack_tasks import deploy_stack_async
        with pytest.raises(ValueError, match="not found"):
            deploy_stack_async(99999, 99999)


# ── destroy_stack_async ──────────────────────────────────────────────

class TestDestroyStackAsync:
    """tasks.stack_tasks.destroy_stack_async."""

    @patch(f"{_MOD}.StackDeploymentService")
    @patch(f"{_MOD}.get_db_context")
    def test_destroy_success(self, mock_db_ctx, mock_svc_cls, db):
        """Successful stack destruction returns success."""
        project, template, stack, module = _make_stack_env(db)

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        mock_svc = MagicMock()
        mock_svc.destroy_stack.return_value = (True, "Destroyed")
        mock_svc_cls.return_value = mock_svc

        # Mock publish_message the same way
        mock_publish = MagicMock()
        import services.websocket_service as ws_mod
        ws_mod.publish_message = mock_publish
        try:
            from tasks.stack_tasks import destroy_stack_async
            result = destroy_stack_async(stack.id, project.id)
        finally:
            if hasattr(ws_mod, 'publish_message'):
                delattr(ws_mod, 'publish_message')

        assert result["status"] == "success"

    @patch(f"{_MOD}.get_db_context")
    def test_destroy_stack_not_found(self, mock_db_ctx, db):
        """Destroy nonexistent stack raises ValueError."""
        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.stack_tasks import destroy_stack_async
        with pytest.raises(ValueError, match="not found"):
            destroy_stack_async(99999, 99999)
