"""DPU inline host-SSH credentials (in-band).

Phase 2.5 of in-band DPU work — lets a DPU carry its own host-side SSH
credentials so we can reach the host without first requiring the
operator to create a saved SSHCredential. The saved-credential FK added
in v2_074 (``host_ssh_credential_id``) stays — inline fields take
precedence, FK is the fallback.

Schema mirrors the existing BMC-credential columns on dpus
(``bmc_username_encrypted`` + ``bmc_password_encrypted``), extended with
port + auth_type + key fields so the same row can handle either a
password- or key-based login on a non-22 port.

Revision ID: v2_075
Revises: v2_074
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_075"
down_revision = "v2_074"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dpus", sa.Column("host_ssh_username", sa.String(length=255), nullable=True))
    op.add_column("dpus", sa.Column("host_ssh_port", sa.Integer(), nullable=True))
    op.add_column("dpus", sa.Column("host_ssh_auth_type", sa.String(length=16), nullable=True))
    op.add_column("dpus", sa.Column("host_ssh_password_encrypted", sa.Text(), nullable=True))
    op.add_column("dpus", sa.Column("host_ssh_private_key_encrypted", sa.Text(), nullable=True))
    op.add_column("dpus", sa.Column("host_ssh_key_passphrase_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dpus", "host_ssh_key_passphrase_encrypted")
    op.drop_column("dpus", "host_ssh_private_key_encrypted")
    op.drop_column("dpus", "host_ssh_password_encrypted")
    op.drop_column("dpus", "host_ssh_auth_type")
    op.drop_column("dpus", "host_ssh_port")
    op.drop_column("dpus", "host_ssh_username")
