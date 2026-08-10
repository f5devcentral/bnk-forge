"""
Unit tests for DependencyGraphService — Kahn's algorithm implementation (B4).

Self-contained: no DB, no Celery. Uses mock ProjectModule objects to verify
the layer-based topological sort, cycle detection, and edge cases.

Test cases:
  1. No dependencies — all modules in layer 0
  2. Linear chain — one module per layer
  3. Diamond dependencies — correct layer grouping
  4. Cycle detection — raises ValueError
  5. Single module — single layer with one module
  6. Empty input — returns empty list
  7. build_layers_for_modules with reverse=True
  8. build_layers_for_modules filters deps to subset
  9. validate_no_cycles returns (True, None) for acyclic graph
  10. validate_no_cycles returns (False, cycle_ids) for cyclic graph
  11. _kahn_layers standalone function
"""

import os
import sys
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)


# ---------------------------------------------------------------------------
# Helpers — lightweight mock ProjectModule
# ---------------------------------------------------------------------------

class FakeLibraryModule:
    """Minimal stand-in for ModuleLibrary."""
    def __init__(self, name: str):
        self.name = name
        self.path = f"modules/{name}"


class FakeProjectModule:
    """
    Minimal stand-in for ProjectModule.  Hashable by id so it can live in sets.
    """
    def __init__(
        self,
        id: int,
        name: str,
        dependencies: list[int] | None = None,
        enabled: bool = True,
        project_id: int = 1,
    ):
        self.id = id
        self.library_module = FakeLibraryModule(name)
        self.dependencies = dependencies or []
        self.enabled = enabled
        self.project_id = project_id
        self.module_library_id = id
        self.status = "not_initialized"

    def __hash__(self):
        return hash(self.id)

    def __eq__(self, other):
        return isinstance(other, FakeProjectModule) and self.id == other.id

    def __repr__(self):
        return f"FakeProjectModule(id={self.id}, name={self.library_module.name})"


def _ids_per_layer(layers) -> list[list[int]]:
    """Convert layers of modules to layers of sorted IDs for easy assertion."""
    return [sorted(m.id for m in layer) for layer in layers]


# ---------------------------------------------------------------------------
# Tests for the pure _kahn_layers function
# ---------------------------------------------------------------------------

