"""
Unit tests for _check_curl_status and _parse_curl_response hardening.

D-017: absent HTTP_STATUS marker must raise QKViewError, not return
success-shaped {} — which would silently mask an empty CWC response.
"""

import pytest

from services.qkview_service import QKViewError, _check_curl_status, _parse_curl_response


class TestCheckCurlStatus:
    def test_valid_200_passes(self):
        response = '{"ok": true}\n---HTTP_STATUS:200---'
        _check_curl_status(response, "/status")  # no raise

    def test_valid_404_raises(self):
        response = '{"error": "not found"}\n---HTTP_STATUS:404---'
        with pytest.raises(QKViewError) as exc_info:
            _check_curl_status(response, "/status")
        assert exc_info.value.status_code == 404

    def test_valid_500_raises(self):
        response = "internal error\n---HTTP_STATUS:500---"
        with pytest.raises(QKViewError) as exc_info:
            _check_curl_status(response, "/status")
        assert exc_info.value.status_code == 500

    def test_curl_error_prefix_raises(self):
        response = "curl: (6) Could not resolve host: cwc-service"
        with pytest.raises(QKViewError) as exc_info:
            _check_curl_status(response, "/reactivate")
        assert "connection failed" in str(exc_info.value).lower()

    def test_empty_response_raises(self):
        with pytest.raises(QKViewError) as exc_info:
            _check_curl_status("", "/reactivate")
        assert "did not complete" in str(exc_info.value)
        assert exc_info.value.status_code is None

    def test_whitespace_only_raises(self):
        with pytest.raises(QKViewError) as exc_info:
            _check_curl_status("   \n  ", "/reactivate")
        assert "did not complete" in str(exc_info.value)
        assert exc_info.value.status_code is None

    def test_arbitrary_text_no_marker_raises(self):
        with pytest.raises(QKViewError) as exc_info:
            _check_curl_status("some unexpected output", "/reactivate")
        assert "did not complete" in str(exc_info.value)
        assert exc_info.value.status_code is None


class TestParseCurlResponse:
    def test_empty_response_raises(self):
        with pytest.raises(QKViewError) as exc_info:
            _parse_curl_response("", "/reactivate")
        assert "did not complete" in str(exc_info.value)
        assert exc_info.value.status_code is None

    def test_whitespace_only_raises(self):
        with pytest.raises(QKViewError) as exc_info:
            _parse_curl_response("   \t\n  ", "/reactivate")
        assert "did not complete" in str(exc_info.value)

    def test_curl_connection_error_raises(self):
        response = "curl: (6) Could not resolve host: cwc-service.f5-utils.svc"
        with pytest.raises(QKViewError) as exc_info:
            _parse_curl_response(response, "/reactivate")
        assert "connection failed" in str(exc_info.value).lower()

    def test_valid_200_with_json_body(self):
        response = '{"State": "Active"}\n---HTTP_STATUS:200---'
        result = _parse_curl_response(response, "/status")
        assert result == {"State": "Active"}

    def test_valid_200_empty_body_returns_empty_dict(self):
        # A completed request that returns 200 with empty body still emits the marker.
        response = "\n---HTTP_STATUS:200---"
        result = _parse_curl_response(response, "/status")
        assert result == {}

    def test_4xx_body_with_marker_raises(self):
        response = '{"error": "bad request"}\n---HTTP_STATUS:400---'
        with pytest.raises(QKViewError) as exc_info:
            _parse_curl_response(response, "/reactivate")
        assert exc_info.value.status_code == 400

    def test_5xx_body_with_marker_raises(self):
        response = "internal server error\n---HTTP_STATUS:503---"
        with pytest.raises(QKViewError) as exc_info:
            _parse_curl_response(response, "/reactivate")
        assert exc_info.value.status_code == 503

    def test_list_body_wrapped_in_items(self):
        response = '[{"id": 1}, {"id": 2}]\n---HTTP_STATUS:200---'
        result = _parse_curl_response(response, "/items")
        assert result == {"items": [{"id": 1}, {"id": 2}]}

    def test_non_json_body_returns_raw(self):
        response = "not json at all\n---HTTP_STATUS:200---"
        result = _parse_curl_response(response, "/status")
        assert "raw" in result
        assert "not json at all" in result["raw"]

    def test_no_marker_arbitrary_text_raises(self):
        # If CWC returned partial output with no marker, that's not a 200 OK.
        with pytest.raises(QKViewError) as exc_info:
            _parse_curl_response('{"partial": true}', "/reactivate")
        assert "did not complete" in str(exc_info.value)
        assert exc_info.value.status_code is None
