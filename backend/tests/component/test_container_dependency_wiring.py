"""Regression tests: the container path must actually apply dependency wiring.

A pack can declare an input as coming from another module's output
(``source: "module"``). That resolution lived inline in ``build_variables``, so
only the engines routed through it honoured the declaration; the container engine
assembles its own inputs and silently ignored it, leaving the step to fail from
inside the image on an input nobody had supplied.

These drive the real ``_build_engine_and_ctx`` rather than the extracted function
on its own. Asserting the function in isolation does not hold the fix in place —
the wiring call can be deleted from ``_build_engine_and_ctx`` and unit tests that
re-implement the merge in the test body stay green. What matters is that the
context the engine actually receives carries the resolved value.

The dependency *lookup* is stubbed; resolving a module by path is covered by the
unit tests. What is under test here is the plumbing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.factories import ModuleLibraryFactory, ProjectModuleFactory

# A pack input wired from another module's output.
_WIRED_INPUT = {
    "name": "registry_generic_host",
    "source": "module",
    "from_module": "harbor",
    "from_output": "registry_host",
}


def _container_module(db, *, inputs_metadata, variables=None):
    lib = ModuleLibraryFactory(
        db,
        category="container",
        module_source_kind="artifact",
        execution_engine="container",
        inputs_metadata=inputs_metadata,
    )
    return ProjectModuleFactory(db, library_module=lib, variables=variables or {})


def _build(db, module, *, dependency=None, operation="apply", fallback=None):
    """Drive _build_engine_and_ctx with its I/O collaborators mocked."""
    from tasks import container_tasks

    wm = MagicMock()
    wm.artifact_workspace_key.return_value = "bp-1"
    wm.ensure_artifact_workspace.return_value = "/app/workspaces/1/bp-1"
    wm.artifact_workspace_host_path.return_value = "/host/1/bp-1"
    wm.artifact_workspace_volume.return_value = "bnk-forge_workspace_data"
    wm.artifact_workspace_subpath.return_value = "1/bp-1"

    with (
        patch.object(
            container_tasks, "_artifact_manifest", return_value={"state": {"scope": "deployment"}}
        ),
        patch.object(container_tasks, "_registry_host", return_value="ghcr.io"),
        patch.object(container_tasks, "_resolve_runner", return_value=MagicMock()),
        patch("services.workspace_manager.WorkspaceManager", return_value=wm),
        patch(
            "services.execution.container_run_secrets.resolve_pull_authfile_for_module",
            return_value=None,
        ),
        patch(
            "services.execution.variable_assembler.find_dependency_by_path",
            return_value=dependency,
        ) as lookup,
        patch(
            "services.execution.variable_assembler._resolve_from_dependency_outputs",
            return_value=fallback,
        ),
    ):
        engine, ctx = container_tasks._build_engine_and_ctx(
            db, module, operation=operation
        )
        return engine, ctx, lookup


def _dependency_with(outputs):
    dep = MagicMock()
    dep.outputs = outputs
    return dep


@pytest.mark.component
class TestContainerDependencyWiring:
    def test_ctx_carries_the_value_wired_from_a_dependency(self, db):
        """The regression: deleting the wiring call must fail this."""
        module = _container_module(db, inputs_metadata={"required": [], "optional": [_WIRED_INPUT]})
        _engine, ctx, _lookup = _build(
            db, module, dependency=_dependency_with({"registry_host": "10.243.0.4"})
        )
        assert ctx.variables["registry_generic_host"] == "10.243.0.4"

    def test_operator_value_beats_a_dependency_output(self, db):
        """A blueprint that hard-codes a host is not overridden by a dependency."""
        module = _container_module(
            db,
            inputs_metadata={"required": [], "optional": [_WIRED_INPUT]},
            variables={"registry_generic_host": "registry.example.com"},
        )
        _engine, ctx, _lookup = _build(
            db, module, dependency=_dependency_with({"registry_host": "10.243.0.4"})
        )
        assert ctx.variables["registry_generic_host"] == "registry.example.com"

    def test_absent_required_dependency_is_tolerated_when_the_value_is_supplied(self, db):
        """The wiring must not be stricter here than in build_variables.

        A pack wiring from a module that does not exist in this deployment
        (infra/aws/vpc on bare metal) must not fail the build when the operator
        already supplied the value — build_variables' Layer 2.6 seeds exactly
        this case so Layer 3 stays quiet, and this path inherits it by seeding
        the dict it hands to the wiring.
        """
        module = _container_module(
            db,
            inputs_metadata={
                "required": [{
                    "name": "external_subnet_cidrs",
                    "source": "module",
                    "from_module": "infra/aws/vpc",
                    "from_output": "subnet_cidrs",
                }],
                "optional": [],
            },
            variables={"external_subnet_cidrs": ["10.0.0.0/24"]},
        )
        # dependency=None → the module genuinely is not in this deployment.
        _engine, ctx, _lookup = _build(db, module, dependency=None)
        assert ctx.variables["external_subnet_cidrs"] == ["10.0.0.0/24"]

    def test_absent_required_dependency_still_raises_when_nothing_supplies_it(self, db):
        """Seeding must not swallow the genuine failure it is guarding."""
        module = _container_module(
            db,
            inputs_metadata={
                "required": [{
                    "name": "external_subnet_cidrs",
                    "source": "module",
                    "from_module": "infra/aws/vpc",
                    "from_output": "subnet_cidrs",
                }],
                "optional": [],
            },
        )
        with pytest.raises(ValueError, match="Required dependency not available"):
            _build(db, module, dependency=None)

    def test_destroy_stays_lenient(self, db):
        """A destroy runs after its dependencies may already be torn down."""
        module = _container_module(
            db,
            inputs_metadata={
                "required": [{
                    "name": "external_subnet_cidrs",
                    "source": "module",
                    "from_module": "infra/aws/vpc",
                    "from_output": "subnet_cidrs",
                }],
                "optional": [],
            },
        )
        _engine, ctx, _lookup = _build(db, module, dependency=None, operation="destroy")
        assert "external_subnet_cidrs" not in ctx.variables

    def test_stack_instance_id_is_forwarded_to_the_dependency_lookup(self, db):
        """Multi-stack disambiguation depends on it reaching the lookup.

        find_dependency_by_path takes stack_instance_id to tell two instances of
        the same blueprint apart. The container path passes it through
        getattr(module, "stack_instance_id", None); nothing asserted it arrived,
        and a factory-built module leaves it None, so the branch was never
        reached by any test.
        """
        from tests.factories import StackInstanceFactory

        module = _container_module(db, inputs_metadata={"required": [], "optional": [_WIRED_INPUT]})
        instance = StackInstanceFactory(db)          # real row: the FK is enforced
        module.stack_instance_id = instance.id
        db.flush()

        _engine, ctx, lookup = _build(
            db, module, dependency=_dependency_with({"registry_host": "10.243.0.4"})
        )

        assert lookup.called, "the dependency lookup should have run"
        assert lookup.call_args.kwargs.get("stack_instance_id") == instance.id
        assert ctx.variables["registry_generic_host"] == "10.243.0.4"

    def test_the_fallback_resolves_when_the_declared_path_does_not_match(self, db):
        """The Layer-3 fallback is reachable on this path too.

        When the declared from_module matches no module, the wiring falls back to
        searching actual dependencies for one publishing that output. That branch
        is covered on the build_variables path but was patched to None throughout
        these tests, so the container path never exercised it.
        """
        module = _container_module(db, inputs_metadata={"required": [], "optional": [_WIRED_INPUT]})

        _engine, ctx, _lookup = _build(
            db, module,
            dependency=None,                 # the declared path matches nothing
            fallback="10.9.9.9",             # but a real dependency publishes it
        )

        assert ctx.variables["registry_generic_host"] == "10.9.9.9"
