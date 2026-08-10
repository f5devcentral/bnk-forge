# UX Roadmap: Simplifying K8s + Network Function Operations

> Make complex Kubernetes and network-function workflows approachable for first-time users while preserving full expert control.

Last updated: 2026-03-31 | Product-state note: avoid treating this roadmap as a release-version ledger.

---

## Problem Statement

Kubernetes is hard. Deploying network functions on Kubernetes is harder. Users shouldn't need to become OpenTofu experts, CIDR block calculators, or Helm timeout format memorizers. They want to say **"I need secure, fast, observable traffic routing on my cluster"** and have BNK Forge translate that intent into infrastructure.

The execution platform is strong; the onboarding and day-to-day UX should continue to improve.

---

## UX Gaps — Status (current branch)

### 1. ✅ The "What Do I Do First?" Problem — RESOLVED
**Was:** Users landed on empty dashboard, unsure what to do.
**Now:** First Deployment Wizard (UX-001) detects first run, guides users through 3 choices → running BNK in under 10 minutes.

### 2. ✅ Raw K8s/Networking Concepts Leak Through — RESOLVED
**Was:** Stack templates exposed raw CIDRs, instance types, manifest versions.
**Now:** Intent-based presets (UX-002) offer Small/Medium/Large sizing. Raw fields are still accessible via "Advanced" toggle (progressive disclosure).

### 3. ✅ Modules First, Recipes Second — RESOLVED
**Was:** Users had to browse catalog, understand each module, wire dependencies manually.
**Now:** Blueprints are the primary deployment path. Modules remain for power users.

### 4. ✅ Errors Are Descriptive, Not Actionable — IMPROVED
**Was:** Error suggestions were text-only.
**Now:** ErrorBoundary catches chunk-load failures with "new version available" prompt, error states on Fleet health and Snapshot history, toast notifications replace console.log.

### 5. ✅ Deployment Is a Log Viewer, Not a Launch Sequence — RESOLVED
**Was:** Deployment view was a terminal log.
**Now:** Visual deployment pipeline with layer progress, real-time status per module.

### 6. ✅ Cluster Onboarding Path Clarity — RESOLVED
**Was:** Cluster onboarding guidance mixed primary and secondary connectivity paths.
**Now:** Kubeconfig-first onboarding (D3) is the default path. Operator connectivity remains secondary/legacy-supported for specific environments.

### 7. ✅ Drift Is Buried — RESOLVED
**Was:** Drift detection buried under project modules.
**Now:** Drift status visible on Command Center dashboard, per-module drift banners.

### Remaining Gap
- **Gateway Topology Phase 3 completion (TOPO-004)** — Topology now includes Backends, Policy Builder, and Configuration Builder. Remaining UX work focuses on finalizing Phase 3 interactions (advanced drag/drop attachment UX, undo/redo polish, and apply previews).

---

## Roadmap Plan

### Phase 1: The First 5 Minutes ✅ COMPLETE

These changes ensure a new user goes from zero to deployed BNK in under 10 minutes with fewer than 5 decisions.

#### 1.1 First Deployment Wizard ✅ Done (UX-001)

**What:** An interactive wizard that's not a tour but actually deploys infrastructure.

```
Welcome to BNK Forge!
Let's get your first network function running.

Step 1: Where's your cluster?
  [ I need to create one on AWS ]  [ I already have one ]

Step 2: (If existing) Connect your cluster
         Paste kubeconfig  — or —  [ Install Operator (copy command) ]
         (If AWS) Pick a region → [us-west-2 v]

Step 3: What do you want to deploy?
  [ Full BNK Stack ]  [ Just a Gateway ]  [ Demo Apps to try it out ]

Step 4: Size your environment
  [ Dev/Test (~$150/mo) ]  [ Standard (~$450/mo) ]  [ Production (~$900/mo) ]

                                                         [ Deploy Now ]
```

