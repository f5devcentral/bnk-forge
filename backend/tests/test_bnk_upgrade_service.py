"""
Tests for BNK Upgrade Service — Sprint 8.2

Tests the version parsing, plan generation, pre-check validation,
and upgrade plan structure. These tests have ZERO database or cluster
dependencies — they test pure logic only.

The service module imports models.py which needs psycopg2. To avoid
this, we mock the DB-dependent imports and only test the pure logic.
"""

import os
import sys
import types

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

# Stub out database-dependent modules before importing the service
# This lets us test pure logic without psycopg2
if "database" not in sys.modules:
    db_stub = types.ModuleType("database")
    db_stub.Base = type("Base", (), {})  # type: ignore
    db_stub.get_db = lambda: None  # type: ignore
    db_stub.SessionLocal = None  # type: ignore
    sys.modules["database"] = db_stub

if "models" not in sys.modules:
    models_stub = types.ModuleType("models")
    # Create minimal stub classes
    models_stub.BnkUpgrade = type("BnkUpgrade", (), {})  # type: ignore
    models_stub.KubernetesCluster = type("KubernetesCluster", (), {})  # type: ignore
    models_stub.ProjectModule = type("ProjectModule", (), {})  # type: ignore
    sys.modules["models"] = models_stub

# Stub cluster_scanner and helm_service to avoid their DB imports
if "services.cluster_scanner" not in sys.modules:
    scanner_stub = types.ModuleType("services.cluster_scanner")
    scanner_stub.ClusterScanner = type("ClusterScanner", (), {"__init__": lambda self, db: None, "scan": lambda self, cid: {}})  # type: ignore
    sys.modules["services.cluster_scanner"] = scanner_stub

if "services.helm_service" not in sys.modules:
    helm_stub = types.ModuleType("services.helm_service")
    helm_stub.HelmService = type("HelmService", (), {"__init__": lambda self, db: None})  # type: ignore
    sys.modules["services.helm_service"] = helm_stub


# ───── Version Parsing Tests ─────

class TestVersionParsing:
    """Test FLO version string parsing and comparison."""

    def test_parse_standard_version(self):
        from services.bnk_upgrade_service import parse_version
        assert parse_version("v1.198.4-0.1.36") == (1, 198, 4, 0, 1, 36)

    def test_parse_version_no_prefix(self):
        from services.bnk_upgrade_service import parse_version
        assert parse_version("1.198.4") == (1, 198, 4)

    def test_parse_version_short(self):
        from services.bnk_upgrade_service import parse_version
        assert parse_version("v2.0.0") == (2, 0, 0)

    def test_parse_version_with_build(self):
        from services.bnk_upgrade_service import parse_version
        assert parse_version("v1.199.0-0.1.0") == (1, 199, 0, 0, 1, 0)

    def test_parse_version_empty(self):
        from services.bnk_upgrade_service import parse_version
        assert parse_version("") == (0,)

    def test_parse_version_alpha_suffix(self):
        from services.bnk_upgrade_service import parse_version
        # Should stop at non-numeric part
        result = parse_version("v1.2.3-beta")
        assert result == (1, 2, 3)

    def test_version_gt_major(self):
        from services.bnk_upgrade_service import version_gt
        assert version_gt("v2.0.0", "v1.198.4-0.1.36") is True

    def test_version_gt_minor(self):
        from services.bnk_upgrade_service import version_gt
        assert version_gt("v1.199.0-0.1.0", "v1.198.4-0.1.36") is True

    def test_version_gt_patch(self):
        from services.bnk_upgrade_service import version_gt
        assert version_gt("v1.198.5-0.1.36", "v1.198.4-0.1.36") is True

    def test_version_gt_build(self):
        from services.bnk_upgrade_service import version_gt
        assert version_gt("v1.198.4-0.1.37", "v1.198.4-0.1.36") is True

    def test_version_not_gt_same(self):
        from services.bnk_upgrade_service import version_gt
        assert version_gt("v1.198.4-0.1.36", "v1.198.4-0.1.36") is False

    def test_version_not_gt_older(self):
        from services.bnk_upgrade_service import version_gt
        assert version_gt("v1.198.3", "v1.198.4-0.1.36") is False

    def test_version_eq(self):
        from services.bnk_upgrade_service import version_eq
        assert version_eq("v1.198.4-0.1.36", "v1.198.4-0.1.36") is True

    def test_version_eq_with_prefix(self):
        from services.bnk_upgrade_service import version_eq
        assert version_eq("v1.198.4", "1.198.4") is True

    def test_version_not_eq(self):
        from services.bnk_upgrade_service import version_eq
        assert version_eq("v1.198.4", "v1.198.5") is False


