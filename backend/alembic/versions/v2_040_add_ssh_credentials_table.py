"""Add ssh_credentials table and ssh_credential_id FKs on clusters/projects.

Creates the first-class SSHCredential model that replaces the SSH-specific
columns previously bolted onto CloudCredentialTemplate. SSH is an access
method, not a cloud provider — this migration makes that distinction real.

Also adds ssh_credential_id FK to kubernetes_clusters and projects tables.
Existing data in cloud_credential_templates with provider='ssh' is migrated
into the new ssh_credentials table, and referencing clusters/projects are
updated to point to the new rows.

Revision ID: v2_040
Revises: v2_039
"""
import sqlalchemy as sa

from alembic import op

revision = "v2_040"
down_revision = "v2_039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create ssh_credentials table
    op.create_table(
        "ssh_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("auth_type", sa.String(50), nullable=False, server_default="key"),
        sa.Column("password_encrypted", sa.Text(), nullable=True),
        sa.Column("private_key_encrypted", sa.Text(), nullable=True),
        sa.Column("key_passphrase_encrypted", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_by", sa.String(255), nullable=True),
    )

    # 2. Add ssh_credential_id to kubernetes_clusters
    op.add_column(
        "kubernetes_clusters",
        sa.Column("ssh_credential_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_cluster_ssh_credential",
        "kubernetes_clusters",
        "ssh_credentials",
        ["ssh_credential_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 3. Add ssh_credential_id to projects
    op.add_column(
        "projects",
        sa.Column("ssh_credential_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_ssh_credential",
        "projects",
        "ssh_credentials",
        ["ssh_credential_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 4. Migrate existing SSH credential templates into the new table.
    # This is a data migration — copy rows from cloud_credential_templates
    # where provider='ssh' into ssh_credentials, then update FKs.
    conn = op.get_bind()

    # Find all SSH-type credential templates
    ssh_templates = conn.execute(
        sa.text(
            "SELECT id, name, description, ssh_host, ssh_port, ssh_username, "
            "ssh_auth_type, ssh_password_encrypted, ssh_key_encrypted, "
            "ssh_key_passphrase_encrypted, is_default, created_at, updated_at, created_by "
            "FROM cloud_credential_templates WHERE provider = 'ssh'"
        )
    ).fetchall()

    for tpl in ssh_templates:
        old_id = tpl[0]
        # Insert into ssh_credentials
        result = conn.execute(
            sa.text(
                "INSERT INTO ssh_credentials "
                "(name, description, host, port, username, auth_type, "
                "password_encrypted, private_key_encrypted, key_passphrase_encrypted, "
                "is_default, created_at, updated_at, created_by) "
                "VALUES (:name, :desc, :host, :port, :username, :auth_type, "
                ":password, :key, :passphrase, :is_default, :created_at, :updated_at, :created_by)"
            ),
            {
                "name": tpl[1],
                "desc": tpl[2],
                "host": tpl[3] or "localhost",
                "port": tpl[4] or 22,
                "username": tpl[5] or "root",
                "auth_type": tpl[6] or "key",
                "password": tpl[7],
                "key": tpl[8],
                "passphrase": tpl[9],
                "is_default": tpl[10] or False,
                "created_at": tpl[11],
                "updated_at": tpl[12],
                "created_by": tpl[13],
            },
        )
        new_id = result.lastrowid

        # Update clusters that referenced this old template
        conn.execute(
            sa.text(
                "UPDATE kubernetes_clusters SET ssh_credential_id = :new_id "
                "WHERE ssh_credential_template_id = :old_id"
            ),
            {"new_id": new_id, "old_id": old_id},
        )

        # Update projects that referenced this old template (provider='ssh')
        conn.execute(
            sa.text(
                "UPDATE projects SET ssh_credential_id = :new_id "
                "WHERE credential_template_id = :old_id"
            ),
            {"new_id": new_id, "old_id": old_id},
        )


def downgrade() -> None:
    # Drop FKs and columns, then drop table
    op.drop_constraint("fk_project_ssh_credential", "projects", type_="foreignkey")
    op.drop_column("projects", "ssh_credential_id")

    op.drop_constraint("fk_cluster_ssh_credential", "kubernetes_clusters", type_="foreignkey")
    op.drop_column("kubernetes_clusters", "ssh_credential_id")

    op.drop_table("ssh_credentials")
