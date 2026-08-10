"""
Unit tests for #389/#390 — BNK upgrade pre-checks, plan generation, and
version detection are install-shape-aware (FLO not required on a
helm/manual install), and the k8s-compatibility check compares versions
numerically (not lexically) and treats "newer than tested" as a warning.
"""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from services.bnk_upgrade_service import BnkUpgradeService, detect_current_bnk_version  # noqa: E402


def _svc() -> BnkUpgradeService:
    svc = BnkUpgradeService.__new__(BnkUpgradeService)
    svc.db = MagicMock()
    return svc


# ── (d) Version-detection fallback from helm chart when FLO absent ────────


class TestDetectCurrentBnkVersion:
    def test_prefers_flo_version_when_present(self):
        bnk_install = {
            "flo": {"version": "v1.198.4-0.1.36", "helm_release": {"chart": "f5-lifecycle-operator-1.198.4"}},
        }
        assert detect_current_bnk_version(bnk_install) == "v1.198.4-0.1.36"

    def test_falls_back_to_helm_chart_version_when_flo_absent(self):
        bnk_install = {
            "install_shape": "helm",
            "flo": {
                "version": None,
                "helm_release": {"name": "f5ingress", "namespace": "bnk-app1", "chart": "f5ingress-2.21.13"},
            },
        }
        assert detect_current_bnk_version(bnk_install) == "2.21.13"

    def test_returns_none_when_helm_chart_version_absent(self):
        """Helm release secret carries only a revision ("4"), no chart version.
        Controller/TMM image tags are raw semver (e.g. v14.59.1-0.0.70) and
        cannot be reliably compared to BNK release versions — return None."""
        bnk_install = {
            "install_shape": "helm",
            "flo": {
                "version": None,
                "helm_release": {"name": "f5ingress", "namespace": "bnk-app1", "version": "4", "status": "deployed"},
            },
            "controller": {"pods": 1, "running": 1, "version": "v14.59.1-0.0.70"},
            "tmm": {"pods": 1, "running": 1, "version": "v10.159.3-0.1.5"},
        }
        assert detect_current_bnk_version(bnk_install) is None

    def test_returns_none_when_controller_absent_and_no_chart_version(self):
        bnk_install = {
            "install_shape": "helm",
            "flo": {"version": None, "helm_release": {"name": "f5ingress", "version": "4"}},
            "controller": {"pods": 0, "running": 0, "version": None},
            "tmm": {"pods": 1, "running": 1, "version": "v10.159.3-0.1.5"},
        }
        assert detect_current_bnk_version(bnk_install) is None

    def test_returns_none_when_nothing_detectable(self):
        bnk_install = {"install_shape": "unknown", "flo": {"version": None, "helm_release": {}}}
        assert detect_current_bnk_version(bnk_install) is None


# ── (a)/(b) Pre-checks: helm-shape ready, FLO-shape unchanged ─────────────


