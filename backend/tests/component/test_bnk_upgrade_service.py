"""
BC-C56: Component tests for BnkUpgradeService.

Tests version parsing, version comparison, known version info lookup,
create_upgrade, get/list/cancel queries, and get_available_versions.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from core.errors import AppError
from models.enums import BnkUpgradeStatus
from services.bnk_upgrade_service import (
    BnkUpgradeService,
    get_known_version_info,
    parse_version,
    version_eq,
    version_gt,
)

# ── Version Helpers ──────────────────────────────────────────────────

class TestParseVersion:
    def test_simple_version(self):
        assert parse_version("1.198.4") == (1, 198, 4)

    def test_with_v_prefix(self):
        assert parse_version("v1.198.4") == (1, 198, 4)

    def test_with_build_suffix(self):
        assert parse_version("v1.198.4-0.1.36") == (1, 198, 4, 0, 1, 36)

    def test_empty_string(self):
        assert parse_version("") == (0,)


class TestVersionComparison:
    def test_gt_true(self):
        assert version_gt("v1.199.0", "v1.198.4") is True

    def test_gt_false(self):
        assert version_gt("v1.198.0", "v1.199.0") is False

    def test_eq_true(self):
        assert version_eq("v1.198.4", "1.198.4") is True

    def test_eq_false(self):
        assert version_eq("v1.198.0", "v1.199.0") is False


class TestGetKnownVersionInfo:
    def test_bnk_21(self):
        info = get_known_version_info("v1.198.4-0.1.36")
        assert info is not None
        assert "2.1" in info["label"]

    def test_bnk_22_real_flo_version(self):
        # BNK 2.2 uses FLO 2.9.x (new 2.x.x scheme — NOT v1.199.x)
        info = get_known_version_info("2.9.27")
        assert info is not None
        assert "2.2" in info["label"]

    def test_bnk_23_real_flo_version(self):
        # BNK 2.3 uses FLO 2.21.x (live-observed on a known-2.3 cluster)
        info = get_known_version_info("2.21.13-0.0.28")
        assert info is not None
        assert "2.3" in info["label"]

    def test_v1_199_unmapped(self):
        # v1.199.x was previously mis-mapped to BNK 2.2; it is not in the grounded matrix
        info = get_known_version_info("v1.199.0-0.1.0")
        assert info is None

    def test_unknown_version(self):
        info = get_known_version_info("v99.0.0")
        assert info is None


# ── Create Upgrade ───────────────────────────────────────────────────

class TestCreateUpgrade:
    @patch("services.bnk_upgrade_service.ClusterScanner")
    def test_create_upgrade_success(self, mock_scanner):
        """Test create_upgrade happy path."""
        db = MagicMock()
        cluster = MagicMock()
        cluster.id = 1
        cluster.project_id = 1

        # Cluster exists
        db.query.return_value.filter.return_value.first.side_effect = [
            cluster,  # cluster lookup
            None,     # no existing upgrade
        ]

        # Scanner returns BNK info
        scanner = mock_scanner.return_value
        scanner.scan.return_value = {
            "bnk_install": {
                "status": "installed",
                "flo": {"version": "v1.198.4-0.1.36", "running": 1, "pods": 1},
                "tmm": {"running": 2, "pods": 2},
                "vlans": [],
            },
            "cluster_info": {"version": "v1.28.0"},
        }

        svc = BnkUpgradeService(db)
        # Mock mixin methods
        svc._run_pre_checks = MagicMock(return_value=[
            {"name": "bnk_installed", "status": "pass", "critical": True, "detail": "ok"},
        ])
        svc._build_plan = MagicMock(return_value=[{"step": 1, "action": "health_gate"}])

        upgrade = svc.create_upgrade(cluster_id=1, target_version="v1.199.0")
        assert upgrade is not None
        db.add.assert_called_once()
        db.commit.assert_called()

    @patch("services.bnk_upgrade_service.ClusterScanner")
    def test_create_upgrade_helm_shape_yields_ready_plan(self, mock_scanner):
        """(#389) End-to-end: a helm/manual install (no FLO) scan must produce
        a READY plan via create_upgrade's real pre-checks + plan builder —
        no critical version_detected failure just because FLO is absent."""
        db = MagicMock()
        cluster = MagicMock()
        cluster.id = 1
        cluster.project_id = 1

        db.query.return_value.filter.return_value.first.side_effect = [
            cluster,  # cluster lookup
            None,     # no existing upgrade
        ]

        scanner = mock_scanner.return_value
        scanner.scan.return_value = {
            "bnk_install": {
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
                "cne_instance": {},
            },
            "cluster_info": {"version": "v1.30.0"},
        }

        svc = BnkUpgradeService(db)
        upgrade = svc.create_upgrade(cluster_id=1, target_version="v2.22.0")

        assert upgrade.status == BnkUpgradeStatus.READY
        assert upgrade.pre_check_passed is True
        assert upgrade.from_version == "2.21.13"

        helm_step = next(s for s in upgrade.plan if s["action"] == "helm_upgrade")
        assert helm_step["release_name"] == "f5ingress"
        assert helm_step["namespace"] == "bnk-app1"

    @patch("services.bnk_upgrade_service.ClusterScanner")
    def test_create_upgrade_helm_shape_no_chart_version_is_undetectable(self, mock_scanner):
        """(#389) Real cluster-54 shape: helm release secret carries only a
        revision (version:"4"), no chart version. Controller/TMM image tags
        (e.g. v14.59.1-0.0.70) cannot be reliably compared to BNK release
        versions, so from_version is None and the version_detected pre-check
        fails, setting status to FAILED — not a 500. The explicit-None
        cne_instance/vlans must NOT cause an AttributeError in _build_plan."""
        db = MagicMock()
        cluster = MagicMock()
        cluster.id = 1
        cluster.project_id = 1

        db.query.return_value.filter.return_value.first.side_effect = [
            cluster,  # cluster lookup
            None,     # no existing upgrade
        ]

        # Exact live cluster-54 scan shape: cne_instance is explicit None (no
        # CNEInstance CR on a helm install), vlans is None. A `.get(k, {})`
        # default does NOT apply to an explicit None, so _build_plan crashed
        # with AttributeError before the `or {}` / `or []` hardening.
        scanner = mock_scanner.return_value
        scanner.scan.return_value = {
            "bnk_install": {
                "status": "installed",
                "install_shape": "helm",
                "flo": {
                    "version": None,
                    "pods": 0,
                    "running": 0,
                    "helm_release": {"name": "f5ingress", "namespace": "bnk-app1", "version": "4", "status": "deployed"},
                },
                "controller": {"pods": 1, "running": 1, "version": "v14.59.1-0.0.70"},
                "tmm": {"pods": 1, "running": 1, "version": "v10.159.3-0.1.5"},
                "vlans": None,
                "cne_instance": None,
            },
            "cluster_info": {"version": "v1.36.0"},
        }

        svc = BnkUpgradeService(db)
        # Must not raise AttributeError inside _build_plan on the explicit-None
        # cne_instance/vlans. Version is undetectable → status is FAILED, not a 500.
        upgrade = svc.create_upgrade(cluster_id=1, target_version="v2.22.0")

        assert upgrade.status == BnkUpgradeStatus.FAILED
        assert upgrade.from_version is None
        version_check = next(c for c in upgrade.pre_checks if c["name"] == "version_detected")
        assert version_check["status"] == "fail"

    @patch("services.bnk_upgrade_service.ClusterScanner")
    def test_create_upgrade_cluster_not_found(self, mock_scanner):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        svc = BnkUpgradeService(db)
        with pytest.raises(ValueError, match="not found"):
            svc.create_upgrade(cluster_id=999, target_version="v1.199.0")

    @patch("services.bnk_upgrade_service.ClusterScanner")
    def test_create_upgrade_supersedes_ready_upgrade(self, mock_scanner):
        """Re-planning when a READY upgrade exists cancels the old one and succeeds."""
        db = MagicMock()
        cluster = MagicMock()
        cluster.id = 1
        cluster.project_id = 1

        existing_upgrade = MagicMock()
        existing_upgrade.id = 42
        existing_upgrade.status = BnkUpgradeStatus.READY

        # cluster lookup → existing upgrade lookup
        db.query.return_value.filter.return_value.first.side_effect = [
            cluster,
            existing_upgrade,
        ]

        scanner = mock_scanner.return_value
        scanner.scan.return_value = {
            "bnk_install": {"flo": {"version": "v1.198.4-0.1.36"}},
            "cluster_info": {"version": "v1.28.0"},
        }

        svc = BnkUpgradeService(db)
        svc._run_pre_checks = MagicMock(return_value=[
            {"name": "bnk_installed", "status": "pass", "critical": True, "detail": "ok"},
        ])
        svc._build_plan = MagicMock(return_value=[{"step": 1, "action": "health_gate"}])

        upgrade = svc.create_upgrade(cluster_id=1, target_version="v1.199.0")

        # Old upgrade must be cancelled
        assert existing_upgrade.status == BnkUpgradeStatus.CANCELLED
        assert existing_upgrade.completed_at is not None
        # New upgrade record was added
        db.add.assert_called_once()
        assert upgrade is not None

    @patch("services.bnk_upgrade_service.ClusterScanner")
    def test_create_upgrade_rejects_executing_upgrade_with_409(self, mock_scanner):
        """Re-planning while IN_PROGRESS raises a 409 AppError with upgrade_id in details."""
        db = MagicMock()
        cluster = MagicMock()
        cluster.id = 1
        cluster.project_id = 1

        existing_upgrade = MagicMock()
        existing_upgrade.id = 7
        existing_upgrade.status = BnkUpgradeStatus.IN_PROGRESS

        db.query.return_value.filter.return_value.first.side_effect = [
            cluster,
            existing_upgrade,
        ]

        svc = BnkUpgradeService(db)
        with pytest.raises(AppError) as exc_info:
            svc.create_upgrade(cluster_id=1, target_version="v1.199.0")

        err = exc_info.value
        assert err.status_code == 409
        assert err.code == "UPGRADE_IN_PROGRESS"
        assert err.details["upgrade_id"] == 7


# ── Query Methods ────────────────────────────────────────────────────

class TestQueries:
    def test_get_upgrade(self):
        db = MagicMock()
        mock_upgrade = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_upgrade

        svc = BnkUpgradeService(db)
        result = svc.get_upgrade(1)
        assert result == mock_upgrade

    def test_list_upgrades(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = [
            MagicMock(), MagicMock(),
        ]

        svc = BnkUpgradeService(db)
        result = svc.list_upgrades(cluster_id=1)
        assert len(result) == 2

    def test_cancel_upgrade_success(self):
        db = MagicMock()
        upgrade = MagicMock()
        upgrade.status = "ready"
        db.query.return_value.filter.return_value.first.return_value = upgrade

        svc = BnkUpgradeService(db)
        result = svc.cancel_upgrade(1)
        assert result.status == "cancelled"
        db.commit.assert_called()

    def test_cancel_upgrade_wrong_status(self):
        db = MagicMock()
        upgrade = MagicMock()
        upgrade.status = "in_progress"
        db.query.return_value.filter.return_value.first.return_value = upgrade

        svc = BnkUpgradeService(db)
        with pytest.raises(ValueError, match="Cannot cancel"):
            svc.cancel_upgrade(1)


# ── Fallback Versions ───────────────────────────────────────────────

class TestFallbackVersions:
    def test_get_fallback_versions_returns_list(self):
        versions = BnkUpgradeService._get_fallback_versions()
        assert isinstance(versions, list)
        assert len(versions) > 0
        for v in versions:
            assert "version" in v
            assert "label" in v
            assert v["source"] == "known"

    def test_get_available_versions_no_cluster(self):
        db = MagicMock()
        svc = BnkUpgradeService(db)
        versions, error = svc.get_available_versions(cluster_id=None)
        assert versions == []
        assert error is not None


# ── Rollback status guard ────────────────────────────────────────────

def _make_upgrade(status: str, rollback_available: bool = True):
    """Build a minimal mock BnkUpgrade for rollback guard tests."""
    upgrade = MagicMock()
    upgrade.id = 1
    upgrade.status = status
    upgrade.rollback_available = rollback_available
    upgrade.error_message = None
    return upgrade


class TestRollbackStatusGuard:
    """rollback() must accept FAILED/IN_PROGRESS/HEALTH_CHECK and reject others.

    Verifies that BnkUpgradeStatus.HEALTH_CHECK, ROLLING_BACK, and ROLLED_BACK
    all exist (no AttributeError) and that the status guard behaves correctly.
    """

    def _make_svc(self):
        db = MagicMock()
        svc = BnkUpgradeService(db)
        return svc, db

    @pytest.mark.parametrize("allowed_status", [
        BnkUpgradeStatus.FAILED,
        BnkUpgradeStatus.IN_PROGRESS,
        BnkUpgradeStatus.HEALTH_CHECK,
    ])
    @patch("services.bnk_upgrade_execution_service.HelmService")
    @patch("services.bnk_upgrade_execution_service.ClusterScanner")
    def test_rollback_proceeds_for_allowed_statuses(self, mock_scanner, mock_helm, allowed_status):  # noqa: PT019
        """rollback() should not raise ValueError for FAILED, IN_PROGRESS, or HEALTH_CHECK."""
        svc, db = self._make_svc()
        upgrade = _make_upgrade(allowed_status)
        db.query.return_value.filter.return_value.first.return_value = upgrade

        mock_helm.return_value.rollback_release.return_value = {"exit_code": 0}
        mock_scanner.return_value.scan.return_value = {
            "bnk_install": {
                "flo": {"version": "v1.198.4"},
                "tmm": {},
                "health": "healthy",
                "status": "installed",
            },
        }

        # Should not raise — specifically no AttributeError on enum lookup
        result = svc.rollback(upgrade_id=1)
        assert result is upgrade

    @pytest.mark.parametrize("blocked_status", [
        BnkUpgradeStatus.PLANNING,
        BnkUpgradeStatus.READY,
        BnkUpgradeStatus.COMPLETED,
        BnkUpgradeStatus.CANCELLED,
        BnkUpgradeStatus.ROLLING_BACK,
        BnkUpgradeStatus.ROLLED_BACK,
    ])
    def test_rollback_rejected_for_blocked_statuses(self, blocked_status):
        """rollback() should raise ValueError for statuses outside the allowed set."""
        svc, db = self._make_svc()
        upgrade = _make_upgrade(blocked_status)
        db.query.return_value.filter.return_value.first.return_value = upgrade

        with pytest.raises(ValueError, match="Cannot rollback from status"):
            svc.rollback(upgrade_id=1)

    def test_rollback_enum_members_exist(self):
        """BnkUpgradeStatus must define HEALTH_CHECK, ROLLING_BACK, and ROLLED_BACK."""
        assert BnkUpgradeStatus.HEALTH_CHECK == "health_check"
        assert BnkUpgradeStatus.ROLLING_BACK == "rolling_back"
        assert BnkUpgradeStatus.ROLLED_BACK == "rolled_back"

    def test_rollback_no_rollback_available(self):
        """rollback() raises ValueError when rollback_available is False."""
        svc, db = self._make_svc()
        upgrade = _make_upgrade(BnkUpgradeStatus.FAILED, rollback_available=False)
        db.query.return_value.filter.return_value.first.return_value = upgrade

        with pytest.raises(ValueError, match="No rollback available"):
            svc.rollback(upgrade_id=1)
