"""
Unit tests for the licensing mutation success contract (D-017).

These tests verify that activate_license, post_license_receipt,
switch_license, and force_renew_license derive their success field
from observable CWC state changes — not from the absence of an exception.
"""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from services.qkview_service import (
    QKViewError,
    _validate_jwt_shape,
    activate_license,
    post_license_receipt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_jwt(alg: str = "RS512", typ: str = "JWT") -> str:
    """Build a minimal structurally-valid JWT (unsigned)."""
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": alg, "typ": typ}).encode()
    ).rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "test"}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{payload}.fakesig"


def _cwc_status_body(state: str, asset_id: str = "") -> dict:
    return {
        "InitialRegistrationStatus": {
            "LicenseStatus": {"State": state},
            "LicenseDetails": {"DigitalAssetID": asset_id},
        }
    }


def _make_k8s_service(cluster_id: int = 9) -> MagicMock:
    svc = MagicMock()
    cluster = MagicMock()
    cluster.id = cluster_id
    svc.get_cluster.return_value = cluster
    svc.load_kubeconfig.return_value = MagicMock()
    return svc


# ---------------------------------------------------------------------------
# _validate_jwt_shape
# ---------------------------------------------------------------------------


class TestValidateJwtShape:
    def test_valid_jwt_returns_none(self):
        assert _validate_jwt_shape(_make_jwt()) is None

    def test_no_dots_returns_error(self):
        err = _validate_jwt_shape("notajwt")
        assert err is not None
        assert "three" in err.lower()

    def test_two_parts_returns_error(self):
        err = _validate_jwt_shape("part1.part2")
        assert err is not None
        assert "three" in err.lower()

    def test_four_parts_returns_error(self):
        err = _validate_jwt_shape("a.b.c.d")
        assert err is not None

    def test_empty_signature_returns_error(self):
        jwt = _make_jwt()
        header_payload = ".".join(jwt.split(".")[:2])
        err = _validate_jwt_shape(f"{header_payload}.")
        assert err is not None
        assert "empty" in err.lower() or "signature" in err.lower()

    def test_garbage_header_returns_error(self):
        err = _validate_jwt_shape("notbase64!.payload.sig")
        assert err is not None
        assert "header" in err.lower()

    def test_header_missing_alg_returns_error(self):
        header = base64.urlsafe_b64encode(
            json.dumps({"typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(b"{}").rstrip(b"=").decode()
        err = _validate_jwt_shape(f"{header}.{payload}.sig")
        assert err is not None
        assert "alg" in err.lower()

    def test_header_missing_typ_returns_error(self):
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "RS512"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(b"{}").rstrip(b"=").decode()
        err = _validate_jwt_shape(f"{header}.{payload}.sig")
        assert err is not None
        assert "typ" in err.lower()


# ---------------------------------------------------------------------------
# activate_license
# ---------------------------------------------------------------------------


class TestActivateLicense:
    def _run(self, cwc_side_effects: list, jwks_side_effect=None) -> dict:
        svc = _make_k8s_service()
        with (
            patch(
                "services.qkview_service._cwc_request",
                side_effect=cwc_side_effects,
            ),
            patch(
                "services.qkview_service.ensure_valid_jwks",
                side_effect=jwks_side_effect or [{"status": "valid", "namespace": "f5-operator"}],
            ),
        ):
            return activate_license(svc, 9, _make_jwt())

    def test_garbage_jwt_empty_cwc_response_returns_false(self):
        # Pre GET /status → some state; POST /reactivate → {}; Post GET /status → same state
        pre_body = _cwc_status_body("Pending", "")
        post_body = _cwc_status_body("Pending", "")
        result = self._run([pre_body, {}, post_body])
        assert result["success"] is False
        assert "did not change" in result["error_message"]
        assert result["license_state_pre"] == "Pending"
        assert result["license_state_post"] == "Pending"

    def test_valid_jwt_state_changes_returns_true(self):
        pre_body = _cwc_status_body("Pending", "")
        post_body = _cwc_status_body("Active", "asset-123")
        result = self._run([pre_body, {}, post_body])
        assert result["success"] is True
        assert result["license_state_pre"] == "Pending"
        assert result["license_state_post"] == "Active"
        assert result["digital_asset_id_post"] == "asset-123"

    def test_asset_id_change_is_sufficient_for_success(self):
        pre_body = _cwc_status_body("Licensed", "old-id")
        post_body = _cwc_status_body("Licensed", "new-id")
        result = self._run([pre_body, {}, post_body])
        assert result["success"] is True

    def test_benign_reactivate_already_active_no_state_change_returns_success(self):
        # Pre state is already in the allow-list and post state is identical —
        # idempotent re-issue with the same JWT should not surface as an error.
        pre_body = _cwc_status_body("Active", "asset-abc")
        post_body = _cwc_status_body("Active", "asset-abc")
        result = self._run([pre_body, {}, post_body])
        assert result["success"] is True
        assert "already active" in result.get("message", "").lower()

    def test_cwc_request_raises_propagates(self):
        svc = _make_k8s_service()
        with (
            patch(
                "services.qkview_service._cwc_request",
                side_effect=QKViewError("no HTTP status returned", status_code=None),
            ),
            patch(
                "services.qkview_service.ensure_valid_jwks",
                return_value={"status": "valid", "namespace": "f5-operator"},
            ),
        ):
            with pytest.raises(QKViewError) as exc_info:
                activate_license(svc, 9, _make_jwt())
        assert "no HTTP status returned" in str(exc_info.value)

    def test_jwks_failure_surfaces_in_response(self):
        pre_body = _cwc_status_body("Pending", "")
        post_body = _cwc_status_body("Active", "asset-abc")
        svc = _make_k8s_service()
        with (
            patch(
                "services.qkview_service._cwc_request",
                side_effect=[pre_body, {}, post_body],
            ),
            patch(
                "services.qkview_service.ensure_valid_jwks",
                side_effect=QKViewError("JWKS not found"),
            ),
        ):
            result = activate_license(svc, 9, _make_jwt())
        assert result["success"] is True  # JWKS failure is non-fatal
        assert result.get("jwks_validation", {}).get("status") == "failed"


# ---------------------------------------------------------------------------
# post_license_receipt
# ---------------------------------------------------------------------------


class TestPostLicenseReceipt:
    def _run(self, cwc_return) -> dict:
        svc = _make_k8s_service()
        with patch(
            "services.qkview_service._cwc_request",
            return_value=cwc_return,
        ):
            return post_license_receipt(svc, 9, "manifest-data")

    def test_empty_response_returns_false(self):
        result = self._run({})
        assert result["success"] is False
        assert "empty response" in result["error_message"].lower()

    def test_raw_empty_string_returns_false(self):
        result = self._run({"raw": ""})
        assert result["success"] is False

    def test_non_empty_response_returns_true(self):
        result = self._run({"receipt": "ok", "status": "accepted"})
        assert result["success"] is True

    def test_cwc_raises_propagates(self):
        svc = _make_k8s_service()
        with patch(
            "services.qkview_service._cwc_request",
            side_effect=QKViewError("no HTTP status returned", status_code=None),
        ):
            with pytest.raises(QKViewError):
                post_license_receipt(svc, 9, "manifest")


# ---------------------------------------------------------------------------
# switch_license (via renew path)
# ---------------------------------------------------------------------------


class TestSwitchLicense:
    def _run(self, jwt: str, cwc_side_effects: list) -> dict:
        from services.qkview_service import switch_license

        svc = _make_k8s_service()
        with (
            patch("services.qkview_service._detect_cwc_namespace", return_value="f5-utils"),
            patch(
                "services.qkview_service._detect_cwc_operator_namespace",
                return_value="f5-operator",
            ),
            patch("services.qkview_service.ensure_valid_jwks", return_value={"status": "valid"}),
            patch(
                "services.qkview_service._cwc_request",
                side_effect=cwc_side_effects,
            ),
            patch(
                "services.qkview_service._patch_cpcl_config_jwt",
                return_value={"status": "patched"},
            ),
        ):
            return switch_license(svc, 9, jwt)

    def test_invalid_jwt_shape_fails_before_cwc(self):
        result = self._run("not-a-jwt", [])
        assert result["success"] is False
        assert "three" in result["error_message"].lower() or "parse" in result["error_message"].lower()
        # No CWC requests should have been made
        assert not any(s["step"] == "reactivate" for s in result["steps"])

    def test_no_state_change_returns_false(self):
        pre_body = _cwc_status_body("Pending", "")
        post_body = _cwc_status_body("Pending", "")
        result = self._run(_make_jwt(), [pre_body, {}, post_body])
        assert result["success"] is False
        assert "did not change" in result["error_message"]

    def test_state_change_returns_true(self):
        pre_body = _cwc_status_body("Pending", "")
        post_body = _cwc_status_body("Active", "xyz")
        result = self._run(_make_jwt(), [pre_body, {}, post_body])
        assert result["success"] is True
        assert result["license_state_post"] == "Active"


# ---------------------------------------------------------------------------
# force_renew_license
# ---------------------------------------------------------------------------


class TestForceRenewLicense:
    def _run(self, jwt: str, cwc_side_effects: list) -> dict:
        from services.qkview_service import force_renew_license

        svc = _make_k8s_service()
        with (
            patch("services.qkview_service._detect_cwc_namespace", return_value="f5-utils"),
            patch(
                "services.qkview_service._detect_cwc_operator_namespace",
                return_value="f5-operator",
            ),
            patch("services.qkview_service.ensure_valid_jwks", return_value={"status": "valid"}),
            patch(
                "services.qkview_service._delete_cpcl_state_secrets",
                return_value={"deleted": [], "skipped": []},
            ),
            patch("services.qkview_service._restart_cwc_pod", return_value="old-pod-xyz"),
            patch("services.qkview_service._cleanup_all_client_pods"),
            patch(
                "services.qkview_service._cwc_request",
                side_effect=cwc_side_effects,
            ),
            patch(
                "services.qkview_service._patch_cpcl_config_jwt",
                return_value={"status": "patched"},
            ),
        ):
            return force_renew_license(svc, 9, jwt)

    def test_invalid_jwt_shape_fails_before_restart(self):
        result = self._run("garbage", [])
        assert result["success"] is False
        assert "parse" in result["error_message"].lower() or "three" in result["error_message"].lower()
        # No pod restart should have occurred — check steps
        assert not any(s["step"] == "cwc_restart" for s in result["steps"])

    def test_no_active_state_post_returns_false(self):
        # /reactivate succeeds but POST /status shows still Pending
        post_body = _cwc_status_body("Pending", "")
        result = self._run(_make_jwt(), [{}, post_body])
        assert result["success"] is False
        assert "did not reach active" in result["error_message"].lower()

    def test_active_state_post_returns_true(self):
        post_body = _cwc_status_body("Active", "new-asset-id")
        result = self._run(_make_jwt(), [{}, post_body])
        assert result["success"] is True
        assert result["license_state_post"] == "Active"
