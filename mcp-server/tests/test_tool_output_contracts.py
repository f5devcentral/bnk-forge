"""Focused MCP output contract truthfulness tests for governed tools.

Slice 7 scope (bounded):
- system tools backed by typed Tier-1 routes
- cluster_management Tier-1 typed read/mutate tools
- selected helm typed routes commonly used by operators

These tests verify MCP tool outputs remain truthful passthroughs of backend
response shape (including structured error envelopes).
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp", reason="mcp package not installed in this test environment")

from bnk_forge_mcp.tools.cluster_management import register as register_cluster
from bnk_forge_mcp.tools.diagnostics_fleet import register as register_diagnostics
from bnk_forge_mcp.tools.helm import register as register_helm
from bnk_forge_mcp.tools.iac_operations import register as register_iac
from bnk_forge_mcp.tools.system import register as register_system


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorator


class _StubClient:
    def __init__(self, result: dict) -> None:
        self.result = result

    async def get(self, path: str, params=None):
        return self.result

    async def post(self, path: str, json=None, params=None):
        return self.result

    async def put(self, path: str, json=None, params=None):
        return self.result

    async def delete(self, path: str, params=None):
        return self.result


def _parse_tool_json(result: str) -> dict:
    parsed = json.loads(result)
    assert isinstance(parsed, dict)
    return parsed


@pytest.mark.asyncio
async def test_system_health_output_preserves_typed_contract_shape() -> None:
    payload = {
        "services": {
            "database": {"status": "healthy", "response_time_ms": 12.5, "error": None},
            "redis": {"status": "healthy", "response_time_ms": 4.2, "error": None},
        },
        "timestamp": "2026-03-27T17:00:00Z",
    }

    mcp = _FakeMCP()
    register_system(mcp, _StubClient(payload))

    parsed = _parse_tool_json(await mcp.tools["system_health"]())
    assert parsed == payload
    assert {"services", "timestamp"}.issubset(parsed.keys())


@pytest.mark.asyncio
async def test_system_settings_output_preserves_nested_settings_entries() -> None:
    payload = {
        "settings": {
            "general": [
                {
                    "key": "feature_x",
                    "value": "true",
                    "value_type": "bool",
                    "description": "feature toggle",
                    "is_encrypted": False,
                }
            ]
        }
    }

    mcp = _FakeMCP()
    register_system(mcp, _StubClient(payload))

    parsed = _parse_tool_json(await mcp.tools["system_settings"]())
    assert parsed == payload
    assert "settings" in parsed and "general" in parsed["settings"]


@pytest.mark.asyncio
async def test_cluster_detail_and_connectivity_outputs_remain_truthful() -> None:
    cluster_payload = {
        "id": 7,
        "name": "prod-cluster",
        "status": "active",
        "default_namespace": "default",
        "project_id": 3,
    }
    test_payload = {
        "success": True,
        "message": "Connection successful",
        "cluster_name": "prod-cluster",
        "version": "v1.30.1",
        "api_server": "https://10.0.0.1:6443",
        "cloud_provider": "on-prem",
        "region": "lab",
        "status_code": 200,
    }

    mcp = _FakeMCP()
    register_cluster(mcp, _StubClient(cluster_payload))
    parsed_cluster = _parse_tool_json(await mcp.tools["get_cluster"](cluster_id=7))
    assert parsed_cluster == cluster_payload
    assert {"id", "name", "status"}.issubset(parsed_cluster.keys())

    mcp2 = _FakeMCP()
    register_cluster(mcp2, _StubClient(test_payload))
    parsed_test = _parse_tool_json(await mcp2.tools["test_cluster_connectivity"](cluster_id=7))
    assert parsed_test == test_payload
    assert {"success", "message", "cluster_name"}.issubset(parsed_test.keys())


@pytest.mark.asyncio
async def test_cluster_resource_tools_preserve_envelope_fields() -> None:
    resource_list_payload = {
        "resources": [{"name": "nginx", "namespace": "default", "kind": "Deployment"}],
        "count": 1,
        "resource_type": "deployments",
        "namespace": "default",
        "cluster_id": 7,
    }
    describe_payload = {
        "name": "nginx",
        "namespace": "default",
        "kind": "Deployment",
        "metadata": {"name": "nginx"},
        "spec": {"replicas": 2},
        "status": {"readyReplicas": 2},
        "conditions": [{"type": "Available", "status": "True"}],
        "events": [],
        "relationships": {},
        "cluster_id": 7,
        "resource_type": "deployments",
        "resource_name": "nginx",
    }

    mcp = _FakeMCP()
    register_cluster(mcp, _StubClient(resource_list_payload))
    parsed_list = _parse_tool_json(
        await mcp.tools["list_resources"](cluster_id=7, resource_type="deployments", namespace="default")
    )
    assert parsed_list == resource_list_payload
    assert {"resources", "count", "resource_type", "cluster_id"}.issubset(parsed_list.keys())

    mcp2 = _FakeMCP()
    register_cluster(mcp2, _StubClient(describe_payload))
    parsed_describe = _parse_tool_json(
        await mcp2.tools["describe_resource"](
            cluster_id=7,
            resource_type="deployments",
            name="nginx",
            namespace="default",
        )
    )
    assert parsed_describe == describe_payload
    assert {"metadata", "spec", "status", "conditions", "events"}.issubset(parsed_describe.keys())


@pytest.mark.asyncio
async def test_cluster_logs_metrics_events_and_restart_outputs_remain_stable() -> None:
    pod_logs_payload = {
        "logs": "line-1\nline-2",
        "pod_name": "tmm-0",
        "namespace": "f5-bnk",
        "cluster_id": 7,
        "container": "tmm",
    }
    events_payload = {
        "events": [{"type": "Warning", "reason": "BackOff", "message": "retrying"}],
        "count": 1,
        "cluster_id": 7,
        "namespace": "f5-bnk",
        "resource_type": None,
        "resource_name": None,
        "event_type": "Warning",
    }
    node_top_payload = {
        "available": True,
        "metrics": [{"name": "node-a", "cpu_millicores": 500}],
        "error": None,
        "cluster_id": 7,
        "sort_by": "cpu",
    }
    pod_top_payload = {
        "available": True,
        "metrics": [{"name": "pod-a", "namespace": "f5-bnk", "cpu_millicores": 10}],
        "error": None,
        "cluster_id": 7,
        "namespace": "f5-bnk",
        "sort_by": None,
    }
    restart_payload = {
        "success": True,
        "message": "Pod deleted for restart",
        "pod_name": "tmm-0",
        "namespace": "f5-bnk",
        "cluster_id": 7,
    }

    for tool_name, kwargs, payload, required_keys in [
        ("get_pod_logs", {"cluster_id": 7, "pod_name": "tmm-0", "namespace": "f5-bnk"}, pod_logs_payload, {"logs", "pod_name", "namespace", "cluster_id"}),
        ("get_cluster_events", {"cluster_id": 7, "namespace": "f5-bnk", "limit": 50}, events_payload, {"events", "count", "cluster_id"}),
        ("get_node_metrics", {"cluster_id": 7}, node_top_payload, {"available", "metrics", "cluster_id"}),
        ("get_pod_metrics", {"cluster_id": 7, "namespace": "f5-bnk"}, pod_top_payload, {"available", "metrics", "cluster_id"}),
        ("restart_pod", {"cluster_id": 7, "pod_name": "tmm-0", "namespace": "f5-bnk"}, restart_payload, {"success", "message", "pod_name", "namespace", "cluster_id"}),
    ]:
        mcp = _FakeMCP()
        register_cluster(mcp, _StubClient(payload))
        parsed = _parse_tool_json(await mcp.tools[tool_name](**kwargs))
        assert parsed == payload
        assert required_keys.issubset(parsed.keys())


@pytest.mark.asyncio
async def test_selected_helm_outputs_preserve_backend_contract_shape() -> None:
    release_list_payload = {
        "success": True,
        "releases": [{"name": "bnk", "namespace": "f5-bnk", "status": "deployed", "revision": 3}],
        "count": 1,
    }
    release_detail_payload = {
        "success": True,
        "release": {"name": "bnk", "namespace": "f5-bnk", "status": "deployed"},
    }
    repo_list_payload = {
        "success": True,
        "repositories": [{"name": "bitnami", "url": "https://charts.bitnami.com/bitnami"}],
        "count": 1,
    }
    chart_search_payload = {
        "success": True,
        "charts": [{"name": "nginx", "version": "1.0.0", "repository": "bitnami"}],
        "count": 1,
    }
    install_payload = {
        "success": True,
        "message": "Chart bitnami/nginx installed as bnk",
        "result": {"release_name": "bnk", "status": "deployed"},
    }

    for tool_name, kwargs, payload, required_keys in [
        ("helm_list_releases", {"cluster_id": 7, "namespace": "f5-bnk"}, release_list_payload, {"success", "releases", "count"}),
        ("helm_get_release", {"cluster_id": 7, "release_name": "bnk", "namespace": "f5-bnk"}, release_detail_payload, {"success", "release"}),
        ("helm_list_repos", {}, repo_list_payload, {"success", "repositories", "count"}),
        ("helm_search_charts", {"keyword": "nginx"}, chart_search_payload, {"success", "charts", "count"}),
        (
            "helm_install",
            {"cluster_id": 7, "release_name": "bnk", "chart": "bitnami/nginx", "namespace": "f5-bnk", "values": {"replicaCount": 2}, "version": "1.0.0"},
            install_payload,
            {"success", "message", "result"},
        ),
    ]:
        mcp = _FakeMCP()
        register_helm(mcp, _StubClient(payload))
        parsed = _parse_tool_json(await mcp.tools[tool_name](**kwargs))
        assert parsed == payload
        assert required_keys.issubset(parsed.keys())


@pytest.mark.asyncio
async def test_output_error_envelope_remains_structured_for_ai_recovery() -> None:
    error_payload = {
        "ok": False,
        "request": {"method": "GET", "path": "/api/version"},
        "error": {
            "status_code": 403,
            "detail": "Forbidden",
            "error_class": "auth_error",
            "retryable": False,
            "url": "/api/version",
            "next_action": "Re-authenticate and verify credentials/permissions for this operation.",
        },
    }

    mcp = _FakeMCP()
    register_system(mcp, _StubClient(error_payload))

    parsed = _parse_tool_json(await mcp.tools["system_version"]())
    assert parsed == error_payload
    assert parsed["ok"] is False
    assert parsed["error"]["error_class"] == "auth_error"


@pytest.mark.asyncio
async def test_fleet_health_output_can_include_platform_context_and_guidance() -> None:
    payload = {
        "total_clusters": 2,
        "healthy": 1,
        "warning": 1,
        "critical": 0,
        "offline": 0,
        "operators": [],
        "platform_context": {
            "mixed_platform_profiles": True,
            "comparison_caveats": ["Fleet contains mixed detected platform profiles."],
            "support_semantics": ["Support/readiness semantics are platform-aware."],
        },
    }

    mcp = _FakeMCP()
    register_diagnostics(mcp, _StubClient(payload))

    parsed = _parse_tool_json(await mcp.tools["fleet_health"]())
    assert parsed["platform_context"]["mixed_platform_profiles"] is True
    assert "mcp_guidance" in parsed
    assert any("mixed detected platform profiles" in entry.lower() for entry in parsed["mcp_guidance"])


# ---------------------------------------------------------------------------
# Issue 2 — Normalized registration envelopes (breaking change, coordinated
# with awsbnkctl client). Both tools now return {success, <entity>, message?}
# instead of the prior inconsistent flat/bare shapes.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_project_returns_normalized_envelope_with_nested_project() -> None:
    """Backend flat shape → MCP envelope: {success, project: {id, name, ...}, message}."""
    backend_response = {
        "success": True,
        "project_id": 39,
        "name": "my-project",
        "environment": "dev",
        "message": "Project created successfully",
    }

    mcp = _FakeMCP()
    register_iac(mcp, _StubClient(backend_response))

    parsed = _parse_tool_json(
        await mcp.tools["create_project"](
            name="my-project",
            environment="dev",
        )
    )

    assert parsed["success"] is True
    # #66: the mutating-tool envelope exposes the same universal ok key, and it
    # is meta -- it must not leak into the nested project entity.
    assert parsed["ok"] is True
    assert "ok" not in parsed["project"]
    assert "project" in parsed
    assert parsed["project"]["id"] == 39
    assert parsed["project"]["name"] == "my-project"
    assert parsed["project"]["environment"] == "dev"
    assert parsed["message"] == "Project created successfully"
    # Flat keys must not leak to top level
    assert "project_id" not in parsed
    assert "name" not in parsed


@pytest.mark.asyncio
async def test_create_project_envelope_works_without_message_field() -> None:
    """message is optional — envelope must not include it when absent."""
    backend_response = {"success": True, "project_id": 7, "name": "proj-b"}

    mcp = _FakeMCP()
    register_iac(mcp, _StubClient(backend_response))

    parsed = _parse_tool_json(await mcp.tools["create_project"](name="proj-b"))

    assert parsed["success"] is True
    assert parsed["project"]["id"] == 7
    assert parsed["project"]["name"] == "proj-b"
    assert "message" not in parsed


@pytest.mark.asyncio
async def test_create_project_passes_through_mcp_error_envelope_unchanged() -> None:
    """Structured MCP error envelope (ok=false) must not be re-wrapped."""
    error_envelope = {
        "ok": False,
        "request": {"method": "POST", "path": "/api/projects"},
        "error": {
            "status_code": 422,
            "detail": "name already exists",
            "error_class": "validation_error",
            "retryable": False,
            "url": "/api/projects",
            "next_action": "Choose a unique project name.",
        },
    }

    mcp = _FakeMCP()
    register_iac(mcp, _StubClient(error_envelope))

    parsed = _parse_tool_json(await mcp.tools["create_project"](name="dup"))

    assert parsed == error_envelope
    assert parsed["ok"] is False
    assert parsed["error"]["error_class"] == "validation_error"


@pytest.mark.asyncio
async def test_create_cluster_returns_normalized_envelope_with_nested_cluster() -> None:
    """Backend bare-object shape → MCP envelope: {success: true, cluster: {...}}."""
    backend_response = {
        "id": 12,
        "name": "prod-eks",
        "project_id": 39,
        "status": "active",
        "cloud_provider": "aws",
        "region": "us-east-1",
        "default_namespace": "default",
    }

    mcp = _FakeMCP()
    register_cluster(mcp, _StubClient(backend_response))

    parsed = _parse_tool_json(
        await mcp.tools["create_cluster"](
            project_id=39,
            name="prod-eks",
            kubeconfig="dGVzdA==",
            cloud_provider="aws",
            region="us-east-1",
        )
    )

    assert parsed["success"] is True
    assert "cluster" in parsed
    assert parsed["cluster"]["id"] == 12
    assert parsed["cluster"]["name"] == "prod-eks"
    assert parsed["cluster"]["project_id"] == 39
    assert parsed["cluster"]["status"] == "active"
    # Bare keys must not leak to top level
    assert "id" not in parsed
    assert "name" not in parsed


@pytest.mark.asyncio
async def test_create_cluster_envelope_strips_message_to_top_level() -> None:
    """When the backend object carries a message, it surfaces at top level."""
    backend_response = {
        "id": 5,
        "name": "lab-cluster",
        "project_id": 1,
        "status": "pending",
        "message": "Cluster registered; scan running in background.",
    }

    mcp = _FakeMCP()
    register_cluster(mcp, _StubClient(backend_response))

    parsed = _parse_tool_json(
        await mcp.tools["create_cluster"](
            project_id=1,
            name="lab-cluster",
            kubeconfig="dGVzdA==",
        )
    )

    assert parsed["success"] is True
    assert parsed["message"] == "Cluster registered; scan running in background."
    # message must not also appear inside cluster entity
    assert "message" not in parsed["cluster"]
    assert parsed["cluster"]["id"] == 5


@pytest.mark.asyncio
async def test_create_cluster_passes_through_mcp_error_envelope_unchanged() -> None:
    """Structured MCP error envelope (ok=false) must not be re-wrapped."""
    error_envelope = {
        "ok": False,
        "request": {"method": "POST", "path": "/api/projects/1/k8s/clusters"},
        "error": {
            "status_code": 409,
            "detail": "Cluster name already exists in project",
            "error_class": "validation_error",
            "retryable": False,
            "url": "/api/projects/1/k8s/clusters",
            "next_action": "Choose a unique cluster name within the project.",
        },
    }

    mcp = _FakeMCP()
    register_cluster(mcp, _StubClient(error_envelope))

    parsed = _parse_tool_json(
        await mcp.tools["create_cluster"](project_id=1, name="dup", kubeconfig="dGVzdA==")
    )

    assert parsed == error_envelope
    assert parsed["ok"] is False
    assert parsed["error"]["error_class"] == "validation_error"