# ───── Known Version Lookup Tests ─────

class TestVersionMetadata:
    """Test version range-based metadata lookup."""

    def test_unknown_version_returns_none(self):
        from services.bnk_upgrade_service import get_known_version_info
        info = get_known_version_info("v99.99.99")
        assert info is None

    def test_version_ranges_exist(self):
        from services.bnk_upgrade_service import _VERSION_RANGES
        assert isinstance(_VERSION_RANGES, list)
        assert len(_VERSION_RANGES) >= 3

    def test_bnk_21_detected(self):
        """FLO v1.198.x should map to BNK 2.1 GA."""
        from services.bnk_upgrade_service import get_known_version_info
        info = get_known_version_info("v1.198.4-0.1.36")
        assert info is not None
        assert info["label"] == "BNK 2.1 GA"

    def test_bnk_22_detected(self):
        """FLO 2.9.x should map to BNK 2.2 GA (corrected matrix; the old v1.199.x
        assumption was wrong and is removed by the #217 registry-driven resolution)."""
        from services.bnk_upgrade_service import get_known_version_info
        info = get_known_version_info("v2.9.27")
        assert info is not None
        assert info["label"] == "BNK 2.2 GA"

    def test_bnk_20_detected(self):
        """FLO v1.197.x and below should map to BNK 2.0."""
        from services.bnk_upgrade_service import get_known_version_info
        info = get_known_version_info("v1.197.2-0.0.1")
        assert info is not None
        assert info["label"] == "BNK 2.0"

    def test_bnk_23_k8s_range_widened(self):
        """#390: BNK 2.3 GA's max_k8s must be widened past 1.31 to cover
        real-world clusters (e.g. kind 1.36)."""
        from services.bnk_upgrade_service import get_known_version_info
        info = get_known_version_info("v2.21.13")
        assert info is not None
        assert info["label"] == "BNK 2.3 GA"
        assert info["min_k8s"] == "1.30"
        assert info["max_k8s"] == "1.33"

    def test_version_without_v_prefix(self):
        """Versions without v prefix should still match (regex strips it)."""
        from services.bnk_upgrade_service import get_known_version_info
        info = get_known_version_info("1.198.4-0.1.36")
        assert info is not None
        assert info["label"] == "BNK 2.1 GA"

    def test_get_available_versions_without_cluster_returns_empty(self):
        """Without a cluster_id, no registry query is possible — should return empty."""
        from services.bnk_upgrade_service import BnkUpgradeService
        service = BnkUpgradeService.__new__(BnkUpgradeService)
        versions, error = service.get_available_versions(cluster_id=None)
        assert versions == []
        assert error is not None


# ───── Install-Shape-Aware Version Detection Tests (#389) ─────

