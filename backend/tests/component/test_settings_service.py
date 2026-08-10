"""
Tests for services.settings_service — encrypted setting key constants.

BC-008: Settings service — validate the ENCRYPTED_SETTING_KEYS set.
"""

import pytest

from services.settings_service import ENCRYPTED_SETTING_KEYS

# ---------------------------------------------------------------------------
# ENCRYPTED_SETTING_KEYS
# ---------------------------------------------------------------------------


class TestEncryptedSettingKeys:
    """Validate the set of keys that require encryption at rest."""

    def test_set_is_non_empty(self):
        assert len(ENCRYPTED_SETTING_KEYS) > 0

    def test_known_keys_present(self):
        assert "aws.secret_access_key" in ENCRYPTED_SETTING_KEYS
        assert "module_library.git_token" in ENCRYPTED_SETTING_KEYS

    def test_all_entries_are_strings(self):
        for key in ENCRYPTED_SETTING_KEYS:
            assert isinstance(key, str), f"Expected str, got {type(key)} for {key!r}"

    def test_no_empty_strings(self):
        for key in ENCRYPTED_SETTING_KEYS:
            assert key.strip() != "", "ENCRYPTED_SETTING_KEYS contains an empty string"
