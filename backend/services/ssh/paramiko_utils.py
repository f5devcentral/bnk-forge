"""Shared paramiko primitives for SSH key loading and credential handling.

This module consolidates duplicated SSH key-loading logic from:
- services/bare_metal/ssh_session.py
- services/ssh_tunnel_manager.py
- services/ssh_service.py
- services/discovery_service.py

Key try-order is standardized: Ed25519 → RSA → ECDSA (modern → legacy).
"""

import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import paramiko

if TYPE_CHECKING:
    from models.ssh_credential import SSHCredential

logger = logging.getLogger(__name__)


def load_private_key_from_content(
    content: str,
    passphrase: str | None = None,
) -> paramiko.PKey:
    """Load a private key from string content (PEM/OpenSSH format).

    Args:
        content: Private key content as string
        passphrase: Optional passphrase for encrypted keys

    Returns:
        paramiko.PKey instance

    Raises:
        paramiko.SSHException: If key cannot be loaded as any supported type

    Try-order: Ed25519 → RSA → ECDSA (preferred → legacy)
    """
    key_file = io.StringIO(content)

    # Try Ed25519 first (modern, preferred)
    try:
        key_file.seek(0)
        return paramiko.Ed25519Key.from_private_key(key_file, password=passphrase)
    except (paramiko.SSHException, ValueError):
        pass

    # Try RSA (common, legacy)
    try:
        key_file.seek(0)
        return paramiko.RSAKey.from_private_key(key_file, password=passphrase)
    except (paramiko.SSHException, ValueError):
        pass

    # Try ECDSA (less common)
    try:
        key_file.seek(0)
        return paramiko.ECDSAKey.from_private_key(key_file, password=passphrase)
    except (paramiko.SSHException, ValueError):
        pass

    raise paramiko.SSHException(
        "Unable to load private key: not a valid Ed25519, RSA, or ECDSA key"
    )


def load_private_key_from_file(
    path: str | Path,
    passphrase: str | None = None,
) -> paramiko.PKey:
    """Load a private key from a file path.

    Args:
        path: Path to private key file
        passphrase: Optional passphrase for encrypted keys

    Returns:
        paramiko.PKey instance

    Raises:
        paramiko.SSHException: If key cannot be loaded as any supported type
        FileNotFoundError: If key file doesn't exist

    Try-order: Ed25519 → RSA → ECDSA (preferred → legacy)
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Private key file not found: {path}")

    password = passphrase if passphrase else None

    # Try Ed25519 first (modern, preferred)
    try:
        return paramiko.Ed25519Key.from_private_key_file(str(path), password=password)
    except (paramiko.SSHException, ValueError):
        pass

    # Try RSA (common, legacy)
    try:
        return paramiko.RSAKey.from_private_key_file(str(path), password=password)
    except (paramiko.SSHException, ValueError):
        pass

    # Try ECDSA (less common)
    try:
        return paramiko.ECDSAKey.from_private_key_file(str(path), password=password)
    except (paramiko.SSHException, ValueError):
        pass

    raise paramiko.SSHException(
        f"Unable to load private key from {path}: not a valid Ed25519, RSA, or ECDSA key"
    )


def decrypt_ssh_credential(credential: "SSHCredential") -> dict:
    """Decrypt an SSHCredential into a connection-ready dict.

    Args:
        credential: SSHCredential ORM model instance

    Returns:
        Dict with keys:
        - username: str
        - port: int
        - host: str | None (from credential, may be None if caller provides host separately)
        - auth_type: "key" | "password"
        - password: str | None (decrypted)
        - private_key_content: str | None (decrypted key string)
        - key_passphrase: str | None (decrypted)

    Preference: key auth over password when both are present.
    """
    from core.encryption import decrypt_value

    result = {
        "username": credential.username,
        "port": credential.port or 22,
        "host": credential.host,
        "auth_type": credential.auth_type or "key",
        "password": None,
        "private_key_content": None,
        "key_passphrase": None,
    }

    # Decrypt password if present
    if credential.password_encrypted:
        try:
            result["password"] = decrypt_value(str(credential.password_encrypted))
        except Exception as e:
            logger.warning("Failed to decrypt SSH credential password: %s", e)

    # Decrypt private key if present
    if credential.private_key_encrypted:
        try:
            result["private_key_content"] = decrypt_value(str(credential.private_key_encrypted))
        except Exception as e:
            logger.warning("Failed to decrypt SSH credential private key: %s", e)

    # Decrypt key passphrase if present
    if credential.key_passphrase_encrypted:
        try:
            result["key_passphrase"] = decrypt_value(str(credential.key_passphrase_encrypted))
        except Exception as e:
            logger.warning("Failed to decrypt SSH credential key passphrase: %s", e)

    # Determine effective auth type
    if result["private_key_content"]:
        result["auth_type"] = "key"
    elif result["password"]:
        result["auth_type"] = "password"

    return result
