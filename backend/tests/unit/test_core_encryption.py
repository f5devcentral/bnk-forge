"""
BU-002: Unit tests for core.encryption module.

Tests encrypt/decrypt roundtrip, edge cases, error handling,
and the decrypt_value_or_none fallback.
"""

import pytest

from core.encryption import (
    decrypt_value,
    decrypt_value_or_none,
    encrypt_value,
)
from core.errors import DecryptionError


class TestEncryptDecryptRoundtrip:
    def test_basic_roundtrip(self):
        """Encrypt then decrypt returns original value."""
        original = "super-secret-token-12345"
        encrypted = encrypt_value(original)
        assert encrypted != original  # actually encrypted
        assert decrypt_value(encrypted) == original

    def test_unicode_roundtrip(self):
        """Unicode strings survive encrypt/decrypt."""
        original = "p\u00e4ssw\u00f6rd-\u00fc\u00f1\u00eec\u00f8d\u00e9"
        encrypted = encrypt_value(original)
        assert decrypt_value(encrypted) == original

    def test_long_string_roundtrip(self):
        """Long strings (e.g., kubeconfig YAML) survive roundtrip."""
        original = "a" * 10000
        encrypted = encrypt_value(original)
        assert decrypt_value(encrypted) == original

    def test_json_roundtrip(self):
        """JSON-formatted strings survive roundtrip."""
        import json
        original = json.dumps({"key": "value", "nested": {"a": [1, 2, 3]}})
        encrypted = encrypt_value(original)
        assert decrypt_value(encrypted) == original


class TestEncryptEdgeCases:
    def test_encrypt_none_returns_none(self):
        assert encrypt_value(None) is None

    def test_encrypt_empty_string_returns_none(self):
        assert encrypt_value("") is None

    def test_encrypted_value_is_different_each_time(self):
        """Fernet uses a random IV, so encrypting the same value twice yields different ciphertexts."""
        a = encrypt_value("same")
        b = encrypt_value("same")
        assert a != b
        # But both decrypt to the same value
        assert decrypt_value(a) == decrypt_value(b) == "same"


class TestDecryptEdgeCases:
    def test_decrypt_none_returns_none(self):
        assert decrypt_value(None) is None

    def test_decrypt_empty_string_returns_none(self):
        assert decrypt_value("") is None

    def test_decrypt_garbage_raises_decryption_error(self):
        with pytest.raises(DecryptionError) as exc_info:
            decrypt_value("not-valid-encrypted-data")
        assert exc_info.value.code == "DECRYPTION_ERROR"

    def test_decrypt_wrong_key_raises_decryption_error(self):
        """Encrypting with one key and decrypting with another should fail."""
        from cryptography.fernet import Fernet

        other_key = Fernet.generate_key()
        other_cipher = Fernet(other_key)
        encrypted_with_other = other_cipher.encrypt(b"secret").decode()

        with pytest.raises(DecryptionError):
            decrypt_value(encrypted_with_other)


class TestEncryptionKeyIsTheOperatorEnvKey:
    """bonnyr-f5 #193 B-3, asserted on the CONSUMER (core.encryption), not the config
    flag: when the operator sets ENCRYPTION_KEY, core.config validates it and writes
    it to the at-rest key file, so get_encryption_key() — what actually encrypts
    stored secrets — returns THAT value. Previously ENCRYPTION_KEY gated
    validate_production while a DIFFERENT, auto-generated file key did the encrypting."""

    def test_operator_env_key_becomes_the_at_rest_key(self, tmp_path, monkeypatch):
        from cryptography.fernet import Fernet

        import core.encryption as enc
        from core import config as config_mod

        opkey = Fernet.generate_key().decode()
        key_file = str(tmp_path / "encryption.key")
        monkeypatch.setattr(config_mod, "_KEYS_DIR", str(tmp_path))
        monkeypatch.setenv("ENCRYPTION_KEY_FILE", key_file)
        # config validates + writes the env key to the at-rest file.
        config_mod.Settings(ENVIRONMENT="production", ENCRYPTION_KEY=opkey)
        # The SECOND consumer (this module) loads exactly that key from the file.
        monkeypatch.setattr(enc, "ENCRYPTION_KEY_FILE", key_file)
        assert enc.get_encryption_key().decode() == opkey
        # And it genuinely encrypts/decrypts with it.
        cipher = Fernet(enc.get_encryption_key())
        assert cipher.decrypt(cipher.encrypt(b"bigip-admin-password")) == b"bigip-admin-password"


class TestDecryptValueOrNone:
    def test_returns_none_on_failure(self):
        """decrypt_value_or_none returns None instead of raising."""
        result = decrypt_value_or_none("garbage-data")
        assert result is None

    def test_returns_value_on_success(self):
        encrypted = encrypt_value("hello")
        assert decrypt_value_or_none(encrypted) == "hello"

    def test_none_input_returns_none(self):
        assert decrypt_value_or_none(None) is None

    def test_empty_input_returns_none(self):
        assert decrypt_value_or_none("") is None
