# E2E-CRITICAL-004: MCP End-to-End Sanity Coverage Plan

**Status:** Complete
**Version:** 2.11.0

---

## Problem

68 MCP tools are registered but zero E2E tests verify that the full
chain works: MCP server → HTTP client → Backend API → Database → Response.
Unit tests mock the HTTP layer; we need at least one path that proves
the real integration.

---

## Strategy

### Scope: 8 Tools (One Per Domain)

| # | Tool | Domain | Risk | Auth | Why Selected |
|---|------|--------|------|------|-------------|
| 1 | `system_health` | System | read | admin | Server readiness probe |
| 2 | `list_clusters` | Cluster | read | viewer | Foundation for all cluster ops |
| 3 | `get_cluster` | Cluster | read | viewer | Tests path param interpolation |
| 4 | `bnk_health` | BNK | read | cluster_owner | Core BNK domain |
| 5 | `helm_list_releases` | Helm | read | viewer | Optional param handling |
| 6 | `config_export` | Config | read | operator | Format selector (yaml/json) |
| 7 | `list_projects` | IaC | read | viewer | Project domain coverage |
| 8 | `fleet_health` | Fleet | read | viewer | Fleet domain coverage |

All selected tools are READ_ONLY — safe for any environment.

---

## Test Architecture

### Layer 1: MCP Protocol Smoke (Container Health)

Already exists in `docker-compose.yml` health check:
```yaml
mcp:
  test: python -c "import urllib.request; ..."  # JSON-RPC ping
```

**Status:** Implemented. No action needed.

### Layer 2: Tool Invocation Smoke (New)

Tests that each selected tool can be invoked via the MCP protocol and
returns a valid response envelope.

**Implementation:** `mcp-server/tests/test_e2e_smoke.py`

```python
@pytest.mark.e2e
async def test_system_health_e2e():
    """Invoke system_health through real HTTP to backend."""
    client = BNKForgeClient(config)
    result = await client.get("/api/system/health")
    assert result["status"] == "healthy"
```

### Layer 3: Response Contract Verification (Existing + Extension)

Existing `test_tool_output_contracts.py` uses stub responses. Extend
with live-backend variants that validate against real API responses.

---

## Test File Structure

```
mcp-server/tests/
├── test_e2e_smoke.py          # NEW — Layer 2 live backend tests
├── test_tool_output_contracts.py  # Existing — extend with live variants
├── test_server.py             # Existing — registration checks
├── test_tool_catalog.py       # Existing — governance checks
└── ...
```

---

## Environment Requirements

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `MCP_E2E` | Yes | `false` | Gate for E2E tests (skip in unit runs) |
| `API_BASE_URL` | Yes | `http://localhost:8000` | Backend API target |
| `MCP_USERNAME` | Yes | `admin` | Auth credentials |
| `MCP_SERVICE_PASSWORD` | Yes | _(no default, #187)_ | Shared secret: backend seeds the mcp account, MCP server authenticates with it |

---

## CI Integration

```yaml
# In e2e-tests.yml, after Docker Compose is up:
- name: MCP E2E Smoke
  run: |
    cd mcp-server
    MCP_E2E=true API_BASE_URL=http://localhost:8000 \
    pytest tests/test_e2e_smoke.py -v
  if: success()
```

Also exposed via Makefile:
```makefile
test-mcp-e2e:
	cd mcp-server && MCP_E2E=true pytest tests/test_e2e_smoke.py -v
```

---

## Success Criteria

| Criterion | Measurement |
|-----------|-------------|
| All 8 tools respond without error | `assert response["ok"] is True` |
| Response shapes match catalog | Fields match `ToolCatalogEntry` expectations |
| Latency under 5 seconds per tool | `assert duration_ms < 5000` |
| No credential leakage in responses | No `password`, `secret`, `token` in output |
| Works in CI Docker environment | `make test-mcp-e2e` passes in GitHub Actions |

---

## Reuse Patterns from Existing Tests

| Pattern | Source File | Reuse |
|---------|-----------|-------|
| Registration verification | `test_server.py` | Verify all 8 tools registered |
| Output contract shape | `test_tool_output_contracts.py` | Extend `_StubClient` with live client |
| HTTP request mocking | `test_client.py` | Use `respx` for isolated tests |
| Catalog governance | `test_tool_catalog.py` | Verify E2E tools match catalog metadata |

---

## Out of Scope

- Mutating tool E2E (too risky for automated runs)
- Performance/load testing
- Multi-user concurrent MCP sessions
- WebSocket-based MCP transport (only Streamable HTTP tested)
