"""Unit tests for the pure parts of graph-wide supply-chain resolution.

Covers the no-op hooks, the merged dockerconfigjson assembly, and the
ECR-region-from-host parser. DB-backed graph walk + token assembly live in the
component test (``tests/component/test_supply_chain_graph.py``).
"""

from __future__ import annotations

import base64
import json

import pytest

from services.container_registry_service import ContainerRegistryService
from services.execution import supply_chain as sc


@pytest.mark.unit
class TestNoOpHooks:
    def test_mirror_image_is_passthrough(self):
        ref = "ghcr.io/jgruberf5/tools@sha256:" + "a" * 64
        assert sc.mirror_image(ref) == ref

    def test_verify_signature_passes(self):
        ref = "ghcr.io/jgruberf5/tools@sha256:" + "b" * 64
        assert sc.verify_signature(ref) is True


@pytest.mark.unit
class TestMergedDockerConfig:
    def test_merges_multiple_hosts(self):
        auths = {
            "ghcr.io": {"username": "u1", "password": "p1", "auth": "x1"},
            "us.icr.io": {"username": "iamapikey", "password": "tok", "auth": "x2"},
        }
        out_b64 = sc.build_merged_dockerconfigjson(auths)
        doc = json.loads(base64.b64decode(out_b64))
        assert set(doc["auths"].keys()) == {"ghcr.io", "us.icr.io"}
        assert doc["auths"]["ghcr.io"]["username"] == "u1"
        assert doc["auths"]["us.icr.io"]["username"] == "iamapikey"

    def test_empty_auths_yields_empty_doc(self):
        out_b64 = sc.build_merged_dockerconfigjson({})
        doc = json.loads(base64.b64decode(out_b64))
        assert doc == {"auths": {}}


@pytest.mark.unit
class TestEcrRegionParser:
    def test_extracts_region(self):
        host = "123456789012.dkr.ecr.us-east-1.amazonaws.com"
        assert ContainerRegistryService._ecr_region_from_host(host) == "us-east-1"

    def test_returns_none_for_non_ecr_host(self):
        assert ContainerRegistryService._ecr_region_from_host("ghcr.io") is None
