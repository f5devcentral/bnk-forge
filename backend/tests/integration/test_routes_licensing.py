"""Integration tests for licensing routes contract shapes."""

from unittest.mock import patch


class TestLicensingStatusContracts:
    """Contract tests for typed licensing responses."""

    @patch("routes.licensing.legacy_get_license_status")
    @patch("routes.licensing._try_operator_dispatch")
    def test_status_returns_typed_contract_keys(
        self,
        mock_dispatch,
        mock_legacy_status,
        client,
        viewer_headers,
        all_test_users,
    ):
        mock_dispatch.return_value = None
        mock_legacy_status.return_value = {
            "success": True,
            "Status": {
                "LicenseStatus": {"State": "Active"},
                "TelemetryStatus": {"State": "Running"},
            },
            "extra_field": "preserved",
        }

        resp = client.get("/api/licensing/10/status", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()

        # Typed response_model keeps required key and allows passthrough extras
        assert "success" in data
        # Optional None fields may be omitted by response serialization
        assert "operator_dispatch" not in data or data["operator_dispatch"] is None
        assert "Status" in data
        assert data["success"] is True
        assert data["extra_field"] == "preserved"

    @patch("routes.licensing._try_operator_dispatch")
    def test_cwc_status_returns_typed_contract_keys(
        self,
        mock_dispatch,
        client,
        viewer_headers,
        all_test_users,
    ):
        mock_dispatch.return_value = {
            "success": True,
            "cwc_service_found": True,
            "cwc_reachable": True,
            "certs_mounted": True,
            "setup_complete": True,
            "cert_manager_available": True,
            "server_cert_exists": True,
            "client_cert_exists": True,
            "message": "ok",
        }

        resp = client.get("/api/licensing/10/cwc-status", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert set(data.keys()) == {
            "cwc_service_found",
            "cwc_reachable",
            "certs_mounted",
            "setup_complete",
            "cert_manager_available",
            "message",
            "operator_dispatch",
            "success",
            "server_cert_exists",
            "client_cert_exists",
        }
        assert data["operator_dispatch"] is True
        assert data["success"] is True

    def test_status_requires_auth(self, client):
        resp = client.get("/api/licensing/10/status")
        assert resp.status_code == 401

    @patch("routes.licensing.legacy_get_license_report")
    @patch("routes.licensing._try_operator_dispatch")
    def test_report_returns_typed_contract(
        self,
        mock_dispatch,
        mock_legacy_report,
        client,
        viewer_headers,
        all_test_users,
    ):
        mock_dispatch.return_value = None
        mock_legacy_report.return_value = {
            "success": True,
            "raw": "signed-report-data",
            "extra": "kept",
        }

        resp = client.get("/api/licensing/10/report", headers=viewer_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert "success" in data
        assert "raw" in data
        assert "operator_dispatch" not in data or data["operator_dispatch"] is None
        assert data["success"] is True
        assert data["raw"] == "signed-report-data"
        assert data["extra"] == "kept"

    @patch("routes.licensing.legacy_activate_license")
    @patch("routes.licensing._try_operator_dispatch")
    def test_activate_returns_typed_contract(
        self,
        mock_dispatch,
        mock_legacy_activate,
        client,
        operator_headers,
        all_test_users,
    ):
        mock_dispatch.return_value = None
        mock_legacy_activate.return_value = {
            "success": True,
            "message": "activated",
            "receipt_required": False,
        }

        resp = client.post(
            "/api/licensing/10/activate",
            headers=operator_headers,
            json={"jwt": "jwt-data"},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert data["message"] == "activated"
        assert data["receipt_required"] is False
        assert "operator_dispatch" not in data or data["operator_dispatch"] is None

    @patch("routes.licensing.legacy_post_license_receipt")
    @patch("routes.licensing._try_operator_dispatch")
    def test_receipt_returns_typed_contract(
        self,
        mock_dispatch,
        mock_legacy_receipt,
        client,
        operator_headers,
        all_test_users,
    ):
        mock_dispatch.return_value = None
        mock_legacy_receipt.return_value = {
            "success": True,
            "message": "receipt accepted",
        }

        resp = client.post(
            "/api/licensing/10/receipt",
            headers=operator_headers,
            json={"manifest": "signed-manifest"},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert data["message"] == "receipt accepted"
        assert "operator_dispatch" not in data or data["operator_dispatch"] is None
