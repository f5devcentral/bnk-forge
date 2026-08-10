# BNK-Forge as a Customer Product — What Changes

> "What if this became the official F5 BNK deployment and lifecycle management tool that customers use?"

This document examines how every architectural decision shifts when the audience changes from internal SEs to F5 customers.

---

## The Fundamental Shift

| Dimension | Internal SE Tool | Customer Product |
|---|---|---|
| **Who runs it** | F5 SEs who know BNK internals | NetOps/Platform engineers who may not know K8s deeply |
| **Where it runs** | SE laptop, demo VM | Customer data center, customer cloud, air-gapped environments |
| **How long it runs** | Hours to days (demo lifecycle) | Months to years (production lifecycle) |
| **Tolerance for failure** | "I'll SSH in and fix it" | Zero — it must self-heal or guide the user |
| **Security posture** | Internal network, trusted users | Customer network, compliance requirements, audit trails |
| **Support model** | Slack the SE who built it | F5 TAC, documentation, self-service troubleshooting |
| **Scale** | 1-5 deployments per instance | Potentially dozens of clusters, multi-team |
| **Upgrade path** | `git pull && docker compose up` | Managed upgrades with rollback, zero downtime |
| **Branding** | "BNK-Forge" (internal name) | "F5 BNK Manager" or similar (product name) |

---

## 1. The Architecture Question Is Now Answered: Two Connectivity Models

For an internal tool, we debated "1 container vs 9." For a customer product, we considered "run on K8s." But both miss the real answer.

**BNK-Forge runs independently** (Docker on laptop/VM/server). It connects to customer clusters via **two first-class connectivity models** — because not every environment is the same.

### Why NOT run BNK-Forge on the customer's K8s cluster

The initial instinct — "customers have K8s, so run BNK Manager on K8s" — has three fatal flaws:

1. **Chicken and egg** — The "Build It All" persona doesn't have a cluster yet. They need BNK-Forge running BEFORE any cluster exists.
2. **Can't fix what you're standing on** — If the cluster is unhealthy, the management tool is also down.
3. **Infra management requires independence** — You can't resize the node group you're sitting on.

### Two Connectivity Models

Not every customer can install an operator on their cluster. Some environments have strict pod policies, air-gapped networks with no outbound, managed K8s with restricted control planes, or compliance rules against running third-party agents. For these environments, BNK-Forge connects directly using the credentials and tunnels it already supports today.

```
┌──────────────────────────────────────────────────────────────────────┐
│  BNK-FORGE (Control Plane)                                            │
│  Runs: Docker on laptop, VM, server                                   │
│                                                                       │
│  ┌──────────┐ ┌────────┐ ┌──────────────────────────────────────┐   │
│  │ FastAPI   │ │ React  │ │ Engines                              │   │
│  │ API + WS  │ │ UI     │ │ ├─ OpenTofu     (local, cloud infra) │   │
│  │           │ │        │ │ ├─ Operator     (remote, via WS)     │   │
│  │           │ │        │ │ └─ Direct K8s   (remote, via kube)   │   │
│  └──────────┘ └────────┘ └──────────┬────────────────┬──────────┘   │
│                                      │                │              │
└──────────────────────────────────────┼────────────────┼──────────────┘
                                       │                │
         ┌─────────────────────────────┘                │
         │  MODEL A: Operator                           │  MODEL B: Direct
         │  (operator phones home)                      │  (BNK-Forge reaches out)
         │                                              │
    ┌────▼────────────────────────┐    ┌────────────────▼─────────────────┐
    │  CLUSTER (operator model)   │    │  CLUSTER (direct model)           │
    │                             │    │                                   │
    │  bnk-operator/ (~128 MB)    │    │  No agent installed               │
    │  ├── Connects OUTBOUND      │    │  BNK-Forge connects via:          │
    │  │   to BNK-Forge via WS    │    │  ├── kubeconfig (file or inline)  │
    │  ├── Executes kr8s, helm    │    │  ├── EKS/AKS/GKE cloud auth      │
    │  ├── Reports health         │    │  ├── SSH tunnel (on-prem)         │
    │  └── Uses ServiceAccount    │    │  └── Credential templates         │
    │                             │    │                                   │
    │  f5-bnk/ (managed)          │    │  f5-bnk/ (managed)                │
    └─────────────────────────────┘    └───────────────────────────────────┘
```

### Model A: Operator (phones home) — preferred when possible

The operator is a lightweight pod (~128 MB) installed on the customer's cluster. It makes an **outbound** WebSocket connection to BNK-Forge — no inbound ports needed on the customer's network.

