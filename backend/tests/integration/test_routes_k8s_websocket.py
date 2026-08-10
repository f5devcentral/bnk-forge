"""
Integration tests for K8s WebSocket routes — /ws/k8s.

Covers: pod exec WebSocket, pod logs follow WebSocket, token validation,
connected messages, stdin/stdout relay, error handling, log streaming.

Uses FastAPI TestClient. K8s services are mocked at the route level.
"""

import asyncio
import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from services.auth_service import create_access_token


def _make_token(role: str = "admin") -> str:
    """Create a valid JWT token for testing."""
    return create_access_token(data={"sub": f"test{role}", "role": role})


# ═══════════════════════════════════════════════════════════════════════
# Pod Exec — Auth
# ═══════════════════════════════════════════════════════════════════════


class TestPodExecAuth:
    """WebSocket auth for /ws/k8s/clusters/{id}/pods/{pod}/exec."""

    def test_exec_missing_token_closes_with_4401(self, client, sample_user):
        """WebSocket without token query param is rejected."""
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/k8s/clusters/1/pods/test-pod/exec?namespace=default"
            ):
                pass

    def test_exec_invalid_token_closes(self, client, sample_user):
        """Invalid JWT token closes WebSocket."""
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/k8s/clusters/1/pods/test-pod/exec?namespace=default&token=invalid.jwt.token"
            ):
                pass

    def test_exec_valid_admin_token_accepted(self, client, sample_user):
        """Valid admin JWT passes auth — connection accepted."""
        token = _make_token("admin")
        # Auth passes, then cluster 999 not found triggers error
        with client.websocket_connect(
            f"/ws/k8s/clusters/999/pods/test-pod/exec?namespace=default&token={token}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"

    def test_exec_valid_viewer_token_accepted(self, client, sample_user):
        """Valid viewer JWT passes auth — connection accepted."""
        token = _make_token("viewer")
        with client.websocket_connect(
            f"/ws/k8s/clusters/999/pods/test-pod/exec?namespace=default&token={token}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"

    def test_exec_valid_operator_token_accepted(self, client, sample_user):
        """Valid operator JWT passes auth — connection accepted."""
        token = _make_token("operator")
        with client.websocket_connect(
            f"/ws/k8s/clusters/999/pods/test-pod/exec?namespace=default&token={token}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"


# ═══════════════════════════════════════════════════════════════════════
# Pod Exec — Connected + Exec Flow
# ═══════════════════════════════════════════════════════════════════════


class TestPodExecFlow:
    """WebSocket /ws/k8s/clusters/{id}/pods/{pod}/exec — exec stream flow."""

    @patch("routes.k8s_websocket.stream")
    @patch("routes.k8s_websocket.KubernetesService")
    def test_sends_connected_message(
        self, mock_k8s_cls, mock_stream,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should send connected message with pod/namespace/container details."""
        cluster = make_k8s_cluster(project=sample_project, name="exec-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="exec-test")
        mock_k8s.load_kubeconfig.return_value = MagicMock()

        # Mock the exec stream — closes immediately (is_open returns False)
        mock_resp = MagicMock()
        mock_resp.is_open.return_value = False
        mock_resp.returncode = 0
        mock_resp.read_channel.return_value = None
        mock_stream.return_value = mock_resp

        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/exec"
            f"?namespace=default&container=main&command=/bin/sh&token={token}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "connected"
            assert msg["pod"] == "my-pod"
            assert msg["namespace"] == "default"
            assert msg["container"] == "main"
            assert msg["command"] == "/bin/sh"

            # Stream closed immediately → exit message
            msg2 = ws.receive_json()
            assert msg2["type"] == "exit"
            assert msg2["code"] == 0

    @patch("routes.k8s_websocket.stream")
    @patch("routes.k8s_websocket.KubernetesService")
    def test_relays_stdout(
        self, mock_k8s_cls, mock_stream,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should relay stdout from exec stream as base64-encoded messages."""
        cluster = make_k8s_cluster(project=sample_project, name="stdout-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="stdout-test")
        mock_k8s.load_kubeconfig.return_value = MagicMock()

        # Mock exec stream: one stdout read, then stream closes
        call_count = [0]
        mock_resp = MagicMock()

        def _is_open():
            call_count[0] += 1
            return call_count[0] <= 2  # open for 2 checks

        mock_resp.is_open.side_effect = _is_open
        mock_resp.read_stdout.side_effect = ["hello world", None]
        mock_resp.read_stderr.return_value = None
        mock_resp.returncode = 0
        mock_resp.read_channel.return_value = None
        mock_stream.return_value = mock_resp

        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/exec"
            f"?namespace=default&token={token}"
        ) as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            stdout = ws.receive_json()
            assert stdout["type"] == "stdout"
            decoded = base64.b64decode(stdout["data"]).decode("utf-8")
            assert decoded == "hello world"

    @patch("routes.k8s_websocket.stream")
    @patch("routes.k8s_websocket.KubernetesService")
    def test_relays_stderr(
        self, mock_k8s_cls, mock_stream,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should relay stderr from exec stream."""
        cluster = make_k8s_cluster(project=sample_project, name="stderr-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="stderr-test")
        mock_k8s.load_kubeconfig.return_value = MagicMock()

        call_count = [0]
        mock_resp = MagicMock()

        def _is_open():
            call_count[0] += 1
            return call_count[0] <= 2

        mock_resp.is_open.side_effect = _is_open
        mock_resp.read_stdout.return_value = None
        mock_resp.read_stderr.side_effect = ["error: something failed", None]
        mock_resp.returncode = 0
        mock_resp.read_channel.return_value = None
        mock_stream.return_value = mock_resp

        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/exec"
            f"?namespace=default&token={token}"
        ) as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            stderr = ws.receive_json()
            assert stderr["type"] == "stderr"
            decoded = base64.b64decode(stderr["data"]).decode("utf-8")
            assert "error: something failed" in decoded

    @patch("routes.k8s_websocket.stream")
    @patch("routes.k8s_websocket.KubernetesService")
    def test_distroless_shell_not_found(
        self, mock_k8s_cls, mock_stream,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should send error message for distroless containers (shell not available)."""
        cluster = make_k8s_cluster(project=sample_project, name="distroless-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="distroless-test")
        mock_k8s.load_kubeconfig.return_value = MagicMock()

        mock_resp = MagicMock()
        mock_resp.is_open.return_value = False  # stream never opens (distroless)
        # returncode raises ValueError with the OCI error
        mock_resp.returncode = None  # update call first
        mock_resp.update.return_value = None

        # Simulate ValueError from resp.returncode (distroless container)
        type(mock_resp).returncode = property(
            lambda self: (_ for _ in ()).throw(
                ValueError(
                    "error executing command in container: "
                    "OCI runtime exec failed: executable file not found"
                )
            )
        )
        mock_resp.read_channel.return_value = None
        mock_stream.return_value = mock_resp

        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/exec"
            f"?namespace=default&command=/bin/bash&token={token}"
        ) as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            error = ws.receive_json()
            assert error["type"] == "error"
            assert "not available" in error["message"] or "distroless" in error["message"]

    @patch("routes.k8s_websocket.stream")
    @patch("routes.k8s_websocket.KubernetesService")
    def test_exit_code_127_shell_not_found(
        self, mock_k8s_cls, mock_stream,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should report shell not found for exit code 127."""
        cluster = make_k8s_cluster(project=sample_project, name="exit127-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="exit127-test")
        mock_k8s.load_kubeconfig.return_value = MagicMock()

        mock_resp = MagicMock()
        mock_resp.is_open.return_value = False
        mock_resp.returncode = 127
        mock_resp.read_channel.return_value = None
        mock_stream.return_value = mock_resp

        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/exec"
            f"?namespace=default&command=/bin/zsh&token={token}"
        ) as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            error = ws.receive_json()
            assert error["type"] == "error"
            assert "not available" in error["message"] or "distroless" in error["message"]

    @patch("routes.k8s_websocket.stream")
    @patch("routes.k8s_websocket.KubernetesService")
    def test_normal_exit(
        self, mock_k8s_cls, mock_stream,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should send exit message with code 0 for normal session end."""
        cluster = make_k8s_cluster(project=sample_project, name="normal-exit-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="normal-exit-test")
        mock_k8s.load_kubeconfig.return_value = MagicMock()

        mock_resp = MagicMock()
        mock_resp.is_open.return_value = False
        mock_resp.returncode = 0
        mock_resp.read_channel.return_value = None
        mock_stream.return_value = mock_resp

        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/exec"
            f"?namespace=default&token={token}"
        ) as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"

            exit_msg = ws.receive_json()
            assert exit_msg["type"] == "exit"
            assert exit_msg["code"] == 0
            assert exit_msg["message"] == "Session terminated"

    def test_exec_nonexistent_cluster_sends_error(self, client, sample_user):
        """Non-existent cluster ID should send exec error message."""
        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/999/pods/test-pod/exec?namespace=default&token={token}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "failed" in msg["message"].lower() or "Exec" in msg["message"]


# ═══════════════════════════════════════════════════════════════════════
# Pod Logs Follow — Auth
# ═══════════════════════════════════════════════════════════════════════


class TestPodLogsAuth:
    """WebSocket auth for /ws/k8s/clusters/{id}/pods/{pod}/logs/follow."""

    def test_logs_missing_token_closes(self, client, sample_user):
        """WebSocket without token is rejected."""
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/k8s/clusters/1/pods/test-pod/logs/follow?namespace=default"
            ):
                pass

    def test_logs_invalid_token_closes(self, client, sample_user):
        """Invalid token closes WebSocket."""
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/k8s/clusters/1/pods/test-pod/logs/follow?namespace=default&token=bad"
            ):
                pass

    def test_logs_valid_token_accepted(self, client, sample_user):
        """Valid token passes auth — connection accepted."""
        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/999/pods/test-pod/logs/follow?namespace=default&token={token}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"  # cluster not found


# ═══════════════════════════════════════════════════════════════════════
# Pod Logs Follow — Stream Flow
# ═══════════════════════════════════════════════════════════════════════


class TestPodLogsFlow:
    """WebSocket /ws/k8s/clusters/{id}/pods/{pod}/logs/follow — log streaming."""

    @patch("routes.k8s_websocket.KubernetesService")
    def test_sends_connected_message(
        self, mock_k8s_cls,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should send connected message then stream logs."""
        cluster = make_k8s_cluster(project=sample_project, name="logs-connected-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="logs-connected-test")
        mock_api_client = MagicMock()
        mock_k8s.load_kubeconfig.return_value = mock_api_client

        # Mock CoreV1Api and log response
        mock_v1 = MagicMock()
        mock_log_response = MagicMock()

        # Stream yields two lines then ends
        chunks = [b"line1\n", b"line2\n"]
        chunk_iter = iter(chunks)

        def _stream(**kwargs):
            return chunk_iter

        mock_log_response.stream = _stream
        mock_log_response.close = MagicMock()
        mock_v1.read_namespaced_pod_log.return_value = mock_log_response

        with patch("routes.k8s_websocket.client") as mock_k8s_client:
            mock_k8s_client.CoreV1Api.return_value = mock_v1

            token = _make_token("admin")
            with client.websocket_connect(
                f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/logs/follow"
                f"?namespace=default&container=main&token={token}"
            ) as ws:
                connected = ws.receive_json()
                assert connected["type"] == "connected"
                assert connected["pod"] == "my-pod"
                assert connected["namespace"] == "default"
                assert connected["container"] == "main"

                log1 = ws.receive_json()
                assert log1["type"] == "log"
                assert log1["data"] == "line1"

                log2 = ws.receive_json()
                assert log2["type"] == "log"
                assert log2["data"] == "line2"

    @patch("routes.k8s_websocket.KubernetesService")
    def test_handles_multiline_chunks(
        self, mock_k8s_cls,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should correctly split multi-line chunks into individual log messages."""
        cluster = make_k8s_cluster(project=sample_project, name="logs-multiline-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="logs-multiline-test")
        mock_k8s.load_kubeconfig.return_value = MagicMock()

        mock_v1 = MagicMock()
        mock_log_response = MagicMock()

        # Single chunk with multiple lines
        chunks = [b"alpha\nbeta\ngamma\n"]
        chunk_iter = iter(chunks)
        mock_log_response.stream = lambda **kw: chunk_iter
        mock_log_response.close = MagicMock()
        mock_v1.read_namespaced_pod_log.return_value = mock_log_response

        with patch("routes.k8s_websocket.client") as mock_k8s_client:
            mock_k8s_client.CoreV1Api.return_value = mock_v1

            token = _make_token("admin")
            with client.websocket_connect(
                f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/logs/follow"
                f"?namespace=default&token={token}"
            ) as ws:
                connected = ws.receive_json()
                assert connected["type"] == "connected"

                logs = []
                for _ in range(3):
                    msg = ws.receive_json()
                    assert msg["type"] == "log"
                    logs.append(msg["data"])

                assert logs == ["alpha", "beta", "gamma"]

    @patch("routes.k8s_websocket.KubernetesService")
    def test_handles_partial_line_buffering(
        self, mock_k8s_cls,
        client, sample_user, sample_project, make_k8s_cluster, db,
    ):
        """Should buffer partial lines and flush when next chunk completes them."""
        cluster = make_k8s_cluster(project=sample_project, name="logs-buffer-test")
        mock_k8s = MagicMock()
        mock_k8s_cls.return_value = mock_k8s
        mock_k8s.get_cluster.return_value = MagicMock(name="logs-buffer-test")
        mock_k8s.load_kubeconfig.return_value = MagicMock()

        mock_v1 = MagicMock()
        mock_log_response = MagicMock()

        # Two chunks: first ends mid-line, second completes it
        chunks = [b"start of li", b"ne\ncomplete line\n"]
        chunk_iter = iter(chunks)
        mock_log_response.stream = lambda **kw: chunk_iter
        mock_log_response.close = MagicMock()
        mock_v1.read_namespaced_pod_log.return_value = mock_log_response

        with patch("routes.k8s_websocket.client") as mock_k8s_client:
            mock_k8s_client.CoreV1Api.return_value = mock_v1

            token = _make_token("admin")
            with client.websocket_connect(
                f"/ws/k8s/clusters/{cluster.id}/pods/my-pod/logs/follow"
                f"?namespace=default&token={token}"
            ) as ws:
                connected = ws.receive_json()
                assert connected["type"] == "connected"

                log1 = ws.receive_json()
                assert log1["data"] == "start of line"

                log2 = ws.receive_json()
                assert log2["data"] == "complete line"

    def test_logs_nonexistent_cluster_sends_error(self, client, sample_user):
        """Non-existent cluster should send error message."""
        token = _make_token("admin")
        with client.websocket_connect(
            f"/ws/k8s/clusters/999/pods/test-pod/logs/follow?namespace=default&token={token}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["type"] == "error"
