"""ADR-494 Phase A: introduce release_source table + add provenance columns to bnk_deployable_release.

Revision ID: v2_148
Revises: v2_147
Create Date: 2026-07-22

New table: release_source
  First-class BNK release source entity (kind = oci|mirror|manual) with optional
  encrypted credential and sync-state tracking.

Catalog changes: bnk_deployable_release
  + source_id: Integer FK → release_source.id ON DELETE SET NULL (nullable)
  + last_synced: DateTime(timezone=True) nullable
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_148"
down_revision = "v2_147"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create release_source table
    # ------------------------------------------------------------------
    op.create_table(
        "release_source",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("credential_encrypted", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auto_sync", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_interval_hours", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sync_status", sa.String(50), nullable=False, server_default="idle"),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("release_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.create_index(op.f("ix_release_source_id"), "release_source", ["id"], unique=False)
    op.create_index(op.f("ix_release_source_name"), "release_source", ["name"], unique=True)
    op.create_index("idx_release_source_kind", "release_source", ["kind"])
    op.create_index("idx_release_source_active", "release_source", ["is_active"])
    op.create_index("idx_release_source_sync_status", "release_source", ["sync_status"])

    # ------------------------------------------------------------------
    # 2. Add source_id + last_synced to bnk_deployable_release
    # ------------------------------------------------------------------
    with op.batch_alter_table("bnk_deployable_release") as b:
        b.add_column(sa.Column("source_id", sa.Integer(), nullable=True))
        b.add_column(sa.Column("last_synced", sa.DateTime(timezone=True), nullable=True))
        b.create_foreign_key(
            "fk_bnk_deployable_release_source_id",
            "release_source",
            ["source_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------------------------
    # 1. Remove source_id + last_synced from bnk_deployable_release
    # ------------------------------------------------------------------
    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_bnk_deployable_release_source_id", "bnk_deployable_release", type_="foreignkey")
        op.drop_column("bnk_deployable_release", "source_id")
        op.drop_column("bnk_deployable_release", "last_synced")
    else:
        with op.batch_alter_table("bnk_deployable_release") as b:
            b.drop_column("source_id")
            b.drop_column("last_synced")

    # ------------------------------------------------------------------
    # 2. Drop release_source table
    # ------------------------------------------------------------------
    op.drop_index("idx_release_source_sync_status", table_name="release_source")
    op.drop_index("idx_release_source_active", table_name="release_source")
    op.drop_index("idx_release_source_kind", table_name="release_source")
    op.drop_index(op.f("ix_release_source_name"), table_name="release_source")
    op.drop_index(op.f("ix_release_source_id"), table_name="release_source")
    op.drop_table("release_source")
