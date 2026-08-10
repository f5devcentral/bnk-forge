"""
Pydantic schemas for the Auth domain.

These were previously defined inline in routes/auth.py.
Moved here for consistency with the per-domain schema pattern.
"""


from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Request body for POST /api/auth/login."""
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


class UserInfo(BaseModel):
    """User info object returned in login and user list responses."""
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: str | None = None
    created_at: str | None = None


class LoginResponse(BaseModel):
    """Response for POST /api/auth/login."""
    token: str
    user: UserInfo
    must_change_password: bool


class ChangePasswordRequest(BaseModel):
    """Request body for POST /api/auth/change-password."""
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, description="New password (min 8 chars)")


class UserCreateRequest(BaseModel):
    """Request body for POST /api/auth/users."""
    username: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=1)
    password: str = Field(..., min_length=8)
    role: str = Field(default="operator", description="User role: admin, operator, viewer")
    must_change_password: bool = Field(
        default=False,
        description="Require the user to change their password on first login.",
    )


class UserResponse(BaseModel):
    """Response schema for a single user."""
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: str | None = None
    created_at: str | None = None


class UserListResponse(BaseModel):
    """Response for GET /api/auth/users."""
    users: list[UserResponse]


class MeResponse(BaseModel):
    """Response for GET /api/auth/me."""
    user: UserInfo


class UserCreateResponse(BaseModel):
    """Response for POST /api/auth/users."""
    success: bool
    user: UserResponse


class UserUpdateRequest(BaseModel):
    """Request body for PUT /api/auth/users/{user_id}."""
    email: str | None = Field(None, min_length=1, description="New email address")
    role: str | None = Field(None, description="New role: admin, operator, viewer")
    is_active: bool | None = Field(None, description="Enable/disable user account")


class UserUpdateResponse(BaseModel):
    """Response for PUT /api/auth/users/{user_id}."""
    success: bool
    user: UserResponse


class UserWithProjectCount(BaseModel):
    """User info with project ownership count (MU-006)."""
    id: int
    username: str
    email: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: str | None = None
    created_at: str | None = None
    project_count: int = 0


class UserListWithCountsResponse(BaseModel):
    """Response for GET /api/auth/users (enhanced with project counts)."""
    users: list[UserWithProjectCount]
    total: int


class UserDeleteResponse(BaseModel):
    """Response for DELETE /api/auth/users/{user_id}."""
    success: bool
    message: str
