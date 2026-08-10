"""
BC-026: Component tests for DependencyGraphService — build layers,
topological sort, cycle detection, stats, recursive deps.

Uses real SQLite DB with factory fixtures.
"""

import pytest

from models import ModuleLibrary, ProjectModule
from services.dependency_graph_service import DependencyGraphService, _kahn_layers

# ── Helpers ──────────────────────────────────────────────────────────

def _make_project(db, name="Graph Project"):
    from models import Project
    project = Project(
        name=name, project_type="kubernetes", cloud_provider="on-prem",
        environment="dev", backend_type="local", color="#000", icon="cloud",
        is_active=True,
    )
    db.add(project)
    db.flush()
    return project


def _make_module(db, project, name, dependencies=None, enabled=True):
    lib = ModuleLibrary(
        name=name, category="kubernetes", path=f"modules/{name}",
        provider="test", description=f"Module {name}",
        git_source="https://github.com/test/test.git",
        is_official=True, is_active=True,
    )
    db.add(lib)
    db.flush()
    pm = ProjectModule(
        project_id=project.id, module_library_id=lib.id,
        path_in_project=f"modules/{name}", status="applied",
        variables={}, enabled=enabled, dependencies=dependencies or [],
    )
    db.add(pm)
    db.flush()
    return pm


# ── Build Layers ─────────────────────────────────────────────────────

class TestBuildLayers:
    def test_no_modules(self, db):
        project = _make_project(db)
        svc = DependencyGraphService(db)
        layers = svc.build_layers(project.id)
        assert layers == []

    def test_single_module_single_layer(self, db):
        project = _make_project(db)
        _make_module(db, project, "vpc")
        svc = DependencyGraphService(db)
        layers = svc.build_layers(project.id)
        assert len(layers) == 1
        assert len(layers[0]) == 1

    def test_independent_modules_same_layer(self, db):
        project = _make_project(db)
        _make_module(db, project, "vpc")
        _make_module(db, project, "s3")
        _make_module(db, project, "iam")

        svc = DependencyGraphService(db)
        layers = svc.build_layers(project.id)
        # All independent → single layer
        assert len(layers) == 1
        assert len(layers[0]) == 3

    def test_chain_dependency(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "vpc")
        m2 = _make_module(db, project, "subnet", dependencies=[m1.id])
        m3 = _make_module(db, project, "ec2", dependencies=[m2.id])

        svc = DependencyGraphService(db)
        layers = svc.build_layers(project.id)
        assert len(layers) == 3
        assert layers[0][0].id == m1.id
        assert layers[1][0].id == m2.id
        assert layers[2][0].id == m3.id

    def test_disabled_modules_excluded(self, db):
        project = _make_project(db)
        _make_module(db, project, "vpc")
        _make_module(db, project, "disabled_mod", enabled=False)

        svc = DependencyGraphService(db)
        layers = svc.build_layers(project.id)
        all_ids = [m.id for layer in layers for m in layer]
        assert len(all_ids) == 1


# ── Cycle Detection ──────────────────────────────────────────────────

class TestCycleDetection:
    def test_no_cycles(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "a")
        _make_module(db, project, "b", dependencies=[m1.id])

        svc = DependencyGraphService(db)
        is_valid, cycle = svc.validate_no_cycles(project.id)
        assert is_valid is True
        assert cycle is None

    def test_detects_cycle(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "cycle_a")
        m2 = _make_module(db, project, "cycle_b", dependencies=[m1.id])
        # Create cycle: m1 -> m2 -> m1
        m1.dependencies = [m2.id]
        db.flush()

        svc = DependencyGraphService(db)
        is_valid, cycle = svc.validate_no_cycles(project.id)
        assert is_valid is False
        assert cycle is not None
        assert m1.id in cycle
        assert m2.id in cycle

    def test_empty_project_no_cycles(self, db):
        project = _make_project(db)
        svc = DependencyGraphService(db)
        is_valid, cycle = svc.validate_no_cycles(project.id)
        assert is_valid is True


