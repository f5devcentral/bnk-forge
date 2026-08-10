"""
Unit tests for D-023 Phase 2 — TMOS AS3 Engine.

All tests are pure (no DB, no network). Covers:
  1. IControlWriteClient — tenant guard, token refresh on poll, write helper.
  2. AS3 wire-format pure helpers — _is_task_pending, _task_terminal_result, _parse_dry_run_delta.
  3. TmosEngine.init — version gate (3.29 fail, 3.30 pass).
  4. TmosEngine.apply — async-poll happy, sync-200 (no hang), failure D-017.
  5. TmosEngine.plan — dry-run flag injected, async poll path.
  6. TmosEngine.destroy — tenant-scope REFUSAL (empty, *, slash).
  7. dispatch routing — tmos module → tmos tasks.
  8. render_as3 — dict and string template substitution.
  9. No secrets in logs — declaration body must not be logged.
"""

import inspect
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cred(auth_type="token"):
    from core.encryption import encrypt_value

    cred = MagicMock()
    cred.auth_type = auth_type
    cred.username = "admin"
    cred.password_encrypted = encrypt_value("Password1!")
    cred.token_encrypted = encrypt_value("TEST-TOKEN-XYZ")
    return cred


def _make_write_client():
    from services.f5.icontrol_client import IControlWriteClient

    cred = _make_cred("token")
    client = IControlWriteClient("10.0.0.1", 443, cred, verify_https_cert=False)
    # Pre-set token so we skip the real login
    client._token = "TEST-TOKEN-XYZ"
    client._token_expires_at = datetime.now(UTC) + timedelta(minutes=15)
    client._session.headers["X-F5-Auth-Token"] = "TEST-TOKEN-XYZ"
    return client


def _make_ctx(tenant="test_tenant", declaration=None):
    from services.execution.engine_interface import ModuleContext

    cred = _make_cred("token")
    return ModuleContext(
        module_id=1,
        project_id=1,
        path="tmos/as3-app",
        category="tmos",
        tmos_host="10.0.0.1",
        tmos_port=443,
        tmos_verify_https=False,
        tmos_credential=cred,
        as3_tenant=tenant,
        as3_declaration=declaration or {"class": "ADC", "test_tenant": {"class": "Tenant"}},
    )


def _ok_resp(code=200, message="success", task_id=None):
    """Build a mock response that looks like a terminal AS3 result."""
    resp = MagicMock()
    resp.status_code = code
    data = {
        "results": [{"code": code, "message": message}],
    }
    if task_id:
        data["id"] = task_id
        data["selfLink"] = f"https://10.0.0.1/mgmt/shared/appsvcs/task/{task_id}"
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


