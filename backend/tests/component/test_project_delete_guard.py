"""DELETE /api/projects/{id} must not orphan live cloud resources (issue #125).

Forge holds the only record of what a module built, so deleting a project whose
modules still own infrastructure abandons it with no retry path — reported on
3.1.6, where a destroy-all returned non-zero, DELETE succeeded 22 seconds later,
and a live ROKS cluster plus its VPC, three subnets and three public gateways
had to be removed by hand.
"""

from unittest.mock import patch

import pytest

from core.errors import ConflictError
from services.project_service import ProjectService, summarize_module_state


@pytest.mark.component
class TestDeleteRefusesUndestroyedModules:
    def _project_with_module(self, db, make_project, make_module_library,
                             make_project_module, status):
        project = make_project(is_active=False)
        lib = make_module_library(name=f"m-{status}", path=f"bnk/{status}")
        module = make_project_module(project=project, library_module=lib, status=status)
        db.flush()
        return project, module

    @pytest.mark.parametrize("status", [
        "applied",          # the obvious case: live infrastructure
        "destroy_failed",   # the reported case
        "destroying",       # mid-teardown
        "applying",
        "apply_failed",     # partial infra
        "failed",
    ])
    def test_refuses_when_a_module_may_still_own_resources(
        self, db, make_project, make_module_library, make_project_module, status
    ):
        project, module = self._project_with_module(
            db, make_project, make_module_library, make_project_module, status)

        with patch("services.project_service.invalidate_cache"), \
             pytest.raises(ConflictError) as exc:
            ProjectService(db).delete_project(project.id)

        assert "not destroyed" in str(exc.value).lower(), (
            f"a module in {status!r} did not block deletion — its cloud resources "
            "would be orphaned with no way to reach them"
        )
        # The client needs to know WHICH modules, not just that something blocked.
        details = exc.value.details
        assert details["requires_force"] is True
        ids = [m["id"] for m in details["undestroyed_modules"]]
        assert module.id in ids
        assert details["undestroyed_modules"][0]["status"] == status

    @pytest.mark.parametrize("status", [
        "destroyed", "not_initialized", "initialized", "planned",
        "init_failed", "plan_failed",
    ])
    def test_allows_deletion_when_nothing_owns_infrastructure(
        self, db, make_project, make_module_library, make_project_module, status
    ):
        """Contrast: the gate must not block an ordinary cleanup.

        Without this, 'refuses everything' would pass the test above.
        """
        project, _ = self._project_with_module(
            db, make_project, make_module_library, make_project_module, status)

        with patch("services.project_service.invalidate_cache"), \
             patch("services.workspace_manager.WorkspaceManager.cleanup_project_workspaces",
                   return_value=0):
            result = ProjectService(db).delete_project(project.id)

        assert result["success"] is True

    def test_force_still_abandons_deliberately(
        self, db, make_project, make_module_library, make_project_module
    ):
        """Abandoning resources on purpose stays possible — it just isn't default."""
        project, _ = self._project_with_module(
            db, make_project, make_module_library, make_project_module, "destroy_failed")

        with patch("services.project_service.invalidate_cache"), \
             patch("services.workspace_manager.WorkspaceManager.cleanup_project_workspaces",
                   return_value=0):
            result = ProjectService(db).delete_project(project.id, force=True)

        assert result["success"] is True

    def test_empty_project_still_deletes(self, db, make_project):
        """A project with no modules has nothing to orphan."""
        project = make_project(is_active=False)
        db.flush()
        with patch("services.project_service.invalidate_cache"), \
             patch("services.workspace_manager.WorkspaceManager.cleanup_project_workspaces",
                   return_value=0):
            assert ProjectService(db).delete_project(project.id)["success"] is True


@pytest.mark.unit
class TestModuleStateSummary:
    """deployed_count alone cannot distinguish clean from wholly-failed.

    A destroy_failed module reports deployed_count=0, failed_count=1 — identical
    to success on deployed_count alone. That ambiguity is what the reporter's
    teardown script misread before issuing the DELETE.
    """

    @pytest.mark.parametrize("deployed,failed,expected", [
        (0, 0, "clean"),
        (2, 0, "in_progress"),
        (0, 1, "failed"),        # the case that looked clean
        (1, 1, "failed"),        # failure dominates
        (None, None, "clean"),   # nulls behave as zero
        (None, 3, "failed"),
    ])
    def test_summary(self, deployed, failed, expected):
        assert summarize_module_state(deployed, failed) == expected
