"""
Authentication service for BNK-Forge.
Handles user management, password hashing, and JWT token generation.
"""
import logging
import secrets
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


def token_requires_password_change(token: str) -> bool:
    """#184: True when the JWT's user still owes a password change.

    WebSocket validators (k8s/dpus) authenticate off JWT claims alone and never
    load the User, so must_change_password is invisible there -- a must-change
    admin could otherwise reach pod exec / DPU console / BMC SSH with the seed
    credential while REST refuses /api/auth/users. This loads the row so the WS
    and REST paths enforce the same gate from one place. Returns False on any
    resolution failure (an invalid/expired token is refused by the caller).
    """
    from database import get_db_context
    try:
        with get_db_context() as db:
            user = get_user_from_token(db, token)
            return bool(getattr(user, "must_change_password", False))
    except Exception:
        return False


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

    # #184: never seed a known/published default. If DEFAULT_ADMIN_PASSWORD is
    # unset, generate a strong random one and log it ONCE so the operator can
    # retrieve it from the boot logs. The account is must_change_password, so it
    # only survives until first login regardless.
    seed_password = settings.DEFAULT_ADMIN_PASSWORD
    generated = False
    if not seed_password:
        seed_password = secrets.token_urlsafe(18)
        generated = True

    admin = create_user(
        db=db,
        username="admin",
        email="admin@bnk-forge.local",
        password=seed_password,
        role="admin",
        must_change_password=True,
    )
    # ENG-006: Startup seed manages its own transaction
    db.commit()
    if generated:
        # Persist the one-time password to the (mode-600, volume-backed) keys
        # dir rather than only logging it: a single boot-log line is easy to
        # miss (log rotation, JSON formatting) and logging the plaintext is a
        # known aggregation-exposure risk. Log a POINTER, not the secret. Safe
        # to delete after first login (the account is must_change_password).
        import os
        keys_dir = os.environ.get("KEYS_DIR", "/app/keys")
        pw_path = os.path.join(keys_dir, "initial_admin_password")
        persisted = False
        try:
            os.makedirs(keys_dir, exist_ok=True)
            with open(pw_path, "w") as fh:
                fh.write(seed_password + "\n")
            os.chmod(pw_path, 0o600)
            persisted = True
        except (OSError, PermissionError) as exc:
            logger.warning("Could not write %s: %s", pw_path, exc)
        if persisted:
            logger.warning(
                "Seeded admin user 'admin' with a GENERATED password, written to "
                "%s (retrieve it, then delete it — you must change it on first login). "
                "Set DEFAULT_ADMIN_PASSWORD to choose your own instead.",
                pw_path,
            )
        else:
            # Last resort if the file couldn't be written: log the secret so the
            # operator isn't locked out. Prefer the file path above.
            logger.warning(
                "Seeded admin user 'admin' with GENERATED password (could not persist "
                "to disk): %s -- save it now; change required on first login.",
                seed_password,
            )
    else:
        logger.info("Seeded admin user 'admin' from DEFAULT_ADMIN_PASSWORD — change required on first login")
    return admin


def ensure_service_user(db: Session, username: str, password: str, role: str = "admin") -> None:
    """Idempotent create-or-reconcile a non-human service account.

    Called unconditionally on every startup so the stored password hash always
    matches the current MCP_SERVICE_PASSWORD env var — prevents auth drift when
    the env var is rotated without the DB being updated.
    """
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
