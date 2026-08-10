"""
Tests for services.execution.task_dispatch — Celery task routing.

Covers metadata-driven routing behavior.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from services.execution.task_dispatch import (
    dispatch_apply,
    dispatch_apply_signature,
    dispatch_destroy,
    dispatch_init,
    dispatch_plan,
    get_engine_type,
)

# ── Helpers ──────────────────────────────────────────────────────────────

def _make_module(
    lib_path="infra/aws/vpc",
    path_in_project=None,
    *,
    execution_engine=None,
    engine_type=None,
    module_source_kind="git_catalog",
):
    module = SimpleNamespace(
        id=42,
        path_in_project=path_in_project or lib_path,
        library_module=SimpleNamespace(
            path=lib_path,
            execution_engine=execution_engine,
            engine_type=engine_type,
            module_source_kind=module_source_kind,
        ),
    )
    return module


# ── dispatch_init ────────────────────────────────────────────────────────

class TestDispatchInit:
    def test_dispatches_to_opentofu_for_official_catalog_without_k8s_execution_metadata(self):
        with patch("tasks.opentofu_tasks.run_opentofu_init") as mock_ot_init:
            mock_ot_init.delay.return_value = MagicMock(id="task-1")
            dispatch_init(1, _make_module(execution_engine="opentofu"), auto_apply=True)
            mock_ot_init.delay.assert_called_once()

    def test_dispatches_to_k8s_for_official_catalog_kubernetes_direct(self):
        with patch("tasks.kubernetes_tasks.run_k8s_init") as mock_k8s_init:
            mock_k8s_init.delay.return_value = MagicMock(id="task-2")
            dispatch_init(1, _make_module(lib_path="k8s/bnk", execution_engine="kubernetes-direct"), auto_apply=False)
            mock_k8s_init.delay.assert_called_once()

    def test_dispatches_to_k8s_for_official_catalog_operator_engine(self):
        with patch("tasks.kubernetes_tasks.run_k8s_init") as mock_k8s_init:
            mock_k8s_init.delay.return_value = MagicMock(id="task-2-op")
            dispatch_init(1, _make_module(lib_path="k8s/bnk", execution_engine="operator"), auto_apply=False)
            mock_k8s_init.delay.assert_called_once()

    def test_dispatches_to_ansible_when_explicit_metadata_present(self):
        with patch("tasks.ansible_tasks.run_ansible_init") as mock_ansible_init:
            mock_ansible_init.delay.return_value = MagicMock(id="task-ans-init")
            dispatch_init(1, _make_module(execution_engine="ansible"), auto_apply=False)
            mock_ansible_init.delay.assert_called_once_with(1, 42, auto_apply=False)

    def test_engine_type_kubernetes_dispatches_to_k8s_regardless_of_source_kind(self):
        module = _make_module(
            lib_path="bnk/gateway",
            module_source_kind="git_catalog",
            execution_engine=None,
            engine_type="kubernetes",
        )

        with patch("tasks.kubernetes_tasks.run_k8s_init") as mock_k8s_init, patch(
            "tasks.opentofu_tasks.run_opentofu_init"
        ) as mock_ot_init:
            mock_k8s_init.delay.return_value = MagicMock(id="task-k8s-engine-type")
            dispatch_init(1, module, auto_apply=False)
            mock_k8s_init.delay.assert_called_once()
            mock_ot_init.delay.assert_not_called()


# ── dispatch_plan ────────────────────────────────────────────────────────

class TestDispatchPlan:
    def test_dispatches_to_opentofu(self):
        with patch("tasks.opentofu_tasks.run_opentofu_plan") as mock_ot:
            mock_ot.delay.return_value = MagicMock(id="task-plan-ot")
            dispatch_plan(1, _make_module())
            mock_ot.delay.assert_called_once_with(1, 42)

    def test_dispatches_to_ansible_when_explicit_metadata_present(self):
        with patch("tasks.ansible_tasks.run_ansible_plan") as mock_ansible_plan:
            mock_ansible_plan.delay.return_value = MagicMock(id="task-plan-ans")
            dispatch_plan(1, _make_module(execution_engine="ansible"))
            mock_ansible_plan.delay.assert_called_once_with(1, 42)

    def test_dispatches_to_k8s_for_kubernetes_execution_engine(self):
        with patch("tasks.kubernetes_tasks.run_k8s_plan") as mock_k8s_plan:
            mock_k8s_plan.delay.return_value = MagicMock(id="task-plan-k8s")
            dispatch_plan(1, _make_module(lib_path="k8s/bnk", execution_engine="kubernetes-direct"))
            mock_k8s_plan.delay.assert_called_once_with(1, 42)


# ── dispatch_apply ───────────────────────────────────────────────────────

class TestDispatchApply:
    def test_dispatches_to_opentofu(self):
        with patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_ot:
            mock_ot.delay.return_value = MagicMock(id="task-3")
            dispatch_apply(1, _make_module(execution_engine="opentofu"))
            mock_ot.delay.assert_called_once()

    def test_apply_signature_forwards_force_new_plan_to_opentofu(self):
        with patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_ot:
            mock_ot.s.return_value = MagicMock(name="sig")
            dispatch_apply_signature(1, _make_module(execution_engine="opentofu"), force_new_plan=True)
            mock_ot.s.assert_called_once_with(1, 42, force_new_plan=True)

    def test_apply_signature_defaults_force_new_plan_false(self):
        with patch("tasks.opentofu_tasks.run_opentofu_apply") as mock_ot:
            mock_ot.s.return_value = MagicMock(name="sig")
            dispatch_apply_signature(1, _make_module(execution_engine="opentofu"))
            mock_ot.s.assert_called_once_with(1, 42, force_new_plan=False)

    def test_dispatches_to_k8s(self):
        with patch("tasks.kubernetes_tasks.run_k8s_apply") as mock_k8s:
            mock_k8s.delay.return_value = MagicMock(id="task-4")
            dispatch_apply(1, _make_module(lib_path="k8s/bnk", execution_engine="kubernetes-direct"))
            mock_k8s.delay.assert_called_once()

    def test_dispatches_to_ansible_when_explicit_metadata_present(self):
        with patch("tasks.ansible_tasks.run_ansible_apply") as mock_ansible_apply:
            mock_ansible_apply.delay.return_value = MagicMock(id="task-ans-apply")
            dispatch_apply(1, _make_module(execution_engine="ansible"))
            mock_ansible_apply.delay.assert_called_once_with(1, 42)


# ── dispatch_destroy ─────────────────────────────────────────────────────

class TestDispatchDestroy:
    def test_dispatches_to_opentofu(self):
        with patch("tasks.opentofu_tasks.run_opentofu_destroy") as mock_ot:
            mock_ot.delay.return_value = MagicMock(id="task-5")
            dispatch_destroy(1, _make_module(execution_engine="opentofu"))
            mock_ot.delay.assert_called_once()

    def test_dispatches_to_k8s(self):
        with patch("tasks.kubernetes_tasks.run_k8s_destroy") as mock_k8s:
            mock_k8s.delay.return_value = MagicMock(id="task-6")
            dispatch_destroy(1, _make_module(lib_path="k8s/bnk", execution_engine="kubernetes-direct"))
            mock_k8s.delay.assert_called_once()

    def test_dispatches_to_ansible_when_explicit_metadata_present(self):
        with patch("tasks.ansible_tasks.run_ansible_destroy") as mock_ansible_destroy:
            mock_ansible_destroy.delay.return_value = MagicMock(id="task-ans-destroy")
            dispatch_destroy(1, _make_module(execution_engine="ansible"))
            mock_ansible_destroy.delay.assert_called_once_with(1, 42)


# ── get_engine_type ──────────────────────────────────────────────────────

class TestGetEngineType:
    def test_returns_execution_engine_alias_for_ansible(self):
        assert get_engine_type(_make_module(execution_engine="ansible")) == "ansible"

    def test_returns_kubernetes_for_kubernetes_direct_execution_engine(self):
        assert get_engine_type(_make_module(execution_engine="kubernetes-direct")) == "kubernetes"

    def test_engine_type_alias_still_supported_for_legacy_callers(self):
        assert (
            get_engine_type(
                _make_module(
                    execution_engine=None,
                    engine_type="kubernetes",
                    module_source_kind="builtin",
                )
            )
            == "kubernetes"
        )

    def test_engine_type_kubernetes_resolves_to_kubernetes(self):
        assert (
            get_engine_type(
                _make_module(
                    execution_engine=None,
                    engine_type="kubernetes",
                    module_source_kind="git_catalog",
                )
            )
            == "kubernetes"
        )

    def test_builtin_without_explicit_engine_defaults_to_opentofu(self):
        assert (
            get_engine_type(_make_module(module_source_kind="builtin", execution_engine=None, engine_type=None))
            == "opentofu"
        )

    def test_official_catalog_without_explicit_engine_defaults_to_opentofu(self):
        assert get_engine_type(_make_module(execution_engine=None, engine_type=None, module_source_kind="git_catalog")) == "opentofu"
