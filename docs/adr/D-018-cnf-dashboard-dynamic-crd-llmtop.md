# D-018 — CNF dashboard + dynamic CRD discovery + CR management + llmtop exporter

- **Status:** Proposed (draft)
- **Date proposed:** 2026-05-25
- **Backlog id:** `architecture-cnf-dashboard-dynamic-crd`
- **Source:** Capability relook of two external tools — `InfraWhisperer/llmtop` (LLM inference monitor, already vestigially baked into forge) and `mahdymo/k8s-cr-dashboard` ("K8s Ops Hub"; F5-flavored CRD ops dashboard), with `pehlicd/crd-wizard` (253★) as a maturity reference.
- **Full RFC:** `rfc_cnf_dashboard_dynamic_crd_llmtop.md` (user memory).
- **Depends on:** none to start. Touches (new) `backend/services/crd_discovery_service.py`, `backend/routes/k8s/crds.py`; (extend) `backend/core/k8s_resource_registry.py`, `backend/routes/k8s/resources.py`, `backend/services/kubernetes_service.py`; (new FE) `frontend-v2/src/pages/CNF.tsx` + `cnf-parts/`; (extend FE) `components/k8s/F5BNKTopologyViewer.tsx`; (llmtop) `bnk-operator/`, `backend/tasks/backend_health_task.py`, `backend/services/backend_health_service.py`.
- **Resume trigger:** user request 2026-05-25 to integrate CNE/CNF capabilities and improve the rough AI-Analyzer/llmtop surface; priority confirmed = dynamic CRD discovery first, llmtop = in-cluster exporter.

## Context

Forge's K8s Custom-Resource layer is **gated by a static registry** (`backend/core/k8s_resource_registry.py`, ~100 hardcoded types). **Correction (2026-05-25, verified live):** forge *can already* list CRD **definitions** (the generic resource route + the `customresourcedefinition` registry entry already enumerate all installed CRDs — e.g. 104 on `aws-syd-test`). The real limitation is narrower and more fundamental: the static registry acts as a **gate** on *instance* access — forge can only list/get **instances** of the ~100 kinds it hardcodes; instances of any *other* installed CRD return **HTTP 400** (verified: `analyzers.k8s.f5.com`, `rabbitmqs.k8s.f5.com`, `dssms.k8s.f5.com`, `coremonds.k8s.f5.com` all 400). That gate contradicts the actual intent, which is **discovery, not a hardcoded allowlist**.

The corrected architecture: **dynamic discovery is the source of truth** for "what resource types exist in a cluster"; the **static registry is demoted to a curated *enrichment overlay*** (display names, categories, icons for kinds forge has opinions about) that must **not gate** what is listable. The headline deliverable is therefore *"browse/manage instances of any discovered CRD"* — lifting the registry gate on the resource route — for which P1 (CRD discovery) is the necessary substrate.

Forge also cannot create/edit CRs, has topology only for F5 BNK, and its AI-Analyzer telemetry is a bespoke in-process `vllm:*` `/metrics` parser that lost GPU/multi-engine richness when the baked-in `llmtop` binary was dropped (its API-proxy scrape silently failed *from outside* the cluster — see `followup_llmtop_k8s_scrape_investigation`).

