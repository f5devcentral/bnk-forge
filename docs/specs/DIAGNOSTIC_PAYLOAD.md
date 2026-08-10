# Diagnostic Payload Standardization — PLAT-REL-002

> Standard diagnostic payload contract for health-like APIs across BNK Forge.

Status: **Accepted** | Created: 2026-03-27

---

## Problem

Operators need more than a status badge. When something is degraded or broken, they need to know:
- **What** is wrong (message)
- **How bad** is it (severity)
- **What to do** (suggestion)
- **What evidence** supports the assessment (evidence)

Today, only the connectivity probe provides this structure (`message` + `suggestion`). Fleet health, system health, and BNK health return bare status strings with no explanation.

---

## Diagnostic Payload Contract

Every health-like API should be able to include a `diagnostics` array of `DiagnosticItem` objects. Each item represents one finding.

### DiagnosticItem Schema

```python
class DiagnosticItem(BaseModel):
    """A single diagnostic finding for operator-facing APIs."""
    severity: HealthSeverity          # healthy | degraded | unhealthy | unknown
    message: str                      # Human-readable explanation of the finding
    suggestion: str | None = None     # Actionable next step for the operator
    source: str | None = None         # Which subsystem/check produced this (e.g. "connectivity", "bnk_health")
    evidence: dict[str, Any] | None = None  # Machine-readable context (probe results, counts, etc.)
    timestamp: str | None = None      # ISO 8601 when this was assessed
```

### DiagnosticSummary Schema

For endpoints that aggregate multiple diagnostics:

```python
class DiagnosticSummary(BaseModel):
    """Aggregated diagnostic output for health-like endpoints."""
    overall: HealthSeverity           # Rollup of all diagnostic severities
    message: str                      # One-line summary for operator/AI consumption
    diagnostics: list[DiagnosticItem] = []  # Individual findings
    assessed_at: str                  # ISO 8601 when this assessment was made
```

---

## Design Principles

1. **Additive, not replacing** — Diagnostics extend existing responses. The `status` field stays. The `diagnostics` array adds explanation.

2. **Operator-first language** — Messages should use operator vocabulary, not internal implementation terms. "Port 6443 is blocked by a firewall" not "TCP SYN timeout on socket connect".

3. **Suggestion is always actionable** — If we can't suggest a next step, `suggestion` should be `None`, not "Contact support" or "Unknown error".

4. **Evidence is machine-readable** — Evidence contains structured data that UIs or AI assistants can render or reason about. Not just strings.

5. **Severity matches canonical model** — Uses `HealthSeverity` from PLAT-REL-001. Never uses ad-hoc strings.

---

## Example Payloads

### Healthy Cluster Connectivity

```json
{
  "severity": "healthy",
  "message": "Kubernetes API is accessible (v1.30). Latency: 5ms.",
  "suggestion": null,
  "source": "connectivity_probe",
  "evidence": {
    "icmp_reachable": true,
    "icmp_latency_ms": 5.0,
    "tcp_open": true,
    "k8s_api_accessible": true,
    "k8s_version": "1.30"
  },
  "timestamp": "2026-03-27T15:30:00Z"
}
```

### Degraded Fleet Member (Port Blocked)

```json
{
  "severity": "degraded",
  "message": "Host responds to ping (170ms) but port 6443 is blocked.",
  "suggestion": "Request firewall rule to allow TCP 6443 from this server to 10.144.35.73, or enable SSH tunneling.",
  "source": "connectivity_probe",
  "evidence": {
    "icmp_reachable": true,
    "icmp_latency_ms": 170.0,
    "tcp_open": false,
    "tcp_port": 6443,
    "k8s_api_accessible": false
  },
  "timestamp": "2026-03-27T15:30:00Z"
}
```

### Unhealthy BNK Platform

```json
{
  "severity": "unhealthy",
  "message": "BNK data plane has 0 of 3 TMM pods healthy.",
  "suggestion": "Check TMM pod logs for crash reasons. Verify DPF/VLAN configuration.",
  "source": "bnk_health",
  "evidence": {
    "section": "dataPlane",
    "component": "tmm",
    "healthy_count": 0,
    "total_count": 3,
    "pods": ["f5-tmm-abc", "f5-tmm-def", "f5-tmm-ghi"]
  },
  "timestamp": "2026-03-27T15:30:00Z"
}
```

### System Service Degraded

```json
{
  "severity": "degraded",
  "message": "Celery has 0 active workers.",
  "suggestion": "Check Celery container logs. Background tasks will not execute until workers are available.",
  "source": "system_health",
  "evidence": {
    "service": "celery",
    "workers": 0,
    "response_time_ms": 45.2
  },
  "timestamp": "2026-03-27T15:30:00Z"
}
```

### Unknown (Expired License)

```json
{
  "severity": "degraded",
  "message": "BNK license has expired (expired 2026-03-26).",
  "suggestion": "Renew the BNK license via the License page or contact F5 support.",
  "source": "license_check",
  "evidence": {
    "state": "expired",
    "expiry_date": "2026-03-26",
    "days_expired": 1
  },
  "timestamp": "2026-03-27T15:30:00Z"
}
```

---

## Candidate Adoption Endpoints

| Endpoint | Priority | Current | Target |
|----------|----------|---------|--------|
| `GET /clusters/{id}/connectivity` | ✅ Already has | `message` + `suggestion` + probe results | Wrap in `DiagnosticItem` |
| `GET /clusters/connectivity` (batch) | ✅ Already has | Same | Same |
| `GET /system/health` | High | Bare `status: str` per service | Add `diagnostics` to response |
| `GET /operators/fleet/health` | High | Status + counts only | Add `diagnostics` per cluster |
| `GET /clusters/{id}/f5bnk/health` | Medium | Severity tree + pod details | Add top-level `DiagnosticSummary` |
| `GET /clusters/{id}/dpf/health` | Medium | Status string | Add `diagnostics` |
| `GET /clusters/{id}/scanner` | Low | Recommendations array | Already structured, low priority |

---

## Implementation

The `DiagnosticItem` and `DiagnosticSummary` schemas should live in `backend/schemas/diagnostics.py` as shared, domain-agnostic Pydantic models. Endpoints can include them directly or compose them into their existing response schemas.

---

## Related Documents

- [Canonical Status Semantics (PLAT-REL-001)](STATUS_SEMANTICS.md)
- [Strategic Backlog](../STRATEGIC_BACKLOG.md)
- [Sprint Plan — Platform Truthfulness 001](../SPRINT_PLATFORM_TRUTHFULNESS_001.md)
