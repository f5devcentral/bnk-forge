# Catalog snapshot — ADR-204 parity source

Vendored from `git@github.com:JLCode-tech/bnk-forge-modules.git`
- branch: fix/cneinstance-sriov-dataplane
- commit: 97c722e539a8cd32468aeb8d0af868ae210d950b

Used by parity tests to render the catalog path (build_manifest_payload /
build_helm_payload) and diff against the bare-metal/bnk-* SSH ports for the DPU case.

## What's vendored

Only the **load-bearing** files per module: `bnkforge.pack.json`, `manifests/`, and
`values.yaml`. The `.tf` / `module.json` / `README.md` / `examples/` are intentionally
NOT vendored — the manifest renderer never reads them (issue #440).

## Drift guard

`catalog_sha()` is asserted in `tests/unit/test_adr204_catalog_snapshot.py`
(`EXPECTED_CATALOG_SHA`), which also cross-checks the SHA recorded here. Re-vendoring
the snapshot therefore forces an intentional update in that test + this file — it can't
change silently (issue #439). We can't reach the private catalog from unit CI, so this
is a consistency+completeness guard, not a live "matches catalog today" check; the live
re-check is the manual `refresh.sh` step below.

## Regenerate

    ./refresh.sh <commit-sha> [branch]

then bump `EXPECTED_CATALOG_SHA` in `test_adr204_catalog_snapshot.py`, update the
commit/branch above, and re-run the parity + snapshot tests. Any parity diff is a real
behaviour delta — review it, don't just re-baseline.

## Pinned to a WIP branch — planned resolution

The pin is an **unmerged** branch (`fix/cneinstance-sriov-dataplane`), and the cneinstance
parity strips `spec.dataPlane` from the catalog side before asserting, because that field
is a real upstream catalog bug (released FLO 2.2 CNEInstance has no `spec.dataPlane`; DPU/sriov
is expressed via `spec.dpu.enabled` + networkAttachments). When that fix lands upstream:
re-vendor at the merged SHA via `refresh.sh`, then drop the `spec.dataPlane` carve-out in
`test_adr204_ssh_parity.py` if the merged catalog no longer emits it. Until then the pin is
deliberate and the carve-out is documented in the parity test.
