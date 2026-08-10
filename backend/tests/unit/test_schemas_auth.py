"""
BU-012 + CT-037: Unit tests for schemas.auth module.

Tests Pydantic schema validation for auth domain:
LoginRequest, ChangePasswordRequest, UserCreateRequest, UserUpdateRequest.
Includes negative validation tests (CT-037).
"""

import pytest
from pydantic import ValidationError

from schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    UserCreateRequest,
    UserCreateResponse,
    UserDeleteResponse,
    UserInfo,
    UserListResponse,
    UserResponse,
    UserUpdateRequest,
)


class TestLoginRequest:
    def test_valid_login(self):
        req = LoginRequest(username="admin", password="secret123")
        assert req.username == "admin"
        assert req.password == "secret123"

    def test_empty_username_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="", password="secret")

    def test_empty_password_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="admin", password="")

    def test_missing_username_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(password="secret")

    def test_missing_password_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="admin")


class TestChangePasswordRequest:
    def test_valid_change(self):
        req = ChangePasswordRequest(current_password="old", new_password="newpass12")
        assert req.current_password == "old"
        assert req.new_password == "newpass12"

    def test_short_new_password_rejected(self):
        """New password must be at least 8 characters."""
        with pytest.raises(ValidationError):
            ChangePasswordRequest(current_password="old", new_password="short")

    def test_empty_current_password_rejected(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(current_password="", new_password="newpass12")

    def test_exactly_8_chars_accepted(self):
        req = ChangePasswordRequest(current_password="x", new_password="12345678")
        assert len(req.new_password) == 8


class TestUserCreateRequest:
    def test_valid_create(self):
        req = UserCreateRequest(
            username="newuser",
            email="new@test.com",
            password="password123",
        )
        assert req.role == "operator"  # default

    def test_custom_role(self):
        req = UserCreateRequest(
            username="admin2",
            email="admin2@test.com",
            password="password123",
            role="admin",
        )
        assert req.role == "admin"

    def test_short_password_rejected(self):
        with pytest.raises(ValidationError):
            UserCreateRequest(
                username="user",
                email="user@test.com",
                password="short",
            )

    def test_long_username_rejected(self):
        with pytest.raises(ValidationError):
            UserCreateRequest(
                username="a" * 101,
                email="user@test.com",
                password="password123",
            )


class TestUserInfo:
    def test_full_user_info(self):
        info = UserInfo(
            id=1,
            username="admin",
            email="admin@test.com",
            role="admin",
            is_active=True,
            must_change_password=False,
            last_login_at="2026-01-01T00:00:00",
            created_at="2025-12-01T00:00:00",
        )
        assert info.id == 1
        assert info.is_active is True

    def test_optional_fields_default_to_none(self):
        info = UserInfo(
            id=1,
            username="user",
            email="user@test.com",
            role="viewer",
            is_active=True,
            must_change_password=True,
        )
        assert info.last_login_at is None
        assert info.created_at is None


class TestLoginResponse:
    def test_valid_response(self):
        resp = LoginResponse(
            token="jwt.token.here",
            user=UserInfo(
                id=1,
                username="admin",
                email="a@b.com",
                role="admin",
                is_active=True,
                must_change_password=False,
            ),
            must_change_password=False,
        )
        assert resp.token == "jwt.token.here"
        assert resp.user.username == "admin"


class TestResponseSchemas:
    """Quick validation that response schemas can be constructed."""

    def test_user_response(self):
        resp = UserResponse(
            id=1, username="u", email="e", role="admin",
            is_active=True, must_change_password=False,
        )
        assert resp.id == 1

    def test_user_list_response(self):
        resp = UserListResponse(users=[])
        assert resp.users == []

    def test_user_create_response(self):
        user = UserResponse(
            id=1, username="u", email="e", role="admin",
            is_active=True, must_change_password=False,
        )
        resp = UserCreateResponse(success=True, user=user)
        assert resp.success is True

    def test_user_delete_response(self):
        resp = UserDeleteResponse(success=True, message="Deleted")
        assert resp.message == "Deleted"

    def test_me_response(self):
        info = UserInfo(
            id=1, username="u", email="e", role="admin",
            is_active=True, must_change_password=False,
        )
        resp = MeResponse(user=info)
        assert resp.user.role == "admin"


# =====================================================================
# CT-037: Negative schema tests — wrong payload shapes → ValidationError
# =====================================================================


class TestLoginRequestNegative:
    def test_missing_username_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(password="secret")  # type: ignore[call-arg]

    def test_missing_password_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="admin")  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest()  # type: ignore[call-arg]

    def test_username_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(username=123, password="secret")  # type: ignore[arg-type]

    def test_password_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            LoginRequest(username="admin", password=123)  # type: ignore[arg-type]


