"""
Tests for variable transform security (S23-001).

Covers:
  - JWT detection guard in apply_transformation()
  - _looks_like_jwt() heuristic
  - encoding='raw' in InputSpec
  - Normal base64 encoding still works for non-JWT values
"""

import base64
import os
import sys

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.append(backend_path)

from services.execution.variable_assembler import (
    _looks_like_jwt,
    apply_transformation,
)

# =========================================================================
# _looks_like_jwt heuristic
# =========================================================================

class TestLooksLikeJwt:
    """Tests for the JWT detection heuristic."""

    def test_real_jwt_detected(self):
        # A real-looking JWT (header.payload.signature)
        jwt = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        assert _looks_like_jwt(jwt) is True

    def test_short_jwt_like_detected(self):
        jwt = "eyJhbGci.eyJzdWIi.abc123"
        assert _looks_like_jwt(jwt) is True

    def test_not_jwt_plain_string(self):
        assert _looks_like_jwt("hello world") is False

    def test_not_jwt_base64_string(self):
        assert _looks_like_jwt("SGVsbG8gV29ybGQ=") is False

    def test_not_jwt_two_dots(self):
        assert _looks_like_jwt("a.b") is False

    def test_not_jwt_four_dots(self):
        assert _looks_like_jwt("a.b.c.d") is False

    def test_not_jwt_three_dots_wrong_prefix(self):
        # Three segments but doesn't start with 'eyJ'
        assert _looks_like_jwt("abc.def.ghi") is False

    def test_empty_string(self):
        assert _looks_like_jwt("") is False

    def test_not_jwt_url(self):
        assert _looks_like_jwt("https://example.com/path") is False

    def test_not_jwt_ip_address(self):
        assert _looks_like_jwt("192.168.1.1") is False


# =========================================================================
# apply_transformation — JWT guard
# =========================================================================

class TestApplyTransformationJwtGuard:
    """S23-001: base64 transform must NOT double-encode JWTs."""

    def test_jwt_not_base64_encoded(self):
        jwt = (
            "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        result = apply_transformation(jwt, "base64")
        # Should return the JWT unchanged, NOT base64-encoded
        assert result == jwt

    def test_normal_string_still_base64_encoded(self):
        value = "hello world"
        result = apply_transformation(value, "base64")
        expected = base64.b64encode(b"hello world").decode()
        assert result == expected

    def test_base64_value_still_encoded(self):
        # A regular base64 string (not a JWT) should still be encoded
        value = "SGVsbG8gV29ybGQ="
        result = apply_transformation(value, "base64")
        expected = base64.b64encode(value.encode()).decode()
        assert result == expected

    def test_none_transform_passes_through(self):
        jwt = "eyJhbGci.eyJzdWIi.sig"
        assert apply_transformation(jwt, "none") == jwt
        assert apply_transformation(jwt, "") == jwt
        assert apply_transformation(jwt, None) == jwt

    def test_uppercase_still_works(self):
        assert apply_transformation("hello", "uppercase") == "HELLO"

    def test_lowercase_still_works(self):
        assert apply_transformation("HELLO", "lowercase") == "hello"

    def test_json_encode_still_works(self):
        result = apply_transformation({"key": "value"}, "json_encode")
        assert result == '{"key": "value"}'


# =========================================================================
# InputSpec encoding field
# =========================================================================

from modules.base import InputSpec


class TestInputSpecEncoding:
    """Test the encoding field on InputSpec."""

    def test_default_encoding_is_none(self):
        spec = InputSpec(name="foo")
        assert spec.encoding is None

    def test_raw_encoding(self):
        spec = InputSpec(name="jwt_token", encoding="raw")
        assert spec.encoding == "raw"

