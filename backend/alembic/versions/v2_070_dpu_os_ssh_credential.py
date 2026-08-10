"""Phase 3 — attach an SSH credential to the project-level DPU OS defaults.

The Flash engine (Phase 4) injects the chosen credential's public key into
the rendered bf.cfg so newly-flashed DPUs trust it immediately, and uses
the matching private key when Forge SSHes into the DPU OS. Picker in the
Project DPU Settings card reuses the cross-project SSHCredential catalog.

Revision ID: v2_070
Revises: v2_069
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_070"
down_revision = "v2_069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "project_dpu_settings",
        sa.Column(
            "default_os_ssh_credential_id",
            sa.Integer(),
            sa.ForeignKey("ssh_credentials.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("project_dpu_settings", "default_os_ssh_credential_id")
