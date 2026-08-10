"""Unit tests for blueprint_context.py — pure transform functions."""

import pytest

from services.execution.blueprint_context import (
    MODULE_TRANSFORMS,
    ProjectContext,
    _derive_selfip_from_vlan,
    _derive_subnet_cidr,
    _resolve_selfip,
    _transform_bnk_gatewayclass,
    _transform_bnk_prerequisites,
    _transform_bnk_vlans,
    _transform_cert_manager,
    _transform_cneinstance,
    _transform_flo,
    _transform_install_cni,
    _transform_network_setup,
    _transform_taint_dpu_node,
    resolve_dialog_field_default,
    transform_for_module,
)


class TestDeriveSelfipFromVlan:
    """Derive self-IP from VLAN subnet + start_host offset."""

    def test_standard_derivation(self):
        assert _derive_selfip_from_vlan("10.10.20.0/24", 100) == "10.10.20.100"

    def test_internal_vlan(self):
        assert _derive_selfip_from_vlan("10.10.10.0/24", 100) == "10.10.10.100"

    def test_offset_1(self):
        assert _derive_selfip_from_vlan("192.168.1.0/24", 1) == "192.168.1.1"

    def test_none_subnet(self):
        assert _derive_selfip_from_vlan(None, 100) is None

    def test_none_start_host(self):
        assert _derive_selfip_from_vlan("10.10.20.0/24", None) is None

    def test_offset_outside_subnet(self):
        """Offset 300 exceeds /24 (256 addresses)."""
        assert _derive_selfip_from_vlan("10.10.20.0/24", 300) is None

    def test_prefix_16(self):
        assert _derive_selfip_from_vlan("10.1.0.0/16", 500) == "10.1.1.244"

    def test_invalid_cidr(self):
        assert _derive_selfip_from_vlan("not-a-cidr", 100) is None


class TestResolveSelfip:
    """_resolve_selfip: parse CIDR, bare IP, or fall back to DPU record."""

    def test_cidr_notation(self):
        ip, prefix = _resolve_selfip("10.0.10.240/24", None)
        assert ip == "10.0.10.240"
        assert prefix == 24

    def test_bare_ip(self):
        ip, prefix = _resolve_selfip("10.0.10.240", None)
        assert ip == "10.0.10.240"
        assert prefix is None

    def test_fallback_to_dpu(self):
        ip, prefix = _resolve_selfip(None, "10.10.20.100")
        assert ip == "10.10.20.100"
        assert prefix is None

    def test_dialog_takes_precedence_over_dpu(self):
        ip, prefix = _resolve_selfip("10.0.10.240/24", "10.10.20.100")
        assert ip == "10.0.10.240"
        assert prefix == 24

    def test_empty_string_falls_through(self):
        ip, prefix = _resolve_selfip("", "10.10.20.100")
        assert ip == "10.10.20.100"

    def test_both_none(self):
        ip, prefix = _resolve_selfip(None, None)
        assert ip is None
        assert prefix is None

    def test_invalid_prefix_returns_ip_without_prefix(self):
        ip, prefix = _resolve_selfip("10.0.10.240/abc", None)
        assert ip == "10.0.10.240"
        assert prefix is None

    def test_out_of_range_prefix_returns_ip_without_prefix(self):
        ip, prefix = _resolve_selfip("10.0.10.240/200", None)
        assert ip == "10.0.10.240"
        assert prefix is None

    def test_strips_whitespace(self):
        ip, prefix = _resolve_selfip("  10.0.10.240/24  ", None)
        assert ip == "10.0.10.240"
        assert prefix == 24


class TestDeriveSubnetCidr:
    def test_standard_cidr(self):
        assert _derive_subnet_cidr("10.0.10.240", 24) == "10.0.10.0/24"

    def test_prefix_16(self):
        assert _derive_subnet_cidr("10.1.20.5", 16) == "10.1.0.0/16"

    def test_invalid_ip(self):
        assert _derive_subnet_cidr("not-an-ip", 24) is None


