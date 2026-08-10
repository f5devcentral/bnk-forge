# D-024 — F5 BIG-IP eBPF Observability (EOB) Integration: Observed Flow Data → Topology Overlay

- **Status:** Accepted; **P1 (validate) committed**, P2 (ingest) + P3 (topology overlay) accepted-in-shape, **gated on P1's findings.**
- **Date:** 2026-06-03
- **Source:** planning session 2026-06-03 — external research on F5 EOB (= MantisNet Tawon CVF) + code-grounding of forge's operator-ingest + topology seams.
- **Reuses:** the operator→backend ingest pattern (`llm_metrics_service`), the topology builder/viewer, D-018 (CRD discovery), Helm deploy.
- **Relates to:** D-022 (flow data as a fleet/member health signal), D-019 (dynamic-by-default), D-017 (honest contract — don't imply flow coverage we don't have).

## Context

Forge's topology viewer (`topology_builder_service.py` → reactflow `F5BNKTopologyViewer`) renders **only inferred edges** — `owns` (ownerRef) and `selects` (Service label-selector). It has **zero observed traffic data**: no flow rates, no real east-west talker→talker edges, no latency/error overlay (code-verified). "What is actually talking to what, and how much" is unanswerable today.

**F5 BIG-IP eBPF Observability (EOB)** fills exactly that gap. The research established:

- **What it is:** F5's rebrand of **MantisNet Tawon CVF** (acquired Aug 2025), shipping today as a **certified image** (`quay.io/mantisnet/tawon`). A **per-node, privileged DaemonSet eBPF agent** — **not** a DPU feature, **not** in the BNK (TMM) data plane. It captures kernel-level L4/L7 flows without sidecars or app instrumentation.
- **What it emits:** **CNFlow** — serialized **JSON** flow records (service-to-service + latency/error + deep protocol metadata; strong 5G/telco lineage: SBI/PFCP/NGAP/DNS/HTTP2) over an **event message bus (NATS/Kafka)** plus a **REST/GraphQL API**. Notably **not OTLP, not Prometheus**.
- **Relationship to BNK:** a *companion CNF* extending Cloud-Native Edition, horizontal across the F5 portfolio — **independent of the DPU** and of BNK's own analyzer/OTel telemetry. It surfaces a **different data class** (observed east-west flows) than BNK's TMM-pod metrics, so it **enriches** rather than overlaps.

### Forge's reusable seams (grounded)

- **Ingest transport:** the operator→backend pattern is message-type-extensible — `{type: "llm_metrics", ...}` over websocket + an HTTP-polling equivalent, registration-token auth, stored as Redis snapshots. A new `{type: "ebpf_flows", ...}` (or a backend-side poller) drops in the same way.
- **Topology schema is extensible but flow-blind today:** `TopologyEdge` has only `kind="owns"|"selects"` and **no stats field**; `TopologyNode.meta` is a free dict. Flow edges attach by adding `kind="flows"` + an edge `stats` payload.
- **Storage:** **latest-snapshot only** (Redis, 60s TTL) — **no time-series store exists** (verified absent). Fine for a live overlay; a constraint for history.
- **Discovery + deploy:** D-018 CRD discovery auto-surfaces EOB's CRDs; forge already orchestrates Helm.

### The load-bearing unknown

The f5.com product page exposes **no deployment docs, no public Helm chart, no documented REST/GraphQL schema**, and EOB is **almost certainly entitlement-gated** (CNE / sales — "Contact us" only). Everything technical above is corroborated from the certified Red Hat/quay image + pre-acquisition MantisNet docs, *not* a current F5 API contract. **Forge must not build a consumer against an unconfirmed contract.**

## Decision

Integrate EOB as a **net-new observability source whose headline value is overlaying real observed flow edges on forge's topology view** — but **gate all build work behind a validation spike**, because the API surface and licensing are the binding risk.

### Phasing

| Phase | Scope | Status |
|---|---|---|
| **P1 — Validate (spike)** | Against a **live EOB image**: confirm (a) **pullability/licensing** of `quay.io/mantisnet/tawon` (entitlement path), (b) the **actual egress contract** — REST/GraphQL schema and/or NATS/Kafka CNFlow shape as F5 ships it *now* (not the MantisNet legacy), (c) the **deploy footprint** (Helm chart, CRDs, privileged-SCC requirements). Output: a go/no-go + a confirmed CNFlow schema. | **Committed** |
| **P2 — Deploy + ingest** | Forge deploys the EOB DaemonSet (Helm) and discovers its CRDs (D-018). Backend adds a **CNFlow poller** — **REST/GraphQL pull first** (lightest; mirrors the existing scrape→message-type→Redis pipeline via a new `ebpf_flows` type), message-bus (NATS/Kafka) consumer deferred. Latest-snapshot storage initially. | Accepted, gated on P1 |
| **P3 — Topology flow overlay** | Extend `TopologyEdge` with `kind="flows"` + a `stats` payload (rate/latency/errors); render observed east-west edges on the reactflow viewer (the real "ant trails"). Optionally feed per-member flow health into D-022. | Accepted, gated on P1 |

