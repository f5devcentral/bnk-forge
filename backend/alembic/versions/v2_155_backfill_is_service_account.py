"""Backfill users.is_service_account for the legacy mcp service account.

Revision ID: v2_155
Revises: v2_154

bonnyr-f5 #188 round 4 (INV-7) / #193 (minor, rationale corrected): the backfill
lives in its OWN revision, separate from ``v2_154`` (which only adds the column,
``server_default false``). NOTE: ``v2_154`` and ``v2_155`` are BOTH introduced in
THIS diff — the earlier claim that ``v2_154`` "already shipped in earlier RCs" was
false, so no install has ``alembic_version = v2_154`` without also getting
``v2_155`` on the same ``alembic upgrade``. The split is kept regardless because it
is the correct shape: a schema change and its data backfill are cleanly separable
and independently reversible, and IF a future build ever ships the column ahead of
the backfill, a distinct revision still guarantees every install that stopped at
``v2_154`` applies the backfill on its next upgrade (appending it to ``v2_154``
would silently skip such installs, since an applied revision is immutable).

Why the backfill is needed at all: the column ships ``server_default false``, so
without it EVERY pre-existing row — including the ``mcp`` service account that
every already-deployed install carries — is classified ``is_service_account =
False``. Both consumers gate on that column, so the mis-classification is a trap
on an upgraded install:

  * ``disable_stale_service_user`` filters ``is_service_account IS TRUE``, so the
    stale ``mcp`` row is never disabled and the shipped ``mcp-service-changeme``
    default keeps authenticating as role=admin -> issue #187 stays open for every
    existing install.
  * ``ensure_service_user`` refuses any row where ``not is_service_account``, so
    setting a real ``MCP_SERVICE_PASSWORD`` raises forever and MCP is dead.

Scope of the backfill is deliberately narrow — we reclassify a row as a service
account ONLY when it carries the exact fingerprint that the legacy
``ensure_service_user`` + ``create_user`` seed produced, never an arbitrary row:

  * ``username = 'mcp'`` — the ONLY value the legacy service account was ever
    created under. ``MCP_SERVICE_USERNAME`` defaults to ``'mcp'`` (core/config.py)
    and the migration cannot know an operator's overridden value at apply time;
    reading app settings into a migration is non-deterministic and, worse, some
    legacy ``.env`` files point that var at ``admin`` — backfilling the configured
    name would then reclassify the HUMAN admin as a service account, the exact
    takeover #188 set out to prevent. We backfill the known legacy default only.
  * ``email = 'mcp@bnk-forge.local'`` — ``create_user`` synthesised the service
    account's email as ``f"{username}@bnk-forge.local"``, so the legacy ``mcp``
    row provably has this address. Requiring it as a second signal means a real
    human who merely happens to be named ``mcp`` (with any real email) is left
    untouched.

The pair (username + synthesised email) is the creation fingerprint of the
service account and cannot collide with a human provisioned through normal
signup, which always carries a real email. Rows that don't match keep the
``server_default false`` — correct, they are human accounts.

Documented edge: an operator who set a CUSTOM ``MCP_SERVICE_USERNAME`` (not the
default ``mcp``) before upgrading will not have that row backfilled here; the
remedy is to point ``MCP_SERVICE_USERNAME`` at a dedicated name (the default
``mcp`` is now backfilled and reconcilable). Silently reclassifying an
operator-named row we cannot prove we created risks taking over a human account,
which is strictly worse than a one-line rename for the rare custom-username install.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_155"
down_revision = "v2_154"
branch_labels = None
depends_on = None

# The legacy default service username and the email create_user synthesised for
# it. Kept as constants so the backfill scope is explicit and auditable.
_LEGACY_SERVICE_USERNAME = "mcp"
_LEGACY_SERVICE_EMAIL = "mcp@bnk-forge.local"


def upgrade() -> None:
    # Backfill: classify ONLY the provably-seeded legacy mcp service account.
    # Parameterised so the literals are quoted safely on every backend.
    op.execute(
        sa.text(
            "UPDATE users SET is_service_account = :t "
            "WHERE username = :u AND email = :e"
        ).bindparams(t=True, u=_LEGACY_SERVICE_USERNAME, e=_LEGACY_SERVICE_EMAIL)
    )


def downgrade() -> None:
    # Reversing the classification is safe and precise: only rows carrying the
    # exact legacy fingerprint were flipped, so we clear the flag for exactly
    # those rows. The column itself is owned by v2_154 and is left in place.
    op.execute(
        sa.text(
            "UPDATE users SET is_service_account = :f "
            "WHERE username = :u AND email = :e"
        ).bindparams(f=False, u=_LEGACY_SERVICE_USERNAME, e=_LEGACY_SERVICE_EMAIL)
    )
