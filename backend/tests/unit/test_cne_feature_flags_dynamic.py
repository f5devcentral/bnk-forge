"""
Unit tests for #135 — CNE feature flags derived from live spec dict-with-enabled
keys in all 3 views (health, topology, scanner).

Verifies:
- coreCollection and envDiscovery are surfaced (were previously dropped)
- Unknown future feature keys are included automatically
- Bare-bool spec values (not dicts) are not treated as feature flags
- All three views produce consistent output for the same spec
"""

import pytest

from services.bnk.health import _derive_spec_feature_flags, _extract_cne_features
from services.bnk.topology import _build_cne_instance
from services.scanner.bnk_install import _parse_cneinstance

# ── Shared spec fixture ───────────────────────────────────────────────


def _full_spec() -> dict:
    """Realistic CNEInstance spec with all known feature flags."""
    return {
        "firewallACL": {"enabled": True},
        "intelligentLB": {"enabled": False},
        "pseudoCNI": {"enabled": True},
        "metricSubsystem": {"enabled": True},
        "loggingSubsystem": {"enabled": False},
        "coreCollection": {"enabled": True},
        "envDiscovery": {"enabled": False},
        # Non-feature keys that should not appear in features output
        "containerPlatform": "k8s",
        "networkAttachments": ["net-1"],
    }


def _make_cne(name: str = "cne-1", spec: dict | None = None) -> dict:
    return {
        "metadata": {"name": name, "namespace": "f5-bnk"},
        "spec": spec if spec is not None else _full_spec(),
        "status": {"phase": "Running"},
    }


# ── _derive_spec_feature_flags (health module helper) ────────────────


class TestDeriveSpecFeatureFlags:
    def test_includes_core_collection(self):
        result = _derive_spec_feature_flags(_full_spec())
        assert "coreCollection" in result
        assert result["coreCollection"] is True

    def test_includes_env_discovery(self):
        result = _derive_spec_feature_flags(_full_spec())
        assert "envDiscovery" in result
        assert result["envDiscovery"] is False

    def test_excludes_non_dict_keys(self):
        result = _derive_spec_feature_flags(_full_spec())
        assert "containerPlatform" not in result
        assert "networkAttachments" not in result

    def test_unknown_future_feature_is_included(self):
        spec = {"futureFeature": {"enabled": True}}
        result = _derive_spec_feature_flags(spec)
        assert result["futureFeature"] is True

    def test_dict_without_enabled_key_is_excluded(self):
        # A dict that doesn't have 'enabled' is not a feature flag
        spec = {"notAFeature": {"something": "else"}}
        result = _derive_spec_feature_flags(spec)
        assert "notAFeature" not in result

    def test_empty_spec_returns_empty_dict(self):
        assert _derive_spec_feature_flags({}) == {}


# ── health.py _extract_cne_features ──────────────────────────────────


class TestExtractCneFeaturesNowDynamic:
    def test_core_collection_surfaced(self):
        f = _extract_cne_features([_make_cne()])
        assert "coreCollection" in f
        assert f["coreCollection"] is True

    def test_env_discovery_surfaced(self):
        f = _extract_cne_features([_make_cne()])
        assert "envDiscovery" in f
        assert f["envDiscovery"] is False

    def test_unknown_spec_key_is_included(self):
        cne = _make_cne(spec={"novelFeature": {"enabled": True}})
        f = _extract_cne_features([cne])
        assert f["novelFeature"] is True


# ── topology.py _build_cne_instance ──────────────────────────────────


class TestBuildCneInstanceNowDynamic:
    def test_core_collection_surfaced(self):
        result = _build_cne_instance(_make_cne())
        assert "coreCollection" in result["features"]
        assert result["features"]["coreCollection"] is True

    def test_env_discovery_surfaced(self):
        result = _build_cne_instance(_make_cne())
        assert "envDiscovery" in result["features"]
        assert result["features"]["envDiscovery"] is False

    def test_unknown_spec_key_is_included(self):
        cne = _make_cne(spec={"novelFeature": {"enabled": True}})
        result = _build_cne_instance(cne)
        assert result["features"]["novelFeature"] is True


# ── scanner/bnk_install.py _parse_cneinstance ────────────────────────


class TestParseCneInstanceNowDynamic:
    def test_core_collection_surfaced(self):
        result = _parse_cneinstance([_make_cne()])
        assert "coreCollection" in result["features"]
        assert result["features"]["coreCollection"] is True

    def test_env_discovery_surfaced(self):
        result = _parse_cneinstance([_make_cne()])
        assert "envDiscovery" in result["features"]
        assert result["features"]["envDiscovery"] is False

    def test_unknown_spec_key_is_included(self):
        cne = _make_cne(spec={"novelFeature": {"enabled": True}})
        result = _parse_cneinstance([cne])
        assert result["features"]["novelFeature"] is True


# ── Cross-view consistency ────────────────────────────────────────────


class TestAllThreeViewsAgree:
    """All three views must produce identical feature flags for the same spec."""

    def test_all_views_agree_on_full_spec(self):
        cne = _make_cne()

        health_features = {k: v for k, v in _extract_cne_features([cne]).items() if k != "name"}
        topology_features = _build_cne_instance(cne)["features"]
        scanner_features = _parse_cneinstance([cne])["features"]

        assert health_features == topology_features == scanner_features, (
            f"Views disagree:\n"
            f"  health:   {health_features}\n"
            f"  topology: {topology_features}\n"
            f"  scanner:  {scanner_features}"
        )