class TestChangePasswordRequestNegative:
    def test_missing_current_password_rejected(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(new_password="newpass12")  # type: ignore[call-arg]

    def test_missing_new_password_rejected(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(current_password="old")  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest()  # type: ignore[call-arg]

    def test_new_password_too_short_rejected(self):
        """new_password has min_length=8."""
        with pytest.raises(ValidationError):
            ChangePasswordRequest(current_password="old", new_password="short")

    def test_new_password_exactly_7_chars_rejected(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(current_password="old", new_password="1234567")

    def test_current_password_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(current_password=123, new_password="newpass12")  # type: ignore[arg-type]


class TestUserCreateRequestNegative:
    def test_missing_username_rejected(self):
        with pytest.raises(ValidationError):
            UserCreateRequest(email="e@t.com", password="password123")  # type: ignore[call-arg]

    def test_missing_email_rejected(self):
        with pytest.raises(ValidationError):
            UserCreateRequest(username="user", password="password123")  # type: ignore[call-arg]

    def test_missing_password_rejected(self):
        with pytest.raises(ValidationError):
            UserCreateRequest(username="user", email="e@t.com")  # type: ignore[call-arg]

    def test_missing_all_required_rejected(self):
        with pytest.raises(ValidationError):
            UserCreateRequest()  # type: ignore[call-arg]

    def test_password_too_short_rejected(self):
        """password has min_length=8."""
        with pytest.raises(ValidationError):
            UserCreateRequest(username="user", email="e@t.com", password="short")

    def test_username_too_long_rejected(self):
        """username has max_length=100."""
        with pytest.raises(ValidationError):
            UserCreateRequest(username="x" * 101, email="e@t.com", password="password123")

    def test_empty_username_rejected(self):
        """username has min_length=1."""
        with pytest.raises(ValidationError):
            UserCreateRequest(username="", email="e@t.com", password="password123")

    def test_empty_email_rejected(self):
        """email has min_length=1."""
        with pytest.raises(ValidationError):
            UserCreateRequest(username="user", email="", password="password123")

    def test_username_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            UserCreateRequest(username=123, email="e@t.com", password="password123")  # type: ignore[arg-type]


class TestUserUpdateRequestNegative:
    def test_all_optional(self):
        """Update with nothing is valid (all fields optional)."""
        req = UserUpdateRequest()
        assert req.email is None
        assert req.role is None
        assert req.is_active is None

    def test_partial_update_valid(self):
        req = UserUpdateRequest(role="admin", is_active=False)
        assert req.role == "admin"
        assert req.is_active is False

    def test_empty_email_rejected(self):
        """email has min_length=1 when provided."""
        with pytest.raises(ValidationError):
            UserUpdateRequest(email="")

    def test_email_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            UserUpdateRequest(email=123)  # type: ignore[arg-type]

    def test_is_active_wrong_type_rejected(self):
        """Pydantic v2 lax mode coerces truthy strings to bool, but dicts should fail."""
        with pytest.raises(ValidationError):
            UserUpdateRequest(is_active={"value": True})  # type: ignore[arg-type]

    def test_role_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            UserUpdateRequest(role=123)  # type: ignore[arg-type]
