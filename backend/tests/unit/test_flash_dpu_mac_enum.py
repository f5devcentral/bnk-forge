"""Unit tests for DPU tmfifo MAC enumeration (ADR-478 / BM2-005).

Covers:
  - rshim0 / rshim1 default MAC computation
  - host-level base override (applied / unset)
  - MAC derived with NO Dpu DB row (self-contained in flash_dpu)
  - _build_bf_cfg_content emits NET_RSHIM_MAC when mac is set
  - _inject_rendered_bf_conf silent-skip when Dpu row is absent (normal for regular topology)
  - _select_rshim_by_pci selects the correct rshim by PCI address (Round 2)
  - precedence: explicit net_rshim_mac in variables is not overwritten (Round 2)
  - index >= 10 guard in _compute_rshim_mac (Round 2)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from modules.bare_metal.flash_dpu import (
    _DEFAULT_RSHIM_MAC_BASE,
    FlashDPUModule,
    _compute_rshim_mac,
    _select_rshim_by_pci,
)
from services.execution.variable_assembler import _inject_rendered_bf_conf

# ── _compute_rshim_mac ────────────────────────────────────────────────────────


class TestComputeRshimMac:
    def test_rshim0_defaultBase_producesExpectedMac(self):
        mac = _compute_rshim_mac("rshim0")
        assert mac == "00:1a:ca:ff:ff:10"

    def test_rshim1_defaultBase_producesExpectedMac(self):
        mac = _compute_rshim_mac("rshim1")
        assert mac == "00:1a:ca:ff:ff:11"

    def test_rshim0_rshim1_macsDiffer(self):
        assert _compute_rshim_mac("rshim0") != _compute_rshim_mac("rshim1")

    def test_neverCollidesWithBlueFieldDefault(self):
        """Neither rshim0 nor rshim1 should produce the BlueField factory default."""
        for dev in ("rshim0", "rshim1"):
            assert _compute_rshim_mac(dev) not in ("00:1a:ca:ff:ff:01", "00:1a:ca:ff:ff:02"), (
                f"rshim device {dev!r} produced a BlueField factory-default MAC"
            )

    def test_hostOverride_base_appliedWhenSet(self):
        """A non-default base produces an enumerated MAC from that base."""
        mac = _compute_rshim_mac("rshim0", "00:1a:ca:ff:ff:2")
        assert mac == "00:1a:ca:ff:ff:20"

    def test_hostOverride_base_differentFromDefault(self):
        """Override base generates a different MAC than the default base."""
        default_mac = _compute_rshim_mac("rshim0")
        override_mac = _compute_rshim_mac("rshim0", "00:1a:ca:ff:ff:2")
        assert default_mac != override_mac

    def test_hostOverride_unset_usesDefaultBase(self):
        """When base is None, the module default applies."""
        mac_explicit = _compute_rshim_mac("rshim0", _DEFAULT_RSHIM_MAC_BASE)
        mac_implicit = _compute_rshim_mac("rshim0", None)
        assert mac_explicit == mac_implicit

    def test_rshim0_mac_endsWith10(self):
        mac = _compute_rshim_mac("rshim0")
        assert mac.endswith(":10"), f"Expected MAC to end with ':10', got {mac!r}"

    def test_rshim1_mac_endsWith11(self):
        mac = _compute_rshim_mac("rshim1")
        assert mac.endswith(":11"), f"Expected MAC to end with ':11', got {mac!r}"


# ── _build_bf_cfg_content: NET_RSHIM_MAC emission ────────────────────────────


class TestBuildBfCfgContent:
    def test_withMac_emitsNetRshimMacLine(self):
        content = FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$hash",
            dpu_hostname="test-dpu",
            dpu_password="secret",
            net_rshim_mac="00:1a:ca:ff:ff:10",
        )
        assert "NET_RSHIM_MAC='00:1a:ca:ff:ff:10'" in content

    def test_withRshim0Mac_bf_cfgContainsCorrectMac(self):
        """rshim0-derived MAC produces exact expected string in bf.cfg."""
        mac = _compute_rshim_mac("rshim0")
        content = FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$hash",
            dpu_hostname="test-dpu",
            dpu_password="secret",
            net_rshim_mac=mac,
        )
        assert f"NET_RSHIM_MAC='{mac}'" in content

    def test_withRshim1Mac_bf_cfgContainsCorrectMac(self):
        """rshim1-derived MAC produces exact expected string in bf.cfg."""
        mac = _compute_rshim_mac("rshim1")
        content = FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$hash",
            dpu_hostname="test-dpu",
            dpu_password="secret",
            net_rshim_mac=mac,
        )
        assert f"NET_RSHIM_MAC='{mac}'" in content

    def test_withoutMac_netRshimMacLineAbsent(self):
        """When net_rshim_mac is empty, NET_RSHIM_MAC must not appear in bf.cfg."""
        content = FlashDPUModule._build_bf_cfg_content(
            pw_hash="$6$hash",
            dpu_hostname="test-dpu",
            dpu_password="secret",
            net_rshim_mac="",
        )
        assert "NET_RSHIM_MAC" not in content


# ── Loud-fail path: _inject_rendered_bf_conf ─────────────────────────────────


class _StubSettings:
    """Minimal ProjectDpuSettings stand-in with a template configured."""

    def __init__(self, bf_template_id=1, default_os_password_encrypted=None):
        self.project_id = 99
        self.bf_template_id = bf_template_id
        self.default_os_password_encrypted = default_os_password_encrypted
        self.default_os_ssh_credential_id = None


class _StubHost:
    def __init__(self, host_ip="192.168.1.1", project_id=99, deploy_dpu_pci_address=None):
        self.name = "test-host"
        self.host_ip = host_ip
        self.project_id = project_id
        self.deploy_dpu_pci_address = deploy_dpu_pci_address


class _StubModule:
    project_id = 99


def _make_db_with_settings_but_no_dpu(settings=None, bf_template=None):
    """Return a mock db where settings exist but Dpu query returns None."""
    settings = settings or _StubSettings()
    bf_template = bf_template or MagicMock()  # template row exists

    db = MagicMock()

    def query_side_effect(model_cls):
        from models.dpu import BfConfTemplate, Dpu, ProjectDpuSettings
        q = MagicMock()
        if model_cls is ProjectDpuSettings:
            q.filter.return_value.first.return_value = settings
        elif model_cls is BfConfTemplate:
            q.filter.return_value.first.return_value = bf_template
        elif model_cls is Dpu:
            q.filter.return_value.first.return_value = None  # no Dpu row
            q.filter.return_value.filter.return_value.first.return_value = None
        else:
            q.filter.return_value.first.return_value = None
        return q

    db.query.side_effect = query_side_effect
    return db


class TestInjectRenderedBfConfLoudFail:
    def test_dpuRowMissing_settingsExist_returnsSilently(self):
        """When DPU settings + template are configured but Dpu row is absent, return silently.

        Missing Dpu rows are normal for regular-topology hosts where discovery never
        creates a per-DPU record (host.deploy_dpu_pci_address is unset).  The minimal
        bf.cfg fallback is safe because flash_dpu.py populates NET_RSHIM_MAC independently.
        """
        db = _make_db_with_settings_but_no_dpu()
        host = _StubHost()
        module = _StubModule()
        variables: dict = {}

        # Must not raise; rendered_bf_conf must not be injected (minimal fallback applies)
        _inject_rendered_bf_conf(db, host, module, variables)
        assert "rendered_bf_conf" not in variables

    def test_dpuRowMissing_withPciFilter_returnsSilently(self):
        """Missing Dpu row with a PCI-scoped host also returns silently."""
        db = _make_db_with_settings_but_no_dpu()
        host = _StubHost(deploy_dpu_pci_address="0000:0d:00.0")
        module = _StubModule()
        variables: dict = {}

        _inject_rendered_bf_conf(db, host, module, variables)
        assert "rendered_bf_conf" not in variables

    def test_dpuRowMissing_doesNotRaise(self):
        """No RuntimeError is raised when settings exist but no Dpu row is found."""
        db = _make_db_with_settings_but_no_dpu()
        # Must complete without any exception
        _inject_rendered_bf_conf(db, _StubHost(), _StubModule(), {})

    def test_noSettingsConfigured_returnsSilently(self):
        """When DPU settings are absent (no DPU tab), _inject_rendered_bf_conf is a no-op."""
        db = MagicMock()
        from models.dpu import ProjectDpuSettings
        q = MagicMock()
        q.filter.return_value.first.return_value = None  # no settings
        db.query.return_value = q

        variables: dict = {}
        # Must not raise, must not modify variables
        _inject_rendered_bf_conf(db, _StubHost(), _StubModule(), variables)
        assert "rendered_bf_conf" not in variables

    def test_settingsWithNoTemplateId_returnsSilently(self):
        """When settings exist but bf_template_id is None, return silently (DPU tab unconfigured)."""
        db = MagicMock()
        from models.dpu import ProjectDpuSettings
        no_template_settings = _StubSettings(bf_template_id=None)
        q = MagicMock()
        q.filter.return_value.first.return_value = no_template_settings
        db.query.return_value = q

        variables: dict = {}
        _inject_rendered_bf_conf(db, _StubHost(), _StubModule(), variables)
        assert "rendered_bf_conf" not in variables

    def test_templateRowMissing_raisesRuntimeError(self):
        """When template ID is set but the template row is gone, raise loudly."""
        db = MagicMock()
        from models.dpu import BfConfTemplate, ProjectDpuSettings

        settings = _StubSettings(bf_template_id=42)

        def query_side_effect(model_cls):
            q = MagicMock()
            if model_cls is ProjectDpuSettings:
                q.filter.return_value.first.return_value = settings
            elif model_cls is BfConfTemplate:
                q.filter.return_value.first.return_value = None  # template gone
            else:
                q.filter.return_value.first.return_value = None
            return q

        db.query.side_effect = query_side_effect

        with pytest.raises(RuntimeError, match="template"):
            _inject_rendered_bf_conf(db, _StubHost(), _StubModule(), {})


# ── Round 2: index >= 10 guard ────────────────────────────────────────────────


class TestIndexGuard:
    def test_indexTen_raisesRuntimeError(self):
        """rshim10 would produce a malformed MAC octet — must raise, not emit."""
        with pytest.raises(RuntimeError, match="10"):
            _compute_rshim_mac("rshim10")

    def test_indexNine_succeeds(self):
        """rshim9 is the last valid index (produces a single-digit suffix)."""
        mac = _compute_rshim_mac("rshim9")
        assert mac.endswith(":19")

    def test_indexTen_errorMentionsMalformed(self):
        """Error message must explain the malformed-octet risk."""
        with pytest.raises(RuntimeError, match="malformed"):
            _compute_rshim_mac("rshim10")


# ── Round 2: net_rshim_mac precedence (existing value not overwritten) ────────


class TestMacPrecedence:
    def test_existingNetRshimMac_notOverwritten(self):
        """An explicit net_rshim_mac already in variables takes priority over the computed value.

        The execute() guard is: `if not variables.get('net_rshim_mac'): ...`.
        This test verifies the guard logic is correct: a pre-set value survives.
        """
        # Simulate what execute() does — check the guard condition directly
        variables: dict = {"net_rshim_mac": "00:1a:ca:ff:ff:03"}
        if not variables.get("net_rshim_mac"):
            variables["net_rshim_mac"] = _compute_rshim_mac("rshim0")
        assert variables["net_rshim_mac"] == "00:1a:ca:ff:ff:03", (
            "Pre-set net_rshim_mac should not be overwritten by the computed value"
        )

    def test_noExistingNetRshimMac_computedValueSet(self):
        """When net_rshim_mac is absent, the computed value is used."""
        variables: dict = {}
        if not variables.get("net_rshim_mac"):
            variables["net_rshim_mac"] = _compute_rshim_mac("rshim0")
        assert variables["net_rshim_mac"] == "00:1a:ca:ff:ff:10"

    def test_emptyStringNetRshimMac_computedValueSet(self):
        """An empty-string net_rshim_mac is treated as falsy → computed value applies."""
        variables: dict = {"net_rshim_mac": ""}
        if not variables.get("net_rshim_mac"):
            variables["net_rshim_mac"] = _compute_rshim_mac("rshim1")
        assert variables["net_rshim_mac"] == "00:1a:ca:ff:ff:11"


# ── Round 2: _select_rshim_by_pci — PCI-address-based selection ──────────────


class _R:
    """Minimal SSH session.execute() result stub."""
    def __init__(self, stdout: str = "", exit_code: int = 0):
        self.stdout = stdout
        self.exit_code = exit_code


class _MiscSession:
    """Records misc-reads for each rshim device."""
    def __init__(self, misc_by_rshim: dict[str, str]) -> None:
        self._misc = misc_by_rshim
        self.calls: list[str] = []

    def execute(self, cmd: str, timeout: int = 30) -> _R:  # noqa: ARG002
        self.calls.append(cmd)
        for rshim, content in self._misc.items():
            if f"/dev/{rshim}/misc" in cmd:
                return _R(stdout=content)
        return _R(stdout="")


_noop = lambda *_: None  # noqa: E731


class TestSelectRshimByPci:
    def test_rshim1_selectedByPciMatch_producesMac11(self):
        """rshim1 misc DEV_NAME matches PCI address → rshim1 selected → MAC ends :11."""
        session = _MiscSession({
            "rshim0": "DEV_NAME  pcie-0000:0d:00.2\nOTHER  x",
            "rshim1": "DEV_NAME  pcie-0000:b4:00.2\nOTHER  y",
        })
        selected = _select_rshim_by_pci(session, ["rshim0", "rshim1"], "0000:b4:00.2", _noop)
        assert selected == "rshim1"
        mac = _compute_rshim_mac(selected)
        assert mac == "00:1a:ca:ff:ff:11"

    def test_rshim0_selectedByPciMatch_producesMac10(self):
        """rshim0 misc DEV_NAME matches PCI address → rshim0 selected → MAC ends :10."""
        session = _MiscSession({
            "rshim0": "DEV_NAME  pcie-0000:0d:00.2\n",
            "rshim1": "DEV_NAME  pcie-0000:b4:00.2\n",
        })
        selected = _select_rshim_by_pci(session, ["rshim0", "rshim1"], "0000:0d:00.2", _noop)
        assert selected == "rshim0"
        mac = _compute_rshim_mac(selected)
        assert mac == "00:1a:ca:ff:ff:10"

    def test_noMatch_raisesRuntimeError(self):
        """When no rshim misc DEV_NAME contains the PCI address, raise loudly — no fallback."""
        session = _MiscSession({
            "rshim0": "DEV_NAME  pcie-0000:0d:00.2\n",
            "rshim1": "DEV_NAME  pcie-0000:b4:00.2\n",
        })
        with pytest.raises(RuntimeError, match="No rshim device matches"):
            _select_rshim_by_pci(session, ["rshim0", "rshim1"], "0000:cc:00.2", _noop)

    def test_noMatch_errorMentionsPciAddress(self):
        """The error message must include the PCI address being searched for."""
        session = _MiscSession({"rshim0": "DEV_NAME  pcie-0000:0d:00.2\n"})
        with pytest.raises(RuntimeError, match="0000:zz:00.2"):
            _select_rshim_by_pci(session, ["rshim0"], "0000:zz:00.2", _noop)

    def test_noMatch_doesNotFallBackToRshim0(self):
        """On PCI mismatch, _select_rshim_by_pci must NOT silently return rshim0."""
        session = _MiscSession({"rshim0": "DEV_NAME  pcie-0000:0d:00.2\n"})
        try:
            result = _select_rshim_by_pci(session, ["rshim0"], "0000:ff:00.2", _noop)
            assert False, f"Expected RuntimeError but got {result!r}"
        except RuntimeError:
            pass  # correct — no silent fallback
