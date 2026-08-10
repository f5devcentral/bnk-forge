"""
Unit tests for the LLM metrics ingest + read service.

Two core scenarios required by the task:
  (a) Unlinked operator → snapshot dropped, no exception.
  (b) Operator snapshot present in Redis → get_backend_health returns it verbatim
      (with added source hint).

All external deps (Redis, DB) are mocked.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_backend_health_data(namespace: str = "default", name: str = "my-model") -> dict:
    """Minimal valid BackendHealthShape as the operator would push."""
    return {
        "available": True,
        "backends": [
            {
                "pod_name": "my-model-pod-0",
                "namespace": namespace,
                "backend_kind": "vllm",
                "model": "llama3",
                "status": "healthy",
                "kv_cache_used_pct": 42,
                "running": 3,
                "waiting": 0,
                "ttft_p95_ms": 180.5,
                "itl_p95_ms": 12.3,
                "output_tps": 88,
                "gpu_util_pct": None,
                "vram_used_gb": None,
                "vram_total_gb": None,
                "gpu_temp_c": None,
            }
        ],
        "errors": {},
        "updated_at": "2026-05-25T10:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# store_llm_metrics tests
# ---------------------------------------------------------------------------

class TestStoreLlmMetrics:
    """Tests for store_llm_metrics."""

    def test_unlinked_operator_drops_snapshot_no_exception(self):
        """If operator.cluster_id is None, snapshot is dropped silently."""
        db = MagicMock()
        mock_op = MagicMock()
        mock_op.cluster_id = None
        db.query.return_value.filter.return_value.first.return_value = mock_op

        with patch("services.llm_metrics_service._get_redis") as mock_redis:
            from services.llm_metrics_service import store_llm_metrics

            result = store_llm_metrics(db, "op-abc", "ns", "analyzer", {"available": True})

        assert result is False
        mock_redis.assert_not_called()  # Redis never touched for unlinked operator

    def test_operator_not_found_drops_snapshot(self):
        """If ConnectedOperator record missing, treat as unlinked."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with patch("services.llm_metrics_service._get_redis") as mock_redis:
            from services.llm_metrics_service import store_llm_metrics

            result = store_llm_metrics(db, "op-unknown", "ns", "analyzer", {})

        assert result is False
        mock_redis.assert_not_called()

    def test_linked_operator_stores_in_redis(self):
        """Linked operator (cluster_id set) writes to Redis with correct key + TTL."""
        db = MagicMock()
        mock_op = MagicMock()
        mock_op.cluster_id = 7
        db.query.return_value.filter.return_value.first.return_value = mock_op

        mock_redis = MagicMock()
        data = _make_backend_health_data()

        with patch("services.llm_metrics_service._get_redis", return_value=mock_redis):
            from services.llm_metrics_service import store_llm_metrics

            result = store_llm_metrics(db, "op-xyz", "prod", "llama3-analyzer", data)

        assert result is True
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        key, ttl, payload = call_args[0]
        assert key == "llm_metrics:7:prod:llama3-analyzer"
        assert ttl == 60
        stored = json.loads(payload)
        assert stored["available"] is True
        assert len(stored["backends"]) == 1

    def test_redis_unavailable_returns_false(self):
        """If Redis is None, store gracefully returns False."""
        db = MagicMock()
        mock_op = MagicMock()
        mock_op.cluster_id = 5
        db.query.return_value.filter.return_value.first.return_value = mock_op

        with patch("services.llm_metrics_service._get_redis", return_value=None):
            from services.llm_metrics_service import store_llm_metrics

            result = store_llm_metrics(db, "op-xyz", "ns", "name", {})

        assert result is False


# ---------------------------------------------------------------------------
# get_llm_metrics_snapshot tests
# ---------------------------------------------------------------------------

