# D-034 — Portable BNK Use-Case Artifact (parameterized config/policy bundle)

- **Status:** Accepted (PRD + 5 open questions signed off by operator 2026-07-21; ready to decompose P0 tracer)
- **Date:** 2026-07-21
- **Wave:** Pages-rework Wave 2. Standalone / cluster-scoped; Wave 3 (Fleet, D-025/D-026) and Wave 4 (fleet fan-out) *consume* this object, they are not prerequisites.
- **Repairs:** the `config_export_service` **verbatim-promotion footgun** (import applies one cluster's data-plane onto another unchanged).
- **Closes:** the `k8s_drift_service` **desired-state stub** (complete diff engine, no desired input).
- **Related:** D-018 (dynamic CRD dashboard), D-021/D-023 (migration → *generates* CRs; this *packages* them), D-025/D-026 (Fleet — future consumer), D-028 (unified blueprint catalog — sibling "named reusable unit" pattern), `bf_conf_template_service` (the named/versioned + `matching_bnk_version` + refuse-delete-while-referenced pattern this is modelled on).

---

## Context / Problem

BNK Forge has no **named, versioned, portable unit of "a BNK use-case"** — a bundle of the config
and policy that makes a cluster *do a job* (e.g. "east-west-secure", "north-south-waf"), that can be
lifted off one cluster and applied to another with that cluster's own addressing. Three concrete gaps
trace to its absence:

1. **The export footgun.** `config_export_service.export_cluster_config` pulls live CRs and strips
   only k8s-managed metadata (`uid`, `resourceVersion`, …) — it promotes `spec` **verbatim**. The
   `/clusters/{id}/bnk/import` route then server-side-applies those specs onto a *different* cluster.
   Cluster A's `F5SPKVlan.selfip_v4s`, `F5SPKStaticRoute.gateway`, `F5SPKSnatpool.addresses`, and
   `F5SPKEgress.sourceTranslation` land on cluster B **unchanged** → wrong addressing → broken data
   plane. There is no parameterization; "portable" today means "portable only to an identically-addressed cluster."

2. **The drift stub.** `k8s_drift_service` has a complete, working diff engine (`_diff_dicts`,
   `_normalize_for_comparison`) that is **starved of a desired-state input** — `check_manifest_drift`
   / `check_helm_drift` return `"not available"` for lack of one. There is nothing that says "this is
   what the cluster *should* look like."

3. **No governance/reuse unit.** Marcus builds a gateway+policy+VLAN set by hand every time; Aisha has
   no object to version, promote, or refuse-to-delete-while-in-use; Atlas (MCP/API) has no artifact to
   export/import; Sofia has no named baseline to detect drift against.

**The convergence.** These are the same missing object seen from four angles. `bnk/topology.py`
already knows *exactly which CR fields are cluster-specific*; `config_export` already *extracts* the
CRs (just verbatim); `bf_conf_template` is the proven *named/versioned + inject* pattern; `k8s_drift`
is the *payoff* waiting for a desired-state. The Use-Case Artifact is the object that unifies them.

**Grounding personas** (`docs/features/E2E_PERSONAS.md`): Marcus (build config once, reuse across
clusters), Aisha (govern/version/promote, Config Export/Promotion surface), Atlas (headless
export/import via MCP/API), Sofia (drift = "cluster diverged from use-case v1").

---

## Goals / Non-goals

**Goals (v1).**
- A new domain object — **`UseCaseArtifact`** — with immutable **versions**.
- Each version bundles a curated set of **config + policy + supporting data-plane CRs** (kind set below).
- A **typed parameter schema**: cluster-specific values are **lifted into named params** via a
  **hybrid** flow — auto-propose from the known cluster-specific field registry, author confirms/renames/types.
- **`render(version, param_values) → concrete CRs`** by injecting per-cluster values; a missing
  required param is a **hard error** (the footgun repair — never apply a half-injected CR).
- **Two authoring paths:** (a) **capture from a live "golden" cluster** (reuse `config_export` +
  `topology`), (b) **author from scratch** (extend `ConfigBuilder`/`PolicyBuilder` to emit a
  parameterized bundle).
- **Portable export/import** (YAML/JSON) of an artifact version (not a live-cluster snapshot).
- **Drift wired in v1:** rendered artifact = desired-state feeding the existing `_diff_dicts` engine
  (`check_usecase_drift`), closing the stub.

**Non-goals (v1).**
- **Fleet fan-out** — applying one artifact across many clusters with waves/gates is Wave 4; it consumes
  this object (`fleet_bulkop_service` SAFE_ACTIONS allowlist) but is out of scope here.
- **DPF provisioning/services, logging/HSL, AI Analyzer, CNEInstance** kinds — large surface, rarely
  portable, deferred.
- Full GitOps / external repo backing of artifacts (in-DB is v1; export is the interop seam).
- Auto-remediation of drift (detect only in v1; reconcile is a follow-up).

---

## v1 CR coverage

| In (v1) | Category | Cluster-specific fields lifted to params |
|---------|----------|------------------------------------------|
| `Gateway`, `HTTPRoute` (+ other route kinds present) | Gateway API config | listener addresses (status-derived, read-only — not templated); hostnames *may* be params |
| `BNKSecPolicy`, `BNKNetPolicy` | Policy | targetRefs (name-based, portable as-is) |
| `F5BigFwPolicy`, `F5BigCneAddresslist`, `F5BigCnePortlist`, `F5BigCneIrule` | Firewall / iRules | address lists (IP/CIDR params) |
| `F5SPKVlan` | Data-plane | `interfaces`, `selfip_v4s`, `prefixlen_v4`, `mtu` |
| `F5SPKStaticRoute` | Data-plane | `destination`, `gateway` |
| `F5SPKSnatpool` | Data-plane | `addresses` / `members` |
| `F5SPKEgress` | Data-plane | `sourceTranslation` addresses |

**Out (v1):** `CNEInstance` (FLO-owned lifecycle, not portable config), `DPFOperatorConfig`/`DPUCluster`/
`DPUSet`/`BFB`/`DPUFlavor`, all `DPUService*`, `F5BigLogHslpub`/`F5BigLogProfile`, `F5BigAnalyzer`,
`F5BigGlobalOptions`.

---

## Domain model

Modelled on `bf_conf_template` (named/versioned/CRUD/`matching_bnk_version`/refuse-delete-while-referenced)
and on `BlueprintRelease` immutability (a content change = a new version, never an in-place edit).

- **`UseCaseArtifact`** — `id`, `name` (unique), `description`, `created_by`, timestamps. The mutable
  container: rename/describe only.
- **`UseCaseArtifactVersion`** — *immutable* once created:
  - `artifact_id` FK, `version` (semver-ish string), `matching_bnk_version`
  - `cr_templates` (JSON): the parameterized CRs (`${param}` tokens substituted for lifted values)
  - `param_schema` (JSON): list of param descriptors (below)
  - `source` (`captured_from_cluster` | `authored`), `source_cluster_id` (nullable)
  - `content_hash` (for dedup / capture idempotency), `created_by`, `created_at`
  - Unique `(artifact_id, version)`.
- **`UseCaseApplication`** — the *binding*, for drift reproducibility:
  - `artifact_version_id` FK, `cluster_id` FK, `param_values` (JSON — the resolved injection), `applied_at`, `applied_by`
  - Records "cluster X runs artifact-version Y with *these* injected values" → drift always compares
    against the exact desired-state that was applied.

**Param descriptor** (`param_schema` entry):
`{ key, type: ip|cidr|iface|int|string|list<ip>|namespace|..., kind: environmental|assigned, label, description, default, required, source_paths: [ {kind, jsonpath} ] }`
— `source_paths` is what capture filled it from and what render substitutes back into.
- **`kind: environmental`** — a fact that exists on the target *before* config (interface names
  `p0`/`p1`/`bond0`, existing upstream gateways, node CIDRs). Discoverable from target topology →
  **auto-filled** at apply time.
- **`kind: assigned`** — a value the artifact is about to *create* on the target (`selfip_v4s`, SNAT
  pool addresses, egress source IP). Cannot be discovered (does not exist yet) → **always prompts**.
  This is why zero-touch apply is a non-goal (below).

---

## The cluster-specific path registry (DRY single source of truth)

The one table that both **capture** (propose params) and **topology/discovery** (find defaults) read.
Prevents the two sides from drifting apart. Seeded from the fields `bnk/topology.py::_build_data_plane`
already extracts:

```
F5SPKVlan          spec.interfaces        → iface (list)
F5SPKVlan          spec.selfip_v4s        → ip (list)
F5SPKVlan          spec.prefixlen_v4      → int
F5SPKVlan          spec.mtu               → int
F5SPKStaticRoute   spec.destination       → cidr
F5SPKStaticRoute   spec.gateway           → ip
F5SPKSnatpool      spec.addresses|members → ip (list)
F5SPKEgress        spec.sourceTranslation → ip (list)
F5BigCneAddresslist spec.addresses        → ip|cidr (list)
```

Capture walks each exported CR against this registry; every hit becomes a proposed param with the
discovered value as `default`. Topology discovery on a *target* cluster resolves those same paths to
supply per-cluster defaults at apply/drift time.

---

## Namespace remap (multi-namespace + create-new)

Bundles span multiple source namespaces, and a target may not have them. Cluster-scoped CRs
(`GatewayClass`) have no namespace and are untouched. For namespaced CRs:

- The `param_schema` carries a **namespace map** — one `type: namespace` param per *distinct source
  namespace* in the bundle (`ns_map[<source_ns>]`).
- **At apply time the UI** lists the target cluster's existing namespaces (core `list_namespace`) and,
  per source ns, offers: a **dropdown of discovered namespaces** (default = same-name if present) **or
  "Create new namespace…"**. A chosen-new namespace is created (server-side apply of a `Namespace`)
  **before** its CRs are applied.
- **Render rewrites** both `metadata.namespace` *and* the known cross-namespace reference fields whose
  value matches a remapped source ns — `parentRefs[].namespace`, `backendRefs[].namespace`,
  `ReferenceGrant.spec.from/to[].namespace`, policy `targetRefs[].namespace`. (Reference-remap fidelity
  is an explicit v1 render rule with its own test; miss it and cross-ns routes/grants break silently.)

## Render / inject / apply

1. `render(version, param_values)` → for each `cr_template`, substitute `${param}` tokens (including the
   namespace remap above) → concrete CR list.
2. **Required-param guard:** any unfilled required param → **hard error** listing the gaps. No partial apply.
3. **Apply is a halfway-house, never zero-touch (v1).** There is always a **review-and-confirm** step:
   environmental params + same-name namespace defaults are **pre-filled** from target discovery; assigned
   params, any environmental param discovery couldn't resolve, and the namespace map are **presented for
   operator input/confirmation**. "Apply with all-defaults, zero prompts" is an explicit **non-goal** —
   assigned values (selfips, SNAT, egress IPs) never exist on a fresh target to discover.
4. Apply reuses the **existing `/bnk/import` server-side-apply write path** (`field_manager="bnk-forge"`,
   `KNOWN_PLURALS`) — but fed **rendered, per-cluster CRs** instead of verbatim ones. That single change
   is the footgun repair.

## Drift wiring (closes the stub)

`check_usecase_drift(cluster, artifact_version, param_values)`:
1. `render(version, param_values)` → **desired** CRs.
2. Fetch **actual** CRs from the cluster (reuse `config_export_service._fetch_resources`).
3. Feed each desired/actual pair to the existing `k8s_drift._normalize_for_comparison` + `_diff_dicts`.
4. Return the standard drift shape — `_k8s_catalog_drift_unavailable` is no longer the only outcome.

---

## Phased delivery (tracer-bullet vertical slices)

Each phase is an independently-mergeable slice; Phase 0 proves the *whole pipeline* on the narrowest surface.

- **Phase 0 — tracer:** one kind (`F5SPKVlan`) end-to-end: capture → propose one param
  (`selfip_v4s`) → store artifact+version → render → apply → drift. Thin but full-depth; de-risks the
  data model and the render/diff contracts before breadth.
- **Phase 1 — model + capture:** migration (`UseCaseArtifact`/`Version`/`Application` + path registry);
  capture-from-cluster over the full v1 kind set; auto-propose params API; artifact/version CRUD with
  refuse-delete-while-applied.
- **Phase 2 — render + apply (footgun repair):** render+inject, required-param guard, apply via the
  existing import write path fed rendered CRs; portable **export/import** of an artifact version.
- **Phase 3 — author from scratch:** extend `ConfigBuilder`/`PolicyBuilder` to emit a parameterized
  bundle; param confirm/rename/type UI (the "hybrid" author step).
- **Phase 4 — drift:** `check_usecase_drift` + a drift surface ("cluster diverged from use-case v1").
- **Phase 5 — headless (Atlas):** MCP/API tools — list/export/import/apply/drift — so the AI-agent
  persona drives it without UI.

---

## Persona acceptance (regression targets)

- **Marcus:** capture a live gateway+policy+VLAN as `east-west-secure v1`; apply to a fresh cluster
  whose selfips/routes differ → data plane comes up with *its own* addressing, not the source's.
- **Aisha:** version `east-west-secure` v1→v2 (immutable v1 preserved); cannot delete v1 while a cluster runs it.
- **Sofia:** hand-edit a live CR → drift report shows "diverged from `east-west-secure v1`" with the exact path.
- **Atlas:** same capture→apply→drift loop via MCP/API, no UI.
- **Cross-cutting (personas doc):** refresh mid-apply resumes; cancel leaves no half-applied bundle;
  deep-link to an artifact version loads directly.

---

## Resolved decisions (operator sign-off 2026-07-21)

1. **Version immutability — YES.** Published versions are immutable (`BlueprintRelease`/`bf_conf`
   semantics: content change ⇒ new version). The **authoring editor holds the mutable draft** (reusing
   the Wave-1 `useBuilderDraft` localStorage pattern); **"Create version" is the freeze point** — no
   `draft` status column, a version row is always frozen. Guards the drift baseline against
   edited-underneath-you.
2. **Param precedence — override wins, discovery fills.** `value = operator_override if provided else
   discovered_default`; empty + required ⇒ block. Params carry `kind: environmental|assigned`;
   **environmental** auto-fill from target discovery, **assigned** always prompt. **Zero-prompt apply is
   a non-goal** — apply is always a pre-filled review-and-confirm (the "halfway house").
3. **Namespace — multi-ns + create-new.** A `type: namespace` param per distinct source namespace; apply
   UI offers a **dropdown of discovered target namespaces + "create new"**; render remaps
   `metadata.namespace` and cross-ns reference fields (see Namespace remap section). Not a single
   source-ns-default param.
4. **Secret safety — capture+flag, hard-exclude Secret.** `Secret` kind is never captured (belt-and-
   suspenders test even though it's outside the v1 set). **iRules are captured verbatim but flagged**
   ("contains iRule code — review before external sharing") on capture summary and export — no
   regex-redaction (false confidence); the author is a trusted operator, threat model is *accidental*
   leak into a shared artifact.
5. **Capture idempotency — hash structure, not values.** `content_hash` covers the parameterized
   templates + the param key/type/path set, **excluding discovered default values**. Makes artifact
   identity **address-independent** — the same config shape captured from two differently-addressed
   clusters dedupes to one artifact; re-capturing an unchanged shape returns "already captured as vN".

---

## Consequences

- **Repairs** the export/import footgun and **closes** the `k8s_drift` desired-state stub — two live
  defects retired by one object.
- **Creates the object Wave 4 fans out** — fleet rollout applies an artifact version across members via
  the existing gated wave executor; no rework of this model expected.
- **New tables + Alembic migration** — coordinate the head per the project's stacked-migration rule
  (serial merge or planned merge revision).
- **OpenAPI + `api-generated.ts` regen** on the new routes/schemas (CI OpenAPI-freshness is strict).
- **Additive, reversible** — no change to existing export/import behaviour until callers opt into the
  rendered path; the old verbatim path can stay for same-cluster snapshotting.