Behind the scenes, this selects the right Blueprint, pre-fills all sane defaults, and kicks off parallel execution. The user makes 3-4 choices and gets a running network function.

**For the power user:** Every step has an "I want to customize" expansion. Step 2 can reveal full kubeconfig editor, SSH tunnel config, and credential templates. Step 3 can expand to show individual module selection. Step 4 can expand to show every variable across every module. The wizard is a fast path, not a locked path.

**Technical approach:**
- New React component: `FirstDeploymentWizard.tsx`
- Detects first run (no projects exist) and shows automatically
- Maps wizard choices to existing Stack template + variable overrides
- Calls existing stack deployment API
- Can be re-triggered from Dashboard or Help menu

#### 1.2 Intent-Based Configuration Presets (With Full Override) ✅ Done (UX-002)

**What:** Add human-meaningful choices as a layer *on top of* raw configuration. The raw fields never go away — presets populate them, users override them.

**Network Size:**
```
[ Small (dev) ]          [ Medium (staging) ]       [ Large (production) ]   [ Custom ]
  10.0.0.0/20             10.0.0.0/16                10.0.0.0/12              You decide
  Up to 4K addresses      Up to 65K addresses        Up to 1M addresses

  [ Advanced v ]
    VPC CIDR:        [10.0.0.0/16    ]    <- Editable, preset filled this in
    Public subnets:  [10.0.1.0/24, ...]   <- Full control
    Private subnets: [10.0.10.0/24, ...]  <- Full control
    AZs:             [us-west-2a, 2b, 2c] <- Full control
```

**Cluster Size:**
```
[ Minimum Viable ]       [ Standard ]               [ High Performance ]     [ Custom ]
  2 nodes, 4 vCPU         3 nodes, 8 vCPU            3 nodes, 16 vCPU        You decide
  ~$150/mo                 ~$450/mo                   ~$900/mo

  [ Advanced v ]
    Instance type:     [c5n.4xlarge   ]   <- Editable
    Node count:        [3             ]   <- Editable
    Min nodes:         [2             ]   <- Editable
    Max nodes:         [6             ]   <- Editable
    Disk size (GB):    [100           ]   <- Editable
    K8s version:       [1.31          ]   <- Editable
```

**BNK Profile:**
```
[ Evaluation ]           [ Standard ]               [ High Availability ]    [ Custom ]
  1 TMM, basic gateway    2 TMM, full gateway        3+ TMM, multi-gateway   You decide
  No security policies     Standard policies          WAF + network policies

  [ Advanced v ]
    TMM replicas:          [2                  ]
    Gateway listeners:     [JSON editor         ]
    Security policy:       [YAML editor         ]
    Helm values override:  [Full values.yaml    ]
    BNK manifest version:  [2.2.0-3.2226.0-... ]
```

**The key UX:** Selecting a preset fills in all the advanced fields. The user can then tweak any individual field. The "Custom" option starts with empty fields for the person who wants to build from scratch. **Nothing is locked — presets are suggestions, not constraints.**

**Technical approach:**
- New `presets.ts` configuration file mapping intent → variable values
- Preset selector component shown during stack deployment
- "Advanced" section always visible as a collapsible panel (not hidden)
- Selecting a preset populates the advanced fields; editing any field switches indicator to "Modified"
- "Custom" preset starts with empty/minimal defaults
- Presets stored as named configurations in the database (users can save their own)

#### 1.3 Blueprints as Primary Path (Modules Stay for Power Users) ✅ Done

**What:** Rename "Stacks" to "Blueprints" and make them the primary *starting point* for deployment. The full module catalog remains fully accessible for users who want to build custom configurations from scratch.

- Blueprints are front-and-center on the Dashboard (not hidden under Build > Stacks)
- Each Blueprint shows: visual topology diagram (ReactFlow), time estimate, cost estimate, "Deploy This" button
- "I want to customize" expansion reveals the individual modules within the Blueprint — users can add/remove/reconfigure any module
- Module catalog remains in the sidebar (not hidden, not labeled "Advanced") — it's the path for users who want to hand-pick components
- Users can save their own custom module combinations as personal Blueprints

