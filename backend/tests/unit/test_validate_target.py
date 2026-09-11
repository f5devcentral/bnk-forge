"""
Unit tests for ``validate_target`` in BenchmarkTargetService.
"""

from unittest.mock import MagicMock, patch

import pytest

from models.enums import BenchmarkTargetStatus
from services.benchmark_target_service import BenchmarkTargetService


def _target(
    target_id: int = 1,
    llm_base_url: str = "http://vllm.awsbnkctl-scn-aiinference:80",
    cluster_id: int | None = 2,
    llm_namespace: str = "awsbnkctl-scn-aiinference",
) -> MagicMock:
    target = MagicMock()
    target.id = target_id
    target.name = "vllm-test"
    target.llm_base_url = llm_base_url
    target.cluster_id = cluster_id
    target.llm_namespace = llm_namespace
    target.cluster = MagicMock()
    target.status = BenchmarkTargetStatus.VALIDATING
    target.validation_msg = None
    target.last_validated = None
    target.updated_at = None
    return target


class TestValidateTarget:
    @patch("requests.get")
    def test_http_probe_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        db = MagicMock()
        target = _target()
        query_mock = db.query.return_value
        query_mock.options.return_value = query_mock
        query_mock.filter.return_value.first.return_value = target

        svc = BenchmarkTargetService(db)
        result = svc.validate_target(1)

        assert result.status == BenchmarkTargetStatus.ACTIVE
        assert "HTTP 200" in result.validation_msg

    @patch("socket.create_connection")
    @patch("requests.get")
    def test_tcp_probe_success(self, mock_get, mock_conn):
        mock_get.side_effect = Exception("Connection refused")
        mock_sock = MagicMock()
        mock_conn.return_value = mock_sock

        db = MagicMock()
        target = _target()
        query_mock = db.query.return_value
        query_mock.options.return_value = query_mock
        query_mock.filter.return_value.first.return_value = target

        svc = BenchmarkTargetService(db)
        result = svc.validate_target(1)

        assert result.status == BenchmarkTargetStatus.ACTIVE
        assert "TCP connect OK" in result.validation_msg

    @patch("kubernetes.client.CoreV1Api")
    @patch("services.kubernetes.KubernetesService.load_kubeconfig")
    @patch("socket.create_connection")
    @patch("requests.get")
    def test_k8s_fallback_success_with_ready_pods(
        self, mock_get, mock_conn, mock_load, mock_core_cls
    ):
        mock_get.side_effect = Exception("Connection refused")
        mock_conn.side_effect = OSError("Name or service not known")

        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        # Mock Service
        svc_mock = MagicMock()
        svc_mock.metadata.name = "vllm"
        svc_mock.metadata.namespace = "awsbnkctl-scn-aiinference"
        svc_mock.spec.selector = {"app": "vllm"}
        mock_core.read_namespaced_service.return_value = svc_mock

        # Mock Pod with Ready condition
        pod = MagicMock()
        pod.metadata.name = "vllm-pod-1"
        cond = MagicMock()
        cond.type = "Ready"
        cond.status = "True"
        pod.status.conditions = [cond]
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[pod])

        db = MagicMock()
        target = _target()
        query_mock = db.query.return_value
        query_mock.options.return_value = query_mock
        query_mock.filter.return_value.first.return_value = target

        svc = BenchmarkTargetService(db)
        result = svc.validate_target(1)

        assert result.status == BenchmarkTargetStatus.ACTIVE
        assert "1 ready pod(s)" in result.validation_msg

    @patch("kubernetes.client.CoreV1Api")
    @patch("services.kubernetes.KubernetesService.load_kubeconfig")
    @patch("socket.create_connection")
    @patch("requests.get")
    def test_k8s_fallback_fails_with_zero_ready_pods(
        self, mock_get, mock_conn, mock_load, mock_core_cls
    ):
        mock_get.side_effect = Exception("Connection refused")
        mock_conn.side_effect = OSError("Name or service not known")

        mock_core = MagicMock()
        mock_core_cls.return_value = mock_core

        svc_mock = MagicMock()
        svc_mock.spec.selector = {"app": "vllm"}
        mock_core.read_namespaced_service.return_value = svc_mock

        pod = MagicMock()
        pod.status.conditions = []
        mock_core.list_namespaced_pod.return_value = MagicMock(items=[pod])

        db = MagicMock()
        target = _target()
        query_mock = db.query.return_value
        query_mock.options.return_value = query_mock
        query_mock.filter.return_value.first.return_value = target

        svc = BenchmarkTargetService(db)
        result = svc.validate_target(1)

        assert result.status == BenchmarkTargetStatus.ERROR
        assert "0 ready pods" in result.validation_msg
