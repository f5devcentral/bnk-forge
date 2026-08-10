"""Unit tests for the module status state machine.

Pure tests on the transition graph definition itself (no DB). Component
tests for `transition_module_status` (DB writes + lock interaction) live
in tests/component/test_module_state.py.
"""

from __future__ import annotations

import pytest

from models.enums import ModuleStatus
from services.module_state import (
    ALLOWED_TRANSITIONS,
    IllegalTransitionError,
    is_legal_transition,
)

# ---------------------------------------------------------------------------
# Graph hygiene — assertions about the static transition table
# ---------------------------------------------------------------------------


class TestGraphHygiene:
    def test_every_status_has_a_transition_entry(self):
        """Every ModuleStatus value must appear as a key in ALLOWED_TRANSITIONS.

        Otherwise a transition FROM that state has no defined successors and
        would always be rejected (silent footgun)."""
        missing = set(ModuleStatus) - set(ALLOWED_TRANSITIONS.keys())
        assert not missing, (
            f"ModuleStatus values missing from ALLOWED_TRANSITIONS keys: "
            f"{sorted(s.value for s in missing)}"
        )

    def test_every_target_status_is_a_known_status(self):
        """Every to-status referenced by the graph must be a real ModuleStatus.

        Catches typos in the graph definition (e.g. accidental string instead
        of enum member)."""
        all_targets = {t for targets in ALLOWED_TRANSITIONS.values() for t in targets}
        unknown = {t for t in all_targets if not isinstance(t, ModuleStatus)}
        assert not unknown, f"Non-ModuleStatus targets in graph: {unknown}"

    def test_every_status_is_reachable(self):
        """Every status (except NOT_INITIALIZED, the initial state, and
        FAILED, a defensive catch-all that no code currently writes) must
        be reachable from at least one other state. An unreachable state
        is unreachable in production too — dead code in the enum."""
        reachable: set[ModuleStatus] = set()
        for targets in ALLOWED_TRANSITIONS.values():
            reachable.update(targets)
        # Documented carve-outs:
        #   NOT_INITIALIZED — the spawn state (default for new modules).
        #     Currently also reachable via admin reset, but that's incidental.
        #   FAILED — generic catch-all defined in the enum but never written
        #     by any caller (audit 2026-05-04). Kept defensively because the
        #     status column is String(50), not an enum at the DB level — if
        #     a row somehow ends up with status='failed' the helper still
        #     needs a transition graph entry to recover from it.
        carve_outs = {ModuleStatus.NOT_INITIALIZED, ModuleStatus.FAILED}
        unreachable = set(ModuleStatus) - reachable - carve_outs
        assert not unreachable, (
            f"ModuleStatus values not reachable from any other state: "
            f"{sorted(s.value for s in unreachable)}"
        )

    def test_terminal_states_have_recovery_paths(self):
        """Terminal states (per ModuleStatus.terminal_states()) must still
        have at least one outbound edge so users can recover (retry, reset,
        destroy) instead of getting permanently stuck."""
        for terminal in ModuleStatus.terminal_states():
            edges = ALLOWED_TRANSITIONS.get(terminal, frozenset())
            assert edges, (
                f"Terminal state {terminal.value!r} has no recovery edges — "
                f"users would be permanently stuck."
            )


# ---------------------------------------------------------------------------
# is_legal_transition — pure function
# ---------------------------------------------------------------------------


class TestIsLegalTransition:
    def test_self_loop_always_legal(self):
        """Idempotent re-set is always legal (caller shouldn't have to
        special-case it)."""
        for status in ModuleStatus:
            assert is_legal_transition(status.value, status.value), (
                f"Self-loop on {status.value!r} should be legal"
            )

    def test_unknown_from_status_rejects(self):
        assert is_legal_transition("unknown_status", "applied") is False

    def test_unknown_to_status_rejects(self):
        assert is_legal_transition("applied", "warp_speed") is False

    def test_known_legal_edge(self):
        assert is_legal_transition("not_initialized", "initializing") is True

    def test_known_illegal_edge(self):
        # The classic Kleppmann example from the RFC.
        assert is_legal_transition("not_initialized", "applied") is False

    def test_apply_failed_can_retry_apply(self):
        # Common recovery path users hit constantly.
        assert is_legal_transition("apply_failed", "applying") is True

    def test_destroyed_can_redeploy(self):
        assert is_legal_transition("destroyed", "initializing") is True


# ---------------------------------------------------------------------------
# IllegalTransitionError — error shape
# ---------------------------------------------------------------------------


class TestIllegalTransitionError:
    def test_carries_module_id_and_statuses(self):
        err = IllegalTransitionError(42, "not_initialized", "applied")
        assert err.module_id == 42
        assert err.from_status == "not_initialized"
        assert err.to_status == "applied"
        assert "42" in str(err)
        assert "not_initialized" in str(err)
        assert "applied" in str(err)

    def test_can_be_caught_as_exception(self):
        with pytest.raises(IllegalTransitionError):
            raise IllegalTransitionError(1, "a", "b")