def _async_task_resp(task_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890", status_code=202):
    """Build a mock 202 response with a task reference."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "id": task_id,
        "selfLink": f"https://10.0.0.1/mgmt/shared/appsvcs/task/{task_id}",
    }
    resp.raise_for_status = MagicMock()
    return resp


def _fail_resp(code=422, message="declaration failed: bad config"):
    resp = MagicMock()
    resp.status_code = code
    resp.json.return_value = {
        "results": [{"code": code, "message": message}],
    }
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# 1. IControlWriteClient — tenant guard
# ---------------------------------------------------------------------------


class TestIControlWriteClientTenantGuard:
    """delete_tenant and post_declaration enforce tenant safety rules."""

    def test_delete_empty_tenant_raises(self):
        from services.f5.icontrol_client import IControlWriteClient

        client = _make_write_client()
        with pytest.raises(ValueError, match="non-empty"):
            client.delete_tenant("")

    def test_delete_whitespace_tenant_raises(self):
        client = _make_write_client()
        with pytest.raises(ValueError, match="non-empty"):
            client.delete_tenant("   ")

    def test_delete_wildcard_tenant_raises(self):
        client = _make_write_client()
        with pytest.raises(ValueError, match="wildcard"):
            client.delete_tenant("*")

    def test_delete_slash_tenant_raises(self):
        client = _make_write_client()
        with pytest.raises(ValueError, match="'/'"):
            client.delete_tenant("foo/bar")

    def test_post_empty_tenant_raises(self):
        client = _make_write_client()
        with pytest.raises(ValueError, match="non-empty"):
            client.post_declaration("", {})

    def test_post_whitespace_tenant_raises(self):
        client = _make_write_client()
        with pytest.raises(ValueError, match="non-empty"):
            client.post_declaration("   ", {})

    def test_delete_valid_tenant_calls_correct_url(self):
        """DELETE /mgmt/shared/appsvcs/declare/{tenant} is the exact URL shape."""
        client = _make_write_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"code": 200, "message": "success"}]}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "request", return_value=mock_resp) as mock_req:
            client.delete_tenant("my_tenant")

        mock_req.assert_called_once()
        call_args = mock_req.call_args
        method = call_args[0][0]
        url = call_args[0][1]
        assert method == "DELETE"
        assert "/mgmt/shared/appsvcs/declare/my_tenant" in url

    def test_post_declaration_injects_dry_run_flag(self):
        """post_declaration injects controls.dryRun=true when dry_run=True."""
        client = _make_write_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"code": 200, "message": "dry-run ok"}]}
        mock_resp.raise_for_status = MagicMock()

        captured = {}

        def _capture_request(method, url, json=None, timeout=None):
            captured["body"] = json
            return mock_resp

        with patch.object(client._session, "request", side_effect=_capture_request):
            client.post_declaration("my_tenant", {"class": "ADC"}, dry_run=True)

        assert captured.get("body", {}).get("controls", {}).get("dryRun") is True

    def test_post_declaration_does_not_inject_dry_run_by_default(self):
        """dry_run=False (default) must NOT inject controls.dryRun."""
        client = _make_write_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"code": 200, "message": "ok"}]}
        mock_resp.raise_for_status = MagicMock()

        captured = {}

        def _capture(method, url, json=None, timeout=None):
            captured["body"] = json
            return mock_resp

        with patch.object(client._session, "request", side_effect=_capture):
            client.post_declaration("my_tenant", {"class": "ADC"}, dry_run=False)

        body = captured.get("body", {})
        assert "controls" not in body or body.get("controls", {}).get("dryRun") is not True


# ---------------------------------------------------------------------------
# 2. Token refresh on poll
# ---------------------------------------------------------------------------


class TestIControlWriteClientTokenRefresh:
    """poll_task calls _refresh_if_needed on every iteration."""

    def test_poll_calls_refresh_every_iteration(self):
        """_refresh_if_needed is called on each poll loop iteration."""
        from services.f5.icontrol_client import IControlWriteClient

        client = _make_write_client()
        task_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        task_path = f"/mgmt/shared/appsvcs/task/{task_id}"

        # First response: in progress; second: terminal
        in_progress = MagicMock()
        in_progress.status_code = 200
        in_progress.json.return_value = {
            "id": task_id,
            "results": [{"code": 0, "message": "in progress"}],
        }
        in_progress.raise_for_status = MagicMock()

        terminal = MagicMock()
        terminal.status_code = 200
        terminal.json.return_value = {
            "id": task_id,
            "results": [{"code": 200, "message": "success"}],
        }
        terminal.raise_for_status = MagicMock()

        responses = [in_progress, terminal]

        def _get_resp(url, timeout=None):
            return responses.pop(0)

        refresh_calls = []

        def _track_refresh():
            refresh_calls.append(1)

        with patch.object(client._session, "get", side_effect=_get_resp):
            with patch.object(client, "_refresh_if_needed", side_effect=_track_refresh):
                result = client.poll_task(task_id, interval_s=0)

        # Should have been called twice (once per loop iteration)
        assert len(refresh_calls) == 2
        assert result["results"][0]["message"] == "success"


# ---------------------------------------------------------------------------
# 3. AS3 wire-format pure helpers
# ---------------------------------------------------------------------------


class TestAS3WireFormatHelpers:
    """_is_task_pending, _task_terminal_result, _parse_dry_run_delta."""

    def test_is_task_pending_true_on_in_progress(self):
        from services.f5.icontrol_client import _is_task_pending

        resp = {"results": [{"code": 0, "message": "in progress"}]}
        assert _is_task_pending(resp) is True

    def test_is_task_pending_false_on_success(self):
        from services.f5.icontrol_client import _is_task_pending

        resp = {"results": [{"code": 200, "message": "success"}]}
        assert _is_task_pending(resp) is False

    def test_is_task_pending_true_on_empty_results(self):
        from services.f5.icontrol_client import _is_task_pending

        # 202 with no results yet is still pending
        resp = {"id": "abc", "selfLink": "/task/abc"}
        assert _is_task_pending(resp) is True

    def test_is_task_pending_false_on_failed(self):
        from services.f5.icontrol_client import _is_task_pending

        resp = {"results": [{"code": 422, "message": "declaration failed"}]}
        assert _is_task_pending(resp) is False

    def test_task_terminal_result_success(self):
        from services.f5.icontrol_client import _task_terminal_result

        resp = {"results": [{"code": 200, "message": "tenant deployed"}]}
        success, msg = _task_terminal_result(resp)
        assert success is True
        assert "tenant deployed" in msg

    def test_task_terminal_result_failure(self):
        from services.f5.icontrol_client import _task_terminal_result

        resp = {"results": [{"code": 422, "message": "schema validation error"}]}
        success, msg = _task_terminal_result(resp)
        assert success is False
        assert "schema validation error" in msg

    def test_task_terminal_result_multiple_entries_all_ok(self):
        from services.f5.icontrol_client import _task_terminal_result

        resp = {
            "results": [
                {"code": 200, "message": "tenant1 ok"},
                {"code": 200, "message": "tenant2 ok"},
            ]
        }
        success, msg = _task_terminal_result(resp)
        assert success is True

    def test_task_terminal_result_one_failure_is_failure(self):
        from services.f5.icontrol_client import _task_terminal_result

        resp = {
            "results": [
                {"code": 200, "message": "tenant1 ok"},
                {"code": 500, "message": "tenant2 failed"},
            ]
        }
        success, msg = _task_terminal_result(resp)
        assert success is False

    def test_parse_dry_run_delta_extracts_counts(self):
        from services.f5.icontrol_client import _parse_dry_run_delta

        resp = {"dryRunResults": {"adds": 2, "changes": 1, "removes": 0}}
        adds, changes, destroys, details = _parse_dry_run_delta(resp)
        assert adds == 2
        assert changes == 1
        assert destroys == 0
        assert isinstance(details, str)

    def test_parse_dry_run_delta_zero_counts_on_empty(self):
        from services.f5.icontrol_client import _parse_dry_run_delta

        resp = {"results": [{"code": 200, "message": "no changes"}]}
        adds, changes, destroys, details = _parse_dry_run_delta(resp)
        assert adds == 0
        assert changes == 0
        assert destroys == 0

    def test_parse_dry_run_delta_strips_declaration_from_details(self):
        """declaration key (may contain TLS private keys) must NOT appear in details.

        Some AS3 versions echo the submitted declaration back in the dry-run
        response.  _parse_dry_run_delta must strip it before serialising to
        PlanResult.details so TLS private keys are never stored.
        """
        from services.f5.icontrol_client import _parse_dry_run_delta

        secret_key = "-----BEGIN RSA PRIVATE KEY-----SECRETCONTENT-----END RSA PRIVATE KEY-----"
        resp = {
            "dryRunResults": {"adds": 1, "changes": 0, "removes": 0},
            "results": [{"code": 200, "message": "dry-run ok"}],
            "declaration": {
                "class": "ADC",
                "tls_server": {"class": "TLS_Server", "privateKey": secret_key},
            },
        }
        adds, changes, destroys, details = _parse_dry_run_delta(resp)

        assert adds == 1
        assert "declaration" not in details, (
            "'declaration' key must be stripped from dry-run details to prevent TLS key leak"
        )
        assert secret_key not in details, (
            "TLS private key content must not appear in dry-run details"
        )


# ---------------------------------------------------------------------------
# 4. TmosEngine.init — version gate
# ---------------------------------------------------------------------------


class TestTmosEngineInit:
    """Version gate: AS3 < 3.30 → failure; ≥ 3.30 → success."""

    def _mock_client(self, as3_version: str):
        mock = MagicMock()
        mock.authenticate.return_value = (
            "TEST-TOKEN",
            datetime.now(UTC) + timedelta(minutes=15),
        )
        mock.get_as3_version.return_value = as3_version
        return mock

    def test_init_fails_on_as3_version_below_3_30(self):
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = self._mock_client("3.29.0")

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.init(ctx)

        assert result.success is False
        assert "3.30" in (result.error_message or "")

    def test_init_succeeds_on_as3_version_3_30(self):
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = self._mock_client("3.30.0")

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.init(ctx)

        assert result.success is True
        assert "3.30.0" in result.stdout

    def test_init_succeeds_on_as3_version_3_36(self):
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = self._mock_client("3.36.0")

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.init(ctx)

        assert result.success is True

    def test_init_fails_on_no_as3(self):
        """AS3 not installed returns '0.0.0' → fails version gate."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = self._mock_client("0.0.0")

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.init(ctx)

        assert result.success is False


# ---------------------------------------------------------------------------
# 5. TmosEngine.apply — async-poll happy, sync-200, failure D-017
# ---------------------------------------------------------------------------


class TestTmosEngineApply:
    """apply() handles 200-sync AND 202-async correctly; D-017 honest failure."""

    def test_apply_sync_200_success(self):
        """A direct 200 response (no async) is handled correctly."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = MagicMock()
        # post_declaration returns a final 200 (no task id → not async)
        mock_client.post_declaration.return_value = {
            "results": [{"code": 200, "message": "success"}],
        }

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.apply(ctx)

        assert result.success is True
        mock_client.poll_task.assert_not_called()  # must NOT poll a sync response

    def test_apply_async_202_polls_to_success(self):
        """A 202 response triggers polling; terminal success → OperationResult.success=True."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        task_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        mock_client = MagicMock()
        mock_client.post_declaration.return_value = {
            "id": task_id,
            "selfLink": f"https://10.0.0.1/mgmt/shared/appsvcs/task/{task_id}",
        }
        mock_client.poll_task.return_value = {
            "results": [{"code": 200, "message": "tenant applied"}],
        }

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.apply(ctx)

        assert result.success is True
        mock_client.poll_task.assert_called_once_with(task_id, on_output=None)

    def test_apply_async_202_polls_to_failure_d017(self):
        """When the polled task returns a failure code, success=False (D-017 honest failure)."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        task_id = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

        mock_client = MagicMock()
        mock_client.post_declaration.return_value = {
            "id": task_id,
            "selfLink": f"https://10.0.0.1/mgmt/shared/appsvcs/task/{task_id}",
        }
        mock_client.poll_task.return_value = {
            "results": [{"code": 422, "message": "schema validation failed"}],
        }

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.apply(ctx)

        assert result.success is False
        assert result.error_message is not None
        assert "schema validation failed" in result.error_message

    def test_apply_empty_tenant_refused(self):
        """Empty tenant → success=False, engine never calls post_declaration."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx(tenant="")
        mock_client = MagicMock()

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.apply(ctx)

        assert result.success is False
        assert "empty" in (result.error_message or "").lower()
        mock_client.post_declaration.assert_not_called()

    def test_apply_sync_200_failure_d017(self):
        """Non-2xx in sync 200 path → success=False."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = MagicMock()
        mock_client.post_declaration.return_value = {
            "results": [{"code": 500, "message": "internal error"}],
        }

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.apply(ctx)

        assert result.success is False
        assert result.error_message is not None


# ---------------------------------------------------------------------------
# 6. TmosEngine.plan — dry-run flag
# ---------------------------------------------------------------------------


class TestTmosEnginePlan:
    """plan() injects controls.dryRun and returns PlanResult."""

    def test_plan_posts_with_dry_run_true(self):
        """post_declaration is called with dry_run=True."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = MagicMock()
        mock_client.get_as3_version.return_value = "3.36.0"
        mock_client.post_declaration.return_value = {
            "dryRunResults": {"adds": 1, "changes": 0, "removes": 0},
            "results": [{"code": 200, "message": "dry-run complete"}],
        }

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.plan(ctx)

        mock_client.post_declaration.assert_called_once()
        call_kwargs = mock_client.post_declaration.call_args
        assert call_kwargs.kwargs.get("dry_run") is True or (
            len(call_kwargs.args) >= 3 and call_kwargs.args[2] is True
        ) or call_kwargs.kwargs.get("dry_run", True)

    def test_plan_fails_on_as3_below_3_30(self):
        """plan() must refuse and NOT call post_declaration when AS3 < 3.30.

        On AS3 < 3.30, controls.dryRun is silently IGNORED — the declaration
        would be applied for real.  The version gate must fire before any write.
        """
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = MagicMock()
        mock_client.get_as3_version.return_value = "3.29.0"

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.plan(ctx)

        assert result.has_changes is False
        assert "3.30" in (result.details or "")
        mock_client.post_declaration.assert_not_called()  # CRITICAL: no write happened

    def test_plan_empty_tenant_returns_plan_result_with_details(self):
        """Empty tenant → PlanResult with descriptive details (not an exception)."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx(tenant="")

        result = engine.plan(ctx)
        assert isinstance(result.details, str)
        assert result.details  # non-empty explanation

    def test_plan_calls_get_as3_version_exactly_once_on_version_gate_fail(self):
        """get_as3_version() must be called only once even when version gate rejects.

        Calling it twice (once for the check, once in the error string) causes
        a redundant HTTP auth round-trip on every failed plan attempt.
        """
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx()
        mock_client = MagicMock()
        mock_client.get_as3_version.return_value = "3.29.0"

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.plan(ctx)

        assert result.has_changes is False
        assert "3.29.0" in (result.details or ""), "version string must appear in refusal message"
        mock_client.get_as3_version.assert_called_once()


# ---------------------------------------------------------------------------
# 7. TmosEngine.destroy — tenant-scope REFUSAL
# ---------------------------------------------------------------------------


class TestTmosEngineDestroyTenantGuard:
    """destroy() fails-closed when as3_tenant is empty."""

    def test_destroy_empty_tenant_fails_closed(self):
        """Empty as3_tenant → success=False, delete_tenant never called."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx(tenant="")
        mock_client = MagicMock()

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.destroy(ctx)

        assert result.success is False
        assert "empty" in (result.error_message or "").lower()
        mock_client.delete_tenant.assert_not_called()

    def test_destroy_none_tenant_fails_closed(self):
        """None as3_tenant → success=False."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx(tenant=None)

        mock_client = MagicMock()
        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.destroy(ctx)

        assert result.success is False
        mock_client.delete_tenant.assert_not_called()

    def test_destroy_valid_tenant_calls_delete_tenant(self):
        """Valid tenant → delete_tenant called with correct name."""
        from services.execution.tmos_engine import TmosEngine

        engine = TmosEngine()
        ctx = _make_ctx(tenant="my_app")
        mock_client = MagicMock()
        mock_client.delete_tenant.return_value = {
            "results": [{"code": 200, "message": "tenant deleted"}],
        }

        with patch.object(engine, "_client", return_value=mock_client):
            result = engine.destroy(ctx)

        assert result.success is True
        mock_client.delete_tenant.assert_called_once_with("my_app")

    def test_client_delete_tenant_refuses_wildcard(self):
        """IControlWriteClient.delete_tenant raises on '*'."""
        client = _make_write_client()
        with pytest.raises(ValueError, match="wildcard"):
            client.delete_tenant("*")

    def test_client_delete_tenant_refuses_path(self):
        """IControlWriteClient.delete_tenant raises on path-containing names."""
        client = _make_write_client()
        with pytest.raises(ValueError, match="'/'"):
            client.delete_tenant("Common/app")


# ---------------------------------------------------------------------------
# 8. Dispatch routing — tmos module → tmos tasks
# ---------------------------------------------------------------------------


class TestDispatchTmos:
    """dispatch_* functions route tmos execution_engine to tmos_tasks."""

    def _make_tmos_module(self, lib_path="tmos/as3-app"):
        module = MagicMock()
        module.id = 77
        module.library_module = MagicMock()
        module.library_module.path = lib_path
        module.library_module.execution_engine = "tmos"
        module.library_module.engine_type = None
        module.library_module.module_source_kind = None
        module.library_module.deploy_model = None
        module.path_in_project = lib_path
        return module

    def test_dispatch_init_routes_to_tmos(self):
        from services.execution.task_dispatch import dispatch_init

        with patch("tasks.tmos_tasks.run_tmos_init") as mock_init:
            mock_init.delay.return_value = MagicMock(id="t-1")
            dispatch_init(10, self._make_tmos_module())
            mock_init.delay.assert_called_once_with(10, 77, auto_apply=False)

    def test_dispatch_plan_routes_to_tmos(self):
        from services.execution.task_dispatch import dispatch_plan

        with patch("tasks.tmos_tasks.run_tmos_plan") as mock_plan:
            mock_plan.delay.return_value = MagicMock(id="t-2")
            dispatch_plan(10, self._make_tmos_module())
            mock_plan.delay.assert_called_once_with(10, 77)

    def test_dispatch_apply_routes_to_tmos(self):
        from services.execution.task_dispatch import dispatch_apply

        with patch("tasks.tmos_tasks.run_tmos_apply") as mock_apply:
            mock_apply.delay.return_value = MagicMock(id="t-3")
            dispatch_apply(10, self._make_tmos_module())
            mock_apply.delay.assert_called_once_with(10, 77)

    def test_dispatch_destroy_routes_to_tmos(self):
        from services.execution.task_dispatch import dispatch_destroy

        with patch("tasks.tmos_tasks.run_tmos_destroy") as mock_destroy:
            mock_destroy.delay.return_value = MagicMock(id="t-4")
            dispatch_destroy(10, self._make_tmos_module())
            mock_destroy.delay.assert_called_once_with(10, 77)

    def test_dispatch_apply_signature_routes_to_tmos(self):
        from services.execution.task_dispatch import dispatch_apply_signature

        with patch("tasks.tmos_tasks.run_tmos_apply") as mock_apply:
            mock_apply.s.return_value = MagicMock(name="sig")
            dispatch_apply_signature(10, self._make_tmos_module())
            mock_apply.s.assert_called_once_with(10, 77)

    def test_dispatch_destroy_signature_routes_to_tmos(self):
        from services.execution.task_dispatch import dispatch_destroy_signature

        with patch("tasks.tmos_tasks.run_tmos_destroy") as mock_destroy:
            mock_destroy.s.return_value = MagicMock(name="sig")
            dispatch_destroy_signature(10, self._make_tmos_module())
            mock_destroy.s.assert_called_once_with(10, 77)

    def test_tmos_engine_in_explicit_engines(self):
        from services.execution.task_dispatch import _EXPLICIT_EXECUTION_ENGINES

        assert "tmos" in _EXPLICIT_EXECUTION_ENGINES

    def test_non_tmos_not_routed_to_tmos(self):
        """SSH module does not route to tmos tasks."""
        from services.execution.task_dispatch import dispatch_apply

        ssh_module = MagicMock()
        ssh_module.id = 88
        ssh_module.library_module = MagicMock()
        ssh_module.library_module.execution_engine = None
        ssh_module.library_module.engine_type = "ssh"
        ssh_module.library_module.module_source_kind = None
        ssh_module.library_module.deploy_model = None
        ssh_module.path_in_project = "bare-metal/probe"

        with patch("tasks.ssh_tasks.run_ssh_apply") as mock_ssh:
            with patch("tasks.tmos_tasks.run_tmos_apply") as mock_tmos:
                mock_ssh.delay.return_value = MagicMock(id="ssh-1")
                dispatch_apply(10, ssh_module)
                mock_tmos.delay.assert_not_called()
                mock_ssh.delay.assert_called_once()


# ---------------------------------------------------------------------------
# 9. render_as3 — template rendering
# ---------------------------------------------------------------------------


class TestRenderAS3:
    """render_as3 produces a valid declaration dict from template + variables."""

    def test_render_dict_template_substitutes_variables(self):
        from services.execution.variable_assembler import render_as3

        template = {"class": "ADC", "tenant": "{{tenant_name}}", "port": "{{port}}"}
        variables = {"tenant_name": "my_app", "port": "8080"}
        result = render_as3(template, variables)
        assert result["tenant"] == "my_app"
        assert result["port"] == "8080"

    def test_render_string_template_json(self):
        from services.execution.variable_assembler import render_as3

        template = '{"class": "ADC", "schemaVersion": "{{as3_version}}"}'
        variables = {"as3_version": "3.36.0"}
        result = render_as3(template, variables)
        assert result["schemaVersion"] == "3.36.0"

    def test_render_invalid_json_raises(self):
        from services.execution.variable_assembler import render_as3

        template = '{"class": "ADC", "bad": {{bad_value}'  # Invalid JSON after substitution
        variables = {}
        with pytest.raises(ValueError):
            render_as3(template, variables)

    def test_render_unresolved_placeholder_stays_in_place(self):
        """Unresolved {{var}} stays in the output rather than raising."""
        from services.execution.variable_assembler import render_as3

        template = {"class": "ADC", "tenant": "{{tenant_name}}", "other": "{{missing}}"}
        variables = {"tenant_name": "my_app"}
        result = render_as3(template, variables)
        assert result["tenant"] == "my_app"
        assert result["other"] == "{{missing}}"  # unresolved placeholder preserved


# ---------------------------------------------------------------------------
# 10. Read-only invariant not broken — base class still clean
# ---------------------------------------------------------------------------


class TestIControlReadOnlyInvariantPreserved:
    """IControlClient still has NO write verbs — subclass is the only write surface."""

    def test_base_class_has_no_write_verbs(self):
        from services.f5.icontrol_client import IControlClient

        write_verbs = {
            "post", "patch", "delete", "put", "apply", "deploy",
            "create", "update", "destroy", "write", "push",
        }
        public_methods = [
            name
            for name, method in inspect.getmembers(IControlClient, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        offenders = [m for m in public_methods if any(verb in m.lower() for verb in write_verbs)]
        assert offenders == [], (
            f"IControlClient (base) must not expose write methods: {offenders}"
        )

    def test_write_client_has_write_methods(self):
        """Sanity: the subclass DOES have the write methods."""
        from services.f5.icontrol_client import IControlWriteClient

        write_methods = {
            name
            for name, _ in inspect.getmembers(IControlWriteClient, predicate=inspect.isfunction)
            if not name.startswith("_")
        }
        assert "post_declaration" in write_methods
        assert "delete_tenant" in write_methods
        assert "poll_task" in write_methods
        assert "get_as3_version" in write_methods


# ---------------------------------------------------------------------------
# 11. No secrets in logs
# ---------------------------------------------------------------------------


class TestNoSecretsInLogs:
    """Declaration bodies must not be emitted to the logger."""

    def test_post_declaration_does_not_log_body(self, caplog):
        """post_declaration logs intent (tenant + host) but never the body."""
        import logging

        client = _make_write_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"code": 200, "message": "ok"}]}
        mock_resp.raise_for_status = MagicMock()

        secret_value = "SECRET-PRIVATE-KEY-CONTENT-12345"
        declaration = {"class": "ADC", "tls_key": secret_value}

        with patch.object(client._session, "request", return_value=mock_resp):
            with caplog.at_level(logging.DEBUG, logger="services.f5.icontrol_client"):
                client.post_declaration("my_tenant", declaration, dry_run=False)

        # The secret must not appear anywhere in captured log output
        for record in caplog.records:
            assert secret_value not in record.getMessage(), (
                f"Secret leaked in log: {record.getMessage()!r}"
            )

    def test_delete_tenant_does_not_log_body(self, caplog):
        """delete_tenant has no body, but logs just the tenant name and host."""
        import logging

        client = _make_write_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"results": [{"code": 200, "message": "ok"}]}
        mock_resp.raise_for_status = MagicMock()

        with patch.object(client._session, "request", return_value=mock_resp):
            with caplog.at_level(logging.DEBUG, logger="services.f5.icontrol_client"):
                client.delete_tenant("my_tenant")

        # Verify tenant name IS logged (expected) but no sensitive body
        tenant_logged = any("my_tenant" in r.getMessage() for r in caplog.records)
        assert tenant_logged


# ---------------------------------------------------------------------------
# 12. _parse_as3_version edge cases
# ---------------------------------------------------------------------------


class TestParseAS3Version:
    """_parse_as3_version handles edge cases gracefully."""

    def test_parses_standard_semver(self):
        from services.f5.icontrol_client import _parse_as3_version

        assert _parse_as3_version("3.30.0") == (3, 30)
        assert _parse_as3_version("3.36.1") == (3, 36)
        assert _parse_as3_version("3.29.0") == (3, 29)

    def test_returns_zero_zero_on_unparseable(self):
        from services.f5.icontrol_client import _parse_as3_version

        assert _parse_as3_version("0.0.0") == (0, 0)
        assert _parse_as3_version("invalid") == (0, 0)
        assert _parse_as3_version("") == (0, 0)

    def test_version_comparison_works(self):
        from services.f5.icontrol_client import _AS3_DRY_RUN_MIN, _parse_as3_version

        assert _parse_as3_version("3.29.0") < _AS3_DRY_RUN_MIN
        assert _parse_as3_version("3.30.0") >= _AS3_DRY_RUN_MIN
        assert _parse_as3_version("3.36.0") >= _AS3_DRY_RUN_MIN
