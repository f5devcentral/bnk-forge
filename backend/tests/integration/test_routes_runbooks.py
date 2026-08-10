"""
Integration tests for runbook routes — /api/runbooks.

Covers: list runbooks, execute runbook, handle nonexistent runbook, auth enforcement.
Endpoints delegate to runbook_service functions and KubernetesService (all mocked).
Uses FastAPI TestClient with real SQLite DB.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.errors import NotFoundError


class TestListRunbooks:
    """GET /api/runbooks."""

    @patch("routes.runbooks.list_runbooks")
    def test_list_runbooks(self, mock_list, client, viewer_headers, all_test_users):
        """Viewer can list all available runbooks."""
        mock_list.return_value = [
            {
                "id": "network-diagnostics",
                "name": "Network Diagnostics",
                "description": "Diagnose network connectivity issues",
                "category": "networking",
                "step_count": 4,
                "steps": [
                    {"name": "Check DNS", "description": "Verify DNS resolution"},
                    {"name": "Check Services", "description": "Verify service endpoints"},
                    {"name": "Check Ingress", "description": "Verify ingress rules"},
                    {"name": "Check Network Policies", "description": "Review network policies"},
                ],
            },
            {
                "id": "pod-health",
                "name": "Pod Health Check",
                "description": "Diagnose pod health issues",
                "category": "workloads",
                "step_count": 3,
                "steps": [
                    {"name": "Check Pod Status", "description": "List pod statuses"},
                    {"name": "Check Events", "description": "Review recent events"},
                    {"name": "Check Resources", "description": "Verify resource limits"},
                ],
            },
        ]

        response = client.get("/api/runbooks", headers=viewer_headers)
        assert response.status_code == 200
        data = response.json()
        assert "runbooks" in data
        assert len(data["runbooks"]) == 2
        assert data["runbooks"][0]["id"] == "network-diagnostics"
        assert data["runbooks"][0]["step_count"] == 4


class TestExecuteRunbook:
    """POST /api/runbooks/{runbook_id}/run."""

    @patch("routes.runbooks.KubernetesService")
    @patch("routes.runbooks.execute_runbook")
    def test_execute_runbook(
        self, mock_execute, mock_k8s_svc_cls, client, viewer_headers, all_test_users
    ):
        """Viewer can execute a runbook against a cluster."""
        mock_execute.return_value = {
            "runbook_id": "pod-health",
            "runbook_name": "Pod Health Check",
            "cluster_id": 1,
            "status": "completed",
            "steps": [
                {"name": "Check Pod Status", "status": "pass", "message": "All pods healthy"},
                {"name": "Check Events", "status": "pass", "message": "No warning events"},
                {"name": "Check Resources", "status": "fail", "message": "2 pods over memory limit"},
            ],
            "summary": {"total": 3, "passed": 2, "failed": 1, "skipped": 0},
        }

        response = client.post(
            "/api/runbooks/pod-health/run",
            json={"cluster_id": 1},
            headers=viewer_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["runbook_id"] == "pod-health"
        assert data["status"] == "completed"
        assert len(data["steps"]) == 3
        assert data["summary"]["passed"] == 2
        assert data["summary"]["failed"] == 1
        mock_execute.assert_called_once()

    @patch("routes.runbooks.KubernetesService")
    @patch("routes.runbooks.execute_runbook")
    def test_execute_nonexistent_runbook(
        self, mock_execute, mock_k8s_svc_cls, client, viewer_headers, all_test_users
    ):
        """Executing a nonexistent runbook returns 404."""
        mock_execute.side_effect = ValueError("Unknown runbook: does-not-exist")

        response = client.post(
            "/api/runbooks/does-not-exist/run",
            json={"cluster_id": 1},
            headers=viewer_headers,
        )
        assert response.status_code == 404


class TestRunbookAuthentication:
    """Authentication enforcement for runbook endpoints."""

    def test_unauthenticated_list(self, client):
        """Unauthenticated request to list runbooks returns 401."""
        response = client.get("/api/runbooks")
        assert response.status_code == 401

    def test_unauthenticated_execute(self, client):
        """Unauthenticated request to execute a runbook returns 401."""
        response = client.post(
            "/api/runbooks/pod-health/run",
            json={"cluster_id": 1},
        )
        assert response.status_code == 401
