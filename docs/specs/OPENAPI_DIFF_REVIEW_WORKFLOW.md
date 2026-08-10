# OpenAPI Diff Review Workflow — API-CONTRACT-005

> How API contract changes are detected, surfaced, and reviewed. Ensures no Tier 1 contract change lands silently.

Status: **Accepted** | Created: 2026-03-27

---

## Problem

Contract changes currently land silently. If a developer adds a field to a response model, changes a route path, or alters an enum value, the only signal is:

1. The `openapi-check` CI job fails if `openapi.json` wasn't regenerated.
2. The `typecheck-frontend` CI job fails if generated TypeScript types weren't updated.

These checks catch **stale spec files**, but they don't catch **the contract change itself**. A developer who diligently regenerates the spec and types can land a breaking contract change with no review visibility.

---

## Current Infrastructure

BNK Forge already has strong foundations for contract visibility:

| Component | What it does | Location |
|-----------|-------------|----------|
| `scripts/generate-openapi.py` | Generates `backend/openapi.json` from FastAPI app | `scripts/` |
| `scripts/generate-openapi.py --check` | Fails if committed spec is stale | CI P1 |
| `openapi-typescript` | Generates `frontend-v2/src/types/api-generated.ts` from spec | CI P1 |
| CI `openapi-check` job | Blocks merge if spec is stale | `.github/workflows/ci.yml` |
| CI `typecheck-frontend` job | Blocks merge if generated TS types are stale | `.github/workflows/ci.yml` |

**What's missing:** visibility of *what changed* in the contract, not just *whether the spec is fresh*.

---

## Proposed Workflow

### Layer 1: OpenAPI Diff in PR Comments (Automated)

**What:** When a PR changes `backend/openapi.json`, a CI step generates a human-readable diff summary and posts it as a PR comment.

**How:**

```yaml
# Addition to .github/workflows/ci.yml (Phase 1)
openapi-diff:
  name: "P1 · OpenAPI Diff"
  needs: changes
  if: needs.changes.outputs.backend == 'true'
  runs-on: ubuntu-latest
  permissions:
    pull-requests: write
  steps:
    - uses: actions/checkout@v4
      with:
        fetch-depth: 0  # Need base branch for diff

    - name: Check for OpenAPI changes
      id: check
      run: |
        git diff origin/${{ github.base_ref }}...HEAD -- backend/openapi.json > /tmp/openapi.diff
        if [ -s /tmp/openapi.diff ]; then
          echo "changed=true" >> $GITHUB_OUTPUT
        else
          echo "changed=false" >> $GITHUB_OUTPUT
        fi

    - name: Generate diff summary
      if: steps.check.outputs.changed == 'true'
      run: |
        python scripts/openapi-diff-summary.py \
          --base <(git show origin/${{ github.base_ref }}:backend/openapi.json) \
          --head backend/openapi.json \
          --output /tmp/diff-summary.md

    - name: Post PR comment
      if: steps.check.outputs.changed == 'true'
      uses: marocchino/sticky-pull-request-comment@v2
      with:
        header: openapi-diff
        path: /tmp/diff-summary.md
```

**Diff summary format:**

```markdown
## 📋 API Contract Changes

### Endpoints Changed
| Change | Method | Path | Details |
|--------|--------|------|---------|
| ➕ Added | GET | `/api/foo` | New endpoint |
| ✏️ Modified | GET | `/api/k8s/clusters` | Added `response_model` |
| ❌ Removed | POST | `/api/bar` | Endpoint removed |

### Schema Changes
| Change | Schema | Field | Details |
|--------|--------|-------|---------|
| ➕ Added | `ClusterListResponse` | `total_count` | New field |
| ✏️ Modified | `ConnectivityStatus` | — | Added enum value `partial` |
| ❌ Removed | `FooResponse` | `bar` | Field removed |

### Tier Impact
- **Tier 1 endpoints affected:** 2 (GET /api/k8s/clusters, GET /api/k8s/clusters/{id})
- **MCP tools affected:** list_clusters, get_cluster

> ⚠️ This PR changes Tier 1 API contracts. Review the changes above for backward compatibility.
```

### Layer 2: Tier 1 Contract Change Gate (Merge Blocker)