class TestDetectCurrentBnkVersion:
    """Version detection must not require FLO — helm/manual installs too (#389)."""

    def test_flo_version_preferred_when_present(self):
        from services.bnk_upgrade_service import detect_current_bnk_version
        bnk_install = {
            "flo": {
                "version": "v1.198.4-0.1.36",
                "helm_release": {"chart": "f5-lifecycle-operator-1.198.4"},
            },
        }
        assert detect_current_bnk_version(bnk_install) == "v1.198.4-0.1.36"

    def test_falls_back_to_helm_chart_version_when_flo_absent(self):
        """Helm/manual install (cluster 54 shape): no FLO pods, but the
        f5ingress Helm release chart carries the version."""
        from services.bnk_upgrade_service import detect_current_bnk_version
        bnk_install = {
            "install_shape": "helm",
            "flo": {
                "version": None,
                "helm_release": {"name": "f5ingress", "namespace": "bnk-app1", "chart": "f5ingress-2.21.13"},
            },
        }
        assert detect_current_bnk_version(bnk_install) == "2.21.13"

    def test_falls_back_to_helm_chart_version_with_build_suffix(self):
        from services.bnk_upgrade_service import detect_current_bnk_version
        bnk_install = {
            "install_shape": "helm",
            "flo": {"version": None, "helm_release": {"chart": "f5ingress-2.21.13-0.0.28"}},
        }
        assert detect_current_bnk_version(bnk_install) == "2.21.13-0.0.28"

    def test_no_version_when_neither_flo_nor_helm_chart_available(self):
        from services.bnk_upgrade_service import detect_current_bnk_version
        bnk_install = {"install_shape": "unknown", "flo": {"version": None, "helm_release": {}}}
        assert detect_current_bnk_version(bnk_install) is None

    def test_no_version_when_helm_release_missing_chart_key(self):
        from services.bnk_upgrade_service import detect_current_bnk_version
        bnk_install = {"install_shape": "helm", "flo": {"version": None, "helm_release": {"name": "f5ingress"}}}
        assert detect_current_bnk_version(bnk_install) is None


# ───── Pre-Check Logic Tests ─────

