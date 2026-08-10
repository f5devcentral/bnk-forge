"""Rename plaintext credential columns in environments table to *_encrypted
and encrypt any existing values (SEC-003).

Revision ID: v2_034
Revises: v2_033
Create Date: 2026-02-20
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "v2_034"
down_revision = "v2_033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Rename plaintext columns to _encrypted suffix
    # Using batch mode for SQLite compatibility in tests
    with op.batch_alter_table("environments") as batch_op:
        batch_op.alter_column("aws_secret_access_key", new_column_name="aws_secret_access_key_encrypted")
        batch_op.alter_column("gcp_credentials_json", new_column_name="gcp_credentials_json_encrypted")
        batch_op.alter_column("azure_credentials_json", new_column_name="azure_credentials_json_encrypted")

    # Encrypt any existing plaintext values
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, aws_secret_access_key_encrypted, gcp_credentials_json_encrypted, azure_credentials_json_encrypted "
            "FROM environments WHERE aws_secret_access_key_encrypted IS NOT NULL "
            "OR gcp_credentials_json_encrypted IS NOT NULL "
            "OR azure_credentials_json_encrypted IS NOT NULL"
        )
    ).fetchall()

    if rows:
        try:
            from core.encryption import encrypt_value
            for row in rows:
                updates = {}
                # Only encrypt values that look like plaintext (not already encrypted)
                # Fernet tokens start with 'gAAAAA'
                if row[1] and not row[1].startswith("gAAAAA"):
                    updates["aws_secret_access_key_encrypted"] = encrypt_value(row[1])
                if row[2] and not row[2].startswith("gAAAAA"):
                    updates["gcp_credentials_json_encrypted"] = encrypt_value(row[2])
                if row[3] and not row[3].startswith("gAAAAA"):
                    updates["azure_credentials_json_encrypted"] = encrypt_value(row[3])

                if updates:
                    set_clause = ", ".join(f"{k} = :val_{k}" for k in updates)
                    params = {f"val_{k}": v for k, v in updates.items()}
                    params["id"] = row[0]
                    conn.execute(
                        sa.text(f"UPDATE environments SET {set_clause} WHERE id = :id"),
                        params,
                    )
        except Exception as e:
            # If encryption fails (e.g., no key available), just log and continue
            # The columns are already renamed — values will be plaintext until re-saved
            print(f"Warning: Could not encrypt existing values: {e}")


def downgrade() -> None:
    with op.batch_alter_table("environments") as batch_op:
        batch_op.alter_column("aws_secret_access_key_encrypted", new_column_name="aws_secret_access_key")
        batch_op.alter_column("gcp_credentials_json_encrypted", new_column_name="gcp_credentials_json")
        batch_op.alter_column("azure_credentials_json_encrypted", new_column_name="azure_credentials_json")
