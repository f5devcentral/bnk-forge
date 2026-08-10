"""Unit tests for EngineRegistry (D-019 E3, GitHub #138).

Test surface:
1. add-fake-engine-appears-in-all-six-views (inject a copy, don't mutate global)
2. Five frozen-snapshot regressions (today's exact literals):
   - valid_pack_engines order/values
   - runner_profiles_by_engine shape
   - pack_to_execution (no operator/ssh keys)
   - explicit_execution_engines (real set; includes kubernetes-direct, not kubernetes)
   - explicit_engine_types (script EXCLUDED)
   - router_priority (keyed by name "kubernetes" NOT "kubernetes-direct")
3. Backed constants still equal registry-derived snapshots
4. registry_service unknown-source-type: no KeyError, graceful fallback
"""

import pytest

from services.engine_registry import _ENGINES, EngineRegistry, EngineSpec, engine_registry
from services.execution.task_dispatch import _EXPLICIT_ENGINE_TYPES, _EXPLICIT_EXECUTION_ENGINES
from services.module_metadata import (
    PACK_ENGINE_TO_EXECUTION_ENGINE,
    VALID_PACK_ENGINES,
    VALID_RUNNER_PROFILES_BY_ENGINE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_spec() -> EngineSpec:
    return EngineSpec(
        name="helm",
        pack_engine="helm",
        execution_engine="helm-direct",
        legacy_engine_type="helm",
        runner_profiles=("helm-default",),
        router_priority=4,
    )


def _registry_with_fake() -> EngineRegistry:
    """Return a new EngineRegistry with the fake engine appended — never mutates global."""
    return EngineRegistry(engines=_ENGINES + (_make_fake_spec(),))


# ---------------------------------------------------------------------------
# 1. Add fake engine — appears in all six derived views
# ---------------------------------------------------------------------------

class TestFakeEngineAppearsEverywhere:
    def test_appears_in_valid_pack_engines(self):
        reg = _registry_with_fake()
        assert "helm" in reg.valid_pack_engines()

    def test_appears_in_runner_profiles_by_engine(self):
        reg = _registry_with_fake()
        assert "helm" in reg.runner_profiles_by_engine()
        assert reg.runner_profiles_by_engine()["helm"] == ["helm-default"]

    def test_appears_in_pack_to_execution(self):
        reg = _registry_with_fake()
        assert reg.pack_to_execution()["helm"] == "helm-direct"

    def test_appears_in_explicit_execution_engines(self):
        reg = _registry_with_fake()
        assert "helm-direct" in reg.explicit_execution_engines()

    def test_appears_in_explicit_engine_types(self):
        reg = _registry_with_fake()
        assert "helm" in reg.explicit_engine_types()

    def test_appears_in_router_priority(self):
        reg = _registry_with_fake()
        assert reg.router_priority()["helm"] == 4

    def test_does_not_mutate_global(self):
        """Injecting a copy must not change the module-level singleton."""
        _registry_with_fake()
        assert "helm" not in engine_registry.valid_pack_engines()


# ---------------------------------------------------------------------------
# 2. Frozen-snapshot regressions — today's exact literals
# ---------------------------------------------------------------------------

class TestFrozenSnapshots:
    def test_valid_pack_engines_order_and_values(self):
        """Declaration order is human-facing and must be preserved."""
        assert engine_registry.valid_pack_engines() == ["opentofu", "kubernetes", "ansible", "script", "container"]

    def test_runner_profiles_by_engine_shape(self):
        expected = {
            "opentofu": ["opentofu-default"],
            "kubernetes": ["kubernetes-default"],
            "ansible": ["ansible-default"],
            "script": ["script-restricted"],
            "container": ["container-default"],
        }
        assert engine_registry.runner_profiles_by_engine() == expected

    def test_pack_to_execution_no_operator_or_ssh_keys(self):
        result = engine_registry.pack_to_execution()
        expected = {
            "opentofu": "opentofu",
            "ansible": "ansible",
            "kubernetes": "kubernetes-direct",
            "script": "script",
            "container": "container",
        }
        assert result == expected
        assert "operator" not in result
        assert "ssh" not in result

    def test_explicit_execution_engines_is_set_and_correct(self):
        result = engine_registry.explicit_execution_engines()
        assert isinstance(result, set)
        assert result == {"ansible", "cli-bnkctl", "container", "kubernetes-direct", "operator", "opentofu", "ssh", "tmos"}
        # Confirm "kubernetes" (the pack name) is NOT present
        assert "kubernetes" not in result

    def test_explicit_engine_types_excludes_script(self):
        result = engine_registry.explicit_engine_types()
        assert isinstance(result, set)
        assert result == {"ansible", "kubernetes", "opentofu", "ssh"}
        # script has legacy_engine_type=None — must be excluded
        assert "script" not in result

    def test_router_priority_keyed_by_name_not_execution_engine(self):
        """#1 misroute trap: router key is "kubernetes" not "kubernetes-direct"."""
        result = engine_registry.router_priority()
        assert result == {"ssh": 0, "operator": 1, "kubernetes": 2, "opentofu": 3}
        # Explicitly assert the trap value
        assert "kubernetes" in result
        assert "kubernetes-direct" not in result

    def test_router_priority_is_dict_of_int(self):
        result = engine_registry.router_priority()
        for k, v in result.items():
            assert isinstance(k, str)
            assert isinstance(v, int)


# ---------------------------------------------------------------------------
# 3. Backed constants equal registry-derived snapshots
# ---------------------------------------------------------------------------

class TestBackedConstantsMatchRegistry:
    def test_valid_pack_engines_backed(self):
        assert VALID_PACK_ENGINES == engine_registry.valid_pack_engines()

    def test_runner_profiles_by_engine_backed(self):
        assert VALID_RUNNER_PROFILES_BY_ENGINE == engine_registry.runner_profiles_by_engine()

    def test_pack_engine_to_execution_engine_backed(self):
        assert PACK_ENGINE_TO_EXECUTION_ENGINE == engine_registry.pack_to_execution()

    def test_explicit_execution_engines_backed(self):
        assert _EXPLICIT_EXECUTION_ENGINES == engine_registry.explicit_execution_engines()

    def test_explicit_engine_types_backed(self):
        assert _EXPLICIT_ENGINE_TYPES == engine_registry.explicit_engine_types()

    def test_explicit_execution_engines_supports_sorted(self):
        """Error path in task_dispatch does sorted() — must not fail."""
        result = sorted(_EXPLICIT_EXECUTION_ENGINES)
        assert result == sorted(engine_registry.explicit_execution_engines())


# ---------------------------------------------------------------------------
# 4. registry_service source-type graceful degrade — no KeyError
# ---------------------------------------------------------------------------

class TestRegistryServiceSourceTypeGracefulDegrade:
    def test_unknown_source_type_does_not_raise(self):
        """_SOURCE_TYPES.get(unknown, {}).get("dir_short", fallback) must not KeyError."""
        from services.registry_service import _SOURCE_TYPES

        unknown = "unknown_source_type_xyz"
        result = _SOURCE_TYPES.get(unknown, {}).get("dir_short", unknown)
        assert result == unknown  # fallback to the type name itself

    def test_known_source_types_have_dir_short(self):
        from services.registry_service import _SOURCE_TYPES

        for st in ("opentofu_registry", "terraform_registry", "tfc_private", "public_git"):
            assert "dir_short" in _SOURCE_TYPES[st]

    def test_known_source_types_have_display_name(self):
        from services.registry_service import _SOURCE_TYPES

        assert _SOURCE_TYPES["opentofu_registry"]["display_name"] == "OpenTofu Registry"
        assert _SOURCE_TYPES["terraform_registry"]["display_name"] == "Terraform Registry"
        assert _SOURCE_TYPES["tfc_private"]["display_name"] == "Terraform Cloud (private)"
        assert _SOURCE_TYPES["public_git"]["display_name"] == "Public Git"

    def test_dir_short_values_match_prior_behavior(self):
        """dir_short values must match the old _SOURCE_TYPE_DIR_SHORT map exactly."""
        from services.registry_service import _SOURCE_TYPES

        assert _SOURCE_TYPES["terraform_registry"]["dir_short"] == "terraform"
        assert _SOURCE_TYPES["opentofu_registry"]["dir_short"] == "opentofu"
        assert _SOURCE_TYPES["tfc_private"]["dir_short"] == "tfc"
        assert _SOURCE_TYPES["public_git"]["dir_short"] == "git"
