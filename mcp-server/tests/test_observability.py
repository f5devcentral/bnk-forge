"""Tests for MCP invocation observability instrumentation."""

from __future__ import annotations

import json
import logging

import pytest

from bnk_forge_mcp.observability import (
    _FALLBACK_AUTH_EXPECTATION,
    _FALLBACK_RISK_CLASS,
    ObservabilityMCPProxy,
    instrument_tool,
)
from bnk_forge_mcp.tool_catalog import get_high_risk_tool_catalog


@pytest.mark.asyncio
async def test_instrument_tool_emits_start_and_success_events(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bnk_forge_mcp.observability")

    async def _tool_impl(cluster_id: int, token: str) -> str:
        # The wrapper must not log this payload or secret token.
        return json.dumps({"ok": True, "cluster_id": cluster_id})

    # Pass pre-resolved risk/auth (as the proxy would, via stamped _meta).
    wrapped = instrument_tool("cluster_management", _tool_impl, risk_class="read_only", auth_expectation="viewer")
    result = await wrapped(7, token="super-secret-token")

    assert json.loads(result)["ok"] is True
    assert "tool_invocation_start" in caplog.text
    assert '"tool_name":"_tool_impl"' in caplog.text
    assert '"module":"cluster_management"' in caplog.text
    # Risk now comes from the stamped _meta argument, NOT _CATALOG_BY_TOOL at call time.
    assert '"risk_class":"read_only"' in caplog.text
    assert '"auth_expectation":"viewer"' in caplog.text
    assert '"event":"tool_invocation_result"' in caplog.text
    assert '"success":true' in caplog.text
    assert "super-secret-token" not in caplog.text


@pytest.mark.asyncio
async def test_instrument_tool_risk_from_stamped_meta_not_catalog(caplog: pytest.LogCaptureFixture) -> None:
    """instrument_tool uses the risk/auth it was called with, not re-reads from _CATALOG_BY_TOOL."""
    caplog.set_level(logging.INFO, logger="bnk_forge_mcp.observability")

    async def _tool_impl() -> str:
        return json.dumps({"ok": True})

    # Override with non-catalog values to prove it uses the argument, not the catalog.
    wrapped = instrument_tool("system", _tool_impl, risk_class="destructive", auth_expectation="admin")
    await wrapped()

    assert '"risk_class":"destructive"' in caplog.text
    assert '"auth_expectation":"admin"' in caplog.text


@pytest.mark.asyncio
async def test_instrument_tool_emits_failure_class_from_error_envelope(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bnk_forge_mcp.observability")

    async def _tool_impl() -> str:
        return json.dumps(
            {
                "ok": False,
                "error": {
                    "error_class": "auth_error",
                },
            }
        )

    wrapped = instrument_tool("system", _tool_impl)
    await wrapped()

    assert '"event":"tool_invocation_result"' in caplog.text
    assert '"success":false' in caplog.text
    assert '"error_class":"auth_error"' in caplog.text


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.tool_kwargs: dict[str, dict] = {}

    def tool(self, *args, **kwargs):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            self.tool_kwargs[fn.__name__] = kwargs
            return fn

        return decorator


@pytest.mark.asyncio
async def test_observability_proxy_instruments_registered_tool(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bnk_forge_mcp.observability")

    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "system")

    @proxy.tool()
    async def demo_tool() -> str:
        return json.dumps({"ok": True})

    await mcp.tools["demo_tool"]()
    assert '"tool_name":"demo_tool"' in caplog.text
    assert '"module":"system"' in caplog.text


def test_observability_proxy_stamps_module_in_meta() -> None:
    """ObservabilityMCPProxy.tool() injects meta['module'] into the underlying mcp.tool() call."""
    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "cluster_management")

    @proxy.tool()
    async def my_tool() -> str:
        return "ok"

    assert "my_tool" in mcp.tool_kwargs
    assert mcp.tool_kwargs["my_tool"].get("meta", {}).get("module") == "cluster_management"


def test_observability_proxy_stamps_risk_and_auth_in_meta() -> None:
    """ObservabilityMCPProxy.tool() injects risk_class + auth_expectation into meta from catalog seed."""
    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "system")

    @proxy.tool()
    async def system_health() -> str:
        return "ok"

    registered_meta = mcp.tool_kwargs["system_health"].get("meta", {})
    assert registered_meta.get("module") == "system"
    # system_health is in catalog with admin/read_only
    assert registered_meta.get("risk_class") == "read_only"
    assert registered_meta.get("auth_expectation") == "admin"


def test_observability_proxy_does_not_clobber_existing_meta() -> None:
    """If a caller already passes meta, the proxy merges module in without overwriting other keys."""
    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "helm")

    @proxy.tool(meta={"custom_key": "custom_value"})
    async def another_tool() -> str:
        return "ok"

    registered_meta = mcp.tool_kwargs["another_tool"].get("meta", {})
    assert registered_meta.get("module") == "helm"
    assert registered_meta.get("custom_key") == "custom_value"