### Scope boundaries

- **Generic Kubernetes service-map slice first.** EOB's deep telco protocol metadata (SBI/PFCP/NGAP) is out of forge's initial scope; the service-to-service + latency/error slice is the target.
- **REST/GraphQL pull before message-bus.** Don't take a NATS/Kafka infra dependency until the streaming volume justifies it.
- **Latest-snapshot overlay before time-series.** Persisting historical flows is a separate decision (forge has no TSDB today); P3 ships a live overlay, history is a follow-up.

### Guardrails

1. **No consumer before the contract is confirmed** (P1 gate) — the API is inferred from legacy/acquired docs; F5 may have changed it. Build nothing against assumptions.
2. **Privileged DaemonSet** — forge's deploy path must handle the SCC/privileged affordance explicitly and surface it to the operator (security-visible).
3. **Format honesty (D-017)** — CNFlow is JSON over a bus/REST, *not* OTLP/Prometheus; don't route it through the metrics pipeline as if it were. Flow ingestion is new code, not a config tweak.
4. **Don't over-claim coverage** — observed flows reflect what the eBPF agent on each node actually sees; the overlay must distinguish *observed* edges from *inferred* (`owns`/`selects`) ones, never silently merge them into one "this is the truth" graph.
5. **Licensing surfaced** — if EOB is entitlement-gated, forge must fail clearly ("EOB not licensed/available") rather than partially deploy.

## Consequences

- **Closes a real gap cheaply where it can.** The topology viewer goes from *inferred* to *observed* — high-value, and it reuses three existing seams (ingest message-type, topology schema, CRD discovery/Helm). The genuinely new code is the CNFlow poller + the flow-edge schema/render.
- **Risk is front-loaded and contained.** P1 is a pure spike; nothing is built until licensing + API are confirmed. If P1 returns "no public contract / hard-gated," the ADR parks as a watch-item with the design intact — no sunk consumer code.
- **Architecturally additive, not entangled.** EOB sits beside BNK's analyzer/OTel telemetry (different data class), so no rework of existing telemetry; it can later feed D-022 member health.
- **Time-series is a deliberate non-goal for now.** Live overlay first; a TSDB decision (for flow history / trends) is explicitly deferred — consistent with forge's current latest-snapshot model.

## References

- External: [F5 BIG-IP eBPF Observability](https://www.f5.com/products/big-ip-services/ebpf-observability) · [Red Hat catalog — mantisnet/tawon (certified image)](https://catalog.redhat.com/en/software/containers/mantisnet/tawon/645b4941086c676cb58fa4cd) · [F5 acquires MantisNet](https://www.f5.com/company/blog/f5-acquires-mantisnet-to-enhance-cloud-native-observability-in-the-f5-application-delivery-and-security-platform) · [DevCentral — visibility for telco/cloud-native](https://community.f5.com/kb/technicalarticles/visibility-for-modern-telco-and-cloud%E2%80%91native-networks/345354) · [BNK OTel collectors/Grafana (for contrast)](https://clouddocs.f5.com/bigip-next-for-kubernetes/2.0.0-GA/spk-otel-visualize.html)
- Internal seams: `backend/services/llm_metrics_service.py`, `backend/routes/operator_ws.py` + `operator_polling.py` (message-type ingest), `backend/services/topology_builder_service.py` + `backend/routes/k8s/topology.py` + `frontend-v2/src/components/k8s/F5BNKTopologyViewer.tsx` (topology schema — `TopologyNode`/`TopologyEdge`), `backend/services/crd_discovery_service.py` (D-018), `backend/services/analyzer_metrics_service.py` (the Prometheus-proxy path, for contrast)
- ADRs: D-018 (CRD discovery), D-022 (fleet member health), D-019 (dynamic), D-017 (honest contract). Sibling F5-observability decision: F5 Insight (folded into D-023 as MCP-consume — classic-BIG-IP estate; EOB here is the k8s/flow estate).