| Aspect | Detail |
|---|---|
| **Install** | `helm install f5/bnk-operator --set controlPlane.url=wss://... --set controlPlane.token=...` |
| **Auth** | ServiceAccount RBAC — no kubeconfig or cloud creds stored in BNK-Forge |
| **Network** | Outbound WSS only — works through NAT, firewalls, VPNs |
| **Health** | Continuous — operator reports BNK health every 30s, even when BNK-Forge isn't looking |
| **Disconnect visibility** | BNK-Forge shows "operator disconnected" — you know the cluster has issues |
| **Multi-cluster** | Natural — each cluster gets an operator, all phone home to one BNK-Forge |
| **Best for** | Cloud clusters, environments that allow custom pods, greenfield deployments |

> See [OPERATOR_ARCHITECTURE.md](./OPERATOR_ARCHITECTURE.md) for the full technical design.

### Model B: Direct (BNK-Forge reaches out) — for restricted environments

BNK-Forge connects to the cluster's K8s API using credentials it already manages. **This is what we run in production today** — EKS auth via AWS SSO, SSH tunnels for on-prem clusters, kubeconfig files. It works and will continue to work.

| Aspect | Detail |
|---|---|
| **Setup** | Add cluster in BNK-Forge UI, provide kubeconfig or configure cloud auth |
| **Auth methods** | Kubeconfig (file/inline), AWS SSO/IAM (EKS), Azure AD (AKS), GCP (GKE), SSH tunnel + kubeconfig (on-prem) |
| **Network** | BNK-Forge needs network access to K8s API (direct, VPN, or SSH tunnel) |
| **Health** | On-demand — BNK-Forge polls cluster when dashboard is open or on scheduled checks |
| **Credential management** | Credential templates handle refresh (AWS SSO expiry, token rotation) |
| **Multi-cluster** | Supported — one credential template per cluster |
| **Best for** | Managed K8s with restrictions, air-gapped (BNK-Forge on same network), legacy clusters, environments that prohibit custom agents |

### When to use which

| Scenario | Model |
|---|---|
| Cloud K8s (EKS, AKS, GKE) — can install pods | **A: Operator** |
| Cloud K8s — strict pod policies, no custom agents allowed | **B: Direct** (cloud auth) |
| On-prem K8s — can install pods, has outbound internet | **A: Operator** |
| On-prem K8s — air-gapped, no outbound | **B: Direct** (SSH tunnel + kubeconfig) |
| On-prem K8s — BNK-Forge on same network | **B: Direct** (kubeconfig) |
| "Build It All" — creating infra from scratch | **A: Operator** (auto-installed after cluster creation) |
| SE demo / quick eval | **B: Direct** (fastest to set up, no operator install step) |
| Multi-cluster fleet | **A: Operator** (one BNK-Forge, many operators phoning home) |

### What this means for our architecture

Both models share the same UI, the same module catalog, and the same deployment logic. The only difference is the transport:

| | Model A: Operator | Model B: Direct |
|---|---|---|
| **Engine** | `OperatorEngine` — sends commands via WebSocket | `KubernetesEngine` — kr8s with kubeconfig / `OpenTofuEngine` — existing tofu modules |
| **K8s auth** | ServiceAccount (automatic) | Kubeconfig / cloud auth / SSH tunnel (credential templates) |
| **Credential storage** | Registration token only | Cloud creds + kubeconfig in credential templates (encrypted) |
| **Health monitoring** | Continuous push from operator | Scheduled pull from BNK-Forge |
| **Offline cluster** | "Operator disconnected" visible immediately | "Connection failed" on next poll |

**The engine router picks the right engine per cluster:**
```python
class EngineRouter:
    def get_engine(self, project, module) -> DeploymentEngine:
        cluster = project.cluster
        
        if module.requires_cloud_infra:
            return self.opentofu_engine          # Always local
        
        if cluster.has_connected_operator:
            return OperatorEngine(cluster.operator)   # Model A
        
        if cluster.has_kubeconfig_or_credentials:
            return KubernetesEngine(cluster.kubeconfig) # Model B
        
        raise EngineError("No connectivity — install operator or configure credentials")
```

---

## 2. Authentication & Authorization — Now Mandatory

For an internal tool: "everyone's an admin, no login."
For a customer product: this is a dealbreaker on day one.

### What's needed