class TestKahnLayers:
    """Tests for the standalone _kahn_layers helper."""

    def _call(self, modules, **kwargs):
        from services.dependency_graph_service import _kahn_layers
        module_map = {m.id: m for m in modules}
        module_ids = set(module_map.keys())
        return _kahn_layers(module_map, module_ids, **kwargs)

    # -- empty input ---------------------------------------------------

    def test_empty_input(self):
        """Empty module set produces empty layers."""
        result = self._call([])
        assert result == []

    # -- single module -------------------------------------------------

    def test_single_module_no_deps(self):
        """A single module with no dependencies forms one layer."""
        m = FakeProjectModule(1, "vpc")
        layers = self._call([m])
        assert _ids_per_layer(layers) == [[1]]

    # -- all independent (no deps) ------------------------------------

    def test_no_dependencies(self):
        """All modules with zero dependencies land in layer 0."""
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "s3"),
            FakeProjectModule(3, "iam"),
        ]
        layers = self._call(modules)
        assert len(layers) == 1
        assert sorted(m.id for m in layers[0]) == [1, 2, 3]

    # -- linear chain --------------------------------------------------

    def test_linear_chain(self):
        """A → B → C produces three layers, one module each."""
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1]),
            FakeProjectModule(3, "instance", dependencies=[2]),
        ]
        layers = self._call(modules)
        assert _ids_per_layer(layers) == [[1], [2], [3]]

    # -- diamond -------------------------------------------------------

    def test_diamond_dependencies(self):
        """
        Diamond graph:
            A
           / \\
          B   C
           \\ /
            D

        A = layer 0, B+C = layer 1, D = layer 2
        """
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet-a", dependencies=[1]),
            FakeProjectModule(3, "subnet-b", dependencies=[1]),
            FakeProjectModule(4, "instance", dependencies=[2, 3]),
        ]
        layers = self._call(modules)
        assert _ids_per_layer(layers) == [[1], [2, 3], [4]]

    # -- wider fan-out ------------------------------------------------

    def test_wide_fanout(self):
        """
        One root, many leaves depending on it, then a collector.
            A
          / | \\
         B  C  D
          \\ | /
            E
        """
        modules = [
            FakeProjectModule(1, "root"),
            FakeProjectModule(2, "b", dependencies=[1]),
            FakeProjectModule(3, "c", dependencies=[1]),
            FakeProjectModule(4, "d", dependencies=[1]),
            FakeProjectModule(5, "collector", dependencies=[2, 3, 4]),
        ]
        layers = self._call(modules)
        assert _ids_per_layer(layers) == [[1], [2, 3, 4], [5]]

    # -- cycle detection -----------------------------------------------

    def test_cycle_two_nodes(self):
        """Two-node cycle: A ↔ B raises ValueError."""
        modules = [
            FakeProjectModule(1, "a", dependencies=[2]),
            FakeProjectModule(2, "b", dependencies=[1]),
        ]
        with pytest.raises(ValueError, match="Circular dependency"):
            self._call(modules)

    def test_cycle_three_nodes(self):
        """Three-node cycle: A → B → C → A raises ValueError."""
        modules = [
            FakeProjectModule(1, "a", dependencies=[3]),
            FakeProjectModule(2, "b", dependencies=[1]),
            FakeProjectModule(3, "c", dependencies=[2]),
        ]
        with pytest.raises(ValueError, match="Circular dependency"):
            self._call(modules)

    def test_cycle_with_acyclic_prefix(self):
        """
        Some modules are acyclic, but a sub-graph has a cycle.
        The acyclic modules should still be identified; the cycle should raise.
        """
        modules = [
            FakeProjectModule(1, "root"),               # layer 0, no cycle
            FakeProjectModule(2, "a", dependencies=[1, 3]),  # part of cycle
            FakeProjectModule(3, "b", dependencies=[2]),     # part of cycle
        ]
        with pytest.raises(ValueError, match="Circular dependency"):
            self._call(modules)

    # -- deps pointing outside the set ---------------------------------

    def test_external_deps_ignored(self):
        """
        Dependencies pointing to IDs not in module_ids are silently ignored.
        Module 2 depends on 99 (external) and 1 (internal).
        """
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1, 99]),
        ]
        layers = self._call(modules)
        assert _ids_per_layer(layers) == [[1], [2]]

    # -- filter_deps_to_set -------------------------------------------

    def test_filter_deps_to_set(self):
        """
        With filter_deps_to_set=True, deps outside module_ids are excluded.
        Module 2 depends on [1, 99]; 99 is outside set; only dep on 1 counts.
        """
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1, 99]),
        ]
        layers = self._call(modules, filter_deps_to_set=True)
        assert _ids_per_layer(layers) == [[1], [2]]

    def test_filter_deps_to_set_makes_node_independent(self):
        """
        Module 2 depends on [99] (outside set). With filtering, it has 0 in-degree.
        """
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[99]),
        ]
        layers = self._call(modules, filter_deps_to_set=True)
        assert len(layers) == 1
        assert sorted(m.id for m in layers[0]) == [1, 2]


# ---------------------------------------------------------------------------
# Tests for DependencyGraphService methods (with mocked DB)
# ---------------------------------------------------------------------------

class TestDependencyGraphServiceBuildLayers:
    """Tests for build_layers() which queries DB for enabled modules."""

    def _make_service(self, modules):
        """Create service with a mocked DB session returning *modules*."""
        from services.dependency_graph_service import DependencyGraphService

        db = MagicMock()
        # Chain: db.query().options().filter().all() -> modules
        query_mock = MagicMock()
        db.query.return_value = query_mock
        query_mock.options.return_value = query_mock
        query_mock.filter.return_value = query_mock
        query_mock.all.return_value = modules

        return DependencyGraphService(db)

    def test_empty_project(self):
        svc = self._make_service([])
        assert svc.build_layers(project_id=1) == []

    def test_three_independent(self):
        modules = [
            FakeProjectModule(1, "a"),
            FakeProjectModule(2, "b"),
            FakeProjectModule(3, "c"),
        ]
        svc = self._make_service(modules)
        layers = svc.build_layers(project_id=1)
        assert len(layers) == 1
        assert sorted(m.id for m in layers[0]) == [1, 2, 3]

    def test_linear_chain(self):
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1]),
            FakeProjectModule(3, "instance", dependencies=[2]),
        ]
        svc = self._make_service(modules)
        layers = svc.build_layers(project_id=1)
        assert _ids_per_layer(layers) == [[1], [2], [3]]

    def test_cycle_raises(self):
        modules = [
            FakeProjectModule(1, "a", dependencies=[2]),
            FakeProjectModule(2, "b", dependencies=[1]),
        ]
        svc = self._make_service(modules)
        with pytest.raises(ValueError, match="Circular dependency"):
            svc.build_layers(project_id=1)


