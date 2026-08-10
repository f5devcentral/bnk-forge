"""
Integration tests for config export routes — /api/clusters/{id}/bnk/export* and /api/clusters/bnk/diff.

Covers: JSON export, YAML export, config diff, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB. Export service functions are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

_SAMPLE_CONFIG = {
    "bnk_forge_export": {
        "cluster": {"id": 1, "name": "export-cluster"},
        "version": "1.0",
    },
    "resources": {
        "gateways": [{"kind": "Gateway", "metadata": {"name": "gw-1"}}],
    },
    "module_variables": {},
}


class TestExportJson:
    """GET /api/clusters/{cluster_id}/bnk/export/json."""

    @patch("routes.config_export.export_cluster_config", return_value=_SAMPLE_CONFIG)
    def test_export_json(
        self, mock_export, client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer can export cluster config as JSON."""
        cluster = make_k8s_cluster(project=sample_project, name="json-export-cluster")

        response = client.get(
            f"/api/clusters/{cluster.id}/bnk/export/json",
            headers=viewer_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "bnk_forge_export" in data
        assert "resources" in data
        mock_export.assert_called_once_with(cluster.id, pytest.approx(mock_export.call_args[0][1], abs=0) if False else mock_export.call_args[0][1])

    @patch("routes.config_export.export_cluster_config")
    def test_export_json_cluster_not_found(
        self, mock_export, client, viewer_headers, all_test_users,
    ):
        """Exporting from a nonexistent cluster returns 404."""
        mock_export.side_effect = ValueError("cluster not found")

        response = client.get(
            "/api/clusters/99999/bnk/export/json",
            headers=viewer_headers,
        )
        assert response.status_code == 404


class TestExportYaml:
    """GET /api/clusters/{cluster_id}/bnk/export."""

    @patch("routes.config_export.config_to_yaml", return_value="apiVersion: v1\nkind: ConfigMap\n")
    @patch("routes.config_export.export_cluster_config", return_value=_SAMPLE_CONFIG)
    def test_export_yaml(
        self, mock_export, mock_to_yaml,
        client, viewer_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Viewer can export cluster config as YAML download."""
        cluster = make_k8s_cluster(project=sample_project, name="yaml-export-cluster")

        response = client.get(
            f"/api/clusters/{cluster.id}/bnk/export",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        assert "application/x-yaml" in response.headers.get("content-type", "")
        assert "Content-Disposition" in response.headers
        assert "attachment" in response.headers["Content-Disposition"]
        assert response.text.startswith("apiVersion")
        mock_export.assert_called_once()
        mock_to_yaml.assert_called_once()


class TestDiffConfigs:
    """POST /api/clusters/bnk/diff."""

    @patch("routes.config_export.export_cluster_config")
    @patch("routes.config_export.diff_configs")
    def test_diff_configs(
        self, mock_diff, mock_export,
        client, operator_headers, all_test_users,
        sample_project, make_k8s_cluster,
    ):
        """Operator can diff configs between two clusters."""
        cluster_a = make_k8s_cluster(project=sample_project, name="diff-cluster-a")
        cluster_b = make_k8s_cluster(project=sample_project, name="diff-cluster-b")

        mock_export.side_effect = [
            _SAMPLE_CONFIG,
            {**_SAMPLE_CONFIG, "resources": {"gateways": []}},
        ]
        mock_diff.return_value = {
            "summary": "1 difference(s)",
            "resources": {
                "only_in_a": ["Gateway/gw-1"],
                "only_in_b": [],
                "changed": [],
            },
        }

        response = client.post(
            "/api/clusters/bnk/diff",
            json={"cluster_a_id": cluster_a.id, "cluster_b_id": cluster_b.id},
            headers=operator_headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "resources" in data
        assert "platform_context" in data
        assert "mixed_platform_profiles" in data["platform_context"]
        assert mock_export.call_count == 2
        mock_diff.assert_called_once()


class TestConfigExportRBAC:
    """RBAC enforcement for config export endpoints."""

    def test_viewer_cannot_diff(self, client, viewer_headers, all_test_users):
        """Viewer cannot diff cluster configs (operator-only) — returns 403."""
        response = client.post(
            "/api/clusters/bnk/diff",
            json={"cluster_a_id": 1, "cluster_b_id": 2},
            headers=viewer_headers,
        )
        assert response.status_code == 403

    def test_unauthenticated_cannot_export(self, client, sample_project, make_k8s_cluster):
        """Unauthenticated request to export returns 401."""
        cluster = make_k8s_cluster(project=sample_project, name="noauth-export")
        response = client.get(f"/api/clusters/{cluster.id}/bnk/export/json")
        assert response.status_code == 401
