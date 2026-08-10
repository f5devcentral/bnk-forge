"""
Integration tests for config promotion routes — /api/config/promote and /api/config/promotions.

Covers: dry-run promote, list promotion history, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB. Config export functions are mocked.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from models.config_promotion import ConfigPromotion

_SAMPLE_CONFIG_A = {
    "bnk_forge_export": {"cluster": {"id": 1, "name": "source-cluster"}},
    "resources": {
        "gateways": [
            {
                "kind": "Gateway",
                "apiVersion": "gateway.networking.k8s.io/v1",
                "metadata": {"name": "gw-prod", "namespace": "default"},
                "spec": {"listeners": []},
            },
        ],
    },
}

_SAMPLE_CONFIG_B = {
    "bnk_forge_export": {"cluster": {"id": 2, "name": "target-cluster"}},
    "resources": {
        "gateways": [],
    },
}


class TestPromoteDryRun:
    """POST /api/config/promote (dry_run=true)."""

    @patch("routes.config_promotion.diff_configs")
    @patch("routes.config_promotion.export_cluster_config")
    def test_promote_dry_run(
        self, mock_export, mock_diff,
        client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Operator can preview promotion changes with dry_run=true."""
        source = make_k8s_cluster(project=sample_project, name="promo-source")
        target = make_k8s_cluster(project=sample_project, name="promo-target")

        mock_export.side_effect = [_SAMPLE_CONFIG_A, _SAMPLE_CONFIG_B]
        mock_diff.return_value = {
            "resources": {
                "only_in_a": ["Gateway/default/gw-prod"],
                "only_in_b": [],
                "changed": [],
            },
        }

        response = client.post(
            "/api/config/promote",
            json={
                "source_cluster_id": source.id,
                "target_cluster_id": target.id,
                "dry_run": True,
            },
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["applied"] is False
        assert data["total_changes"] >= 1
        assert data["source_cluster"]["name"] == "promo-source"
        assert data["target_cluster"]["name"] == "promo-target"
        assert mock_export.call_count == 2
        mock_diff.assert_called_once()

    @patch("routes.config_promotion.export_cluster_config")
    def test_promote_same_cluster_rejected(
        self, mock_export,
        client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Promoting from a cluster to itself returns 400."""
        cluster = make_k8s_cluster(project=sample_project, name="self-promo")

        response = client.post(
            "/api/config/promote",
            json={
                "source_cluster_id": cluster.id,
                "target_cluster_id": cluster.id,
                "dry_run": True,
            },
            headers=operator_headers,
        )
        assert response.status_code == 400


class TestListPromotions:
    """GET /api/config/promotions."""

    def test_list_promotions(
        self, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster, db,
    ):
        """Viewer can list promotion history."""
        source = make_k8s_cluster(project=sample_project, name="hist-source")
        target = make_k8s_cluster(project=sample_project, name="hist-target")

        # Insert a promotion record directly
        promo = ConfigPromotion(
            source_cluster_id=source.id,
            target_cluster_id=target.id,
            source_cluster_name="hist-source",
            target_cluster_name="hist-target",
            change_count=3,
            changes_summary=[
                {"action": "add", "kind": "Gateway", "name": "gw-1", "namespace": "default"},
            ],
            applied_by="test-admin",
            success=True,
            applied_count=2,
            failed_count=0,
            skipped_count=1,
        )
        db.add(promo)
        db.commit()

        response = client.get("/api/config/promotions", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 1
        assert len(data["items"]) >= 1
        item = data["items"][0]
        assert item["source_cluster_name"] == "hist-source"
        assert item["success"] is True
        assert item["change_count"] == 3

    def test_list_promotions_empty(
        self, client, viewer_headers, all_test_users,
    ):
        """Empty promotion history returns 200 with zero items."""
        response = client.get("/api/config/promotions", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["total"] >= 0
        assert isinstance(data["items"], list)


class TestPromotionRBAC:
    """RBAC enforcement for config promotion endpoints."""

    def test_viewer_cannot_promote(self, client, viewer_headers, all_test_users):
        """Viewer cannot promote config — returns 403."""
        response = client.post(
            "/api/config/promote",
            json={
                "source_cluster_id": 1,
                "target_cluster_id": 2,
                "dry_run": True,
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_unauthenticated(self, client):
        """Unauthenticated request to promote returns 401."""
        response = client.post(
            "/api/config/promote",
            json={
                "source_cluster_id": 1,
                "target_cluster_id": 2,
                "dry_run": True,
            },
        )
        assert response.status_code == 401

    def test_unauthenticated_list(self, client):
        """Unauthenticated request to list promotions returns 401."""
        response = client.get("/api/config/promotions")
        assert response.status_code == 401
