# D-023 — Classic F5 BIG-IP (TMOS) Integration: Discover · Manage (AS3) · Unify · Migrate to BNK

- **Status:** Accepted (umbrella); **P1 (discover/inventory) committed**, P2 (AS3 engine) + P3 (migrate→BNK) accepted-in-shape.
- **Date:** 2026-06-03
- **Source:** planning session 2026-06-03 — external research (AS3/DO/iControl model, F5 CIS) + code-grounding of the engine contract and target/credential model.
- **Drivers (user):** (a) **hybrid fleet unification** — classic BIG-IP as a managed member alongside clusters/hosts; (b) **migrate BIG-IP → BNK**; (c) **discover/migrate CIS** + BIG-IP VE or HW.
- **Threads together:** D-021 (proxy/source migration), D-022 (fleet member), D-018 (CRD discovery), D-019 (dynamic-by-default), D-010 (EngineRegistry — actuator seam)
- **Out of scope:** Declarative Onboarding (DO) — see "Scoping"; BIG-IP Next / Central Manager — separate future engine.

## Context

Forge orchestrates **k8s-native F5 (BNK)** but has **zero awareness of classic F5 BIG-IP (TMOS)** — no device model, no AS3/iControl, no CIS detection (code-verified absent). Yet most F5 estate today is classic BIG-IP, often fronting Kubernetes via **CIS** (`k8s-bigip-ctlr`). Customers want forge to (1) *see* that estate as part of one fleet, and (2) *move* it onto BNK.

### What the research established

**AS3 vs DO — only one fits forge's engine contract.** Forge engines implement `init / plan / apply / destroy / get_outputs`.

- **AS3** (Application Services 3, day-2 app delivery — virtuals/pools/profiles/WAF, scoped per **tenant = BIG-IP partition**) maps **cleanly**: real dry-run **plan** (`controls.dryRun`), idempotent **apply**, per-tenant **destroy** (`DELETE /declare/{tenant}`), native rollback (15 versions).
- **DO** (Declarative Onboarding — licensing/VLAN/self-IP/HA) **fights the contract**: no plan, **not idempotent**, **no destroy** (DO never unlicenses / deletes users / breaks trust). It is a run-once day-0 step.

**CIS** = `k8s-bigip-ctlr`, runs **in-cluster**, watches k8s resources and **pushes AS3 declarations to an external classic BIG-IP** — a *connector*, not a data plane. CRDs are `cis.f5.com/v1` (`VirtualServer`, `TransportServer`, `TLSProfile`, `Policy`, `IngressLink`, `ExternalDNS`). **The CIS GitHub repo went EOL April 2026** — a direct tailwind for migration. Forge's **generic CRD discovery (D-018) already surfaces `cis.f5.com` CRDs** as uncurated "discovered" entries; the strongest detection signal is the controller Deployment (image `f5networks/k8s-bigip-ctlr`) whose `--bigip-url` arg + `f5-bigip-ctlr-login` secret **identify the external BIG-IP it drives**.

**Async is mandatory** (AS3 auto-swaps to async after 45s → apply/plan are task-poll loops; tokens TTL ~20 min, need refresh). **Tenant = partition = blast radius** (an unscoped `DELETE /declare` wipes *all* tenants). **Classic TMOS (VE + HW)** is uniform for AS3; **BIG-IP Next is a different API and is being wound down** → out.

## Scoping

- **DO is OUT — onboarding is a customer pre-requisite.** Forge connects to an **already-licensed, already-onboarded** BIG-IP. This removes the entire non-idempotent/non-destroyable fight and keeps the engine clean. (A converge-only DO module *may* be added later if demanded, with explicit "destroy unsupported / plan is no-op" semantics — not committed here.)
- **AS3 is the management API**; **iControl REST** is used read-only for discovery/device-facts and as a thin fallback for what AS3 doesn't model.
- **Classic TMOS only** (VE + hardware). BIG-IP Next / Central Manager and the v21.1 native declarative API are a separate future engine.

## Decision

Add **classic BIG-IP integration as a discover → manage → migrate arc**, reusing forge's existing seams (engine abstraction, D-018 discovery, D-021 migration, D-022 fleet membership). A registered BIG-IP is a **new fleet member type** (`member_type="f5-bigip"` per D-022); a CIS-driven BIG-IP is *discovered* and registered the same way.

