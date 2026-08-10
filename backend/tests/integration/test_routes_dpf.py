"""
Integration tests for DPF routes — /api/k8s/clusters/{cid}/dpf/*.

Covers: detect, data, health, authentication checks.

Uses FastAPI TestClient with real SQLite DB. DPF service functions
and KubernetesService are mocked (no live cluster).
"""

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Realistic DPF data helpers
# ---------------------------------------------------------------------------


def _make_dpf_detect_result(installed: bool = True) -> dict:
    """Build a detect_dpf return value."""
    if not installed:
        return {"installed": False, "operator": None, "devices": [], "clusters": []}
    return {
        "installed": True,
        "operator": {"name": "dpf-operator", "version": "1.2.0", "status": "Running"},
        "devices": [
            {"name": "dpu0", "phase": "DPUReady", "bf_version": "4.6.0"},
        ],
        "clusters": [
            {"name": "dpu-cluster-1", "phase": "Ready", "device_count": 1},
        ],
    }


def _make_dpf_data() -> dict:
    """Build a fetch_all_dpf_data return value with realistic resource shapes."""
    return {
        "resources": {
            "dpf_operator_config": [
                {"metadata": {"name": "dpf-operator"}, "spec": {}, "status": {"phase": "Running"}},
            ],
            "dpu_device": [
                {"metadata": {"name": "dpu0", "namespace": "dpf-system"},
                 "spec": {"bf_version": "4.6.0"},
                 "status": {"phase": "DPUReady", "conditions": []}},
            ],
            "dpu_cluster": [
                {"metadata": {"name": "dpu-cluster-1", "namespace": "dpf-system"},
                 "spec": {},
                 "status": {"phase": "Ready"}},
            ],
            "dpu_service": [
                {"metadata": {"name": "flannel", "namespace": "dpf-system"},
                 "spec": {"serviceType": "Networking"},
                 "status": {"phase": "Ready"}},
            ],
            "dpu_service_chain": [],
            "dpu_service_interface": [],
            "service_function_chain": [],
            "bfb": [
                {"metadata": {"name": "bf-bundle-4.6"},
                 "spec": {"version": "4.6.0"},
                 "status": {"phase": "Ready"}},
            ],
        },
        "cluster_id": 1,
    }


def _make_dpf_health() -> dict:
    """Build an analyze_dpf_health return value."""
    return {
        "overall": "healthy",
        "operator": {"status": "Running", "version": "1.2.0", "severity": "healthy"},
        "devices": {"total": 1, "ready": 1, "severity": "healthy"},
        "clusters": {"total": 1, "ready": 1, "severity": "healthy"},
        "services": {"total": 1, "ready": 1, "severity": "healthy"},
    }


# ---------------------------------------------------------------------------
# GET /api/k8s/clusters/{cluster_id}/dpf/detect
# ---------------------------------------------------------------------------


class TestDPFDetect:
    """GET /api/k8s/clusters/{cluster_id}/dpf/detect."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="dpf-detect-noauth")
        response = client.get(f"/api/k8s/clusters/{cluster.id}/dpf/detect")
        assert response.status_code == 401

    @patch("routes.k8s.dpf.detect_dpf")
    @patch("routes.k8s.dpf.KubernetesService")
    def test_dpf_installed(
        self, mock_k8s_cls, mock_detect,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can detect DPF installed on a cluster."""
        cluster = make_k8s_cluster(project=sample_project, name="dpf-detect-installed")
        mock_detect.return_value = _make_dpf_detect_result(installed=True)

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/dpf/detect",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["installed"] is True
        assert data["operator"]["name"] == "dpf-operator"
        assert len(data["devices"]) == 1
        mock_detect.assert_called_once()

    @patch("routes.k8s.dpf.detect_dpf")
    @patch("routes.k8s.dpf.KubernetesService")
    def test_dpf_not_installed(
        self, mock_k8s_cls, mock_detect,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Returns installed=False when DPF is not present."""
        cluster = make_k8s_cluster(project=sample_project, name="dpf-detect-none")
        mock_detect.return_value = _make_dpf_detect_result(installed=False)

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/dpf/detect",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["installed"] is False


# ---------------------------------------------------------------------------
# GET /api/k8s/clusters/{cluster_id}/dpf/data
# ---------------------------------------------------------------------------


class TestDPFData:
    """GET /api/k8s/clusters/{cluster_id}/dpf/data."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="dpf-data-noauth")
        response = client.get(f"/api/k8s/clusters/{cluster.id}/dpf/data")
        assert response.status_code == 401

    @patch("routes.k8s.dpf.analyze_dpf_health")
    @patch("routes.k8s.dpf.fetch_all_dpf_data")
    @patch("routes.k8s.dpf.KubernetesService")
    def test_returns_data_and_health(
        self, mock_k8s_cls, mock_fetch, mock_health,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can fetch unified DPF data with health analysis."""
        cluster = make_k8s_cluster(project=sample_project, name="dpf-data-cluster")
        dpf_data = _make_dpf_data()
        mock_fetch.return_value = dpf_data
        mock_health.return_value = _make_dpf_health()

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/dpf/data",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()

        assert data["cluster_id"] == cluster.id
        assert "health" in data
        assert "resources" in data
        assert data["health"]["overall"] == "healthy"
        assert "dpu_device" in data["resources"]
        assert len(data["resources"]["dpu_device"]) == 1

        mock_fetch.assert_called_once()
        mock_health.assert_called_once_with(dpf_data)


# ---------------------------------------------------------------------------
# GET /api/k8s/clusters/{cluster_id}/dpf/health
# ---------------------------------------------------------------------------


class TestDPFHealth:
    """GET /api/k8s/clusters/{cluster_id}/dpf/health."""

    def test_requires_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="dpf-health-noauth")
        response = client.get(f"/api/k8s/clusters/{cluster.id}/dpf/health")
        assert response.status_code == 401

    @patch("routes.k8s.dpf.analyze_dpf_health")
    @patch("routes.k8s.dpf.fetch_all_dpf_data")
    @patch("routes.k8s.dpf.KubernetesService")
    def test_returns_health_summary(
        self, mock_k8s_cls, mock_fetch, mock_health,
        client, viewer_headers, all_test_users, sample_project, make_k8s_cluster,
    ):
        """Viewer can get DPF health summary."""
        cluster = make_k8s_cluster(project=sample_project, name="dpf-health-cluster")
        mock_fetch.return_value = _make_dpf_data()
        health = _make_dpf_health()
        mock_health.return_value = health

        response = client.get(
            f"/api/k8s/clusters/{cluster.id}/dpf/health",
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()

        assert data["cluster_id"] == cluster.id
        assert data["overall"] == "healthy"
        assert data["devices"]["total"] == 1
        assert data["devices"]["ready"] == 1
        assert data["services"]["severity"] == "healthy"


# ---------------------------------------------------------------------------
# Bulk auth check
# ---------------------------------------------------------------------------


class TestDPFAllEndpointsRequireAuth:
    """All DPF endpoints reject unauthenticated requests."""

    def test_all_endpoints_require_auth(self, client, sample_project, make_k8s_cluster):
        cluster = make_k8s_cluster(project=sample_project, name="dpf-bulk-noauth")
        cid = cluster.id

        endpoints = [
            f"/api/k8s/clusters/{cid}/dpf/detect",
            f"/api/k8s/clusters/{cid}/dpf/data",
            f"/api/k8s/clusters/{cid}/dpf/health",
        ]
        for url in endpoints:
            resp = client.get(url)
            assert resp.status_code == 401, f"GET {url} should require auth"
