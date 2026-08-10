"""Phase 1 — bf.conf template catalog + per-VLAN uplink + drop LAG switch.

Introduces:
  • bf_conf_templates table (admin-managed; Phase 2 adds full CRUD).
  • project_dpu_settings.bf_template_id FK → bf_conf_templates.id.
  • project_dpu_settings.{external,internal,storage}_vlan_uplink
    — one of "bond0" (default, when LAG is in use) / "p0" / "p1".
  • Drops project_dpu_settings.high_speed_lag: LAG vs. non-LAG is now
    expressed by the chosen bf.conf template + the per-VLAN uplink.

Seeds two templates from backend/alembic/seed_data/:
  1. "F5/Nvidia RCP RA with LACP based LAG uplinks" (bf_template_lag.conf)
  2. "Interfaces attached to p0 and/or p1 uplink" (bf_template_nolag.conf)

Revision ID: v2_069
Revises: v2_068
"""

import os

import sqlalchemy as sa

from alembic import op

revision = "v2_069"
down_revision = "v2_068"
branch_labels = None
depends_on = None


_UPLINK_COLS = [
    "external_vlan_uplink",
    "internal_vlan_uplink",
    "storage_vlan_uplink",
]


_SEED_TEMPLATES = [
    (
        "F5/Nvidia RCP RA with LACP based LAG uplinks",
        "bf_template_lag.conf",
    ),
    (
        "Interfaces attached to p0 and/or p1 uplink",
        "bf_template_nolag.conf",
    ),
]


def _read_seed(filename: str) -> str:
    seed_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "seed_data", filename
    )
    with open(seed_path, encoding="utf-8") as f:
        return f.read()


def upgrade() -> None:
    op.create_table(
        "bf_conf_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("matching_bnk_version", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    op.add_column(
        "project_dpu_settings",
        sa.Column(
            "bf_template_id",
            sa.Integer(),
            sa.ForeignKey("bf_conf_templates.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    for col in _UPLINK_COLS:
        op.add_column(
            "project_dpu_settings",
            sa.Column(col, sa.String(length=16), nullable=True),
        )

    # Seed the two templates (idempotent on name uniqueness).
    bind = op.get_bind()
    for name, filename in _SEED_TEMPLATES:
        existing = bind.execute(
            sa.text("SELECT id FROM bf_conf_templates WHERE name = :name"),
            {"name": name},
        ).first()
        if existing is None:
            bind.execute(
                sa.text(
                    "INSERT INTO bf_conf_templates "
                    "(name, description, content, matching_bnk_version) "
                    "VALUES (:name, :desc, :content, NULL)"
                ),
                {
                    "name": name,
                    "desc": name,
                    "content": _read_seed(filename),
                },
            )

    op.drop_column("project_dpu_settings", "high_speed_lag")


def downgrade() -> None:
    op.add_column(
        "project_dpu_settings",
        sa.Column(
            "high_speed_lag",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    for col in reversed(_UPLINK_COLS):
        op.drop_column("project_dpu_settings", col)
    op.drop_column("project_dpu_settings", "bf_template_id")
    op.drop_table("bf_conf_templates")
