# Operator Deployment Guide

> Deploy the BNK Forge operator on your Kubernetes cluster for remote management.

Last updated: 2026-03-09 | Operator version: 1.1.0 | BNK Forge version: 2.10.78

---

## Overview

The BNK Forge operator is a lightweight agent (~128 MB) that runs on your Kubernetes cluster and connects **outbound** to your BNK Forge instance via WebSocket. This means:

- **No inbound ports needed** on your cluster's network
- **No kubeconfig stored** in BNK Forge — the operator uses its own ServiceAccount
- **Continuous health monitoring** — the operator reports BNK health every 30 seconds
- **Works through NAT, firewalls, and VPNs** — outbound WebSocket only

### When to Use the Operator

| Scenario | Use Operator? |
|---|---|
| Cloud K8s (EKS, AKS, GKE) — can install pods | **Yes** (recommended) |
| On-prem K8s with outbound internet | **Yes** (recommended) |
| Air-gapped / no outbound internet | No — use Direct mode (kubeconfig) |
| Strict pod policies, no custom agents allowed | No — use Direct mode (cloud auth) |
| Quick SE demo / evaluation | Either — Direct is fastest to set up |
| Multi-cluster fleet management | **Yes** (each cluster gets an operator, all phone home) |

---

## Quick Start

### Step 1: Generate Install Command in BNK Forge

1. Open BNK Forge in your browser
2. Navigate to **Operate > Fleet**
3. Open the operator connectivity flow (if needed for your environment)
4. Enter a name for your cluster
5. Copy the generated install command

### Step 2: Install on Your Cluster

Paste the command into a terminal with `kubectl` access to your target cluster:

```bash
helm install bnk-operator oci://ghcr.io/f5/bnk-operator \
  --set controlPlane.url=wss://YOUR_BNK_FORGE_IP:2650/ws/operator \
  --set controlPlane.token=YOUR_GENERATED_TOKEN \
  --set clusterName=my-cluster \
  --namespace bnk-system --create-namespace
```

### Step 3: Verify Connection

Within seconds, you should see the operator appear as "Connected" in the BNK Forge Fleet page.

```bash
# Check the operator pod is running
kubectl get pods -n bnk-system

# Check operator logs
kubectl logs -n bnk-system deployment/bnk-operator

# Check readiness (should return 200)
kubectl exec -n bnk-system deployment/bnk-operator -- wget -qO- http://localhost:8080/readyz
```

---

## Installation Options

### Option A: Helm (Recommended)

```bash
helm install bnk-operator oci://ghcr.io/f5/bnk-operator \
  --set controlPlane.url=wss://bnk-forge.example.com:2650/ws/operator \
  --set controlPlane.token=<token> \
  --namespace bnk-system --create-namespace
```

### Option B: Helm with Custom Values

Create a `values.yaml`:

```yaml
controlPlane:
  url: "wss://bnk-forge.example.com:2650/ws/operator"
  token: "your-registration-token"

clusterName: "prod-us-east-1"

# Polling mode (if WebSocket is blocked by proxy)
# connectivityMode: polling
# pollIntervalSeconds: 5

# TLS for self-signed BNK Forge
tls:
  insecure: false    # Set true ONLY for dev/testing
  caCert: ""         # Paste CA cert PEM here for self-signed

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 128Mi

# Prometheus metrics
metrics:
  enabled: true
  interval: 30s

# Pod disruption budget
podDisruptionBudget:
  enabled: true
  maxUnavailable: 0
```

Install with:
```bash
helm install bnk-operator oci://ghcr.io/f5/bnk-operator \
  -f values.yaml \
  --namespace bnk-system --create-namespace
```

### Option C: Polling Mode (Restrictive Networks)

If your network blocks WebSocket connections (e.g., strict HTTP-only proxies), use polling mode:

```bash
helm install bnk-operator oci://ghcr.io/f5/bnk-operator \
  --set controlPlane.url=https://bnk-forge.example.com:2650 \
  --set controlPlane.token=<token> \
  --set connectivityMode=polling \
  --set pollIntervalSeconds=5 \
  --namespace bnk-system --create-namespace
```

Polling mode uses standard HTTPS requests instead of WebSocket. Slightly higher latency but works through any HTTP proxy.

---

## Configuration Reference

### Required Values

| Value | Description | Example |
|---|---|---|
| `controlPlane.url` | BNK Forge WebSocket endpoint | `wss://10.0.1.5:2650/ws/operator` |
| `controlPlane.token` | Registration token from BNK Forge | Generated in UI |

### Optional Values

| Value | Default | Description |
|---|---|---|
| `clusterName` | Auto-detected | Display name for this cluster in BNK Forge |
| `connectivityMode` | `websocket` | `websocket` or `polling` |
| `pollIntervalSeconds` | `5` | Polling interval (polling mode only) |
| `tls.insecure` | `false` | Skip TLS verification (dev only) |
| `tls.caCert` | `""` | CA certificate PEM for self-signed BNK Forge |
| `tls.existingCaSecret` | `""` | K8s Secret name containing CA cert |
| `healthCheckInterval` | `30` | Seconds between health reports |
| `healthFullReportInterval` | `300` | Seconds between full status dumps |
| `logLevel` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `logFormat` | `text` | `text` or `json` (structured logging) |
| `metrics.enabled` | `false` | Enable Prometheus metrics endpoint |
| `podDisruptionBudget.enabled` | `false` | Enable PDB for high availability |
| `networkPolicy.enabled` | `false` | Restrict egress to BNK Forge + K8s API only |

