"""ADR-424: Add plain index on dpus(kubernetes_cluster_id).

Revision ID: v2_151
Revises: v2_150

Separated from v2_150 so that stacks that already applied v2_150 (before
this index was appended to that revision) can acquire the index via an
incremental migration rather than hitting a silent no-op or a duplicate-
index error (INV-7).
"""

import sqlalchemy as sa  # noqa: F401 — imported for symmetry with other migrations

from alembic import op

revision = "v2_151"
down_revision = "v2_150"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain index for membership queries, before_delete UPDATE, and reconcile
    # (WHERE kubernetes_cluster_id = X over NULL-tmfifo rows that the partial
    # unique index ix_dpus_cluster_tmfifo_ip does not cover).
    # if_not_exists=True makes this idempotent for stacks that already ran the
    # old v2_150 (which included this index inline before it was extracted here).
    op.create_index(
        "ix_dpus_kubernetes_cluster_id", "dpus", ["kubernetes_cluster_id"],
        unique=False, if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_dpus_kubernetes_cluster_id", table_name="dpus", if_exists=True)