```
┌─────────────────────────────────────────────────────────┐
│  Authentication                                          │
│                                                          │
│  ○ Local users (username/password) — for air-gapped      │
│  ○ OIDC/OAuth2 (Okta, Azure AD, Google) — enterprise SSO │
│  ○ LDAP/Active Directory — traditional enterprise        │
│  ○ K8s ServiceAccount tokens — for API/CLI access        │
│  ○ API keys — for CI/CD integration                      │
│                                                          │
├─────────────────────────────────────────────────────────┤
│  Authorization (RBAC)                                    │
│                                                          │
│  Roles:                                                  │
│  ├── admin     — Full access, manage users, system config│
│  ├── operator  — Deploy, destroy, modify BNK             │
│  ├── viewer    — Read-only access, view dashboards       │
│  └── auditor   — View audit logs, compliance reports     │
│                                                          │
│  Scoping:                                                │
│  ├── Global    — Access all projects/clusters            │
│  ├── Project   — Access specific projects only           │
│  └── Cluster   — Access specific clusters only           │
│                                                          │
├─────────────────────────────────────────────────────────┤
│  Audit Trail                                             │
│                                                          │
│  Every action logged with:                               │
│  ├── Who (authenticated user identity)                   │
│  ├── What (action: deploy, destroy, modify, view)        │
│  ├── When (timestamp, timezone)                          │
│  ├── Where (project, cluster, module)                    │
│  ├── Outcome (success, failure, error message)           │
│  └── Source IP (for compliance)                          │
└─────────────────────────────────────────────────────────┘
```

### Existing hooks in the codebase

The codebase already has placeholders:
- `JWT_SECRET_KEY` in config (`core/config.py`)
- `UnauthorizedError` / `ForbiddenError` exception classes (`core/errors.py`)
- `triggered_by` field on Task model (currently always `"user"` or `"system"`)
- `created_by` field on CloudCredentialTemplate
- `AuditLog` model (`models.py`) — exists but sparsely used
- `authStore` in frontend Zustand stores — exists but empty

The FastAPI middleware pattern for auth is straightforward:
```python
# Dependencies injection:
async def get_current_user(token: str = Depends(oauth2_scheme)):
    ...

@router.post("/projects")
async def create_project(project: ProjectCreate, user: User = Depends(get_current_user)):
    ...
```

### Implementation approach

**Phase 1:** Local users with JWT. Simple `users` table, bcrypt passwords, JWT tokens. This unlocks the audit trail and `triggered_by` field.

**Phase 2:** OIDC integration. Use `authlib` or `python-jose` for OIDC. Customer configures their IdP URL in settings. BNK Manager redirects to IdP for login.

**Phase 3:** RBAC with project-scoped permissions. Role assignments stored in DB. Middleware checks permissions before every route.

---

## 3. Security — Enterprise Grade

### What changes

| Area | Internal Tool | Customer Product |
|---|---|---|
| **Credential storage** | Fernet encryption with local key file | K8s Secrets, HashiCorp Vault integration, or customer-managed KMS |
| **TLS** | HTTP by default, "use external proxy for HTTPS" | TLS mandatory. Certs from cert-manager (which BNK already requires) |
| **Secrets in transit** | Plaintext on internal network | mTLS between components, encrypted WebSocket |
| **Container image** | `latest` tag, no scanning | Signed images, vulnerability scanning, SBOM |
| **Docker socket** | Mounted into container | Eliminated (K8s-native deployment) |
| **CORS** | `ALLOWED_ORIGINS=*` | Strict origin policy |
| **CSP** | None | Content Security Policy headers |
| **Dependency supply chain** | `pip install` from PyPI | Pinned versions, hash verification, internal mirror |
| **OpenTofu state** | Local filesystem or S3 | Encrypted at rest, access-controlled, backed up |
| **Compliance** | N/A | SOC 2, FedRAMP considerations for government customers |

### Credential Storage Evolution

For a customer product, storing encrypted credentials in our own database is a liability. Better options:

```
Option A: K8s Secrets (embedded mode)
  └── BNK Manager creates K8s Secrets for cloud creds, F5 licenses
  └── K8s handles encryption at rest (if etcd encryption is enabled)
  └── RBAC controls who can read secrets
  └── No custom encryption code needed

Option B: External Secrets Operator integration
  └── Customer stores credentials in Vault/AWS SM/Azure KV
  └── ESO syncs them to K8s Secrets
  └── BNK Manager reads K8s Secrets
  └── Credentials never enter our database

Option C: Vault integration (direct)
  └── BNK Manager has Vault client
  └── Reads credentials at execution time
  └── Shortest credential exposure window
  └── Requires Vault (not all customers have it)
```

**Recommendation:** Support all three. Default to K8s Secrets (Option A) for simplicity. Option B and C for enterprise customers who require it.

