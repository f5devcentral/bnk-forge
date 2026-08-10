"""ADR-204 catalog-snapshot integrity guard (issue #439).

The parity gate (test_adr204_ssh_parity.py) proves the SSH ports match the
*vendored* catalog snapshot. This file guards the snapshot itself so it can't
drift silently:

- ``catalog_sha()`` was previously defined but never asserted, so a re-vendor
  could change the pinned commit with nothing failing. These tests pin the
  expected SHA and cross-check it against SNAPSHOT.md — re-vendoring now forces
  an intentional update here (the tripwire), instead of a silent change.
- We can't reach the private ``bnk-forge-modules`` catalog from unit CI, so this
  is a *consistency + completeness* guard, not a live "matches catalog today"
  check. Live re-verification is a manual step (refresh.sh) documented in
  SNAPSHOT.md.
"""

from __future__ import annotations

import re

import pytest

from tests.fixtures.catalog_parity import SNAPSHOT_ROOT, catalog_sha

pytestmark = pytest.mark.unit

# Pinned catalog commit the snapshot was vendored from. Bump this (and SNAPSHOT.md
# + refresh.sh) deliberately whenever the snapshot is re-vendored — see issue #439.
EXPECTED_CATALOG_SHA = "97c722e539a8cd32468aeb8d0af868ae210d950b"

# The 7 catalog modules whose pack content backs the ADR-204 parity gate.
EXPECTED_MODULES = [
    "bnk/bnk-gatewayclass",
    "bnk/cneinstance",
    "bnk/flo",
    "k8s/bnk-cert-issuer",
    "k8s/bnk-namespaces",
    "k8s/cert-manager",
    "k8s/network-setup",
]


def test_catalog_sha_is_a_valid_commit_id():
    sha = catalog_sha()
    assert re.fullmatch(r"[0-9a-f]{40}", sha), f"CATALOG_SHA is not a 40-char hex SHA: {sha!r}"


def test_catalog_sha_matches_pinned_tripwire():
    # Fails if the snapshot is re-vendored without updating this test — the
    # intentional signal that the parity baseline moved.
    assert catalog_sha() == EXPECTED_CATALOG_SHA


def test_snapshot_md_records_the_same_sha():
    # The two committed files must agree — catches updating one but not the other.
    snapshot_md = (SNAPSHOT_ROOT / "SNAPSHOT.md").read_text()
    assert catalog_sha() in snapshot_md, "SNAPSHOT.md does not reference the pinned CATALOG_SHA"


def test_every_module_has_a_pack_manifest():
    # Completeness: the parity harness loads bnkforge.pack.json per module.
    missing = [m for m in EXPECTED_MODULES if not (SNAPSHOT_ROOT / m / "bnkforge.pack.json").is_file()]
    assert not missing, f"snapshot modules missing bnkforge.pack.json: {missing}"


def test_no_dead_opentofu_fixtures_reintroduced():
    # Guards issue #440: only pack/manifests/values are load-bearing. The manifest
    # renderer never reads .tf / module.json, so they must not creep back in.
    stray = sorted(
        str(p.relative_to(SNAPSHOT_ROOT))
        for p in SNAPSHOT_ROOT.rglob("*")
        if p.is_file() and (p.suffix == ".tf" or p.name == "module.json")
    )
    assert not stray, f"dead OpenTofu fixtures reintroduced under catalog_snapshot: {stray}"
