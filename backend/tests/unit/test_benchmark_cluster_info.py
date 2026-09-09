"""
Unit tests for benchmark cluster_name serialization and cluster_id filtering.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock
from models.benchmark import BenchmarkTarget, BenchmarkRun, BenchmarkRunGroup
from schemas.benchmarks import BenchmarkTargetResponse, BenchmarkRunResponse, RunGroupSummary


class TestBenchmarkClusterInfo:
    def test_target_cluster_name_property_and_serialization(self):
        mock_cluster = MagicMock()
        mock_cluster.name = "bnk-singapore"

        now = datetime.now(timezone.utc)
        target = BenchmarkTarget(
            id=1,
            name="vllm-awsbnkctl",
            description="Test target",
            cluster_id=42,
            llm_base_url="http://vllm.default:8000",
            llm_model="meta-llama/Llama-3-8b",
            llm_namespace="default",
            llm_endpoint="/v1/chat/completions",
            proxy_namespace="perf-proxies",
            status="active",
        )
        target.cluster = mock_cluster
        target.proxy_deployments = []
        target.created_at = now
        target.updated_at = now
        target.last_validated = None
        target.validation_msg = None
        target.tags = None

        assert target.cluster_name == "bnk-singapore"

        # Test Pydantic schema serialization
        response = BenchmarkTargetResponse.model_validate(target)
        assert response.cluster_id == 42
        assert response.cluster_name == "bnk-singapore"

    def test_target_without_cluster_returns_none(self):
        target = BenchmarkTarget(
            id=2,
            name="orphan-target",
            cluster_id=99,
            llm_base_url="http://vllm.default:8000",
            llm_model="test-model",
            llm_namespace="default",
            llm_endpoint="/v1/chat/completions",
            proxy_namespace="perf-proxies",
            status="active",
        )
        target.cluster = None
        assert target.cluster_name is None

    def test_run_cluster_name_property(self):
        mock_cluster = MagicMock()
        mock_cluster.name = "bnk-tokyo"

        mock_target = MagicMock()
        mock_target.cluster = mock_cluster

        run = BenchmarkRun(
            id=10,
            tool="aiperf",
            proxy="haproxy",
            model="llama-3",
            base_url="http://10.10.1.229:30267",
            status="completed",
        )
        run.target = mock_target

        assert run.cluster_name == "bnk-tokyo"

    def test_run_group_cluster_name_property(self):
        mock_cluster = MagicMock()
        mock_cluster.name = "bnk-us-east"

        mock_target = MagicMock()
        mock_target.cluster = mock_cluster

        group = BenchmarkRunGroup(
            id=5,
            scenario_key="prefix-cache",
            scenario_name="Prefix Cache",
            status="completed",
        )
        group.target = mock_target

        assert group.cluster_name == "bnk-us-east"
