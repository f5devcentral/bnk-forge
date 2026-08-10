"""
BU-024: Unit tests for auth_service pure functions.

Tests the non-DB functions: hash_password, verify_password,
create_access_token, decode_token. No database or mocking required.
"""

from datetime import timedelta

import pytest

from core.errors import UnauthorizedError
from services.auth_service import (
    JWT_ALGORITHM,
    JWT_EXPIRATION_HOURS,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)

# ── Password hashing ─────────────────────────────────────────────────


class TestHashPassword:
    def test_returns_bcrypt_hash(self):
        hashed = hash_password("mysecret")
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_hash_differs_from_plaintext(self):
        hashed = hash_password("password123")
        assert hashed != "password123"

    def test_same_password_produces_different_hashes(self):
        """bcrypt uses random salt, so two hashes of the same password differ."""
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2

    def test_empty_string_hashes(self):
        """Empty string is technically valid — bcrypt can hash it."""
        hashed = hash_password("")
        assert hashed.startswith("$2")

    def test_unicode_password_hashes(self):
        hashed = hash_password("pässwörd-ünïcödé")
        assert hashed.startswith("$2")

    def test_long_password_hashes(self):
        """bcrypt truncates at 72 bytes, but should still hash without error."""
        hashed = hash_password("a" * 200)
        assert hashed.startswith("$2")


# ── Password verification ────────────────────────────────────────────


class TestVerifyPassword:
    def test_correct_password_returns_true(self):
        hashed = hash_password("correct-horse-battery-staple")
        assert verify_password("correct-horse-battery-staple", hashed) is True

    def test_wrong_password_returns_false(self):
        hashed = hash_password("correct-password")
        assert verify_password("wrong-password", hashed) is False

    def test_empty_password_against_empty_hash(self):
        hashed = hash_password("")
        assert verify_password("", hashed) is True

    def test_unicode_password_verifies(self):
        original = "pässwörd-ünïcödé"
        hashed = hash_password(original)
        assert verify_password(original, hashed) is True
        assert verify_password("wrong", hashed) is False

    def test_case_sensitive(self):
        hashed = hash_password("MyPassword")
        assert verify_password("MyPassword", hashed) is True
        assert verify_password("mypassword", hashed) is False
        assert verify_password("MYPASSWORD", hashed) is False


# ── JWT token creation ───────────────────────────────────────────────


class TestCreateAccessToken:
    def test_returns_string_token(self):
        token = create_access_token(data={"sub": "testuser"})
        assert isinstance(token, str)
        assert len(token) > 20  # JWT tokens are substantial

    def test_token_has_three_parts(self):
        """JWT tokens have header.payload.signature format."""
        token = create_access_token(data={"sub": "testuser"})
        parts = token.split(".")
        assert len(parts) == 3

    def test_token_contains_subject(self):
        """Decoding the token should return the subject."""
        token = create_access_token(data={"sub": "admin"})
        payload = decode_token(token)
        assert payload["sub"] == "admin"

    def test_token_contains_expiration(self):
        token = create_access_token(data={"sub": "user1"})
        payload = decode_token(token)
        assert "exp" in payload

    def test_custom_expiry(self):
        token = create_access_token(
            data={"sub": "user1"},
            expires_delta=timedelta(minutes=30),
        )
        payload = decode_token(token)
        assert "exp" in payload

    def test_additional_claims_preserved(self):
        token = create_access_token(data={"sub": "user1", "role": "admin", "custom": 42})
        payload = decode_token(token)
        assert payload["sub"] == "user1"
        assert payload["role"] == "admin"
        assert payload["custom"] == 42

    def test_original_data_not_mutated(self):
        data = {"sub": "user1"}
        create_access_token(data=data)
        assert "exp" not in data  # .copy() in create_access_token prevents mutation


# ── JWT token decoding ───────────────────────────────────────────────


class TestDecodeToken:
    def test_valid_token_decodes(self):
        token = create_access_token(data={"sub": "testuser"})
        payload = decode_token(token)
        assert payload["sub"] == "testuser"

    def test_invalid_token_raises_unauthorized(self):
        with pytest.raises(UnauthorizedError) as exc_info:
            decode_token("not-a-valid-jwt-token")
        assert "Invalid token" in exc_info.value.message

    def test_empty_string_raises_unauthorized(self):
        with pytest.raises(UnauthorizedError):
            decode_token("")

    def test_tampered_token_raises_unauthorized(self):
        """Modifying a token should invalidate the signature."""
        token = create_access_token(data={"sub": "user1"})
        # Tamper with the token by modifying a character in the payload section
        parts = token.split(".")
        tampered_payload = parts[1] + "x"
        tampered_token = f"{parts[0]}.{tampered_payload}.{parts[2]}"
        with pytest.raises(UnauthorizedError):
            decode_token(tampered_token)

    def test_missing_subject_raises_unauthorized(self):
        """A token without 'sub' claim should fail."""
        token = create_access_token(data={"role": "admin"})
        # create_access_token doesn't require sub, but decode_token does
        with pytest.raises(UnauthorizedError) as exc_info:
            decode_token(token)
        assert "missing subject" in exc_info.value.message

    def test_expired_token_raises_unauthorized(self):
        """A token with a past expiry should fail."""
        token = create_access_token(
            data={"sub": "user1"},
            expires_delta=timedelta(seconds=-1),  # Already expired
        )
        with pytest.raises(UnauthorizedError) as exc_info:
            decode_token(token)
        assert "Invalid token" in exc_info.value.message


# ── Constants ────────────────────────────────────────────────────────


class TestAuthConstants:
    def test_algorithm_is_hs256(self):
        assert JWT_ALGORITHM == "HS256"

    def test_expiration_hours_is_24(self):
        assert JWT_EXPIRATION_HOURS == 24
