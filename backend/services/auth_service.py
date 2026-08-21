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

# Passwords this project has shipped as a default for the mcp SERVICE account
# (role=admin, must_change bypassed) at some point -- the exact same #184 hazard
# class as the admin defaults above, just a second account. Published in
# config.py, the compose files, and .env.example, so any account still
# authenticating with one of these holds a publicly-known admin credential.
# ``changeme`` is here too because the old shipped compose pointed the MCP client
# at admin/changeme. Refused as a seed value and rotated out of any existing row.
_KNOWN_DEFAULT_SERVICE_PASSWORDS = ("mcp-service-changeme", "changeme")

# #186 BLOCKER 3 (bonnyr-f5 r5): usernames that belong to a HUMAN identity and
# must never be resolved by ensure_service_user. That function locates its target
# purely by ``User.username`` and then force-sets role=admin / is_active=True /
# must_change_password=False. If MCP_SERVICE_USERNAME (or the Helm chart's
# mcpUsername) is pointed at "admin", it would REWRITE the human admin row --
# clearing the #184 must-change gate and handing the mcp secret full admin access
# (probe: "mcp secret now authenticates as admin? YES role=admin must_change=False").
# Refuse the co-option: a service account may not adopt a reserved human identity.
# (Name kept identical to #188's guard so the two land cleanly on the integration
# branch.)
_RESERVED_HUMAN_USERNAMES = frozenset({"admin"})


class GeneratedCredentialPersistError(RuntimeError):
    """A generated credential could not be written to the keys dir.

    #186 (bonnyr-f5): the docs promise "the plaintext is never logged". The old
    code broke that promise — on an unwritable ``/app/keys`` it fell back to
    logging the generated plaintext, leaking a live secret into the logs (a real
    aggregation-exposure risk). We now fail closed instead: raise this
    (WITHOUT the plaintext in the message) so startup refuses to proceed and the
    operator remediates. Because the credential is persisted BEFORE the DB row is
    created/rotated, a failure leaves nothing committed and the next boot retries
    cleanly once the keys volume is writable (or an explicit password env var is
    set, which skips generation entirely).
    """