---

## What the Operator Does

The operator handles these commands from BNK Forge:

| Command | Description |
|---|---|
| `apply_manifests` | Apply K8s manifests via server-side apply (kubectl apply equivalent) |
| `destroy_manifests` | Delete K8s resources |
| `install_helm` | Install or upgrade a Helm release |
| `uninstall_helm` | Remove a Helm release |
| `scan_cluster` | Check cluster prerequisites (CRDs, storage, networking) |
| `get_health` | Return BNK component health (FLO, TMM, Gateways, etc.) |
| `get_resources` | List K8s resources by type |
| `get_logs` | Stream pod logs |

### RBAC Permissions

The operator's ServiceAccount has these permissions:

- **Full access** to F5 BNK CRDs (F5SPKVlan, CNEInstance, etc.)
- **Full access** to Gateway API resources (Gateways, HTTPRoutes, etc.)
- **Read/Write** on core resources (Pods, Services, ConfigMaps, Secrets, Deployments, etc.)
- **Read** on Nodes
- **Helm operations** in all namespaces

The ClusterRole is created by the Helm chart and can be customized via `values.yaml`.

---

## Health Monitoring

The operator continuously monitors BNK health and reports to BNK Forge:

- **Every 30 seconds**: Delta health check (only reports changes)
- **Every 5 minutes**: Full health report (regardless of changes)
- **Components monitored**: FLO, TMM, CWC, DSSM, Observer, Gateways, VLANs, certificates

Health data powers the BNK Health Dashboard in the BNK Forge UI.

### Health Endpoints

The operator exposes health endpoints for Kubernetes probes:

| Endpoint | Purpose | Probe Type |
|---|---|---|
| `GET /healthz` | Liveness — process is running | `livenessProbe` |
| `GET /readyz` | Readiness — connected to BNK Forge | `readinessProbe` |
| `GET /metrics` | Prometheus metrics | Scrape target |

### Prometheus Metrics

When `metrics.enabled: true`, the operator exposes:

| Metric | Type | Description |
|---|---|---|
| `bnk_operator_uptime_seconds` | Gauge | Time since operator started |
| `bnk_operator_connected` | Gauge | 1 if connected to BNK Forge, 0 if not |
| `bnk_operator_commands_total` | Counter | Commands received by type |
| `bnk_operator_command_duration_seconds` | Histogram | Command execution time |
| `bnk_operator_reconnects_total` | Counter | WebSocket reconnection attempts |
| `bnk_operator_health_checks_total` | Counter | Health checks performed |

---

## Troubleshooting

### Operator Not Connecting

```bash
# Check pod status
kubectl get pods -n bnk-system

# Check logs for connection errors
kubectl logs -n bnk-system deployment/bnk-operator

# Common issues:
# - "Connection refused" → BNK Forge not reachable (check URL, firewall)
# - "401 Unauthorized" → Token expired or invalid (regenerate in UI)
# - "TLS handshake error" → Set tls.insecure=true for dev or provide CA cert
```

### Operator Connected but Commands Fail

```bash
# Check RBAC permissions
kubectl auth can-i create gateways.gateway.networking.k8s.io \
  --as=system:serviceaccount:bnk-system:bnk-operator

# Check operator logs for command errors
kubectl logs -n bnk-system deployment/bnk-operator | grep ERROR
```

### Readiness Probe Failing

The readiness probe returns 503 when the operator is not connected to BNK Forge. This is expected during:
- Initial startup (connecting)
- Network interruptions (reconnecting with exponential backoff)
- BNK Forge restart (operator will reconnect automatically)

### High Memory Usage

The operator is designed to use ~64-128 MB. If it exceeds this:

```bash
# Check if health reports are accumulating
kubectl logs -n bnk-system deployment/bnk-operator | grep "health report"

# Restart the operator
kubectl rollout restart deployment/bnk-operator -n bnk-system
```

---

## Upgrading

```bash
# Upgrade to a new operator version
helm upgrade bnk-operator oci://ghcr.io/f5/bnk-operator \
  --reuse-values \
  --namespace bnk-system

# Or with new values
helm upgrade bnk-operator oci://ghcr.io/f5/bnk-operator \
  -f values.yaml \
  --namespace bnk-system
```

## Uninstalling

```bash
helm uninstall bnk-operator --namespace bnk-system
kubectl delete namespace bnk-system
```

The operator will appear as "Disconnected" in BNK Forge. You can remove it from the UI via Operators > Disconnect.

---

## Security Considerations

- The operator makes **outbound connections only** — no inbound ports are opened
- Registration tokens are **single-use** and hashed (bcrypt) in BNK Forge's database
- The operator runs as **non-root** (UID 1000) with a read-only root filesystem
- All capabilities are **dropped** by default
- WebSocket connections use **TLS** (wss://) in production
- NetworkPolicy option restricts egress to only BNK Forge and the K8s API

---

## Related Documents

- [Product Vision](PRODUCT_VISION.md) — Where BNK Forge is heading
- [Operator Architecture](../archive/OPERATOR_ARCHITECTURE.md) — Full technical design of the operator protocol
- [Customer Product Vision](../architecture/CUSTOMER_PRODUCT_VISION.md) — Two connectivity models explained
