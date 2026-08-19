"""Add container_registries table (Container Registries access method).

A first-class OCI registry access method mirroring ssh_credentials: global,
name-unique, multiple entries, encrypted secrets never serialized. Standalone
types (ghcr/quay/far) carry their own secret; derived types (ecr/acr/icr/gar)
reference a cloud_credential_templates row for short-lived token exchange.

Revision ID: v2_138
Revises: v2_137

Idempotent by necessity (INV-7). Fresh installs are provisioned by init_db.py
with ``create_all`` + ``stamp head``, so a stack installed at any release whose
ORM already declared ContainerRegistry has this table ALREADY, while its stamp
sits at v2_137 — below this revision. Upgrading such a stack replays v2_138 and
an unguarded ``create_table`` raises DuplicateTable, which aborts the whole
upgrade and crash-loops the backend. Guarding the create lets those stacks move
through the chain; on a DB that genuinely lacks the table this is unchanged.
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_138"
down_revision = "v2_137"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("container_registries"):
        # Built by create_all at install time; nothing to do. Indexes below are
        # created with if_not_exists so they converge either way.
        _ensure_indexes()
        return

    op.create_table(
        "container_registries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("registry_host", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("token_encrypted", sa.Text(), nullable=True),
        sa.Column("far_service_account_encrypted", sa.Text(), nullable=True),
        sa.Column("credential_template_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("last_test_status", sa.String(32), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["credential_template_id"], ["cloud_credential_templates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    _ensure_indexes()


def _ensure_indexes() -> None:
    op.create_index(
        "ix_container_registries_id", "container_registries", ["id"], if_not_exists=True
    )
    op.create_index(
        "ix_container_registries_name", "container_registries", ["name"], if_not_exists=True
    )


def downgrade() -> None:
    # if_exists: v2_152 drops this index (it is redundant beside the unique
    # constraint), so downgrading past that revision reaches here with it gone.
    op.drop_index(
        "ix_container_registries_name", "container_registries", if_exists=True
    )
    op.drop_index("ix_container_registries_id", "container_registries")
    op.drop_table("container_registries")
