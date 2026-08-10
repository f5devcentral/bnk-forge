"""Add api_tokens table for non-interactive (CLI / CI/CD) authentication.

Lets operators issue long-lived bearer tokens scoped to a user account, without
the JWT login dance. Tokens are stored as SHA-256 hashes; the plaintext is
returned exactly once at creation. Format: ``bnk_<32-char-base32>`` so the
prefix can be used as a fast-reject lookup before doing the constant-time
hash compare.

Revision ID: v2_139
Revises: v2_138

Chained onto v2_138 (container_registries, from #340) after #340 merged to
staging ahead of this PR — resolving the earlier v2_136 collision between the
two branches.
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_139"
down_revision = "v2_138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    has_table = bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'api_tokens'"
        )
    ).fetchone()
    if has_table:
        return

    op.create_table(
        "api_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="operator"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_api_tokens_user_id", "api_tokens", ["user_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"], unique=True)
    op.create_index("ix_api_tokens_token_prefix", "api_tokens", ["token_prefix"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_token_prefix", table_name="api_tokens")
    op.drop_index("ix_api_tokens_token_hash", table_name="api_tokens")
    op.drop_index("ix_api_tokens_user_id", table_name="api_tokens")
    op.drop_table("api_tokens")
