# D-017 — Licensing chain: success contract + curl-response parser hardening

- **Status:** Proposed
- **Date proposed:** 2026-05-21
- **Backlog id:** `architecture-licensing-success-contract`
- **Source memo:** 2026-05-21 licensing live-probe audit — Activate with garbage JWT returned `200 success:true`; backend log captured `CWC POST /reactivate raw response: ` (empty)
- **Depends on:** none (touches `backend/services/qkview_service.py`, `backend/routes/licensing.py`, `frontend-v2/src/hooks/useLicensing.ts`, `frontend-v2/src/components/k8s/LicensingPanel.tsx`)
- **Resume trigger:** confirmed user-reported failure ("activate button worked but no license applied, no error") + live-confirmed silent-fail on `aws-syd-test-cluster` — schedule before next demo / before users rely on the FE feedback for licensing operations.

## Context

Live probe 2026-05-21 against `aws-syd-test-cluster` (id=9) with `POST /api/licensing/9/activate` body `{"jwt":"this-is-not-a-real-jwt-just-garbage"}`:

```
HTTP 200
{"success":true,"operator_dispatch":null,
 "jwks_validation":{"status":"valid","namespace":"f5-operator"}}

backend log:
  INFO services.qkview_service | CWC POST /reactivate raw response: 
  INFO POST /api/licensing/9/activate HTTP/1.1 200 OK
```

CWC returned a completely empty response body. The chain that converts that into a green "License activated successfully" toast on the FE has three layered defects:

**A. `_parse_curl_response` defaults to `status_code = 200` when the HTTP_STATUS marker is absent** (`qkview_service.py:724`)

Curl with `-w '%{http_code}'` *always* emits the marker on a completed HTTP exchange. Absent marker = the request never completed (websocket-multiplexing dropout, curl killed, CWC returned no bytes, etc.). The function only checks for a `curl:` error prefix; absence of both treats as 200 OK with empty body. Empty body → `{}` → success-shaped.

**B. Mutations hardcode `{"success": True, **result}` from a 2xx return** (`qkview_service.py:1521, 1541, 1964, 2090`)

`activate_license`, `post_license_receipt`, `switch_license`, `force_renew_license` all return `success: True` on the happy path with no inspection of whether the underlying operation actually changed state. `switch_license` and `force_renew_license` *do* capture a post-call `license_status` (lines 1968, 2094) — the data is right there but not gated.

**C. FE mutation `onSuccess` ignores the response body** (`useLicensing.ts:155-214`, `LicensingPanel.tsx:173-174,190-209,222-228,238`)

Every mutation's `onSuccess` calls `notify.success(...)` based purely on HTTP 2xx; the response payload's `success` / `error_message` / `license_state` fields are never read.

These compound. (A) alone produces silent success on an empty CWC response; (B) hides the case where CWC explicitly says "rejected" in a 2xx body; (C) loses any explicit failure signal the BE *does* propagate (we saw this with the operator-dispatch 502 path — works correctly but is bypassed when no operator is installed, which is the live state on `aws-syd-test-cluster`).

**Deletion test:** remove just (A) — the silent-fail goes away but mutations still happily return `success: True` if CWC ever sends a "rejected" 200. Remove just (B) — empty responses still slip through. Remove just (C) — BE knows the truth, user still sees success toast. All three need to land together to actually fix the user-visible bug.

## Confirmed scope (live)

| Mutation | File:line | Live verdict | Underlying defect(s) |
|---|---|---|---|
| Activate License | `qkview_service.py:1495` | **BROKEN** — garbage JWT → 200 `success:true` | A + B + C |
| Submit Receipt | `qkview_service.py:1527` | OK today (CWC returned explicit 405) | structurally B + C, currently masked by upstream |
| Renew (default) | `qkview_service.py:1847` | inferred broken | A + B + C, plus mutates `cpcl-config-cm` before validation |
| Renew (force) | `qkview_service.py:1972` | inferred broken | A + B + C, plus 2-min CWC restart before validation |
| Download Telemetry Report | `qkview_service.py:1480` | BROKEN today (405) | gate not enforced in FE (button enabled regardless of telemetry_state); error mapping unclear |
| Validate JWKS | `qkview_service.py:1700` | OK | explicit status dict, raises on real failure — **reference shape** |
| CWC Setup | `qkview_service.py:1086` | OK | explicit steps, raises on partial failure — **reference shape** |

