"""
Conftest for backend unit tests.

Unit tests should NOT use the database or any external services.
They test pure functions, validators, data transformations, and schemas.

Available fixtures:
- tmp_path (built-in pytest) — temporary directory for file operations
- temp_encryption_key — a temporary encryption key file for core.encryption tests
- mock_settings — patched Settings object with test defaults
"""

import os
import tempfile

import pytest


@pytest.fixture()
def temp_encryption_key(tmp_path):
    """Create a temporary encryption key file and set the env var.

    Yields the key file path. Cleans up automatically via tmp_path.
    """
    from core.encryption import generate_key

    key_file = tmp_path / "test-encryption.key"
    key = generate_key()
    key_file.write_bytes(key)

    old_val = os.environ.get("ENCRYPTION_KEY_FILE")
    os.environ["ENCRYPTION_KEY_FILE"] = str(key_file)
    yield str(key_file)
    if old_val is not None:
        os.environ["ENCRYPTION_KEY_FILE"] = old_val
    else:
        os.environ.pop("ENCRYPTION_KEY_FILE", None)


@pytest.fixture()
def mock_settings(monkeypatch):
    """Provide a Settings-like object with safe test defaults.

    Use this when testing functions that read from core.config.settings
    without needing a real database or file system.
    """
    defaults = {
        "DATABASE_URL": "sqlite:///file::memory:",
        "SECRET_KEY": "test-secret-key-for-unit-tests",
        "ENVIRONMENT": "testing",
        "REQUIRE_AUTH": "true",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    yield defaults