class TestPreChecks:
    """Test pre-upgrade validation logic (no DB)."""

    def _make_service_prechecks(self, **kwargs):
        """Call the _run_pre_checks method directly with test data."""
        from services.bnk_upgrade_service import BnkUpgradeService

        # Create a minimal instance (we won't use the db for pure logic tests)
        service = BnkUpgradeService.__new__(BnkUpgradeService)

        defaults = {
            "bnk_install": {
                "status": "installed",
                "health": "healthy",
                "flo": {"version": "v1.198.4-0.1.36", "pods": 2, "running": 2, "helm_release": {"name": "flo"}},
                "tmm": {"pods": 1, "running": 1, "containers": {"total": 7, "ready": 7}},
                "controller": {"pods": 1, "running": 1},
                "vlans": [{"name": "external", "programmed": True}, {"name": "internal", "programmed": True}],
            },
            "cluster_info": {"version": "v1.28.5"},
            "current_version": "v1.198.4-0.1.36",
            "target_version": "v1.199.0-0.1.0",
        }
        defaults.update(kwargs)
        return service._run_pre_checks(**defaults)

    def test_healthy_cluster_all_pass(self):
        checks = self._make_service_prechecks()
        statuses = {c["name"]: c["status"] for c in checks}
        assert statuses["bnk_installed"] == "pass"
        assert statuses["version_detected"] == "pass"
        assert statuses["version_change"] == "pass"
        assert statuses["flo_health"] == "pass"
        assert statuses["tmm_health"] == "pass"
        assert statuses["vlans_programmed"] == "pass"
        assert statuses["helm_release"] == "pass"

    def test_bnk_not_installed_fails(self):
        checks = self._make_service_prechecks(
            bnk_install={"status": "not_installed", "flo": {}, "tmm": {}, "vlans": [], "controller": {}},
            current_version=None,
        )
        bnk_check = next(c for c in checks if c["name"] == "bnk_installed")
        assert bnk_check["status"] == "fail"
        assert bnk_check["critical"] is True

    def test_no_version_detected_fails(self):
        checks = self._make_service_prechecks(current_version=None)
        ver_check = next(c for c in checks if c["name"] == "version_detected")
        assert ver_check["status"] == "fail"
        assert ver_check["critical"] is True

    def test_same_version_fails(self):
        checks = self._make_service_prechecks(target_version="v1.198.4-0.1.36")
        ver_check = next(c for c in checks if c["name"] == "version_change")
        assert ver_check["status"] == "fail"

    def test_downgrade_warns(self):
        checks = self._make_service_prechecks(target_version="v1.197.0")
        ver_check = next(c for c in checks if c["name"] == "version_change")
        assert ver_check["status"] == "warn"
        assert not ver_check["critical"]

    def test_unhealthy_tmm_warns(self):
        bnk = {
            "status": "installed",
            "health": "degraded",
            "flo": {"version": "v1.198.4-0.1.36", "pods": 2, "running": 2, "helm_release": {"name": "flo"}},
            "tmm": {"pods": 1, "running": 0, "containers": {"total": 7, "ready": 0}},
            "controller": {"pods": 1, "running": 1},
            "vlans": [{"name": "ext", "programmed": True}],
        }
        checks = self._make_service_prechecks(bnk_install=bnk)
        tmm_check = next(c for c in checks if c["name"] == "tmm_health")
        assert tmm_check["status"] == "warn"

    def test_vlan_not_programmed_warns(self):
        bnk = {
            "status": "installed",
            "health": "healthy",
            "flo": {"version": "v1.198.4-0.1.36", "pods": 2, "running": 2, "helm_release": {"name": "flo"}},
            "tmm": {"pods": 1, "running": 1, "containers": {"total": 7, "ready": 7}},
            "controller": {"pods": 1, "running": 1},
            "vlans": [{"name": "ext", "programmed": True}, {"name": "int", "programmed": False}],
        }
        checks = self._make_service_prechecks(bnk_install=bnk)
        vlan_check = next(c for c in checks if c["name"] == "vlans_programmed")
        assert vlan_check["status"] == "warn"

    def test_no_helm_release_warns(self):
        bnk = {
            "status": "installed",
            "health": "healthy",
            "flo": {"version": "v1.198.4-0.1.36", "pods": 2, "running": 2, "helm_release": {}},
            "tmm": {"pods": 1, "running": 1, "containers": {"total": 7, "ready": 7}},
            "controller": {"pods": 1, "running": 1},
            "vlans": [],
        }
        checks = self._make_service_prechecks(bnk_install=bnk)
        helm_check = next(c for c in checks if c["name"] == "helm_release")
        assert helm_check["status"] == "warn"

    # ── Helm/manual install (no FLO) — #389 ──

    def test_helm_shape_install_produces_passing_plan(self):
        """Reproduces the live cluster-54 defect: helm/manual install, no FLO
        pods, but the f5ingress Helm release + TMM are healthy. Validate &
        Plan must not hard-fail on version_detected or flo_health."""
        bnk = {
            "status": "installed",
            "health": "healthy",
            "install_shape": "helm",
            "flo": {
                "version": None,
                "pods": 0,
                "running": 0,
                "helm_release": {"name": "f5ingress", "namespace": "bnk-app1", "chart": "f5ingress-2.21.13"},
            },
            "tmm": {"pods": 1, "running": 1, "containers": {"total": 4, "ready": 4}},
            "controller": {"pods": 1, "running": 1},
            "vlans": [{"name": "external", "programmed": True}],
        }
        checks = self._make_service_prechecks(
            bnk_install=bnk,
            current_version="2.21.13",
            target_version="2.22.0",
            cluster_info={"version": "v1.31.0"},
        )
        critical_failures = [c for c in checks if c["status"] == "fail" and c.get("critical", True)]
        assert critical_failures == []

        statuses = {c["name"]: c["status"] for c in checks}
        assert statuses["bnk_installed"] == "pass"
        assert statuses["version_detected"] == "pass"
        assert statuses["flo_health"] == "pass"
        assert statuses["tmm_health"] == "pass"

    def test_helm_shape_flo_health_is_pass_not_warn(self):
        """FLO health should be N/A (pass), not warn, when install_shape != flo."""
        bnk = {
            "status": "installed",
            "install_shape": "helm",
            "flo": {"version": None, "pods": 0, "running": 0, "helm_release": {"name": "f5ingress"}},
            "tmm": {"pods": 1, "running": 1, "containers": {"total": 4, "ready": 4}},
            "controller": {"pods": 1, "running": 1},
            "vlans": [],
        }
        checks = self._make_service_prechecks(bnk_install=bnk, current_version="2.21.13")
        flo_check = next(c for c in checks if c["name"] == "flo_health")
        assert flo_check["status"] == "pass"
        assert flo_check["critical"] is False

    def test_flo_shape_flo_health_unchanged_when_pods_missing(self):
        """Regression guard: FLO-shape installs still warn on missing FLO pods."""
        bnk = {
            "status": "installed",
            "install_shape": "flo",
            "flo": {"version": "v1.198.4-0.1.36", "pods": 2, "running": 0, "helm_release": {"name": "flo"}},
            "tmm": {"pods": 1, "running": 1, "containers": {"total": 7, "ready": 7}},
            "controller": {"pods": 1, "running": 1},
            "vlans": [],
        }
        checks = self._make_service_prechecks(bnk_install=bnk)
        flo_check = next(c for c in checks if c["name"] == "flo_health")
        assert flo_check["status"] == "warn"

    def test_no_version_detected_still_fails_when_neither_flo_nor_helm_available(self):
        """The only condition that should still critically fail is: no
        version detectable by ANY method (not just FLO)."""
        checks = self._make_service_prechecks(current_version=None)
        ver_check = next(c for c in checks if c["name"] == "version_detected")
        assert ver_check["status"] == "fail"
        assert ver_check["critical"] is True

    # ── Kubernetes compatibility — numeric compare + widened range (#390) ──

    def test_k8s_compat_passes_within_range(self):
        checks = self._make_service_prechecks(
            cluster_info={"version": "v1.30.5"},
            target_version="v2.21.13",
        )
        k8s_check = next(c for c in checks if c["name"] == "k8s_compat")
        assert k8s_check["status"] == "pass"
        assert k8s_check["critical"] is True

    def test_k8s_compat_newer_than_max_warns_not_fails(self):
        """K8s 1.36 (real cluster-54 ground truth) must warn, not critically fail."""
        checks = self._make_service_prechecks(
            cluster_info={"version": "v1.36.0"},
            target_version="v2.21.13",
        )
        k8s_check = next(c for c in checks if c["name"] == "k8s_compat")
        assert k8s_check["status"] == "warn"
        assert k8s_check["critical"] is False

    def test_k8s_compat_older_than_min_fails(self):
        checks = self._make_service_prechecks(
            cluster_info={"version": "v1.20.0"},
            target_version="v2.21.13",
        )
        k8s_check = next(c for c in checks if c["name"] == "k8s_compat")
        assert k8s_check["status"] == "fail"
        assert k8s_check["critical"] is True

    def test_k8s_compat_numeric_not_lexical_two_digit_minor(self):
        """Lexical '1.30' <= '1.9' is True; numerically 1.9 < 1.30 must fail."""
        checks = self._make_service_prechecks(
            cluster_info={"version": "v1.9.0"},
            target_version="v2.21.13",
        )
        k8s_check = next(c for c in checks if c["name"] == "k8s_compat")
        assert k8s_check["status"] == "fail"

    def test_k8s_compat_numeric_not_lexical_1_36_vs_1_31(self):
        """Lexical '1.36' <= '1.31' mis-sorts (True); numerically 1.36 > 1.31,
        so with a 1.30-1.33 range this must warn (newer), not fail."""
        checks = self._make_service_prechecks(
            cluster_info={"version": "v1.36.0"},
            target_version="v2.21.13",
        )
        k8s_check = next(c for c in checks if c["name"] == "k8s_compat")
        assert k8s_check["status"] != "fail"


