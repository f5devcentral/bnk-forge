"""Seed the initial Bluefield software image row.

Per TODO.md (DPU Provisioning), populate the admin catalog with the
default BFB image users can further edit/extend.

Idempotent: only inserts if the filename is not already present.

Revision ID: v2_063
Revises: v2_062
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_063"
down_revision = "v2_062"
branch_labels = None
depends_on = None


INITIAL_FILENAME = "bf-bundle-2.9.2-32_25.02_ubuntu-22.04_prod.bfb"
INITIAL_BASE_URL = "https://content.mellanox.com/BlueField/BFBs/Ubuntu22.04"


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT id FROM bluefield_software_images WHERE image_filename = :fn"),
        {"fn": INITIAL_FILENAME},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                "INSERT INTO bluefield_software_images "
                "(image_filename, base_url, notes) "
                "VALUES (:fn, :url, :notes)"
            ),
            {
                "fn": INITIAL_FILENAME,
                "url": INITIAL_BASE_URL,
                "notes": "Seed image (Ubuntu 22.04 based BF-Bundle 2.9.2).",
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM bluefield_software_images WHERE image_filename = :fn"),
        {"fn": INITIAL_FILENAME},
    )