# ── Execution Order ──────────────────────────────────────────────────

class TestExecutionOrder:
    def test_execution_order_returns_ids(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "root")
        m2 = _make_module(db, project, "child", dependencies=[m1.id])

        svc = DependencyGraphService(db)
        order = svc.get_execution_order(project.id)
        assert order == [[m1.id], [m2.id]]


# ── Graph Stats ──────────────────────────────────────────────────────

class TestGraphStats:
    def test_stats_no_modules(self, db):
        project = _make_project(db)
        svc = DependencyGraphService(db)
        stats = svc.get_dependency_graph_stats(project.id)
        assert stats["total_modules"] == 0
        assert stats["total_layers"] == 0

    def test_stats_with_modules(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "s_vpc")
        m2 = _make_module(db, project, "s_subnet", dependencies=[m1.id])
        m3 = _make_module(db, project, "s_sg", dependencies=[m1.id])

        svc = DependencyGraphService(db)
        stats = svc.get_dependency_graph_stats(project.id)
        assert stats["total_modules"] == 3
        assert stats["total_layers"] == 2
        assert stats["max_parallel_modules"] == 2  # subnet and sg in parallel
        assert stats["modules_with_no_deps"] == 1
        assert stats["modules_with_deps"] == 2

    def test_stats_with_cycle_returns_error(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "cyc_a")
        m2 = _make_module(db, project, "cyc_b", dependencies=[m1.id])
        m1.dependencies = [m2.id]
        db.flush()

        svc = DependencyGraphService(db)
        stats = svc.get_dependency_graph_stats(project.id)
        assert "error" in stats


# ── Recursive Dependencies ───────────────────────────────────────────

class TestRecursiveDeps:
    def test_recursive_dependencies(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "base")
        m2 = _make_module(db, project, "mid", dependencies=[m1.id])
        m3 = _make_module(db, project, "top", dependencies=[m2.id])

        svc = DependencyGraphService(db)
        deps = svc.get_module_dependencies_recursive(m3.id)
        assert m1.id in deps
        assert m2.id in deps
        assert m3.id not in deps

    def test_recursive_no_deps(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "lonely")

        svc = DependencyGraphService(db)
        deps = svc.get_module_dependencies_recursive(m1.id)
        assert deps == set()


# ── Reverse Dependencies ─────────────────────────────────────────────

class TestReverseDeps:
    def test_reverse_dependencies(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "rev_base")
        m2 = _make_module(db, project, "rev_child1", dependencies=[m1.id])
        m3 = _make_module(db, project, "rev_child2", dependencies=[m1.id])

        svc = DependencyGraphService(db)
        dependents = svc.get_reverse_dependencies(m1.id, project.id)
        dep_ids = [d.id for d in dependents]
        assert m2.id in dep_ids
        assert m3.id in dep_ids


# ── Build Layers for Subset ──────────────────────────────────────────

class TestBuildLayersForModules:
    def test_subset_layers(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "sub_a")
        m2 = _make_module(db, project, "sub_b", dependencies=[m1.id])
        m3 = _make_module(db, project, "sub_c")

        svc = DependencyGraphService(db)
        layers = svc.build_layers_for_modules([m1, m2])
        assert len(layers) == 2

    def test_reverse_layers(self, db):
        project = _make_project(db)
        m1 = _make_module(db, project, "rv_a")
        m2 = _make_module(db, project, "rv_b", dependencies=[m1.id])

        svc = DependencyGraphService(db)
        layers = svc.build_layers_for_modules([m1, m2], reverse=True)
        # Reversed: leaf first
        assert layers[0][0].id == m2.id
        assert layers[1][0].id == m1.id

    def test_empty_modules(self, db):
        svc = DependencyGraphService(db)
        layers = svc.build_layers_for_modules([])
        assert layers == []
