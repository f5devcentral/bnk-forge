# MCP-PROD-010: Autonomous-Use Eligibility Rules

**Status:** Complete
**Version:** 2.11.0

---

## Purpose

Define which MCP tools are safe for autonomous AI invocation vs. which
require human confirmation before execution.

---

## Classification Framework

### Tier 1: Fully Autonomous (AI can invoke freely)

**Criteria:** Read-only, no side effects, no credential exposure, no cost impact.

| Tool Pattern | Examples | Rationale |
|-------------|---------|-----------|
| List/Get operations | `list_projects`, `get_cluster`, `get_health` | Read-only data retrieval |
| Status queries | `get_task_status`, `get_drift_status` | Observational only |
| Search/filter | `search_modules`, `list_helm_releases` | No state change |
| Health checks | `get_system_health`, `get_fleet_health` | Diagnostic only |
| Metadata reads | `get_module_outputs`, `get_cluster_resources` | Informational |

### Tier 2: Autonomous with Guardrails (AI can invoke with constraints)

**Criteria:** Creates resources but is reversible, low blast radius, or
generates a preview before executing.

| Tool Pattern | Examples | Guardrails |
|-------------|---------|------------|
| Plan/preview | `plan_module` | Shows diff, doesn't apply |
| Create (non-destructive) | `create_project`, `add_module` | Reversible via delete |
| Config changes | `update_drift_settings` | Toggles, not infrastructure |
| Notifications | `create_alert_channel` | Informational setup |

### Tier 3: Human Confirmation Required (AI must ask first)

**Criteria:** Mutates infrastructure, costs money, or is irreversible.

| Tool Pattern | Examples | Why Confirm |
|-------------|---------|-------------|
| Apply/deploy | `apply_module`, `deploy_stack` | Creates real infrastructure |
| Destroy | `destroy_module`, `delete_project` | Irreversible data loss |
| Credential ops | `rotate_credentials`, `delete_secret` | Security-sensitive |
| Cluster mutations | `install_helm`, `uninstall_helm` | Affects live workloads |
| User management | `create_user`, `delete_user` | Access control |
| Bulk operations | `deploy_all_modules`, `destroy_all` | High blast radius |

### Tier 4: Prohibited (AI must never invoke)

**Criteria:** System-level changes that could break the platform.

| Tool Pattern | Examples | Rationale |
|-------------|---------|-----------|
| Database admin | (none exposed) | Could corrupt state |
| Auth system | (none exposed) | Could lock out users |
| Encryption keys | (none exposed) | Could make data unreadable |

---

## Decision Matrix

```
Is the tool read-only?
  ├── Yes → Tier 1 (Autonomous)
  └── No → Does it create real infrastructure?
              ├── No → Is it easily reversible?
              │         ├── Yes → Tier 2 (Autonomous + Guardrails)
              │         └── No → Tier 3 (Human Confirm)
              └── Yes → Does it cost money or affect live workloads?
                        ├── Yes → Tier 3 (Human Confirm)
                        └── No → Tier 2 (Autonomous + Guardrails)
```

---

## Implementation

The `tool_catalog.json` already tracks `risk_class` per tool. Map risk
classes to eligibility tiers:

| Risk Class | Eligibility Tier |
|-----------|-----------------|
| `read` | Tier 1 |
| `config` | Tier 2 |
| `mutate` | Tier 3 |
| `destroy` | Tier 3 |

AI agents consuming MCP tools should check `risk_class` before invocation
and prompt the user for confirmation on `mutate` and `destroy` operations.