## Decision (deeper shape)

A four-part fix. Each part addresses one independent defect; together they make the licensing chain's success contract explicit at every layer.

### 1. Harden `_parse_curl_response` — no implicit 2xx

`backend/services/qkview_service.py:719-756`. Change the default behavior:

- If no `---HTTP_STATUS:` marker AND no `curl:` prefix → raise `QKViewError("CWC request did not complete — empty response from in-cluster curl. This is not the same as a 200 OK.", status_code=None)`. The current code treats this as 200; the new code treats it as "we don't know what happened, fail loudly".
- Same for `_check_curl_status` (line 699-716) — currently it silently passes on no-marker; same treatment.

This is the single most load-bearing change. It catches the empty-response case at the lowest layer and prevents *every* mutation downstream from inheriting the silent-success.

### 2. Mutation success contract — derive from outcome, not from 2xx

For every mutation that talks to CWC and changes state:

**Activate, Renew (both variants)**: success requires a post-call `GET /status` showing the operation took effect. Concretely:
- Capture `pre.license_state`, `pre.digital_asset_id` before the POST /reactivate.
- After POST, fetch status again.
- `success = (post.digital_asset_id != pre.digital_asset_id) OR (post.license_state in {"Active","Licensed","Verification Complete"} AND post.license_state != pre.license_state)`.
- On `success=False`, populate `error_message` with the captured CWC response body (so the FE has something concrete to show).

`switch_license` and `force_renew_license` already do the GET /status — gate the return on the comparison, don't just attach it.

**Receipt**: there's no observable post-state on the cluster for a receipt POST. Instead, require a non-empty meaningful response body. Empty body → fail.

**Validate JWKS, CWC Setup**: already correctly shaped — use them as the reference contract.

### 3. Remove pre-mutation steps from `switch_license` / `force_renew_license`

`switch_license` patches `cpcl-config-cm` with the new JWT *before* CWC validates it (step 3, line 1903). `force_renew_license` deletes CPCL state secrets and restarts the CWC pod *before* the activate call (steps 4-5). Reorder so:

1. Pre-validate JWT shape (decode base64, check claims) — fail fast on garbage before touching the cluster.
2. POST /reactivate first, verify success.
3. Only on confirmed success, persist to `cpcl-config-cm` (so a future CWC restart picks up the validated JWT).

Today, firing renew with garbage writes garbage to the persistent ConfigMap and leaves a landmine for the next CWC restart. The fix here is also defense-in-depth: even if (1) and (2) above are bypassed, a user with a bad JWT doesn't damage state.

### 4. FE: read the response, gate the toast, surface error_message

`frontend-v2/src/hooks/useLicensing.ts:155-214` and `frontend-v2/src/components/k8s/LicensingPanel.tsx:173-238`:

- Each mutation's `onSuccess(data)` inspects `data.success`. On `false`, call `notify.error(data.error_message ?? 'Operation reported failure')` instead of `notify.success(...)`.
- For Activate/Renew: also display the post-state delta (`License state: Expiring Soon → Active`) so the user has positive confirmation, not just "no error".
- Download Telemetry Report button: gate `disabled` on `licenseInfo.telemetryState === 'Config Report Ready to Download'` (the docstring at `qkview_service.py:1486-1487` makes this an explicit precondition). Button-disabled-with-tooltip beats post-click 405.
- Backend should also map "telemetry not ready" to a 409 with hint rather than passing the upstream 405 through.

## Out of scope