---

## 4. Reliability — Production Grade

The long-lived environment analysis revealed critical gaps. For a customer product, these become P0 issues:

### Health Monitoring (The BNK Health Dashboard becomes central)

```
┌─────────────────────────────────────────────────────────┐
│  F5 BNK Health                                ● HEALTHY  │
│                                                          │
│  Platform Components                                     │
│  ├── FLO Operator .............. v2.2.0  ● Running       │
│  ├── CWC ....................... v2.2.0  ● Running       │
│  ├── DSSM ...................... v2.2.0  ● Running       │
│  └── Observer .................. v2.2.0  ● Running       │
│                                                          │
│  Data Plane                                              │
│  ├── TMM Instances ............. 2/2     ● Ready (7/7)   │
│  ├── Traffic throughput ........ 2.4 Gbps                │
│  └── Active connections ........ 12,847                   │
│                                                          │
│  Networking                                              │
│  ├── Gateway ................... bnk-gw  ● Programmed    │
│  │   ├── :80 HTTP .............. 3 routes attached       │
│  │   └── :443 HTTPS ............ 3 routes attached       │
│  └── VLANs .................... 2/2     ● Up             │
│                                                          │
│  Security & Compliance                                   │
│  ├── License ................... ● Active (expires 2027)  │
│  ├── TLS Certificates .......... 4/4 valid (58 days)     │
│  ├── Firewall Policy ........... ● Active (12 rules)     │
│  └── Last Audit ................ 2 hours ago              │
│                                                          │
│  System Health                                           │
│  ├── Credentials ............... ● Valid                  │
│  ├── Cluster Connectivity ...... ● Connected              │
│  ├── Last Health Check ......... 30 seconds ago           │
│  └── Drift Status .............. No drift detected        │
│                                                          │
│  [Upgrade BNK]  [Export Config]  [Backup]  [Logs]        │
└─────────────────────────────────────────────────────────┘
```

### Self-Healing Capabilities

For a customer product, "alert and wait for human" isn't enough. The system should auto-remediate common issues:

```python
class BNKHealthMonitor:
    """Continuous health monitoring with auto-remediation."""
    
    async def check_and_heal(self, cluster):
        issues = await self.scanner.full_health_check(cluster)
        
        for issue in issues:
            if issue.auto_remediable:
                await self.remediate(issue)
                await self.audit_log(f"Auto-remediated: {issue}")
            else:
                await self.alert(issue)
    
    # Auto-remediable issues:
    # - Pod restart (kubectl delete pod → K8s recreates)
    # - Certificate renewal (cert-manager handles, we monitor and trigger if needed)
    # - Credential refresh (re-assume role, re-fetch SSO token)
    # - Stale tunnel reconnection
    # - Redis/DB connection pool refresh
    
    # NOT auto-remediable (alert only):
    # - License expiry
    # - Node failure  
    # - Persistent storage full
    # - Network partition
    # - BNK version incompatibility
```

### Alerting — Out of Band

In-app notifications are fine for an active user. For a customer product managing production BNK:

```
Alert Channels:
  ├── In-app notifications (existing)
  ├── Email (SMTP configuration)
  ├── Webhook (generic — integrates with PagerDuty, Opsgenie, etc.)
  ├── Slack / Microsoft Teams
  ├── Syslog (enterprise SOC integration)
  └── SNMP traps (network operations centers)

Alert Severity:
  ├── Critical — BNK data plane down, license expired, credentials dead
  ├── Warning  — Certificate expiring, credential refresh failing, drift detected
  ├── Info     — Deployment complete, upgrade available, health check passed
  └── Debug    — Detailed operation logs (opt-in)
```

---

## 5. Multi-Tenancy & Multi-Cluster

### Single cluster is the starting point
Most customers will start with one BNK deployment on one cluster. One operator phones home to BNK-Forge. Simple.

### Multi-cluster is natural with the operator model
Larger customers will have production, staging, DR, and regional clusters. Each gets an operator. They all phone home to the same BNK-Forge instance. **No hub cluster, no kubeconfig sprawl, no credential management.**

```
┌─────────────────────────────────────────────────────────┐
│  F5 BNK-Forge (your laptop / VM / server)                │
│                                                          │
│  Connected Clusters                                      │
│  ┌──────────────────────────────────────────────────┐   │
│  │ prod-us-east-1   ● Healthy   BNK 2.2   3 routes │ ← operator connected │
│  │ staging-us-west-2 ● Warning   BNK 2.2   1 route  │ ← operator connected │
│  │ dr-eu-west-1     ● Offline   last seen: 5m ago  │ ← operator disconnected │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  [Connect Cluster]  [Deploy BNK to New Cluster]          │
│  [Compare Configs]  [Sync Staging → Prod]                │
└─────────────────────────────────────────────────────────┘
```

