"""Add heartbeat + fence-token lock columns to proxy_deployments and bnk_upgrades.

Phase 1.6 of the robust module locking RFC: extends the Postgres heartbeat +
fencing-token pattern from project_modules (Phase 1, PR #98) to the two other
entity tables that have concurrent-write race risk.

Required schema on each allowed table (enforced by startup_steps.py assertion):
  - lock_fence_token BIGINT NOT NULL DEFAULT 0
  - holding_task_id  BIGINT NULL
  - heartbeat_at     TIMESTAMPTZ NULL
  - lock_acquired_at TIMESTAMPTZ NULL

This file originally shipped with revision id "v2_098" via PR #105. It collided
with the IBM v2_098 (cloud_credential_templates.ibmcloud_api_key_encrypted) that
landed via the PR #106 merge. Renumbered to v2_106 to linearize the chain after
v2_105. All operations are guarded by inspector checks so DBs already stamped at
v2_098 (with these columns present) can replay this migration as a safe no-op.
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_106"
down_revision = "v2_105"
branch_labels = None
depends_on = None


_TABLES = ("proxy_deployments", "bnk_upgrades")


def _existing_columns(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def _existing_indexes(inspector: sa.engine.reflection.Inspector, table: str) -> set[str]:
    return {ix["name"] for ix in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table in _TABLES:
        existing = _existing_columns(inspector, table)
        if "lock_fence_token" not in existing:
            op.add_column(table, sa.Column(
                "lock_fence_token",
                sa.BigInteger,
                nullable=False,
                server_default="0",
            ))
        if "holding_task_id" not in existing:
            op.add_column(table, sa.Column(
                "holding_task_id",
                sa.BigInteger,
                nullable=True,
            ))
        if "heartbeat_at" not in existing:
            op.add_column(table, sa.Column(
                "heartbeat_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ))
        if "lock_acquired_at" not in existing:
            op.add_column(table, sa.Column(
                "lock_acquired_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ))

    pm_existing = _existing_columns(inspector, "project_modules")
    if "lock_acquired_at" not in pm_existing:
        op.add_column("project_modules", sa.Column(
            "lock_acquired_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ))

    pd_indexes = _existing_indexes(inspector, "proxy_deployments")
    if "idx_proxy_deployments_holding_task_id" not in pd_indexes:
        op.create_index(
            "idx_proxy_deployments_holding_task_id",
            "proxy_deployments",
            ["holding_task_id"],
            postgresql_where=sa.text("holding_task_id IS NOT NULL"),
        )
    bu_indexes = _existing_indexes(inspector, "bnk_upgrades")
    if "idx_bnk_upgrades_holding_task_id" not in bu_indexes:
        op.create_index(
            "idx_bnk_upgrades_holding_task_id",
            "bnk_upgrades",
            ["holding_task_id"],
            postgresql_where=sa.text("holding_task_id IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    pm_existing = _existing_columns(inspector, "project_modules")
    if "lock_acquired_at" in pm_existing:
        op.drop_column("project_modules", "lock_acquired_at")

    bu_indexes = _existing_indexes(inspector, "bnk_upgrades")
    if "idx_bnk_upgrades_holding_task_id" in bu_indexes:
        op.drop_index("idx_bnk_upgrades_holding_task_id", table_name="bnk_upgrades")
    pd_indexes = _existing_indexes(inspector, "proxy_deployments")
    if "idx_proxy_deployments_holding_task_id" in pd_indexes:
        op.drop_index("idx_proxy_deployments_holding_task_id", table_name="proxy_deployments")

    for table in reversed(_TABLES):
        existing = _existing_columns(inspector, table)
        if "lock_acquired_at" in existing:
            op.drop_column(table, "lock_acquired_at")
        if "heartbeat_at" in existing:
            op.drop_column(table, "heartbeat_at")
        if "holding_task_id" in existing:
            op.drop_column(table, "holding_task_id")
        if "lock_fence_token" in existing:
            op.drop_column(table, "lock_fence_token")