**The distinction:** Blueprints are the recommended starting point. Modules are the building blocks. Neither replaces the other — they serve different workflows. Blueprints say "here's a proven recipe." Modules say "here are all the ingredients — build what you want."

**Technical approach:**
- Rename throughout UI (Stacks → Blueprints is a string replacement)
- Add Blueprint cards to Dashboard below the stats row
- Add ReactFlow mini-diagram component to Blueprint cards
- "Customize" on a Blueprint opens it in the Project view with all modules visible and editable
- "Save as Blueprint" action on any Project to capture current module configuration

#### 1.4 Copy-Paste Operator Install ✅ Historical / secondary path only

**What (historical):** Earlier UX work supported a copy/paste operator install flow.

```bash
helm install bnk-operator oci://ghcr.io/f5/bnk-operator \
  --set controlPlane.url=wss://10.0.1.5/ws/operator \
  --set controlPlane.token=eyJhbGci... \
  --namespace bnk-system --create-namespace
```

That path is no longer the primary onboarding experience. Kubeconfig-first cluster addition is the default. Operator installation remains a secondary / legacy-supported option for specific outbound-only environments and should be documented as such.

**Technical approach:**
- ~~New component: `ConnectClusterDialog.tsx`~~ — **Removed in D3-CLEANUP (2026-03-10)**
- ~~Backend: `POST /api/operators/generate-install-command`~~ — **Removed in D3-CLEANUP**
- **D3 architecture**: Clusters are now added via kubeconfig on the Kubernetes page.
  No operator install required — fleet health is queried directly via kubeconfig.

---

### Phase 2: The Deployment Experience ✅ COMPLETE

#### 2.1 Visual Deployment Pipeline ✅ Done

**What:** During deployment, show a visual pipeline instead of just a log viewer.

```
Layer 1              Layer 2                Layer 3
+-----------+       +------------+         +------------+
| VPC    OK |------>| EKS    ..  |-------->| BNK        |
+-----------+       +------------+         | (waiting)  |
+-----------+           ^                  +------------+
| Security  |----------/
|    OK     |
+-----------+

EKS: Creating control plane... (7 of ~15 min)
[========--------] 47%

[ Show Full Log ]
```

**Technical approach:**
- New component: `DeploymentPipeline.tsx` using ReactFlow
- Consumes existing dependency graph service (layers are already computed)
- Consumes existing task status + deployment logs via WebSocket
- Progress bar calculated from `ParallelExecutionService.DEFAULT_TIME_ESTIMATES`
- Expandable log panel per module (existing log viewer component)
- Replaces the current modal-only log view during parallel deployments

#### 2.2 Error-to-Action Buttons ✅ Done (ErrorBoundary + toast notifications)

**What:** Turn every error suggestion into a clickable action.

| Current | Better |
|---|---|
| "FAR credentials not configured. Upload your FAR credentials in Settings > Secrets." | "FAR credentials needed" → **[Upload Now]** (opens upload dialog inline) |
| "Cluster not connected. Provide a kubeconfig (primary) or register operator (secondary)." | "Cluster offline" → **[Connect Cluster]** (opens kubeconfig-first cluster connect flow) |
| "Module dependency not deployed. Deploy VPC first." | "Waiting for VPC" → **[Deploy VPC Now]** (triggers init+plan+apply) |
| "Drift detected on 3 resources." | "3 resources drifted" → **[Review Changes]** / **[Reconcile All]** |

**Technical approach:**
- Extend `error-handler.ts` to return an `action` object: `{ label: string, route?: string, callback?: () => void }`
- Extend toast/notification components to render action buttons
- Map ~15 most common error patterns to specific actions
- Each action either navigates to a page or triggers an API call directly

