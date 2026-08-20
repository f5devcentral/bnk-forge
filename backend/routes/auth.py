"""
Authentication routes for BNK-Forge.
Handles login, user management, and token refresh.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from core.auth_context import effective_role  # re-exported: routes import it from here
from core.errors import ForbiddenError, NotFoundError, UnauthorizedError, handle_route_errors
from database import get_db
from models import KubernetesCluster, Project, ProjectModule, User
from schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MeResponse,
    UserCreateRequest,
    UserCreateResponse,
    UserDeleteResponse,
    UserListWithCountsResponse,
    UserUpdateRequest,
    UserUpdateResponse,
)
from schemas.projects import SuccessResponse
from services.auth_service import (
    authenticate_user,
    change_password,
    create_access_token,
    create_user,
    get_user_from_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _login_exception_response(request: Request, exc: Exception) -> JSONResponse:
    from core.errors import AppError

    if isinstance(exc, AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "path": request.url.path,
                }
            },
        )

    logger.exception("Unhandled login exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "details": {"error_type": type(exc).__name__},
                "path": request.url.path,
            }
        },
    )


# Schemas imported from schemas/auth.py


# --- Role constants ---

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLE_VIEWER = "viewer"
ALL_ROLES = {ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER}


# --- Auth dependencies ---

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: extract and validate the Authorization header.

    Accepts either:
    - JWT (issued by /api/auth/login) — the interactive web flow.
    - Long-lived API token of form ``bnk_<base32>`` — the non-interactive
      CLI / CI/CD flow. Verified via ApiTokenService; updates last_used_at
      on every successful request.
    """
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise UnauthorizedError("Missing or invalid Authorization header")

    token = auth_header.split(" ", 1)[1]

    # API token path — fast-rejected by prefix before the hash compare.
    if token.startswith("bnk_"):
        from services.api_token_service import ApiTokenService, clamp_role
        user, api_token = ApiTokenService(db).verify(token)
        # An API token may grant LESS than its owner's account role (a CI token
        # issued with --role viewer must stay viewer even on an admin account).
        # Re-clamp to the owner's *current* role too, so demoting a user
        # immediately narrows every token they already issued.
        user.request_role = clamp_role(api_token.role, user.role)
        _enforce_password_change(request, user)
        return user

    user = get_user_from_token(db, token)
    _enforce_password_change(request, user)
    return user


# Endpoints a user must still reach while must_change_password is set: change
# the password, read their own state, and log out. Everything else is refused.
_PASSWORD_CHANGE_EXEMPT_SUFFIXES = (
    "/auth/change-password",
    "/auth/me",
    "/auth/logout",
)


def _enforce_password_change(request: Request, user: User) -> None:
    """#184: must_change_password must gate the API, not just the UI.

    The seeded admin (and any admin-created must-change user) gets a fully-valid
    token, so without a server-side gate a client can skip the change-password
    screen and call every endpoint directly with the seed credential. Refuse all
    but the exempt endpoints until the password is rotated.
    """
    if not getattr(user, "must_change_password", False):
        return
    path = request.url.path
    if any(path.endswith(suffix) for suffix in _PASSWORD_CHANGE_EXEMPT_SUFFIXES):
        return
    raise ForbiddenError(
        "Password change required before using the API. "
        "POST /api/auth/change-password with your current and new password."
    )


def require_role(*allowed_roles: str):
    """
    FastAPI dependency factory: require user to have one of the specified roles.

    Usage:
        @router.get("/admin-only", dependencies=[Depends(require_role("admin"))])
        @router.post("/mutate", dependencies=[Depends(require_role("admin", "operator"))])

    Or inject user:
        def my_route(user: User = Depends(require_role("admin", "operator"))):
    """
    def _check_role(user: User = Depends(get_current_user)) -> User:
        role = effective_role(user)
        if role not in allowed_roles:
            raise ForbiddenError(
                f"Requires role: {' or '.join(allowed_roles)}. You have: {role}"
            )
        return user
    return _check_role


# Convenience shortcuts
require_admin = require_role(ROLE_ADMIN)
require_operator = require_role(ROLE_ADMIN, ROLE_OPERATOR)
require_viewer = require_role(ROLE_ADMIN, ROLE_OPERATOR, ROLE_VIEWER)  # any authenticated user


# --- Ownership enforcement (MU-002) ---

