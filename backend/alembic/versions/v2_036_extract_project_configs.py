"""Extract encryption, backend, and infra configs from projects table.

Creates 3 new 1:1 config tables and migrates existing data from projects:
- project_encryption_configs (IMP-014): 10 encryption columns
- project_backend_configs (IMP-015): 5 S3 backend columns
- project_infra_configs (IMP-016): 8 on-prem SSH columns

Columns are NOT dropped from the projects table in this migration to
allow for a gradual transition. A follow-up migration can drop them
once all service code reads from the new tables.

Revision ID: v2_036
Revises: v2_035
Create Date: 2026-02-22
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "v2_036"
down_revision = "v2_035"
branch_labels = None
depends_on = None


def upgrade():
    # --- IMP-014: project_encryption_configs ---
    op.create_table(
        "project_encryption_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("state_encryption_enabled", sa.Boolean(), server_default="1"),
        sa.Column("encryption_provider", sa.String(50), server_default="pbkdf2"),
        sa.Column("encryption_passphrase_encrypted", sa.Text(), nullable=True),
        sa.Column("encryption_kms_key_id", sa.String(500), nullable=True),
        sa.Column("encryption_kms_region", sa.String(50), nullable=True),
        sa.Column("encryption_vault_url", sa.String(500), nullable=True),
        sa.Column("encryption_vault_key_name", sa.String(255), nullable=True),
        sa.Column("encryption_openbao_address", sa.String(500), nullable=True),
        sa.Column("encryption_openbao_token_encrypted", sa.Text(), nullable=True),
        sa.Column("encryption_openbao_transit_key", sa.String(255), nullable=True),
    )

    # --- IMP-015: project_backend_configs ---
    op.create_table(
        "project_backend_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("s3_state_bucket", sa.String(255), nullable=True),
        sa.Column("s3_state_key_prefix", sa.String(255), nullable=True),
        sa.Column("s3_state_region", sa.String(50), nullable=True),
        sa.Column("s3_use_native_locking", sa.Boolean(), server_default="1"),
        sa.Column("s3_dynamodb_table", sa.String(255), nullable=True),
    )

    # --- IMP-016: project_infra_configs ---
    op.create_table(
        "project_infra_configs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("infra_enabled", sa.Boolean(), server_default="0"),
        sa.Column("infra_host", sa.String(500), nullable=True),
        sa.Column("infra_ssh_port", sa.Integer(), server_default="22"),
        sa.Column("infra_ssh_username", sa.String(255), nullable=True),
        sa.Column("infra_ssh_password_encrypted", sa.String(500), nullable=True),
        sa.Column("infra_ssh_key_encrypted", sa.Text(), nullable=True),
        sa.Column("infra_auth_type", sa.String(20), server_default="password"),
        sa.Column("infra_os_type", sa.String(50), nullable=True),
    )

    # --- Data migration: copy existing data from projects into new tables ---
    conn = op.get_bind()

    # Migrate encryption configs
    conn.execute(sa.text("""
        INSERT INTO project_encryption_configs (
            project_id, state_encryption_enabled, encryption_provider,
            encryption_passphrase_encrypted, encryption_kms_key_id,
            encryption_kms_region, encryption_vault_url, encryption_vault_key_name,
            encryption_openbao_address, encryption_openbao_token_encrypted,
            encryption_openbao_transit_key
        )
        SELECT
            id, state_encryption_enabled, encryption_provider,
            encryption_passphrase_encrypted, encryption_kms_key_id,
            encryption_kms_region, encryption_vault_url, encryption_vault_key_name,
            encryption_openbao_address, encryption_openbao_token_encrypted,
            encryption_openbao_transit_key
        FROM projects
    """))

    # Migrate backend configs (only for projects that have S3 backend data)
    conn.execute(sa.text("""
        INSERT INTO project_backend_configs (
            project_id, s3_state_bucket, s3_state_key_prefix,
            s3_state_region, s3_use_native_locking, s3_dynamodb_table
        )
        SELECT
            id, s3_state_bucket, s3_state_key_prefix,
            s3_state_region, s3_use_native_locking, s3_dynamodb_table
        FROM projects
    """))

    # Migrate infra configs
    conn.execute(sa.text("""
        INSERT INTO project_infra_configs (
            project_id, infra_enabled, infra_host, infra_ssh_port,
            infra_ssh_username, infra_ssh_password_encrypted,
            infra_ssh_key_encrypted, infra_auth_type, infra_os_type
        )
        SELECT
            id, infra_enabled, infra_host, infra_ssh_port,
            infra_ssh_username, infra_ssh_password_encrypted,
            infra_ssh_key_encrypted, infra_auth_type, infra_os_type
        FROM projects
    """))


def downgrade():
    op.drop_table("project_infra_configs")
    op.drop_table("project_backend_configs")
    op.drop_table("project_encryption_configs")
