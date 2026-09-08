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
    """module_state is derived from module statuses, not the stored counts.

    The counts bucket by a different rule than the delete gate: deployed_count
    counts only "applied", failed_count counts the five *_failed statuses, and
    neither counts "applying" or "destroying". Derived from those, a module
    mid-teardown reported "clean" while the gate refused with 409 -- a polling
    client would have read "clean" and issued the DELETE, which is issue #125
    under a new field name.
    """

    @pytest.mark.parametrize("statuses,expected", [
        ([], "clean"),
        (["destroyed"], "clean"),
        (["not_initialized", "planned", "initialized"], "clean"),
        (["init_failed", "plan_failed"], "clean"),      # failed, but own nothing
        (["applied"], "in_progress"),
        (["destroying"], "in_progress"),                # was "clean" -- the bug
        (["applying"], "in_progress"),
        (["initializing"], "in_progress"),
        (["destroy_failed"], "failed"),
        (["apply_failed"], "failed"),
        (["failed"], "failed"),
        (["applied", "destroy_failed"], "failed"),      # failure dominates
        (["destroyed", "applied"], "in_progress"),      # one live module is enough
        # A null status is not in NO_INFRA_STATUSES, so it counts as owning
        # infrastructure. The gate reads it the same way and refuses the delete,
        # which is the fail-closed direction: an unknown status could own anything.
        ([None], "in_progress"),
        (["destroyed", None], "in_progress"),
    ])
    def test_summary(self, statuses, expected):
        assert summarize_module_state(statuses) == expected


@pytest.mark.component
class TestModuleStateAgreesWithTheDeleteGate:
    """The invariant: module_state == "clean"  <=>  DELETE succeeds unforced.

    Both sides read NO_INFRA_STATUSES, so this holds by construction -- the
    point of the test is that it keeps holding when a status is added. If a new
    status reaches only one of the two, this fails and names it.
    """

    ALL_STATUSES = [
        "applied", "applying", "destroying", "destroy_failed", "apply_failed",
        "failed", "initializing", "planning",
        "destroyed", "not_initialized", "initialized", "planned",
        "init_failed", "plan_failed",
    ]

    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_field_and_gate_never_disagree(self, db, make_project, make_module_library,
                                           make_project_module, status):
        project = make_project(is_active=False)
        lib = make_module_library(name=f"agree-{status}", path=f"bnk/{status}")
        make_project_module(project=project, library_module=lib, status=status)
        db.flush()

        state = summarize_module_state(m.status for m in project.project_modules)

        gate_allows = True
        try:
            ProjectService(db).delete_project(project.id, force=False)
        except ConflictError:
            gate_allows = False

        assert (state == "clean") is gate_allows, (
            f"status {status!r}: module_state={state!r} but "
            f"DELETE {'succeeded' if gate_allows else 'was refused'} -- "
            "the field a client polls disagrees with the gate that protects it"
        )