### Target & credential model

- **`F5BIGIPDevice`** (new table, parallel to `BareMetalHost`): `project_id`, `name`, `mgmt_host`, `mgmt_port=443`, `mgmt_credential_id`, `verify_https_cert`, cached `device_info` (model/version/HA/partitions), discovery timestamps.
- **`F5Credential`** (new, parallel to `SSHCredential`): token-preferred (`/mgmt/shared/authn/login`) or basic, encrypted at rest, with `last_test_*` health fields.
- Decrypt-in-task → populate new `ModuleContext` F5 fields (`f5_mgmt_host/port/username/password/token/verify_cert/partition`) → never logged. Each device auto-registers a D-022 `FleetMember`.

### Phasing

| Phase | Scope | Status |
|---|---|---|
| **P1 — Discover + inventory (read-only)** | (a) **Register classic BIG-IP devices** (`F5BIGIPDevice`/`F5Credential`); probe via iControl/AS3 GET for device facts + existing AS3 config. (b) **Discover CIS in a cluster scan**: detect the `k8s-bigip-ctlr` Deployment, parse `--bigip-url`/secret to resolve the **external BIG-IP**, **curate the `cis.f5.com` CRDs** (so D-018 surfaces them with real metadata), and list `VirtualServer`/`TransportServer` CRs as the migration inventory. (c) Surface both as **D-022 fleet members**; extend D-021 proxy discovery to recognize CIS as a front-door type. | **Committed** |
| **P2 — Manage via AS3 engine (write)** | New `"tmos"` execution engine (D-010 actuator): `init` (auth + version-probe `/mgmt/shared/appsvcs/info`), `plan` (**real dry-run** `controls.dryRun`), `apply` (POST declaration, **always async task-poll** + token refresh), `destroy` (**tenant-scoped** `DELETE /declare/{tenant}`), `get_outputs` (virtual/pool state). Render declarations **forge-side** from the module/variable system (reuse F5 FAST/Mustache bodies); modules tagged `execution_engine="tmos"`. Tenant-scoping is a hard invariant. | Accepted, not scheduled |
| **P3 — Migrate CIS/BIG-IP → BNK** | Translate discovered config → BNK Gateway API: `VirtualServer`→`Gateway`+`HTTPRoute`, pool members→`backendRefs`, `TransportServer`→`TCPRoute`/`UDPRoute`, `TLSProfile`→listener `tls`, `Policy`→BNK security CRD. Guided re-IP/DNS cutover; decommission CIS controller + BIG-IP partition. **This is D-021's L2/L3 (translate→preview→cutover) applied to a BIG-IP/CIS source.** | Accepted, not scheduled |
| **P4 — Observability (F5 Insight, consume)** | Surface analytics for the managed BIG-IP estate by **consuming F5 Insight** (see below) — not building observability from scratch. | Accepted, not scheduled |

### Observability extension — F5 Insight (consume via MCP)

