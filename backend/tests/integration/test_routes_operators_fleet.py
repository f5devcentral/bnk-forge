"""
Integration tests for operator fleet routes — /api/operators/fleet-health and /api/operators/fleet/compare.

Covers: fleet health aggregation (D3 kubeconfig-based), fleet comparison, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB. BNK data services are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from models.kubernetes import KubernetesCluster


def _make_cluster(db, **overrides):
    """Create a KubernetesCluster record in the test DB."""
    cluster = KubernetesCluster(
        name=overrides.get("name", "test-cluster"),
        kubeconfig_encrypted=overrides.get("kubeconfig_encrypted", b"fake-encrypted"),
        context=overrides.get("context", "default"),
        version=overrides.get("version", "v1.28.0"),
    )
    if "project_id" in overrides:
        cluster.project_id = overrides["project_id"]
    if "cloud_provider" in overrides:
        cluster.cloud_provider = overrides["cloud_provider"]
    if "region" in overrides:
        cluster.region = overrides["region"]
    if "account_id" in overrides:
        cluster.account_id = overrides["account_id"]
    if "discovery_status" in overrides:
        cluster.discovery_status = overrides["discovery_status"]
    db.add(cluster)
    db.commit()
    db.refresh(cluster)
    return cluster


class TestFleetHealth:
    """GET /api/operators/fleet-health — D3 kubeconfig-based."""

    @patch("routes.operators.fleet._query_cluster_health")
    def test_fleet_health(
        self, mock_query_health,
        client, viewer_headers, all_test_users, db,
    ):
        """Viewer can retrieve fleet health summary."""
        cluster = _make_cluster(
            db,
            name="prod-cluster",
            version="v1.28.0",
            cloud_provider="aws",
            region="us-east-1",
            account_id="123456789012",
            discovery_status="discovered",
        )

        mock_query_health.return_value = {
            "status": "healthy",
            "bnk_severity": "healthy",
            "effective_connectivity_status": "connected",
            "reachable": True,
            "bnk_version": "1.8.0",
            "route_count": 10,
            "tmm_count": 2,
            "gateway_count": 2,
            "uptime_seconds": 3600,
            "health_summary": {"healthy": 4, "warning": 0, "critical": 0},
        }

        response = client.get("/api/operators/fleet-health", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["total_clusters"] == 1
        assert data["healthy"] == 1
        assert len(data["operators"]) == 1
        assert data["operators"][0]["cluster_name"] == "prod-cluster"
        assert data["operators"][0]["bnk_version"] == "1.8.0"
        assert data["operators"][0]["connectivity_mode"] == "kubeconfig"
        assert data["operators"][0]["effective_connectivity_status"] == "connected"
        assert data["operators"][0]["bnk_severity"] == "healthy"
        assert data["unknown"] == 0
        assert data["operators"][0]["detected_platform_profile"] in {
            "generic_onprem", "eks", "aks", "gke", "ocp", "unknown",
        }
        assert data["operators"][0]["cloud_provider"] == "aws"
        assert data["operators"][0]["region"] == "us-east-1"
        assert data["operators"][0]["account_id"] == "123456789012"
        assert data["operators"][0]["discovery_status"] == "discovered"
        assert "platform_context" in data
        assert "mixed_platform_profiles" in data["platform_context"]

    def test_fleet_health_no_clusters(self, client, viewer_headers, all_test_users):
        """Fleet health with no clusters returns empty response."""
        response = client.get("/api/operators/fleet-health", headers=viewer_headers)
        assert response.status_code == 200

        data = response.json()
        assert data["total_clusters"] == 0
        assert data["operators"] == []
        assert data["platform_context"]["mixed_platform_profiles"] is False

    @patch("routes.operators.fleet._query_cluster_health")
    def test_fleet_health_tracks_unknown_bnk_severity_without_marking_offline(
        self,
        mock_query_health,
        client,
        viewer_headers,
        all_test_users,
        db,
    ):
        """Reachable clusters with unknown BNK severity stay connected, not offline."""
        _make_cluster(db, name="reachable-no-bnk")
        mock_query_health.return_value = {
            "status": "unknown",
            "bnk_severity": "unknown",
            "effective_connectivity_status": "connected",
            "reachable": True,
            "bnk_version": None,
            "route_count": 0,
            "tmm_count": 0,
            "gateway_count": 0,
            "uptime_seconds": 0,
            "health_summary": {"healthy": 0, "warning": 0, "critical": 0},
            "health_issues": [],
        }

        response = client.get("/api/operators/fleet-health", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["offline"] == 0
        assert data["unknown"] == 1
        assert data["operators"][0]["effective_connectivity_status"] == "connected"

    @patch("routes.operators.fleet.KubernetesService")
    @patch("routes.operators.fleet._probe_tcp")
    def test_query_cluster_health_skips_tcp_precheck_for_aws_cluster(
        self,
        mock_probe_tcp,
        mock_k8s_service,
        db,
    ):
        """AWS clusters should rely on kubeconfig API check, not raw TCP probe."""
        cluster = _make_cluster(
            db,
            name="aws-cluster",
            context="aws-cluster",
        )
        cluster.cloud_provider = "aws"
        cluster.api_server = "https://example.eks.amazonaws.com"
        cluster.ssh_tunnel_enabled = False
        db.commit()

        svc = MagicMock()
        mock_k8s_service.return_value = svc
        svc.test_connection.return_value = {
            "success": True,
            "message": "Connection successful",
        }

        with (
            patch("routes.operators.fleet._cluster_has_bnk_api_groups", return_value=False),
            patch("routes.operators.fleet._cluster_has_dpf_api_groups", return_value=False),
        ):
            from routes.operators.fleet import _query_cluster_health

            result = _query_cluster_health(cluster, db)

        mock_probe_tcp.assert_not_called()
        svc.test_connection.assert_called_once_with(cluster.id)
        assert result["effective_connectivity_status"] == "connected"
        assert result["status"] == "warning"


class TestFleetCompare:
    """POST /api/operators/fleet/compare."""

    def test_fleet_compare_via_clusters_returns_cluster_config_shape(
        self,
        client, operator_headers, all_test_users, db,
    ):
        """Operator can compare two clusters directly and get cluster-config response shape."""
        from models.kubernetes import KubernetesCluster

        cluster_a = KubernetesCluster(name="mgx1", context="mgx1", kubeconfig_encrypted="fake-encrypted")
        cluster_b = KubernetesCluster(name="mgx3", context="mgx3", kubeconfig_encrypted="fake-encrypted")
        db.add_all([cluster_a, cluster_b])
        db.commit()

        with patch("services.config_export_service.export_cluster_config") as mock_export, patch("services.config_export_service.diff_configs") as mock_diff:
            mock_export.side_effect = [
                {"bnk_forge_export": {"cluster": {"name": "mgx1"}}, "resources": {}, "module_config": {}},
                {"bnk_forge_export": {"cluster": {"name": "mgx3"}}, "resources": {}, "module_config": {}},
            ]
            mock_diff.return_value = {
                "summary": "1 difference(s) between 'mgx1' and 'mgx3'",
                "total_diffs": 1,
                "cluster_a": "mgx1",
                "cluster_b": "mgx3",
                "resources": {
                    "only_in_a": ["Gateway/default/gw-a"],
                    "only_in_b": [],
                    "changed": [],
                },
                "modules": {
                    "only_in_a": [],
                    "only_in_b": [],
                    "changed": [],
                },
            }

            response = client.post(
                "/api/operators/fleet/compare",
                json={"operator_a_id": cluster_a.id, "operator_b_id": cluster_b.id},
                headers=operator_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["comparison_mode"] == "cluster_config"
        assert data["operator_a"] == "mgx1"
        assert data["operator_b"] == "mgx3"
        assert "platform_context" in data
        assert data["platform_context"]["mixed_platform_profiles"] is False
        assert data["resources"]["only_in_a"] == [
            {"kind": "Gateway", "namespace": "default", "name": "gw-a"},
        ]

    def test_fleet_compare_via_operators(
        self,
        client, operator_headers, all_test_users, db,
    ):
        """Operator can compare two operators' configurations (health-report fallback)."""
        from models import ConnectedOperator

        # Create two operator records in DB
        op_a = ConnectedOperator(
            operator_id="op-a-uuid",
            cluster_name="cluster-a",
            status="connected",
            last_health_report={
                "bnk": {"installed": True, "bnk_version": "1.8.0", "tmm_total": 2},
            },
        )
        op_b = ConnectedOperator(
            operator_id="op-b-uuid",
            cluster_name="cluster-b",
            status="connected",
            last_health_report={
                "bnk": {"installed": True, "bnk_version": "1.9.0", "tmm_total": 3},
            },
        )
        db.add_all([op_a, op_b])
        db.commit()

        response = client.post(
            "/api/operators/fleet/compare",
            json={"operator_a_id": op_a.id, "operator_b_id": op_b.id},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data == {
            "operator_a": "cluster-a",
            "operator_b": "cluster-b",
            "comparison_mode": "health_report",
            "total_diffs": data["total_diffs"],
            "summary": data["summary"],
            "differences": data["differences"],
            "platform_context": None,
        }
        assert data["operator_a"] == "cluster-a"
        assert data["operator_b"] == "cluster-b"
        assert data["comparison_mode"] == "health_report"
        assert data["total_diffs"] > 0
        assert isinstance(data["differences"], list)


class TestFleetRBAC:
    """RBAC enforcement for fleet endpoints."""

    def test_viewer_cannot_compare(self, client, viewer_headers, all_test_users):
        """Viewer cannot compare fleet operators — returns 403."""
        response = client.post(
            "/api/operators/fleet/compare",
            json={"operator_a_id": 1, "operator_b_id": 2},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_unauthenticated_fleet_health(self, client):
        """Unauthenticated request to fleet health returns 401."""
        response = client.get("/api/operators/fleet-health")
        assert response.status_code == 401