def _check_ownership(user: User, project: Project) -> None:
    """Raise ForbiddenError if user is not the project owner (admin bypasses)."""
    if effective_role(user) == ROLE_ADMIN:
        return  # Admins bypass all ownership checks
    if project.user_id is None:
        return  # Legacy projects with no owner — allow until backfilled
    if project.user_id != user.id:
        raise ForbiddenError(
            f"You don't have permission to modify this project. Owned by: {project.user.username if project.user else 'unknown'}"
        )


def require_project_owner(project_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)) -> User:
    """
    FastAPI dependency: require the current user to be the project owner (or admin).

    Usage (inject into route handler):
        def my_route(project_id: int, user: User = Depends(require_project_owner)):

    Note: FastAPI matches `project_id` from the path parameter automatically.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise NotFoundError("project", project_id)
    _check_ownership(user, project)
    return user


def require_module_owner(module_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)) -> User:
    """
    FastAPI dependency: require the current user to own the project that contains this module.

    Usage (inject into route handler):
        def my_route(module_id: int, user: User = Depends(require_module_owner)):
    """
    module = db.query(ProjectModule).filter(ProjectModule.id == module_id).first()
    if not module:
        raise NotFoundError("module", module_id)
    project = db.query(Project).filter(Project.id == module.project_id).first()
    if not project:
        raise NotFoundError("project", module.project_id)
    _check_ownership(user, project)
    return user


def require_cluster_owner(cluster_id: int, user: User = Depends(require_operator), db: Session = Depends(get_db)) -> User:
    """
    FastAPI dependency: require the current user to own the project that contains this cluster.

    Usage (inject into route handler):
        def my_route(cluster_id: int, user: User = Depends(require_cluster_owner)):
    """
    cluster = db.query(KubernetesCluster).filter(KubernetesCluster.id == cluster_id).first()
    if not cluster:
        raise NotFoundError("cluster", cluster_id)
    project = db.query(Project).filter(Project.id == cluster.project_id).first()
    if not project:
        raise NotFoundError("project", cluster.project_id)
    _check_ownership(user, project)
    return user


# --- Username extraction (shared by execution / orchestration / deployment routes) ---

def get_username_from_request(request: Request) -> str:
    """Extract username from JWT token in request. Returns 'user' if not available."""
    try:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            from services.auth_service import decode_token
            payload = decode_token(auth_header.split(" ", 1)[1])
            return payload.get("sub", "user")
    except Exception as e:
        logger.debug(f"Could not extract username from JWT: {e}")
    return "user"


# --- Helper ---

def _user_to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        # Report the role in force, so `/me` under a downscoped API token shows
        # what the caller can actually do. Only the authenticated principal ever
        # carries a request_role, so admin user listings are unaffected.
        "role": effective_role(user),
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


# --- Routes ---

@router.post("/login", response_model=LoginResponse)
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate and return a JWT token."""
    try:
        user = authenticate_user(db, body.username, body.password)
        db.commit()  # ENG-006: Route owns the transaction

        token = create_access_token(data={"sub": user.username, "role": user.role})

        logger.info(f"User logged in: {user.username}")
        return {
            "token": token,
            "user": _user_to_dict(user),
            "must_change_password": user.must_change_password,
        }
    except Exception as exc:
        return _login_exception_response(request, exc)


@router.get("/me", response_model=MeResponse)
@handle_route_errors("get current user")
def get_me(user: User = Depends(get_current_user)):
    """Get the current authenticated user."""
    return {"user": _user_to_dict(user)}


