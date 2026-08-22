#!/usr/bin/env python3
"""Bounded live smoke validation for BNK-Forge MCP runtime.

This script validates a small, durable set of high-value MCP behaviors against a
running deployment:

1. MCP endpoint reachability + JSON-RPC protocol round-trip (`ping`)
2. MCP tool catalog discoverability (`tools/list`)
3. Read-only governed tool execution (`system_version`, `list_clusters`)
4. Structured MCP error-envelope behavior on a failing governed tool call

The scope is intentionally small and read-only-first so it can be used as a
post-deploy/runtime sanity check without becoming a fragile e2e framework.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class SmokeConfig:
    mcp_url: str
    timeout_seconds: float
    insecure_tls: bool
    invalid_cluster_id: int


class SmokeFailure(RuntimeError):
    """Raised when a smoke assertion fails."""


class JsonRpcClient:
    def __init__(self, config: SmokeConfig) -> None:
        self._config = config
        self._next_id = 1

        context: ssl.SSLContext | None = None
        if self._config.insecure_tls:
            context = ssl._create_unverified_context()  # noqa: SLF001 - explicit CLI opt-in
        self._ssl_context = context

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1

        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params

        req = urllib.request.Request(
            self._config.mcp_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                req,
                timeout=self._config.timeout_seconds,
                context=self._ssl_context,
            ) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - exercised in live runs
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise SmokeFailure(
                f"JSON-RPC call failed with HTTP {exc.code} for method '{method}'. "
                f"Response body: {detail}"
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - exercised in live runs
            raise SmokeFailure(
                f"Unable to reach MCP endpoint '{self._config.mcp_url}' for method '{method}': {exc}"
            ) from exc

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SmokeFailure(
                f"MCP response for method '{method}' was not valid JSON. Raw body: {body!r}"
            ) from exc

        if not isinstance(parsed, dict):
            raise SmokeFailure(f"MCP response for method '{method}' must be a JSON object, got: {type(parsed)}")

        if parsed.get("id") != request_id:
            raise SmokeFailure(
                f"MCP response ID mismatch for method '{method}': "
                f"expected {request_id}, got {parsed.get('id')}"
            )

        if parsed.get("jsonrpc") != "2.0":
            raise SmokeFailure(
                f"MCP response missing jsonrpc=2.0 for method '{method}': {parsed.get('jsonrpc')!r}"
            )

        return parsed


def _expect_rpc_result(response: dict[str, Any], method: str) -> dict[str, Any]:
    if "error" in response:
        raise SmokeFailure(f"MCP JSON-RPC error for method '{method}': {response['error']}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise SmokeFailure(f"MCP method '{method}' returned non-object result: {result!r}")
    return result


def _extract_tool_payload(result: dict[str, Any], tool_name: str) -> dict[str, Any]:
    """Extract tool JSON payload from FastMCP result envelope.

    FastMCP tool calls usually return text content items; each tool currently emits
    JSON text (`json.dumps(...)`). We parse that JSON and return it as a dict.
    """

    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                text = item["text"]
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError as exc:
                    if text.startswith("Error executing tool "):
                        hint = ""
                        if "/api/auth/login" in text or "Invalid username or password" in text:
                            hint = (
                                " Hint: MCP backend credentials are likely invalid. "
                                "Set MCP_SERVICE_PASSWORD in the compose .env (bonnyr-f5 #193 M7: compose maps it to "
                                "the MCP container's BNK_FORGE_PASSWORD and the backend's MCP_SERVICE_PASSWORD — the "
                                "MCP process itself reads only BNK_FORGE_*, not MCP_SERVICE_*). MCP authenticates as the "
                                "mcp service account (username fixed to 'mcp', not admin); the backend reconciles the mcp "
                                "account to that SAME password every boot. When it is unset the mcp account is left "
                                "disabled (#186)."
                            )
                        raise SmokeFailure(
                            f"Tool '{tool_name}' execution failed before returning MCP JSON payload: {text}.{hint}"
                        ) from exc
                    raise SmokeFailure(
                        f"Tool '{tool_name}' returned non-JSON text content: {text!r}"
                    ) from exc
                if isinstance(payload, dict):
                    return payload
                raise SmokeFailure(
                    f"Tool '{tool_name}' JSON payload must be an object, got {type(payload)}"
                )

    raise SmokeFailure(
        f"Unable to extract JSON payload for tool '{tool_name}'. "
        f"Result keys: {sorted(result.keys())}"
    )


def _call_tool(client: JsonRpcClient, tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    rpc = client.call(
        "tools/call",
        {
            "name": tool_name,
            "arguments": arguments or {},
        },
    )
    result = _expect_rpc_result(rpc, f"tools/call:{tool_name}")
    return _extract_tool_payload(result, tool_name)


def _call_tool_expect_failure_text(
    client: JsonRpcClient,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Return wrapper-level failure text for tools that fail before JSON payload emission.

    Some FastMCP tool/runtime failures are surfaced as plain text like
    "Error executing tool ..." rather than the tool's JSON envelope. For bounded live
    smoke validation, we treat that as an acceptable controlled failure mode when the
    backend returns the expected failing resource condition.
    """

    rpc = client.call(
        "tools/call",
        {
            "name": tool_name,
            "arguments": arguments or {},
        },
    )
    result = _expect_rpc_result(rpc, f"tools/call:{tool_name}")
    content = result.get("content")
    if not isinstance(content, list):
        raise SmokeFailure(
            f"Expected text content list for failing tool '{tool_name}', got: {type(content)}"
        )

    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            text = item["text"]
            if text.startswith("Error executing tool "):
                return text

    raise SmokeFailure(
        f"Expected wrapper-level failure text for tool '{tool_name}', got result keys: {sorted(result.keys())}"
    )