**What:** If a PR changes any Tier 1 endpoint's response schema in a backward-incompatible way, the CI posts a warning and optionally blocks the merge until acknowledged.

**How:** The diff summary script detects:
- **Removed fields** from Tier 1 response schemas → ⚠️ BREAKING
- **Changed field types** in Tier 1 response schemas → ⚠️ BREAKING
- **Removed endpoints** that back MCP tools → ⚠️ BREAKING
- **Added fields** to Tier 1 response schemas → ✅ Safe (additive)
- **New endpoints** → ✅ Safe

For this first iteration, this is **informational only** (warning comment, not merge blocker). The team can promote it to a merge blocker once the response model wiring stabilizes.

### Layer 3: Periodic Full Contract Audit (Manual/Scheduled)

**What:** A scheduled job (weekly or on-demand) regenerates the full OpenAPI spec and compares it to the committed version, catching any drift that bypassed the PR workflow.

**How:**

```makefile
# Makefile target
openapi-audit:
    @echo "Regenerating OpenAPI spec..."
    @python scripts/generate-openapi.py
    @if git diff --quiet backend/openapi.json; then \
        echo "✅ OpenAPI spec is up to date"; \
    else \
        echo "❌ OpenAPI spec has drifted! Run: make openapi"; \
        git diff --stat backend/openapi.json; \
        exit 1; \
    fi
```

This is already effectively implemented via `make openapi-check` / `scripts/generate-openapi.py --check`. The audit layer is about running it outside of PR context.

---

## Diff Summary Script

A new script `scripts/openapi-diff-summary.py` will:

1. Parse base and head `openapi.json` files.
2. Compare paths (endpoints added/removed/modified).
3. Compare schemas (models added/removed, fields changed).
4. Cross-reference with Tier 1 endpoint list from API-CONTRACT-001.
5. Cross-reference with MCP tool mapping.
6. Output a Markdown summary.

### Implementation Sketch

```python
#!/usr/bin/env python3
"""
Generate a human-readable summary of OpenAPI spec changes.

Usage:
    python scripts/openapi-diff-summary.py --base old.json --head new.json --output diff.md
"""
import json
import argparse
from pathlib import Path

# Tier 1 paths (from API-CONTRACT-001)
TIER1_PATHS = {
    "/api/auth/login", "/api/auth/me", "/api/auth/users",
    "/api/k8s/clusters", "/api/k8s/clusters/{cluster_id}",
    "/api/k8s/clusters/{cluster_id}/connectivity",
    "/api/k8s/clusters/connectivity",
    "/api/k8s/clusters/{cluster_id}/f5bnk/data",
    "/api/k8s/clusters/{cluster_id}/f5bnk/health",
    "/api/k8s/clusters/{cluster_id}/f5bnk/gateway-topology",
    "/api/operators/fleet-health",
    "/api/system/health",
    "/api/projects", "/api/projects/{project_id}",
    # ... full list from tiering spec
}

def diff_openapi(base: dict, head: dict) -> dict:
    """Compare two OpenAPI specs and return structured changes."""
    changes = {"endpoints": [], "schemas": [], "tier1_affected": []}

    base_paths = set(base.get("paths", {}).keys())
    head_paths = set(head.get("paths", {}).keys())

    for path in head_paths - base_paths:
        changes["endpoints"].append({"change": "added", "path": path})
    for path in base_paths - head_paths:
        changes["endpoints"].append({"change": "removed", "path": path})
    for path in base_paths & head_paths:
        if base["paths"][path] != head["paths"][path]:
            changes["endpoints"].append({"change": "modified", "path": path})

    # Schema diffing (compare components/schemas)
    base_schemas = set(base.get("components", {}).get("schemas", {}).keys())
    head_schemas = set(head.get("components", {}).get("schemas", {}).keys())

    for schema in head_schemas - base_schemas:
        changes["schemas"].append({"change": "added", "schema": schema})
    for schema in base_schemas - head_schemas:
        changes["schemas"].append({"change": "removed", "schema": schema})

    # Check Tier 1 impact
    for change in changes["endpoints"]:
        if change["path"] in TIER1_PATHS:
            changes["tier1_affected"].append(change)

    return changes
```