Key advantages over the previous "hub cluster" model:
- **No kubeconfigs stored** — each operator uses its own ServiceAccount
- **Outbound-only networking** — operators phone home through NAT/firewalls/VPNs
- **Cluster failure is visible from outside** — "operator disconnected 🔴" vs being blind because the hub is also down
- **No credential refresh** — no EKS token expiry, no SSO session management per cluster

### Configuration as Code / GitOps Compatibility

Customers with mature platform teams will want:
- Export BNK configuration as declarative YAML/JSON
- Store in Git alongside application configs
- Import/apply configuration from Git (GitOps workflow)
- Diff configurations between clusters
- Promote configurations (staging → production)

This doesn't mean we become Argo CD or Flux. It means we provide:
```
GET  /api/clusters/{id}/bnk/export          → YAML/JSON of full BNK config
POST /api/clusters/{id}/bnk/import          → Apply config from YAML/JSON
GET  /api/clusters/{a}/bnk/diff/{b}         → Diff between two clusters
POST /api/clusters/{id}/bnk/apply-from/{id} → Copy config from another cluster
```

The operator makes this especially powerful — BNK-Forge exports config from cluster A, then sends `apply_manifests` commands to cluster B's operator. No cross-cluster network access needed.

---

## 6. Packaging & Distribution

There are **two artifacts** to distribute — BNK-Forge (the control plane) and the operator (per-cluster agent).

### How customers get it

| Artifact | Distribution | How |
|---|---|---|
| **BNK-Forge** (control plane) | Docker image | `docker run f5/bnk-forge` or `docker compose up` |
| **BNK-Forge** (team/enterprise) | Docker Compose or optional Helm chart | Run on a shared VM or management K8s cluster |
| **BNK Operator** (per cluster) | Helm chart (primary) | `helm install f5/bnk-operator` from F5 charts repo |
| **BNK Operator** (air-gapped) | OCI artifact | Pull from `repo.f5.com/charts/bnk-operator` |
| **BNK Operator** (quick install) | kubectl apply | `kubectl apply -f https://bnk-forge:2650/api/operators/install/<token>.yaml` |

### The Operator Helm Chart (per-cluster — the primary distribution)

```
charts/bnk-operator/
  Chart.yaml
  values.yaml
  templates/
    deployment.yaml           # Single lightweight pod (~128 MB)
    serviceaccount.yaml       # With RBAC for managing BNK resources
    clusterrole.yaml          # BNK CRDs, Gateway API, core K8s, cert-manager
    clusterrolebinding.yaml
    configmap.yaml            # Operator config (health check intervals, etc.)
```

```yaml
# values.yaml (customer configures these)
controlPlane:
  url: "wss://bnk-forge.example.com/ws/operator"  # BNK-Forge WebSocket endpoint
  token: ""                   # Registration token from BNK-Forge

resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    cpu: 200m
    memory: 128Mi

healthCheck:
  interval: 30               # seconds between health reports
  fullReportInterval: 300     # seconds between full status dumps
```

### BNK-Forge Docker Compose (the control plane)

BNK-Forge itself keeps its existing Docker Compose deployment. For customers who want something lighter:

```yaml
# Slim mode — single container for small teams
docker run -d \
  -p 2650:2650 \
  -v bnk-forge-data:/data \
  f5/bnk-forge:latest

# Full mode — with OpenTofu infra support
docker compose up -d
```

### Versioning & Compatibility Matrix

Customers need to know what works with what:

```
BNK-Forge Version  |  Operator Version  |  BNK Versions  |  K8s Versions
───────────────────┼───────────────────┼────────────────┼───────────────
1.0.x              |  1.0.x             |  BNK 2.1, 2.2  |  1.28-1.31
1.1.x              |  1.0.x, 1.1.x     |  BNK 2.1-2.3   |  1.29-1.32
```

Note: The operator and BNK-Forge versions are decoupled. An older operator can talk to a newer BNK-Forge (within a major version) and vice versa. The WebSocket protocol is versioned.

---

## 7. Day 2 Operations — The Core Value Proposition

For an internal tool, Day 2 is a nice-to-have. For a customer product, **Day 2 IS the product.** Deployment is a one-time event. Operations are every day.

### What customers need daily