def _persist_generated_password(password: str, filename: str = "initial_admin_password") -> str:
    """Write a generated credential to a mode-0600 file in the keys dir.

    Returns the path on success. Raises :class:`GeneratedCredentialPersistError`
    if the keys dir is unwritable — the plaintext is NEVER logged or included in
    the exception, so an unwritable ``/app/keys`` can never leak the secret. The
    caller logs a POINTER to the returned path; there is deliberately no
    "log the secret instead" fallback.

    The file is created with 0o600 at open() time so the plaintext credential is
    never momentarily group/world-readable (open()+chmod would create it 0644
    under the usual umask, then narrow it). O_TRUNC handles a stale file from a
    prior seed/rotation without failing.

    Shared by the fresh-install admin seed (#184), the upgrade remediation
    (#186), and the mcp service-account seed/rotation (#186 BLOCKER 1) so a
    generated credential is surfaced identically on every path — ``filename``
    keeps the admin and mcp secrets in distinct files so neither clobbers the
    other.
    """
    import os
    keys_dir = os.environ.get("KEYS_DIR", "/app/keys")
    pw_path = os.path.join(keys_dir, filename)
    try:
        os.makedirs(keys_dir, exist_ok=True)
        fd = os.open(pw_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(password + "\n")
    except OSError as exc:  # PermissionError/NotADirectoryError are OSError subclasses
        # Fail closed. NEVER put `password` in this message: it propagates into
        # logs, which is exactly the leak we are closing (#186).
        raise GeneratedCredentialPersistError(
            f"could not persist generated credential to {pw_path}: {exc}"
        ) from exc
    return pw_path


def _rotate_known_default_admin(db: Session) -> None:
    """#186 (bonnyr-f5): make a published default credential UNUSABLE on upgrade.

    A deployment seeded before #184 holds admin/'changeme' with
    must_change_password=False. The new seed logic never runs for it (users
    already exist), so it keeps the published default.

    Merely flagging must_change_password does NOT remove the capability:
    /api/auth/change-password is exempt from the gate and verifies
    current_password against the stored hash, so anyone holding the published
    'changeme' (it's in dist/README.md, user-pack/install-guide.html and
    scripts/ibm_cloud_bnk_forge.sh) could rotate the password before the operator
    does and take over the account. A mitigation must remove the capability, not
    request its removal.

    So we OVERWRITE the hash -- the published default stops working the moment
    this runs -- and leave the account must_change_password so the replacement
    only survives until first login.

    Provenance (bonnyr-f5 r4): the replacement follows the SAME source-of-truth
    rule as a fresh seed, so the documented retrieval instructions stay correct
    on upgrade too:
      * DEFAULT_ADMIN_PASSWORD set to a non-published value (Helm wires it from
        the ``admin-password`` Secret) -> rotate TO that value, so the Secret /
        env the docs tell operators to read is what now authenticates. No
        keys-file is written (nothing was generated).
      * otherwise -> generate a fresh random secret and surface it exactly like a
        fresh install (mode-0600 keys-file, pointer logged once).
    Rotating to a *published* default (e.g. DEFAULT_ADMIN_PASSWORD=changeme) is
    refused -- that would just re-publish the hole -- so such a value falls
    through to generation.
    """
    # #186 (bonnyr-f5 r4, INV-8): lock the row for the read-then-write. Two `api`
    # replicas booting together would otherwise both read admin/'changeme', each
    # generate a DIFFERENT secret, and interleave file-write vs DB-commit so the
    # keys-file and the stored hash end up from different runs -> permanent admin
    # lockout. FOR UPDATE serializes them: the loser blocks, then re-reads the
    # already-rotated hash (no longer a known default) and no-ops. (Silently
    # ignored on SQLite, which the tests use and which has no concurrent writers.)
    admin = db.query(User).filter(User.username == "admin").with_for_update().first()
    if admin is None:
        return
    if not any(verify_password(p, admin.hashed_password) for p in _KNOWN_DEFAULT_ADMIN_PASSWORDS):
        return

    configured = settings.DEFAULT_ADMIN_PASSWORD
    if configured and configured not in _KNOWN_DEFAULT_ADMIN_PASSWORDS:
        # Rotate to the operator/chart-supplied secret so the documented source
        # (Helm admin-password Secret / DEFAULT_ADMIN_PASSWORD env) is authoritative.
        admin.hashed_password = hash_password(configured)  # type: ignore[assignment]
        admin.must_change_password = True  # type: ignore[assignment]
        db.commit()
        logger.warning(
            "Existing 'admin' still held a known shipped default password; "
            "OVERWROTE it with DEFAULT_ADMIN_PASSWORD (the published default no "
            "longer works) -- retrieve it from the same source you configured "
            "(Helm: the admin-password Secret) and change it on first login (#186).",
        )
        return

    new_password = secrets.token_urlsafe(18)
    # Persist the new secret BEFORE overwriting the hash: if the keys dir is
    # unwritable this raises (fail closed, no plaintext logged) with the row's
    # published-default hash untouched, so the next boot retries the whole
    # remediation cleanly. Never fall back to logging the plaintext (#186).
    pw_path = _persist_generated_password(new_password)
    admin.hashed_password = hash_password(new_password)  # type: ignore[assignment]
    admin.must_change_password = True  # type: ignore[assignment]
    db.commit()
    logger.warning(
        "Existing 'admin' still held a known shipped default password; "
        "OVERWROTE it with a generated secret (the published default no longer "
        "works) and wrote the new one to %s -- retrieve it, then change it on "
        "first login (#186).",
        pw_path,
    )


def seed_admin_user(db: Session) -> User | None:
    """Create default admin user if no users exist. Returns the user or None if already exists."""
    existing_users = db.query(User).count()
    if existing_users > 0:
        _rotate_known_default_admin(db)  # #186: upgrade safety for pre-#184 installs
        return None

    # #184: never seed a known/published default. If DEFAULT_ADMIN_PASSWORD is
    # unset, generate a strong random one and persist it to the (mode-600,
    # volume-backed) keys dir so the operator can retrieve it. The account is
    # must_change_password, so it only survives until first login regardless.
    seed_password = settings.DEFAULT_ADMIN_PASSWORD
    generated = False
    if not seed_password:
        seed_password = secrets.token_urlsafe(18)
        generated = True

    pw_path = None
    if generated:
        # Persist the one-time password BEFORE creating the row: a single boot-log
        # line is easy to miss (log rotation, JSON formatting) and logging the
        # plaintext is a known aggregation-exposure risk, so we surface a POINTER,
        # never the secret. Persisting first means an unwritable keys dir raises
        # here (fail closed, no plaintext logged) with NO admin row committed, so
        # the next boot retries the seed cleanly instead of stranding an admin
        # account whose generated password nobody can read (#186).
        pw_path = _persist_generated_password(seed_password)

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
        logger.warning(
            "Seeded admin user 'admin' with a GENERATED password, written to "
            "%s (retrieve it, then delete it — you must change it on first login). "
            "Set DEFAULT_ADMIN_PASSWORD to choose your own instead.",
            pw_path,
        )
    else:
        logger.info("Seeded admin user 'admin' from DEFAULT_ADMIN_PASSWORD — change required on first login")
    return admin


def _log_generated_service_password(username: str, pw_path: str, action: str) -> None:
    """Log a POINTER to a persisted generated service-account secret (#186).

    The plaintext is never logged — the caller persisted it via
    :func:`_persist_generated_password` (which fails closed) and passes only the
    resulting file path here.
    """
    logger.warning(
        "%s service account '%s' with a GENERATED password, written to %s "
        "(retrieve it and point the MCP client at it via MCP_SERVICE_PASSWORD). "
        "Set MCP_SERVICE_PASSWORD to choose your own instead (#186).",
        action, username, pw_path,
    )


def ensure_service_user(
    db: Session, username: str, password: str | None, role: str = "admin"
) -> None:
    """Idempotent create-or-reconcile a non-human service account.

    Called unconditionally on every startup. A genuine operator-supplied password
    is reconciled onto the row so the stored hash always matches the current
    MCP_SERVICE_PASSWORD env var — prevents auth drift when the env var is rotated
    without the DB being updated.

    #186 BLOCKER 1 (bonnyr-f5): the mcp service account is role=admin and
    must_change_password=False, so it is EXEMPT from the #184 must-change gate.
    Seeding or reconciling it to a shipped published default (mcp-service-changeme
    / changeme) republishes a live, publicly-known admin credential — the exact
    class of hole #184 closed for the human admin. So a published default (or an
    absent password) is treated as "no usable secret":

      * fresh row  -> a strong random secret is generated and surfaced like the
        admin seed (mode-0600 file + one-time pointer log). The published default
        NEVER becomes the stored credential, so it can never authenticate.
      * existing row still holding a known published default -> OVERWRITTEN with a
        fresh random secret (upgrade remediation, same as _rotate_known_default_admin).
      * existing row already holding a generated/operator secret -> left intact,
        so the reconcile is idempotent and does not churn the secret every boot.

    #186 BLOCKER 1 (bonnyr-f5 r5): the backend now receives MCP_SERVICE_PASSWORD on
    every deploy mode (compose backend-env anchors, the ibm installer, and the Helm
    shared-env sourced from the release Secret's mcp-password key), so this reconcile
    binds the mcp account to the SAME per-install secret the mcp client uses. A
    reserved-name guard (above) refuses to run against a human username such as
    ``admin``. #188's Helm mcp-secret work shares this credential surface.
    """
    # #186 BLOCKER 3 (bonnyr-f5 r5): fail closed BEFORE any lookup if the caller
    # points a service account at a reserved human username. Without this, a
    # deployment that sets MCP_SERVICE_USERNAME=admin (or ships the chart's old
    # mcpUsername: admin) silently rewrites the human admin row and grants the mcp
    # secret admin access. A service account may never co-opt a human identity.
    if username in _RESERVED_HUMAN_USERNAMES:
        raise ValueError(
            f"Refusing to provision service account under reserved human username "
            f"{username!r}: a service account must not co-opt the human admin "
            f"identity. Set MCP_SERVICE_USERNAME to a dedicated service name (#186)."
        )

    published_default = bool(password) and password in _KNOWN_DEFAULT_SERVICE_PASSWORDS
    # An operator secret we may actually store, or None if there is nothing usable.
    usable_password = None if (not password or published_default) else password

    # #186 (bonnyr-f5 r4/r5, INV-8): lock the row for the read-then-write on the
    # RECONCILE/ROTATE (existing-row) paths, so two `api` replicas cannot desync
    # the stored hash from the keys-file (same lockout hazard as the admin rotation
    # above). No-op on SQLite (tests). Caveat (bonnyr-f5 r5): with_for_update()
    # cannot lock a row that does not exist yet, so the FIRST-CREATE path below is
    # NOT serialised by this lock — it is serialised by the username UNIQUE
    # constraint instead: a losing racer's INSERT raises IntegrityError and that
    # boot's seed step fails closed and retries on the next start (both racers
    # persist the same generated secret to the keys-file first, so a retry
    # converges). Startup runs single-replica in every shipped topology, so the
    # create race is theoretical; the reconcile lock is what matters at scale.
    user = db.query(User).filter(User.username == username).with_for_update().first()

    if user is None:
        generated = usable_password is None
        seed_password = usable_password if usable_password is not None else secrets.token_urlsafe(18)
        # Persist the generated secret BEFORE creating the row (fail closed on an
        # unwritable keys dir, never logging the plaintext) so a failure leaves
        # nothing committed and the next boot retries cleanly (#186).
        pw_path = _persist_generated_password(seed_password, filename=f"initial_{username}_password") if generated else None
        create_user(
            db=db,
            username=username,
            email=f"{username}@bnk-forge.local",
            password=seed_password,
            role=role,
            must_change_password=False,
        )
        # ENG-006: Startup seed manages its own transaction
        db.commit()
        if generated:
            _log_generated_service_password(username, pw_path, "Seeded")
        else:
            logger.info(f"Created service account: {username} (role={role})")
        return

    if usable_password is not None:
        # Operator supplied a genuine (non-default) password: reconcile to it.
        user.hashed_password = hash_password(usable_password)  # type: ignore[assignment]
        user.must_change_password = False  # type: ignore[assignment]
        user.role = role  # type: ignore[assignment]
        user.is_active = True  # type: ignore[assignment]
        db.commit()  # ENG-006: Startup seed manages its own transaction
        logger.info(f"Reconciled service account: {username}")
        return

    # No usable secret configured. Only touch the row if it still holds a known
    # published default (an upgrade left holding the shipped credential); rotate
    # it to a fresh random secret so the published value stops working. A row that
    # already holds a generated/operator secret is left untouched (idempotent).
    if any(verify_password(p, str(user.hashed_password)) for p in _KNOWN_DEFAULT_SERVICE_PASSWORDS):
        new_password = secrets.token_urlsafe(18)
        # Persist BEFORE overwriting the hash: fail closed on an unwritable keys
        # dir (no plaintext logged), leaving the published-default hash untouched
        # so the next boot retries the rotation cleanly (#186).
        pw_path = _persist_generated_password(new_password, filename=f"initial_{username}_password")
        user.hashed_password = hash_password(new_password)  # type: ignore[assignment]
        user.must_change_password = False  # type: ignore[assignment]
        user.role = role  # type: ignore[assignment]
        user.is_active = True  # type: ignore[assignment]
        db.commit()  # ENG-006: Startup seed manages its own transaction
        _log_generated_service_password(username, pw_path, "Rotated")
