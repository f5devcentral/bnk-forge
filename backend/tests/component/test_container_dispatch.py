"""Component tests for container-engine dispatch routing.

Verifies task_dispatch routes a module whose library_module.execution_engine is
'container' to the tasks.container_tasks family (init/plan/apply/destroy), and
that the celery task signatures are produced for parallel execution.

The Celery tasks themselves are mocked (.delay / .s) so no broker is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.execution import task_dispatch
from tests.factories import ModuleLibraryFactory, ProjectModuleFactory


def _container_module(db):
    lib = ModuleLibraryFactory(
        db,
        category="container",
        module_source_kind="artifact",
        execution_engine="container",
    )
    return ProjectModuleFactory(db, library_module=lib)


@pytest.mark.component
class TestContainerDispatchRouting:
    def test_resolve_dispatch_engine_is_container(self, db):
        module = _container_module(db)
        assert task_dispatch.get_engine_type(module) == "container"

    def test_dispatch_init_routes_to_container_task(self, db):
        module = _container_module(db)
        with patch("tasks.container_tasks.run_container_init") as task:
            task.delay.return_value = MagicMock(id="celery-1")
            task_dispatch.dispatch_init(101, module)
            task.delay.assert_called_once_with(101, module.id, auto_apply=False)

    def test_dispatch_plan_routes_to_container_task(self, db):
        module = _container_module(db)
        with patch("tasks.container_tasks.run_container_plan") as task:
            task.delay.return_value = MagicMock(id="celery-2")
            task_dispatch.dispatch_plan(102, module)
            task.delay.assert_called_once_with(102, module.id)

    def test_dispatch_apply_routes_to_container_task(self, db):
        module = _container_module(db)
        with patch("tasks.container_tasks.run_container_apply") as task:
            task.apply_async.return_value = MagicMock(id="celery-3")
            task_dispatch.dispatch_apply(103, module)
            # No manifest budget → global defaults (no time-limit kwargs).
            task.apply_async.assert_called_once_with((103, module.id))

    def test_dispatch_apply_derives_time_limit_from_manifest_budget(self, db):
        module = _container_module(db)
        # cluster-up: 3600s × 3 attempts + 2×300s backoff = 11400s budget — exceeds
        # the global 7500s task_time_limit, so a per-task limit must be derived.
        module.library_module.pack_manifest = {
            "steps": {
                "apply": [
                    {"name": "init", "timeout_seconds": 300},
                    {"name": "cluster-up", "timeout_seconds": 3600, "retry": {"max_attempts": 3, "backoff_seconds": 300}},
                ]
            }
        }
        with patch("tasks.container_tasks.run_container_apply") as task:
            task.apply_async.return_value = MagicMock(id="celery-3b")
            task_dispatch.dispatch_apply(107, module)
            _, kwargs = task.apply_async.call_args
            assert kwargs["time_limit"] > 7500
            assert 7500 <= kwargs["soft_time_limit"] < kwargs["time_limit"]

    def test_dispatch_destroy_routes_to_container_task(self, db):
        module = _container_module(db)
        with patch("tasks.container_tasks.run_container_destroy") as task:
            task.apply_async.return_value = MagicMock(id="celery-4")
            task_dispatch.dispatch_destroy(104, module)
            # No manifest budget → global defaults (no time-limit kwargs).
            task.apply_async.assert_called_once_with((104, module.id))

    def test_dispatch_destroy_derives_time_limit_from_manifest_budget(self, db):
        """A destroy can outlive the global limit exactly as an apply can.

        Being hard-killed mid-destroy leaves the module lock for the reclaim
        sweep — the same failure the apply path was already protected against
        (issue #463 F5).
        """
        module = _container_module(db)
        module.library_module.pack_manifest = {
            "steps": {
                "destroy": [
                    {"name": "teardown", "timeout_seconds": 3600,
                     "retry": {"max_attempts": 3, "backoff_seconds": 300}},
                ]
            }
        }
        db.flush()
        with patch("tasks.container_tasks.run_container_destroy") as task:
            task.apply_async.return_value = MagicMock(id="celery-4b")
            task_dispatch.dispatch_destroy(104, module)

        kwargs = task.apply_async.call_args.kwargs
        assert kwargs.get("time_limit", 0) > 7500, (
            "destroy budget exceeding the global limit did not raise this task's "
            "limit — a long teardown is hard-killed mid-run (#463 F5)"
        )
        assert kwargs["soft_time_limit"] < kwargs["time_limit"]

    def test_apply_signature_routes_to_container_task(self, db):
        module = _container_module(db)
        with patch("tasks.container_tasks.run_container_apply") as task:
            signature = MagicMock()
            signature.set.return_value = "sig-apply"
            task.s.return_value = signature
            sig = task_dispatch.dispatch_apply_signature(105, module)
            assert sig == "sig-apply"
            task.s.assert_called_once_with(105, module.id)

    def test_destroy_signature_routes_to_container_task(self, db):
        module = _container_module(db)
        with patch("tasks.container_tasks.run_container_destroy") as task:
            signature = MagicMock()
            signature.set.return_value = "sig-destroy"
            task.s.return_value = signature
            sig = task_dispatch.dispatch_destroy_signature(106, module)
            assert sig == "sig-destroy"
            task.s.assert_called_once_with(106, module.id)


@pytest.mark.component
class TestTimeLimitDerivationReadsCanonicalSteps:
    """#127: the time-limit derivation must read the SAME step-set the engine runs.

    `_derive_container_time_limits` read ``manifest["steps"]`` directly, while
    the engine and the validator resolve lifecycle steps through
    ``canonical_step_sets`` (which prefers ``execution.steps``, canonical since
    #123). An ``execution.steps`` manifest therefore derived an empty budget and
    silently fell back to the global 7500 s limit -- so the long cluster build
    this derivation exists to protect was hard-killed mid-run anyway. The
    failure was silent: no error, just the wrong ceiling.
    """

    # Identical steps, declared in each of the two supported locations.
    _STEPS = [
        {"name": "init", "timeout_seconds": 300},
        {"name": "cluster-up", "timeout_seconds": 3600,
         "retry": {"max_attempts": 3, "backoff_seconds": 300}},
    ]

    def _module(self, db, manifest):
        module = _container_module(db)
        module.library_module.pack_manifest = manifest
        return module

    def test_execution_steps_derive_the_same_budget_as_top_level_steps(self, db):
        """The issue's exact reproduction: both locations must yield one answer."""
        top = self._module(db, {"steps": {"apply": self._STEPS}})
        canonical = self._module(db, {"execution": {"steps": {"apply": self._STEPS}}})

        from_top = task_dispatch._derive_container_time_limits(top, "apply")
        from_canonical = task_dispatch._derive_container_time_limits(canonical, "apply")

        assert from_top, "sanity: the top-level shape must derive a budget"
        assert from_canonical == from_top, (
            "execution.steps derived a different ceiling than top-level steps -- "
            "the canonical location silently fell back to the global limit (#127)"
        )
        assert from_canonical["time_limit"] > 7500

    def test_derivation_and_engine_resolve_the_same_steps(self, db):
        """Mirror of #123's engine/validator agreement test, for the derivation.

        The engine runs canonical_step_sets(manifest)[phase]. Whatever budget
        the derivation computes must come from exactly those steps.
        """
        from services.module_metadata import canonical_step_sets

        manifest = {"execution": {"steps": {"apply": self._STEPS, "destroy": self._STEPS[:1]}}}
        module = self._module(db, manifest)

        for phase in ("apply", "destroy"):
            engine_steps = canonical_step_sets(manifest)[phase]
            # Re-derive from a manifest holding ONLY the engine's resolved steps
            # at the top level; if the derivation reads the canonical set, the
            # two budgets are identical.
            reference = self._module(db, {"steps": {phase: engine_steps}})
            assert (
                task_dispatch._derive_container_time_limits(module, phase)
                == task_dispatch._derive_container_time_limits(reference, phase)
            ), f"derivation disagrees with the engine's resolved {phase} steps"

    def test_destroy_budget_is_derived_from_execution_steps(self, db):
        """destroy goes through the same resolver as apply (issue #463 F5)."""
        module = self._module(db, {"execution": {"steps": {"destroy": self._STEPS}}})
        limits = task_dispatch._derive_container_time_limits(module, "destroy")
        assert limits and limits["time_limit"] > 7500

    def test_action_step_set_is_untouched_by_the_resolver(self, db):
        """The issue is explicit: leave the `action` branch reading manifest["actions"]."""
        module = self._module(db, {
            "actions": {"e2e": {"steps": self._STEPS}},
            # A top-level lifecycle set too -- must not leak into the action budget.
            "steps": {"apply": [{"name": "x", "timeout_seconds": 1}]},
        })
        limits = task_dispatch._derive_container_time_limits(module, "action", action="e2e")
        assert limits and limits["time_limit"] > 7500

    def test_missing_phase_in_canonical_set_uses_global_defaults(self, db):
        """An execution.steps manifest with no destroy set must not invent one."""
        module = self._module(db, {"execution": {"steps": {"apply": self._STEPS}}})
        assert task_dispatch._derive_container_time_limits(module, "destroy") == {}

    def test_dispatch_apply_honours_execution_steps_end_to_end(self, db):
        """Through the real dispatch path, not just the helper."""
        module = self._module(db, {"execution": {"steps": {"apply": self._STEPS}}})
        with patch("tasks.container_tasks.run_container_apply") as task:
            task.apply_async.return_value = MagicMock(id="celery-127")
            task_dispatch.dispatch_apply(127, module)
            _, kwargs = task.apply_async.call_args
            assert kwargs.get("time_limit", 0) > 7500, (
                "dispatch fell back to the global limit for an execution.steps manifest"
            )


@pytest.mark.component
class TestContainerActionTimeLimits:
    """A long e2e/scenario action needs the same derived limit as apply (#463 F5)."""

    def test_action_dispatch_derives_time_limit_from_the_action_step_set(self, db):
        module = _container_module(db)
        module.library_module.pack_manifest = {
            "actions": {
                "run-e2e": {
                    "title": "E2E",
                    "steps": [
                        {"name": "e2e", "timeout_seconds": 3600,
                         "retry": {"max_attempts": 3, "backoff_seconds": 300}},
                    ],
                }
            }
        }
        db.flush()
        with patch("tasks.container_tasks.run_container_action") as task:
            task.apply_async.return_value = MagicMock(id="celery-act")
            task_dispatch.dispatch_container_action(107, module, "run-e2e", {"scenario": "x"})

        kwargs = task.apply_async.call_args.kwargs
        assert kwargs.get("time_limit", 0) > 7500, (
            "an action budget exceeding the global limit did not raise this task's "
            "limit — a long e2e run is hard-killed mid-run, leaving the module lock "
            "for the reclaim sweep (#463 F5)"
        )

    def test_action_without_a_budget_uses_global_defaults(self, db):
        """Contrast: a short action must not get a bespoke limit."""
        module = _container_module(db)
        module.library_module.pack_manifest = {
            "actions": {"quick": {"title": "Quick", "steps": [{"name": "q", "timeout_seconds": 60}]}}
        }
        db.flush()
        with patch("tasks.container_tasks.run_container_action") as task:
            task.apply_async.return_value = MagicMock(id="celery-act2")
            task_dispatch.dispatch_container_action(108, module, "quick", None)

        assert "time_limit" not in task.apply_async.call_args.kwargs
