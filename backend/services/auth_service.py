"""
Authentication service for BNK-Forge.
Handles user management, password hashing, and JWT token generation.
"""
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from core.config import settings
from core.errors import BadRequestError, ConflictError, UnauthorizedError
from models import User

logger = logging.getLogger(__name__)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 24


def hash_password(password: str) -> str:
    """Hash a plaintext password."""
    return cast(str, pwd_context.hash(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a hash."""
    return cast(bool, pwd_context.verify(plain_password, hashed_password))


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(UTC) + (expires_delta or timedelta(hours=JWT_EXPIRATION_HOURS))
    to_encode.update({"exp": expire})
    return cast(str, jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM))


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises UnauthorizedError on failure."""
    try:
        payload: dict[str, Any] = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        username: str | None = payload.get("sub")
        if username is None:
            raise UnauthorizedError("Invalid token: missing subject")
        return payload
    except JWTError as e:
        raise UnauthorizedError(f"Invalid token: {e}")


def authenticate_user(db: Session, username: str, password: str) -> User:
    """Authenticate a user by username/password. Returns User or raises UnauthorizedError."""
    user = db.query(User).filter(User.username == username).first()
    if not user:
        # Also try email
        user = db.query(User).filter(User.email == username).first()

    if not user or not verify_password(password, str(user.hashed_password)):
        raise UnauthorizedError("Invalid username or password")

    if not user.is_active:
        raise UnauthorizedError("Account is disabled")

    # Update last login
    user.last_login_at = datetime.now(UTC)  # type: ignore[assignment]
    # ENG-006: No commit — caller (route) owns the transaction

    return user


def get_user_from_token(db: Session, token: str) -> User:
    """Get the user associated with a JWT token. Raises UnauthorizedError on failure."""
    payload = decode_token(token)
    username = payload.get("sub")

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise UnauthorizedError("User not found")
    if not user.is_active:
        raise UnauthorizedError("Account is disabled")

    return user


def create_user(db: Session, username: str, email: str, password: str,
                role: str = "operator", must_change_password: bool = False) -> User:
    """Create a new user. Raises ConflictError if username/email already exists."""
    # Check for existing username
    if db.query(User).filter(User.username == username).first():
        raise ConflictError("user", f"Username '{username}' already exists")

    # Check for existing email
    if db.query(User).filter(User.email == email).first():
        raise ConflictError("user", f"Email '{email}' already exists")

    # Validate role
    valid_roles = {"admin", "operator", "viewer"}
    if role not in valid_roles:
        raise BadRequestError(f"Invalid role '{role}'. Must be one of: {', '.join(valid_roles)}")

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        role=role,
        must_change_password=must_change_password,
    )
    db.add(user)
    db.flush()  # ENG-006: Flush to generate ID; caller (route) owns the transaction
    db.refresh(user)

    logger.info(f"Created user: {username} (role={role})")
    return user


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    """Change a user's password. Validates current password first."""
    if not verify_password(current_password, str(user.hashed_password)):
        raise BadRequestError("Current password is incorrect")

    if len(new_password) < 8:
        raise BadRequestError("New password must be at least 8 characters")

    user.hashed_password = hash_password(new_password)  # type: ignore[assignment]
    user.must_change_password = False  # type: ignore[assignment]
    # ENG-006: No commit — caller (route) owns the transaction

    logger.info(f"Password changed for user: {user.username}")


def seed_admin_user(db: Session) -> User | None:
    """Create default admin user if no users exist. Returns the user or None if already exists."""
    existing_users = db.query(User).count()
    if existing_users > 0:
        return None

    admin = create_user(
        db=db,
        username="admin",
        email="admin@bnk-forge.local",
        password=settings.DEFAULT_ADMIN_PASSWORD,
        role="admin",
        must_change_password=True,
    )
    # ENG-006: Startup seed manages its own transaction
    db.commit()
    logger.info("Seeded default admin user — password change required on first login")
    return admin


# Usernames that belong to human accounts and must never be reconciled as a
# service account. ensure_service_user rewrites the hash and clears
# must_change_password, so pointing it at "admin" (which happens when
# MCP_USERNAME still defaults to "admin" on an old .env) silently takes over the
# human admin row and disables #186's gate on it (bonnyr-f5).
_RESERVED_HUMAN_USERNAMES = frozenset({"admin"})


def ensure_service_user(db: Session, username: str, password: str, role: str = "admin") -> None:
    """Idempotent create-or-reconcile a non-human service account.

    Called unconditionally on every startup so the stored password hash always
    matches the current MCP_SERVICE_PASSWORD env var — prevents auth drift when
    the env var is rotated without the DB being updated.

    Refuses a reserved human username: reconciling would rewrite that account's
    hash and clear must_change_password, converting a var whose shipped default
    was "admin" into a takeover of the real admin account.
    """
    if username in _RESERVED_HUMAN_USERNAMES:
        raise ValueError(
            f"refusing to reconcile reserved human username '{username}' as a "
            f"service account — set MCP_USERNAME to a dedicated name like 'mcp'"
        )
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        create_user(
            db=db,
            username=username,
            email=f"{username}@bnk-forge.local",
            password=password,
            role=role,
            must_change_password=False,
        )
        # ENG-006: Startup seed manages its own transaction
        db.commit()
        logger.info(f"Created service account: {username} (role={role})")
    else:
        # Reconcile: update hash to match current env var; never requires current password
        user.hashed_password = hash_password(password)  # type: ignore[assignment]
        user.must_change_password = False  # type: ignore[assignment]
        user.role = role  # type: ignore[assignment]
        user.is_active = True  # type: ignore[assignment]
        # ENG-006: Startup seed manages its own transaction
        db.commit()
        logger.info(f"Reconciled service account: {username}")


def disable_stale_service_user(db: Session, username: str) -> None:
    """#188 (bonnyr-f5): on upgrade with MCP_SERVICE_PASSWORD unset, an mcp
    account seeded by a prior release still holds the shipped
    'mcp-service-changeme' default and keeps authenticating (the reconcile branch
    only runs when a password IS set). Deactivate the stale account so the known
    default can't be used until the operator configures a real one (which
    re-seeds and re-activates it). Never touches a reserved human username.
    """
    if username in _RESERVED_HUMAN_USERNAMES:
        return
    user = db.query(User).filter(User.username == username, User.is_active.is_(True)).first()
    if user is not None:
        user.is_active = False  # type: ignore[assignment]
        db.commit()
        logger.warning(
            "Disabled stale MCP service account '%s' — MCP_SERVICE_PASSWORD is "
            "unset, so its pre-existing (possibly default) credential must not "
            "keep authenticating. Set MCP_SERVICE_PASSWORD to re-enable MCP.",
            username,
        )