class TestTransformBnkVlans:
    """The critical transform: canonical → bnk/bnk-vlans catalog variables."""

    def _ctx(self, **kw) -> ProjectContext:
        return ProjectContext(**kw)

    def test_from_dialog_cidr(self):
        """Dialog CIDR → self_ips list + subnet_cidrs derived."""
        variables = {"external_selfip": "10.0.10.240/24", "internal_selfip": "10.0.20.240/24"}
        result = _transform_bnk_vlans(variables, self._ctx())
        assert result["external_self_ips"] == ["10.0.10.240"]
        assert result["internal_self_ips"] == ["10.0.20.240"]
        assert result["external_subnet_cidrs"] == ["10.0.10.0/24"]
        assert result["internal_subnet_cidrs"] == ["10.0.20.0/24"]

    def test_from_dpu_record(self):
        """No dialog input → falls back to DPU record IPs."""
        ctx = self._ctx(
            dpu_external_vlan_ipv4="10.10.20.100",
            dpu_internal_vlan_ipv4="10.10.10.100",
            external_vlan_ipv4_subnet="10.10.20.0/24",
            internal_vlan_ipv4_subnet="10.10.10.0/24",
        )
        result = _transform_bnk_vlans({}, ctx)
        assert result["external_self_ips"] == ["10.10.20.100"]
        assert result["internal_self_ips"] == ["10.10.10.100"]
        assert result["external_subnet_cidrs"] == ["10.10.20.0/24"]
        assert result["internal_subnet_cidrs"] == ["10.10.10.0/24"]

    def test_user_override_wins(self):
        """If external_self_ips already set, transform does NOT override."""
        variables = {"external_self_ips": ["1.2.3.4"], "internal_self_ips": ["5.6.7.8"]}
        result = _transform_bnk_vlans(variables, self._ctx())
        assert "external_self_ips" not in result
        assert "internal_self_ips" not in result

    def test_mtu_from_context(self):
        ctx = self._ctx(high_speed_mtu=9000)
        result = _transform_bnk_vlans({}, ctx)
        assert result["mtu"] == 9000

    def test_mtu_not_overwritten_if_set(self):
        ctx = self._ctx(high_speed_mtu=9000)
        result = _transform_bnk_vlans({"mtu": 1500}, ctx)
        assert "mtu" not in result

    def test_empty_context_produces_nothing(self):
        result = _transform_bnk_vlans({}, self._ctx())
        assert result == {}


class TestTransformCneinstance:
    def test_version_from_profile(self):
        ctx = ProjectContext(bnk_manifest_version="2.2.0")
        result = _transform_cneinstance({}, ctx)
        assert result["manifest_version"] == "2.2.0"

    def test_dpu_enabled_from_context(self):
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_cneinstance({}, ctx)
        assert result["dpu_enabled"] is True

    def test_dpu_implies_sriov_data_plane_mode(self):
        """DPU mode must set tmm_data_plane_mode=sriov, not kernel (default).

        Kernel mode generates empty resource names in TMM DaemonSet causing
        FLO reconcile error: 'name part must be non-empty'.
        """
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_cneinstance({}, ctx)
        assert result["tmm_data_plane_mode"] == "sriov"

    def test_dpu_sriov_overrides_pack_default(self):
        """DPU mode MUST override pack default tmm_data_plane_mode=kernel.

        Pack ships default: "kernel" which lands in variables at Layer 1.
        The transform at Layer 4.5 must override it to "sriov" —
        otherwise TMM DaemonSet gets empty resource names.
        Bug 10 fix: removed "not in variables" guard.
        """
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_cneinstance({"tmm_data_plane_mode": "kernel"}, ctx)
        assert result["tmm_data_plane_mode"] == "sriov"

    def test_non_dpu_does_not_set_sriov_mode(self):
        """Non-DPU deployments should not force sriov mode."""
        ctx = ProjectContext(is_dpu_enabled=False)
        result = _transform_cneinstance({}, ctx)
        assert "tmm_data_plane_mode" not in result

    def test_no_override_when_set(self):
        ctx = ProjectContext(bnk_manifest_version="2.2.0")
        result = _transform_cneinstance({"manifest_version": "custom"}, ctx)
        assert "manifest_version" not in result


class TestTransformInstallCni:
    """DPU OOB subnet injection for Calico multi-subnet detection."""

    def test_dpu_sets_oob_subnet(self):
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_install_cni({}, ctx)
        assert result["dpu_oob_subnet"] == "192.168.100.0/24"

    def test_non_dpu_returns_empty(self):
        ctx = ProjectContext(is_dpu_enabled=False)
        result = _transform_install_cni({}, ctx)
        assert result == {}

    def test_user_override_not_overwritten(self):
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_install_cni({"dpu_oob_subnet": "10.0.0.0/24"}, ctx)
        assert "dpu_oob_subnet" not in result


