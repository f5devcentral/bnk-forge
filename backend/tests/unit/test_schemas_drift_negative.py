"""
CT-035: Negative schema tests for Drift request schemas.

Tests that DriftSettingsRequest and TriggerDriftCheckRequest reject
wrong payload shapes with ValidationError.
"""

import pytest
from pydantic import ValidationError

from schemas.drift import DriftSettingsRequest, TriggerDriftCheckRequest


class TestDriftSettingsRequestNegative:
    def test_valid_defaults(self):
        req = DriftSettingsRequest()
        assert req.enabled is False
        assert req.schedule_type == "cron"
        assert req.schedule_value == "0 2 * * *"
        assert req.check_all_modules is True
        assert req.notify_on_drift is True
        assert req.ignore_insignificant_changes is False

    def test_full_settings(self):
        req = DriftSettingsRequest(
            enabled=True,
            schedule_type="interval",
            schedule_value="30m",
            check_all_modules=False,
            module_ids=[1, 2, 3],
            notify_on_drift=True,
            notification_channels=["slack", "email"],
            notification_config={"slack_webhook": "https://hooks.slack.com/..."},
            ignore_insignificant_changes=True,
            ignore_patterns=["*.tfstate.backup"],
        )
        assert req.enabled is True
        assert req.module_ids == [1, 2, 3]
        assert len(req.notification_channels) == 2

    def test_enabled_wrong_type_rejected(self):
        """Pydantic v2 lax mode coerces truthy strings to bool, but dicts should fail."""
        with pytest.raises(ValidationError):
            DriftSettingsRequest(enabled={"value": True})  # type: ignore[arg-type]

    def test_schedule_type_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            DriftSettingsRequest(schedule_type=123)  # type: ignore[arg-type]

    def test_module_ids_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            DriftSettingsRequest(module_ids="not-a-list")  # type: ignore[arg-type]

    def test_module_ids_wrong_element_type_rejected(self):
        with pytest.raises(ValidationError):
            DriftSettingsRequest(module_ids=["one", "two"])  # type: ignore[list-item]

    def test_notification_channels_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            DriftSettingsRequest(notification_channels=123)  # type: ignore[arg-type]

    def test_notification_config_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            DriftSettingsRequest(notification_config="not-a-dict")  # type: ignore[arg-type]

    def test_ignore_patterns_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            DriftSettingsRequest(ignore_patterns=123)  # type: ignore[arg-type]


class TestTriggerDriftCheckRequestNegative:
    def test_valid_defaults(self):
        req = TriggerDriftCheckRequest()
        assert req.module_ids is None

    def test_with_module_ids_valid(self):
        req = TriggerDriftCheckRequest(module_ids=[1, 5, 10])
        assert req.module_ids == [1, 5, 10]

    def test_module_ids_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            TriggerDriftCheckRequest(module_ids="not-a-list")  # type: ignore[arg-type]

    def test_module_ids_wrong_element_type_rejected(self):
        with pytest.raises(ValidationError):
            TriggerDriftCheckRequest(module_ids=["one"])  # type: ignore[list-item]