---

### Phase 3: Day 2 Experience (Mostly Complete)

#### 3.1 Cluster Health That Explains and Fixes ✅ Done

**What:** The health dashboard doesn't just show status — it explains why you should care and offers fix buttons.

```
+-------------------------------------------------+
| FLO Operator          * Critical                |
|                                                 |
| WHY: FLO manages your network functions.        |
| Without it, no traffic processing occurs.       |
|                                                 |
| WHAT'S WRONG: Pod CrashLoopBackOff             |
| Last restart: 2 min ago (4 restarts)            |
|                                                 |
| [ View Logs ]  [ Restart Pod ]  [ Diagnostics ] |
+-------------------------------------------------+
```

**Technical approach:**
- Extend health response to include `explanation` and `remediation_actions` per component
- New component: `HealthDetailCard.tsx` with expandable explanation
- Action buttons call existing K8s operations (restart pod, view logs, describe)
- "Run Diagnostics" triggers a predefined check sequence (see 3.3)

#### 3.2 Drift as First-Class Citizen ✅ Done

**What:** Drift is visible everywhere, not buried in project modules.

- Sidebar badge showing drift count (always visible)
- Dashboard "Attention Needed" section includes drift items (already partially there)
- Drift detail view shows side-by-side diff (backend already computes this)
- One-click **[Reconcile]** (re-apply desired state) or **[Accept]** (update desired state to match reality)

**Technical approach:**
- Add drift count to the global dashboard stats query
- Sidebar badge component reading from React Query cache
- New `DriftDetailPanel.tsx` with diff viewer (can use `react-diff-viewer`)
- Reconcile action calls existing `apply` workflow on affected modules
- Accept action updates the module's stored state to match live state

#### 3.3 Runbook Automation ✅ Done

**What:** Pre-built diagnostic sequences for common problems.

Example: "My gateway isn't routing traffic"
1. Check Gateway status → Programmed? Accepted?
2. Check HTTPRoutes → Attached to gateway? Valid backends?
3. Check backend Services → Endpoints exist? Pods healthy?
4. Check TLS certificates → Valid? Not expired?
5. Check TMM pods → Running? Ready?
6. Check DNS → Hostname resolving?

Each step shows pass/fail and links to the relevant resource.

**Technical approach:**
- New `RunbookService` in backend that orchestrates sequential checks
- Each check is a K8s API query with pass/fail evaluation
- Results streamed via WebSocket as each step completes
- New `RunbookWizard.tsx` component showing step-by-step progress
- Start with 3-5 runbooks for the most common issues
- Runbook definitions stored as JSON configurations (extensible)

#### 3.4 Config Snapshots and Rollback ✅ Done

**What:** Before any change, auto-snapshot. After any failure, one-click rollback.

- Every `apply` operation auto-snapshots the current state first
- Snapshots stored in DB with timestamp and trigger (manual, auto-before-apply, auto-before-upgrade)
- "Restore" button on any snapshot re-applies that configuration
- Snapshot diff shows what changed between any two points in time

**Technical approach:**
- Extend existing config export (`/f5bnk/export`) to save to DB as `ConfigSnapshot` model
- Pre-apply hook in deployment task that creates snapshot before execution
- New `SnapshotHistory.tsx` component with timeline view
- Restore calls existing config import workflow

---

### Phase 4: Multi-Cluster & Fleet Management ✅ COMPLETE

#### 4.1 Fleet Dashboard ✅ Done (UX-012)

**What:** One view showing all connected clusters with aggregate health.

```
Connected Clusters                                    [Connect Cluster]
+--------------------------------------------------------------+
| prod-us-east-1     * Healthy    BNK 2.2    3 routes   2 TMM |
| staging-us-west-2  * Warning    BNK 2.2    1 route    1 TMM |
| dr-eu-west-1       * Offline    last seen: 5m ago            |
+--------------------------------------------------------------+

[Compare Configs]  [Sync Staging -> Prod]  [Upgrade All]
```

