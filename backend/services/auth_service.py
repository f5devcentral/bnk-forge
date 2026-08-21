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

from core.config import MCP_KNOWN_DEFAULT_PASSWORDS, settings
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


def holds_known_default_password(user: User) -> bool:
    """True if the user's stored hash still matches a shipped default password.

    bonnyr-f5 #188 (round 4): disable_stale_service_user only flips is_active — it
    never touches the hash. So a disabled service account still carries
    bcrypt("mcp-service-changeme"), and simply re-activating the row (e.g. via
    PUT /api/auth/users/{id}) would bring the published default credential back to
    life. The re-enable path checks this so a known default can never be revived
    without first rotating to a real secret.
    """
    stored = str(user.hashed_password)
    return any(verify_password(candidate, stored) for candidate in MCP_KNOWN_DEFAULT_PASSWORDS)


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
        svc = create_user(
            db=db,
            username=username,
            email=f"{username}@bnk-forge.local",
            password=password,
            role=role,
            must_change_password=False,
        )
        svc.is_service_account = True  # type: ignore[assignment]  # provenance
        # ENG-006: Startup seed manages its own transaction
        db.commit()
        logger.info(f"Created service account: {username} (role={role})")
    else:
        # bonnyr-f5 #188: refuse to reconcile a pre-existing row this seeder did
        # NOT create as a service account -- a name collision must never take over
        # a human account. Gate on provenance, not the username.
        if not user.is_service_account:
            raise ValueError(
                f"refusing to reconcile '{username}': it is not a service account. "
                f"Point MCP_USERNAME at a dedicated name that isn't an existing user."
            )
        # Narrowed mutation (bonnyr-f5 #188): keep the hash in sync and re-activate
        # the row. We do NOT widen privilege — role is left untouched. Re-activation
        # IS required and IS safe: disable_stale_service_user deactivates this
        # account when no real password is configured (its docstring promises that
        # configuring one "re-seeds and re-activates it"), and the provenance guard
        # above already proved this is our service account, not a human row. Without
        # this, a stale-then-reconciled mcp account stays is_active=False forever and
        # every MCP login fails with "Account is disabled" despite a correct password.
        user.hashed_password = hash_password(password)  # type: ignore[assignment]
        user.must_change_password = False  # type: ignore[assignment]
        user.is_active = True  # type: ignore[assignment]  # revive a disabled-stale row
        # ENG-006: Startup seed manages its own transaction
        db.commit()
        logger.info(f"Reconciled service account: {username}")


def disable_stale_service_user(db: Session) -> None:
    """#188 (bonnyr-f5): a service account seeded by a prior release still holds
    the shipped 'mcp-service-changeme' default and keeps authenticating on upgrade.
    Deactivate EVERY active service-account row so no known default can be used
    until the operator configures a real password (which re-seeds and re-activates
    the account).

    Round 5 (BLOCKER-1): seed_auth_step now calls this UNCONDITIONALLY, before the
    reconcile — not only on the no-password path. The reconcile is name-keyed, so
    on the diligent-operator path (strong MCP_SERVICE_PASSWORD but MCP_USERNAME
    left at the legacy 'admin') it raises a reserved-name ValueError and never
    reaches a disable; running this first is what closes that hole. When a usable
    password IS set for a dedicated username, the reconcile re-activates that one
    row immediately after, so the net effect is: exactly the configured service
    account stays active, every stale default is revoked.

    Keyed on provenance (is_service_account), NOT on the configured username
    (bonnyr-f5 #188 round 4, INV-11): on the dist/IBM upgrade path
    MCP_SERVICE_USERNAME resolves from a legacy .env to 'admin', so matching the
    configured name would early-return and leave the stale 'mcp' row still
    authenticating with the shipped default. The provenance flag is set only on
    rows this seeder created, never on a human account, so disabling all service
    accounts can never touch a human login — which is also why no reserved-username
    guard is needed (or wanted: that guard is exactly what made this a no-op).
    """
    rows = db.query(User).filter(
        User.is_active.is_(True),
        User.is_service_account.is_(True),  # bonnyr-f5 #188: never a human row
    ).all()
    if not rows:
        return
    for user in rows:
        user.is_active = False  # type: ignore[assignment]
    db.commit()
    for user in rows:
        logger.warning(
            "Disabled stale MCP service account '%s' — no usable "
            "MCP_SERVICE_PASSWORD is set, so its pre-existing (possibly default) "
            "credential must not keep authenticating. Set MCP_SERVICE_PASSWORD to "
            "re-enable MCP.",
            user.username,
        )
