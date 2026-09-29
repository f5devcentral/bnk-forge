"""
Unit tests for services.tmm_debug_service — debug sidecar exec.

Covers the specific, actionable error raised when a TMM pod has no
'debug' sidecar container (as opposed to a generic failure).
"""

from unittest.mock import MagicMock

import pytest

from services.tmm_debug_service import exec_debug_command


def _make_pod(container_names: list[str]):
    pod = MagicMock()
    pod.spec.containers = [MagicMock(name=n) for n in container_names]
    # MagicMock(name=...) sets the mock's repr name, not the `.name` attribute — set explicitly.
    for c, n in zip(pod.spec.containers, container_names):
        c.name = n
    pod.spec.init_containers = None
    return pod


class TestExecDebugCommandNoSidecar:
    def test_raises_specific_error_listing_present_containers(self):
        api_client = MagicMock()
        core_v1 = MagicMock()
        core_v1.read_namespaced_pod.return_value = _make_pod(["f5-tmm", "blobd", "observer"])

        import services.tmm_debug_service as svc

        original_core_v1_api = svc.k8s_client.CoreV1Api
        svc.k8s_client.CoreV1Api = MagicMock(return_value=core_v1)
        try:
            with pytest.raises(ValueError) as exc_info:
                exec_debug_command(api_client, "f5-tmm-abc123", "bnk-app1", ["tmctl", "-d", "blade"])
        finally:
            svc.k8s_client.CoreV1Api = original_core_v1_api

        message = str(exc_info.value)
        assert "no 'debug' sidecar container" in message
        assert "f5-tmm, blobd, observer" in message
        assert "BNK TMM configuration" in message

    def test_happy_path_unaffected_when_debug_present(self):
        api_client = MagicMock()
        core_v1 = MagicMock()
        core_v1.read_namespaced_pod.return_value = _make_pod(["f5-tmm", "debug", "blobd", "observer"])

        import services.tmm_debug_service as svc

        original_core_v1_api = svc.k8s_client.CoreV1Api
        svc.k8s_client.CoreV1Api = MagicMock(return_value=core_v1)
        try:
            # Should proceed past the container check without raising ValueError.
            # k8s_stream will fail against a MagicMock, so we only assert it's
            # NOT the container-presence ValueError.
            try:
                exec_debug_command(api_client, "f5-tmm-abc123", "bnk-app1", ["tmctl"])
            except ValueError as e:
                pytest.fail(f"Unexpected ValueError with debug container present: {e}")
            except Exception:
                pass  # exec against a mocked stream is expected to fail differently
        finally:
            svc.k8s_client.CoreV1Api = original_core_v1_api


class TestCachedExecForce:
    def test_force_skips_cached_output(self):
        from unittest.mock import patch

        from services.tmm_debug_service import _cached_exec_debug_command

        fresh = {"stdout": "fresh", "stderr": "", "exit_code": 0, "duration_ms": 1, "command": "x"}
        with patch("services.tmm_debug_service.cache") as mock_cache, \
                patch("services.tmm_debug_service.exec_debug_command", return_value=fresh) as mock_exec:
            mock_cache.get.return_value = {"stdout": "stale"}
            assert _cached_exec_debug_command(1, MagicMock(), "p", "ns", ["tmctl"])["stdout"] == "stale"
            assert _cached_exec_debug_command(1, MagicMock(), "p", "ns", ["tmctl"], force=True) == fresh
        mock_exec.assert_called_once()
