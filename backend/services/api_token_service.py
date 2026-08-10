"""Issue, verify, and revoke long-lived API tokens for non-interactive access.

Tokens are 32 base32 characters (160 bits of entropy) prefixed with ``bnk_``.
The plaintext is returned exactly once at creation; the DB stores only a
SHA-256 hash. The 12-character ``token_prefix`` (``bnk_`` + first 8 random
chars) is also stored and indexed so verification can fast-reject
non-existent tokens before doing the (constant-time) full hash compare —
useful when many tokens exist or under credential-spray load.

Token format example::

    bnk_K3M7QBVZX9A1HPNW2RS4TJEF6L8CGYDU
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, UnauthorizedError
from models import ApiToken, User

TOKEN_PREFIX = "bnk_"
TOKEN_RANDOM_LEN = 32  # base32 chars
PREFIX_LOOKUP_LEN = 12  # "bnk_" + first 8 random


def _generate_token_plaintext() -> str:
    # secrets.token_urlsafe gives base64url; we want lowercase base32 for
    # readability and to avoid mixed-case copy issues from terminals.
    raw = secrets.token_bytes(20)  # 160 bits → 32 base32 chars
    body = _base32_lower(raw)
    return f"{TOKEN_PREFIX}{body}"


def _base32_lower(raw: bytes) -> str:
    import base64
    return base64.b32encode(raw).decode("ascii").lower().rstrip("=")


def _hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _prefix_of(plaintext: str) -> str:
    return plaintext[:PREFIX_LOOKUP_LEN]


@dataclass(frozen=True)
class IssuedToken:
    id: int
    plaintext: str  # only available at creation
    name: str
    role: str
    expires_at: datetime | None
    created_at: datetime


class ApiTokenService:
    """Service layer for API token lifecycle."""

    VALID_ROLES = {"viewer", "operator", "admin"}

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        user: User,
        name: str,
        role: str | None = None,
        expires_in_days: int | None = None,
        max_role: str | None = None,
    ) -> IssuedToken:
        """Issue a new token for ``user``. Returns the plaintext exactly once.

        ``max_role`` is the ceiling the new token may not exceed — callers pass
        the role in force for the request, which for an API-token caller is the
        token's own (possibly downscoped) role. Without it a viewer-scoped CI
        token on an admin account could mint itself a fresh admin token.
        """
        name = (name or "").strip()
        if not name:
            raise BadRequestError("Token name is required", code="TOKEN_NAME_REQUIRED")

        # An API token can only grant a role <= the caller's role in force.
        # Roles are linearly ordered: viewer < operator < admin.
        ceiling = (max_role or user.role or "viewer").strip()
        effective_role = (role or ceiling).strip()
        if effective_role not in self.VALID_ROLES:
            raise BadRequestError(
                f"Invalid role '{effective_role}'", code="INVALID_TOKEN_ROLE"
            )
        if not _role_at_most(effective_role, ceiling):
            raise BadRequestError(
                f"Cannot issue token with role '{effective_role}' (you are '{ceiling}')",
                code="TOKEN_ROLE_ESCALATION",
            )

        expires_at: datetime | None = None
        if expires_in_days is not None:
            if expires_in_days <= 0 or expires_in_days > 3650:
                raise BadRequestError(
                    "expires_in_days must be between 1 and 3650",
                    code="INVALID_EXPIRY",
                )
            expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)

        plaintext = _generate_token_plaintext()
        token = ApiToken(
            user_id=user.id,
            name=name,
            token_hash=_hash_token(plaintext),
            token_prefix=_prefix_of(plaintext),
            role=effective_role,
            expires_at=expires_at,
        )
        self.db.add(token)
        self.db.flush()
        self.db.refresh(token)

        return IssuedToken(
            id=token.id,
            plaintext=plaintext,
            name=token.name,
            role=token.role,
            expires_at=token.expires_at,
            created_at=token.created_at,
        )

    def list_for_user(self, user: User) -> list[dict[str, Any]]:
        rows = (
            self.db.query(ApiToken)
            .filter(ApiToken.user_id == user.id)
            .order_by(ApiToken.created_at.desc())
            .all()
        )
        return [self._serialize(t) for t in rows]

    def revoke(self, *, user: User, token_id: int) -> None:
        token = (
            self.db.query(ApiToken)
            .filter(ApiToken.id == token_id, ApiToken.user_id == user.id)
            .first()
        )
        if token is None:
            raise NotFoundError("api_token", token_id)
        self.db.delete(token)
        self.db.flush()

    # ------------------------------------------------------------------
    # Verification (used by auth middleware)
    # ------------------------------------------------------------------

    def verify(self, plaintext: str) -> tuple[User, ApiToken]:
        """Return (user, token) for a valid plaintext token; raise UnauthorizedError otherwise."""
        if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
            raise UnauthorizedError("Invalid API token format")

        prefix = _prefix_of(plaintext)
        candidates = (
            self.db.query(ApiToken)
            .filter(ApiToken.token_prefix == prefix)
            .all()
        )
        expected_hash = _hash_token(plaintext)
        match: ApiToken | None = None
        for cand in candidates:
            if hmac.compare_digest(cand.token_hash, expected_hash):
                match = cand
                break
        if match is None:
            raise UnauthorizedError("Invalid API token")

        if match.expires_at is not None and match.expires_at < datetime.now(UTC):
            raise UnauthorizedError("API token has expired")

        user = self.db.query(User).filter(User.id == match.user_id).first()
        if user is None or not user.is_active:
            raise UnauthorizedError("Token user is inactive")

        match.last_used_at = datetime.now(UTC)
        self.db.flush()

        return user, match

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(token: ApiToken) -> dict[str, Any]:
        return {
            "id": token.id,
            "name": token.name,
            "role": token.role,
            "token_prefix": token.token_prefix,
            "expires_at": token.expires_at,
            "last_used_at": token.last_used_at,
            "created_at": token.created_at,
        }


_ROLE_ORDER = {"viewer": 1, "operator": 2, "admin": 3}


def _role_at_most(requested: str, max_role: str) -> bool:
    return _ROLE_ORDER.get(requested, 99) <= _ROLE_ORDER.get(max_role, 0)


def clamp_role(token_role: str | None, user_role: str | None) -> str:
    """The lesser of the token's role and its owner's current account role.

    Issue-time validation already rejects a token above the issuer's role, but
    the account can be demoted afterwards — clamping again at request time means
    a demotion instantly narrows every token that user has outstanding.
    """
    account_role = (user_role or "viewer").strip()
    requested = (token_role or "").strip()
    if not requested or requested not in _ROLE_ORDER:
        return account_role
    return requested if _role_at_most(requested, account_role) else account_role
