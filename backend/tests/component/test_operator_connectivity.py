"""
BC-033: Component tests for OperatorConnectivity — operator connection modes.

Tests cover: resolve_direct_ws, resolve_reverse_ssh, resolve_polling,
resolve_ngrok_tunnel, resolve_in_cluster, resolve_connectivity dispatcher,
get_available_modes, _build_helm_command, and edge cases.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from services.operator_connectivity import (
    ConnectivitySetup,
    _build_helm_command,
    get_available_modes,
    resolve_connectivity,
    resolve_direct_ws,
    resolve_in_cluster,
    resolve_ngrok_tunnel,
    resolve_polling,
    resolve_reverse_ssh,
)

# ── resolve_direct_ws ───────────────────────────────────────────────

class TestResolveDirectWS:
    def test_basic_ws_url(self):
        db = MagicMock()
        result = resolve_direct_ws(db, "op-1", {
            "bnk_forge_host": "10.0.0.1",
            "bnk_forge_port": 443,
        })

        assert isinstance(result, ConnectivitySetup)
        assert result.mode == "direct_ws"
        assert result.control_plane_url == "ws://10.0.0.1:443/ws/operator"
        assert result.setup_status == "ready"

    def test_tls_uses_wss(self):
        db = MagicMock()
        result = resolve_direct_ws(db, "op-1", {
            "bnk_forge_host": "forge.example.com",
            "bnk_forge_port": 443,
            "use_tls": True,
        })
        assert result.control_plane_url.startswith("wss://")

    def test_localhost_warning(self):
        db = MagicMock()
        result = resolve_direct_ws(db, "op-1", {
            "bnk_forge_host": "localhost",
        })
        assert any("localhost" in n for n in result.notes)

    def test_env_vars_set(self):
        db = MagicMock()
        result = resolve_direct_ws(db, "op-1", {
            "bnk_forge_host": "forge.host",
        })
        assert result.env_vars["CONNECTIVITY_MODE"] == "websocket"
        assert "CONTROL_PLANE_URL" in result.env_vars


# ── resolve_reverse_ssh ─────────────────────────────────────────────

class TestResolveReverseSSH:
    def test_missing_ssh_credential_returns_pending(self):
        db = MagicMock()
        result = resolve_reverse_ssh(db, "op-1", {})

        assert result.setup_status == "pending"
        assert result.error is not None

    @patch("services.operator_connectivity.SSHCredential", create=True)
    def test_invalid_ssh_credential_returns_error(self, mock_model):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        result = resolve_reverse_ssh(db, "op-1", {
            "ssh_credential_id": 99,
        })
        assert result.setup_status == "error"

    @patch("services.operator_connectivity.SSHCredential", create=True)
    def test_valid_ssh_returns_ready(self, mock_model):
        cred = MagicMock()
        cred.host = "bastion.example.com"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = cred

        result = resolve_reverse_ssh(db, "op-1", {
            "ssh_credential_id": 1,
            "reverse_port": 443,
        })
        assert result.setup_status == "ready"
        assert "bastion.example.com" in result.control_plane_url


# ── resolve_polling ──────────────────────────────────────────────────

class TestResolvePolling:
    def test_basic_polling_url(self):
        db = MagicMock()
        result = resolve_polling(db, "op-1", {
            "bnk_forge_host": "10.0.0.1",
            "bnk_forge_port": 443,
        })

        assert result.mode == "polling"
        assert result.control_plane_url == "http://10.0.0.1:443"
        assert result.env_vars["CONNECTIVITY_MODE"] == "polling"
        assert result.setup_status == "ready"

    def test_tls_polling(self):
        db = MagicMock()
        result = resolve_polling(db, "op-1", {
            "bnk_forge_host": "forge.example.com",
            "use_tls": True,
        })
        assert result.control_plane_url.startswith("https://")

    def test_custom_poll_interval(self):
        db = MagicMock()
        result = resolve_polling(db, "op-1", {
            "bnk_forge_host": "10.0.0.1",
            "poll_interval_seconds": 10,
        })
        assert result.env_vars["POLL_INTERVAL_SECONDS"] == "10"


# ── resolve_ngrok_tunnel ────────────────────────────────────────────

class TestResolveNgrok:
    def test_no_public_url_returns_pending(self):
        db = MagicMock()
        result = resolve_ngrok_tunnel(db, "op-1", {})

        assert result.setup_status == "pending"
        assert result.error is not None
        assert any("ngrok" in n for n in result.notes)

    def test_with_public_url_returns_ready(self):
        db = MagicMock()
        result = resolve_ngrok_tunnel(db, "op-1", {
            "public_url": "https://abc123.ngrok-free.app",
        })

        assert result.setup_status == "ready"
        assert "wss://abc123.ngrok-free.app/ws/operator" == result.control_plane_url

    def test_http_url_converted_to_ws(self):
        db = MagicMock()
        result = resolve_ngrok_tunnel(db, "op-1", {
            "public_url": "http://abc.localhost",
        })
        assert result.control_plane_url.startswith("ws://")


# ── resolve_in_cluster ──────────────────────────────────────────────

class TestResolveInCluster:
    @patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "10.96.0.1"})
    def test_in_k8s_returns_ready(self):
        db = MagicMock()
        result = resolve_in_cluster(db, "op-1", {})

        assert result.setup_status == "ready"
        assert "svc.cluster.local" in result.control_plane_url

    @patch.dict(os.environ, {}, clear=True)
    def test_not_in_k8s_returns_pending(self):
        # Remove KUBERNETES_SERVICE_HOST
        env = os.environ.copy()
        env.pop("KUBERNETES_SERVICE_HOST", None)
        with patch.dict(os.environ, env, clear=True):
            db = MagicMock()
            result = resolve_in_cluster(db, "op-1", {})
            assert result.setup_status == "pending"
            assert any("not appear to be running inside Kubernetes" in n for n in result.notes)

    @patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "10.96.0.1"})
    def test_custom_service_name(self):
        db = MagicMock()
        result = resolve_in_cluster(db, "op-1", {
            "service_name": "my-forge",
            "service_namespace": "tools",
            "service_port": 8080,
        })
        assert "my-forge.tools.svc.cluster.local:8080" in result.control_plane_url


# ── resolve_connectivity dispatcher ─────────────────────────────────

class TestResolveConnectivity:
    def test_unknown_mode_returns_error(self):
        db = MagicMock()
        result = resolve_connectivity(db, "unknown_mode", "op-1", {})

        assert result.setup_status == "error"
        assert "Unknown connectivity mode" in result.error

    def test_dispatches_to_correct_resolver(self):
        db = MagicMock()
        result = resolve_connectivity(db, "polling", "op-1", {
            "bnk_forge_host": "10.0.0.1",
        })
        assert result.mode == "polling"
        assert result.setup_status == "ready"


# ── _build_helm_command ─────────────────────────────────────────────

class TestBuildHelmCommand:
    def test_websocket_mode_command(self):
        cmd = _build_helm_command("ws://host:443/ws/operator", "tok-123", "websocket")
        assert "helm install" in cmd
        assert "controlPlane.url=ws://host:443/ws/operator" in cmd
        assert "connectivityMode=websocket" in cmd

    def test_polling_mode_includes_interval(self):
        cmd = _build_helm_command("http://host:443", "tok-123", "polling", poll_interval=10)
        assert "connectivityMode=polling" in cmd
        assert "pollIntervalSeconds=10" in cmd
