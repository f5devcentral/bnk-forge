"""
Unit tests for SSH engine dispatch routing in task_dispatch.py.

Verifies that dispatch_init/apply/destroy correctly route to SSH engine
tasks when engine_type="ssh", and that get_engine_type returns "ssh"
for SSH modules.

No DB needed — uses mocked module objects.
"""

from unittest.mock import MagicMock, patch

import pytest

from services.execution.task_dispatch import (
    _get_explicit_engine_type,
    dispatch_apply,
    dispatch_apply_signature,
    dispatch_destroy,
    dispatch_destroy_signature,
    dispatch_init,
    dispatch_plan,
    get_engine_type,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_module(engine_type="ssh", lib_path="bare-metal/probe-dpu"):
    """Build a mock module with SSH engine type metadata."""
    module = MagicMock()
    module.id = 99
    module.library_module = MagicMock()
    module.library_module.path = lib_path
    module.library_module.engine_type = engine_type
    module.path_in_project = lib_path
    return module


def _make_module_no_engine(lib_path="infra/aws/vpc"):
    """Build a mock module without explicit engine type."""
    module = MagicMock()
    module.id = 42
    module.library_module = MagicMock()
    module.library_module.path = lib_path
    module.library_module.engine_type = None
    module.path_in_project = lib_path
    return module


# ── _get_explicit_engine_type ─────────────────────────────────────────


class TestGetExplicitEngineType:
    def test_returns_ssh_when_engine_type_is_ssh(self):
        result = _get_explicit_engine_type(_make_module(engine_type="ssh"))
        assert result == "ssh"

    def test_returns_ssh_case_insensitive(self):
        result = _get_explicit_engine_type(_make_module(engine_type="SSH"))
        assert result == "ssh"

    def test_returns_ssh_with_whitespace(self):
        result = _get_explicit_engine_type(_make_module(engine_type=" ssh "))
        assert result == "ssh"

    def test_returns_none_when_no_engine_type(self):
        result = _get_explicit_engine_type(_make_module_no_engine())
        assert result is None

    def test_returns_none_when_no_library_module(self):
        module = MagicMock()
        module.library_module = None
        result = _get_explicit_engine_type(module)
        assert result is None

    def test_returns_none_for_unrecognized_engine_type(self):
        result = _get_explicit_engine_type(_make_module(engine_type="unknown_engine"))
        assert result is None


# ── get_engine_type ───────────────────────────────────────────────────


class TestGetEngineTypeSSH:
    def test_get_engine_type_returns_ssh_for_ssh_module(self):
        module = _make_module(engine_type="ssh")
        assert get_engine_type(module) == "ssh"

    def test_get_engine_type_returns_ssh_not_opentofu(self):
        module = _make_module(engine_type="ssh")
        result = get_engine_type(module)
        assert result != "opentofu"
        assert result != "kubernetes"

    def test_get_engine_type_returns_opentofu_without_ssh_metadata(self):
        module = _make_module_no_engine()
        assert get_engine_type(module) == "opentofu"


# ── dispatch_init → SSH ──────────────────────────────────────────────


class TestDispatchInitSSH:
    def test_dispatch_init_routes_to_ssh_when_engine_type_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_init") as mock_ssh_init:
            mock_ssh_init.delay.return_value = MagicMock(id="ssh-init-1")
            result = dispatch_init(10, _make_module())
            mock_ssh_init.delay.assert_called_once_with(10, 99, auto_apply=False)

    def test_dispatch_init_passes_auto_apply_to_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_init") as mock_ssh_init:
            mock_ssh_init.delay.return_value = MagicMock(id="ssh-init-2")
            dispatch_init(10, _make_module(), auto_apply=True)
            mock_ssh_init.delay.assert_called_once_with(10, 99, auto_apply=True)

    def test_dispatch_init_does_not_route_to_opentofu_for_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_init") as mock_ssh, \
             patch("tasks.opentofu_tasks.run_opentofu_init") as mock_ot:
            mock_ssh.delay.return_value = MagicMock(id="ssh-1")
            dispatch_init(10, _make_module())
            mock_ot.delay.assert_not_called()

    def test_dispatch_init_does_not_route_to_k8s_for_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_init") as mock_ssh, \
             patch("tasks.kubernetes_tasks.run_k8s_init") as mock_k8s:
            mock_ssh.delay.return_value = MagicMock(id="ssh-1")
            dispatch_init(10, _make_module())
            mock_k8s.delay.assert_not_called()


# ── dispatch_plan → SSH ──────────────────────────────────────────────


class TestDispatchPlanSSH:
    def test_dispatch_plan_delegates_to_ssh_init_for_probe(self):
        """SSH plan is a lightweight probe — delegates to init."""
        with patch("tasks.ssh_tasks.run_ssh_init") as mock_ssh_init:
            mock_ssh_init.delay.return_value = MagicMock(id="ssh-plan-1")
            dispatch_plan(10, _make_module())
            mock_ssh_init.delay.assert_called_once_with(10, 99, auto_apply=False)


# ── dispatch_apply → SSH ─────────────────────────────────────────────


class TestDispatchApplySSH:
    def test_dispatch_apply_routes_to_ssh_when_engine_type_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_apply") as mock_ssh_apply:
            mock_ssh_apply.delay.return_value = MagicMock(id="ssh-apply-1")
            result = dispatch_apply(10, _make_module())
            mock_ssh_apply.delay.assert_called_once_with(10, 99)

    def test_dispatch_apply_does_not_route_to_opentofu_for_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_apply") as mock_ssh, \
             patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_ot:
            mock_ssh.delay.return_value = MagicMock(id="ssh-1")
            dispatch_apply(10, _make_module())
            mock_ot.delay.assert_not_called()

    def test_dispatch_apply_signature_routes_to_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_apply") as mock_ssh_apply:
            mock_ssh_apply.s.return_value = MagicMock(name="sig")
            dispatch_apply_signature(10, _make_module())
            mock_ssh_apply.s.assert_called_once_with(10, 99)


# ── dispatch_destroy → SSH ───────────────────────────────────────────


class TestDispatchDestroySSH:
    def test_dispatch_destroy_routes_to_ssh_when_engine_type_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_destroy") as mock_ssh_destroy:
            mock_ssh_destroy.delay.return_value = MagicMock(id="ssh-destroy-1")
            result = dispatch_destroy(10, _make_module())
            mock_ssh_destroy.delay.assert_called_once_with(10, 99)

    def test_dispatch_destroy_does_not_route_to_opentofu_for_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_destroy") as mock_ssh, \
             patch("tasks.opentofu_tasks.run_opentofu_destroy") as mock_ot:
            mock_ssh.delay.return_value = MagicMock(id="ssh-1")
            dispatch_destroy(10, _make_module())
            mock_ot.delay.assert_not_called()

    def test_dispatch_destroy_signature_routes_to_ssh(self):
        with patch("tasks.ssh_tasks.run_ssh_destroy") as mock_ssh_destroy:
            mock_ssh_destroy.s.return_value = MagicMock(name="sig")
            dispatch_destroy_signature(10, _make_module())
            mock_ssh_destroy.s.assert_called_once_with(10, 99)


# ── Cross-engine isolation ───────────────────────────────────────────


class TestCrossEngineIsolation:
    """Ensure SSH routing doesn't interfere with other engine types."""

    def test_non_ssh_module_does_not_route_to_ssh_init(self):
        with patch("tasks.opentofu_tasks.run_opentofu_init") as mock_ot:
            mock_ot.delay.return_value = MagicMock(id="ot-1")
            dispatch_init(1, _make_module_no_engine())
            mock_ot.delay.assert_called_once()

    def test_ansible_module_does_not_route_to_ssh(self):
        ansible_mod = _make_module(engine_type="ansible", lib_path="ansible/some-playbook")
        with patch("tasks.ansible_tasks.run_ansible_init") as mock_ans:
            mock_ans.delay.return_value = MagicMock(id="ans-1")
            dispatch_init(1, ansible_mod)
            mock_ans.delay.assert_called_once()