@router.post("/change-password", response_model=SuccessResponse)
@handle_route_errors("change password")
def change_user_password(
    request: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Change the current user's password."""
    change_password(db, user, request.current_password, request.new_password)
    db.commit()  # ENG-006: Route owns the transaction
    return {"success": True, "message": "Password changed successfully"}


@router.post("/users", dependencies=[Depends(require_admin)], response_model=UserCreateResponse)
@handle_route_errors("create user")
def create_new_user(
    request: UserCreateRequest,
    db: Session = Depends(get_db),
):
    """Create a new user (admin only)."""
    user = create_user(
        db=db,
        username=request.username,
        email=request.email,
        password=request.password,
        role=request.role,
        must_change_password=request.must_change_password,
    )
    db.commit()  # ENG-006: Route owns the transaction
    return {"success": True, "user": _user_to_dict(user)}


@router.get("/users", dependencies=[Depends(require_admin)], response_model=UserListWithCountsResponse)
@handle_route_errors("list users")
def list_users(db: Session = Depends(get_db)):
    """List all users with project ownership counts (admin only)."""
    # MU-006: Join users with project counts in a single query
    results = (
        db.query(User, sa_func.count(Project.id).label("project_count"))
        .outerjoin(Project, Project.user_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at)
        .all()
    )
    users = []
    for user_row, project_count in results:
        user_dict = _user_to_dict(user_row)
        user_dict["project_count"] = project_count
        users.append(user_dict)
    return {"users": users, "total": len(users)}


@router.put("/users/{user_id}", dependencies=[Depends(require_admin)], response_model=UserUpdateResponse)
@handle_route_errors("update user")
def update_user(
    user_id: int,
    request: UserUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a user's role, email, or active status (admin only).

    Cannot demote yourself from admin. Cannot deactivate yourself.
    """
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise NotFoundError("user", user_id)

    # Safety: prevent self-demotion from admin
    if user.id == user_id and request.role and request.role != "admin":
        raise ForbiddenError("Cannot change your own role from admin")

    # Safety: prevent self-deactivation
    if user.id == user_id and request.is_active is False:
        raise ForbiddenError("Cannot deactivate your own account")

    # Validate role if provided
    if request.role is not None:
        if request.role not in ALL_ROLES:
            from core.errors import BadRequestError
            raise BadRequestError(f"Invalid role: {request.role}. Must be one of: {', '.join(ALL_ROLES)}")
        target.role = request.role

    if request.email is not None:
        # Check for duplicate email
        existing = db.query(User).filter(User.email == request.email, User.id != user_id).first()
        if existing:
            from core.errors import ConflictError
            raise ConflictError("user", f"Email '{request.email}' is already in use")
        target.email = request.email

    if request.is_active is not None:
        target.is_active = request.is_active

    db.commit()
    db.refresh(target)

    logger.info(f"User updated: {target.username} (by {user.username})")
    return {"success": True, "user": _user_to_dict(target)}


@router.delete("/users/{user_id}", dependencies=[Depends(require_admin)], response_model=UserDeleteResponse)
@handle_route_errors("delete user")
def delete_user(
    user_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a user (admin only). Cannot delete yourself."""
    if user.id == user_id:
        raise ForbiddenError("Cannot delete your own account")

    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise NotFoundError("user", user_id)

    db.delete(target)
    db.commit()

    logger.info(f"User deleted: {target.username} (by {user.username})")
    return {"success": True, "message": f"User '{target.username}' deleted"}


# ---------------------------------------------------------------------------
# API tokens for non-interactive (CLI / CI/CD) access
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _PydanticBaseModel
from pydantic import Field as _PydanticField


class ApiTokenCreateRequest(_PydanticBaseModel):
    name: str = _PydanticField(..., min_length=1, max_length=255)
    role: str | None = _PydanticField(default=None, description="viewer | operator | admin (defaults to caller's role)")
    expires_in_days: int | None = _PydanticField(default=None, ge=1, le=3650)


@router.post("/tokens")
@handle_route_errors("create api token")
def create_api_token(
    body: ApiTokenCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Issue a long-lived API token. Plaintext is returned exactly once."""
    from services.api_token_service import ApiTokenService
    issued = ApiTokenService(db).create(
        user=user, name=body.name, role=body.role, expires_in_days=body.expires_in_days,
        # Ceiling is the role in force, not the account role — a downscoped
        # token must not be able to mint a more privileged one.
        max_role=effective_role(user),
    )
    db.commit()
    return {
        "id": issued.id,
        "name": issued.name,
        "role": issued.role,
        "token": issued.plaintext,  # only present at creation
        "expires_at": issued.expires_at,
        "created_at": issued.created_at,
    }


@router.get("/tokens")
@handle_route_errors("list api tokens")
def list_api_tokens(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the current user's API tokens (plaintext is never returned)."""
    from services.api_token_service import ApiTokenService
    return {"tokens": ApiTokenService(db).list_for_user(user)}


@router.delete("/tokens/{token_id}")
@handle_route_errors("revoke api token")
def revoke_api_token(
    token_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Revoke an API token by ID."""
    from services.api_token_service import ApiTokenService
    ApiTokenService(db).revoke(user=user, token_id=token_id)
    db.commit()
    return {"success": True, "message": f"API token {token_id} revoked"}
