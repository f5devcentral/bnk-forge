"""Encrypt sensitive ApplicationSettings at rest

SEC-005: Credentials stored in ApplicationSettings were plaintext.
This migration encrypts existing values for:
- aws.secret_access_key
- aws.session_token
- module_library.git_token

Revision ID: v2_019_encrypt_app_settings
Revises: v2_018_persistent_workspaces
Create Date: 2026-02-06
"""
import sqlalchemy as sa
from sqlalchemy.orm import Session

from alembic import op

revision = 'v2_019_encrypt_app_settings'
down_revision = 'v2_018_persistent_workspaces'
branch_labels = None
depends_on = None

# Keys that should be encrypted
ENCRYPTED_KEYS = {
    'aws.secret_access_key',
    'aws.session_token',
    'module_library.git_token',
}


def upgrade():
    """Encrypt existing plaintext sensitive settings."""
    # Import encryption after alembic context is ready
    from core.encryption import encrypt_value

    bind = op.get_bind()
    session = Session(bind=bind)

    # Get all settings that should be encrypted
    result = session.execute(
        sa.text("SELECT id, key, value, is_encrypted FROM application_settings WHERE key IN :keys"),
        {"keys": tuple(ENCRYPTED_KEYS)}
    )

    for row in result:
        setting_id, key, value, is_encrypted = row

        # Skip if already encrypted or no value
        if is_encrypted or not value:
            continue

        # Skip if value looks already encrypted (Fernet tokens start with 'gAAAAA')
        if value.startswith('gAAAAA'):
            # Just mark as encrypted
            session.execute(
                sa.text("UPDATE application_settings SET is_encrypted = true WHERE id = :id"),
                {"id": setting_id}
            )
            continue

        # Encrypt the plaintext value
        encrypted_value = encrypt_value(value)
        if encrypted_value:
            session.execute(
                sa.text("UPDATE application_settings SET value = :value, is_encrypted = true WHERE id = :id"),
                {"id": setting_id, "value": encrypted_value}
            )

    session.commit()


def downgrade():
    """Decrypt settings back to plaintext (not recommended for production)."""
    from core.encryption import decrypt_value_or_none

    bind = op.get_bind()
    session = Session(bind=bind)

    # Get all encrypted settings
    result = session.execute(
        sa.text("SELECT id, key, value FROM application_settings WHERE key IN :keys AND is_encrypted = true"),
        {"keys": tuple(ENCRYPTED_KEYS)}
    )

    for row in result:
        setting_id, key, value = row

        if not value:
            continue

        # Decrypt the value
        decrypted_value = decrypt_value_or_none(value)
        if decrypted_value:
            session.execute(
                sa.text("UPDATE application_settings SET value = :value, is_encrypted = false WHERE id = :id"),
                {"id": setting_id, "value": decrypted_value}
            )

    session.commit()
