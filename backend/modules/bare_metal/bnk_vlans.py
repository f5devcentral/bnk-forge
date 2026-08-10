"""
SSH port of catalog module 24 — bnk/bnk-vlans (bare-metal/bnk-vlans), authored fresh.

Creates the F5SPKVlan CRs that tell TMM which self-IPs to configure on its
data-plane interfaces (1.1 = external, 1.2 = internal). DPU case (via d019
_transform_bnk_vlans): self-IPs from the Dpu record, subnet CIDRs + MTU from
ProjectDpuSettings, namespace forced to f5-bnk.

No catalog pack exists for this module (tofu-only upstream), so it is authored
fresh to match the tofu F5SPKVlan template; parity is structural.

Parity source: bnk-forge-modules bnk/bnk-vlans/main.tf (F5SPKVlan template).
"""

from __future__ import annotations

from typing import Any

from modules.bare_metal.bnk_ssh_base import BnkSSHModule
from modules.base import InputSpec, OutputSpec


def _prefixlen(cidrs: list[str] | None, fallback: str) -> int:
    cidr = (cidrs or [fallback])[0]
    try:
        return int(str(cidr).split("/")[1])
    except (IndexError, ValueError):
        return 24


def _auto_selfip(cidrs: list[str] | None, fallback: str) -> str:
    """Auto-derive .240 from the first subnet network (matches the tofu local)."""
    cidr = (cidrs or [fallback])[0]
    network = str(cidr).split("/")[0]
    octets = network.split(".")
    return f"{octets[0]}.{octets[1]}.{octets[2]}.240"


class BnkVlansSSHModule(BnkSSHModule):
    name = "BNK VLANs [SSH]"
    path = "bare-metal/bnk-vlans"
    description = "Create F5SPKVlan CRs (TMM self-IPs) over SSH"
    version = "1.0.0"
    estimated_duration = 60
    timeout = 300

    dependencies = ["bare-metal/bnk-cneinstance"]

    namespace_var = "namespace"
    default_namespace = "f5-operator"

    inputs = {
        "bare_metal_host_id": InputSpec(name="bare_metal_host_id", source="host", required=True),
        "namespace": InputSpec(name="namespace", source="profile", default="f5-operator"),
        "external_self_ips": InputSpec(name="external_self_ips", type="list", source="profile", required=False, default=None),
        "internal_self_ips": InputSpec(name="internal_self_ips", type="list", source="profile", required=False, default=None),
        "external_subnet_cidrs": InputSpec(name="external_subnet_cidrs", type="list", source="profile", default=None),
        "internal_subnet_cidrs": InputSpec(name="internal_subnet_cidrs", type="list", source="profile", default=None),
        "mtu": InputSpec(name="mtu", type="number", source="profile", default=9000),
        "auto_lasthop": InputSpec(name="auto_lasthop", source="profile", required=False, default=""),
    }

    outputs = {
        "vlans_ready": OutputSpec(resource_kind="", resource_name="", static_value=True),
    }

    def _vlan(
        self, name: str, ns: str, interface: str, ips: list[str], prefixlen: int,
        mtu: Any, internal: bool, auto_lasthop: str,
    ) -> dict[str, Any]:
        spec: dict[str, Any] = {"name": name}
        if internal:
            spec["internal"] = True
        spec["interfaces"] = [interface]
        spec["mtu"] = mtu
        # F5SPKVlan.spec.selfip_v4s requires bare IPv4 addresses (the CRD regex
        # rejects CIDR). DPU-assigned self-IPs may arrive as "10.10.20.100/24";
        # the mask is carried separately in prefixlen_v4, so strip any suffix here.
        spec["selfip_v4s"] = [str(ip).split("/", 1)[0] for ip in ips]
        spec["prefixlen_v4"] = prefixlen
        if auto_lasthop:
            spec["auto_lasthop"] = auto_lasthop
        return {
            "apiVersion": "k8s.f5net.com/v1",
            "kind": "F5SPKVlan",
            "metadata": {"name": name, "namespace": ns},
            "spec": spec,
        }

    def render_manifests(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        ns = self.resolve_namespace(v)
        mtu = v.get("mtu", 9000)
        auto_lasthop = str(v.get("auto_lasthop", "") or "")

        ext_cidrs = v.get("external_subnet_cidrs") or ["10.0.10.0/24"]
        int_cidrs = v.get("internal_subnet_cidrs") or ["10.0.20.0/24"]

        ext_ips = v.get("external_self_ips") or [_auto_selfip(ext_cidrs, "10.0.10.0/24")]
        int_ips = v.get("internal_self_ips") or [_auto_selfip(int_cidrs, "10.0.20.0/24")]

        return [
            self._vlan("external", ns, "1.1", list(ext_ips), _prefixlen(ext_cidrs, "10.0.10.0/24"),
                       mtu, internal=False, auto_lasthop=auto_lasthop),
            self._vlan("internal", ns, "1.2", list(int_ips), _prefixlen(int_cidrs, "10.0.20.0/24"),
                       mtu, internal=True, auto_lasthop=auto_lasthop),
        ]

    def get_required_crds(self, v: dict[str, Any]) -> list[str]:
        # FLO installs the F5SPKVlan CRD asynchronously after bnk-cneinstance
        # reconciles; gate the apply on it so we don't race with "no matches for
        # kind F5SPKVlan" on a clean from-scratch deploy.
        return ["f5-spk-vlans.k8s.f5net.com"]

    def get_required_deployments(self, v: dict[str, Any]) -> list[dict[str, str]]:
        # The F5SPKVlan apply is gated by the f5validate.f5net.com validating
        # webhook, served by the f5-cne-controller Pod on :3340. On a cold deploy
        # that Pod becomes Ready ~2-3 min AFTER the CRD is Established, so without
        # this gate the apply fails with "failed calling webhook ... connection
        # refused". The controller runs in the same namespace as the CRs.
        return [{"name": "f5-cne-controller", "namespace": self.resolve_namespace(v)}]

    def get_readiness_waits(self, v: dict[str, Any]) -> list[dict[str, Any]]:
        ns = self.resolve_namespace(v)
        # 600s (not 180): on a clean from-scratch deploy TMM is still starting when
        # these CRs are applied, so it programs the self-IPs several minutes later
        # (measured ~5-7 min cold). A warm cluster programs almost instantly.
        return [
            {"kind": "f5-spk-vlans.k8s.f5net.com", "name": "external", "namespace": ns,
             "condition": "condition=Programmed", "timeout": 600},
            {"kind": "f5-spk-vlans.k8s.f5net.com", "name": "internal", "namespace": ns,
             "condition": "condition=Programmed", "timeout": 600},
        ]

    def collect_outputs(self, session: Any, v: dict[str, Any]) -> dict[str, Any]:
        return {"vlans_ready": True}
