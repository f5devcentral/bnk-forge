"""Seed initial DOCA software image rows.

Populate the admin catalog with default DOCA packages. Idempotent:
only inserts rows whose version is not already present.

Revision ID: v2_108
Revises: v2_107
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_110"
down_revision = "v2_109"
branch_labels = None
depends_on = None

DOCA_SEEDS = [
    {
        "version": "2.9.2",
        "package_name": "doca-all",
        "description": "DOCA 2.9.2 — mst-tools, rshim, mlnx-sf, OVS, firmware (BNK 2.2 tested)",
        "is_default": True,
        "target": "both",
    },
    {
        "version": "2.8.0",
        "package_name": "doca-all",
        "description": "DOCA 2.8.0 — previous stable release",
        "is_default": False,
        "target": "both",
    },
]


def upgrade() -> None:
    bind = op.get_bind()
    for seed in DOCA_SEEDS:
        existing = bind.execute(
            sa.text("SELECT id FROM doca_software_images WHERE version = :v"),
            {"v": seed["version"]},
        ).first()
        if existing is None:
            bind.execute(
                sa.text(
                    "INSERT INTO doca_software_images "
                    "(version, package_name, description, is_default, target) "
                    "VALUES (:version, :package_name, :description, :is_default, :target)"
                ),
                seed,
            )


def downgrade() -> None:
    bind = op.get_bind()
    for seed in DOCA_SEEDS:
        bind.execute(
            sa.text("DELETE FROM doca_software_images WHERE version = :v"),
            {"v": seed["version"]},
        )
