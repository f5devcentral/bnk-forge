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
from sqlalchemy.exc import IntegrityError
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


def _is_reserved_human_username(username: str) -> bool:
    """True if ``username`` collides with a reserved human identity.

    bonnyr-f5 #193 (minor): the Helm guard normalises with ``lower | trim`` before
    comparing, so ``mcpUsername: Admin`` or ``" admin "`` is refused at render.
    An exact-match Python check let those SAME values through on compose —
    ``MCP_SERVICE_USERNAME=Admin`` minted a second role=admin, must_change=False
    account. Match Helm: compare case-insensitively after trimming surrounding
    whitespace so both surfaces refuse the identical set of inputs.
    """
    return username.strip().lower() in _RESERVED_HUMAN_USERNAMES


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
    prior seed/rotation without failing. The 0o600 open() mode applies ONLY when
    the file is newly created, so a pre-existing file from an older release (e.g.
    left 0o644) would be truncated in place but keep its old, looser mode — we
    fchmod(0o600) after open to force it tight regardless (CR-5).

    Used by the fresh-install admin seed (#184) and the upgrade remediation
    (#186). bonnyr-f5 #193: the ``filename`` parameter is now vestigial — after the
    #188-over-#186 consolidation (MCP no longer generates a secret) every call uses
    the default, so it only ever writes ``initial_admin_password``. It is kept as a
    parameter to preserve the seam should a second generated credential return.
    """
    import os
    keys_dir = os.environ.get("KEYS_DIR", "/app/keys")
    pw_path = os.path.join(keys_dir, filename)
    try:
        os.makedirs(keys_dir, exist_ok=True)
        fd = os.open(pw_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        # The mode arg above only takes effect on CREATE; force 0o600 so a
        # pre-existing looser file (e.g. 0o644 from an older release) is tightened
        # before we write the plaintext into it (CR-5).
        os.fchmod(fd, 0o600)
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
    # bonnyr-f5 #193 (minor): the comment above promises we "never seed a
    # known/published default", but a supplied DEFAULT_ADMIN_PASSWORD was taken
    # verbatim — so an operator who set it to a shipped default (e.g. "changeme")
    # got exactly the live, publicly-known admin credential #184 exists to remove.
    # Mirror the MCP path: refuse a known default and fall through to generation.
    # (_rotate_known_default_admin already refuses one on the upgrade path.)
    if seed_password and seed_password in _KNOWN_DEFAULT_ADMIN_PASSWORDS:
        logger.warning(
            "DEFAULT_ADMIN_PASSWORD is a known published default and will not be "
            "seeded (it would be a live, publicly-known admin credential); "
            "generating a strong random admin password instead (#193)."
        )
        seed_password = None
    if not seed_password:
        seed_password = secrets.token_urlsafe(18)
        generated = True

    # CR-1: fresh-seed race safety. Two `api` replicas booting a fresh install
    # both see 0 users and, with DEFAULT_ADMIN_PASSWORD unset, each GENERATE a
    # DIFFERENT password. Without a guard, a losing replica would overwrite the
    # keys file with its password while its create_user('admin') INSERT loses the
    # username UNIQUE constraint and rolls back — file and committed row would hold
    # different passwords and the operator would be permanently locked out.
    #
    # Fix: create + flush FIRST (create_user flushes, so the loser's INSERT raises
    # IntegrityError right here), then persist the keys file only after WE won the
    # race but BEFORE commit. So (a) a losing replica never touches the file, and
    # (b) an unwritable keys dir still fails closed with NO admin row committed
    # (the enclosing get_db_context rolls back), so the next boot retries the seed
    # cleanly instead of stranding an admin whose generated password nobody can
    # read (#186). The keys file can therefore only ever hold the password of the
    # row that actually committed.
    try:
        admin = create_user(
            db=db,
            username="admin",
            email="admin@bnk-forge.local",
            password=seed_password,
            role="admin",
            must_change_password=settings.DEFAULT_ADMIN_MUST_CHANGE,
        )
    except (IntegrityError, ConflictError):
        # Another replica seeded 'admin' first (concurrent fresh boot). It owns the
        # committed row and the matching keys file; we must NOT write our different
        # generated password. Roll our aborted transaction back and defer.
        db.rollback()
        logger.info(
            "Another replica seeded the admin user first — skipping (concurrent boot)"
        )
        return None

    pw_path = None
    if generated:
        # We won the create race (row flushed, not yet committed). Persist the
        # one-time password now, BEFORE commit: a single boot-log line is easy to
        # miss (log rotation, JSON formatting) and logging the plaintext is a known
        # aggregation-exposure risk, so we surface a POINTER, never the secret. An
        # unwritable keys dir raises here (fail closed, no plaintext logged); we
        # roll the flushed-but-uncommitted admin row back so NOTHING is committed
        # and the next boot retries the seed cleanly instead of stranding an admin
        # whose generated password nobody can read (#186).
        try:
            pw_path = _persist_generated_password(seed_password)
        except GeneratedCredentialPersistError:
            db.rollback()
            raise

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


# NOTE: _RESERVED_HUMAN_USERNAMES is defined once, above (near the config
# constants), and shared by ensure_service_user below — the #188 and #186 guards
# use the identical frozenset, so the integration keeps a single definition.
def ensure_service_user(
    db: Session, username: str, password: str | None, role: str = "admin"
) -> None:
    """Idempotent create-or-reconcile a non-human service account.

    Called by ``startup_steps.seed_auth_step`` ONLY when a usable
    MCP_SERVICE_PASSWORD is configured (its ``_mcp_pw_usable`` gate: non-empty and
    not a known published default). The unset / published-default case is owned
    ENTIRELY by :func:`disable_stale_service_user`, which deactivates the account
    until an operator configures a real password — this function is never reached
    then. It therefore requires a usable (non-empty, non-published-default)
    ``password`` and only ever creates/reconciles with it; there is no
    generate-on-unset or rotate-on-unset path here (that dead code was removed —
    it was unreachable from startup, see #193 review).

    A genuine operator-supplied password is reconciled onto the row so the stored
    hash always matches the current MCP_SERVICE_PASSWORD env var — prevents auth
    drift when the env var is rotated without the DB being updated.

    Combined credential model (#186 + bonnyr-f5 #188):
      * provenance (#188): a freshly-created row is flagged is_service_account so
        disable_stale_service_user can find it and a later reconcile can prove it
        is ours. A pre-existing row that is NOT a service account is refused —
        UNLESS it still authenticates with a shipped published default, which is
        by definition a stale service credential from a pre-provenance install
        (the v2_155 backfill flags the known legacy 'mcp' row, but a row seeded
        under another path may still lack the flag); neutralising it is exactly
        the upgrade remediation, so we adopt and reconcile it to the configured
        password.
      * reserved-name guard (#186/#188): a service account may never co-opt a
        human identity such as ``admin``; that is refused before any lookup.

    #186 BLOCKER 1 (bonnyr-f5 r5): the backend now receives MCP_SERVICE_PASSWORD on
    every deploy mode (compose backend-env anchors, the ibm installer, and the Helm
    shared-env sourced from the release Secret's mcp-password key), so this
    reconcile binds the mcp account to the SAME per-install secret the mcp client
    uses.

    NOTE (multi-service-account limitation, bounded follow-up): today exactly one
    service account ('mcp') exists, so disable_stale_service_user's blanket
    deactivate-then-reconcile touches only that row. If more service accounts are
    ever added, the disable/reconcile pairing would need to skip rows about to be
    reconciled (or do disable+reconcile in one transaction) to avoid a momentary
    inactive window — see the #193 integration triage.
    """
    # #186 BLOCKER 3 / #188 (bonnyr-f5): fail closed BEFORE any lookup if the
    # caller points a service account at a reserved human username. Without this,
    # a deployment that sets MCP_SERVICE_USERNAME=admin (or ships the chart's old
    # mcpUsername: admin) silently rewrites the human admin row and grants the mcp
    # secret admin access. A service account may never co-opt a human identity.
    if _is_reserved_human_username(username):
        raise ValueError(
            f"refusing to reconcile reserved human username '{username}' as a "
            f"service account — set MCP_SERVICE_USERNAME to a dedicated name like "
            f"'mcp' (a service account must not co-opt the human admin identity)"
        )

    # This function only ever runs with a usable password (seed_auth_step's
    # _mcp_pw_usable gate). Fail closed and loudly if that contract is violated:
    # the unset / published-default case belongs to disable_stale_service_user,
    # never here. Seeding/reconciling the role=admin, must-change-EXEMPT mcp
    # account to a shipped published default would republish a live, publicly-known
    # admin credential (#186).
    if not password or password in _KNOWN_DEFAULT_SERVICE_PASSWORDS:
        raise ValueError(
            f"refusing to seed service account '{username}' without a usable "
            f"MCP_SERVICE_PASSWORD (it is unset or a known published default); the "
            f"unset case is owned by disable_stale_service_user, not this function."
        )

    # #186 (bonnyr-f5 r4/r5, INV-8): lock the row for the read-then-write on the
    # RECONCILE (existing-row) path, so two `api` replicas cannot desync the stored
    # hash. No-op on SQLite (tests). with_for_update() cannot lock a not-yet-existing
    # row, so the FIRST-CREATE path is serialised by the username UNIQUE constraint
    # instead (a losing racer's INSERT raises and that boot's seed retries).
    user = db.query(User).filter(User.username == username).with_for_update().first()

    if user is None:
        svc = create_user(
            db=db,
            username=username,
            email=f"{username}@bnk-forge.local",
            password=password,
            role=role,
            must_change_password=False,
        )
        svc.is_service_account = True  # type: ignore[assignment]  # provenance (#188)
        # ENG-006: Startup seed manages its own transaction
        db.commit()
        logger.info(f"Created service account: {username} (role={role})")
        return

    # Existing row. bonnyr-f5 #188: refuse to reconcile a row this seeder did NOT
    # provision as a service account — a name collision must never take over a
    # human account. Gate on provenance, not the username. EXCEPTION (#186
    # integration, scoped by bonnyr-f5 #193 B1): a non-service row is adopted ONLY
    # when it carries v2_155's exact backfill fingerprint — username 'mcp' AND
    # email 'mcp@bnk-forge.local'. That is a stale service credential from a
    # pre-provenance install (the migration deliberately used the same conservative
    # rule so "a real human who merely happens to be named mcp is left untouched"),
    # so neutralising it is the upgrade remediation. Keying the exception on the
    # password value instead (as the pre-#193 code did) adopted ANY human row whose
    # password was `changeme` — a takeover of the human account (bonnyr-f5 #193 B1).
    is_adoption = False
    if not user.is_service_account:
        fingerprint_match = (
            user.username == "mcp" and str(user.email) == "mcp@bnk-forge.local"
        )
        holds_published_default = fingerprint_match and any(
            verify_password(p, str(user.hashed_password))
            for p in _KNOWN_DEFAULT_SERVICE_PASSWORDS
        )
        if not holds_published_default:
            raise ValueError(
                f"refusing to reconcile '{username}': it is not a service account. "
                f"Point MCP_SERVICE_USERNAME at a dedicated name that isn't an "
                f"existing user."
            )
        is_adoption = True

    # Operator supplied a genuine (non-default) password: reconcile to it.
    # Re-activation is required and safe (disable_stale_service_user runs first on
    # every boot and may have deactivated this row; its docstring promises that
    # configuring a real password "re-seeds and re-activates it"). Role is left
    # untouched — we do NOT widen privilege on reconcile (bonnyr-f5 #188).
    user.hashed_password = hash_password(password)  # type: ignore[assignment]
    # bonnyr-f5 #193 B1: only clear the must-change gate on a genuine service row
    # (provenance already set). Never clear it as a side effect of ADOPTING a row —
    # doing so would defeat #184/#186's must-change gate on the adopted account.
    if not is_adoption:
        user.must_change_password = False  # type: ignore[assignment]
    user.is_active = True  # type: ignore[assignment]  # revive a disabled-stale row
    user.is_service_account = True  # type: ignore[assignment]  # adopt a backfilled/legacy row
    db.commit()  # ENG-006: Startup seed manages its own transaction
    logger.info(f"Reconciled service account: {username}")


def disable_stale_service_user(
    db: Session,
    skip_username: str | None = None,
    password_configured: bool = False,
) -> None:
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

    bonnyr-f5 #193 M2: ``skip_username`` leaves the row the caller is about to
    reconcile untouched, so a correctly-configured install never commits an
    inactive window for its live MCP account (a rolling restart would otherwise
    401 live MCP traffic for a bcrypt-plus-commit window). ``password_configured``
    only tunes the log wording: when a usable MCP_SERVICE_PASSWORD IS set, any row
    still disabled here is a genuinely stale EXTRA service account, not the "no
    password is set" case -- so the misleading "no usable MCP_SERVICE_PASSWORD is
    set" warning no longer fires on every boot of a correct install.
    """
    query = db.query(User).filter(
        User.is_active.is_(True),
        User.is_service_account.is_(True),  # bonnyr-f5 #188: never a human row
    )
    if skip_username is not None:
        query = query.filter(User.username != skip_username)
    rows = query.all()
    if not rows:
        return
    for user in rows:
        user.is_active = False  # type: ignore[assignment]
        # bonnyr-f5 #193 (minor): don't leave the (possibly published-default)
        # hash intact and lean solely on is_active + the login route guard.
        # Neutralise the credential too, so the stale default cannot authenticate
        # even if some future caller checks the password before is_active, or the
        # row is flipped active again out of band. Re-enabling the account always
        # goes through ensure_service_user, which reconciles the hash to
        # MCP_SERVICE_PASSWORD, so overwriting it here loses nothing recoverable.
        user.hashed_password = hash_password(secrets.token_urlsafe(32))  # type: ignore[assignment]
    db.commit()
    for user in rows:
        if password_configured:
            logger.warning(
                "Disabled extra stale MCP service account '%s' -- a usable "
                "MCP_SERVICE_PASSWORD is configured for '%s', so this additional "
                "service row's pre-existing credential must not keep authenticating.",
                user.username,
                skip_username,
            )
        else:
            logger.warning(
                "Disabled stale MCP service account '%s' -- no usable "
                "MCP_SERVICE_PASSWORD is set, so its pre-existing (possibly default) "
                "credential must not keep authenticating. Set MCP_SERVICE_PASSWORD to "
                "re-enable MCP.",
                user.username,
            )
