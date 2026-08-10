"""
Golden contract tests — Auth domain.

Validates that auth endpoints return responses matching their declared
Pydantic response_model schemas. These tests exercise the full HTTP stack
(route → service → DB) with minimal mocking.

Endpoints tested:
- POST /api/auth/login           → LoginResponse
- GET  /api/auth/me              → MeResponse
- GET  /api/auth/users           → UserListWithCountsResponse
"""

from schemas.auth import LoginResponse, MeResponse, UserListWithCountsResponse

# ------------------------------------------------------------------ #
# POST /api/auth/login → LoginResponse
# ------------------------------------------------------------------ #


class TestLoginContract:
    """POST /api/auth/login returns shape matching LoginResponse."""

    def test_login_response_shape(self, client, sample_user):
        """L1+L2: Response parses through LoginResponse, required fields present."""
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpassword123"},
        )
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable through Pydantic schema
        parsed = LoginResponse.model_validate(data)

        # L2: Required fields present and typed correctly
        assert isinstance(parsed.token, str)
        assert len(parsed.token) > 0
        assert parsed.user is not None
        assert isinstance(parsed.user.id, int)
        assert isinstance(parsed.user.username, str)
        assert parsed.user.username == "testadmin"
        assert isinstance(parsed.user.email, str)
        assert isinstance(parsed.user.role, str)
        assert isinstance(parsed.user.is_active, bool)
        assert isinstance(parsed.must_change_password, bool)

    def test_login_response_vocabulary(self, client, sample_user):
        """L3: Role field uses valid vocabulary."""
        response = client.post(
            "/api/auth/login",
            json={"username": "testadmin", "password": "testpassword123"},
        )
        data = response.json()
        parsed = LoginResponse.model_validate(data)

        assert parsed.user.role in {"admin", "operator", "viewer"}


# ------------------------------------------------------------------ #
# GET /api/auth/me → MeResponse
# ------------------------------------------------------------------ #


class TestMeContract:
    """GET /api/auth/me returns shape matching MeResponse."""

    def test_me_response_shape(self, client, sample_user, admin_headers):
        """L1+L2: Response parses through MeResponse, required fields present."""
        response = client.get("/api/auth/me", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable
        parsed = MeResponse.model_validate(data)

        # L2: Required fields
        assert parsed.user is not None
        assert isinstance(parsed.user.id, int)
        assert isinstance(parsed.user.username, str)
        assert isinstance(parsed.user.email, str)
        assert isinstance(parsed.user.role, str)
        assert isinstance(parsed.user.is_active, bool)
        assert isinstance(parsed.user.must_change_password, bool)


# ------------------------------------------------------------------ #
# GET /api/auth/users → UserListWithCountsResponse
# ------------------------------------------------------------------ #


class TestUserListContract:
    """GET /api/auth/users returns shape matching UserListWithCountsResponse."""

    def test_user_list_response_shape(self, client, all_test_users, admin_headers):
        """L1+L2: Response parses through schema, required fields present."""
        response = client.get("/api/auth/users", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable
        parsed = UserListWithCountsResponse.model_validate(data)

        # L2: Required fields
        assert isinstance(parsed.users, list)
        assert len(parsed.users) >= 3  # admin + operator + viewer
        assert isinstance(parsed.total, int)
        assert parsed.total == len(parsed.users)

        # Each user has required fields
        for user in parsed.users:
            assert isinstance(user.id, int)
            assert isinstance(user.username, str)
            assert isinstance(user.email, str)
            assert isinstance(user.role, str)
            assert isinstance(user.is_active, bool)
            assert isinstance(user.project_count, int)

    def test_user_list_vocabulary(self, client, all_test_users, admin_headers):
        """L3: Role field uses valid vocabulary for all users."""
        response = client.get("/api/auth/users", headers=admin_headers)
        data = response.json()
        parsed = UserListWithCountsResponse.model_validate(data)

        valid_roles = {"admin", "operator", "viewer"}
        for user in parsed.users:
            assert user.role in valid_roles, f"Unexpected role: {user.role}"
