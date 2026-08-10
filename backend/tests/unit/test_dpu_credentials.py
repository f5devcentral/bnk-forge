"""Unit tests for the BMC credential resilience helper.

Phase 3: verifies the configured → 0penBmc fallback path and the
best-effort auto-PATCH of the root password on successful fallback.
"""

from unittest.mock import MagicMock

import pytest
from paramiko.ssh_exception import AuthenticationException

from services import dpu_credentials


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class TestRedfishPostWithFallback:
    def test_first_attempt_wins_no_patch(self, monkeypatch):
        calls: list[str] = []

        def fake_post(url, auth, json, verify, timeout):  # noqa: ARG001
            calls.append(auth.password)
            return _Resp(204)

        patched: list[tuple] = []
        monkeypatch.setattr(dpu_credentials.requests, "post", fake_post)
        monkeypatch.setattr(
            dpu_credentials, "patch_bmc_root_password",
            lambda *a, **kw: patched.append(a),
        )

        resp = dpu_credentials.redfish_post_with_bmc_fallback(
            "10.0.0.1", "root", "configured",
            url="https://10.0.0.1/redfish/v1/Systems/Bluefield/Actions/ComputerSystem.Reset",
            json_body={"ResetType": "PowerCycle"},
        )
        assert resp.status_code == 204
        assert calls == ["configured"]
        assert patched == []  # no fallback → no auto-patch

    def test_fallback_triggers_patch(self, monkeypatch):
        calls: list[str] = []

        def fake_post(url, auth, json, verify, timeout):  # noqa: ARG001
            calls.append(auth.password)
            return _Resp(401 if auth.password == "configured" else 204)

        patched: list[tuple] = []
        monkeypatch.setattr(dpu_credentials.requests, "post", fake_post)
        monkeypatch.setattr(
            dpu_credentials, "patch_bmc_root_password",
            lambda *a, **kw: patched.append(a),
        )

        resp = dpu_credentials.redfish_post_with_bmc_fallback(
            "10.0.0.1", "root", "configured",
            url="https://10.0.0.1/redfish/v1/Foo",
            json_body={"x": 1},
        )
        assert resp.status_code == 204
        assert calls == ["configured", "0penBmc"]
        # Auto-PATCH fired with the configured password as the new value.
        assert patched == [("10.0.0.1", "root", "0penBmc", "configured")]

    def test_skip_fallback_when_already_default(self, monkeypatch):
        calls: list[str] = []

        def fake_post(url, auth, json, verify, timeout):  # noqa: ARG001
            calls.append(auth.password)
            return _Resp(401)

        patched: list[tuple] = []
        monkeypatch.setattr(dpu_credentials.requests, "post", fake_post)
        monkeypatch.setattr(
            dpu_credentials, "patch_bmc_root_password",
            lambda *a, **kw: patched.append(a),
        )

        resp = dpu_credentials.redfish_post_with_bmc_fallback(
            "10.0.0.1", "root", dpu_credentials.NVIDIA_DEFAULT_BMC_PASSWORD,
            url="https://10.0.0.1/redfish/v1/Foo", json_body={"x": 1},
        )
        assert resp.status_code == 401
        assert calls == ["0penBmc"]  # no retry
        assert patched == []


class TestSshConnectWithFallback:
    def test_first_attempt_wins(self, monkeypatch):
        attempts: list[str] = []

        def fake_connect(self, host, username, password, timeout, allow_agent, look_for_keys):  # noqa: ARG001
            attempts.append(password)

        monkeypatch.setattr("paramiko.SSHClient.connect", fake_connect)
        patched: list[tuple] = []
        monkeypatch.setattr(
            dpu_credentials, "patch_bmc_root_password",
            lambda *a, **kw: patched.append(a),
        )

        client = dpu_credentials.ssh_connect_bmc_with_fallback("h", "root", "configured")
        assert client is not None
        assert attempts == ["configured"]
        assert patched == []

    def test_fallback_triggers_patch(self, monkeypatch):
        attempts: list[str] = []

        def fake_connect(self, host, username, password, timeout, allow_agent, look_for_keys):  # noqa: ARG001
            attempts.append(password)
            if password == "configured":
                raise AuthenticationException("bad pw")

        monkeypatch.setattr("paramiko.SSHClient.connect", fake_connect)
        patched: list[tuple] = []
        monkeypatch.setattr(
            dpu_credentials, "patch_bmc_root_password",
            lambda *a, **kw: patched.append(a),
        )

        client = dpu_credentials.ssh_connect_bmc_with_fallback("h", "root", "configured")
        assert client is not None
        assert attempts == ["configured", "0penBmc"]
        assert patched == [("h", "root", "0penBmc", "configured")]

    def test_auth_failure_on_default_reraises(self, monkeypatch):
        def fake_connect(self, host, username, password, timeout, allow_agent, look_for_keys):  # noqa: ARG001
            raise AuthenticationException("bad pw")

        monkeypatch.setattr("paramiko.SSHClient.connect", fake_connect)
        with pytest.raises(AuthenticationException):
            dpu_credentials.ssh_connect_bmc_with_fallback(
                "h", "root", dpu_credentials.NVIDIA_DEFAULT_BMC_PASSWORD,
            )


class TestPatchBmcRootPassword:
    def test_no_op_when_passwords_identical(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr(
            dpu_credentials.requests, "patch",
            lambda *a, **kw: called.append("called") or MagicMock(),
        )
        dpu_credentials.patch_bmc_root_password("h", "root", "same", "same")
        assert called == []

    def test_catches_network_error(self, monkeypatch):
        import requests as _requests

        def raise_it(*a, **kw):
            raise _requests.RequestException("connect failed")

        monkeypatch.setattr(dpu_credentials.requests, "patch", raise_it)
        # Must not raise
        dpu_credentials.patch_bmc_root_password("h", "root", "a", "b")
