"""Review fixes for the module-lifecycle PR (bonnyr-f5 cold audit)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.component
class TestDestroyScopeBelongsToTheRun:
    """Scope must come from the EXECUTING task, not the module's newest one.

    Blocker: destroy leaf M (module scope) → user clicks Destroy All while it
    runs → the wave skips M because it has a non-terminal destroy task → M
    completes → the newest row is still module-scoped → the chain stops before
    chaining AND before terminal detection. Dependencies stranded, entity stuck
    in DESTROYING forever.
    """

    def test_scope_comes_from_the_supplied_task_not_the_newest(
        self, db, make_project, make_project_module, make_module_library, make_task
    ):
        from tasks._tofu_helpers import _destroy_scope_for

        project = make_project()
        lib = make_module_library(name="m", path="bnk/m")
        module = make_project_module(project=project, library_module=lib, status="destroyed")

        project_task = make_task(project=project, module=module, task_type="destroy",
                                 status="in_progress", meta_data={"destroy_scope": "project"},
                                 run_handle="run-1")
        make_task(project=project, module=module, task_type="destroy", status="completed",
                  meta_data={"destroy_scope": "module"})

        assert _destroy_scope_for(module, db, project_task.id) == "project", (
            "the executing project-scope run resolved as 'module' because a newer "
            "module-scoped row existed — dependencies would be stranded and the "
            "entity left DESTROYING forever"
        )

    def test_without_a_task_id_it_still_falls_back_to_newest(
        self, db, make_project, make_project_module, make_module_library, make_task
    ):
        """The janitor re-drives after worker death with no executing task."""
        from tasks._tofu_helpers import _destroy_scope_for

        project = make_project()
        lib = make_module_library(name="m2", path="bnk/m2")
        module = make_project_module(project=project, library_module=lib, status="destroyed")
        make_task(project=project, module=module, task_type="destroy", status="completed",
                  meta_data={"destroy_scope": "project"}, run_handle="run-9")

        assert _destroy_scope_for(module, db) == "project"


@pytest.mark.component
class TestUnknownDestroyScopeFailsClosed:
    """An unstamped legacy row must NOT fall through to the cascading heuristic.

    Blocker: every destroy Task written before the stamp existed has
    meta_data = NULL. A single-module destroy enqueued by the old code and still
    QUEUED across the rollout would complete under the stack_instance_id
    heuristic and delete its dependency — the original data-loss bug, live
    during the deploy window.
    """

    def test_legacy_row_without_run_handle_resolves_to_module(
        self, db, make_project, make_project_module, make_module_library, make_task
    ):
        from tasks._tofu_helpers import _destroy_scope_for

        project = make_project()
        lib = make_module_library(name="legacy", path="bnk/legacy")
        module = make_project_module(project=project, library_module=lib, status="destroyed")
        # Exactly what create_task produced before the stamp: no meta_data, and
        # no run_handle (only the wave dispatchers set that).
        t = make_task(project=project, module=module, task_type="destroy", status="completed")
        t.meta_data = None
        t.run_handle = None
        db.flush()

        assert _destroy_scope_for(module, db, t.id) == "module", (
            "an unstamped single-module destroy fell through to the cascading "
            "heuristic — this is the original data-loss bug during rollout"
        )

    def test_legacy_row_WITH_a_run_handle_still_defers_to_the_heuristic(
        self, db, make_project, make_project_module, make_module_library, make_task
    ):
        """Contrast: a wave-dispatched legacy row is a real multi-module run."""
        from tasks._tofu_helpers import _destroy_scope_for

        project = make_project()
        lib = make_module_library(name="legacy2", path="bnk/legacy2")
        module = make_project_module(project=project, library_module=lib, status="destroyed")
        t = make_task(project=project, module=module, task_type="destroy", status="completed",
                      run_handle="run-legacy")
        t.meta_data = None
        db.flush()

        assert _destroy_scope_for(module, db, t.id) is None


@pytest.mark.component
class TestCancelRequiresAConfirmedKill:
    """The lock must not be released on an unconfirmed kill.

    Blocker: cancel runs in the FastAPI `backend` service, whose image has no
    docker CLI and no DOCKER_HOST. `docker ps` raised FileNotFoundError, the
    runner swallowed it into "0 containers killed", and the caller
    force-released the lock — green light into a re-apply racing a live
    container over the same workspace.
    """

    def _module(self, db, make_project, make_module_library, make_project_module):
        project = make_project()
        lib = make_module_library(name="vpc", path="infra/vpc")
        m = make_project_module(project=project, library_module=lib, status="applying")
        db.flush()
        return project, m

    @patch("services.project_module_service.update_project_counts")
    def test_unconfirmed_kill_retains_the_lock_and_says_so(
        self, _c, db, make_project, make_module_library, make_project_module, make_task
    ):
        from services.project_module_service import ProjectModuleService

        project, module = self._module(db, make_project, make_module_library, make_project_module)
        svc = ProjectModuleService(db)
        make_task(project=project, module=module, task_type="apply",
                  status="in_progress", celery_task_id="celery-live")

        with patch("celery_app.celery_app"), \
             patch("services.module_lock.ModuleLockService") as lock, \
             patch.object(ProjectModuleService, "_kill_containers_for_tasks",
                          return_value=([], False)):
            result = svc.cancel_operation(module.id)

        lock.return_value.force_release.assert_not_called()
        assert result["containers_kill_confirmed"] is False
        assert "could not be confirmed" in result["message"], (
            "an unconfirmed kill was reported as a clean stop"
        )

    @patch("services.project_module_service.update_project_counts")
    def test_confirmed_kill_releases_the_lock(
        self, _c, db, make_project, make_module_library, make_project_module, make_task
    ):
        """Contrast: the normal path must still release."""
        from services.project_module_service import ProjectModuleService

        project, module = self._module(db, make_project, make_module_library, make_project_module)
        svc = ProjectModuleService(db)
        make_task(project=project, module=module, task_type="apply",
                  status="in_progress", celery_task_id="celery-live")

        with patch("celery_app.celery_app"), \
             patch("services.module_lock.ModuleLockService") as lock, \
             patch.object(ProjectModuleService, "_kill_containers_for_tasks",
                          return_value=(["abc123"], True)):
            result = svc.cancel_operation(module.id)

        lock.return_value.force_release.assert_called_once()
        assert result["containers_kill_confirmed"] is True


@pytest.mark.component
class TestDispatchChokepointGate:
    """`enabled` is enforced where every deploy path crosses.

    Gating at the callers missed stack_service.run_deploy (Deploy All for a
    stack — and the topology filter is the main producer of disabled modules),
    submit_init, and the worker auto-apply chains, which gate only on
    can_execute(), a dependency check that never looks at `enabled`.
    """

    def _disabled(self):
        return MagicMock(id=7, enabled=False)

    @pytest.mark.parametrize("fn,args", [
        ("dispatch_init", ()), ("dispatch_plan", ()), ("dispatch_apply", ()),
        ("dispatch_apply_signature", ()),
        ("dispatch_container_action", ("run-e2e", None)),
    ])
    def test_every_dispatch_entry_point_refuses_a_disabled_module(self, fn, args):
        from services.execution import task_dispatch
        with pytest.raises(ValueError, match="(?i)disabled"):
            getattr(task_dispatch, fn)(1, self._disabled(), *args)

    def test_destroy_is_deliberately_not_gated(self):
        """A disabled module can still hold live infrastructure."""
        from services.execution import task_dispatch
        with patch("tasks.opentofu_tasks.run_opentofu_destroy") as t:
            t.delay.return_value = MagicMock(id="c1")
            task_dispatch.dispatch_destroy(1, MagicMock(id=7, enabled=False,
                                                       library_module=None,
                                                       path_in_project="p"))
