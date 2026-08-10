"""Add columns to support public Terraform/OpenTofu registry, public-git, and TFC private module imports.

ModuleLibrary gains source pinning + smoke-test tracking + per-module metadata
overrides. CloudCredentialTemplate gains a TFC API token + hostname so private
Terraform Cloud modules can be imported with token auth.

Revision ID: v2_105
Revises: v2_104
"""

import sqlalchemy as sa

from alembic import op

revision = "v2_105"
down_revision = "v2_104"
branch_labels = None
depends_on = None


_MODULE_LIBRARY_COLUMNS = (
    ("tarball_sha256", sa.Column("tarball_sha256", sa.String(64), nullable=True)),
    ("upstream_source_url", sa.Column("upstream_source_url", sa.String(1000), nullable=True)),
    ("upstream_subpath", sa.Column("upstream_subpath", sa.String(500), nullable=True)),
    ("metadata_overrides", sa.Column("metadata_overrides", sa.JSON(), nullable=True)),
    ("last_smoke_test_status", sa.Column("last_smoke_test_status", sa.String(20), nullable=True)),
    ("last_smoke_test_output", sa.Column("last_smoke_test_output", sa.Text(), nullable=True)),
    ("last_smoke_test_at", sa.Column("last_smoke_test_at", sa.DateTime(timezone=True), nullable=True)),
)

_CREDENTIAL_TEMPLATE_COLUMNS = (
    ("tfc_api_token_encrypted", sa.Column("tfc_api_token_encrypted", sa.Text(), nullable=True)),
    ("tfc_hostname", sa.Column("tfc_hostname", sa.String(255), nullable=True)),
)


def _column_exists(bind, table: str, column: str) -> bool:
    return bind.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).fetchone() is not None


def upgrade() -> None:
    bind = op.get_bind()

    for name, column in _MODULE_LIBRARY_COLUMNS:
        if not _column_exists(bind, "module_library", name):
            op.add_column("module_library", column)

    for name, column in _CREDENTIAL_TEMPLATE_COLUMNS:
        if not _column_exists(bind, "cloud_credential_templates", name):
            op.add_column("cloud_credential_templates", column)


def downgrade() -> None:
    for name, _ in reversed(_CREDENTIAL_TEMPLATE_COLUMNS):
        op.drop_column("cloud_credential_templates", name)
    for name, _ in reversed(_MODULE_LIBRARY_COLUMNS):
        op.drop_column("module_library", name)