```
┌─────────────────────────────────────────────────────────┐
│  DAILY OPERATIONS (the product's core value)             │
│                                                          │
│  ● Health Dashboard — "Is my BNK healthy?"               │
│    └── Component status, traffic metrics, certificate    │
│        expiry, license status                            │
│                                                          │
│  ● Route Management — "Add/modify/remove traffic rules"  │
│    └── Visual Gateway API route builder (already started │
│        with ListenerBuilder.tsx, RoutingRulesBuilder.tsx)│
│                                                          │
│  ● Policy Management — "Update firewall/network rules"   │
│    └── Visual security/network policy builder            │
│                                                          │
│  ● Troubleshooting — "Why isn't traffic flowing?"        │
│    └── Traffic flow visualization                        │
│    └── Log aggregation (already have PodLogsViewer)      │
│    └── Event timeline                                    │
│    └── Configuration validation                          │
│                                                          │
├─────────────────────────────────────────────────────────┤
│  WEEKLY/MONTHLY OPERATIONS                               │
│                                                          │
│  ● BNK Upgrades — "Upgrade from 2.2 to 2.3"             │
│    └── Pre-upgrade validation                            │
│    └── Rolling upgrade with traffic drain                │
│    └── Post-upgrade health check                         │
│    └── Rollback on failure                               │
│                                                          │
│  ● Scaling — "Add more TMM capacity"                     │
│    └── Resize deployment (Small → Medium → Large)        │
│    └── Add/remove TMM instances                          │
│                                                          │
│  ● Certificate Rotation — "Renew certificates"           │
│    └── cert-manager handles auto-renewal                 │
│    └── BNK Manager monitors and alerts on failure        │
│                                                          │
│  ● Backup/Restore — "Backup my BNK configuration"        │
│    └── Export full config (routes, policies, certs, etc.) │
│    └── Restore to same or different cluster              │
│                                                          │
│  ● Compliance — "Show me what changed"                    │
│    └── Audit log with user attribution                   │
│    └── Configuration change history                      │
│    └── Drift detection and remediation                   │
│                                                          │
├─────────────────────────────────────────────────────────┤
│  RARE OPERATIONS                                         │
│                                                          │
│  ● Initial Deployment — "Install BNK on my cluster"      │
│    └── The cluster-first wizard (Section 9 of review)    │
│                                                          │
│  ● Disaster Recovery — "My cluster died, restore BNK"    │
│    └── Import config backup to new cluster               │
│    └── Re-deploy with saved configuration                │
│                                                          │
│  ● Migration — "Move BNK to a different cluster"         │
│    └── Export from source, import to target              │
│    └── DNS cutover guidance                              │
└─────────────────────────────────────────────────────────┘
```

### The Insight: Deployment Is <5% of the Product Value

```
Customer lifecycle with BNK:

  Day 1:        Deploy BNK (the wizard — 30 minutes)
  Day 2-30:     Configure routes, policies (daily operations)
  Day 30-365:   Monitor, troubleshoot, upgrade, scale
  Day 365+:     Ongoing operations, compliance, DR planning

  ┌────────────────────────────────────────────────────┐
  │ ████ Deploy (5%)                                    │
  │ ████████████████████████████████████████████ Operate│
  └────────────────────────────────────────────────────┘
```

The current codebase is ~70% deployment infrastructure (execution engine, workspace manager, stack templates) and ~30% operations (K8s browser, Helm, drift). For a customer product, this ratio should flip.

---

## 8. What This Changes About the Container/Infra Decision

With the operator architecture, BNK-Forge stays as a Docker Compose application. The question shifts from "how do we deploy on K8s" to "how do we make the control plane lean and reliable."

### PostgreSQL: Stays (but offer SQLite for small deployments)

**PostgreSQL stays** because:
- Multi-user access (RBAC means multiple concurrent users)
- Audit logs need reliable, queryable storage
- Operator registration tokens and cluster state need transactional integrity
- Configuration history and health snapshots need persistence

**SQLite option** for single-user / small-team deployments:
- SE demos, POCs, single-engineer usage
- No external database dependency
- Data stored in a mounted volume
- Upgrade path to PostgreSQL when team grows

### Redis: Stays (the operator model makes it more important)

**Redis becomes MORE important** because:
- WebSocket pub/sub bridges operator connections to browser sessions (health updates from operator → dashboard in real-time)
- Operator message routing — commands from UI/API reach the correct operator WebSocket
- Cache for operator health reports (reduce load when multiple users view dashboard)
- Session storage for authenticated users

### Celery: Evolves but doesn't disappear