def _auth_bootstrap_hint(tool_name: str, payload: dict[str, Any]) -> str:
    """Return a contextual hint when runtime auth/bootstrap fails."""
    error = payload.get("error")
    if not isinstance(error, dict):
        return ""

    if error.get("error_class") != "auth_error":
        return ""

    detail = str(error.get("detail", "")).lower()
    if "invalid username or password" in detail or "/api/auth/login" in str(error.get("url", "")):
        return (
            " Hint: MCP endpoint is reachable, but MCP runtime auth/bootstrap failed. "
            "Set MCP_SERVICE_PASSWORD in the compose .env so it matches the mcp service account the backend "
            "reconciles (bonnyr-f5 #193 M7: compose delivers it to the container as BNK_FORGE_PASSWORD and to the "
            "backend as MCP_SERVICE_PASSWORD — one value, both sides; the username is fixed to 'mcp'). When it is "
            "unset the mcp account is left disabled. Then recreate the mcp container (#186)."
        )

    return (
        " Hint: MCP endpoint is reachable, but MCP runtime auth/bootstrap failed. "
        "Verify MCP credentials/token and backend auth readiness."
    )


def run_smoke(config: SmokeConfig) -> None:
    client = JsonRpcClient(config)

    print(f"[INFO] MCP live smoke against: {config.mcp_url}")

    # 1) Endpoint reachability + protocol-compatible round-trip
    ping_response = client.call("ping")
    _expect_rpc_result(ping_response, "ping")
    print("[PASS] JSON-RPC ping round-trip succeeded")

    # 2) Tool discovery sanity
    tools_response = client.call("tools/list")
    tools_result = _expect_rpc_result(tools_response, "tools/list")
    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        raise SmokeFailure("tools/list did not return a 'tools' array")

    tool_names = {
        str(tool.get("name"))
        for tool in tools
        if isinstance(tool, dict) and tool.get("name")
    }
    required_tools = {"system_version", "list_clusters", "get_cluster"}
    missing = sorted(required_tools - tool_names)
    if missing:
        raise SmokeFailure(f"tools/list missing expected governed tools: {missing}")
    print("[PASS] tools/list includes expected governed tools")

    # 3) Read-only governed tool calls
    version_payload = _call_tool(client, "system_version")
    if version_payload.get("ok") is False:
        hint = _auth_bootstrap_hint("system_version", version_payload)
        raise SmokeFailure(
            f"system_version returned MCP error envelope unexpectedly: {version_payload}.{hint}"
        )
    print("[PASS] system_version tool call succeeded")

    clusters_payload = _call_tool(client, "list_clusters")
    if clusters_payload.get("ok") is False:
        hint = _auth_bootstrap_hint("list_clusters", clusters_payload)
        raise SmokeFailure(
            f"list_clusters returned MCP error envelope unexpectedly: {clusters_payload}.{hint}"
        )
    print("[PASS] list_clusters tool call succeeded")

    # 4) Controlled failure-path behavior on failing tool invocation
    failure_text = _call_tool_expect_failure_text(
        client,
        "get_cluster",
        {"cluster_id": config.invalid_cluster_id},
    )
    expected_tokens = [
        "Error executing tool get_cluster",
        "HTTP 404",
        f"/api/k8s/clusters/{config.invalid_cluster_id}",
        "CLUSTER_NOT_FOUND",
        "Cluster not found",
    ]
    missing_tokens = [token for token in expected_tokens if token not in failure_text]
    if missing_tokens:
        raise SmokeFailure(
            "Controlled failing tool call did not include expected diagnostic tokens. "
            f"Missing={missing_tokens}. Text={failure_text}"
        )
    print("[PASS] failing governed tool call returns explicit, actionable failure text")

    print("[PASS] MCP live smoke suite complete")


def parse_args(argv: list[str]) -> SmokeConfig:
    parser = argparse.ArgumentParser(description="Bounded live MCP runtime smoke checks")
    parser.add_argument(
        "--mcp-url",
        default="http://localhost:8081/mcp",
        help="MCP endpoint URL (default: http://localhost:8081/mcp)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="Disable TLS certificate verification (useful for self-signed local/proxy certs)",
    )
    parser.add_argument(
        "--invalid-cluster-id",
        type=int,
        default=-1,
        help="Cluster ID expected to fail (default: -1)",
    )
    args = parser.parse_args(argv)

    return SmokeConfig(
        mcp_url=args.mcp_url,
        timeout_seconds=args.timeout,
        insecure_tls=args.insecure_tls,
        invalid_cluster_id=args.invalid_cluster_id,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv or sys.argv[1:])
    try:
        run_smoke(config)
    except SmokeFailure as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
