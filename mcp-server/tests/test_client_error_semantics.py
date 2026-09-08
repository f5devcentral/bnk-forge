"""Focused tests for MCP client error semantics."""

from __future__ import annotations

from bnk_forge_mcp.client import APIError, BNKForgeClient
from bnk_forge_mcp.config import MCPConfig


def _client() -> BNKForgeClient:
    return BNKForgeClient(
        MCPConfig(
            api_base_url="http://test-backend:8000",
            api_token="test-token",
            api_timeout=5,
            verify_ssl=False,
        )
    )


def test_api_error_classification_and_retryability() -> None:
    err = APIError(404, "missing", "/api/example")
    assert err.error_class == "not_found"
    assert err.retryable is False

    transient = APIError(503, "service unavailable", "/api/example")
    assert transient.error_class == "transient_error"
    assert transient.retryable is True


def test_error_envelope_contains_actionable_fields() -> None:
    client = _client()
    err = APIError(403, "forbidden", "/api/secure")

    payload = client._error_payload("GET", "/api/secure", err)

    assert payload["ok"] is False
    assert payload["request"] == {"method": "GET", "path": "/api/secure"}
    assert payload["error"]["status_code"] == 403
    assert payload["error"]["error_class"] == "auth_error"
    assert payload["error"]["retryable"] is False
    assert payload["error"]["next_action"]


# ---------------------------------------------------------------------------
# #67 -- structured backend errors must survive as JSON, not a Python repr
# ---------------------------------------------------------------------------

def _resp(status: int, body, *, raw: str | None = None):
    """Minimal stand-in for httpx.Response: .json() and .text."""
    import json as _json

    class _R:
        status_code = status
        text = raw if raw is not None else (_json.dumps(body) if body is not None else "")

        def json(self):
            if body is None:
                raise ValueError("not json")
            return body

    return _R()


def test_structured_backend_error_is_not_a_python_repr() -> None:
    """The issue's live evidence: a 404 whose body is the backend's structured
    error shape came back as detail="{'code': 'PROJECT_NOT_FOUND', ...}" --
    single quotes, unparseable. The string must be the human message and the
    structured parts must be first-class."""
    import json as _json

    body = {
        "code": "PROJECT_NOT_FOUND",
        "message": "Project not found",
        "details": {"project_id": "999999"},
        "path": "/api/projects/999999",
        "request_id": "abc",
    }
    parsed = BNKForgeClient._parse_error_body(_resp(404, body))

    assert parsed["detail"] == "Project not found"
    assert parsed["code"] == "PROJECT_NOT_FOUND"
    assert parsed["details"] == {"project_id": "999999"}
    # The envelope the agent sees round-trips through JSON with the code intact.
    err = APIError(404, parsed["detail"], "/api/projects/999999",
                   code=parsed["code"], details=parsed["details"])
    payload = _client()._error_payload("GET", "/api/projects/999999", err)
    wire = _json.loads(_json.dumps(payload))
    assert wire["error"]["code"] == "PROJECT_NOT_FOUND"
    assert wire["error"]["details"]["project_id"] == "999999"
    assert "'" not in wire["error"]["detail"]  # no repr leaking into the text


def test_real_backend_shape_nested_under_error_is_unwrapped() -> None:
    """The backend's actual handler (core.errors.format_error_response) emits
    {"error": {code, message, details, path, request_id}} -- nested under
    "error", NOT top-level and NOT under "detail". This is precisely the dict
    the old code str()'d (its key loop hit "error"), producing the repr in the
    issue. The parser must unwrap THIS shape, not only FastAPI's."""
    body = {"error": {
        "code": "PROJECT_NOT_FOUND",
        "message": "Project not found",
        "details": {"project_id": "999999"},
        "path": "/api/projects/999999",
        "request_id": "abc",
    }}
    parsed = BNKForgeClient._parse_error_body(_resp(404, body))
    assert parsed["detail"] == "Project not found"
    assert parsed["code"] == "PROJECT_NOT_FOUND"
    assert parsed["details"] == {"project_id": "999999"}


def test_fastapi_wrapped_structured_error_is_unwrapped() -> None:
    """FastAPI HTTPException nests the payload under "detail"; the structured
    dict must still be found one level down."""
    body = {"detail": {"code": "CONFIRMATION_REQUIRED", "message": "confirm it", "details": {"tool": "x"}}}
    parsed = BNKForgeClient._parse_error_body(_resp(400, body))
    assert parsed["detail"] == "confirm it"
    assert parsed["code"] == "CONFIRMATION_REQUIRED"
    assert parsed["details"] == {"tool": "x"}


def test_plain_string_detail_still_works() -> None:
    parsed = BNKForgeClient._parse_error_body(_resp(404, {"detail": "Not Found"}))
    assert parsed == {"detail": "Not Found", "code": None, "details": None}


def test_non_json_body_falls_back_to_text() -> None:
    parsed = BNKForgeClient._parse_error_body(_resp(502, None, raw="<html>bad gateway</html>"))
    assert parsed["detail"] == "<html>bad gateway</html>"
    assert parsed["code"] is None


def test_dict_with_no_human_message_renders_as_json_not_repr() -> None:
    """Even the worst case -- a dict with no message at all -- must be JSON."""
    import json as _json

    parsed = BNKForgeClient._parse_error_body(_resp(500, {"weird": {"shape": 1}}))
    _json.loads(parsed["detail"])  # parses; a Python repr would raise here
    assert "'" not in parsed["detail"]


def test_extract_error_detail_is_backward_compatible() -> None:
    """Existing callers of the string helper keep getting the human string."""
    body = {"code": "X", "message": "human text", "details": {}}
    assert BNKForgeClient._extract_error_detail(_resp(400, body)) == "human text"
