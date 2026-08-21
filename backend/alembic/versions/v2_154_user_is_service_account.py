"""Add users.is_service_account for service-account provenance.

Revision ID: v2_154
Revises: v2_153

bonnyr-f5 #188: ensure_service_user identified service accounts by NAME
(a one-entry denylist of "admin"), so pointing MCP_SERVICE_USERNAME at any other
human row (operator, a named user) let the boot-time reconcile overwrite its
password, promote it to admin, clear its must-change gate and re-activate it.
Provenance recorded at creation lets the seeder refuse any pre-existing row it
did not create, independent of the name.

BACKFILL (bonnyr-f5 #188 round 3): the column ships ``server_default false``, so
without a backfill EVERY pre-existing row — including the ``mcp`` service account
that every already-deployed install carries — is classified ``is_service_account
= False``. Both consumers gate on that column, so the mis-classification is a
trap on an upgraded install:

  * ``disable_stale_service_user`` filters ``is_service_account IS TRUE``, so the
    stale ``mcp`` row is never disabled and the shipped ``mcp-service-changeme``
    default keeps authenticating as role=admin -> issue #187 stays open for every
    existing install.
  * ``ensure_service_user`` refuses any row where ``not is_service_account``, so
    setting a real ``MCP_SERVICE_PASSWORD`` raises forever and MCP is dead.

So ``upgrade()`` must classify the pre-existing service row. Scope of the
backfill is deliberately narrow — we reclassify a row as a service account ONLY
when it carries the exact fingerprint that the legacy ``ensure_service_user`` +
``create_user`` seed produced, never an arbitrary row:

  * ``username = 'mcp'`` — the ONLY value the legacy service account was ever
    created under. ``MCP_SERVICE_USERNAME`` defaults to ``'mcp'`` (core/config.py)
    and the migration cannot know an operator's overridden value at apply time;
    reading app settings into a migration is non-deterministic and, worse, some
    legacy ``.env`` files point that var at ``admin`` — backfilling the configured
    name would then reclassify the HUMAN admin as a service account, the exact
    takeover #188 set out to prevent. We therefore backfill the known legacy
    default only. Operators who ran with a custom MCP username are the documented
    edge (see below).
  * ``email = 'mcp@bnk-forge.local'`` — ``create_user`` synthesised the service
    account's email as ``f"{username}@bnk-forge.local"``, so the legacy ``mcp``
    row provably has this address. Requiring it as a second signal means a real
    human who merely happens to be named ``mcp`` (with any real email) is left
    untouched. This costs nothing for genuine installs, whose ``mcp`` row always
    carries exactly this synthesised address.

The pair (username + synthesised email) is the creation fingerprint of the
service account and cannot collide with a human provisioned through normal
signup, which always carries a real email. Rows that don't match keep the
``server_default false`` — correct, they are human accounts.

Documented edge: an operator who set a CUSTOM ``MCP_SERVICE_USERNAME`` (not the
default ``mcp``) before upgrading will not have that row backfilled here. On the
first boot after upgrade with a real ``MCP_SERVICE_PASSWORD`` set,
``ensure_service_user`` will still refuse that un-provenanced row; the operator's
remedy is the same as for any name collision — point ``MCP_SERVICE_USERNAME`` at
a dedicated name (the default ``mcp`` is now backfilled and reconcilable). This
is intentionally conservative: silently reclassifying an operator-named row we
cannot prove we created risks taking over a human account, which is strictly
worse than requiring a one-line rename for the rare custom-username install.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_154"
down_revision = "v2_153"
branch_labels = None
depends_on = None

# The legacy default service username and the email create_user synthesised for
# it. Kept as constants so the backfill scope is explicit and auditable.
_LEGACY_SERVICE_USERNAME = "mcp"
_LEGACY_SERVICE_EMAIL = "mcp@bnk-forge.local"


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("is_service_account", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # Backfill: classify ONLY the provably-seeded legacy mcp service account.
    # Parameterised so the literals are quoted safely on every backend.
    op.execute(
        sa.text(
            "UPDATE users SET is_service_account = :t "
            "WHERE username = :u AND email = :e"
        ).bindparams(t=True, u=_LEGACY_SERVICE_USERNAME, e=_LEGACY_SERVICE_EMAIL)
    )


def downgrade() -> None:
    # Dropping the column reverses the whole revision, backfill included — the
    # provenance flag exists only in this column, so there is nothing else to
    # undo. Rows revert to being distinguished by name/email as before.
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_service_account")
