"""
Unit tests for MCP benchmark tools.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("mcp", reason="mcp package not installed in this test environment")

from bnk_forge_mcp.tools.benchmarks import register


class _FakeToolManager:
    def __init__(self):
        self.tools = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator


@pytest.mark.asyncio
async def test_list_benchmark_targets_calls_api():
    mcp = _FakeToolManager()
    client = AsyncMock()
    client.get.return_value = {
        "targets": [
            {"id": 1, "name": "mcp-route", "cluster_id": 10, "cluster_name": "cluster-a"},
            {"id": 2, "name": "mcp-route", "cluster_id": 20, "cluster_name": "cluster-b"},
        ],
        "total": 2,
    }

    register(mcp, client)
    list_tool = mcp.tools["list_benchmark_targets"]

    res_str = await list_tool(cluster_id=10, name="mcp-route")
    res = json.loads(res_str)

    assert res["total"] == 2
    assert len(res["targets"]) == 2
    client.get.assert_awaited_once_with(
        "/api/benchmarks/targets",
        params={"cluster_id": 10, "name": "mcp-route"},
    )


@pytest.mark.asyncio
async def test_create_benchmark_target_calls_api():
    mcp = _FakeToolManager()
    client = AsyncMock()
    client.post.return_value = {
        "id": 1,
        "name": "mcp-route",
        "cluster_id": 10,
        "llm_base_url": "http://vllm.default:8000",
        "llm_model": "llama3",
    }

    register(mcp, client)
    create_tool = mcp.tools["create_benchmark_target"]

    res_str = await create_tool(
        cluster_id=10,
        name="mcp-route",
        llm_base_url="http://vllm.default:8000",
        llm_model="llama3",
        description="Test target",
    )
    res = json.loads(res_str)

    assert res["id"] == 1
    assert res["name"] == "mcp-route"
    assert res["cluster_id"] == 10
    client.post.assert_awaited_once_with(
        "/api/benchmarks/targets",
        json={
            "cluster_id": 10,
            "name": "mcp-route",
            "llm_base_url": "http://vllm.default:8000",
            "llm_model": "llama3",
            "llm_namespace": "default",
            "llm_endpoint": "/v1/chat/completions",
            "proxy_namespace": "perf-proxies",
            "description": "Test target",
        },
    )
