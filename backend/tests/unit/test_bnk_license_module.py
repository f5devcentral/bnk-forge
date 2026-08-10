"""
Unit tests for bare-metal/bnk-license SSH module (ADR-478).

Tests:
  - Class attributes (path, name, dependencies, version, timeout)
  - _parse_major_minor helper: correct parsing + edge cases
  - render_manifests: correct License CR shape + fields
  - get_required_crds, get_required_deployments, get_readiness_waits
  - execute() release gating:
      2.2.x → clean no-op (no SSH calls, returns license_active=True)
      2.3.x → delegates to base execute() (applies manifest, waits)
  - collect_outputs returns {"license_active": True}
  - module_registry includes bare-metal/bnk-license

No DB, no live SSH — pure Python + MagicMock.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from modules.bare_metal.bnk_license import BnkLicenseSSHModule, _parse_major_minor

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _module() -> BnkLicenseSSHModule:
    return BnkLicenseSSHModule()


def _vars(**overrides) -> dict:
    """Minimal variable dict accepted by the module."""
    base = {
        "bare_metal_host_id": 1,
        "jwt_token": "eyJ.test.jwt",
        "license_mode": "connected",
        "namespace": "f5-operator",
        "license_cr_name": "bnk-license",
        "manifest_version": "2.3.1-3.2598.3-0.0.304",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _parse_major_minor
# ---------------------------------------------------------------------------

class TestParseMajorMinor:
    def test_parses_231(self):
        assert _parse_major_minor("2.3.1-3.2598.3-0.0.304") == (2, 3)

    def test_parses_221(self):
        assert _parse_major_minor("2.2.1-3.2226.0-0.0.511") == (2, 2)

    def test_parses_plain_version(self):
        assert _parse_major_minor("2.3.0") == (2, 3)

    def test_empty_string_returns_zero(self):
        assert _parse_major_minor("") == (0, 0)

    def test_none_like_empty_returns_zero(self):
        # The caller always str()-wraps, so only test str empty
        assert _parse_major_minor("") == (0, 0)

    def test_garbage_returns_zero(self):
        assert _parse_major_minor("not-a-version") == (0, 0)

    def test_major_only_returns_minor_zero(self):
        assert _parse_major_minor("3") == (3, 0)


# ---------------------------------------------------------------------------
# Class attributes
# ---------------------------------------------------------------------------

class TestClassAttributes:
    def test_path(self):
        assert _module().path == "bare-metal/bnk-license"

    def test_name_nonempty(self):
        assert _module().name

    def test_version(self):
        assert _module().version == "1.0.0"

    def test_timeout_positive(self):
        assert _module().timeout > 0

    def test_dependencies_include_cneinstance(self):
        assert "bare-metal/bnk-cneinstance" in BnkLicenseSSHModule.dependencies

    def test_category_bare_metal(self):
        assert _module().category == "bare-metal"

    def test_target_host(self):
        assert _module().target == "host"

    def test_namespace_var(self):
        assert _module().namespace_var == "namespace"

    def test_default_namespace(self):
        assert _module().default_namespace == "f5-operator"


# ---------------------------------------------------------------------------
# render_manifests
# ---------------------------------------------------------------------------

class TestRenderManifests:
    def test_returns_single_license_cr(self):
        mod = _module()
        manifests = mod.render_manifests(_vars())
        assert len(manifests) == 1

    def test_license_cr_api_version_kind(self):
        manifests = _module().render_manifests(_vars())
        cr = manifests[0]
        assert cr["apiVersion"] == "k8s.f5net.com/v1"
        assert cr["kind"] == "License"

    def test_license_cr_name_and_namespace(self):
        manifests = _module().render_manifests(_vars(
            license_cr_name="my-license", namespace="f5-operator"
        ))
        meta = manifests[0]["metadata"]
        assert meta["name"] == "my-license"
        assert meta["namespace"] == "f5-operator"

    def test_license_cr_jwt_field(self):
        jwt = "eyJhbGciOiJSUzI1NiJ9.payload.sig"
        manifests = _module().render_manifests(_vars(jwt_token=jwt))
        assert manifests[0]["spec"]["jwt"] == jwt

    def test_license_cr_operation_mode(self):
        manifests = _module().render_manifests(_vars(license_mode="offline"))
        assert manifests[0]["spec"]["operationMode"] == "offline"

    def test_license_cr_teem_urls(self):
        spec = _module().render_manifests(_vars())[0]["spec"]
        assert spec["teemCertUrl"] == "https://product.apis.f5.com/ee/v1"
        assert spec["teemEntitlementUrl"] == "https://product-s.apis.f5.com/ee/v1"
        assert spec["teemInitialConfigUrl"] == "https://product-s.apis.f5.com/ee/v1"

    def test_license_cr_defaults(self):
        """Defaults apply when optional inputs are absent."""
        mod = _module()
        manifests = mod.render_manifests({"jwt_token": "eyJ.t.s"})
        meta = manifests[0]["metadata"]
        assert meta["name"] == "bnk-license"
        assert meta["namespace"] == "f5-operator"
        assert manifests[0]["spec"]["operationMode"] == "connected"


# ---------------------------------------------------------------------------
# get_required_crds / get_required_deployments / get_readiness_waits
# ---------------------------------------------------------------------------

class TestGates:
    def test_required_crds_includes_licenses(self):
        mod = _module()
        crds = mod.get_required_crds(_vars())
        assert "licenses.k8s.f5net.com" in crds

    def test_required_deployments_cwc_in_namespace(self):
        mod = _module()
        deps = mod.get_required_deployments(_vars(namespace="f5-operator"))
        assert {"name": "f5-spk-cwc", "namespace": "f5-operator"} in deps

    def test_readiness_waits_license_active(self):
        mod = _module()
        waits = mod.get_readiness_waits(_vars(
            license_cr_name="bnk-license", namespace="f5-operator"
        ))
        assert len(waits) == 1
        w = waits[0]
        assert w["kind"] == "licenses.k8s.f5net.com"
        assert w["name"] == "bnk-license"
        assert w["namespace"] == "f5-operator"
        assert w["condition"] == "condition=LicenseActive"
        assert w["timeout"] == 600

    def test_readiness_waits_respects_cr_name(self):
        mod = _module()
        waits = mod.get_readiness_waits(_vars(license_cr_name="custom-license"))
        assert waits[0]["name"] == "custom-license"


# ---------------------------------------------------------------------------
# collect_outputs
# ---------------------------------------------------------------------------

class TestCollectOutputs:
    def test_returns_license_active_true(self):
        mod = _module()
        out = mod.collect_outputs(MagicMock(), _vars())
        assert out == {"license_active": True}


# ---------------------------------------------------------------------------
# execute() — version gating
# ---------------------------------------------------------------------------

class TestExecuteVersionGating:
    """execute() must be a clean no-op for pre-2.3 releases."""

    def _make_session(self):
        return MagicMock()

    def test_22x_is_noop_no_ssh_calls(self):
        """2.2.x: execute() logs once and returns without touching SSH."""
        mod = _module()
        session = self._make_session()
        logs: list[str] = []

        result = mod.execute(
            session,
            _vars(manifest_version="2.2.1-3.2226.0-0.0.511"),
            logs.append,
        )

        # No SSH commands issued
        session.execute.assert_not_called()

        # Returns license_active=True
        assert result["license_active"] is True
        assert "execution_duration_seconds" in result

        # Logged a skip explanation
        assert any("pre-2.3" in line or "Skipping" in line for line in logs)

    def test_22x_empty_manifest_version_is_noop(self):
        """Empty manifest_version → safe no-op (same as <2.3)."""
        mod = _module()
        session = self._make_session()
        logs: list[str] = []

        result = mod.execute(session, _vars(manifest_version=""), logs.append)

        session.execute.assert_not_called()
        assert result["license_active"] is True

    def test_23x_delegates_to_base_execute(self):
        """2.3.x: execute() must delegate to the BnkSSHModule base class."""
        mod = _module()
        session = self._make_session()
        logs: list[str] = []

        # Patch super().execute() so we don't need a real SSH session
        expected_outputs = {
            "license_active": True,
            "execution_duration_seconds": 1.0,
        }
        with patch.object(
            type(mod).__mro__[1],  # BnkSSHModule
            "execute",
            return_value=expected_outputs,
        ) as mock_base:
            result = mod.execute(
                session,
                _vars(manifest_version="2.3.1-3.2598.3-0.0.304"),
                logs.append,
            )
            mock_base.assert_called_once()

        assert result == expected_outputs

    def test_23x_no_skip_log(self):
        """2.3.x: the no-op skip log must NOT appear."""
        mod = _module()
        logs: list[str] = []

        with patch.object(type(mod).__mro__[1], "execute", return_value={"license_active": True}):
            mod.execute(
                MagicMock(),
                _vars(manifest_version="2.3.0"),
                logs.append,
            )

        assert not any("Skipping" in line for line in logs)


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------

class TestModuleRegistry:
    def test_bnk_license_registered(self):
        """bare-metal/bnk-license must be present in the Python module registry."""
        from modules import get_module_registry
        registry = get_module_registry()
        assert "bare-metal/bnk-license" in registry

    def test_registered_instance_is_correct_type(self):
        from modules import get_module_registry
        registry = get_module_registry()
        assert isinstance(registry["bare-metal/bnk-license"], BnkLicenseSSHModule)