class TestPreChecksInstallShapeAware:
    def _checks(self, bnk_install, current_version, target_version="v2.22.0"):
        svc = _svc()
        return svc._run_pre_checks(
            bnk_install=bnk_install,
            cluster_info={"version": "v1.30.0"},
            current_version=current_version,
            target_version=target_version,
        )

    def test_helm_shape_no_critical_failures(self):
        """(a) Reproduces the live helm/kind install: no FLO, TMM healthy —
        Validate & Plan must not hard-fail on version_detected."""
        bnk_install = {
            "status": "installed",
            "install_shape": "helm",
            "flo": {
                "version": None,
                "pods": 0,
                "running": 0,
                "helm_release": {"name": "f5ingress", "namespace": "bnk-app1", "chart": "f5ingress-2.21.13"},
            },
            "tmm": {"pods": 4, "running": 4},
            "vlans": [],
        }
        current_version = detect_current_bnk_version(bnk_install)
        checks = self._checks(bnk_install, current_version)

        critical_failures = [c for c in checks if c["status"] == "fail" and c.get("critical", True)]
        assert critical_failures == [], critical_failures

        by_name = {c["name"]: c for c in checks}
        assert by_name["version_detected"]["status"] == "pass"
        assert by_name["flo_health"]["status"] == "pass"
        assert by_name["flo_health"]["critical"] is False

    def test_flo_shape_unchanged_version_detected_fails_when_absent(self):
        """(b) FLO-shape regression guard: no FLO pods + no version = still
        a critical failure (unlike the helm-shape warn path)."""
        bnk_install = {
            "status": "partial",
            "install_shape": "flo",
            "flo": {"version": None, "pods": 0, "running": 0, "helm_release": {}},
            "tmm": {"pods": 0, "running": 0},
            "vlans": [],
        }
        checks = self._checks(bnk_install, current_version=None)
        by_name = {c["name"]: c for c in checks}
        assert by_name["version_detected"]["status"] == "fail"
        assert by_name["version_detected"]["critical"] is True

    def test_flo_shape_flo_health_warns_on_missing_pods(self):
        """FLO-shape flo_health must still warn (not silently pass) when FLO
        pods are expected but not running — byte-for-byte prior behavior."""
        bnk_install = {
            "status": "partial",
            "install_shape": "flo",
            "flo": {"version": "v1.198.4", "pods": 2, "running": 0, "helm_release": {"name": "flo"}},
            "tmm": {"pods": 1, "running": 1},
            "vlans": [],
        }
        checks = self._checks(bnk_install, current_version="v1.198.4")
        by_name = {c["name"]: c for c in checks}
        assert by_name["flo_health"]["status"] == "warn"

    def test_missing_install_shape_key_defaults_to_flo_behavior(self):
        """Old scan payloads / test fixtures without install_shape must keep
        the original FLO-centric semantics."""
        bnk_install = {
            "status": "partial",
            "flo": {"version": None, "pods": 0, "running": 0, "helm_release": {}},
            "tmm": {"pods": 0, "running": 0},
            "vlans": [],
        }
        checks = self._checks(bnk_install, current_version=None)
        by_name = {c["name"]: c for c in checks}
        assert by_name["version_detected"]["status"] == "fail"
        assert by_name["version_detected"]["critical"] is True


# ── (c) k8s_compat: numeric compare + newer-than-max is a warn ────────────


class TestK8sCompatNumericCompare:
    def _k8s_check(self, k8s_version, target_version="v2.21.13"):
        svc = _svc()
        bnk_install = {
            "status": "installed",
            "flo": {"version": "v2.9.0", "pods": 1, "running": 1, "helm_release": {}},
            "tmm": {"pods": 1, "running": 1},
            "vlans": [],
        }
        checks = svc._run_pre_checks(
            bnk_install=bnk_install,
            cluster_info={"version": k8s_version},
            current_version="v2.9.0",
            target_version=target_version,
        )
        return next(c for c in checks if c["name"] == "k8s_compat")

    def test_1_9_is_older_than_1_30_numerically(self):
        """Lexical '1.30' <= '1.9' is True; numerically 1.9 < 1.30 must fail."""
        check = self._k8s_check("v1.9.0")
        assert check["status"] == "fail"
        assert check["critical"] is True

    def test_1_36_is_newer_than_1_31_and_only_warns(self):
        """BNK 2.3's range is 1.30-1.33; 1.36 is newer-than-max => warn, not
        a hard fail (the live lab-cluster repro for #390)."""
        check = self._k8s_check("v1.36.0")
        assert check["status"] == "warn"
        assert check["critical"] is False

    def test_within_range_passes(self):
        check = self._k8s_check("v1.31.0")
        assert check["status"] == "pass"
        assert check["critical"] is True

    def test_older_than_min_fails(self):
        check = self._k8s_check("v1.20.0")
        assert check["status"] == "fail"
        assert check["critical"] is True


# ── _build_plan: helm-shape targets the discovered release ────────────────


