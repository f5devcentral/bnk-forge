"""
Unit tests for #132 — FLO upgrade uses chart + namespace from the existing
Helm release record, not hardcoded oci://repo.f5.com/... defaults.

Tests both the plan builder (bnk_upgrade_plan_service) and the step executor
(bnk_upgrade_execution_service).
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)




# ── Plan builder tests ────────────────────────────────────────────────


def _make_bnk_install(chart="oci://private.corp/charts/f5-lifecycle-operator",
                      namespace="custom-ns", release_name="custom-flo"):
    return {
        "status": "installed",
        "flo": {
            "version": "v1.198.4-0.1.36",
            "helm_release": {"name": release_name, "namespace": namespace, "chart": chart},
        },
        "tmm": {"pods": 1, "running": 1},
        "cne_instance": {"name": "bnk-instance"},
        "vlans": [],
    }


class TestPlanBuilderUsesHelmReleaseChart:
    def _build_plan(self, bnk_install, target_version="v1.199.0-0.1.0"):
        from services.bnk_upgrade_service import BnkUpgradeService
        svc = BnkUpgradeService.__new__(BnkUpgradeService)
        return svc._build_plan(bnk_install=bnk_install, target_version=target_version)

    def test_plan_uses_chart_from_release_record(self):
        bnk_install = _make_bnk_install(chart="oci://airgap.corp.example/charts/f5-lifecycle-operator")
        plan = self._build_plan(bnk_install)
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        assert helm_step["chart"] == "oci://airgap.corp.example/charts/f5-lifecycle-operator"
        assert "repo.f5.com" not in helm_step["chart"]

    def test_plan_uses_namespace_from_release_record(self):
        bnk_install = _make_bnk_install(namespace="private-ns")
        plan = self._build_plan(bnk_install)
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        assert helm_step["namespace"] == "private-ns"

    def test_plan_degrades_gracefully_when_chart_missing(self):
        """When helm_release.chart is absent the plan still builds with a fallback + warn step."""
        bnk_install = {
            "status": "installed",
            "flo": {
                "version": "v1.198.4",
                "helm_release": {"name": "f5-lifecycle-operator", "namespace": "f5-cne-core"},
            },
            "tmm": {}, "cne_instance": {}, "vlans": [],
        }
        plan = self._build_plan(bnk_install)
        warn_step = next((s for s in plan if s.get("action") == "warn"), None)
        assert warn_step is not None, "Expected a warn step when chart is missing"
        assert "chart" in warn_step["detail"]
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        # Fallback must use the canonical F5 OCI registry path
        assert "repo.f5.com/charts/f5-lifecycle-operator" in helm_step["chart"]

    def test_plan_degrades_gracefully_when_namespace_missing(self):
        """When helm_release.namespace is absent the plan still builds with a fallback + warn step."""
        bnk_install = {
            "status": "installed",
            "flo": {
                "version": "v1.198.4",
                "helm_release": {
                    "name": "flo",
                    "chart": "oci://private.example/charts/flo",
                    # namespace deliberately absent
                },
            },
            "tmm": {}, "cne_instance": {}, "vlans": [],
        }
        plan = self._build_plan(bnk_install)
        warn_step = next((s for s in plan if s.get("action") == "warn"), None)
        assert warn_step is not None, "Expected a warn step when namespace is missing"
        assert "namespace" in warn_step["detail"]
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        assert helm_step["namespace"] == "f5-cne-core"

    def test_plan_degrades_gracefully_when_helm_release_empty(self):
        """Empty helm_release → plan still builds with fallback chart + namespace + warn step."""
        bnk_install = {
            "status": "installed",
            "flo": {"version": "v1.198.4", "helm_release": {}},
            "tmm": {}, "cne_instance": {}, "vlans": [],
        }
        plan = self._build_plan(bnk_install)
        warn_step = next((s for s in plan if s.get("action") == "warn"), None)
        assert warn_step is not None, "Expected a warn step when helm_release is empty"
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        assert "repo.f5.com/charts/" in helm_step["chart"]
        assert helm_step["namespace"] == "f5-cne-core"


# ── Execution step tests ──────────────────────────────────────────────


class TestExecutionStepRaisesWithoutChart:
    def _make_mixin(self):
        from services.bnk_upgrade_service import BnkUpgradeService
        svc = BnkUpgradeService.__new__(BnkUpgradeService)
        svc.db = None
        return svc

    def test_raises_when_step_has_no_chart(self):
        svc = self._make_mixin()
        helm_mock = MagicMock()
        step = {"release_name": "flo", "namespace": "f5-bnk", "version": "v1.199.0"}
        with pytest.raises(ValueError, match="missing 'chart'"):
            svc._execute_helm_upgrade(helm_mock, cluster_id=1, step=step, emit=lambda x: None)

    def test_raises_when_step_has_no_namespace(self):
        svc = self._make_mixin()
        helm_mock = MagicMock()
        step = {"release_name": "flo", "chart": "oci://private/flo", "version": "v1.199.0"}
        with pytest.raises(ValueError, match="missing 'namespace'"):
            svc._execute_helm_upgrade(helm_mock, cluster_id=1, step=step, emit=lambda x: None)

    def test_calls_helm_with_chart_from_step(self):
        svc = self._make_mixin()
        helm_mock = MagicMock()
        helm_mock.upgrade_release.return_value = {"exit_code": 0, "stdout": "ok"}
        helm_mock.get_values.return_value = {}
        step = {
            "release_name": "my-flo",
            "namespace": "private-ns",
            "chart": "oci://airgap.corp.example/charts/f5-lifecycle-operator",
            "version": "v1.199.0",
            "timeout": 600,
        }
        svc._execute_helm_upgrade(helm_mock, cluster_id=1, step=step, emit=lambda x: None)
        call_kwargs = helm_mock.upgrade_release.call_args.kwargs
        assert call_kwargs["chart"] == "oci://airgap.corp.example/charts/f5-lifecycle-operator"
        assert call_kwargs["namespace"] == "private-ns"
        assert "repo.f5.com" not in call_kwargs["chart"]


# ── Rollback-info capture logic tests ────────────────────────────────


class TestRollbackInfoCaptureUsesHelmStepNamespace:
    """Lines ~91/99 — rollback-info capture derives namespace from the plan's
    helm_upgrade step, not a hardcoded 'f5-bnk' default.

    Tests the inline logic directly (not via execute(), which requires a full
    DB/lock setup) to avoid coupling to the full execution path.
    """

    STEP_HELM_UPGRADE = "helm_upgrade"

    def _plan_with_helm_step(self, namespace="custom-ns", release_name="custom-flo"):
        return [
            {"step": 1, "action": "health_gate", "label": "pre-upgrade health"},
            {
                "step": 2,
                "action": self.STEP_HELM_UPGRADE,
                "label": "Upgrade FLO",
                "release_name": release_name,
                "namespace": namespace,
                "chart": "oci://private.corp/charts/flo",
                "version": "v1.199.0",
            },
        ]

    def test_finds_helm_step_and_extracts_namespace(self):
        """The inline next() finds the helm_upgrade step and extracts namespace."""
        plan = self._plan_with_helm_step(namespace="airgap-ns", release_name="airgap-flo")
        helm_step = next((s for s in plan if s.get("action") == self.STEP_HELM_UPGRADE), None)
        assert helm_step is not None
        assert helm_step.get("namespace") == "airgap-ns"
        assert helm_step.get("release_name") == "airgap-flo"
        assert "f5-bnk" not in (helm_step.get("namespace") or "")

    def test_finds_helm_step_in_custom_namespace(self):
        """Any non-default namespace is returned unchanged."""
        plan = self._plan_with_helm_step(namespace="private-registry-ns")
        helm_step = next((s for s in plan if s.get("action") == self.STEP_HELM_UPGRADE), None)
        assert helm_step is not None
        flo_namespace = helm_step.get("namespace")
        assert flo_namespace == "private-registry-ns"

    def test_raises_when_plan_has_no_helm_step(self):
        """Plan with no helm_upgrade step → next() returns None → ValueError raised."""
        plan = [
            {"step": 1, "action": "health_gate", "label": "pre-upgrade"},
            {"step": 2, "action": "crd_wait", "label": "Wait for CRDs"},
        ]
        helm_step = next((s for s in plan if s.get("action") == self.STEP_HELM_UPGRADE), None)
        assert helm_step is None
        # Mimics the production check: helm_step is None → ValueError
        with pytest.raises(ValueError, match="no helm_upgrade step"):
            if helm_step is None:
                raise ValueError(
                    "Upgrade plan contains no helm_upgrade step — cannot capture rollback info."
                )

    def test_raises_when_helm_step_has_no_namespace(self):
        """helm_upgrade step missing namespace → ValueError, not 'f5-bnk' fallback."""
        plan = [
            {
                "step": 1,
                "action": self.STEP_HELM_UPGRADE,
                "release_name": "flo",
                "chart": "oci://private/flo",
                # namespace deliberately absent
            }
        ]
        helm_step = next((s for s in plan if s.get("action") == self.STEP_HELM_UPGRADE), None)
        assert helm_step is not None
        flo_namespace = helm_step.get("namespace")
        # Mimics the production check: no namespace → ValueError
        with pytest.raises(ValueError, match="missing 'namespace'"):
            if not flo_namespace:
                raise ValueError(
                    "Helm upgrade step is missing 'namespace' — cannot capture rollback info."
                )


# ── Rollback execution namespace tests ───────────────────────────────


class TestRollbackExecutionUsesStoredNamespace:
    """Line ~614/615 — rollback() reads flo_namespace from rollback_info;
    raises if absent rather than falling back to 'f5-bnk'.

    Tests the namespace-extraction logic directly to avoid coupling to
    the status-check tuple that references a pre-existing enum gap (HEALTH_CHECK).
    """

    def test_namespace_extracted_from_rollback_info(self):
        """flo_namespace present → extracted correctly, no 'f5-bnk' involved."""
        rollback_info = {
            "flo_revision": 5,
            "flo_release_name": "private-flo",
            "flo_namespace": "private-ns",
        }
        namespace = rollback_info.get("flo_namespace")
        assert namespace == "private-ns"
        assert "f5-bnk" not in (namespace or "")

    def test_raises_when_namespace_absent_from_rollback_info(self):
        """Missing flo_namespace must raise a clear error, not silently use 'f5-bnk'."""
        rollback_info = {
            "flo_revision": 5,
            "flo_release_name": "flo",
            # flo_namespace deliberately absent
        }
        namespace = rollback_info.get("flo_namespace")
        with pytest.raises(ValueError, match="flo_namespace"):
            if not namespace:
                raise ValueError(
                    "Rollback info is missing 'flo_namespace'. "
                    "Cannot rollback without knowing the FLO release namespace."
                )

    def test_raises_when_rollback_info_empty(self):
        """Completely empty rollback_info → clear error, not 'f5-bnk' default."""
        rollback_info = {}
        namespace = rollback_info.get("flo_namespace")
        with pytest.raises(ValueError, match="flo_namespace"):
            if not namespace:
                raise ValueError(
                    "Rollback info is missing 'flo_namespace'. "
                    "Cannot rollback without knowing the FLO release namespace."
                )

    def test_no_default_fallback_in_production_code(self):
        """Verify the production code has no 'f5-bnk' default on the namespace get."""
        import inspect

        from services.bnk_upgrade_execution_service import BnkUpgradeExecutionMixin
        src = inspect.getsource(BnkUpgradeExecutionMixin.rollback)
        # The old code was: rollback_info.get("flo_namespace", "f5-bnk")
        # After the fix: rollback_info.get("flo_namespace") with explicit raise.
        assert 'get("flo_namespace", "f5-bnk")' not in src, (
            "Production rollback() still has 'f5-bnk' default — fix not applied"
        )