# ───── Plan Generation Tests ─────

class TestPlanGeneration:
    """Test upgrade plan generation logic (no DB)."""

    def _build_plan(self, **kwargs):
        from services.bnk_upgrade_service import BnkUpgradeService
        service = BnkUpgradeService.__new__(BnkUpgradeService)
        defaults = {
            "bnk_install": {
                "status": "installed",
                "flo": {"version": "v1.198.4-0.1.36", "helm_release": {
                    "name": "flo",
                    "namespace": "f5-bnk",
                    "chart": "oci://private-registry.example.com/charts/f5-lifecycle-operator",
                }},
                "tmm": {"pods": 1, "running": 1},
                "cne_instance": {"name": "bnk-instance"},
                "vlans": [{"name": "ext"}, {"name": "int"}],
            },
            "target_version": "v1.199.0-0.1.0",
        }
        defaults.update(kwargs)
        return service._build_plan(**defaults)

    def test_plan_has_correct_step_count(self):
        plan = self._build_plan()
        # Expected: pre-health, helm upgrade, crd wait, flo health,
        #           cneinstance, tmm health, vlans, gatewayclass, post-health = 9
        assert len(plan) == 9

    def test_first_step_is_pre_health(self):
        plan = self._build_plan()
        assert plan[0]["action"] == "health_gate"
        assert plan[0]["phase"] == "pre_upgrade"

    def test_second_step_is_helm_upgrade(self):
        plan = self._build_plan()
        assert plan[1]["action"] == "helm_upgrade"
        assert plan[1]["module"] == "bnk/flo"
        assert plan[1]["version"] == "v1.199.0-0.1.0"

    def test_plan_includes_crd_wait(self):
        plan = self._build_plan()
        crd_steps = [s for s in plan if s["action"] == "crd_wait"]
        assert len(crd_steps) == 1

    def test_plan_includes_cneinstance_apply(self):
        plan = self._build_plan()
        cne_steps = [s for s in plan if s.get("module") == "bnk/cneinstance"]
        assert len(cne_steps) == 1
        assert cne_steps[0]["action"] == "manifest_apply"

    def test_plan_includes_vlan_apply(self):
        plan = self._build_plan()
        vlan_steps = [s for s in plan if s.get("module") == "bnk/bnk-vlans"]
        assert len(vlan_steps) == 1

    def test_plan_includes_gatewayclass_apply(self):
        plan = self._build_plan()
        gc_steps = [s for s in plan if s.get("module") == "bnk/bnk-gatewayclass"]
        assert len(gc_steps) == 1

    def test_last_step_is_post_health(self):
        plan = self._build_plan()
        assert plan[-1]["action"] == "health_gate"
        assert plan[-1]["phase"] == "post_upgrade"

    def test_plan_without_cneinstance(self):
        """If no CNEInstance exists, plan should skip it."""
        plan = self._build_plan(bnk_install={
            "status": "installed",
            "flo": {"version": "v1.198.4-0.1.36", "helm_release": {
                "name": "flo", "namespace": "f5-bnk",
                "chart": "oci://private-registry.example.com/charts/f5-lifecycle-operator",
            }},
            "tmm": {"pods": 1, "running": 1},
            "cne_instance": {},  # No name = not detected
            "vlans": [{"name": "ext"}],
        })
        cne_steps = [s for s in plan if s.get("module") == "bnk/cneinstance"]
        assert len(cne_steps) == 0
        # Plan should be 1 step shorter
        assert len(plan) == 8

    def test_plan_without_vlans(self):
        """If no VLANs exist, plan should skip vlan re-apply."""
        plan = self._build_plan(bnk_install={
            "status": "installed",
            "flo": {"version": "v1.198.4-0.1.36", "helm_release": {
                "name": "flo", "namespace": "f5-bnk",
                "chart": "oci://private-registry.example.com/charts/f5-lifecycle-operator",
            }},
            "tmm": {"pods": 1, "running": 1},
            "cne_instance": {"name": "bnk-instance"},
            "vlans": [],  # No VLANs
        })
        vlan_steps = [s for s in plan if s.get("module") == "bnk/bnk-vlans"]
        assert len(vlan_steps) == 0

    def test_plan_steps_are_sequential(self):
        plan = self._build_plan()
        for i, step in enumerate(plan):
            assert step["step"] == i + 1

    def test_all_steps_have_labels(self):
        plan = self._build_plan()
        for step in plan:
            assert "label" in step
            assert len(step["label"]) > 0

    def test_all_steps_have_timeouts(self):
        plan = self._build_plan()
        for step in plan:
            assert "timeout" in step
            assert step["timeout"] > 0

    def test_helm_upgrade_uses_correct_release(self):
        plan = self._build_plan(bnk_install={
            "status": "installed",
            "flo": {"version": "v1.198.4", "helm_release": {
                "name": "my-flo",
                "namespace": "custom-ns",
                "chart": "oci://airgap-registry.corp.example/charts/f5-lifecycle-operator",
            }},
            "tmm": {"pods": 1, "running": 1},
            "cne_instance": {"name": "inst"},
            "vlans": [],
        })
        helm_step = next(s for s in plan if s["action"] == "helm_upgrade")
        assert helm_step["release_name"] == "my-flo"
        assert helm_step["namespace"] == "custom-ns"
        assert helm_step["chart"] == "oci://airgap-registry.corp.example/charts/f5-lifecycle-operator"


