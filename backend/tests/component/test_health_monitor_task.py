"""
BC-C3: Component tests for health monitor Celery task.

Tests health check dispatching, severity detection, and alert firing.
Mocks K8s API, Redis, and alert service.
"""

from unittest.mock import MagicMock, patch

import pytest

_MOD = "tasks.health_monitor_task"


# ── _fire_health_alert ───────────────────────────────────────────────

class TestFireHealthAlert:
    """tests for _fire_health_alert helper."""

    @patch("services.alert_service.fire_alert")
    def test_fire_alert_called_with_severity_change(self, mock_fire):
        from tasks.health_monitor_task import _fire_health_alert

        cluster = MagicMock()
        cluster.name = "test-cluster"
        cluster.project_id = 1
        cluster.id = 10

        health = {
            "overall_severity": "unhealthy",
            "components": {
                "flo": {"severity": "unhealthy", "running": 0, "total": 1},
                "tmm": {"severity": "healthy", "running": 2, "total": 2},
            },
        }

        db = MagicMock()
        _fire_health_alert(db, cluster, health, "unhealthy", "healthy")

        mock_fire.assert_called_once()
        call_kwargs = mock_fire.call_args[1]
        assert call_kwargs["event_type"] == "health_change"
        assert call_kwargs["severity"] == "critical"
        assert "test-cluster" in call_kwargs["title"]

    @patch("services.alert_service.fire_alert")
    def test_fire_alert_includes_affected_components(self, mock_fire):
        from tasks.health_monitor_task import _fire_health_alert

        cluster = MagicMock()
        cluster.name = "prod-cluster"
        cluster.project_id = 2
        cluster.id = 20

        health = {
            "overall_severity": "degraded",
            "components": {
                "gateways": {"severity": "degraded", "running": 1, "total": 3},
            },
        }

        db = MagicMock()
        _fire_health_alert(db, cluster, health, "degraded", "healthy")

        call_kwargs = mock_fire.call_args[1]
        assert "gateways" in call_kwargs["message"]


# ── check_cluster_health ─────────────────────────────────────────────

class TestCheckClusterHealth:
    """tasks.health_monitor_task.check_cluster_health."""

    @patch(f"{_MOD}._get_redis", return_value=None)
    @patch(f"{_MOD}.get_db_context")
    def test_no_clusters_returns_zero(self, mock_db_ctx, mock_redis, db):
        """No active clusters returns checked=0."""
        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)

        from tasks.health_monitor_task import check_cluster_health
        result = check_cluster_health()

        assert result["checked"] == 0
        assert result["alerts_fired"] == 0

    @patch(f"{_MOD}._fire_health_alert")
    @patch(f"{_MOD}._get_cluster_health")
    @patch(f"{_MOD}._get_redis")
    @patch(f"{_MOD}.get_db_context")
    def test_health_check_fires_alert_on_critical(self, mock_db_ctx,
                                                    mock_redis, mock_health,
                                                    mock_fire, db):
        """First critical health detection fires an alert."""
        from models import KubernetesCluster, Project

        project = Project(
            name="Health Project", project_type="kubernetes",
            cloud_provider="on-prem", environment="dev",
            backend_type="local", color="#000", icon="cloud",
            is_active=True, module_count=1, deployed_count=1,
        )
        db.add(project)
        db.flush()

        cluster = KubernetesCluster(
            name="health-cluster", project_id=project.id,
            context="health-ctx",
        )
        db.add(cluster)
        db.commit()

        mock_db_ctx.return_value.__enter__ = MagicMock(return_value=db)
        mock_db_ctx.return_value.__exit__ = MagicMock(return_value=False)
        mock_redis.return_value = None  # No Redis — no previous state

        mock_health.return_value = {
            "overall_severity": "unhealthy",
            "components": {"flo": {"severity": "unhealthy", "running": 0, "total": 1}},
        }

        from tasks.health_monitor_task import check_cluster_health
        result = check_cluster_health()

        assert result["checked"] == 1
        assert result["alerts_fired"] == 1
        mock_fire.assert_called_once()
