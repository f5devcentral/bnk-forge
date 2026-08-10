"""
Component tests for Module Dependency Resolver.

Tests cover: resolve (required/optional, transitive, circular detection),
get_dependency_graph, get_missing_prerequisites, validate_dependencies,
get_deployment_layers, get_dependency_summary.
"""

import pytest

from services.module_dependency_resolver import (
    CircularDependencyError,
    DependencyResolutionError,
    MissingDependencyError,
    ModuleDependencyResolver,
)

# ── Helpers ──────────────────────────────────────────────────────────


def _build_metadata(modules: dict[str, dict]) -> dict[str, dict]:
    """Build a metadata dict suitable for ModuleDependencyResolver.

    modules: mapping of path -> {"deps_req": [...], "deps_opt": [...], "layer": ..., "order": ...}
    """
    result = {}
    for path, cfg in modules.items():
        deps_req = [{"module": d, "reason": "needed"} for d in cfg.get("deps_req", [])]
        deps_opt = [{"module": d} for d in cfg.get("deps_opt", [])]
        result[path] = {
            "module": {
                "name": path.split("/")[-1],
                "layer": cfg.get("layer", "infrastructure"),
            },
            "dependencies": {
                "required": deps_req,
                "optional": deps_opt,
            },
            "deployment": {
                "order": cfg.get("order", 999),
            },
        }
    return result


# Simple chain: A -> B -> C
CHAIN_METADATA = _build_metadata({
    "mod/a": {"deps_req": ["mod/b"], "order": 3},
    "mod/b": {"deps_req": ["mod/c"], "order": 2},
    "mod/c": {"order": 1},
})

# Diamond: A -> B, A -> C, B -> D, C -> D
DIAMOND_METADATA = _build_metadata({
    "mod/a": {"deps_req": ["mod/b", "mod/c"], "order": 4},
    "mod/b": {"deps_req": ["mod/d"], "order": 3},
    "mod/c": {"deps_req": ["mod/d"], "order": 2},
    "mod/d": {"order": 1},
})

# Circular: A -> B -> C -> A
CIRCULAR_METADATA = _build_metadata({
    "mod/a": {"deps_req": ["mod/b"]},
    "mod/b": {"deps_req": ["mod/c"]},
    "mod/c": {"deps_req": ["mod/a"]},
})

# Optional deps: A --(required)--> B, A --(optional)--> C
OPTIONAL_METADATA = _build_metadata({
    "mod/a": {"deps_req": ["mod/b"], "deps_opt": ["mod/c"], "order": 3},
    "mod/b": {"order": 1},
    "mod/c": {"order": 2},
})


# ── resolve ──────────────────────────────────────────────────────────


class TestResolve:
    def test_simple_chain(self):
        resolver = ModuleDependencyResolver(CHAIN_METADATA)
        result = resolver.resolve(["mod/a"])

        assert set(result["required"]) == {"mod/a", "mod/b", "mod/c"}
        # Deployment order: C (1) -> B (2) -> A (3)
        assert result["deployment_order"] == ["mod/c", "mod/b", "mod/a"]

    def test_diamond_deduplicates(self):
        resolver = ModuleDependencyResolver(DIAMOND_METADATA)
        result = resolver.resolve(["mod/a"])

        assert set(result["required"]) == {"mod/a", "mod/b", "mod/c", "mod/d"}
        assert result["deployment_order"][0] == "mod/d"  # Deepest dependency first

    def test_circular_dependency_raises(self):
        resolver = ModuleDependencyResolver(CIRCULAR_METADATA)
        with pytest.raises(CircularDependencyError, match="Circular dependency"):
            resolver.resolve(["mod/a"])

    def test_module_not_in_metadata_raises(self):
        resolver = ModuleDependencyResolver(CHAIN_METADATA)
        with pytest.raises(DependencyResolutionError, match="not found"):
            resolver.resolve(["mod/nonexistent"])

    def test_missing_required_dependency_raises(self):
        metadata = _build_metadata({
            "mod/a": {"deps_req": ["mod/missing"]},
        })
        resolver = ModuleDependencyResolver(metadata)
        with pytest.raises(MissingDependencyError, match="mod/missing"):
            resolver.resolve(["mod/a"])

    def test_optional_deps_excluded_by_default(self):
        resolver = ModuleDependencyResolver(OPTIONAL_METADATA)
        result = resolver.resolve(["mod/a"])

        assert "mod/b" in result["required"]
        assert "mod/c" not in result["required"]
        assert result["optional"] == []

    def test_optional_deps_included_when_requested(self):
        resolver = ModuleDependencyResolver(OPTIONAL_METADATA)
        result = resolver.resolve(["mod/a"], include_optional=True)

        assert "mod/b" in result["required"]
        assert "mod/c" in result["optional"]

    def test_missing_optional_dep_skipped(self):
        metadata = _build_metadata({
            "mod/a": {"deps_opt": ["mod/missing"]},
        })
        resolver = ModuleDependencyResolver(metadata)
        result = resolver.resolve(["mod/a"], include_optional=True)

        assert result["required"] == ["mod/a"]
        assert result["optional"] == []


