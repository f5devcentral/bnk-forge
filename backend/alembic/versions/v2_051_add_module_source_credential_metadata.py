"""REPO-AUTH-002: add normalized module_source credential metadata fields.

Revision ID: v2_051
Revises: v2_050
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_051"
down_revision = "v2_050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("module_sources", sa.Column("credential_type", sa.String(length=50), nullable=True))
    op.add_column("module_sources", sa.Column("credential_scope", sa.String(length=255), nullable=True))
    op.add_column("module_sources", sa.Column("credential_capabilities", sa.JSON(), nullable=True))
    op.add_column("module_sources", sa.Column("credential_metadata", sa.JSON(), nullable=True))
    op.add_column("module_sources", sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("module_sources", sa.Column("credential_last_rotated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("module_sources", sa.Column("credential_validation_status", sa.String(length=50), nullable=True))
    op.add_column("module_sources", sa.Column("credential_last_validated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("module_sources", sa.Column("credential_validation_error", sa.Text(), nullable=True))

    op.create_index("ix_module_sources_credential_type", "module_sources", ["credential_type"], unique=False)
    op.create_index(
        "ix_module_sources_credential_validation_status",
        "module_sources",
        ["credential_validation_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_module_sources_credential_validation_status", table_name="module_sources")
    op.drop_index("ix_module_sources_credential_type", table_name="module_sources")

    op.drop_column("module_sources", "credential_validation_error")
    op.drop_column("module_sources", "credential_last_validated_at")
    op.drop_column("module_sources", "credential_validation_status")
    op.drop_column("module_sources", "credential_last_rotated_at")
    op.drop_column("module_sources", "credential_expires_at")
    op.drop_column("module_sources", "credential_metadata")
    op.drop_column("module_sources", "credential_capabilities")
    op.drop_column("module_sources", "credential_scope")
    op.drop_column("module_sources", "credential_type")
