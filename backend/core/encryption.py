"""
Encryption utilities for sensitive data
Handles encryption/decryption of auth tokens, credentials, and other secrets
"""
import base64
import hashlib
import logging
import os

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# Encryption key file path from environment or default.
#
# bonnyr-f5 #193 B-3: the FILE at ENCRYPTION_KEY_FILE (default
# $KEYS_DIR/encryption.key, on the persistent keys volume) is the ONE source of
# truth for the at-rest Fernet key actually used to encrypt/decrypt stored secrets.
# There is no longer a shadow: core.config resolves this SAME path
# (``_encryption_key_path()``) and, when the operator sets ``ENCRYPTION_KEY``,
# VALIDATES it as a real Fernet key and WRITES it here (with a ``.operator``
# provenance marker) before this module loads — so setting ``ENCRYPTION_KEY`` now
# genuinely becomes the at-rest key that get_encryption_key() returns and that
# validate_production's fail-fast gates on. When ``ENCRYPTION_KEY`` is unset,
# core.config generates+persists a Fernet key here on first boot. Either way, one
# key, one generator, one provenance signal. Resolution mirrors
# core.config._encryption_key_path() so both modules name the identical file.
ENCRYPTION_KEY_FILE = os.getenv("ENCRYPTION_KEY_FILE") or os.path.join(
    os.environ.get("KEYS_DIR", "/app/keys"), "encryption.key"
)


def get_encryption_key() -> bytes:
    """Get or create persistent encryption key.

    Tries to read from ENCRYPTION_KEY_FILE, or creates a new key if not found.
    Falls back to in-memory key generation if file operations fail (e.g., permissions).

    The file is the single source of truth for the at-rest key; when the operator
    sets ``ENCRYPTION_KEY``, core.config has already validated it and written it to
    this file, so this returns THAT value — see the module note above
    ENCRYPTION_KEY_FILE.
    """
    # Try to read existing key
    if os.path.exists(ENCRYPTION_KEY_FILE):
        try:
            with open(ENCRYPTION_KEY_FILE, 'rb') as f:
                key = f.read().strip()
                if key and len(key) >= 32:  # Valid Fernet key is 44 bytes base64
                    return key
                logger.warning(f"Invalid key in {ENCRYPTION_KEY_FILE}, regenerating")
        except PermissionError as e:
            logger.warning(f"Could not read encryption key from {ENCRYPTION_KEY_FILE}: {e}")
            logger.warning("Will generate in-memory key (NOT RECOMMENDED for production)")
            logger.warning("Fix with: docker exec -u root bnk-forge-backend chown -R bnkforge:bnkforge /app/keys")
        except Exception as e:
            logger.warning(f"Error reading encryption key: {e}")

    # Generate new key
    key = Fernet.generate_key()

    # Try to persist it
    try:
        os.makedirs(os.path.dirname(ENCRYPTION_KEY_FILE), exist_ok=True)
        with open(ENCRYPTION_KEY_FILE, 'wb') as f:
            f.write(key)
        logger.info(f"Generated new encryption key at {ENCRYPTION_KEY_FILE}")
    except PermissionError as e:
        logger.warning(f"Could not persist encryption key to {ENCRYPTION_KEY_FILE}: {e}")
        logger.warning("Using in-memory key (will change on restart - NOT RECOMMENDED for production)")
        logger.warning("Fix with: docker exec -u root bnk-forge-backend chown -R bnkforge:bnkforge /app/keys")
    except Exception as e:
        logger.warning(f"Could not persist encryption key to {ENCRYPTION_KEY_FILE}: {e}")
        logger.warning("Using in-memory key (will change on restart - NOT RECOMMENDED)")

    return key


# Initialize cipher
FERNET_KEY = get_encryption_key()
fernet_cipher = Fernet(FERNET_KEY)


def encrypt_value(value: str) -> str | None:
    """Encrypt a string value"""
    if not value:
        return None
    return fernet_cipher.encrypt(value.encode()).decode()


def decrypt_value(encrypted_value: str) -> str | None:
    """Decrypt a string value.

    Raises DecryptionError on failure instead of returning None,
    so callers get an explicit error rather than silently passing
    null credentials to downstream services.
    """
    if not encrypted_value:
        return None
    try:
        return fernet_cipher.decrypt(encrypted_value.encode()).decode()
    except Exception as e:
        from core.errors import DecryptionError
        logger.error(f"Failed to decrypt value: {e}")
        raise DecryptionError(
            message=f"Failed to decrypt value: {e}",
            details={"error_type": type(e).__name__},
        )


def decrypt_value_or_none(encrypted_value: str) -> str | None:
    """Decrypt a string value, returning None on failure.

    Use this ONLY when graceful degradation is acceptable
    (e.g., migration scripts, optional fields).
    Prefer decrypt_value() for credential paths.
    """
    if not encrypted_value:
        return None
    try:
        return fernet_cipher.decrypt(encrypted_value.encode()).decode()
    except Exception as e:
        logger.warning(f"Failed to decrypt value (returning None): {e}")
        return None


def wrap_fernet_key(key: bytes, passphrase: str) -> dict[str, str]:
    """
    Wrap a Fernet key with a passphrase for secure storage.

    Uses PBKDF2-HMAC-SHA256 (100k iterations) for key derivation,
    then AES-256-GCM for encryption.

    Args:
        key: The raw Fernet key bytes (44 bytes, base64-encoded)
        passphrase: User-provided passphrase (should be 12+ chars)

    Returns:
        dict with {salt, nonce, ciphertext} — all base64-encoded
    """
    # Generate random salt for PBKDF2
    salt = os.urandom(16)

    # Derive 256-bit key from passphrase
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        iterations=100_000,
        dklen=32,
    )

    # Encrypt the Fernet key with AES-256-GCM
    aesgcm = AESGCM(derived_key)
    nonce = os.urandom(12)  # 96-bit nonce for GCM
    ciphertext = aesgcm.encrypt(nonce, key, None)

    # Return all components base64-encoded
    return {
        "salt": base64.b64encode(salt).decode("utf-8"),
        "nonce": base64.b64encode(nonce).decode("utf-8"),
        "ciphertext": base64.b64encode(ciphertext).decode("utf-8"),
    }


def unwrap_fernet_key(wrapped: dict[str, str], passphrase: str) -> bytes:
    """
    Unwrap a Fernet key using the passphrase.

    Args:
        wrapped: dict from wrap_fernet_key() with {salt, nonce, ciphertext}
        passphrase: The same passphrase used during wrapping

    Returns:
        The original Fernet key bytes

    Raises:
        ValueError: If passphrase is incorrect or data is corrupted
    """
    try:
        salt = base64.b64decode(wrapped["salt"])
        nonce = base64.b64decode(wrapped["nonce"])
        ciphertext = base64.b64decode(wrapped["ciphertext"])
    except (KeyError, ValueError) as e:
        raise ValueError(f"Invalid wrapped key format: {e}") from e

    # Derive key from passphrase using same parameters
    derived_key = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        iterations=100_000,
        dklen=32,
    )

    # Decrypt
    try:
        aesgcm = AESGCM(derived_key)
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        raise ValueError("Invalid passphrase or corrupted data") from e