class TestTransformTaintDpuNode:
    """Bug 6 fix: taint key must be 'dpu' in DPU mode (FLO generates TMM toleration key=dpu)."""

    def test_dpu_sets_taint_key(self):
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_taint_dpu_node({}, ctx)
        assert result["taint_key"] == "dpu"

    def test_non_dpu_returns_empty(self):
        ctx = ProjectContext(is_dpu_enabled=False)
        result = _transform_taint_dpu_node({}, ctx)
        assert result == {}


class TestTransformBnkPrerequisites:
    def test_version_injected(self):
        ctx = ProjectContext(bnk_manifest_version="2.2.0")
        result = _transform_bnk_prerequisites({}, ctx)
        assert result["bnk_manifest_version"] == "2.2.0"

    def test_no_override(self):
        ctx = ProjectContext(bnk_manifest_version="2.2.0")
        result = _transform_bnk_prerequisites({"bnk_manifest_version": "x"}, ctx)
        assert result == {}

    def test_dpu_sets_instance_namespace(self):
        """DPU mode forces instance_namespace=f5-bnk for FLO far-secret placement."""
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_bnk_prerequisites({}, ctx)
        assert result["instance_namespace"] == "f5-bnk"

    def test_non_dpu_no_instance_namespace(self):
        ctx = ProjectContext(is_dpu_enabled=False)
        result = _transform_bnk_prerequisites({}, ctx)
        assert "instance_namespace" not in result


class TestTransformNetworkSetup:
    def test_subnets_from_context(self):
        ctx = ProjectContext(
            external_vlan_ipv4_subnet="10.10.20.0/24",
            internal_vlan_ipv4_subnet="10.10.10.0/24",
        )
        result = _transform_network_setup({}, ctx)
        assert result["external_subnet_cidrs"] == ["10.10.20.0/24"]
        assert result["internal_subnet_cidrs"] == ["10.10.10.0/24"]

    def test_dpu_sets_sriov_data_plane_mode(self):
        """DPU mode must propagate tmm_data_plane_mode=sriov through network-setup.

        This output is wired downstream to bnk/cneinstance via Layer 3 dependency
        wiring. If network-setup stores 'kernel' (the default), Layer 3 overwrites
        the transform's 'sriov' value on cneinstance, causing FLO to generate TMM
        DaemonSet with empty resource names.
        """
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_network_setup({}, ctx)
        assert result["tmm_data_plane_mode"] == "sriov"

    def test_non_dpu_does_not_set_data_plane_mode(self):
        ctx = ProjectContext(is_dpu_enabled=False)
        result = _transform_network_setup({}, ctx)
        assert "tmm_data_plane_mode" not in result

    def test_dpu_sriov_overrides_pack_default(self):
        """DPU mode overrides pack default tmm_data_plane_mode via network-setup too."""
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_network_setup({"tmm_data_plane_mode": "kernel"}, ctx)
        assert result["tmm_data_plane_mode"] == "sriov"


class TestTransformForModule:
    def test_unknown_module_returns_empty(self):
        ctx = ProjectContext()
        assert transform_for_module("unknown/module", {}, ctx) == {}

    def test_registered_module_calls_transform(self):
        ctx = ProjectContext(bnk_manifest_version="2.2.0")
        result = transform_for_module("k8s/bnk-prerequisites", {}, ctx)
        assert result["bnk_manifest_version"] == "2.2.0"

    def test_all_bnk_modules_registered(self):
        expected = {
            "k8s/bnk-prerequisites", "k8s/cert-manager", "k8s/network-setup",
            "bnk/flo", "bnk/cneinstance", "bnk/bnk-vlans", "bnk/bnk-gatewayclass",
            "bare-metal/install-cni", "bare-metal/taint-dpu-node",
            # ADR-204: SSH-port aliases reuse the same transform functions.
            "bare-metal/bnk-prerequisites", "bare-metal/network-setup",
            "bare-metal/cert-manager", "bare-metal/bnk-flo",
            "bare-metal/bnk-cneinstance", "bare-metal/bnk-vlans",
            "bare-metal/bnk-gatewayclass",
        }
        assert expected == set(MODULE_TRANSFORMS.keys())


