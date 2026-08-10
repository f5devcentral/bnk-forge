"""Engine Registry — single source of truth for engine validity, profiles, and routing.

All engine-axis constants (pack engines, execution engines, runner profiles,
pack→execution mapping, router priority) are derived from the six rows declared
in _ENGINES.  Adding a new engine means adding exactly one EngineSpec row here;
no other file changes are needed for the five derived views to pick it up.

This is a true stdlib leaf module (dataclasses + typing only).  No engine
implementation imports; no circular dependencies.  Deliberately located at
services/engine_registry.py (NOT under services/execution/) so that importing
it does NOT trigger services/execution/__init__.py and its transitive DB/settings
stack (OpenTofuRuntime, build_variables, can_execute → core.config, database).

Source-type maps (module-import source axis) are intentionally NOT here — they
are a different axis scoped to registry_service.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineSpec:
    name: str                        # canonical id = registry key (router label)
    pack_engine: str | None       # None ⟹ no pack engine (operator, ssh)
    execution_engine: str | None  # persisted as library_module.execution_engine
    legacy_engine_type: str | None  # persisted as library_module.engine_type
    runner_profiles: tuple[str, ...]   # () when no pack engine
    router_priority: int | None     # sort rank in get_engine; None ⟹ not a router candidate


# Declaration order is intentional:
#   valid_pack_engines() preserves this order (human-facing in error messages).
#   Do NOT reorder without confirming no callers depend on position.
_ENGINES: tuple[EngineSpec, ...] = (
    EngineSpec(
        name="opentofu",
        pack_engine="opentofu",
        execution_engine="opentofu",
        legacy_engine_type="opentofu",
        runner_profiles=("opentofu-default",),
        router_priority=3,
    ),
    EngineSpec(
        name="kubernetes",
        pack_engine="kubernetes",
        execution_engine="kubernetes-direct",
        legacy_engine_type="kubernetes",
        runner_profiles=("kubernetes-default",),
        router_priority=2,
    ),
    EngineSpec(
        name="ansible",
        pack_engine="ansible",
        execution_engine="ansible",
        legacy_engine_type="ansible",
        runner_profiles=("ansible-default",),
        router_priority=None,
    ),
    # NOTE: script has legacy_engine_type=None (no engine_type persisted today).
    # execution_engine=None because script is not dispatched via execution_engine metadata;
    # pack_to_execution() maps it by name ("script" → "script") via the name field.
    EngineSpec(
        name="script",
        pack_engine="script",
        execution_engine=None,
        legacy_engine_type=None,
        runner_profiles=("script-restricted",),
        router_priority=None,
    ),
    EngineSpec(
        name="operator",
        pack_engine=None,
        execution_engine="operator",
        legacy_engine_type=None,
        runner_profiles=(),
        router_priority=1,
    ),
    EngineSpec(
        name="ssh",
        pack_engine=None,
        execution_engine="ssh",
        legacy_engine_type="ssh",
        runner_profiles=(),
        router_priority=0,
    ),
    # D-023: classic BIG-IP TMOS/AS3 write engine.
    # No pack_engine (not a module-catalog pack engine — dispatched via execution_engine metadata).
    # No legacy_engine_type (new engine, never persisted as engine_type).
    EngineSpec(
        name="tmos",
        pack_engine=None,
        execution_engine="tmos",
        legacy_engine_type=None,
        runner_profiles=(),
        router_priority=None,
    ),
    # DEPLOY-ENGINE-EXT-007: procedural container step runner for container_image
    # artifacts. pack_engine="container" so artifact manifests validate against it;
    # execution_engine="container" so task_dispatch routes to the container engine.
    # No legacy_engine_type (new engine). router_priority=None — selected explicitly
    # by artifact kind, never a fallback router candidate.
    EngineSpec(
        name="container",
        pack_engine="container",
        execution_engine="container",
        legacy_engine_type=None,
        runner_profiles=("container-default",),
        router_priority=None,
    ),
    # *bnkctl family (awsbnkctl, kindbnkctl, dpubnkctl, …) — local subprocess engine.
    # No pack_engine (not a module-catalog pack engine).
    # No legacy_engine_type (new engine, never persisted as engine_type).
    # health_check() returns False gracefully when binary is absent (API container).
    EngineSpec(
        name="cli-bnkctl",
        pack_engine=None,
        execution_engine="cli-bnkctl",
        legacy_engine_type=None,
        runner_profiles=(),
        router_priority=None,
    ),
)


class EngineRegistry:
    """Derived-view accessors over a tuple of EngineSpec rows.

    Instantiate with a custom engines tuple only in tests (inject a copy,
    never mutate the module-level _ENGINES).
    """

    def __init__(self, engines: tuple[EngineSpec, ...] = _ENGINES) -> None:
        self._engines = engines

    # ------------------------------------------------------------------
    # Derived views
    # ------------------------------------------------------------------

    def valid_pack_engines(self) -> list[str]:
        """Pack-engine names in declaration order (human-facing in error joins)."""
        return [e.pack_engine for e in self._engines if e.pack_engine is not None]

    def runner_profiles_by_engine(self) -> dict[str, list[str]]:
        """Map pack_engine → list of valid runner profile names."""
        return {
            e.pack_engine: list(e.runner_profiles)
            for e in self._engines
            if e.pack_engine is not None
        }

    def pack_to_execution(self) -> dict[str, str]:
        """Map pack_engine → execution_engine.

        Intentionally excludes operator/ssh (pack_engine=None) so that
        module_metadata.py line 799 infer-path does not gain phantom keys.

        When execution_engine is None (script), falls back to name so the
        map still carries the "script" → "script" entry without making
        "script" appear in explicit_execution_engines().
        """
        return {
            e.pack_engine: (e.execution_engine if e.execution_engine is not None else e.name)
            for e in self._engines
            if e.pack_engine is not None
        }

    def explicit_execution_engines(self) -> set[str]:
        """Set of recognised execution_engine values.

        Must be a real set — the error path in task_dispatch does sorted().
        Exactly: {ansible, kubernetes-direct, operator, opentofu, ssh}
        """
        return {
            e.execution_engine
            for e in self._engines
            if e.execution_engine is not None
        }

    def explicit_engine_types(self) -> set[str]:
        """Set of recognised legacy engine_type values.

        script is intentionally excluded (legacy_engine_type=None for script).
        Exactly: {ansible, kubernetes, opentofu, ssh}
        """
        return {
            e.legacy_engine_type
            for e in self._engines
            if e.legacy_engine_type is not None
        }

    def router_priority(self) -> dict[str, int]:
        """Priority sort map for engine_router.get_engine.

        Keyed by EngineSpec.name (e.g. "kubernetes"), NOT execution_engine
        (e.g. "kubernetes-direct").  This is the #1 misroute trap:
        engine_router sorts by router label (name), not by execution_engine.

        Exactly: {ssh: 0, operator: 1, kubernetes: 2, opentofu: 3}
        """
        return {
            e.name: e.router_priority
            for e in self._engines
            if e.router_priority is not None
        }


# Module-level singleton — import this, don't construct EngineRegistry() yourself
# unless overriding in tests.
engine_registry = EngineRegistry()
