"""
BC-C51: Component tests for OperatorEngine.

Tests the K8s Operator execution engine: init, plan, apply, destroy,
health_check, command dispatch (WS and polling), and _build_registry_auth.
All external connections (WebSocket, DB, ServiceRegistry) are mocked.
"""

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from services.execution.engine_interface import ModuleContext, OperationResult, PlanResult
from services.execution.operator_engine import OperatorEngine

# ── Helpers ──────────────────────────────────────────────────────────

def _make_ctx(**overrides):
    defaults = dict(
        module_id=1, project_id=1, path="bnk/flo",
        category="bnk", variables={"version": "1.0"},
        credentials_env={},
        module_source_kind="git_catalog",
        deploy_model="manifests",
        workspace_path="/work",
        pack_manifest={
            "deployment_pack": {
                "working_directory": ".",
                "entrypoints": {"manifest_path": "manifest.yaml"},
            }
        },
    )
    defaults.update(overrides)
    return ModuleContext(**defaults)


# ── Health Check ─────────────────────────────────────────────────────

class TestHealthCheck:
    @patch("services.execution.operator_engine.operator_connections")
    def test_ws_mode_connected(self, mock_conns):
        mock_conns.is_connected.return_value = True
        engine = OperatorEngine("op-1", connectivity_mode="direct_ws")
        assert engine.health_check() is True

    @patch("services.execution.operator_engine.operator_connections")
    def test_ws_mode_disconnected(self, mock_conns):
        mock_conns.is_connected.return_value = False
        engine = OperatorEngine("op-1", connectivity_mode="direct_ws")
        assert engine.health_check() is False

    @patch("services.execution.operator_engine.operator_connections")
    def test_polling_mode_healthy(self, mock_conns):
        from datetime import UTC, datetime

        mock_operator = MagicMock()
        mock_operator.is_connected = True
        mock_operator.last_heartbeat_at = datetime.now(UTC)

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_operator

        with patch("database.SessionLocal", return_value=mock_db):
            engine = OperatorEngine("op-1", connectivity_mode="polling")
            assert engine.health_check() is True

    @patch("services.execution.operator_engine.operator_connections")
    def test_polling_mode_no_operator_record(self, mock_conns):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("database.SessionLocal", return_value=mock_db):
            engine = OperatorEngine("op-1", connectivity_mode="polling")
            assert engine.health_check() is False


# ── Init ─────────────────────────────────────────────────────────────

class TestInit:
    @patch("services.execution.operator_engine.operator_connections")
    def test_init_not_connected(self, mock_conns):
        mock_conns.is_connected.return_value = False
        engine = OperatorEngine("op-1")
        result = engine.init(_make_ctx())
        assert result.success is False
        assert "not connected" in result.error_message

    @patch.object(OperatorEngine, "_send_command")
    @patch("services.execution.operator_engine.operator_connections")
    def test_init_connected_healthy(self, mock_conns, mock_send):
        mock_conns.is_connected.return_value = True
        mock_send.return_value = {
            "success": True,
            "cluster": {"kubernetes_version": "1.28", "nodes_ready": 3, "node_count": 3},
        }
        engine = OperatorEngine("op-1")
        result = engine.init(_make_ctx())
        assert result.success is True

    @patch.object(OperatorEngine, "_send_command")
    @patch("services.execution.operator_engine.operator_connections")
    def test_init_polling_mode_does_not_require_ws_connection(self, mock_conns, mock_send):
        mock_conns.is_connected.return_value = False
        mock_send.return_value = {"success": True, "cluster": {"kubernetes_version": "1.28"}}
        engine = OperatorEngine("op-1", connectivity_mode="polling")
        result = engine.init(_make_ctx())
        assert result.success is True

    @patch.object(OperatorEngine, "_send_command")
    @patch("services.execution.operator_engine.operator_connections")
    def test_init_command_raises(self, mock_conns, mock_send):
        mock_conns.is_connected.return_value = True
        mock_send.side_effect = RuntimeError("WS down")
        engine = OperatorEngine("op-1")
        result = engine.init(_make_ctx())
        assert result.success is False
        assert "WS down" in result.error_message


# ── Plan ─────────────────────────────────────────────────────────────

