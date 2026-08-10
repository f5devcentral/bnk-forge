# D-019 — Dynamic-by-Default (no static enumeration of live state)

- **Status:** Accepted
- **Date:** 2026-05-26
- **Source:** `.agent/audits/2026-05-26-static-vs-dynamic-audit.md` (whole-codebase audit, 7 agents, ~35 distinct findings)
- **Sibling principle ADR:** D-017 (success-contract — "HTTP 2xx ≠ operation success")
- **Mechanism ADRs:** D-007 (K8sPayloadBuilder), D-010 (EngineRegistry), D-018 (dynamic CRD discovery — first instance)
- **Class of bug it governs:** static/hardcoded enumeration of state the live system already knows

## Context

A whole-codebase audit found the same defect shape **~35 times** across K8s, BNK/CNF/DPF, the operator, MCP, the catalog/engine layer, and the frontend: forge keeps a **hand-maintained closed set** — a `dict`, `tuple`, `frozenset`, `Enum`, or `if x == "…"` ladder — that **mirrors something the live system already knows**:

- K8s kinds, CRD plurals / api-groups / api-versions / scope
- installed BNK/CNF/DPF components, their pods, their namespaces
- registered execution engines, runner profiles, dispatch routes
- credential providers, proxy types, platform profiles, blueprint categories
- health severities, CNE feature flags, template prerequisites
- the MCP tool catalog and per-tool risk metadata

When reality diverges from the static set — F5 ships a new CRD, a CRD bumps `v1→v2`, a customer uses a custom namespace, a new engine or provider is added — the static path **fails silently**: it drops data, returns an empty list, 404s, 500s, mislabels health, or misroutes execution. The failure almost never carries a clear error, so it surfaces as "forge is buggy / forge can't see my resource."

This is the same family as D-017 (a contract assumption that silently lies). D-018 already established the cure for one instance (CRD discovery). This ADR generalizes the rule so reviews and future work stop re-introducing the pattern.

### Deletion test (the discriminator)

The audit's signal: not every constant is a bug. Apply the architecture-language deletion test to each static table:

- **If deleting it changes *which* live resources / components / engines the system can see or act on** → it is a **gate**. **Forbidden** by this ADR.
- **If deleting it only loses an icon, a label, a color, or a cache warm-start** → it is an **overlay**. **Allowed.**

A static `{kind → plural}` map that decides whether a resource is fetchable is a gate. A static `{kind → Lucide icon}` map that picks a glyph (with a sane fallback) is an overlay.

## Decision

**Any enumeration of live cluster or runtime state MUST derive from its authoritative dynamic source.** Static tables are permitted **only** as (a) a warm-cache seed, or (b) a display overlay — and **MUST NOT gate behavior**. On divergence, an unknown/new item MUST be discovered or handled gracefully (clear error), **never silently dropped, 404'd, 500'd, or misrouted.**

### Authoritative sources

| Static enumeration of… | Authoritative dynamic source |
|---|---|
| K8s kinds / plurals / groups / versions / scope | K8s discovery API (`/apis`, CRD `.spec.names`, `.spec.versions[storage]`) — cached at scan time |
| Installed CRDs visible in UI | D-018 `/crds` discovery, per cluster |
| BNK/CNF component pods & namespaces | namespace + `app.kubernetes.io/part-of=f5-bnk` label / ownerRef traversal; discovered namespaces persisted on the cluster record |
| CNE feature flags | iterate the live `CNEInstance.spec` |
| Execution engines / runner profiles / dispatch | a single `EngineRegistry` (D-010) with per-engine metadata |
| MCP tool catalog + risk/module metadata | `tools/list` + per-tool `_meta` (the D-018 meta pattern) |
| Template prerequisites / blockers | per-template metadata flags in `stack_templates.json` / `template_modules.json` |
| Providers / proxy types / platform profiles | DB-backed list endpoint; FE consumes it |

### The three permitted shapes for a static table

1. **Warm-cache seed** — seeds a cache that is *authoritatively refreshed* from the dynamic source (e.g. `RESOURCE_REGISTRY` may seed plurals, but discovery overrides). Must self-correct when stale.
2. **Display overlay** — icons, colors, labels, human display names. Must have a graceful fallback for unknown keys (generic icon / raw value / raw YAML pane).
3. **Genuinely fixed constant** — values that are not live state (timeouts, retry counts, protocol literals). Out of scope.

Anything that **decides visibility, routability, validity, or health** of a live entity is a gate and is forbidden.

### Required failure behavior on divergence

- **Read paths:** discover the unknown kind/component; if truly absent, return a structured `*_NOT_FOUND` (404), never an empty 200 or a `ValueError → 500`.
- **Write/dispatch paths:** unknown engine/kind = a **hard, explicit error**, never a silent fallback to a default engine.
- **Health/status:** emit only canonical enum members; round-trip through the enum in a test.

## Consequences

- **Reviews gain a checklist gate** (below). New closed sets of live state are caught in PR review, not in a future audit.
- **Mechanism work is already scoped:** D-007 (K8sPayloadBuilder) carries the K8s write-path dispatch; D-010 (EngineRegistry) carries the engine sprawl; D-018 carries CRD discovery. D-019 is the umbrella they serve — it does not introduce a new module by itself.
- **Migration is incremental, not a rewrite.** Static tables stay as warm-cache seeds while consumers move to discovery; the deletion test tells us when a table has been fully demoted from gate to overlay and can be deleted (per the static-registry-removal follow-up).
- **Cost:** discovery adds K8s API calls; mitigated by caching discovery results at scan time (the pattern D-018 already uses) rather than per-request.
- **Not retroactive-by-fiat:** existing gates are migrated by priority (see audit sequencing), not all at once. This ADR governs *new* code immediately and *prioritizes* the existing backlog.

### Reviewer checklist (apply to every PR)

1. Does this PR add a `dict` / `tuple` / `frozenset` / `Enum` / `if x == "…"` that enumerates K8s kinds, CRDs, components, namespaces, engines, providers, severities, or template reqs?
2. If yes — apply the deletion test. Gate or overlay?
3. If gate → reject; derive from the authoritative source instead.
4. If overlay → confirm it has a graceful unknown-key fallback.
5. On divergence, is the failure a clear error (not a silent drop / empty list / wrong-default)?

## References

- Audit: `.agent/audits/2026-05-26-static-vs-dynamic-audit.md`
- Memory: `audit_static_vs_dynamic_2026-05-26`, `followup_static_registry_removal_migration`, `rfc_cnf_dashboard_dynamic_crd_llmtop` (D-018)
- Vocabulary: `.agent/context/architecture-language.md`
