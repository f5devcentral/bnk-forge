"""Rekey DOCA catalog on (doca_version, host_os, host_arch).

The DOCA version is the release identity — the BFB filename is derived
metadata, not a natural key. This migration drops the unique constraint
on image_filename, makes it nullable, and adds a composite unique
constraint on the true identity triple.

Revision ID: v2_112
Revises: v2_111
"""

from alembic import op

revision = "v2_114"
down_revision = "v2_113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop unique constraint on image_filename
    op.drop_constraint(
        "bluefield_software_images_image_filename_key",
        "bluefield_software_images",
        type_="unique",
    )

    # 2. Make image_filename and base_url nullable
    with op.batch_alter_table("bluefield_software_images") as batch_op:
        batch_op.alter_column("image_filename", nullable=True)
        batch_op.alter_column("base_url", nullable=True)

    # 3. Add composite unique constraint on the real identity triple
    op.create_unique_constraint(
        "uq_doca_release",
        "bluefield_software_images",
        ["doca_version", "host_os", "host_arch"],
    )


def downgrade() -> None:
    # 1. Drop the composite unique constraint
    op.drop_constraint(
        "uq_doca_release",
        "bluefield_software_images",
        type_="unique",
    )

    # 2. Make image_filename and base_url non-nullable again
    with op.batch_alter_table("bluefield_software_images") as batch_op:
        batch_op.alter_column("image_filename", nullable=False)
        batch_op.alter_column("base_url", nullable=False)

    # 3. Restore unique constraint on image_filename
    op.create_unique_constraint(
        "bluefield_software_images_image_filename_key",
        "bluefield_software_images",
        ["image_filename"],
    )
