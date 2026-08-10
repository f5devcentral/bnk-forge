"""Tool mapping hardening tests for critical MCP tools."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("mcp", reason="mcp package not installed in this test environment")

from bnk_forge_mcp.tools.bnk_operations import register as register_bnk
from bnk_forge_mcp.tools.cluster_management import register as register_cluster
from bnk_forge_mcp.tools.config_management import register as register_config
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
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, path: str, params=None):
        self.calls.append(("GET", path, {"params": params}))
        return {"path": path, "params": params}

    async def post(self, path: str, json=None, params=None):
        self.calls.append(("POST", path, {"json": json, "params": params}))
        return {"path": path, "json": json, "params": params}

    async def put(self, path: str, json=None, params=None):
        self.calls.append(("PUT", path, {"json": json, "params": params}))
        return {"path": path, "json": json, "params": params}

    async def delete(self, path: str, params=None):
        self.calls.append(("DELETE", path, {"params": params}))
        return {"path": path, "params": params}


@pytest.mark.asyncio
async def test_system_version_uses_version_endpoint() -> None:
    mcp = _FakeMCP()
    client = _StubClient()
    register_system(mcp, client)

    await mcp.tools["system_version"]()

    method, path, kwargs = client.calls[-1]
    assert method == "GET"
    assert path == "/api/version"
    assert kwargs["params"] is None


@pytest.mark.asyncio
async def test_restart_pod_passes_namespace_as_query_param() -> None:
    mcp = _FakeMCP()
    client = _StubClient()
    register_cluster(mcp, client)

    await mcp.tools["restart_pod"](cluster_id=7, pod_name="tmm-0", namespace="f5-bnk")

    method, path, kwargs = client.calls[-1]
    assert method == "POST"
    assert path == "/api/k8s/clusters/7/pods/tmm-0/restart"
    assert kwargs["json"] is None
    assert kwargs["params"] == {"namespace": "f5-bnk"}


@pytest.mark.asyncio
async def test_helm_mutating_tools_use_namespace_query_param() -> None:
    mcp = _FakeMCP()
    client = _StubClient()
    register_helm(mcp, client)

    await mcp.tools["helm_upgrade"](
        cluster_id=11,
        release_name="bnk",
        chart="f5/bnk",
        namespace="f5-bnk",
        values={"replicaCount": 2},
        version="2.2.0",
    )
    method, path, kwargs = client.calls[-1]
    assert method == "PUT"
    assert path == "/api/k8s/11/helm/releases/bnk/upgrade"
    assert kwargs["params"] == {"namespace": "f5-bnk"}

    await mcp.tools["helm_rollback"](cluster_id=11, release_name="bnk", revision=3, namespace="f5-bnk")
    method, path, kwargs = client.calls[-1]
    assert method == "POST"
    assert path == "/api/k8s/11/helm/releases/bnk/rollback"
    assert kwargs["params"] == {"namespace": "f5-bnk"}
    assert kwargs["json"] == {"revision": 3}

    await mcp.tools["helm_uninstall"](cluster_id=11, release_name="bnk", namespace="f5-bnk")
    method, path, kwargs = client.calls[-1]
    assert method == "DELETE"
    assert path == "/api/k8s/11/helm/releases/bnk"
    assert kwargs["params"] == {"namespace": "f5-bnk"}


@pytest.mark.asyncio
async def test_helm_upgrade_excludes_namespace_from_json_body() -> None:
    mcp = _FakeMCP()
    client = _StubClient()
    register_helm(mcp, client)

    await mcp.tools["helm_upgrade"](
        cluster_id=11,
        release_name="bnk",
        chart="f5/bnk",
        namespace="f5-bnk",
        values={"replicaCount": 2},
        version="2.2.0",
    )

    method, path, kwargs = client.calls[-1]
    assert method == "PUT"
    assert path == "/api/k8s/11/helm/releases/bnk/upgrade"
    assert kwargs["params"] == {"namespace": "f5-bnk"}
    assert kwargs["json"] == {
        "chart": "f5/bnk",
        "values": {"replicaCount": 2},
        "version": "2.2.0",
    }


@pytest.mark.asyncio
async def test_platform_restart_uses_required_default_request_body() -> None:
    mcp = _FakeMCP()
    client = _StubClient()
    register_bnk(mcp, client)

    await mcp.tools["bnk_platform_restart"](cluster_id=7)

    method, path, kwargs = client.calls[-1]
    assert method == "POST"
    assert path == "/api/k8s/clusters/7/recovery/platform-restart"
    assert kwargs["params"] is None
    assert kwargs["json"] == {
        "restart_controller": True,
        "restart_flo": False,
        "restart_tmm": False,
    }


@pytest.mark.asyncio
async def test_config_import_wraps_payload_under_config_key() -> None:
    mcp = _FakeMCP()
    client = _StubClient()
    register_config(mcp, client)

    await mcp.tools["config_import"](
        cluster_id=42,
        config_data='{"resources": {"gateways": []}}',
    )

    method, path, kwargs = client.calls[-1]
    assert method == "POST"
    assert path == "/api/clusters/42/bnk/import"
    assert kwargs["params"] is None
    assert kwargs["json"] == {
        "config": {"resources": {"gateways": []}},
    }


@pytest.mark.asyncio
async def test_iac_mutating_tools_send_required_request_bodies() -> None:
    mcp = _FakeMCP()
    client = _StubClient()
    register_iac(mcp, client)

    await mcp.tools["project_apply"](module_id=101)
    method, path, kwargs = client.calls[-1]
    assert method == "POST"
    assert path == "/api/project-modules/101/apply"
    assert kwargs["json"] == {"auto_approve": False}

    await mcp.tools["project_destroy"](module_id=102)
    method, path, kwargs = client.calls[-1]
    assert method == "POST"
    assert path == "/api/project-modules/102/destroy"
    assert kwargs["json"] == {"auto_approve": False}

    await mcp.tools["project_deploy_all"](project_id=5)
    method, path, kwargs = client.calls[-1]
    assert method == "POST"
    assert path == "/api/projects/5/deploy-all"
    assert kwargs["json"] == {"parallel": True}

    await mcp.tools["project_destroy_all"](project_id=5)
    method, path, kwargs = client.calls[-1]
    assert method == "POST"
    assert path == "/api/projects/5/destroy-all"
    assert kwargs["json"] == {"parallel": True}


@pytest.mark.asyncio
async def test_config_import_rejects_non_json_payloads_with_validation_error() -> None:
    mcp = _FakeMCP()
    client = _StubClient()
    register_config(mcp, client)

    result = await mcp.tools["config_import"](cluster_id=42, config_data="not-json")
    parsed = json.loads(result)

    assert parsed["ok"] is False
    assert parsed["error"]["error_class"] == "validation_error"
    assert "valid JSON" in parsed["error"]["detail"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_config_diff_includes_platform_mixed_profile_guidance_when_present() -> None:
    class _GuidanceClient(_StubClient):
        async def post(self, path: str, json=None, params=None):
            self.calls.append(("POST", path, {"json": json, "params": params}))
            return {
                "summary": "1 difference",
                "platform_context": {
                    "mixed_platform_profiles": True,
                    "comparison_caveats": ["Compared clusters use different detected platform profiles."],
                },
            }

    mcp = _FakeMCP()
    client = _GuidanceClient()
    register_config(mcp, client)

    result = await mcp.tools["config_diff"](cluster_a_id=1, cluster_b_id=2)
    parsed = json.loads(result)

    assert parsed["platform_context"]["mixed_platform_profiles"] is True
    assert "Compared clusters use different detected platform profiles." in parsed["mcp_guidance"]
    assert any("Mixed platform profiles detected" in note for note in parsed["safety_notes"])


@pytest.mark.asyncio
async def test_config_diff_does_not_add_mixed_profile_guidance_when_profiles_are_not_mixed() -> None:
    class _GuidanceClient(_StubClient):
        async def post(self, path: str, json=None, params=None):
            self.calls.append(("POST", path, {"json": json, "params": params}))
            return {
                "summary": "0 differences",
                "platform_context": {
                    "mixed_platform_profiles": False,
                    "comparison_caveats": [],
                },
            }

    mcp = _FakeMCP()
    client = _GuidanceClient()
    register_config(mcp, client)

    result = await mcp.tools["config_diff"](cluster_a_id=1, cluster_b_id=2)
    parsed = json.loads(result)

    assert parsed["platform_context"]["mixed_platform_profiles"] is False
    assert "mcp_guidance" not in parsed
    assert "safety_notes" not in parsed
