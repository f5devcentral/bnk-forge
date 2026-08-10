"""Unit tests for the bf.conf renderer."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.bf_conf_renderer import (
    BfConfRenderError,
    HostContext,
    RenderContext,
    VlanContext,
    _derive_bfb_hostname,
    bfb_with_config_stream,
    build_render_context,
    derive_tmfifo_dpu_host,
    derive_tmfifo_dpu_ip,
    lint_bf_conf,
    public_key_from_private,
    render_bf_conf,
    sha512_crypt,
)

# ── Helpers ────────────────────────────────────────────────────────────────

class _Stub:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _tpl(content: str):
    # Minimal BfConfTemplate stand-in — the renderer only reads .name + .content.
    return _Stub(name="test", content=content)


# ── Tests ──────────────────────────────────────────────────────────────────

class TestRender:
    def test_renders_simple_placeholders(self):
        ctx = RenderContext(
            bfb_hostname="dpu-123", dpu_mtu=9000,
            ubuntu_password_hash="$6$abc",
            host=HostContext(dns_ip="1.1.1.1,8.8.8.8"),
        )
        out = render_bf_conf(
            _tpl("MTU={{ dpu_mtu }} pw={{ DPU_UBUNTU_PASSWORD_HASH }} h={{ bfb_hostname }}"),
            ctx,
        )
        assert out == "MTU=9000 pw=$6$abc h=dpu-123"

    def test_renders_vlan_loop(self):
        ctx = RenderContext(
            bfb_hostname="dpu-1", dpu_mtu=9000, ubuntu_password_hash="x",
            host=HostContext(
                vlans=[
                    VlanContext(name="external0", tag=0, uplink="p0", ip="10.0.0.1/24"),
                    VlanContext(
                        name="internal100", tag=100, uplink="p1", ip="10.1.0.1/24",
                        default_gateway="10.1.0.254",
                    ),
                ],
            ),
        )
        tpl = (
            "{% for v in hostvars[bfb_hostname].vlans %}"
            "{{ v.name }}|{{ v.tag }}|{{ v.uplink }}|{{ v.ip }}"
            "{% if v.default_gateway is defined %}|{{ v.default_gateway }}{% endif %}\n"
            "{% endfor %}"
        )
        out = render_bf_conf(_tpl(tpl), ctx)
        assert "external0|0|p0|10.0.0.1/24\n" in out
        assert "internal100|100|p1|10.1.0.1/24|10.1.0.254\n" in out

    def test_strict_undefined_raises(self):
        ctx = RenderContext(
            bfb_hostname="dpu-1", dpu_mtu=9000, ubuntu_password_hash="x",
            host=HostContext(),
        )
        with pytest.raises(BfConfRenderError):
            render_bf_conf(_tpl("{{ never_defined }}"), ctx)


class TestSha512Crypt:
    def test_produces_sha512_shadow_format(self):
        h = sha512_crypt("hunter2")
        # crypt format: $6$[rounds=N$]<salt>$<hash> — passlib may insert
        # the optional rounds=N segment.
        assert h.startswith("$6$")
        parts = h.split("$")
        assert len(parts) in (4, 5)


class TestPublicKeyFromPrivate:
    def test_roundtrip_ed25519(self):
        # Build a fresh Ed25519 and ensure we can derive its pubkey.
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        priv = Ed25519PrivateKey.generate()
        priv_ssh = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode()
        pub = public_key_from_private(priv_ssh, comment="unit-test")
        assert pub.startswith("ssh-ed25519 ")
        assert pub.endswith(" unit-test")


class TestBuildContext:
    def _make_dpu_and_settings(self, **overrides):
        dpu = _Stub(
            id=1,
            serial_number="MT-SERIAL-42",
            external_vlan_ipv4="10.0.0.1/24", external_vlan_ipv6=None,
            internal_vlan_ipv4="10.1.0.1/24", internal_vlan_ipv6=None,
            extra_vlan_interfaces=[],
        )
        settings = _Stub(
            high_speed_mtu=9000,
            dns_servers=["1.1.1.1", "8.8.8.8"],
            ntp_servers=["ntp.ubuntu.com"],
            external_vlan_id=10, external_vlan_uplink="bond0",
            internal_vlan_id=20, internal_vlan_uplink="bond0",
            extra_vlan_interfaces=[],
        )
        for k, v in overrides.items():
            target = dpu if hasattr(dpu, k) else settings
            setattr(target, k, v)
        return dpu, settings

    def test_skips_unconfigured_vlan_rows(self):
        dpu, settings = self._make_dpu_and_settings()
        ctx = build_render_context(
            dpu=dpu, settings=settings, ssh_credential=None,
            ubuntu_password_plaintext="pw", decrypt=lambda x: x,
        )
        # Storage has no VLAN id / uplink — must be excluded.
        names = [v.name for v in ctx.host.vlans]
        assert names == ["external10", "internal20"]
        # Password was hashed to $6$...
        assert ctx.ubuntu_password_hash.startswith("$6$")
        # DNS joined.
        assert ctx.host.dns_ip == "1.1.1.1,8.8.8.8"

    def test_authorized_keys_derived_from_private_key(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        priv = Ed25519PrivateKey.generate()
        priv_ssh = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ).decode()

        cred = _Stub(name="dpu-os-key", private_key_encrypted="encrypted-blob")
        dpu, settings = self._make_dpu_and_settings()

        ctx = build_render_context(
            dpu=dpu, settings=settings,
            ssh_credential=cred,
            ubuntu_password_plaintext="pw",
            decrypt=lambda blob: priv_ssh,  # stub decrypt → plain key
        )
        assert ctx.host.authorized_keys.startswith("ssh-ed25519 ")
        assert "bnk-forge:dpu-os-key" in ctx.host.authorized_keys


class TestStream:
    def test_stream_emits_bfb_then_bf_cfg(self, tmp_path: Path):
        bfb = tmp_path / "f.bfb"
        bfb.write_bytes(b"AAAA" * 100)
        cfg = "ubuntu_PASSWORD='x'\n"
        out = b"".join(bfb_with_config_stream(bfb, cfg, chunk_size=64))
        assert out == b"AAAA" * 100 + cfg.encode()


class TestLint:
    def test_clean_bf_conf_has_no_issues(self):
        content = (
            'UPDATE_DPU_OS="yes"\n'
            'ubuntu_PASSWORD=\'$6$abc$xyz\'\n'
            'MTU=9000\n'
            'bfb_modify_os() {\n'
            '    cat << EOFMGMT > /mnt/etc/netplan/50-mgmt.yaml\n'
            'network:\n'
            '  renderer: networkd\n'
            '  ethernets:\n'
            '    oob_net0:\n'
            '      dhcp4: true\n'
            '  version: 2\n'
            'EOFMGMT\n'
            '}\n'
        )
        assert lint_bf_conf(content) == []

    def test_catches_unresolved_jinja(self):
        issues = lint_bf_conf("ubuntu_PASSWORD='{{ missing }}'\n")
        assert any("unresolved Jinja" in i for i in issues)

    def test_catches_unbalanced_heredoc(self):
        bad = (
            'bfb_modify_os() {\n'
            '    cat << EOFMGMT > /mnt/etc/netplan/50-mgmt.yaml\n'
            'network:\n'
            '  renderer: networkd\n'
            '}\n'  # missing EOFMGMT closer
        )
        issues = lint_bf_conf(bad)
        assert any("never closed" in i or "bash -n" in i for i in issues)

    def test_catches_bad_yaml_indentation(self):
        # "renderer:" is indented under "network:" correctly, but "ethernets"
        # is outdented by a tab → broken mapping.
        bad = (
            'cat << EOFMGMT > /mnt/etc/netplan/50-mgmt.yaml\n'
            'network:\n'
            '  renderer: networkd\n'
            ' ethernets:\n'  # 1-space indent — inconsistent with 2-space above
            '   oob_net0: {dhcp4: true}\n'
            'EOFMGMT\n'
        )
        issues = lint_bf_conf(bad)
        assert any("YAML heredoc" in i for i in issues), issues

    def test_catches_shell_syntax_error(self):
        # Unclosed quote → bash -n should complain.
        bad = "ubuntu_PASSWORD='unterminated\nMTU=9000\n"
        issues = lint_bf_conf(bad)
        assert any("bash -n" in i for i in issues), issues

    def test_non_yaml_heredocs_not_yaml_validated(self):
        # Redirected to .sh / .service / etc — skipped.
        content = (
            'cat << EOFSH > /mnt/usr/local/sbin/foo.sh\n'
            '#!/bin/bash\n'
            'echo hi\n'
            'EOFSH\n'
        )
        assert lint_bf_conf(content) == []

    def test_append_redirect_also_supported(self):
        # `>>` (append) should be detected the same way `>` is.
        bad = (
            'cat << EOF >> /tmp/x.yaml\n'
            '  nope: [a, b,\n'  # unterminated flow sequence
            'EOF\n'
        )
        issues = lint_bf_conf(bad)
        assert any("YAML heredoc" in i for i in issues)

    def test_lints_both_seeded_templates(self):
        # Render each seeded template with a realistic context and confirm
        # the output passes lint. Guards against the templates themselves
        # producing broken YAML / shell.
        from types import SimpleNamespace

        seed_dir = Path(__file__).resolve().parents[2] / "alembic" / "seed_data"
        ctx = RenderContext(
            bfb_hostname="MT-TEST-1",
            dpu_mtu=9216,
            ubuntu_password_hash="$6$rounds=10000$a$b",
            host=HostContext(
                oob_net0_ip="10.0.0.5/24",
                default_gateway="10.0.0.1",
                dns_ip="1.1.1.1,8.8.8.8",
                dns_domains="example.com",
                authorized_keys="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5 bnk-forge",
                vlans=[
                    VlanContext(name="external0", tag=0, uplink="p0", ip="10.10.20.5/24"),
                    VlanContext(
                        name="internal100", tag=100, uplink="p1",
                        ip="10.10.10.5/24", default_gateway="10.10.10.1",
                    ),
                ],
            ),
        )
        for fname in ("bf_template_lag.conf", "bf_template_nolag.conf"):
            path = seed_dir / fname
            if not path.exists():
                pytest.skip(f"seed template {fname} not present")
            tpl = SimpleNamespace(name=fname, content=path.read_text())
            rendered = render_bf_conf(tpl, ctx)
            issues = lint_bf_conf(rendered)
            assert issues == [], f"{fname} lint issues: {issues}"


class TestTmfifoDpuIp:
    """Per-rshim tmfifo IP derivation — multi-DPU hosts must not collide
    on the legacy fixed 192.168.100.2/30."""

    def test_rshim0_default(self):
        assert derive_tmfifo_dpu_ip("rshim0") == "192.168.100.2/30"
        assert derive_tmfifo_dpu_host("rshim0") == "192.168.100.2"

    def test_rshim1_offsets_subnet(self):
        assert derive_tmfifo_dpu_ip("rshim1") == "192.168.101.2/30"
        assert derive_tmfifo_dpu_host("rshim1") == "192.168.101.2"

    def test_rshim2(self):
        assert derive_tmfifo_dpu_ip("rshim2") == "192.168.102.2/30"

    def test_none_falls_back_to_rshim0(self):
        # Pre-probe DPU rows have null rshim_device — must keep producing
        # the historical single-DPU IP.
        assert derive_tmfifo_dpu_ip(None) == "192.168.100.2/30"
        assert derive_tmfifo_dpu_host(None) == "192.168.100.2"

    def test_garbage_falls_back_to_rshim0(self):
        assert derive_tmfifo_dpu_ip("not-an-rshim") == "192.168.100.2/30"

    def test_render_context_includes_tmfifo_ip(self):
        # bf.conf templates reference {{ hostvars[…].tmfifo_dpu_ip }} —
        # the render context must pass it through.
        ctx = RenderContext(
            bfb_hostname="dpu-1", dpu_mtu=9000,
            ubuntu_password_hash="$6$x",
            host=HostContext(tmfifo_dpu_ip="192.168.101.2/30"),
        )
        out = render_bf_conf(
            _tpl("addr={{ hostvars[bfb_hostname].tmfifo_dpu_ip }}"),
            ctx,
        )
        assert out == "addr=192.168.101.2/30"


class TestBfbHostnameDerivation:
    """Multi-DPU hosts must produce a unique hostname per DPU.

    Single-DPU hosts (rshim0, or rshim_device not yet probed and only
    one DPU on the host) keep the historical `<host>-dpu` so existing
    deployments aren't renamed on re-flash.
    """

    @staticmethod
    def _inband(**kw):
        defaults = dict(
            id=42, serial_number=None, access_mode="in-band",
            host_hostname=None, rshim_device=None, pci_address=None,
        )
        defaults.update(kw)
        return _Stub(**defaults)

    def test_serial_wins_over_everything(self):
        d = self._inband(serial_number="MT-AB-1234")
        assert _derive_bfb_hostname(d) == "MT-AB-1234"

    def test_single_dpu_host_keeps_legacy_hostname(self):
        # peer_count=1 → "<host>-dpu" (no suffix). Backwards compatible
        # with existing single-DPU deployments.
        d = self._inband(host_hostname="worker1", rshim_device="rshim0")
        assert _derive_bfb_hostname(d, peer_count=1) == "worker1-dpu"

    def test_multi_dpu_rshim0_is_dpu1(self):
        # On a multi-DPU host every DPU is numbered 1-indexed, so
        # rshim0 becomes dpu1 (not dpu0 — reads more naturally).
        d = self._inband(host_hostname="worker1", rshim_device="rshim0")
        assert _derive_bfb_hostname(d, peer_count=2) == "worker1-dpu1"

    def test_multi_dpu_rshim1_is_dpu2(self):
        d = self._inband(host_hostname="worker1", rshim_device="rshim1")
        assert _derive_bfb_hostname(d, peer_count=2) == "worker1-dpu2"

    def test_multi_dpu_rshim2_is_dpu3(self):
        d = self._inband(host_hostname="worker1", rshim_device="rshim2")
        assert _derive_bfb_hostname(d, peer_count=3) == "worker1-dpu3"

    def test_two_dpus_on_same_host_are_unique(self):
        # The bug we're fixing: both DPUs on worker1 used to render
        # the same "worker1-dpu" hostname.
        a = self._inband(host_hostname="worker1", rshim_device="rshim0",
                         pci_address="0000:00:11")
        b = self._inband(host_hostname="worker1", rshim_device="rshim1",
                         pci_address="0000:00:10")
        assert _derive_bfb_hostname(a, peer_count=2) == "worker1-dpu1"
        assert _derive_bfb_hostname(b, peer_count=2) == "worker1-dpu2"

    def test_pci_fallback_when_rshim_unknown_on_multi_dpu_host(self):
        # No rshim_device yet (pre-probe). Use PCI to disambiguate so a
        # flash without prior Discover still produces a unique hostname.
        d = self._inband(host_hostname="worker1", pci_address="0000:00:10")
        assert (
            _derive_bfb_hostname(d, peer_count=2)
            == "worker1-dpu-0000-00-10"
        )

    def test_pci_fallback_distinguishes_domains(self):
        # Same short BDF, different PCI domains → must be distinct.
        a = self._inband(host_hostname="worker1", pci_address="0000:00:01")
        b = self._inband(host_hostname="worker1", pci_address="0001:00:01")
        assert (
            _derive_bfb_hostname(a, peer_count=2)
            != _derive_bfb_hostname(b, peer_count=2)
        )

    def test_no_host_hostname_falls_through_to_dpu_id(self):
        d = self._inband(id=42, host_hostname=None, rshim_device="rshim0")
        assert _derive_bfb_hostname(d, peer_count=2) == "dpu-42"

    def test_dotted_host_hostname_truncated_to_first_label(self):
        d = self._inband(host_hostname="worker1.lab.example.com",
                         rshim_device="rshim0")
        assert _derive_bfb_hostname(d, peer_count=1) == "worker1-dpu"

    def test_default_peer_count_is_single_dpu(self):
        # No peer_count passed → renderer treats it as single-DPU host.
        d = self._inband(host_hostname="worker1", rshim_device="rshim1")
        assert _derive_bfb_hostname(d) == "worker1-dpu"
