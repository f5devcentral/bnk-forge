"""Review fixes for the module-lifecycle PR (bonnyr-f5 cold audit)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from core.errors import BadRequestError


@pytest.mark.component
class TestDestroyScopeBelongsToTheRun:
    """Scope must come from the EXECUTING task, not the module's newest one.

    Blocker: destroy leaf M (module scope) → user clicks Destroy All while it
    runs → the wave skips M because it has a non-terminal destroy task → M
    completes → the newest row is still module-scoped → the chain stops before
    chaining AND before terminal detection. Dependencies stranded, entity stuck
    in DESTROYING forever.
    """

    def test_destroy_all_ADOPTS_an_in_flight_module_scoped_task(
        self, db, make_project, make_project_module, make_module_library, make_task
    ):
        """The real scenario, which my previous test had inverted.

        Previously this class created a project-scoped row and asserted it won.
        That state is unreachable: the wave's idempotency guard is precisely why
        no project row exists for a module with an in-flight destroy. The test
        pinned the opposite of the bug.

        What must happen instead: the wave ADOPTS the in-flight module-scoped
        task into the run, so when it completes the chain resolves "project" and
        the dependencies are queued.
        """
        from services.parallel_execution_service import ParallelExecutionService
        from tasks._tofu_helpers import _destroy_scope_for

        project = make_project()
        lib_root = make_module_library(name="cluster", path="bnk/cluster")
        lib_leaf = make_module_library(name="bnk", path="bnk/bnk")
        root = make_project_module(project=project, library_module=lib_root, status="applied")
        leaf = make_project_module(project=project, library_module=lib_leaf,
                                   status="destroying", dependencies=[root.id])

        # Step 1: a single-module destroy is already running for the leaf.
        in_flight = make_task(project=project, module=leaf, task_type="destroy",
                              status="in_progress",
                              meta_data={"destroy_scope": "module"})
        assert _destroy_scope_for(leaf, db, in_flight.id) == "module"

        # Step 2: user clicks Destroy All.
        with patch("tasks._tofu_helpers.DependencyGraphService") as gs, \
             patch("services.execution.task_dispatch.dispatch_destroy_signature") as sig:
            graph = MagicMock()
            graph.get_reverse_dependencies.return_value = []
            gs.return_value = graph
            sig.return_value = MagicMock()
            ParallelExecutionService(db)._dispatch_first_destroy_wave(
                project.id, run_handle="run-adopt", force_destroy=True
            )
        db.refresh(in_flight)

        # Step 3: the executing task is now part of the project run, so when it
        # completes the chain proceeds instead of stranding the dependency.
        assert _destroy_scope_for(leaf, db, in_flight.id) == "project", (
            "the in-flight module-scoped task was skipped rather than adopted — "
            "its dependencies would never be queued and the project would sit in "
            "DESTROYING forever"
        )
        assert in_flight.run_handle == "run-adopt"

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


@pytest.mark.component
class TestCancelResolvesSubstrateNotDispatchFamily:
    """A container artifact on Kubernetes has no kill path — say so (review finding).

    `get_engine_type` returns the DISPATCH FAMILY; the substrate is chosen
    separately from execution.container_runner.backend or deploy_model. So a
    K8s-substrate module took the docker branch, `docker ps` returned zero rows
    with exit 0, and that read as a CONFIRMED kill: lock released, user told it
    stopped, while the Job ran on against live infrastructure.
    """

    def _module(self, manifest):
        return MagicMock(id=9, library_module=MagicMock(pack_manifest=manifest))

    @pytest.mark.parametrize("manifest,expected", [
        ({"execution": {"container_runner": {"backend": "kubernetes"}}}, "kubernetes"),
        ({"deploy_model": "helm"}, "kubernetes"),
        ({"execution": {"container_runner": {"backend": "docker"}}}, "docker"),
        ({"deploy_model": "compose"}, "docker"),
        ({}, "docker"),
    ])
    def test_substrate_resolution_mirrors_the_task_layer(self, manifest, expected):
        from services.project_module_service import ProjectModuleService
        assert ProjectModuleService._container_substrate(self._module(manifest)) == expected

    def test_kubernetes_substrate_reports_UNCONFIRMED(self):
        """No bnkforge.task label is stamped and the reaper is docker-only, so
        there is nothing to kill by — unconfirmed retains the lock."""
        from services.project_module_service import ProjectModuleService

        svc = ProjectModuleService(MagicMock())
        module = self._module({"deploy_model": "helm"})
        task = MagicMock(celery_task_id="celery-1")

        with patch("services.execution.task_dispatch.get_engine_type", return_value="container"), \
             patch("tasks.container_tasks.kill_module_containers") as kill:
            killed, confirmed = svc._kill_containers_for_tasks(module, [task])

        assert confirmed is False, (
            "a Kubernetes-substrate module reported a confirmed kill having killed "
            "nothing — the lock would be released while the Job kept running"
        )
        assert killed == []
        # The decisive assertion: it must not even TRY the docker kill. Without
        # this, the test passes against the broken version too — the unpatched
        # dispatch fails on a missing broker and also returns unconfirmed, which
        # is the right answer reached by accident rather than by design.
        kill.apply_async.assert_not_called()

    def test_docker_substrate_still_dispatches_the_kill(self):
        """Contrast: the substrate that DOES have a kill path must still use it."""
        from services.project_module_service import ProjectModuleService

        svc = ProjectModuleService(MagicMock())
        module = self._module({"deploy_model": "compose"})
        task = MagicMock(celery_task_id="celery-1")
        dispatched = MagicMock()
        dispatched.get.return_value = {"killed": ["abc"], "reachable": True, "error": None}

        with patch("services.execution.task_dispatch.get_engine_type", return_value="container"), \
             patch("tasks.container_tasks.kill_module_containers") as kill:
            kill.apply_async.return_value = dispatched
            killed, confirmed = svc._kill_containers_for_tasks(module, [task])

        assert (killed, confirmed) == (["abc"], True)


@pytest.mark.component
class TestDisabledModulesAreFilteredNotRaisedOver:
    """The gate must not raise into loops that commit before dispatching.

    stack_service.run_deploy commits a queued Task row before dispatch_init and
    has no try/except: a raise abandons every later module, leaves the stack
    DEPLOYING, and leaves an orphan queued row that makes _has_active_task true
    forever — permanently skipping that module on re-runs.
    """

    def test_stack_deploy_filters_disabled_modules_out(self):
        """The filter is on the dispatch SET, so no raise can reach the loop."""
        import inspect

        from services import stack_service

        src = inspect.getsource(stack_service.StackService.run_deploy)
        assert "if m.enabled" in src, (
            "run_deploy does not filter disabled modules — a disabled module "
            "would raise mid-loop and strand the whole stack deploy"
        )

    def test_submit_init_rejects_before_creating_a_task(self, db, make_project,
                                                        make_module_library,
                                                        make_project_module):
        """Rejection must precede create_task, which commits."""
        from models import Task as TaskModel
        from services.project_module_service import ProjectModuleService

        project = make_project()
        lib = make_module_library(name="v", path="i/v")
        module = make_project_module(project=project, library_module=lib,
                                     status="not_initialized", enabled=False)
        db.flush()

        with pytest.raises(BadRequestError, match="(?i)disabled"):
            ProjectModuleService(db).submit_init(module.id)

        assert db.query(TaskModel).filter(TaskModel.module_id == module.id).count() == 0, (
            "an orphan Task row was committed before the rejection — "
            "_has_active_task would then skip this module forever"
        )
        db.refresh(module)
        assert module.status == "not_initialized", "a transitional status was stranded"