class TestProjectContext:
    def test_as_low_precedence_vars_omits_none(self):
        ctx = ProjectContext()
        assert ctx.as_low_precedence_vars() == {}

    def test_as_low_precedence_vars_includes_set_values(self):
        ctx = ProjectContext(bnk_manifest_version="2.2.0", high_speed_mtu=9000)
        result = ctx.as_low_precedence_vars()
        assert result["bnk_manifest_version"] == "2.2.0"
        assert result["high_speed_mtu"] == 9000
        assert "external_vlan_id" not in result

    def test_frozen(self):
        ctx = ProjectContext()
        with pytest.raises(AttributeError):
            ctx.bnk_manifest_version = "x"  # type: ignore[misc]


class TestResolveDialogFieldDefault:
    """Pre-populate dialog field defaults from project context (BM-020 name-mismatch fix)."""

    def test_external_selfip_from_dpu_ip(self):
        """external_selfip should be pre-populated from dpu_external_vlan_ipv4."""
        ctx = ProjectContext(dpu_external_vlan_ipv4="10.10.20.100", dpu_internal_vlan_ipv4="10.10.10.100")
        result = resolve_dialog_field_default("external_selfip", "bnk/bnk-vlans", ctx)
        assert result == "10.10.20.100"

    def test_internal_selfip_from_dpu_ip(self):
        """internal_selfip should be pre-populated from dpu_internal_vlan_ipv4."""
        ctx = ProjectContext(dpu_external_vlan_ipv4="10.10.20.100", dpu_internal_vlan_ipv4="10.10.10.100")
        result = resolve_dialog_field_default("internal_selfip", "bnk/bnk-vlans", ctx)
        assert result == "10.10.10.100"

    def test_returns_none_when_no_dpu_ip(self):
        """Returns None when DPU IPs are not resolved yet."""
        ctx = ProjectContext()
        assert resolve_dialog_field_default("external_selfip", "bnk/bnk-vlans", ctx) is None
        assert resolve_dialog_field_default("internal_selfip", "bnk/bnk-vlans", ctx) is None

    def test_returns_none_for_unknown_module(self):
        """No mapping for modules outside the registry."""
        ctx = ProjectContext(dpu_external_vlan_ipv4="10.10.20.100")
        assert resolve_dialog_field_default("external_selfip", "bnk/some-other-module", ctx) is None

    def test_returns_none_for_unknown_field(self):
        """Unknown field names within a known module return None."""
        ctx = ProjectContext(dpu_external_vlan_ipv4="10.10.20.100")
        assert resolve_dialog_field_default("some_unknown_field", "bnk/bnk-vlans", ctx) is None

    def test_returns_string_type(self):
        """Return value must always be str (or None), never a list."""
        ctx = ProjectContext(dpu_external_vlan_ipv4="10.10.20.100")
        result = resolve_dialog_field_default("external_selfip", "bnk/bnk-vlans", ctx)
        assert isinstance(result, str)


class TestTransformBnkGatewayclass:
    """Bug 11 fix: GatewayClass must get flo_namespace from DPU context."""

    def test_dpu_sets_flo_namespace(self):
        """DPU mode sets flo_namespace=f5-bnk for correct controllerName construction."""
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_bnk_gatewayclass({}, ctx)
        assert result["flo_namespace"] == "f5-bnk"

    def test_dpu_sets_controller_name(self):
        """DPU mode must set controller_name directly: the kubernetes-direct manifest
        renders controllerName from controller_name (not flo_namespace), and otherwise
        falls back to the pack's hardcoded f5-operator default — which the BNK CNE
        controller (in f5-bnk) never accepts. Verified live: only the f5-bnk form is
        Accepted."""
        ctx = ProjectContext(is_dpu_enabled=True)
        result = _transform_bnk_gatewayclass({}, ctx)
        assert result["controller_name"] == "f5.com/f5-bnk-f5-cne-controller"

    def test_non_dpu_returns_empty(self):
        """Non-DPU mode should not set flo_namespace/controller_name (pack default is fine)."""
        ctx = ProjectContext(is_dpu_enabled=False)
        result = _transform_bnk_gatewayclass({}, ctx)
        assert result == {}
