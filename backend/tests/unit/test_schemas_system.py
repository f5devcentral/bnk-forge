"""
CT-030: Negative schema tests for system domain.

Tests that SettingsBatchUpdate and ConfigImportRequest reject wrong payload
shapes with ValidationError. The backend is the last line of defense.
"""

import pytest
from pydantic import ValidationError

from schemas.system import (
    ConfigImportRequest,
    SettingEntry,
    SettingsBatchUpdate,
    SettingsResponse,
    VersionResponse,
)


class TestSettingsBatchUpdateNegative:
    """The bug that started the contract1 sprint: PUT /api/settings."""

    def test_valid_batch_update(self):
        req = SettingsBatchUpdate(settings={"site_name": "BNK-Forge"})
        assert req.settings["site_name"] == "BNK-Forge"

    def test_missing_settings_wrapper_rejected(self):
        """Frontend used to send { "site_name": "x" } instead of { "settings": {...} }."""
        with pytest.raises(ValidationError):
            SettingsBatchUpdate(site_name="x")  # type: ignore[call-arg]

    def test_empty_body_rejected(self):
        """Empty dict → missing required 'settings' field."""
        with pytest.raises(ValidationError):
            SettingsBatchUpdate()  # type: ignore[call-arg]

    def test_settings_wrong_type_rejected(self):
        """settings: 123 → wrong type (should be dict)."""
        with pytest.raises(ValidationError):
            SettingsBatchUpdate(settings=123)  # type: ignore[arg-type]

    def test_settings_as_list_rejected(self):
        """settings: [...] → wrong type."""
        with pytest.raises(ValidationError):
            SettingsBatchUpdate(settings=["a", "b"])  # type: ignore[arg-type]

    def test_settings_as_string_rejected(self):
        """settings: "key=value" → wrong type."""
        with pytest.raises(ValidationError):
            SettingsBatchUpdate(settings="key=value")  # type: ignore[arg-type]

    def test_valid_empty_settings_accepted(self):
        """Empty settings dict is technically valid."""
        req = SettingsBatchUpdate(settings={})
        assert req.settings == {}

    def test_settings_with_non_string_value_rejected(self):
        """Pydantic v2 strict: int is NOT coerced to str for dict[str, str]."""
        with pytest.raises(ValidationError):
            SettingsBatchUpdate(settings={"key": 123})  # type: ignore[dict-item]


class TestConfigImportRequestNegative:
    def test_valid_import(self):
        req = ConfigImportRequest(config={"policies": []})
        assert "policies" in req.config

    def test_missing_config_rejected(self):
        with pytest.raises(ValidationError):
            ConfigImportRequest()  # type: ignore[call-arg]

    def test_config_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            ConfigImportRequest(config="not a dict")  # type: ignore[arg-type]

    def test_config_as_list_rejected(self):
        with pytest.raises(ValidationError):
            ConfigImportRequest(config=[1, 2, 3])  # type: ignore[arg-type]


class TestVersionResponse:
    def test_minimal(self):
        response = VersionResponse(
            current_version="1.2.3",
            latest_version="1.2.4",
            update_available=True,
            commits_behind=1,
        )
        assert response.current_version == "1.2.3"
        assert response.update_available is True


class TestSettingsResponse:
    def test_with_categories(self):
        response = SettingsResponse(
            settings={
                "general": [
                    SettingEntry(
                        key="general.site_name",
                        value="BNK Forge",
                        value_type="string",
                        description="Site display name",
                        is_encrypted=False,
                    )
                ],
                "aws": [
                    SettingEntry(
                        key="aws.access_key_id",
                        value="AKIA...",
                        value_type="string",
                        description="AWS key",
                        is_encrypted=True,
                    )
                ],
            }
        )

        assert "general" in response.settings
        assert response.settings["general"][0].key == "general.site_name"
        assert response.settings["aws"][0].is_encrypted is True