**F5 Insight** ([clouddocs](https://clouddocs.f5.com/products/insight/latest/)) is a self-hosted appliance (K3s + VictoriaMetrics + Grafana + an OpenTelemetry collector) that **remotely polls classic BIG-IP over iControl REST** and adds AI anomaly detection, per-app health/security scoring, and a **native MCP server** with LLM remediation. It is the productized successor to F5's open-source Application Study Tool, and is **classic-BIG-IP-only — it does not touch BNK or Kubernetes.** It therefore has value precisely for the estate *this* ADR manages, so it folds in here rather than as a standalone ADR.

Integration is **consume, not drive**:
- **Primary path — MCP-consume.** Register Insight's MCP server as a forge **MCP tool source** (forge already governs MCP tools — D-019 E6 / #141) to surface its natural-language recommendations + health/security scores in-product.
- **Secondary** — PromQL Insight's VictoriaMetrics for raw metrics, and/or deep-link/embed its Grafana for heavy dashboards.
- **Drive side is minimal** — forge provisions Insight with the BIG-IP inventory/credentials it already holds from P1; there is no documented on-demand "scan device X now" trigger API.

**Unconfirmed (validate before building):** Insight's northbound REST API contract + MCP/API auth model are not exposed in the (JS-rendered) public docs; the MCP-consume path is the documented one. AI features are flagged limited-availability. This is a later phase — it depends on P1 giving forge a BIG-IP inventory to point Insight at.

### Guardrails

1. **Tenant-scope every AS3 write** — no unscoped `POST`/`DELETE /declare`; the engine refuses a write without a target tenant/partition (blast-radius invariant; aligns with D-022 gated-rollout ethos).
2. **Async-always** — apply and dry-run are task-poll state machines (202→in-progress→result), never assume synchronous return.
3. **Version-gate features** — `init` probes AS3/DO RPM versions; gate `dryRun` (needs AS3 ≥3.30) and schema accordingly.
4. **Discovery, not hardcoding** (D-019) — CIS/BIG-IP capabilities are discovered (CRDs, controller args, AS3 `info`); the curated `cis.f5.com` registry entries are a display **overlay**, not a gate.
5. **Migration translation is lossy — be honest** (D-017 discipline) — iRules / GTM(ExternalDNS) / full ASM-WAF / route-domain/SNAT semantics have **no clean Gateway-API analog**; the translator must flag unmapped constructs, never silently drop them. **Verify exact BNK security-CRD kinds against `clouddocs.f5.com/bigip-next-for-kubernetes` before encoding the `Policy`→BNK mapping** (research could not confirm the names).

## Consequences

- **One coherent F5 story.** Forge spans k8s-native BNK *and* classic BIG-IP; the same platform that deploys BNK can now *find* the legacy estate and *move it over*. CIS EOL (April 2026) makes this timely.
- **Maximum reuse, minimal new surface.** AS3 slots into the existing engine/dispatch path (add `"tmos"`); CIS discovery extends D-018 + D-021; the device is a D-022 member; migration is D-021's translator with a BIG-IP source. The genuinely new code is the AS3 transport client + the `F5BIGIPDevice`/`F5Credential` pair + the CR→Gateway-API translator.
- **DO avoided.** Dropping onboarding removes the worst contract-mismatch and a reboot/licensing risk surface; the cost is that forge requires a pre-onboarded device (reasonable — onboarding is a one-time ops task).
- **Risk concentrated late and gated.** The dangerous parts (writing AS3 to a live BIG-IP partition; cutting an app's data path from external BIG-IP to in-cluster BNK) live in P2/P3 behind tenant-scoping, async confirmation, and D-021's verify-before-cutover.
- **P1 is read-only and cheap**, and immediately delivers the "see my whole F5 estate in one fleet" value (D-022) plus the CIS migration inventory.

## References

- External: [CIS overview](https://clouddocs.f5.com/containers/latest/userguide/what-is.html) · [k8s-bigip-ctlr (EOL Apr 2026)](https://github.com/F5Networks/k8s-bigip-ctlr) · [CIS CRDs/modes](https://github.com/F5Networks/k8s-bigip-ctlr/blob/master/docs/config_examples/customResource/CustomResource.md) · [CIS+AS3](https://clouddocs.f5networks.net/containers/latest/kubernetes/kctlr-k8s-as3.html) · [AS3 API](https://clouddocs.f5.com/products/extensions/f5-appsvcs-extension/latest/refguide/as3-api.html) · [AS3 dry-run/Controls](https://clouddocs.f5.com/products/extensions/f5-appsvcs-extension/latest/declarations/miscellaneous.html) · [iControl token auth](https://community.f5.com/kb/technicalarticles/icontrol-rest-authentication-token-management/287462) · [FAST templating](https://clouddocs.f5.com/products/extensions/f5-appsvcs-templates/latest/userguide/template-authoring.html) · [BNK for Kubernetes](https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/overview.html)
- Internal seams: `backend/services/execution/engine_interface.py` (`DeploymentEngine`, `OperationResult`, `PlanResult`, `ModuleContext`), `task_dispatch.py` (`_EXPLICIT_EXECUTION_ENGINES`, `_resolve_dispatch_engine`), `models/bare_metal.py` (target precedent), `models/ssh_credential.py` (cred precedent), `services/crd_discovery_service.py` + `k8s_resource_registry.py` (D-018 discovery — already surfaces `cis.f5.com`), `services/proxy_discovery_service.py` (D-021 detection to extend), `services/execution/variable_assembler.py` (declaration rendering inputs)
- ADRs: D-021 (migration mechanism), D-022 (fleet member), D-018 (CRD discovery), D-019 (dynamic-by-default), D-010 (EngineRegistry), D-017 (honest-contract discipline)