**Technical approach:**
- Fleet page is the canonical multi-cluster dashboard (operators tab merged)
- Kubeconfig-first architecture powers health aggregation
- Aggregate health endpoint: `GET /api/fleet/health`
- Operator-based connectivity remains supported as a secondary path

#### 4.2 Cross-Cluster Config Promotion ✅ Done

**What:** Export config from staging, diff against prod, apply delta.

```
Staging (source)                    Production (target)
+--------------------------+        +--------------------------+
| Gateway: 2 listeners     |   ->   | Gateway: 2 listeners     |
| Routes: 5 (1 NEW)        |   ->   | Routes: 4                |
| Policies: 3              |   =    | Policies: 3              |
+--------------------------+        +--------------------------+

Changes to apply:
  + HTTPRoute "api-v2" (new route from staging)

[Apply to Production]  [Export Diff]
```

**Technical approach:**
- Extend existing config export/import/diff APIs
- New component: `ConfigPromotionWizard.tsx` with source/target picker
- Diff visualization using the existing diff computation
- Apply uses operator fan-out to push changes to target cluster

---

## Design Principles

### 1. Progressive Disclosure — Not Dumbing Down
Show the simple version first. **Every advanced option is one click away, always.** We are not hiding complexity — we are layering it. The person who wants to hand-pick `c5n.4xlarge`, specify `10.0.47.0/24` for their data plane subnet, set Helm timeout to `12m30s`, and tune TMM replica affinity rules — they get all of that. We just don't make everyone wade through it to deploy their first gateway.

The pattern everywhere is:
```
[ Simple Choice ]              <- Default view
  [ Advanced v ]               <- Always visible toggle
    CIDR: 10.0.0.0/16         <- Full control, every field
    Instance: c5n.4xlarge
    Helm timeout: 5m
    Custom values.yaml: {...}
```

This is about **respecting both audiences**: the network engineer who's done this 100 times and wants granular control, AND the platform team who just needs BNK running so their app team can ship.

### 2. Intent Over Implementation (With Full Override)
Users express what they want ("production-grade BNK"), not how to get it. But the implementation details are never hidden — they're one expand/click away. Presets are **starting points**, not ceilings. Every preset can be overridden field-by-field.

### 3. Errors Are Recovery Paths
Every error message includes: what happened, why it matters, and a button that fixes it.

### 4. Zero Surprise Defaults
If the user doesn't configure something, the default should be safe and reasonable. Never fail because a default is missing. Power users override defaults; new users benefit from them.

### 5. Show Don't Tell
Visual deployment pipelines, not log files. Health dashboards, not JSON responses. Topology diagrams, not resource lists. But always with a "Show Raw" / "View YAML" / "Full Log" option for the technofiles who want to see exactly what's happening under the hood.

---

## Implementation Priority

| Phase | Items | Status | Impact |
|---|---|---|---|
| **Phase 1** | First Deployment Wizard, Intent Presets, Blueprints, Operator Install | ✅ Complete | Transforms first-run experience |
| **Phase 2** | Visual Pipeline, Error Actions | ✅ Complete | Makes deployment delightful |
| **Phase 3** | Health Explanations, Drift First-Class, Runbooks, Snapshots | ✅ Complete | Makes Day 2 the product |
| **Phase 4** | Fleet Dashboard, Config Promotion | ✅ Complete | Unlocks multi-cluster value |

**All four phases are complete.** Remaining UX work is focused on topology phase polish and scaling preset evolution.

---

## Related Documents

- [Product Vision](PRODUCT_VISION.md) — The big picture of where BNK Forge is heading
- [Engineering Improvements](ENGINEERING_IMPROVEMENTS.md) — Technical foundations that support the UX vision
- [User Guide](USER_GUIDE.md) — Current user documentation
