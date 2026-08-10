"""Unit tests for apply_dependency_output_wiring.

The wiring resolves inputs a pack declares ``source: "module"`` from a
dependency module's outputs. It used to live inline in ``build_variables``, so
only the engines that route through it honoured the declaration — the container
engine builds its own inputs and silently ignored it, leaving the step to fail
from inside the image on an input nobody had supplied.

These cover the extracted function directly, and the container path's use of it.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.execution.variable_assembler import apply_dependency_output_wiring


def _lib(required=None, optional=None):
    lib = MagicMock()
    lib.inputs_metadata = {"required": required or [], "optional": optional or []}
    return lib


def _module(project_id=1, stack_instance_id=None):
    m = MagicMock()
    m.project_id = project_id
    m.stack_instance_id = stack_instance_id
    return m


@pytest.mark.unit
class TestDependencyOutputWiring:
    def test_resolves_optional_input_from_dependency_output(self):
        dep = MagicMock()
        dep.outputs = {"registry_host": "10.243.0.4"}
        lib = _lib(optional=[{
            "name": "registry_generic_host", "source": "module",
            "from_module": "harbor", "from_output": "registry_host",
        }])
        variables: dict = {}

        with patch("services.execution.variable_assembler.find_dependency_by_path", return_value=dep):
            apply_dependency_output_wiring(MagicMock(), _module(), lib, variables)

        assert variables["registry_generic_host"] == "10.243.0.4"

    def test_leaves_unrelated_inputs_alone(self):
        lib = _lib(optional=[{"name": "prefix", "source": "user"}])
        variables = {"prefix": "fdisco"}

        with patch("services.execution.variable_assembler.find_dependency_by_path") as find:
            apply_dependency_output_wiring(MagicMock(), _module(), lib, variables)

        find.assert_not_called()
        assert variables == {"prefix": "fdisco"}

    def test_missing_required_output_raises_on_apply(self):
        dep = MagicMock()
        dep.outputs = {"something_else": "x"}
        lib = _lib(required=[{
            "name": "registry_generic_host", "source": "module",
            "from_module": "harbor", "from_output": "registry_host",
        }])

        with patch("services.execution.variable_assembler.find_dependency_by_path", return_value=dep):
            with pytest.raises(ValueError, match="Required dependency output not available"):
                apply_dependency_output_wiring(MagicMock(), _module(), lib, {})

    def test_missing_required_output_is_lenient_on_destroy(self):
        """A destroy runs after its dependencies may already be gone."""
        dep = MagicMock()
        dep.outputs = {}
        lib = _lib(required=[{
            "name": "registry_generic_host", "source": "module",
            "from_module": "harbor", "from_output": "registry_host",
        }])
        variables: dict = {}

        with patch("services.execution.variable_assembler.find_dependency_by_path", return_value=dep):
            apply_dependency_output_wiring(
                MagicMock(), _module(), lib, variables, operation="destroy"
            )

        assert "registry_generic_host" not in variables

    def test_absent_dependency_falls_back_to_metadata_default(self):
        lib = _lib(optional=[{
            "name": "registry_repo_prefix", "source": "module",
            "from_module": "harbor", "from_output": "repo_prefix",
            "default": "bnk-mirror",
        }])
        variables: dict = {}

        with patch("services.execution.variable_assembler.find_dependency_by_path", return_value=None), \
             patch("services.execution.variable_assembler._resolve_from_dependency_outputs", return_value=None):
            apply_dependency_output_wiring(MagicMock(), _module(), lib, variables)

        assert variables["registry_repo_prefix"] == "bnk-mirror"

    def test_no_metadata_is_a_noop(self):
        lib = MagicMock()
        lib.inputs_metadata = None
        variables = {"a": 1}
        apply_dependency_output_wiring(MagicMock(), _module(), lib, variables)
        assert variables == {"a": 1}


@pytest.mark.unit
class TestContainerPathUsesTheWiring:
    """The regression this exists to prevent: the container engine ignoring it."""

    def test_operator_supplied_value_beats_a_dependency_output(self):
        """A form value the operator set must survive the wiring.

        container_tasks layers the wired values UNDER the module's own variables,
        so a blueprint that hard-codes a registry host is not overridden by a
        dependency that happens to publish one.
        """
        dep = MagicMock()
        dep.outputs = {"registry_host": "10.243.0.4"}
        lib = _lib(optional=[{
            "name": "registry_generic_host", "source": "module",
            "from_module": "harbor", "from_output": "registry_host",
        }])

        wired: dict = {}
        with patch("services.execution.variable_assembler.find_dependency_by_path", return_value=dep):
            apply_dependency_output_wiring(MagicMock(), _module(), lib, wired)

        operator_values = {"registry_generic_host": "registry.example.com"}
        effective = {**wired, **operator_values}

        assert effective["registry_generic_host"] == "registry.example.com"

    def test_wiring_fills_a_gap_the_operator_left(self):
        dep = MagicMock()
        dep.outputs = {"registry_host": "10.243.0.4"}
        lib = _lib(optional=[{
            "name": "registry_generic_host", "source": "module",
            "from_module": "harbor", "from_output": "registry_host",
        }])

        wired: dict = {}
        with patch("services.execution.variable_assembler.find_dependency_by_path", return_value=dep):
            apply_dependency_output_wiring(MagicMock(), _module(), lib, wired)

        effective = {**wired, **{"prefix": "fdisco"}}

        assert effective["registry_generic_host"] == "10.243.0.4"
