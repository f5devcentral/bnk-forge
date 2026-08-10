# MCP-PROD-012: MCP Readiness Scorecard

**Status:** Complete
**Version:** 2.11.0

---

## Purpose

Repeatable scorecard for tracking MCP maturity across safety, contracts,
observability, and usability. Score quarterly to measure progress.

---

## Scorecard (Current Assessment: 2026-03-30)

### 1. Safety & Governance (25 points)

| Item | Max | Score | Evidence |
|------|-----|-------|----------|
| Tool catalog with risk classification | 5 | 5 | `tool_catalog.json` — all tools classified |
| Mutating tools flagged and confirmed | 5 | 5 | `mutating: true` in catalog, contract tests |
| Secret scrubbing in responses | 5 | 5 | `scrub_secrets()` in error formatter |
| Auth expectation documented per tool | 5 | 5 | `auth_expectation` field in catalog |
| Safety review checklist exists | 5 | 5 | `SEC-GOV-004_SAFETY_REVIEW_CHECKLIST.md` |
| **Subtotal** | **25** | **25** | |

### 2. API Contracts (20 points)

| Item | Max | Score | Evidence |
|------|-----|-------|----------|
| Endpoint mappings documented | 5 | 5 | `tool_catalog.json` has endpoint mappings |
| Error response envelope standardized | 5 | 5 | `ok/error` envelope in `client.py` |
| Contract tests for tool registration | 5 | 5 | `test_tool_catalog.py` |
| Deprecation policy documented | 5 | 5 | MCP compatibility and deprecation policy (commit `9f338d4`) |
| **Subtotal** | **20** | **20** | |

### 3. Observability (20 points)

| Item | Max | Score | Evidence |
|------|-----|-------|----------|
| Invocation logging (start + result) | 5 | 5 | `ObservabilityMCPProxy` logs both events |
| Invocation ID per call | 5 | 5 | UUID-based `invocation_id` |
| Duration tracking | 5 | 5 | `duration_ms` in result events |
| Error classification in logs | 5 | 4 | `error_class` logged, but not correlated to API request ID |
| **Subtotal** | **20** | **19** | |

### 4. Usability (20 points)

| Item | Max | Score | Evidence |
|------|-----|-------|----------|
| Tool descriptions are actionable | 5 | 5 | Descriptions in tool registration |
| Next-action suggestions on errors | 5 | 5 | `next_action` field in error responses |
| High-value workflows documented | 5 | 5 | `MCP-PROD-011_AI_WORKFLOWS.md` |
| Autonomous eligibility rules defined | 5 | 5 | `MCP-PROD-010_AUTONOMOUS_ELIGIBILITY.md` |
| **Subtotal** | **20** | **20** | |

### 5. Testing & Reliability (15 points)

| Item | Max | Score | Evidence |
|------|-----|-------|----------|
| MCP health check in Docker | 3 | 3 | JSON-RPC ping in `docker-compose.yml` |
| MCP smoke test in CI | 3 | 3 | `make smoke-mcp-live` |
| MCP readiness gate in deploy | 3 | 3 | `make mcp-readiness` |
| E2E MCP sanity tests | 3 | 0 | Not yet implemented (E2E-CRITICAL-004) |
| Load/stress testing | 3 | 0 | Not planned |
| **Subtotal** | **15** | **9** | |

---

## Overall Score

| Category | Max | Score | Grade |
|----------|-----|-------|-------|
| Safety & Governance | 25 | 25 | A |
| API Contracts | 20 | 20 | A |
| Observability | 20 | 19 | A- |
| Usability | 20 | 20 | A |
| Testing & Reliability | 15 | 9 | C |
| **Total** | **100** | **93** | **A** |

---

## Gaps to Close

| Gap | Points Lost | Fix | Priority |
|-----|-----------|-----|----------|
| E2E MCP sanity tests | 3 | Implement E2E-CRITICAL-004 | P2 |
| Load/stress testing | 3 | Add basic throughput test | P3 |
| Request ID correlation in MCP | 1 | Propagate `X-Request-ID` to MCP invocations | P3 |

---

## Scoring History

| Date | Score | Delta | Notes |
|------|-------|-------|-------|
| 2026-03-30 | 93/100 | — | Initial assessment |

---

## How to Re-Score

1. Review each item against current codebase
2. Update scores with evidence (commit hash, file path, test name)
3. Add entry to scoring history
4. File PR with updated scorecard