---

## Policy: What Counts as a Public Contract Change

| Change Type | Public? | Requires Diff Review? |
|------------|:-------:|:---------------------:|
| Added endpoint | Yes | ✅ Yes (informational) |
| Removed endpoint | Yes | ✅ Yes (breaking) |
| Changed endpoint path | Yes | ✅ Yes (breaking) |
| Added response field | Yes | ✅ Yes (additive — safe) |
| Removed response field | Yes | ✅ Yes (breaking) |
| Changed response field type | Yes | ✅ Yes (breaking) |
| Added `response_model` to route | Yes | ✅ Yes (hardening — safe) |
| Changed request body schema | Yes | ✅ Yes |
| Changed auth requirements | Yes | ✅ Yes |
| Internal refactor (no shape change) | No | ❌ No |
| New internal/admin endpoint (Tier 3) | Soft | ⚠️ Optional |
| Changed error response format | Yes | ✅ Yes |

### Exceptions

These changes do NOT require contract review:
- Documentation-only changes (descriptions, examples).
- Internal implementation refactors that don't change the public API shape.
- Test infrastructure changes.
- Changes to Tier 3 endpoints (unless they back an MCP tool).

---

## Review Expectations

### Who Reviews Contract Changes?

| Scope | Primary Reviewer | Secondary |
|-------|-----------------|-----------|
| Tier 1 endpoint changes | Backend owner | Frontend consumer |
| Schema changes affecting MCP | Backend owner | MCP maintainer |
| New endpoints | Backend owner | — |
| Tier 2-3 changes | Backend owner | — (self-review OK) |

### What Reviewers Check

1. **Backward compatibility** — Will existing consumers break?
2. **Naming consistency** — Do new fields follow conventions?
3. **Canonical vocabulary** — Do status/severity fields use canonical values from PLAT-REL-001?
4. **MCP impact** — Will any AI tool need updating?
5. **Frontend impact** — Will any UI component need updating?

---

## Rollout Plan

### Phase 1: Informational (Now)

1. Create `scripts/openapi-diff-summary.py`.
2. Add `openapi-diff` step to CI (posts comment, never blocks).
3. Team becomes accustomed to seeing contract diffs in PRs.

### Phase 2: Tier 1 Warnings (After response models are wired)

1. Diff script flags breaking changes to Tier 1 endpoints.
2. PR comment includes ⚠️ warnings for breaking changes.
3. Still informational — team reviews but merge is not blocked.

### Phase 3: Merge Protection (When team is ready)

1. Promote Tier 1 breaking changes to merge blocker.
2. Require explicit acknowledgment (e.g., label `contract-change-reviewed`).
3. Only for backward-incompatible changes, not additive ones.

---

## Integration with Existing CI

The existing CI already runs in the right order for this:

```
P1: openapi-check (spec freshness) → openapi-diff (NEW: diff summary)
P1: typecheck-frontend (generated TS types freshness)
```

The new `openapi-diff` job runs in parallel with `openapi-check` in Phase 1. It has no downstream dependencies — it's purely informational.

---

## Follow-On Implementation Tickets

| Ticket | Description | Priority |
|--------|-------------|----------|
| **CT-B20** | Create `scripts/openapi-diff-summary.py` with path + schema diffing | P1 |
| **CT-B21** | Add `openapi-diff` CI job to `.github/workflows/ci.yml` Phase 1 | P1 |
| **CT-B22** | Embed Tier 1 endpoint list in diff script (from API-CONTRACT-001) | P1 |
| **CT-B23** | Add MCP tool → route cross-reference to diff output | P2 |
| **CT-B24** | Evaluate promoting Tier 1 breaking changes to merge blocker | P2 |

---

## Related Documents

- [Endpoint Contract Tiering — API-CONTRACT-001](ENDPOINT_CONTRACT_TIERING.md)
- [Tier 1 Response Model Coverage Plan — API-CONTRACT-002](TIER1_RESPONSE_MODEL_PLAN.md)
- [Golden Contract Test Matrix — API-CONTRACT-004](GOLDEN_CONTRACT_TEST_MATRIX.md)
- [Status Semantics — PLAT-REL-001](STATUS_SEMANTICS.md)
