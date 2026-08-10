# PLAT-REL-004: Operational Risk Surfacing

**Status:** Accepted
**Version:** 2.11.0
**Last updated:** 2026-03-28

---

## Purpose

Ensure high-impact operational blockers are surfaced clearly in the product UX and health outputs, so operators are never surprised by silent failures.

---

## High-Impact Blockers

| Blocker | Severity | Current Surfacing | Where to Surface |
|---------|----------|-------------------|------------------|
| **Expired cloud credentials** | Critical | GCP/Azure: logged as warning only (stub) | Dashboard attention card, project detail banner |
| **Cluster unreachable** | Critical | Connectivity probe → health dashboard | Dashboard attention card, cluster list badge |
| **Port blocked** | High | Connectivity probe diagnostic | Cluster detail diagnostics panel |
| **Auth failure (K8s)** | High | Connectivity probe diagnostic | Cluster detail, red badge in list |
| **License expired (BNK)** | High | BNK health check | BNK health page, fleet summary |
| **Database disk >85%** | High | `make check-disk` (manual) | System settings, health endpoint |
| **Worker offline** | High | Worker heartbeat (Redis) | System settings, task failures |
| **SSH tunnel down** | Medium | Tunnel manager logs | Cluster detail connectivity tab |
| **Module state locked** | Medium | Workspace lock service | Module card status badge |
| **Drift detected** | Medium | Drift check service | Project detail, attention card |
| **Stale operator** | Low | Operator cleanup task | Cluster detail operator tab |

---

## Surfacing Locations

### 1. Dashboard Attention Cards

The dashboard shows `AttentionCard` components for items requiring operator action.

**Current triggers:**
- Module in error state → "View Error"
- Drift detected → "Review Drift"

**Should also trigger for:**
- Cluster unreachable (connectivity failed)
- Credential expiring within 24h
- BNK license expired
- Worker offline for >5 minutes

### 2. Health Endpoint Enhancement

`GET /api/system/health` currently returns `{"status": "healthy"}`.

**Should also report:**
```json
{
  "status": "healthy",
  "timestamp": "...",
  "checks": {
    "database": "ok",
    "redis": "ok",
    "workers": "ok",
    "disk": "ok"
  },
  "warnings": [
    {"type": "credential_expiry", "message": "AWS credentials for project 'vpc-prod' expire in 6 hours"}
  ]
}
```

### 3. Cluster List Badges

Cluster list currently shows connectivity status badges. These should use the canonical badge vocabulary (UX-OPS-002) with clear severity:

| State | Badge | Color | Tooltip |
|-------|-------|-------|---------|
| Connected | `Connected` | Green | Last checked: {time} |
| Unreachable | `Unreachable` | Red | {diagnostic message} |
| Auth Failed | `Auth Failed` | Red | Check kubeconfig permissions |
| Port Blocked | `Port Blocked` | Orange | {port} unreachable from server |
| Unknown | `Unknown` | Gray | Not yet checked |

### 4. BNK Health Dashboard

BNK health page shows component status. Should prominently surface:
- License status and expiry date
- Component version mismatches across fleet
- Degraded components with actionable diagnostic

### 5. System Settings Page

System page should show infrastructure health:
- Database size and disk usage
- Worker count and status
- Redis connectivity
- Backup status (last successful backup)

---

## Severity Guidance

| Severity | Criteria | User Impact | Response Time |
|----------|----------|-------------|---------------|
| **Critical** | Operations blocked, data at risk | Cannot deploy, risk of data loss | Immediate |
| **High** | Key features degraded | Some operations fail | Within 1 hour |
| **Medium** | Non-critical feature affected | Workaround available | Within 1 day |
| **Low** | Cosmetic or minor inconvenience | No workflow impact | Next sprint |

---

## Implementation Priority

1. **Dashboard attention cards** for cluster unreachable + credential expiry (highest visibility)
2. **Health endpoint** warnings array for monitoring integration
3. **Cluster list badges** using canonical badge vocabulary
4. **System settings** infrastructure health section