class TestGetLlmMetricsSnapshot:
    """Tests for get_llm_metrics_snapshot."""

    def test_returns_none_when_redis_unavailable(self):
        """No Redis → returns None, falls through to Celery."""
        with patch("services.llm_metrics_service._get_redis", return_value=None):
            from services.llm_metrics_service import get_llm_metrics_snapshot

            result = get_llm_metrics_snapshot(1, "ns", "name")

        assert result is None

    def test_returns_none_when_key_missing(self):
        """Key not in Redis (TTL expired) → None → Celery fallback."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = None

        with patch("services.llm_metrics_service._get_redis", return_value=mock_redis):
            from services.llm_metrics_service import get_llm_metrics_snapshot

            result = get_llm_metrics_snapshot(1, "ns", "name")

        assert result is None
        mock_redis.get.assert_called_once_with("llm_metrics:1:ns:name")

    def test_returns_snapshot_with_source_hint(self):
        """When key present, returns parsed JSON with source='operator'."""
        data = _make_backend_health_data()
        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(data)

        with patch("services.llm_metrics_service._get_redis", return_value=mock_redis):
            from services.llm_metrics_service import get_llm_metrics_snapshot

            result = get_llm_metrics_snapshot(7, "prod", "llama3-analyzer")

        assert result is not None
        assert result["available"] is True
        assert len(result["backends"]) == 1
        assert result["source"] == "operator"
        # All 14 required backend keys present
        pod = result["backends"][0]
        required_keys = {
            "pod_name", "namespace", "backend_kind", "model", "status",
            "kv_cache_used_pct", "running", "waiting", "ttft_p95_ms",
            "itl_p95_ms", "output_tps", "gpu_util_pct", "vram_used_gb",
            "vram_total_gb",
        }
        # gpu_temp_c is present in the data but not in the 14-key spec; include it
        assert required_keys.issubset(set(pod.keys()))


# ---------------------------------------------------------------------------
# Integration: get_backend_health prefers operator snapshot
# ---------------------------------------------------------------------------

class TestGetBackendHealthPrefersOperator:
    """Verify that backend_health_service.get_backend_health early-returns operator data."""

    def test_operator_snapshot_returned_without_celery_dispatch(self):
        """When llm_metrics key is present in Redis, Celery task is NOT called."""
        data = _make_backend_health_data("prod", "my-analyzer")

        mock_redis = MagicMock()
        mock_redis.get.return_value = json.dumps(data)

        db = MagicMock()

        with (
            patch("services.llm_metrics_service._get_redis", return_value=mock_redis),
            patch("services.backend_health_service._get_redis", return_value=mock_redis),
            patch("tasks.backend_health_task.fetch_backend_health") as mock_task,
        ):
            # Also stub the Celery cache miss (backend_health key absent)
            def redis_get_side_effect(key: str):
                if key.startswith("llm_metrics:"):
                    return json.dumps(data)
                return None  # backend_health: key absent — would trigger Celery

            mock_redis.get.side_effect = redis_get_side_effect

            from services.backend_health_service import get_backend_health

            result = get_backend_health(db, 7, "prod", "my-analyzer")

        assert result["available"] is True
        assert result["source"] == "operator"
        mock_task.apply_async.assert_not_called()

    def test_celery_fallback_when_no_operator_snapshot(self):
        """When llm_metrics key absent, falls through to Celery dispatch."""
        celery_result = {
            "available": True,
            "backends": [],
            "errors": {},
            "updated_at": "2026-05-25T10:00:00+00:00",
        }

        mock_redis = MagicMock()
        mock_redis.get.return_value = None  # No key for either prefix

        db = MagicMock()

        mock_async_result = MagicMock()
        mock_async_result.get.return_value = celery_result

        with (
            patch("services.llm_metrics_service._get_redis", return_value=mock_redis),
            patch("services.backend_health_service._get_redis", return_value=mock_redis),
            patch("tasks.backend_health_task.fetch_backend_health") as mock_task,
        ):
            mock_task.apply_async.return_value = mock_async_result

            from services.backend_health_service import get_backend_health

            result = get_backend_health(db, 1, "ns", "name")

        mock_task.apply_async.assert_called_once()
        assert result["available"] is True
        assert result.get("source") != "operator"