- The operator-dispatch path (`licensing.py:319-329, 370-384`) already gates on `result.get("success")` and raises 502. It's correctly shaped; this ADR doesn't change it.
- Token caching, mTLS cert rotation, JWKS auto-refresh — orthogonal.
- The CWC `_exec_in_pod` reliability question (why did `/reactivate` return empty in the first place?). Likely a multiplexed-websocket stdout dropout under certain timing; a separate investigation. The fix in this ADR makes that investigation observable (we'll get a loud QKViewError) rather than fixing the underlying flake.

## Consequences

**Locality:** the success contract becomes a single rule — "BE returns `success: True` iff the operation observably took effect; FE reads `success` from the body, not from HTTP code". One rule, four enforcement points.

**Class fix:** this is the same class of bug as the helm-releases "No releases shown" empty-state (`KubernetesV2.tsx:773-799` lacks an `isError` branch, masking a 500). Both stem from "HTTP transport success ≠ operation success" — the FE pattern fix in part 4 is a template the helm-releases page should also adopt when D-016 lands. Worth co-locating in a `frontend-v2/src/hooks/lib/useAppMutation.ts` helper that requires explicit success-field-name + success-toast-builder, so future mutations can't be wired loosely.

**Migration cost:** five files, ~80-150 lines of code changes, ~20-40 lines of new tests (one regression test per mutation that asserts garbage input → BE returns `success: false` AND FE shows error toast). No schema migration.

**Risk:** the `_parse_curl_response` hardening (part 1) is the most likely to surface latent issues — any prior call relying on "CWC sometimes returns empty and we silently treat it as success" will start failing loudly. That is the desired behavior, but expect to fix one or two adjacent call sites when this lands.

## Test win

- `tests/test_licensing_success_contract.py` (new): one parametrized test per mutation × {garbage, empty CWC response, CWC 4xx, CWC 5xx, valid input}. Asserts BE response shape (`success`, `error_message`, `license_state` delta where applicable).
- One playwright/component test: garbage JWT → red toast, not green.
- Today there is **no test** that exercises the silent-success path. After: it's the first test that would fail on regression.

## Scheduling note

- Part 1 (`_parse_curl_response`) is the smallest unit and the highest leverage — can land alone as a focused PR; everything else is downstream.
- Parts 2-3 are best landed together (mutation contract + pre-mutation reorder share files and tests).
- Part 4 can land in parallel with parts 1-3; FE/BE contract is enforced by both sides reading the same field name.
- Pair with D-016 (helm celery port) for the FE `isError` pattern — same file, same render-shape lesson.

## References

- Source: 2026-05-21 audit transcript — live probe `POST /api/licensing/9/activate` with `{"jwt":"this-is-not-a-real-jwt-just-garbage"}` returned `HTTP 200 success:true`; backend log shows `CWC POST /reactivate raw response: ` (empty).
- Code (defect sites):
  - `backend/services/qkview_service.py:699-716` (`_check_curl_status` — no-marker fallthrough)
  - `backend/services/qkview_service.py:719-756` (`_parse_curl_response` — default status_code=200)
  - `backend/services/qkview_service.py:1495-1524` (`activate_license` — hardcoded `success: True`)
  - `backend/services/qkview_service.py:1527-1541` (`post_license_receipt` — same)
  - `backend/services/qkview_service.py:1847-1969` (`switch_license` — pre-mutation + step-tracking ignored at return)
  - `backend/services/qkview_service.py:1972-2095` (`force_renew_license` — same)
  - `backend/services/qkview_service.py:1700-1754` (`ensure_valid_jwks` — reference shape, correct)
  - `backend/services/qkview_service.py:1086-1191` (`setup_cwc_api_certs` — reference shape, correct)
  - `frontend-v2/src/hooks/useLicensing.ts:155-214` (FE mutation hooks — ignore response body)
  - `frontend-v2/src/components/k8s/LicensingPanel.tsx:173-238` (handler `onSuccess` — unconditional success toast)
- Related: D-016 (helm celery port — same `HTTP-success ≠ operation-success` class of bug)
- Memory: `feedback_no_orphan_bugs_during_refactor.md` — the receipt-not-silent-today case must still be fixed because it's structurally vulnerable; we don't leave bugs once we've found them.
