"""
Unit tests for #131 — unrecognized execution_engine must hard-error.

Covers:
- Non-empty unrecognized execution_engine → ValueError (not silent fallback)
- Empty / None execution_engine → still falls through to opentofu default
- All currently-supported engines still resolve correctly
- Unrecognized engine_type (legacy alias) keeps its original behavior (None return)
"""

from unittest.mock import MagicMock, patch

import pytest

from services.execution.task_dispatch import (
    _EXPLICIT_EXECUTION_ENGINES,
    _get_explicit_engine_type,
    _get_explicit_execution_engine,
    _resolve_dispatch_engine,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _module_with_execution_engine(engine: str | None) -> MagicMock:
    module = MagicMock()
    module.id = 1
    module.library_module = MagicMock()
    module.library_module.execution_engine = engine
    module.library_module.engine_type = None
    module.library_module.module_source_kind = None
    module.library_module.deploy_model = None
    return module


def _module_with_engine_type(engine_type: str | None) -> MagicMock:
    module = MagicMock()
    module.id = 2
    module.library_module = MagicMock()
    module.library_module.execution_engine = None
    module.library_module.engine_type = engine_type
    module.library_module.module_source_kind = None
    module.library_module.deploy_model = None
    return module


# ── _get_explicit_execution_engine ───────────────────────────────────


class TestGetExplicitExecutionEngineError:
    def test_unrecognized_non_empty_engine_raises_value_error(self):
        module = _module_with_execution_engine("phantom-engine")
        with pytest.raises(ValueError, match="Unrecognized execution_engine 'phantom-engine'"):
            _get_explicit_execution_engine(module)

    def test_error_message_includes_supported_engines(self):
        module = _module_with_execution_engine("bad-engine")
        with pytest.raises(ValueError, match="Supported engines"):
            _get_explicit_execution_engine(module)

    def test_none_engine_returns_none(self):
        module = _module_with_execution_engine(None)
        assert _get_explicit_execution_engine(module) is None

    def test_empty_string_engine_returns_none(self):
        module = _module_with_execution_engine("")
        assert _get_explicit_execution_engine(module) is None

    def test_whitespace_only_engine_returns_none(self):
        module = _module_with_execution_engine("   ")
        assert _get_explicit_execution_engine(module) is None

    def test_no_library_module_returns_none(self):
        module = MagicMock()
        module.library_module = None
        assert _get_explicit_execution_engine(module) is None


class TestGetExplicitExecutionEngineValid:
    @pytest.mark.parametrize("engine", sorted(_EXPLICIT_EXECUTION_ENGINES))
    def test_all_supported_engines_return_normalized(self, engine):
        module = _module_with_execution_engine(engine)
        assert _get_explicit_execution_engine(module) == engine

    def test_case_insensitive(self):
        module = _module_with_execution_engine("OpenTofu")
        assert _get_explicit_execution_engine(module) == "opentofu"

    def test_strips_whitespace(self):
        module = _module_with_execution_engine("  ansible  ")
        assert _get_explicit_execution_engine(module) == "ansible"


# ── _get_explicit_engine_type (legacy — must NOT hard-error) ─────────


class TestGetExplicitEngineTypePreservesLegacyBehavior:
    """The legacy engine_type alias must continue returning None for unrecognized
    values (not raise). It is a fallback, not the authoritative path."""

    def test_unrecognized_engine_type_returns_none(self):
        module = _module_with_engine_type("phantom-legacy-engine")
        # Must not raise — returns None so dispatch falls through
        result = _get_explicit_engine_type(module)
        assert result is None


# ── _resolve_dispatch_engine — opentofu default preserved ────────────


class TestResolveDispatchEngineDefault:
    def test_no_metadata_defaults_to_opentofu(self):
        module = _module_with_execution_engine(None)
        assert _resolve_dispatch_engine(module) == "opentofu"

    def test_empty_execution_engine_defaults_to_opentofu(self):
        module = _module_with_execution_engine("")
        assert _resolve_dispatch_engine(module) == "opentofu"


class TestResolveDispatchEngineRaisesOnBogus:
    def test_bogus_execution_engine_propagates_value_error(self):
        module = _module_with_execution_engine("bogus")
        with pytest.raises(ValueError, match="Unrecognized execution_engine"):
            _resolve_dispatch_engine(module)
