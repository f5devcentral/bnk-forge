# D-029 — EKS + BNK Blueprint E2E Reliability: Gate · License · Datapath · Verify

- **Status:** Accepted (umbrella)
- **Date:** 2026-06-13
- **Reference implementation:** `awsbnkctl` (sibling repo) — stands up EKS + BNK fully licensed, traffic-verified, with no manual hacking
- **Related ADRs:** D-017 (honest licensing-success contract — extends), D-003 (deploy reliability workplan), D-016 (helm celery task), D-018 (CRD discovery), D-019 (dynamic-by-default), D-028 (unified blueprint catalog), D-023 (F5 estate)

## Context

Forge's EKS + BNK blueprints (`bnk-on-k8s` template + the `bnk-forge-aws-eks-cluster` module catalog) deploy, but users **cannot get to a working licensed cluster with live traffic without hand-hacking the deployment** — attaching ENIs, applying NADs, installing the EBS CSI driver, labelling nodes, patching crash-looping pods, and never knowing if the license actually activated. Traffic testing, when attempted, is "not right."

The sibling Go CLI **`awsbnkctl` does reach that bar**: from nothing → EKS → fully-licensed BNK → traffic verified across three TMM dataplane patterns, repeatably, no manual steps. It is the executable specification for what "good" looks like. Three code-grounded investigations (forge deploy path, forge module catalog, awsbnkctl) established the gap.

**Root cause — fire-and-forget vs. gated state machine.** Forge applies Helm releases and CRs, then declares success from *hardcoded `true` module outputs and fail-open waits*:

- `bnk/flo/outputs.tf` emits `flo_ready = true` / `crds_installed = true` as **literals** — never checked.
- `cneinstance` `wait_for_available` / `verify_pods` **log a warning and exit 0** on timeout/missing pods; `instance_ready = true` is hardcoded.
- The JWT is injected into FLO Helm values only; there is **no `License` CR and no activation verification**. The capable CWC licensing service (`backend/routes/licensing.py`) is orphaned from the deploy path. This is exactly the **D-017 "HTTP 200 ≠ operation success"** bug class, baked into the blueprint.

awsbnkctl never trusts an apply. It gates every phase on the **controller-written `.status` subresource** (`phase25_activation_poll.go`: `F5TmmAvailable==True && CNEControllerAvailable==True && License.status.state=="Active"` — deliberately *not* the noisy rollup `Available`), re-verifies in postflight, fails closed with pod diagnostics, and self-heals three known BNK races.

Beyond gating, the forge module catalog is **incomplete for the primary (non-HP `beta`) path**: it references a NAD (`ens7-ipvlan-l2`) nothing creates, omits the EBS CSI driver while CNEInstance defaults to `gp3`, leaves secondary-ENI attach + node labelling manual, and the `bnk-vlans`/`network-setup` self-IP modules are absent from the EKS catalog. Plus catalog fragility: 4 of 8 BNK modules are absent on the catalog default `main` and deploy *silently skips missing modules*.

## Decision

Adopt the **awsbnkctl phased-gate contract** for the EKS + BNK blueprint, reusing forge's existing seams (the `kubernetes` engine, module outputs, the orphaned licensing service, the post-deploy verification hooks). The arc is **Preflight → Datapath → License → Gate → Verify**, with self-healing folded in.

Core invariant (the **honest readiness contract**, extending D-017): **no deploy step may report success from an apply result or a hardcoded literal.** Success is read from the operator-reconciled `.status` subresource, with a real timeout, fail-closed, and a diagnostic dump on failure. Kill every hardcoded `*_ready = true` output.

Phases (each a backlog slice; ordering encodes the hard-won awsbnkctl lessons):