class TestBuildPlanInstallShapeAware:
    def _plan(self, bnk_install, target_version="v2.22.0"):
        svc = _svc()
        return svc._build_plan(bnk_install=bnk_install, target_version=target_version)

    def test_helm_shape_targets_discovered_release_not_flo_chart(self):
        bnk_install = {
            "status": "installed",
            "install_shape": "helm",
            "flo": {
                "version": None,
                "helm_release": {"name": "f5ingress", "namespace": "bnk-app1", "chart": "f5ingress-2.21.13"},
            },
            "tmm": {}, "cne_instance": {}, "vlans": [],
        }
        plan = self._plan(bnk_install)
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        assert helm_step["release_name"] == "f5ingress"
        assert helm_step["namespace"] == "bnk-app1"
        assert helm_step["chart"] == "f5ingress-2.21.13"
        assert "f5-lifecycle-operator" not in helm_step["chart"]
        # No fallback warning — the release was fully discovered.
        assert not any(s.get("action") == "warn" for s in plan)

    def test_helm_shape_with_nothing_discovered_warns_without_flo_fallback(self):
        bnk_install = {
            "status": "partial",
            "install_shape": "helm",
            "flo": {"version": None, "helm_release": {}},
            "tmm": {}, "cne_instance": {}, "vlans": [],
        }
        plan = self._plan(bnk_install)
        warn_step = next((s for s in plan if s.get("action") == "warn"), None)
        assert warn_step is not None
        assert "no well-known chart" in warn_step["detail"]
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        # Must NOT silently assume the FLO OCI chart for a non-FLO shape.
        assert helm_step["chart"] is None

    def test_flo_shape_unchanged_fallback_to_canonical_oci_chart(self):
        """Regression guard: FLO-shape plan generation is byte-for-byte
        unchanged — missing chart still falls back to repo.f5.com."""
        bnk_install = {
            "status": "installed",
            "install_shape": "flo",
            "flo": {"version": "v1.198.4", "helm_release": {}},
            "tmm": {}, "cne_instance": {}, "vlans": [],
        }
        plan = self._plan(bnk_install)
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        assert "repo.f5.com/charts/f5-lifecycle-operator" in helm_step["chart"]
        assert helm_step["namespace"] == "f5-cne-core"


# ── Execution guard: non-FLO shapes fail clean, no crash ──────────────────


class TestExecutionGuardsNonFloShape:
    def _make_svc_with_upgrade(self, install_shape, status="ready"):
        from models.enums import BnkUpgradeStatus

        svc = _svc()
        upgrade = MagicMock()
        upgrade.status = BnkUpgradeStatus.READY if status == "ready" else status
        upgrade.from_bnk_info = {"install_shape": install_shape}
        upgrade.current_step = 0
        svc.db.query.return_value.filter.return_value.first.return_value = upgrade
        return svc, upgrade

    def test_helm_shape_execution_fails_clean_no_crash(self):
        from models.enums import BnkUpgradeStatus

        svc, upgrade = self._make_svc_with_upgrade("helm")
        result = svc.execute_upgrade(upgrade_id=1)

        assert result is upgrade
        assert upgrade.status == BnkUpgradeStatus.FAILED
        assert "not yet supported" in upgrade.error_message
        assert "helm" in upgrade.error_message

    def test_unknown_shape_execution_fails_clean_no_crash(self):
        from models.enums import BnkUpgradeStatus

        svc, upgrade = self._make_svc_with_upgrade("unknown")
        result = svc.execute_upgrade(upgrade_id=1)

        assert result is upgrade
        assert upgrade.status == BnkUpgradeStatus.FAILED

    @patch("services.bnk_upgrade_execution_service.HelmService")
    def test_flo_shape_execution_proceeds_past_guard(self, mock_helm):
        """FLO-shape upgrades must not be blocked by the new guard — they
        proceed into the normal step loop (which then fails naturally with
        no plan steps defined, proving the guard itself didn't trip)."""
        svc, upgrade = self._make_svc_with_upgrade("flo")
        upgrade.plan = []
        upgrade.step_results = []
        mock_helm.return_value.get_history.return_value = []

        result = svc.execute_upgrade(upgrade_id=1)

        assert result is upgrade
        # Guard did not fire — error message (if any) is not the shape-guard message.
        if upgrade.error_message:
            assert "not yet supported for install_shape" not in upgrade.error_message

    def test_missing_install_shape_defaults_to_flo_and_proceeds(self):
        """Legacy upgrade records without from_bnk_info/install_shape must not
        be newly blocked by this guard."""
        svc, upgrade = self._make_svc_with_upgrade("flo")
        upgrade.from_bnk_info = {}
        upgrade.plan = []
        upgrade.step_results = []

        with patch("services.bnk_upgrade_execution_service.HelmService") as mock_helm:
            mock_helm.return_value.get_history.return_value = []
            result = svc.execute_upgrade(upgrade_id=1)

        assert result is upgrade
        if upgrade.error_message:
            assert "not yet supported for install_shape" not in upgrade.error_message