class TestPlan:
    def test_plan_helm_chart(self):
        engine = OperatorEngine("op-1")
        result = engine.plan(
            _make_ctx(
                deploy_model="helm",
                pack_manifest={
                    "deployment_pack": {
                        "working_directory": ".",
                        "entrypoints": {
                            "chart_ref": "oci://repo.f5.com/charts/flo",
                            "release_name": "flo",
                            "namespace": "f5-bnk",
                        },
                    }
                },
            )
        )
        assert result.has_changes is True
        assert result.adds == 1

    @patch("services.execution.k8s_catalog_payload._load_manifest_documents")
    def test_plan_manifests(self, mock_load_docs, tmp_path):
        mock_load_docs.return_value = [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cfg"}}]
        engine = OperatorEngine("op-1")
        result = engine.plan(_make_ctx(workspace_path=str(tmp_path)))
        assert result.has_changes is True
        assert result.adds == 1


# ── Apply ────────────────────────────────────────────────────────────

class TestApply:
    @patch.object(OperatorEngine, "_send_command")
    def test_apply_helm_success(self, mock_send):
        mock_send.return_value = {
            "success": True,
            "output_lines": ["installed"],
            "resources_created": 5,
            "outputs": {},
        }
        engine = OperatorEngine("op-1")
        result = engine.apply(
            _make_ctx(
                deploy_model="helm",
                pack_manifest={
                    "deployment_pack": {
                        "working_directory": ".",
                        "entrypoints": {
                            "chart_ref": "oci://repo.f5.com/charts/flo",
                            "release_name": "flo",
                            "namespace": "f5-bnk",
                        },
                    }
                },
            )
        )
        assert result.success is True
        assert result.resources_created == 5

    @patch.object(OperatorEngine, "_send_command")
    @patch("services.execution.k8s_catalog_payload._load_manifest_documents")
    def test_apply_manifests_success(self, mock_load_docs, mock_send, tmp_path):
        mock_load_docs.return_value = [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cfg"}}]
        mock_send.return_value = {
            "success": True,
            "output_lines": ["applied"],
            "resources_created": 1,
            "outputs": {},
        }
        engine = OperatorEngine("op-1")
        result = engine.apply(_make_ctx(workspace_path=str(tmp_path)))
        assert result.success is True

    @patch.object(OperatorEngine, "_send_command", side_effect=TimeoutError("timed out"))
    def test_apply_timeout_returns_failure(self, mock_send):
        engine = OperatorEngine("op-1")
        result = engine.apply(
            _make_ctx(
                deploy_model="helm",
                pack_manifest={
                    "deployment_pack": {
                        "working_directory": ".",
                        "entrypoints": {
                            "chart_ref": "oci://repo.f5.com/charts/flo",
                            "release_name": "flo",
                            "namespace": "f5-bnk",
                        },
                    }
                },
            )
        )
        assert result.success is False
        assert "timed out" in result.error_message


# ── Destroy ──────────────────────────────────────────────────────────

class TestDestroy:
    @patch.object(OperatorEngine, "_send_command")
    def test_destroy_helm_success(self, mock_send):
        mock_send.return_value = {
            "success": True,
            "output_lines": ["uninstalled"],
            "resources_destroyed": 5,
        }
        engine = OperatorEngine("op-1")
        result = engine.destroy(
            _make_ctx(
                deploy_model="helm",
                pack_manifest={
                    "deployment_pack": {
                        "working_directory": ".",
                        "entrypoints": {
                            "chart_ref": "oci://repo.f5.com/charts/flo",
                            "release_name": "flo",
                            "namespace": "f5-bnk",
                        },
                    }
                },
            )
        )
        assert result.success is True
        assert result.resources_destroyed == 5


class TestCatalogBackedExecution:
    @patch.object(OperatorEngine, "_send_command")
    def test_apply_catalog_manifests_without_python_module(self, mock_send, tmp_path):
        mock_send.return_value = {"success": True, "output_lines": ["applied"], "resources_created": 1}
        engine = OperatorEngine("op-1")

        ctx = _make_ctx(
            path="k8s/catalog-manifest",
            module_source_kind="git_catalog",
            deploy_model="manifests",
            workspace_path=str(tmp_path),
            pack_manifest={
                "deployment_pack": {
                    "working_directory": ".",
                    "entrypoints": {"manifest_path": "manifest.yaml"},
                }
            },
            variables={"namespace": "apps", "name": "cfg"},
        )

        with patch("services.execution.k8s_catalog_payload._load_manifest_documents") as mock_load_docs:
            mock_load_docs.return_value = [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "${name}"}}]
            result = engine.apply(ctx)

        assert result.success is True
        assert mock_send.call_args.kwargs["action"] == "apply_manifests"
        sent_payload = mock_send.call_args.kwargs["payload"]
        assert sent_payload["manifests"][0]["metadata"]["name"] == "cfg"

    @patch.object(OperatorEngine, "_send_command")
    def test_destroy_catalog_helm_without_python_module(self, mock_send):
        mock_send.return_value = {"success": True, "output_lines": ["deleted"], "resources_destroyed": 1}
        engine = OperatorEngine("op-1")
        ctx = _make_ctx(
            path="k8s/catalog-helm",
            module_source_kind="git_catalog",
            deploy_model="helm",
            workspace_path="/work",
            pack_manifest={
                "deployment_pack": {
                    "working_directory": ".",
                    "entrypoints": {
                        "chart_ref": "oci://repo.f5.com/charts/flo",
                        "release_name": "flo",
                        "namespace": "f5-bnk",
                    },
                }
            },
        )

        result = engine.destroy(ctx)
        assert result.success is True
        assert mock_send.call_args.kwargs["action"] == "uninstall_helm"
        assert mock_send.call_args.kwargs["payload"]["release_name"] == "flo"


# ── Build Registry Auth ──────────────────────────────────────────────

class TestBuildRegistryAuth:
    def test_extracts_auth_from_pull_secret(self):
        docker_config = {
            "auths": {
                "repo.f5.com": {
                    "username": "user1",
                    "password": "pass1",
                }
            }
        }
        encoded = base64.b64encode(json.dumps(docker_config).encode()).decode()

        engine = OperatorEngine("op-1")
        result = engine._build_registry_auth({"cne_pull_secret": encoded})
        assert result is not None
        assert result["username"] == "user1"
        assert result["password"] == "pass1"

    def test_returns_none_when_no_pull_secret(self):
        engine = OperatorEngine("op-1")
        result = engine._build_registry_auth({"AWS_KEY": "abc"})
        assert result is None
