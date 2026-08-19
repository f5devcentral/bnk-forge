"""Unit tests for maintenance mode."""
import json
from unittest.mock import MagicMock, patch

import pytest

from core.maintenance import (
    MAINTENANCE_KEY,
    MAINTENANCE_TTL_SECONDS,
    clear_maintenance_mode,
    get_maintenance_status,
    is_maintenance_mode,
    set_maintenance_mode,
)


class TestMaintenanceMode:
    """Tests for maintenance mode functions."""

    @pytest.fixture
    def mock_redis(self):
        """Mock Redis connection."""
        with patch("core.maintenance._get_redis") as mock:
            redis_mock = MagicMock()
            mock.return_value = redis_mock
            yield redis_mock

    @pytest.mark.unit
    def test_set_maintenance_mode(self, mock_redis):
        """set_maintenance_mode stores data in Redis with TTL."""
        set_maintenance_mode("Test maintenance")

        mock_redis.setex.assert_called_once()
        args = mock_redis.setex.call_args
        assert args[0][0] == MAINTENANCE_KEY
        assert args[0][1] == MAINTENANCE_TTL_SECONDS
        # Third arg is JSON string
        data = json.loads(args[0][2])
        assert data["message"] == "Test maintenance"
        assert "started_at" in data

    @pytest.mark.unit
    def test_set_maintenance_mode_default_message(self, mock_redis):
        """set_maintenance_mode uses default message when none provided."""
        set_maintenance_mode()

        mock_redis.setex.assert_called_once()
        args = mock_redis.setex.call_args
        data = json.loads(args[0][2])
        assert data["message"] == "System maintenance in progress"

    @pytest.mark.unit
    def test_clear_maintenance_mode(self, mock_redis):
        """clear_maintenance_mode deletes the Redis key."""
        clear_maintenance_mode()
        mock_redis.delete.assert_called_once_with(MAINTENANCE_KEY)

    @pytest.mark.unit
    def test_get_maintenance_status_when_active(self, mock_redis):
        """get_maintenance_status returns dict when maintenance is active."""
        mock_redis.get.return_value = json.dumps({
            "message": "Restoring backup",
            "started_at": "2026-04-13T10:00:00Z",
        })

        status = get_maintenance_status()

        assert status is not None
        assert status["message"] == "Restoring backup"
        assert status["started_at"] == "2026-04-13T10:00:00Z"

    @pytest.mark.unit
    def test_get_maintenance_status_when_inactive(self, mock_redis):
        """get_maintenance_status returns None when not in maintenance."""
        mock_redis.get.return_value = None

        status = get_maintenance_status()

        assert status is None

    @pytest.mark.unit
    def test_is_maintenance_mode_true(self, mock_redis):
        """is_maintenance_mode returns True when flag is set."""
        mock_redis.get.return_value = json.dumps({"message": "test", "started_at": "2026-04-13T10:00:00Z"})

        assert is_maintenance_mode() is True

    @pytest.mark.unit
    def test_is_maintenance_mode_false(self, mock_redis):
        """is_maintenance_mode returns False when flag is not set."""
        mock_redis.get.return_value = None

        assert is_maintenance_mode() is False

    @pytest.mark.unit
    def test_get_redis_raises_when_no_redis_url(self):
        """_get_redis raises RuntimeError when REDIS_URL is not configured."""
        from core.maintenance import _get_redis

        with patch("core.maintenance.settings") as mock_settings:
            mock_settings.REDIS_URL = None
            with pytest.raises(RuntimeError, match="REDIS_URL not configured"):
                _get_redis()

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "raw",
        [
            "not json at all",
            "{unclosed",
            b"\x80\x81",
            MagicMock(),
        ],
        ids=["plain-text", "truncated-json", "invalid-bytes", "non-str-object"],
    )
    def test_get_maintenance_status_survives_an_unparseable_value(self, mock_redis, raw):
        """A value json.loads() cannot read degrades to "not in maintenance".

        get_maintenance_status() is called from maintenance_middleware on every
        request, so an exception escaping here is a 500 for the entire API --
        not just for the maintenance endpoints. Whatever is under the key, the
        answer must be None rather than a raise.
        """
        mock_redis.get.return_value = raw

        assert get_maintenance_status() is None
        assert is_maintenance_mode() is False
