"""
Golden contract tests — API domain (version/settings).

Validates that API utility endpoints return responses matching their declared
Pydantic response_model schemas.

Endpoints tested:
- GET /api/version  → VersionResponse
- GET /api/settings → SettingsResponse
"""

from schemas.system import SettingsResponse, VersionResponse


class TestVersionContract:
    """GET /api/system/version returns shape matching VersionResponse."""

    def test_version_response_shape(self, client, admin_headers, sample_user):
        """L1+L2: Response parses through VersionResponse and required fields exist."""
        response = client.get("/api/system/version", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        parsed = VersionResponse.model_validate(data)

        assert isinstance(parsed.current_version, str)
        assert isinstance(parsed.latest_version, str)
        assert isinstance(parsed.update_available, bool)


class TestSettingsContract:
    """GET /api/settings returns shape matching SettingsResponse."""

    def test_settings_response_shape(self, client, admin_headers, sample_user, db):
        """L1+L2: Response parses, grouped categories contain valid setting entries."""
        from models import ApplicationSetting

        db.add_all(
            [
                ApplicationSetting(
                    key="general.site_name",
                    value="BNK Forge",
                    value_type="string",
                    description="Site display name",
                    category="general",
                    is_encrypted=False,
                ),
                ApplicationSetting(
                    key="aws.access_key_id",
                    value="AKIA...",
                    value_type="string",
                    description="AWS access key",
                    category="aws",
                    is_encrypted=True,
                ),
                ApplicationSetting(
                    key="deprecated.old_key",
                    value="legacy",
                    value_type="string",
                    description="Should be filtered",
                    category="deprecated",
                    is_encrypted=False,
                ),
            ]
        )
        db.commit()

        response = client.get("/api/settings", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        parsed = SettingsResponse.model_validate(data)

        assert isinstance(parsed.settings, dict)
        assert "general" in parsed.settings
        assert "aws" in parsed.settings
        assert "deprecated" not in parsed.settings

        general_setting = parsed.settings["general"][0]
        assert general_setting.key == "general.site_name"
        assert general_setting.value == "BNK Forge"
        assert general_setting.is_encrypted is False

        aws_setting = parsed.settings["aws"][0]
        assert aws_setting.key == "aws.access_key_id"
        assert aws_setting.is_encrypted is True