def test_observability_proxy_does_not_clobber_explicit_risk_in_meta() -> None:
    """Explicitly passed meta risk_class wins over catalog seed (setdefault semantics)."""
    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "system")

    @proxy.tool(meta={"risk_class": "destructive", "auth_expectation": "admin"})
    async def system_health() -> str:  # normally read_only/admin in catalog
        return "ok"

    registered_meta = mcp.tool_kwargs["system_health"].get("meta", {})
    # Explicit override must not be clobbered by catalog.
    assert registered_meta.get("risk_class") == "destructive"
    assert registered_meta.get("auth_expectation") == "admin"


def test_synthetic_new_tool_gets_fallback_not_uncataloged() -> None:
    """A tool with no catalog entry gets conservative fallback, not 'uncataloged'."""
    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "some_future_module")

    @proxy.tool()
    async def brand_new_tool_xyz_not_in_catalog() -> str:
        return "ok"

    registered_meta = mcp.tool_kwargs["brand_new_tool_xyz_not_in_catalog"].get("meta", {})
    assert registered_meta.get("risk_class") == _FALLBACK_RISK_CLASS
    assert registered_meta.get("auth_expectation") == _FALLBACK_AUTH_EXPECTATION
    assert registered_meta.get("risk_class") != "uncataloged"
    assert registered_meta.get("auth_expectation") != "uncataloged"


@pytest.mark.parametrize("entry", get_high_risk_tool_catalog())
def test_proxy_stamps_match_catalog_for_every_cataloged_tool(entry: dict) -> None:
    """Behavior-preserving: stamped meta risk/auth == catalog value for every cataloged tool."""
    tool_name = entry["tool_name"]
    module_name = entry["module"]
    expected_risk = entry["risk_class"]
    expected_auth = entry["auth_expectation"]

    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, module_name)

    # Create a function whose __name__ matches the catalog tool_name.
    async def _placeholder() -> str:
        return "ok"

    _placeholder.__name__ = tool_name  # type: ignore[attr-defined]
    _placeholder.__qualname__ = tool_name  # type: ignore[attr-defined]

    proxy.tool()(_placeholder)

    registered_meta = mcp.tool_kwargs[tool_name].get("meta", {})
    assert registered_meta.get("risk_class") == expected_risk, (
        f"{tool_name}: expected risk_class={expected_risk!r}, got {registered_meta.get('risk_class')!r}"
    )
    assert registered_meta.get("auth_expectation") == expected_auth, (
        f"{tool_name}: expected auth_expectation={expected_auth!r}, got {registered_meta.get('auth_expectation')!r}"
    )


def test_diagnostics_fleet_tools_are_not_uncataloged() -> None:
    """All diagnostics_fleet tools must have a curated catalog entry (not fallback)."""
    diagnostics_fleet_catalog = {
        entry["tool_name"]: entry
        for entry in get_high_risk_tool_catalog()
        if entry["module"] == "diagnostics_fleet"
    }

    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "diagnostics_fleet")

    for tool_name, catalog_entry in diagnostics_fleet_catalog.items():
        async def _placeholder() -> str:
            return "ok"

        _placeholder.__name__ = tool_name  # type: ignore[attr-defined]
        _placeholder.__qualname__ = tool_name  # type: ignore[attr-defined]
        proxy.tool()(_placeholder)

        registered_meta = mcp.tool_kwargs[tool_name].get("meta", {})
        assert registered_meta.get("risk_class") != "uncataloged", (
            f"{tool_name} must not emit uncataloged risk_class"
        )
        assert registered_meta.get("auth_expectation") != "uncataloged", (
            f"{tool_name} must not emit uncataloged auth_expectation"
        )
        # Must match catalog exactly.
        assert registered_meta.get("risk_class") == catalog_entry["risk_class"]
        assert registered_meta.get("auth_expectation") == catalog_entry["auth_expectation"]


@pytest.mark.asyncio
async def test_proxy_meta_risk_class_round_trips_to_start_event(caplog: pytest.LogCaptureFixture) -> None:
    """risk_class stamped in _meta appears in tool_invocation_start log event."""
    caplog.set_level(logging.INFO, logger="bnk_forge_mcp.observability")

    mcp = _FakeMCP()
    proxy = ObservabilityMCPProxy(mcp, "diagnostics_fleet")

    @proxy.tool()
    async def fleet_health() -> str:
        return json.dumps({"ok": True})

    await mcp.tools["fleet_health"]()

    assert '"risk_class":"read_only"' in caplog.text
    assert '"auth_expectation":"viewer"' in caplog.text