The operator handles BNK/K8s operations remotely. But Celery is still needed for:
- **OpenTofu operations** — infrastructure creation runs locally, is long-running, needs subprocess management
- **Bulk operations** — "upgrade BNK across 5 clusters" sends commands to 5 operators simultaneously, tracks progress
- **Scheduled tasks** — periodic health snapshots, credential expiry checks, drift detection triggers

The change: Celery workers no longer need kubeconfigs, kubectl, or network access to customer clusters. They only need OpenTofu (for the "Build It All" persona) and the ability to send commands through the WebSocket to operators. This makes the worker image much smaller.

```
CURRENT worker image: ~800 MB-1 GB
  └── Python, OpenTofu, AWS CLI, kubectl, Docker CLI, Infracost

FUTURE worker image: ~200-300 MB
  └── Python, OpenTofu, AWS CLI (only — no kubectl, no Docker CLI)
  └── K8s operations handled by operator, not worker
```

---

## 9. The "Eat Our Own Dog Food" Opportunity

With the operator architecture, BNK-Forge typically runs outside the managed clusters. But for enterprise deployments where BNK-Forge runs on a management K8s cluster with BNK, we can serve the BNK-Forge UI through a BNK Gateway:

```yaml
# BNK-Forge's own Gateway API resources (optional — enterprise deployment)
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bnk-forge
  namespace: bnk-forge
spec:
  parentRefs:
    - name: bnk-gateway
      namespace: f5-bnk
  hostnames:
    - bnk-forge.example.com
  rules:
    - matches:
        - path: {type: PathPrefix, value: /api}
      backendRefs:
        - name: bnk-forge-api
          port: 8000
    - matches:
        - path: {type: PathPrefix, value: /}
      backendRefs:
        - name: bnk-forge-ui
          port: 80
```

This is a powerful proof point: "F5 BNK is so reliable that we run our own management tool behind it."

Even in the Docker Compose deployment model, the demo itself serves as proof — BNK-Forge manages the very BNK deployment that could serve BNK-Forge if we chose to run it on K8s.

---

## 10. API & CLI — Not Just a GUI

Customers will want to integrate BNK Manager into their workflows:

### REST API (already exists, needs auth + docs)
- The FastAPI backend already generates OpenAPI spec
- Add authentication headers
- Version the API (`/api/v1/...`)
- Generate SDK clients (Python, Go, TypeScript) from OpenAPI spec

### CLI Tool
```bash
# Login
bnk-manager login --server https://bnk-manager.example.com

# Deploy BNK
bnk-manager deploy --cluster prod --profile standard \
  --license-jwt-secret f5-license-jwt \
  --far-key-secret f5-far-key

# Check health
bnk-manager health --cluster prod
# OUTPUT:
# BNK Health: HEALTHY
# FLO: v2.2.0 ● Running
# TMM: 2/2 instances ● Ready
# Gateway: Programmed (6 routes)
# License: Active (expires 2027-03-15)
# Certificates: 4/4 valid

# Add a route
bnk-manager route add \
  --cluster prod \
  --gateway bnk-gateway \
  --hostname api.example.com \
  --path-prefix /v1 \
  --backend-service api-v1 \
  --backend-port 8080

# Export config
bnk-manager config export --cluster prod > bnk-prod.yaml

# Import config (promote staging to prod)
bnk-manager config import --cluster prod < bnk-staging.yaml

# Upgrade BNK
bnk-manager upgrade --cluster prod --version 2.3.0 --rolling
```

### Terraform Provider (for IaC purists)
```hcl
provider "bnk-manager" {
  endpoint = "https://bnk-manager.example.com"
  token    = var.bnk_manager_api_token
}

resource "bnk_deployment" "prod" {
  cluster_id = "prod-us-east-1"
  version    = "2.2"
  profile    = "production"
  
  gateway {
    name = "bnk-gateway"
    listener {
      port     = 443
      protocol = "HTTPS"
      tls_secret = "tls-cert"
    }
  }
  
  route {
    hostname = "api.example.com"
    backend  = "api-service:8080"
  }
}
```

---

## 11. How This Changes the Implementation Phases

### Revised Roadmap for Customer Product

