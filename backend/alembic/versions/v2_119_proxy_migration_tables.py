"""Add proxy_migrations and proxy_migration_steps tables (D-021 P3).

Revision ID: v2_119
Revises: v2_117
Create Date: 2026-06-04

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_119"
down_revision = "v2_118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proxy_migrations",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "cluster_id",
            sa.Integer(),
            sa.ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("source_descriptor", sa.JSON(), nullable=False),
        sa.Column("translated_manifest", sa.Text(), nullable=True),
        sa.Column("bnk_gateway_name", sa.String(255), nullable=True),
        sa.Column("bnk_gateway_namespace", sa.String(255), nullable=True),
        sa.Column("verify_result", sa.JSON(), nullable=True),
        sa.Column("teardown_info", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("current_step", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_step", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("triggered_by", sa.String(255), nullable=True),
        sa.Column("cutover_confirmed_by", sa.String(255), nullable=True),
        sa.Column("cutover_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("teardown_confirmed_by", sa.String(255), nullable=True),
        sa.Column("teardown_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_proxy_migrations_cluster_status",
        "proxy_migrations",
        ["cluster_id", "status"],
    )
    op.create_index(
        "ix_proxy_migrations_project_id",
        "proxy_migrations",
        ["project_id"],
    )

    op.create_table(
        "proxy_migration_steps",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "migration_id",
            sa.Integer(),
            sa.ForeignKey("proxy_migrations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("output_log", sa.Text(), nullable=True),
        sa.Column("result_data", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("requires_confirm", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "ix_proxy_migration_steps_migration_idx",
        "proxy_migration_steps",
        ["migration_id", "step_index"],
    )


def downgrade() -> None:
    op.drop_index("ix_proxy_migration_steps_migration_idx", table_name="proxy_migration_steps")
    op.drop_table("proxy_migration_steps")
    op.drop_index("ix_proxy_migrations_project_id", table_name="proxy_migrations")
    op.drop_index("ix_proxy_migrations_cluster_status", table_name="proxy_migrations")
    op.drop_table("proxy_migrations")
