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


def token_user_state(token: str) -> User | None:
    """#184: resolve the JWT's user for the WebSocket auth gate, or None.

    WebSocket validators (k8s/dpus) authenticate off JWT claims alone and never
    load the User, so must_change_password -- and account existence/active state
    -- are invisible there. This loads the row so the WS paths enforce the same
    gate as get_current_user, from one place.

    Returns the User on success, or None if it cannot be resolved for ANY reason
    (invalid/expired token, deleted or disabled account, a transient DB error).
    The caller refuses on None: fail CLOSED, so a resolution failure never
    re-opens pod exec / BMC SSH the way returning "no change owed" would.

    NOTE: the returned instance is read (``must_change_password``) by the caller
    AFTER this session has closed. That is only safe because get_db_context()
    closes WITHOUT committing, so the loaded column stays readable on the
    detached instance. If get_db_context ever gains a db.commit(),
    expire_on_commit=True would expire that attribute and every WebSocket would
    then fail closed with no obvious cause -- read must_change_password here, or
    disable expire_on_commit, if that changes.
    """
    from database import get_db_context
    try:
        with get_db_context() as db:
            return get_user_from_token(db, token)
    except Exception:
        return None


# The only endpoints a must-change user needs before rotating: submit the new
# password, and read their own state so the UI can show the change screen.
# Exact full paths, not suffixes: this is a security gate, so it must not accept
# an unrelated route that merely ends in "/auth/me".
PASSWORD_CHANGE_EXEMPT_PATHS = frozenset({
    "/api/auth/change-password",
    "/api/auth/me",
})


def enforce_password_change(path: str, user: User) -> None:
    """#184/#186: refuse a must-change user everything but the exempt endpoints.

    Enforced at BOTH auth-resolution points -- the get_current_user dependency
    AND AuthMiddleware -- so a route that declares no dependency of its own (there
    are ~32 such /api routes) still inherits the gate. Without the middleware half
    the seed credential can skip the change-password screen and call those routes
    directly (proven: DELETE /api/benchmarks/configs/{id} -> 204 with the seed
    token). Raises ForbiddenError; callers translate it to 403.
    """
    if not getattr(user, "must_change_password", False):
        return
    if path.rstrip("/") in PASSWORD_CHANGE_EXEMPT_PATHS:
        return
    from core.errors import ForbiddenError
    raise ForbiddenError(
        "Password change required before using the API. "
        "POST /api/auth/change-password with your current and new password."
    )


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


# Passwords this project has shipped as an admin default at some point. An
# existing account still authenticating with one of these is an upgrade left
# holding a publicly-known credential.
_KNOWN_DEFAULT_ADMIN_PASSWORDS = ("changeme",)


def _rotate_known_default_admin(db: Session) -> None:
    """#186 (bonnyr-f5): close the upgrade hole the new gate leaves open.

    A deployment seeded before #184 holds admin/'changeme' with
    must_change_password=False. The new seed logic never runs for it (users
    already exist), so it keeps the published default -- and /api/auth/change-
    password (exempt from the gate) is exactly the endpoint that lets that
    credential persist. On every boot, if the admin still authenticates with a
    known shipped default, force a password change so the seed credential can no
    longer be used to reach the API.
    """
    admin = db.query(User).filter(User.username == "admin").first()
    if admin is None or admin.must_change_password:
        return
    if any(verify_password(p, admin.hashed_password) for p in _KNOWN_DEFAULT_ADMIN_PASSWORDS):
        admin.must_change_password = True  # type: ignore[assignment]
        db.commit()
        logger.warning(
            "Existing 'admin' still held a known shipped default password; forced "
            "must_change_password on it so the API can't be reached with it (#186)."
        )


def seed_admin_user(db: Session) -> User | None:
    """Create default admin user if no users exist. Returns the user or None if already exists."""
    existing_users = db.query(User).count()
    if existing_users > 0:
        _rotate_known_default_admin(db)  # #186: upgrade safety for pre-#184 installs
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
        must_change_password=settings.DEFAULT_ADMIN_MUST_CHANGE,
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
            # Open with 0o600 at creation so the plaintext credential is never
            # momentarily group/world-readable (open()+chmod would create it at
            # 0644 under the usual umask, then narrow it). O_TRUNC handles a
            # stale file from a prior seed without failing.
            fd = os.open(pw_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(seed_password + "\n")
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
