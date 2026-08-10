# D-021 — Existing-Proxy Discovery & Guided Migration to F5 BNK

- **Status:** Accepted
- **Date:** 2026-06-03
- **Source:** planning session 2026-06-03 (code-grounded scan of `proxy_discovery_service.py` + cluster-scan pipeline + `ClusterScanResults.tsx`)
- **Governing principle:** D-019 (dynamic-by-default — detect *any* controller, not a fixed allowlist)
- **Reuses pattern:** D-018 (discovery cached at scan time)
- **Relates to:** the Benchmarks proxy flow (`POST /api/benchmarks/targets/{id}/discover-proxies`), `proxy_deploy_service` (the BNK deploy target)

## Context

Forge can **deploy** an F5 BNK proxy in front of an LLM service, but it is **blind to proxies a cluster already runs**. A customer with an existing NGINX / Envoy / HAProxy ingress in front of their workloads has no on-ramp: forge neither shows what they have nor offers a path to BNK.

Detection logic already exists, but it is **target-coupled and lives in the wrong flow**:

- `backend/services/proxy_discovery_service.py` — `ProxyDiscoveryService.discover_all(target: BenchmarkTarget)` scans for envoy / nginx / haproxy / f5-bnk / nodeport, but only marks a proxy `found=True` when it **routes to one specific LLM service** (`target.llm_base_url`). Every scanner does a context check (`_has_ingress_to_backend`, `_find_routes_to_backend`) against that target.
- It is wired only into **Benchmarks** (`routes/benchmarks.py` → `discover-proxies`), to answer "which proxy fronts *my* LLM?".
- The **cluster scan** (`services/scanner/__init__.py` → `ClusterScanEnvelope`, rendered by `frontend-v2/src/components/k8s/ClusterScanResults.tsx`) has collectors for cert-manager, multus, sriov, hugepages, storage, gateway-api, dpf, kamaji, bnk-install — but **no proxy collector**.

So the reusable asset is the **detection plumbing** (K8s queries + backend-match helpers), not the entry contract. A cluster scan has no benchmark target; it needs the inverse question: **"what proxies run on this cluster, and what does each one front?"** — target-*agnostic* inventory.

There is **no Ingress→Gateway translation logic anywhere** in the repo (confirmed). "Migrate to BNK" is genuinely new work, and its depth ranges from a recommendation banner to a live traffic cutover.

### Deletion test (per D-019)

The proxy set must be a **dynamic enumeration**, not a gate. The existing hardcoded 5-type `scanners` dict (`proxy_discovery_service.py:114`) is a gate: a cluster running Traefik/Kong/Contour is silently invisible. Per D-019 this is forbidden for the inventory path — detection MUST derive from live `IngressClasses` + `GatewayClasses` + controller deployments, with the named 5 kept only as a **display/labeling overlay** (icon + friendly name), never as the visibility gate.

## Decision

Add **existing-proxy discovery to the cluster scan** and a **phased guided-migration path to F5 BNK**, built by **broadening the existing service rather than duplicating it**.

### 1. Make detection target-optional (refactor, don't fork)

`ProxyDiscoveryService` gains a cluster-inventory mode:

- `discover_all(target=None, ...)` — when `target` is `None`, run **inventory mode**: enumerate proxies cluster-wide and, for each, report the backends it routes to (instead of testing against one LLM). When `target` is set, today's context-aware benchmark behavior is **unchanged**.
- Both consumers — the Benchmarks endpoint and the new cluster-scan collector — call the **same service**. No second copy of the K8s detection code.
- Inventory mode does **not** write `ProxyDeployment` rows (`auto_create=False`); those stay target-scoped to Benchmarks.

### 2. Detect dynamically (D-019)

Inventory mode enumerates **all** `IngressClasses` (`networking.k8s.io/v1`) + **all** `GatewayClasses` (`gateway.networking.k8s.io/v1`) + known controller deployments, deriving proxy identity from `spec.controllerName` / `spec.controller`. The current `{envoy, nginx, haproxy, f5-bnk, nodeport}` set becomes a **labeling overlay** (icon + display name + "is this F5 BNK already?" flag), with a generic fallback for unknown controllers (Traefik, Kong, Contour, …). Unknown ≠ dropped.

### 3. Surface in the cluster scan (low-friction integration)