- **P1 — Honest readiness gating (foundational).** Replace `flo_ready`/`crds_installed`/`instance_ready` literals and fail-open waits with real `.status` polls: FLO up + `cneinstances` CRD present; CNEInstance gated on `F5TmmAvailable && CNEControllerAvailable`; fail closed with pod-diagnostic dump. Re-verify in the existing post-deploy hook.
- **P2 — License CR + activation verification.** Apply a `License` CR (`k8s.f5net.com/v1`, `connected` mode, JWT inlined) *after* the CNEInstance, then gate on `License.status.state=="Active"`. Wire the existing `routes/licensing.py` service into the deploy path instead of UI-only. Closes the D-017 gap for BNK.
- **P3 — Datapath completion (non-HP path).** Auto-install EBS CSI driver + gp3 StorageClass; auto-create Multus NADs and attach TMM secondary ENIs; port `bnk-vlans`/`network-setup` (F5SPKVlan self-IPs) into the EKS catalog; remove the manual `kubectl label` placement step. This is what eliminates the hand-hacking.
- **P4 — Self-healing for known BNK races.** Port awsbnkctl's three best-effort heals: CWC DNS-warmup crashloop (force-delete on restartCount≥3), DSSM redis `--insecure` probe patch, pod-manager cold-start rollout-restart. Best-effort, never gates.
- **P5 — Fail-fast capacity preflight.** Before any AWS write, validate the target node floor for BNK 2.x Small (≥16 vCPU, ≥64 GiB, ≥3 nodes, pattern-dependent ENI count), aggregating *all* violations. Order CNI prefix-delegation before the node group.
- **P6 — Traffic verification gate.** Port awsbnkctl's three TMM dataplane patterns as a post-deploy verification: L7 Gateway-API HTTP routing, L4 TCP (PROXY iRule real-client-IP), throughput (iperf3 N-S/E-W). Probe the **VIP from the dataplane subnet** (not ClusterIP), gate on `Gateway[Programmed]` + `HTTPRoute[Accepted/ResolvedRefs]`, assert on response body content. Include pool-member resync (toggle backendRef weights ±1).
- **P0 (cross-cutting) — Catalog integrity.** Hard-block (not silently skip) missing required modules; ensure all 8 BNK modules exist on the catalog release branch the blueprint pins; move the default catalog repo off the personal fork.

## Consequences

A forge EKS + BNK deploy that **succeeds only when it is actually licensed and passing traffic** — the same bar awsbnkctl meets — removing the "agents hack it to working order" failure mode and making traffic testing meaningful. Maximal reuse: gating rides the existing `kubernetes` engine + module-output seam; licensing reuses `routes/licensing.py`; verification rides the existing post-deploy hook. P1 alone retires the D-017 bug class for BNK; P3 retires the manual-datapath toil; P6 makes deploy success falsifiable.

Costs / risks: P3 and P6 are real engineering (new modules, traffic harness, jumphost/data-plane reachability) and AWS-account-dependent to validate end-to-end. The self-heals (P4) encode F5-internal race knowledge that drifts per BNK release — treat as best-effort, never load-bearing gates. The license/TEEM URLs and JWS material are F5 product material that must track BNK releases (same manual-review burden the FLO module already carries). Sequence matters: P1 → P2 are prerequisites for trustworthy P6; P3/P5 are independently shippable.

## Non-goals

GKE/AKS parity (this ADR is EKS-scoped; the #301 GCP name bug is tracked separately), classic BIG-IP/TMOS (D-023), and the on-prem `f5licenseproxy` path (connected mode only here).

## References

- Reference implementation: `awsbnkctl` — `internal/cli/lifecycle.go` (phase pipeline), `phase23_license.go` + `phase25_activation_poll.go` (license + gate), `internal/scenarios/` (3 traffic patterns), `phase24*_*.go` (self-heals), `phase00_preflight.go`.
- Forge gaps: `backend/data/stack_templates.json` (`bnk-on-k8s`), `bnk-forge-aws-eks-cluster/modules/eks-cluster-install-flo/outputs.tf` + `eks-cluster-cneinstall/`, `backend/routes/licensing.py` (orphaned), `backend/tasks/opentofu_tasks.py`.
- D-017 (licensing-success contract — this ADR extends it to BNK deploy), D-003 (deploy reliability).