# ───── Health Check Evaluation Tests ─────

class TestHealthCheckEvaluation:
    """Test individual health check evaluations (no DB)."""

    def _eval(self, check_name, bnk):
        from services.bnk_upgrade_service import BnkUpgradeService
        service = BnkUpgradeService.__new__(BnkUpgradeService)
        messages = []
        result = service._evaluate_health_check(check_name, bnk, lambda msg: messages.append(msg))
        return result, messages

    def test_flo_pods_ready_healthy(self):
        ok, _ = self._eval("flo_pods_ready", {"flo": {"running": 2, "pods": 2}})
        assert ok is True

    def test_flo_pods_ready_unhealthy(self):
        ok, msgs = self._eval("flo_pods_ready", {"flo": {"running": 1, "pods": 2}})
        assert ok is False
        assert any("FLO" in m for m in msgs)

    def test_flo_pods_ready_zero(self):
        ok, _ = self._eval("flo_pods_ready", {"flo": {"running": 0, "pods": 0}})
        assert ok is False

    def test_tmm_pods_ready_healthy(self):
        ok, _ = self._eval("tmm_pods_ready", {"tmm": {"running": 1, "pods": 1}})
        assert ok is True

    def test_tmm_containers_ready(self):
        ok, _ = self._eval("tmm_containers_ready", {"tmm": {"containers": {"total": 7, "ready": 7}}})
        assert ok is True

    def test_tmm_containers_not_ready(self):
        ok, msgs = self._eval("tmm_containers_ready", {"tmm": {"containers": {"total": 7, "ready": 4}}})
        assert ok is False
        assert any("TMM containers" in m for m in msgs)

    def test_vlans_programmed_all(self):
        ok, _ = self._eval("vlans_programmed", {
            "vlans": [{"name": "ext", "programmed": True}, {"name": "int", "programmed": True}]
        })
        assert ok is True

    def test_vlans_programmed_some(self):
        ok, msgs = self._eval("vlans_programmed", {
            "vlans": [{"name": "ext", "programmed": True}, {"name": "int", "programmed": False}]
        })
        assert ok is False
        assert any("int" in m for m in msgs)

    def test_vlans_programmed_empty(self):
        ok, _ = self._eval("vlans_programmed", {"vlans": []})
        assert ok is True

    def test_gatewayclass_accepted(self):
        ok, _ = self._eval("gatewayclass_accepted", {})
        assert ok is True  # Lenient check

    def test_unknown_check(self):
        ok, msgs = self._eval("nonexistent_check", {})
        assert ok is True  # Unknown checks pass
        assert any("Unknown" in m for m in msgs)

    def test_controller_pods_ready(self):
        ok, _ = self._eval("controller_pods_ready", {"controller": {"running": 1, "pods": 1}})
        assert ok is True

    def test_controller_pods_not_ready(self):
        ok, _ = self._eval("controller_pods_ready", {"controller": {"running": 0, "pods": 1}})
        assert ok is False
