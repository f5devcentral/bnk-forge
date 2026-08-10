"""ARCH-EXT-002B: add blueprint source and release catalog persistence.

Revision ID: v2_052
Revises: v2_051
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_052"
down_revision = "v2_051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blueprint_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=False),
        sa.Column("branch", sa.String(length=100), nullable=True),
        sa.Column("git_ref", sa.String(length=100), nullable=True),
        sa.Column("sync_status", sa.String(length=50), nullable=False),
        sa.Column("sync_error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_blueprint_sources_id"), "blueprint_sources", ["id"], unique=False)
    op.create_index(op.f("ix_blueprint_sources_name"), "blueprint_sources", ["name"], unique=True)
    op.create_index(op.f("ix_blueprint_sources_source_type"), "blueprint_sources", ["source_type"], unique=False)
    op.create_index(op.f("ix_blueprint_sources_sync_status"), "blueprint_sources", ["sync_status"], unique=False)
    op.create_index(op.f("ix_blueprint_sources_is_active"), "blueprint_sources", ["is_active"], unique=False)

    op.create_index("idx_blueprint_source_type", "blueprint_sources", ["source_type"], unique=False)
    op.create_index("idx_blueprint_source_status", "blueprint_sources", ["sync_status"], unique=False)
    op.create_index("idx_blueprint_source_active", "blueprint_sources", ["is_active"], unique=False)

    op.create_table(
        "blueprint_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("blueprint_source_id", sa.Integer(), nullable=False),
        sa.Column("blueprint_id", sa.String(length=255), nullable=False),
        sa.Column("blueprint_version", sa.String(length=100), nullable=False),
        sa.Column("blueprint_name", sa.String(length=255), nullable=False),
        sa.Column("blueprint_description", sa.Text(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("source_path", sa.String(length=500), nullable=True),
        sa.Column("source_ref", sa.String(length=255), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("validation_state", sa.String(length=50), nullable=False),
        sa.Column("validation_errors", sa.JSON(), nullable=True),
        sa.Column("release_state", sa.String(length=50), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
        sa.ForeignKeyConstraint(["blueprint_source_id"], ["blueprint_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_blueprint_releases_blueprint_id"), "blueprint_releases", ["blueprint_id"], unique=False)
    op.create_index(op.f("ix_blueprint_releases_blueprint_source_id"), "blueprint_releases", ["blueprint_source_id"], unique=False)
    op.create_index(op.f("ix_blueprint_releases_id"), "blueprint_releases", ["id"], unique=False)
    op.create_index(op.f("ix_blueprint_releases_is_active"), "blueprint_releases", ["is_active"], unique=False)
    op.create_index(op.f("ix_blueprint_releases_release_state"), "blueprint_releases", ["release_state"], unique=False)
    op.create_index(op.f("ix_blueprint_releases_validation_state"), "blueprint_releases", ["validation_state"], unique=False)

    op.create_unique_constraint(
        "uq_blueprint_release_source_identity_version",
        "blueprint_releases",
        ["blueprint_source_id", "blueprint_id", "blueprint_version"],
    )
    op.create_index(
        "idx_blueprint_release_identity",
        "blueprint_releases",
        ["blueprint_id", "blueprint_version"],
        unique=False,
    )
    op.create_index("idx_blueprint_release_state", "blueprint_releases", ["release_state"], unique=False)
    op.create_index("idx_blueprint_release_validation", "blueprint_releases", ["validation_state"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_blueprint_release_validation", table_name="blueprint_releases")
    op.drop_index("idx_blueprint_release_state", table_name="blueprint_releases")
    op.drop_index("idx_blueprint_release_identity", table_name="blueprint_releases")
    op.drop_constraint("uq_blueprint_release_source_identity_version", "blueprint_releases", type_="unique")
    op.drop_index(op.f("ix_blueprint_releases_validation_state"), table_name="blueprint_releases")
    op.drop_index(op.f("ix_blueprint_releases_release_state"), table_name="blueprint_releases")
    op.drop_index(op.f("ix_blueprint_releases_is_active"), table_name="blueprint_releases")
    op.drop_index(op.f("ix_blueprint_releases_id"), table_name="blueprint_releases")
    op.drop_index(op.f("ix_blueprint_releases_blueprint_source_id"), table_name="blueprint_releases")
    op.drop_index(op.f("ix_blueprint_releases_blueprint_id"), table_name="blueprint_releases")
    op.drop_table("blueprint_releases")

    op.drop_index("idx_blueprint_source_active", table_name="blueprint_sources")
    op.drop_index("idx_blueprint_source_status", table_name="blueprint_sources")
    op.drop_index("idx_blueprint_source_type", table_name="blueprint_sources")
    op.drop_index(op.f("ix_blueprint_sources_is_active"), table_name="blueprint_sources")
    op.drop_index(op.f("ix_blueprint_sources_sync_status"), table_name="blueprint_sources")
    op.drop_index(op.f("ix_blueprint_sources_source_type"), table_name="blueprint_sources")
    op.drop_index(op.f("ix_blueprint_sources_name"), table_name="blueprint_sources")
    op.drop_index(op.f("ix_blueprint_sources_id"), table_name="blueprint_sources")
    op.drop_table("blueprint_sources")