- **Backend:** a `proxies` analyzer slots into the scan pipeline after `fetch_scan_data()` in `services/scanner/__init__.py`. The result attaches under the existing free-form `prerequisites` dict (`ClusterScanEnvelope.prerequisites: dict[str, Any]` — zero schema friction) as `existing_proxies`.
- **Frontend:** a new `ScanCard` ("Existing Proxies") in `ClusterScanResults.tsx`, using the established `ScanCard` / `StatRow` pattern; extend the `ClusterScanResponse` TS interface in `types/kubernetes.ts`.
- Scan caching is the existing 10-min per-cluster in-memory `_scan_cache` (no Redis/DB change).

### 4. Migration to BNK — phased, ship P1 first

No translation logic exists today, so migration is staged by risk:

- **P1 — Detect + Recommend (ship first).** Inventory card shows each existing proxy, what it fronts, and a **"Migrate to BNK" recommendation** with rationale (e.g. "NGINX ingress fronts `vllm.default:8000` — BNK adds per-route security policy + observability"). A recommendation, not an action. Reuses the scan `recommendations` builder. **Low risk, high value.**
- **P2 — Translate + Preview.** A new translation module renders the equivalent BNK `Gateway` + `HTTPRoute` from the detected Ingress/Route (NGINX/HAProxy `spec.rules[].http.paths[].backend.service` → HTTPRoute `rules[].backendRefs`; Envoy/F5 HTTPRoutes are already the right shape). Surfaced as a **previewable manifest/diff** the user applies. No automatic apply.
- **P3 — Guided cutover.** Translate → deploy BNK alongside the existing proxy (`proxy_deploy_service`) → verify the BNK route serves traffic → cut over → uninstall the old proxy. Live-traffic-affecting; gated behind explicit confirmation and a verify-before-cutover step. Largest scope, designed but **not** committed to a date here.

This ADR commits to **P1 + the inventory/refactor (parts 1–3)**. P2 and P3 are accepted in shape, scheduled later.

## Consequences

- **No duplicate detection code.** The hard K8s logic has one home; the benchmark flow keeps its exact current behavior (target passed → context-aware), the scan flow gets inventory (no target).
- **D-019 compliance.** The scan inventory is discovery-driven; the 5-type table is demoted to an overlay. A new ingress controller appears automatically instead of being silently missed.
- **Cheap P1.** Parts 1–3 are a service refactor + one collector + one card — no schema migration, no new persistence, no new endpoint required (the data rides the existing scan response).
- **Migration is the real cost, and it is deferred.** P2's Ingress→HTTPRoute translation is non-trivial (path-match semantics, annotation handling, multi-path consolidation) and P3 touches live traffic; staging them keeps P1 shippable and low-risk.
- **Honest UX boundary.** P1 must not imply it can *perform* the migration — the card recommends and explains; the apply path arrives in P2/P3. (Guards against the D-017 "success that lies" failure shape.)
- **Benchmarks unaffected.** `ProxyDeployment` rows remain target-scoped; inventory mode is read-only.

## Phasing & tracking

| Phase | Scope | Risk | Status |
|---|---|---|---|
| **P1** | Target-optional inventory + dynamic detection + scan collector + UI card + "migrate" recommendation | Low | Queued |
| **P2** | Ingress/Route → BNK `Gateway`+`HTTPRoute` translation + manifest preview | Medium | Accepted, not scheduled |
| **P3** | Guided deploy-verify-cutover-teardown | High (live traffic) | Accepted, not scheduled |

Roadmap/issue wiring (data-driven roadmap: edit `docs/roadmap.yaml`, run `bin/roadmap-gen.py`) is a follow-up to this ADR.

## References

- Existing detection: `backend/services/proxy_discovery_service.py` (`discover_all`, the 5 `_scan_*` methods, `_has_ingress_to_backend`, `_find_routes_to_backend`)
- Benchmark consumer: `backend/routes/benchmarks.py` (`discover-proxies`); schema `backend/schemas/benchmarks.py` (`ProxyDiscoveryResponse`)
- Scan pipeline: `backend/services/scanner/__init__.py`; envelope `backend/schemas/k8s.py` (`ClusterScanEnvelope`); route `backend/routes/k8s/clusters.py`
- Deploy target: `backend/services/proxy_deploy_service.py`, `backend/tasks/proxy_deploy_tasks.py`
- FE: `frontend-v2/src/components/k8s/ClusterScanResults.tsx`; types `frontend-v2/src/types/kubernetes.ts` (`ClusterScanResponse`)
- Principle: D-019 (dynamic-by-default); pattern: D-018 (discovery cached at scan time); contract hygiene: D-017