The two external tools demonstrate the missing capabilities cleanly:
- **K8s Ops Hub** has real dynamic CRD discovery (`crd_discovery.py`: `ApiextensionsV1Api.list_custom_resource_definition()`, F5-group filter, 120 s TTL cache, static fallback), schema-driven CR **form editors** (VirtualServer/TransportServer/TlsProfile/IngressLink/ExternalDns — all F5 CRDs), a services→pods→containers **topology** graph (React Flow + dagre; the user's "ant trails" = animated routing edges), cross-context diff, and a live events stream.
- **llmtop** auto-discovers inference pods and emits model-grouped, multi-engine (vLLM/SGLang/Dynamo/NIM/TGI/…) metrics incl. GPU, in a JSON mode forge can consume.

Forge already has the scaffolding to absorb this: the F5 BNK page vertical-slice pattern (`pages/F5BNK.tsx` + `f5bnk-parts/`), a reusable health rollup (`services/bnk/health.py`, `lib/health-severity.ts`), topology viewers (`F5BNKTopologyViewer.tsx`, `ResourceTopologyView.tsx`), and `reactflow ^11.11.4` as a dependency. **We extend, not greenfield.**

## Decision

Make **dynamic discovery the source of truth** for resource types; **demote the static registry to a curated enrichment overlay** (display name/category/icon), not a gate. Build a generic **Custom-Resource hub** with **CNF/CNE category groupings** modeled on the F5 BNK page; add a guarded CR write path; generalize topology; and replace the in-process LLM-metrics parser with **llmtop run in-cluster as an exporter**. Phased as tracer-bullet slices, priority-ordered:

1. **P1 — Dynamic CRD discovery (foundational, DONE).** New `crd_discovery_service.py` + `GET /k8s/clusters/{id}/crds?group=`. Lists installed CRDs via `ApiextensionsV1Api`, **discover-then-merge** with the static registry (registry metadata enriches known kinds; discovery surfaces parsed group/version/plural/scope + known-vs-unknown `source`). TTL-cached per cluster. **Reach: direct kubeconfig only** (operator-relay deferred — see Out of Scope). Pattern ref: K8s Ops Hub `crd_discovery.py`.
1b. **P1.5 — Discovery-backed instance access (THE headline gap — lift the registry gate).** Today the generic resource route 400s on any kind not in the static registry, so instances of ~71 of 104 installed CRDs are unreachable. Use the (group, version, plural, scope) from P1's discovery to list/get **instances of any discovered CRD** via `CustomObjectsApi`, removing the hardcoded-allowlist gate. This is the change that fulfills the discovery intent; P1 is its substrate.
2. **P2 — CNF page (read-only).** Clone the F5 BNK slice: `pages/CNF.tsx` + `cnf-parts/{Sidebar,ResourceTable,DetailPanel,resource-registry,constants}` → `hooks/useCNFResources.ts` → P1 discovery + P1.5 instance access → `KubernetesService`. Sidebar categories from discovered CRDs grouped by API group / curated category. Health section reuses the rollup.
3. **P3 — CR write path ("CRD Wizard").** Schema-driven form (react-hook-form + Zod) from `openAPIV3Schema`, YAML toggle, server-side-apply via `POST/PUT /k8s/clusters/{id}/resources/{type}`. **Gated on the authz/audit story in §Risks — does not ship before it.**
4. **P4 — Topology generalization.** Promote `F5BNKTopologyViewer.tsx` to a reusable services→pods→containers graph (keep reactflow v11; add dagre layout). Backend topology builder mirrors K8s Ops Hub `routers/topology.py` (label-selector matching, animated routing edges, pod→logs).
5. **P5 — llmtop exporter.** Run llmtop in-cluster (snapshot `--once --output json` on a schedule, or long-running exporter) hosted/relayed by the bnk-operator; forge consumes the JSON for model-grouped, multi-engine, GPU-aware metrics; retire the bespoke parser in `backend_health_task.py`.
6. **P6 (optional, deferred).** Cross-context CR diff; live events stream.

## Out of scope

- Replacing the static registry — discovery **augments** it, never deletes it (display metadata, icons, categories still come from the registry).
- Bumping `reactflow` v11 → xyflow v12 just to match the reference.
- Verbatim reuse of GPL-3.0 (`crd-wizard`) or unlicensed (`K8s Ops Hub`) code — patterns only.
- GitOps manifest-push and Templates pages (K8s Ops Hub features that overlap forge blueprints).

## Consequences

- **Single source of "what's installed."** Discovery + merge gives every CR surface (CNF page, KubernetesV2, topology) the real cluster picture instead of a hardcoded list.
- **Write path is the risk surface.** Cluster-mutating CR apply must derive success from the returned object/status, not HTTP 2xx (`feedback_http_success_not_operation_success`), with dry-run + audit + an explicit authz model.
- **llmtop in-cluster ties off multiple follow-ups** (`followup_llmtop_k8s_scrape_investigation`, `followup_real_ttft_histogram_in_shim`, `followup_f5biganalyzer_status_filter`, `followup_weight_change_history`) and removes bespoke parser maintenance.
- **Reuse keeps cost down:** P2/P4 lean on existing F5 BNK page + topology + health-rollup + reactflow; P1 is a small additive service.

## Risks / open questions

- **Security:** CR write needs authz/RBAC, server-side-apply dry-run, and audit before shipping (P3 gate).
- **Operator vs kubeconfig reach:** discovery/write/topology/llmtop must work for operator-reached clusters, not only direct-kubeconfig.
- **llmtop distribution + RBAC:** in-cluster SA perms for pod discovery/scrape; image availability to customer clusters (precedent: `followup_smartllm_shim_image_ghcr`); operator as host/relay.
- **Terminology:** "CNF" (telco network functions) vs forge "CNE" (F5 Cloud-Native Edge / CNEInstance). Recommend a generic CR hub with CNF + CNE category groupings rather than a narrowly-named page.

## Follow-ups / known limitations

- **Authz on non-curated CR instance reads (P1.5).** Post gate-lift, the
  generic resource route (`GET /api/k8s/clusters/{id}/resources/{type}`) allows
  any viewer-role user to list instances of *any* installed CRD, not only
  curated registry kinds.  The current `require_viewer` gate is correct for
  now (single-tenant / team-controlled clusters), but should be revisited
  when true multi-user / tenant isolation lands: consider role-gating or
  per-cluster permission scopes for non-curated CR reads.
  (Ref: PR #125 review comment; D-018 §Risks "Security".)

## References

- RFC: `rfc_cnf_dashboard_dynamic_crd_llmtop.md` (full capability map, gap table, phasing, prior-art inventory).
- External: `InfraWhisperer/llmtop`, `mahdymo/k8s-cr-dashboard` (K8s Ops Hub), `pehlicd/crd-wizard`.
- Forge prior art: `frontend-v2/src/components/k8s/F5BNKTopologyViewer.tsx`, `frontend-v2/src/components/modules/ResourceTopologyView.tsx`, `frontend-v2/src/pages/F5BNK.tsx` + `f5bnk-parts/`, `backend/services/bnk/health.py`, `backend/core/k8s_resource_registry.py`, `backend/routes/k8s/resources.py`.
- Related: D-016 (helm celery — same in-cluster/operator data-plane), `feedback_http_success_not_operation_success`.
