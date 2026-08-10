# D-007 — K8sPayloadBuilder Module

- **Status:** Proposed
- **Date proposed:** 2026-05-13 (re-confirmed 2026-05-17)
- **Backlog id:** `architecture-k8s-payload-builder`
- **Source memo:** `architecture_deepening_2026-05-13_six_candidates.md` (#3)
- **Supersedes:** narrow `followup_pack_strategy4_release_name_footgun.md` (folded into broader scope)
- **Depends on:** none
- **Resume trigger:** next reported regression of the "Helm release_name leaks into manifests payload" class, OR opportunistically when touching `k8s_catalog_payload.py` for unrelated work.

## Context

- `backend/services/execution/k8s_catalog_payload.py`
  - `collect_outputs_from_pack` (lines 141-217) — Strategy 4
  - `_apply_pack_input_defaults` called 3× at lines 51-54, 88-91, 155-164

Strategy 4 fires for any `_name`-suffixed output and defaults to `release_name` (a Helm-only idiom). When a manifests module sits beside a Helm sibling, outputs collapse. `_apply_pack_input_defaults` called in three sibling functions — the contract "apply defaults before render" has no owner. Add an engine, forget the third call, defaults silently miss.

**Deletion test:** delete Strategy 4 → Helm `release_name` breaks; keep it → manifests bleed. Real seam, adapter in the wrong place.

## Decision (deeper shape)

A `K8sPayloadBuilder` Module. Interface (sketch):

```
build(ctx, variables) -> (payload, outputs)
```

Pack defaults applied once at entry. Strategy 4 gated to `deployment_pack.engine == "helm"` or a whitelist. Helm and manifests become separate adapters behind one Interface.

## Consequences

**Locality:** "apply defaults before render" lives in one place — adding a new payload kind can't forget to call it.

**Leverage:** engine-specific output collation (Strategy 4 for Helm, identity for manifests, future adapters for Kustomize/Operator) plugs into a stable seam.

**Test win:** one seam-level test — two sibling modules (Helm + manifests), each output stays its own. Today nothing tests this end-to-end.

## References

- Source memo: `architecture_deepening_2026-05-13_six_candidates.md`
- Related follow-up: `followup_pack_strategy4_release_name_footgun.md` (delete after this lands)
- Code: `backend/services/execution/k8s_catalog_payload.py`
