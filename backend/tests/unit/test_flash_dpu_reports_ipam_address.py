"""Regression tests for #118 — flash-dpu must report the address it baked.

`flash_dpu` baked the *allocated* tmfifo address into bf.conf (via
`derive_tmfifo_dpu_ip`, which honours the cluster-scoped IPAM /30) but reported a
module *constant* — `DPU_IP = "192.168.100.2"` — as its `dpu_ip` output. Three
modules consume that output as their SSH target, so once ADR-424 IPAM handed a
DPU anything other than the pool's first /30, `bare-metal/wait-dpu-ready` polled
an address nothing listens on and failed after `max_wait_seconds` (900s) even
though the flash succeeded and the DPU was up.

Unlike the transient W1/W2/W3 mismatches in #115 this never converged: the wrong
address was a constant, not stale state, so a retry failed identically.
"""

from __future__ import annotations

import pytest

from modules.bare_metal.flash_dpu import DPU_IP


@pytest.mark.unit
class TestOutputSpecIsNotStatic:
    def test_dpu_ip_output_is_not_a_static_constant(self):
        """A static value cannot reflect per-DPU IPAM allocation."""
        from modules.bare_metal.flash_dpu import FlashDPUModule

        spec = FlashDPUModule.outputs["dpu_ip"]
        assert spec.static_value is None, (
            "dpu_ip is pinned to a constant again — every DPU past the pool's "
            "first /30 will be probed at the wrong address (#118)"
        )


@pytest.mark.unit
class TestReportedAddressFollowsBfConf:
    """The reported address must come from the same context that rendered bf.conf."""

    def test_prefers_the_allocated_ipam_address(self):
        variables = {"dpu_tmfifo_ip": "192.168.100.6"}
        reported = variables.get("dpu_tmfifo_ip") or DPU_IP
        assert reported == "192.168.100.6"

    def test_falls_back_to_the_constant_without_ipam(self):
        """No bf.conf template configured → no IPAM address; the formula still applies."""
        variables: dict = {}
        reported = variables.get("dpu_tmfifo_ip") or DPU_IP
        assert reported == DPU_IP


@pytest.mark.unit
class TestVariableAssemblerExposesBakedAddress:
    def test_cidr_is_stripped_for_ssh_targets(self):
        """bf.conf carries a /30; an SSH target must not."""
        from services.bf_conf_renderer import derive_tmfifo_dpu_ip

        class _Dpu:
            dpu_tmfifo_ip = "192.168.100.6"
            kubernetes_cluster_id = 3
            rshim_device = "rshim0"

        cidr = derive_tmfifo_dpu_ip("rshim0", dpu=_Dpu())
        assert cidr == "192.168.100.6/30"
        # This is the transform variable_assembler applies before publishing it.
        assert cidr.split("/")[0] == "192.168.100.6"

    def test_non_member_dpu_falls_back_to_the_rshim_formula(self):
        """A DPU with no cluster must not inherit a stale allocation."""
        from services.bf_conf_renderer import derive_tmfifo_dpu_ip

        class _Orphan:
            dpu_tmfifo_ip = "192.168.100.6"
            kubernetes_cluster_id = None
            rshim_device = "rshim0"

        assert derive_tmfifo_dpu_ip("rshim0", dpu=_Orphan()) == "192.168.100.2/30"


@pytest.mark.unit
class TestConsumersPreferTheBakedAddress:
    """wait/validate/setup build their own SSH target, so each needs the fallback."""

    @pytest.mark.parametrize(
        "module_path",
        [
            "backend/modules/bare_metal/wait_dpu_ready.py",
            "backend/modules/bare_metal/validate_dpu_ready.py",
            "backend/modules/bare_metal/setup_dpu_networking.py",
        ],
    )
    def test_consumer_falls_back_to_dpu_tmfifo_ip_before_the_literal(self, module_path):
        from pathlib import Path

        # tests/unit/<file> -> backend/ ; module_path is repo-relative.
        backend_root = Path(__file__).resolve().parents[2]
        source = (backend_root / module_path.removeprefix("backend/")).read_text()
        assert 'variables.get("dpu_tmfifo_ip")' in source, (
            f"{module_path} falls straight through to the 192.168.100.2 literal; "
            "a re-run with no flash-dpu output will probe the wrong DPU (#118)"
        )
