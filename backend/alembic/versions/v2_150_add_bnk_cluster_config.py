"""ADR-424: Add BnkClusterConfig table and multi-host DPU cluster columns.

Revision ID: v2_150
Revises: v2_149

Adds:
  - bnk_cluster_configs table (1:1 side-table to kubernetes_clusters for multi-host BNK clusters)
  - bare_metal_hosts.is_control_plane boolean column
  - dpus.kubernetes_cluster_id, dpus.host_tmfifo_ip, dpus.dpu_tmfifo_ip columns
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_150"
down_revision = "v2_149"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create bnk_cluster_configs table
    op.create_table(
        "bnk_cluster_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cluster_id", sa.Integer(), nullable=False),
        sa.Column("tmfifo_pool_cidr", sa.String(length=64), server_default="192.168.100.0/22", nullable=False),
        sa.Column("join_transport", sa.String(length=32), server_default="rshim", nullable=False),
        sa.Column("control_plane_host_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True),
        sa.ForeignKeyConstraint(["cluster_id"], ["kubernetes_clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["control_plane_host_id"], ["bare_metal_hosts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cluster_id"),
    )
    op.create_index(op.f("ix_bnk_cluster_configs_cluster_id"), "bnk_cluster_configs", ["cluster_id"], unique=True)
    op.create_index(op.f("ix_bnk_cluster_configs_id"), "bnk_cluster_configs", ["id"], unique=False)

    # 2. Add is_control_plane to bare_metal_hosts
    op.add_column(
        "bare_metal_hosts",
        sa.Column("is_control_plane", sa.Boolean(), server_default="false", nullable=False),
    )

    # 3. Add kubernetes_cluster_id and tmfifo IPs to dpus
    op.add_column(
        "dpus",
        sa.Column("kubernetes_cluster_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "dpus",
        sa.Column("host_tmfifo_ip", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "dpus",
        sa.Column("dpu_tmfifo_ip", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_dpus_kubernetes_cluster_id",
        "dpus",
        "kubernetes_clusters",
        ["kubernetes_cluster_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial unique index — uniqueness backstop for concurrent IPAM allocation.
    # Ensures (cluster, dpu_tmfifo_ip) is unique across non-NULL allocations.
    # A concurrent race in allocate_next_subnet then becomes an IntegrityError
    # instead of silent IP address duplication.
    op.create_index(
        "ix_dpus_cluster_tmfifo_ip",
        "dpus",
        ["kubernetes_cluster_id", "dpu_tmfifo_ip"],
        unique=True,
        postgresql_where=sa.text("dpu_tmfifo_ip IS NOT NULL"),
    )
    # ix_dpus_kubernetes_cluster_id is created in v2_151 so that stacks
    # that already applied v2_150 (before the index was added) get it via
    # an incremental migration rather than a silent no-op.


def downgrade() -> None:
    op.drop_index("ix_dpus_cluster_tmfifo_ip", table_name="dpus")
    op.drop_constraint("fk_dpus_kubernetes_cluster_id", "dpus", type_="foreignkey")
    op.drop_column("dpus", "dpu_tmfifo_ip")
    op.drop_column("dpus", "host_tmfifo_ip")
    op.drop_column("dpus", "kubernetes_cluster_id")

    op.drop_column("bare_metal_hosts", "is_control_plane")

    op.drop_index(op.f("ix_bnk_cluster_configs_id"), table_name="bnk_cluster_configs")
    op.drop_index(op.f("ix_bnk_cluster_configs_cluster_id"), table_name="bnk_cluster_configs")
    op.drop_table("bnk_cluster_configs")