| Phase | Weeks | Focus | Key Deliverables |
|---|---|---|---|
| **Phase 1** | 1-4 | Foundation | Quick wins, bug fixes, error handling, auth (local users + JWT) |
| **Phase 2** | 5-8 | K8s Engine + Module Defs | kr8s engine, cluster scanner, Python module definitions — shared by operator and direct engine |
| **Phase 3** | 9-14 | Operator | Operator pod (kr8s + helm), WebSocket protocol, operator registration, health watching, Helm chart for operator |
| **Phase 4** | 15-20 | Product Core | BNK Health Dashboard (powered by operator health reports), route/policy builders, audit trail, alerting (webhook), OIDC auth |
| **Phase 5** | 21-26 | Day 2 Operations | BNK upgrade workflow (via operator), backup/restore, config export/import, multi-cluster, CLI tool |
| **Phase 6** | 27-32 | Enterprise | RBAC with project scoping, Vault integration, LDAP, SNMP, compliance reports |

### What Moves Up in Priority

| Item | Was | Now | Why |
|---|---|---|---|
| Authentication | Phase 4 (nice to have) | **Phase 1** (must have) | Can't ship without auth |
| Operator | Not planned | **Phase 3** (critical path) | Eliminates kubeconfig problem, enables multi-cluster, unlocks health dashboard |
| BNK Health Dashboard | Phase 4 | **Phase 4** | Core product value — powered by operator health reports |
| Audit trail | Not planned | **Phase 4** | Enterprise requirement |
| Alerting (webhook/email) | Not planned | **Phase 4** | Production monitoring — triggered by operator health events |
| Config export/import | Not planned | **Phase 5** | DR, multi-cluster — operator executes import/export commands |
| CLI tool | Not planned | **Phase 5** | Automation/CI integration |

### What Moves Down or Changes

| Item | Was | Now | Why |
|---|---|---|---|
| OpenTofu infra modules | Central | **Optional/secondary** | Most customers have clusters already ("I Have a Cluster" persona) |
| Helm chart for BNK-Forge itself | Previously "Phase 4: Helm Chart" | **Deferred** | BNK-Forge stays Docker Compose. Operator is the Helm chart. |
| Single-container mode | Target state | **BNK-Forge slim image** | `docker run f5/bnk-forge` for SE demos and single-user |
| Multi-cloud infra (Azure/GCP) | Phase 5 | **Phase 6+** | Focus on BNK operations first |

### The key architectural insight

Phase 2 builds the kr8s engine and Python module definitions. Phase 3 wraps that into an operator. The SAME code that would have run in the KubernetesEngine (direct kr8s) now runs inside the operator pod. The difference is where it executes (locally vs in-cluster) and how commands are delivered (direct API call vs WebSocket).

---

## 12. The Name & Positioning

"BNK-Forge" is an internal name that describes the building/creation aspect. For a customer product focused on lifecycle management:

Possible names:
- **F5 BNK Manager** — straightforward, clear
- **F5 BNK Console** — implies management UI
- **F5 BNK Control Plane** — technical but accurate
- **F5 BNK Studio** — modern, suggests a workspace

The key positioning shift:
- FROM: "A tool that deploys BNK using Terraform"
- TO: "The management plane for F5 BNK — deploy, operate, monitor, and scale your BNK deployment"

---

## Summary: What Stays, What Changes

### Keep (The Good Bones)
- Module catalog with `module.json` metadata — becomes the "supported components" registry
- Dependency graph and variable wiring — still needed for deploy orchestration
- Credential template abstraction — becomes the cloud/cluster connection manager
- K8s resource browser — becomes the operations UI
- BNK-specific components (ListenerBuilder, RoutingRulesBuilder) — core Day 2 value
- Helm service — BNK lifecycle is Helm-based (FLO chart)
- WebSocket real-time updates — essential for health dashboards

### Change (Architectural Shifts)
- Execution engine: OpenTofu → operator (remote kr8s/helm) for BNK operations
- Cluster connectivity: Direct kubeconfig → operator phones home via outbound WebSocket
- Credential model: Kubeconfigs stored in DB → no kubeconfigs; operator uses ServiceAccount
- Authentication: None → JWT + OIDC + RBAC
- Credential storage: Fernet in DB → K8s Secrets / Vault (for cloud creds only)
- Alerting: In-app only → Webhook/email/Slack/SNMP
- Background tasks: Celery for everything → Celery for OpenTofu only; operator handles K8s ops
- Health monitoring: None → Continuous via operator health reports + auto-remediation

### Add (New Capabilities)
- Lightweight K8s operator (~128 MB pod, Helm chart for distribution)
- Operator registration flow ("Connect Your Cluster" → one-liner → operator phones home)
- BNK Health Dashboard powered by continuous operator health reports
- BNK upgrade workflow via operator commands
- Configuration export/import/diff via operator (multi-cluster, DR)
- Audit trail with user attribution
- CLI tool for automation
- Enterprise auth (OIDC, LDAP)
- Enterprise alerting (webhook, email, SNMP)