class TestDependencyGraphServiceBuildLayersForModules:
    """Tests for build_layers_for_modules() — subset-based layering."""

    def test_empty(self):
        from services.dependency_graph_service import DependencyGraphService
        db = MagicMock()
        svc = DependencyGraphService(db)
        assert svc.build_layers_for_modules([]) == []

    def test_forward_order(self):
        from services.dependency_graph_service import DependencyGraphService
        db = MagicMock()
        svc = DependencyGraphService(db)

        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1]),
            FakeProjectModule(3, "instance", dependencies=[2]),
        ]
        layers = svc.build_layers_for_modules(modules)
        assert _ids_per_layer(layers) == [[1], [2], [3]]

    def test_reverse_order(self):
        from services.dependency_graph_service import DependencyGraphService
        db = MagicMock()
        svc = DependencyGraphService(db)

        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1]),
            FakeProjectModule(3, "instance", dependencies=[2]),
        ]
        layers = svc.build_layers_for_modules(modules, reverse=True)
        assert _ids_per_layer(layers) == [[3], [2], [1]]

    def test_subset_ignores_external_deps(self):
        """
        Module 2 depends on [1, 99]. Module 99 is NOT in the subset.
        build_layers_for_modules filters deps to the provided set,
        so module 2 only depends on module 1.
        """
        from services.dependency_graph_service import DependencyGraphService
        db = MagicMock()
        svc = DependencyGraphService(db)

        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1, 99]),
        ]
        layers = svc.build_layers_for_modules(modules)
        assert _ids_per_layer(layers) == [[1], [2]]

    def test_cycle_in_subset_raises(self):
        from services.dependency_graph_service import DependencyGraphService
        db = MagicMock()
        svc = DependencyGraphService(db)

        modules = [
            FakeProjectModule(1, "a", dependencies=[2]),
            FakeProjectModule(2, "b", dependencies=[1]),
        ]
        with pytest.raises(ValueError, match="Circular dependency"):
            svc.build_layers_for_modules(modules)


class TestValidateNoCycles:
    """Tests for validate_no_cycles() — project-wide cycle check."""

    def _make_service(self, modules):
        from services.dependency_graph_service import DependencyGraphService
        db = MagicMock()
        query_mock = MagicMock()
        db.query.return_value = query_mock
        query_mock.filter.return_value = query_mock
        query_mock.all.return_value = modules
        return DependencyGraphService(db)

    def test_no_modules(self):
        svc = self._make_service([])
        valid, cycle = svc.validate_no_cycles(project_id=1)
        assert valid is True
        assert cycle is None

    def test_acyclic(self):
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1]),
        ]
        svc = self._make_service(modules)
        valid, cycle = svc.validate_no_cycles(project_id=1)
        assert valid is True
        assert cycle is None

    def test_cyclic(self):
        modules = [
            FakeProjectModule(1, "a", dependencies=[2]),
            FakeProjectModule(2, "b", dependencies=[1]),
        ]
        svc = self._make_service(modules)
        valid, cycle = svc.validate_no_cycles(project_id=1)
        assert valid is False
        assert cycle is not None
        # Both modules should be identified as cycle members
        assert sorted(cycle) == [1, 2]


class TestGetExecutionOrder:
    """Tests for get_execution_order() — ID-only layer output."""

    def _make_service(self, modules):
        from services.dependency_graph_service import DependencyGraphService
        db = MagicMock()
        query_mock = MagicMock()
        db.query.return_value = query_mock
        query_mock.options.return_value = query_mock
        query_mock.filter.return_value = query_mock
        query_mock.all.return_value = modules
        return DependencyGraphService(db)

    def test_execution_order(self):
        modules = [
            FakeProjectModule(1, "vpc"),
            FakeProjectModule(2, "subnet", dependencies=[1]),
            FakeProjectModule(3, "instance", dependencies=[2]),
        ]
        svc = self._make_service(modules)
        order = svc.get_execution_order(project_id=1)
        assert order == [[1], [2], [3]]
