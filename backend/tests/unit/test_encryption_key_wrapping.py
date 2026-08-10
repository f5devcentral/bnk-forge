"""Unit tests for Fernet key wrapping/unwrapping."""
import base64

import pytest
from cryptography.fernet import Fernet

from core.encryption import unwrap_fernet_key, wrap_fernet_key


class TestKeyWrapping:
    """Tests for wrap_fernet_key and unwrap_fernet_key."""

    @pytest.mark.unit
    def test_round_trip_succeeds(self):
        """Wrapping then unwrapping with correct passphrase returns original key."""
        original_key = Fernet.generate_key()
        passphrase = "test-passphrase-123"

        wrapped = wrap_fernet_key(original_key, passphrase)
        unwrapped = unwrap_fernet_key(wrapped, passphrase)

        assert unwrapped == original_key

    @pytest.mark.unit
    def test_wrong_passphrase_fails(self):
        """Unwrapping with wrong passphrase raises ValueError."""
        original_key = Fernet.generate_key()
        passphrase = "correct-passphrase"
        wrong_passphrase = "wrong-passphrase"

        wrapped = wrap_fernet_key(original_key, passphrase)

        with pytest.raises(ValueError, match="Invalid passphrase"):
            unwrap_fernet_key(wrapped, wrong_passphrase)

    @pytest.mark.unit
    def test_wrapped_format_has_required_fields(self):
        """Wrapped output contains salt, nonce, and ciphertext."""
        key = Fernet.generate_key()
        passphrase = "test-passphrase"

        wrapped = wrap_fernet_key(key, passphrase)

        assert "salt" in wrapped
        assert "nonce" in wrapped
        assert "ciphertext" in wrapped
        assert len(wrapped) == 3

    @pytest.mark.unit
    def test_different_passphrases_produce_different_output(self):
        """Same key wrapped with different passphrases produces different ciphertext."""
        key = Fernet.generate_key()

        wrapped1 = wrap_fernet_key(key, "passphrase-one")
        wrapped2 = wrap_fernet_key(key, "passphrase-two")

        assert wrapped1["ciphertext"] != wrapped2["ciphertext"]

    @pytest.mark.unit
    def test_same_passphrase_different_salts(self):
        """Same key + passphrase produces different output each time (random salt)."""
        key = Fernet.generate_key()
        passphrase = "same-passphrase"

        wrapped1 = wrap_fernet_key(key, passphrase)
        wrapped2 = wrap_fernet_key(key, passphrase)

        # Salt should be different each time
        assert wrapped1["salt"] != wrapped2["salt"]
        # Both should still unwrap correctly
        assert unwrap_fernet_key(wrapped1, passphrase) == key
        assert unwrap_fernet_key(wrapped2, passphrase) == key

    @pytest.mark.unit
    def test_invalid_wrapped_format_raises(self):
        """Missing fields in wrapped dict raises ValueError."""
        with pytest.raises(ValueError, match="Invalid wrapped key format"):
            unwrap_fernet_key({"salt": "abc"}, "passphrase")  # missing nonce, ciphertext

    @pytest.mark.unit
    def test_corrupted_ciphertext_raises(self):
        """Corrupted ciphertext raises ValueError."""
        key = Fernet.generate_key()
        passphrase = "test-passphrase"

        wrapped = wrap_fernet_key(key, passphrase)
        # Corrupt the ciphertext
        corrupted = base64.b64encode(b"corrupted-data").decode()
        wrapped["ciphertext"] = corrupted

        with pytest.raises(ValueError, match="Invalid passphrase"):
            unwrap_fernet_key(wrapped, passphrase)

    @pytest.mark.unit
    def test_empty_passphrase_works(self):
        """Empty passphrase is technically allowed (not recommended)."""
        key = Fernet.generate_key()
        passphrase = ""

        wrapped = wrap_fernet_key(key, passphrase)
        unwrapped = unwrap_fernet_key(wrapped, passphrase)

        assert unwrapped == key
