"""Unit tests for cloud client methods in BnkForgeClient.

Tests verify:
- Correct URL path and request body for each method
- Successful response parsing
- Error path via BnkForgeApiError
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts.e2e.client import BnkForgeApiError, BnkForgeClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> BnkForgeClient:
    """Client pointed at a fake base URL with a fake token."""
    c = BnkForgeClient("https://forge.test")
    c._token = "fake-token"  # noqa: SLF001
    return c


def _mock_response(status_code: int, json_body: Any) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


# ---------------------------------------------------------------------------
# list_blueprint_releases
# ---------------------------------------------------------------------------


class TestListBlueprintReleases:
    def test_returns_list(self):
        client = _make_client()
        releases = [{"id": 1, "blueprint_name": "eks-bnk"}]
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, releases)
            result = client.list_blueprint_releases()
        assert result == releases
        call = mock_req.call_args
        assert call.args[0] == "GET"
        assert call.args[1].endswith("/api/blueprint-catalog/releases")

    def test_returns_empty_list_on_non_list_body(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"unexpected": True})
            result = client.list_blueprint_releases()
        assert result == []

    def test_raises_on_error_status(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(401, {"detail": "Unauthorized"})
            with pytest.raises(BnkForgeApiError) as exc_info:
                client.list_blueprint_releases()
        assert exc_info.value.status == 401


# ---------------------------------------------------------------------------
# get_release
# ---------------------------------------------------------------------------


class TestGetRelease:
    def test_correct_path(self):
        client = _make_client()
        payload = {"id": 5, "blueprint_name": "eks-bnk", "validation_state": "valid"}
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, payload)
            result = client.get_release(5)
        assert result["id"] == 5
        assert mock_req.call_args.args[1].endswith("/api/blueprint-catalog/releases/5")


# ---------------------------------------------------------------------------
# get_release_required_inputs
# ---------------------------------------------------------------------------


class TestGetReleaseRequiredInputs:
    def test_correct_path(self):
        client = _make_client()
        payload = {"all_inputs": [], "total_required": 0}
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, payload)
            result = client.get_release_required_inputs(3)
        assert result == payload
        assert mock_req.call_args.args[1].endswith("/api/stacks/releases/3/required-inputs")


# ---------------------------------------------------------------------------
# create_project_from_release
# ---------------------------------------------------------------------------


class TestCreateProjectFromRelease:
    def test_correct_path_and_body(self):
        client = _make_client()
        resp_payload = {
            "success": True,
            "project_id": 42,
            "project_name": "e2e-cloud-20260101",
            "blueprint_release_id": 5,
            "module_count": 3,
            "created_module_ids": [1, 2, 3],
            "message": "created",
        }
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, resp_payload)
            result = client.create_project_from_release(
                5,
                cloud_provider="aws",
                region="us-west-2",
                credential_template_id=7,
                variables={"vpc_cidr": "10.0.0.0/16"},
                name="e2e-cloud-20260101",
            )
        assert result["project_id"] == 42
        call = mock_req.call_args
        assert call.args[0] == "POST"
        assert call.args[1].endswith("/api/stacks/releases/5/projects")
        body = call.kwargs["json"]
        assert body["cloud_provider"] == "aws"
        assert body["region"] == "us-west-2"
        assert body["credential_template_id"] == 7
        assert body["variables"] == {"vpc_cidr": "10.0.0.0/16"}
        assert body["name"] == "e2e-cloud-20260101"

    def test_raises_on_error(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(
                400, {"detail": "Blueprint release not deployable"},
            )
            with pytest.raises(BnkForgeApiError) as exc_info:
                client.create_project_from_release(
                    5,
                    cloud_provider="aws",
                    region="us-west-2",
                    credential_template_id=None,
                    variables={},
                    name="test",
                )
        assert exc_info.value.status == 400


# ---------------------------------------------------------------------------
# deploy_all / destroy_all
# ---------------------------------------------------------------------------


class TestDeployAll:
    def test_correct_path_and_returns_handle(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(
                200,
                {"orchestrator_task_id": "abc123", "total_modules": 3, "total_layers": 2},
            )
            result = client.deploy_all(10)
        assert result["orchestrator_task_id"] == "abc123"
        call = mock_req.call_args
        assert call.args[0] == "POST"
        assert call.args[1].endswith("/api/projects/10/deploy-all")


class TestDestroyAll:
    def test_correct_path_and_returns_handle(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(
                200,
                {"orchestrator_task_id": "def456", "total_modules": 3},
            )
            result = client.destroy_all(10)
        assert result["orchestrator_task_id"] == "def456"
        assert mock_req.call_args.args[1].endswith("/api/projects/10/destroy-all")


# ---------------------------------------------------------------------------
# get_parallel_executions / get_parallel_execution_status
# ---------------------------------------------------------------------------


class TestGetParallelExecutions:
    def test_correct_path_and_returns_list(self):
        client = _make_client()
        records = [
            {
                "id": 7,
                "action": "deploy",
                "status": "in_progress",
                "current_layer": 1,
                "total_layers": 3,
                "started_at": "2026-06-13T10:00:00",
                "completed_at": None,
                "duration_seconds": None,
                "successful_modules": 2,
                "failed_modules": 0,
                "triggered_by": "admin",
            }
        ]
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, records)
            result = client.get_parallel_executions(10)
        assert result == records
        call = mock_req.call_args
        assert call.args[0] == "GET"
        assert call.args[1].endswith("/api/projects/10/parallel-executions")

    def test_returns_empty_list_on_non_list_body(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"unexpected": True})
            result = client.get_parallel_executions(10)
        assert result == []

    def test_raises_on_error_status(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(404, {"detail": "Not found"})
            with pytest.raises(BnkForgeApiError) as exc_info:
                client.get_parallel_executions(10)
        assert exc_info.value.status == 404


class TestGetParallelExecutionStatus:
    def test_correct_path_with_integer_exec_id(self):
        client = _make_client()
        payload = {
            "id": 7,
            "project_id": 10,
            "action": "deploy",
            "status": "completed",
            "current_layer": 3,
            "total_layers": 3,
            "layers_data": {},
            "execution_mode": "sequential",
            "successful_modules": [1, 2, 3],
            "failed_modules": [],
            "skipped_modules": [],
            "error_message": None,
            "started_at": "2026-06-13T10:00:00",
            "completed_at": "2026-06-13T10:05:00",
            "duration_seconds": 300.0,
            "estimated_duration_minutes": 5,
            "progress_percent": 100.0,
            "triggered_by": "admin",
            "created_at": "2026-06-13T09:59:59",
        }
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, payload)
            result = client.get_parallel_execution_status(10, 7)
        assert result["status"] == "completed"
        assert result["id"] == 7
        assert mock_req.call_args.args[1].endswith(
            "/api/projects/10/parallel-executions/7",
        )

    def test_in_progress_status(self):
        client = _make_client()
        payload = {"id": 7, "status": "in_progress", "progress_percent": 50.0}
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, payload)
            result = client.get_parallel_execution_status(10, 7)
        assert result["status"] == "in_progress"

    def test_raises_on_error_status(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(404, {"detail": "Not found"})
            with pytest.raises(BnkForgeApiError) as exc_info:
                client.get_parallel_execution_status(10, 999)
        assert exc_info.value.status == 404


# ---------------------------------------------------------------------------
# get_bnk_health
# ---------------------------------------------------------------------------


class TestGetBnkHealth:
    def test_correct_path(self):
        client = _make_client()
        payload = {"overall": "healthy", "platform": {"severity": "healthy"}, "counts": {}}
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, payload)
            result = client.get_bnk_health(3)
        assert result["overall"] == "healthy"
        assert mock_req.call_args.args[1].endswith("/api/k8s/clusters/3/f5bnk/health")


# ---------------------------------------------------------------------------
# get_license_status / activate_license
# ---------------------------------------------------------------------------


class TestLicenseMethods:
    def test_get_license_status_path(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(
                200, {"success": True, "license_state": "Active"},
            )
            result = client.get_license_status(3)
        assert result["license_state"] == "Active"
        assert mock_req.call_args.args[1].endswith("/api/licensing/3/status")

    def test_activate_license_body(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"success": True})
            client.activate_license(3, "myjwt")
        call = mock_req.call_args
        assert call.args[0] == "POST"
        assert call.args[1].endswith("/api/licensing/3/activate")
        assert call.kwargs["json"]["jwt"] == "myjwt"

    def test_get_cwc_status_path(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(
                200,
                {"cwc_service_found": True, "cwc_reachable": True,
                 "setup_complete": True, "certs_mounted": True,
                 "cert_manager_available": True},
            )
            result = client.get_cwc_status(3)
        assert result["cwc_service_found"] is True
        assert mock_req.call_args.args[1].endswith("/api/licensing/3/cwc-status")


# ---------------------------------------------------------------------------
# delete_stack / delete_project
# ---------------------------------------------------------------------------


class TestDeleteMethods:
    def test_delete_stack_force_path(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"deleted": True})
            client.delete_stack(10, 7, force=True)
        call = mock_req.call_args
        assert call.args[0] == "DELETE"
        assert "force=true" in call.args[1]
        assert "/api/stacks/projects/10/stacks/7" in call.args[1]

    def test_delete_stack_no_force(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"deleted": True})
            client.delete_stack(10, 7, force=False)
        url = mock_req.call_args.args[1]
        assert "force" not in url

    def test_delete_project_path(self):
        client = _make_client()
        with patch.object(client.session, "request") as mock_req:
            mock_req.return_value = _mock_response(200, {"deleted": True})
            client.delete_project(10)
        call = mock_req.call_args
        assert call.args[0] == "DELETE"
        assert call.args[1].endswith("/api/projects/10")