# ── get_dependency_graph ─────────────────────────────────────────────


class TestGetDependencyGraph:
    def test_graph_nodes_and_edges(self):
        resolver = ModuleDependencyResolver(CHAIN_METADATA)
        graph = resolver.get_dependency_graph(["mod/a"])

        node_ids = {n["id"] for n in graph["nodes"]}
        assert node_ids == {"mod/a", "mod/b", "mod/c"}

        edge_pairs = {(e["from"], e["to"]) for e in graph["edges"]}
        assert ("mod/a", "mod/b") in edge_pairs
        assert ("mod/b", "mod/c") in edge_pairs

    def test_graph_marks_selected(self):
        resolver = ModuleDependencyResolver(CHAIN_METADATA)
        graph = resolver.get_dependency_graph(["mod/a"])

        selected = [n for n in graph["nodes"] if n["is_selected"]]
        assert len(selected) == 1
        assert selected[0]["id"] == "mod/a"


# ── get_missing_prerequisites ────────────────────────────────────────


class TestGetMissingPrerequisites:
    def test_all_missing(self):
        resolver = ModuleDependencyResolver(CHAIN_METADATA)
        missing = resolver.get_missing_prerequisites(["mod/a"], [])
        assert set(missing) == {"mod/a", "mod/b", "mod/c"}

    def test_some_already_deployed(self):
        resolver = ModuleDependencyResolver(CHAIN_METADATA)
        missing = resolver.get_missing_prerequisites(["mod/a"], ["mod/c"])
        assert "mod/c" not in missing
        assert "mod/a" in missing
        assert "mod/b" in missing


# ── validate_dependencies ────────────────────────────────────────────


class TestValidateDependencies:
    def test_valid_metadata_no_errors(self):
        resolver = ModuleDependencyResolver(CHAIN_METADATA)
        errors = resolver.validate_dependencies()
        assert errors == []

    def test_detects_missing_required_dep(self):
        metadata = _build_metadata({
            "mod/a": {"deps_req": ["mod/missing"]},
        })
        resolver = ModuleDependencyResolver(metadata)
        # validate_dependencies detects the missing dep in its first pass,
        # but then resolve() raises MissingDependencyError which is not caught
        with pytest.raises(MissingDependencyError, match="mod/missing"):
            resolver.validate_dependencies()

    def test_detects_self_dependency(self):
        metadata = _build_metadata({
            "mod/a": {"deps_req": ["mod/a"]},
        })
        resolver = ModuleDependencyResolver(metadata)
        errors = resolver.validate_dependencies()
        assert any("self-dependency" in e for e in errors)


# ── get_deployment_layers ────────────────────────────────────────────


class TestGetDeploymentLayers:
    def test_groups_by_layer(self):
        metadata = _build_metadata({
            "infra/vpc": {"layer": "infrastructure", "order": 1},
            "k8s/setup": {"deps_req": ["infra/vpc"], "layer": "kubernetes", "order": 2},
        })
        resolver = ModuleDependencyResolver(metadata)
        layers = resolver.get_deployment_layers(["k8s/setup"])

        assert "infrastructure" in layers
        assert "kubernetes" in layers
        assert layers["infrastructure"] == ["infra/vpc"]
        assert layers["kubernetes"] == ["k8s/setup"]


# ── get_dependency_summary ───────────────────────────────────────────


class TestGetDependencySummary:
    def test_summary_structure(self):
        resolver = ModuleDependencyResolver(CHAIN_METADATA)
        summary = resolver.get_dependency_summary(["mod/a"])

        assert summary["selected_modules"] == ["mod/a"]
        assert len(summary["required_dependencies"]) == 3
        assert "graph" in summary
        assert "deployment_layers" in summary
        assert summary["total_modules"] == 3
