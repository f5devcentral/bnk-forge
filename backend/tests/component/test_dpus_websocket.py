"""Component tests for the DPU WebSocket auth guard — routes.dpus_websocket._validate_ws_token.

bonnyr-f5 #193 (Credential minor): the new must-change / unresolvable-user gate on
the DPU console + BMC/OS SSH websockets had NO test, while its k8s twin
(routes.k8s_websocket._validate_ws_token, covered in test_k8s_websocket.py) did.
These mirror the twin so a seed-credential admin can never reach a DPU shell while
REST refuses /api/auth/users, and a token whose user can't be resolved fails CLOSED.
"""

from unittest.mock import AsyncMock, patch

import pytest


class TestDpusValidateWsToken:
    """Mirror of TestValidateWsToken in test_k8s_websocket.py, for the DPU guard."""

    @pytest.mark.asyncio
    @patch("core.config.settings")
    async def test_auth_disabled_returns_true(self, mock_settings):
        mock_settings.REQUIRE_AUTH = False
        from routes.dpus_websocket import _validate_ws_token

        ws = AsyncMock()
        assert await _validate_ws_token(ws, None) is True
        ws.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_token_closes_4401(self):
        from routes.dpus_websocket import _validate_ws_token

        ws = AsyncMock()
        assert await _validate_ws_token(ws, None) is False
        ws.close.assert_awaited_once()
        assert ws.close.call_args[1]["code"] == 4401

    @pytest.mark.asyncio
    async def test_valid_admin_token_returns_true(self, db):
        from routes.dpus_websocket import _validate_ws_token
        from services.auth_service import create_access_token, create_user

        create_user(db, "dpuadmin", "dpuadmin@t.com", "pw", role="admin", must_change_password=False)
        db.commit()
        token = create_access_token(data={"sub": "dpuadmin", "role": "admin"})
        ws = AsyncMock()
        assert await _validate_ws_token(ws, token) is True
        ws.close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_valid_operator_token_returns_true(self, db):
        from routes.dpus_websocket import _validate_ws_token
        from services.auth_service import create_access_token, create_user

        create_user(db, "dpuop", "dpuop@t.com", "pw", role="operator", must_change_password=False)
        db.commit()
        token = create_access_token(data={"sub": "dpuop", "role": "operator"})
        ws = AsyncMock()
        assert await _validate_ws_token(ws, token) is True

    @pytest.mark.asyncio
    async def test_viewer_role_refused(self):
        # DPU shells require admin/operator — a viewer must be closed with 4401.
        from routes.dpus_websocket import _validate_ws_token
        from services.auth_service import create_access_token

        token = create_access_token(data={"sub": "someviewer", "role": "viewer"})
        ws = AsyncMock()
        assert await _validate_ws_token(ws, token) is False
        ws.close.assert_awaited_once()
        assert ws.close.call_args[1]["code"] == 4401

    @pytest.mark.asyncio
    async def test_must_change_user_refused(self, db):
        # #184: a valid token whose user still owes a password change must be refused
        # at the WS boundary — else a seed-credential admin gets a DPU/BMC shell.
        from routes.dpus_websocket import _validate_ws_token
        from services.auth_service import create_access_token, create_user

        create_user(db, "dpumustchange", "dpumc@t.com", "pw", role="admin", must_change_password=True)
        db.commit()
        token = create_access_token(data={"sub": "dpumustchange", "role": "admin"})
        ws = AsyncMock()
        assert await _validate_ws_token(ws, token) is False
        ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_token_for_missing_user_refused(self, db):
        # #184 fail-closed: a valid JWT whose user doesn't exist (deleted) is refused,
        # not allowed through on a resolution failure.
        from routes.dpus_websocket import _validate_ws_token
        from services.auth_service import create_access_token

        token = create_access_token(data={"sub": "ghost", "role": "admin"})
        ws = AsyncMock()
        assert await _validate_ws_token(ws, token) is False
        ws.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_malformed_token_refused(self):
        from routes.dpus_websocket import _validate_ws_token

        ws = AsyncMock()
        assert await _validate_ws_token(ws, "not-a-jwt-at-all") is False
        ws.close.assert_awaited_once()
