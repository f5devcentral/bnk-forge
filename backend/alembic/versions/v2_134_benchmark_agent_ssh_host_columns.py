"""BenchmarkAgent gains project-scoped SSH-host registration columns.

Revision ID: v2_134
Revises: v2_133
Create Date: 2026-06-09

Adds columns needed for Forge-managed remote benchmark agent hosts:
  - project_id        FK → projects (nullable, SET NULL on project delete)
  - host_ip           String(45)  — the SSH target IP (vs ip_address = self-advertised)
  - ssh_credential_id FK → ssh_credentials (nullable, SET NULL on cred delete)
  - ssh_port          Integer, default 22
  - jumphost_chain    JSON  — list of {"ssh_credential_id": N}
  - provision_status  String(50), default 'unprovisioned'
  - provision_message Text
  - readiness         JSON
  - managed           Boolean, default False

All existing rows remain valid: NULLable / defaulted so no backfill needed.
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "v2_134"
down_revision = "v2_133"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("benchmark_agents", sa.Column("project_id", sa.Integer(), nullable=True))
    op.add_column("benchmark_agents", sa.Column("host_ip", sa.String(45), nullable=True))
    op.add_column("benchmark_agents", sa.Column("ssh_credential_id", sa.Integer(), nullable=True))
    op.add_column("benchmark_agents", sa.Column("ssh_port", sa.Integer(), nullable=True, server_default="22"))
    op.add_column("benchmark_agents", sa.Column("jumphost_chain", sa.JSON(), nullable=True))
    op.add_column("benchmark_agents", sa.Column("provision_status", sa.String(50), nullable=True, server_default="unprovisioned"))
    op.add_column("benchmark_agents", sa.Column("provision_message", sa.Text(), nullable=True))
    op.add_column("benchmark_agents", sa.Column("readiness", sa.JSON(), nullable=True))
    op.add_column("benchmark_agents", sa.Column("managed", sa.Boolean(), nullable=False, server_default="false"))

    op.create_foreign_key(
        "fk_benchmark_agents_project_id",
        "benchmark_agents",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_benchmark_agents_ssh_credential_id",
        "benchmark_agents",
        "ssh_credentials",
        ["ssh_credential_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_benchmark_agent_project", "benchmark_agents", ["project_id"])


def downgrade() -> None:
    op.drop_index("idx_benchmark_agent_project", table_name="benchmark_agents")
    op.drop_constraint("fk_benchmark_agents_ssh_credential_id", "benchmark_agents", type_="foreignkey")
    op.drop_constraint("fk_benchmark_agents_project_id", "benchmark_agents", type_="foreignkey")
    op.drop_column("benchmark_agents", "managed")
    op.drop_column("benchmark_agents", "readiness")
    op.drop_column("benchmark_agents", "provision_message")
    op.drop_column("benchmark_agents", "provision_status")
    op.drop_column("benchmark_agents", "jumphost_chain")
    op.drop_column("benchmark_agents", "ssh_port")
    op.drop_column("benchmark_agents", "ssh_credential_id")
    op.drop_column("benchmark_agents", "host_ip")
    op.drop_column("benchmark_agents", "project_id")
