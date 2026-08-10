"""
SSH module: bare-metal/validate-dpu-ready

Validates DPU is correctly configured and ready for BNK installation.
Runs 9 checks (5 critical, 4 warning) across host and DPU.

Uses the Python execute() path. Target is "host" — engine gives a host
session. DPU checks use a chained SSHSession through the host.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class ValidateDPUReadyModule(SSHModule):
    name = "Validate DPU Ready"
    path = "bare-metal/validate-dpu-ready"
    category = "bare-metal"
    phase = "DPU Validation"
    description = "Validate DPU is configured and ready for BNK installation"
    version = "1.0.0"

    target = "host"
    estimated_duration = 30
    timeout = 120

    dependencies = ["bare-metal/install-dpu-prereqs"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "dpu_ip": InputSpec(
            name="dpu_ip",
            source="module",
            from_module="bare-metal/flash-dpu",
            from_output="dpu_ip",
            required=False,
            default="192.168.100.2",
            description="DPU IP address",
        ),
        "hugepages_minimum": InputSpec(
            name="hugepages_minimum",
            source="profile",
            default="8192",
            required=False,
            description="Minimum hugepages required (2M pages)",
        ),
    }

    outputs = {
        "dpu_validated": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "validation_summary": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Validate DPU readiness with 9 checks across host and DPU."""
        t0 = time.monotonic()
        dpu_ip = variables.get("dpu_ip", "192.168.100.2")
        hugepages_min = int(variables.get("hugepages_minimum", "8192"))
        results: dict[str, str] = {}

        CRITICAL_CHECKS = ["hugepages_cmdline", "hugepages_allocated", "iommu_passthrough", "kubelet_active", "containerd_active"]

        # ── Host-side checks ──
        on_output("[validate-dpu] Check 1/9: iptables NAT MASQUERADE...")
        r = session.execute("sudo iptables -t nat -L POSTROUTING -n 2>/dev/null | grep -q MASQUERADE", timeout=15)
        results["iptables_nat"] = "pass" if r.exit_code == 0 else "fail"
        on_output(f"[validate-dpu]   iptables_nat: {results['iptables_nat']}")

        # ── Create DPU session via jumphost chain ──
        on_output(f"[validate-dpu] Connecting to DPU at {dpu_ip}...")
        try:
            from services.bare_metal.ssh_session import SSHSession

            # Build jumphost chain: existing jumphosts first, then the host as final hop
            # (matches wait_dpu_ready.py pattern — chain order matters for multi-hop SSH)
            host_hop: dict[str, Any] = {
                "host": session.host,
                "port": session.port,
                "username": session.username,
                "password": session.password,
                "private_key_content": session.private_key_content,
            }
            jumphost_chain = list(session.jumphost_chain or []) + [host_hop]

            dpu_session = SSHSession(
                host=dpu_ip,
                port=22,
                username=variables.get("dpu_username", "ubuntu"),
                password=variables.get("dpu_password"),
                private_key_content=variables.get("dpu_private_key_content"),
                jumphost_chain=jumphost_chain,
            )
        except Exception as e:
            on_output(f"[validate-dpu] ERROR: Cannot connect to DPU at {dpu_ip}: {e}")
            # All DPU checks fail
            for check in ["resolv_conf", "internet", "f5_registry", "hugepages_cmdline",
                          "hugepages_allocated", "iommu_passthrough", "kubelet_active", "containerd_active"]:
                results[check] = "fail"
            return self._build_result(results, CRITICAL_CHECKS, t0, on_output)

        # ── DPU-side checks ──

        # Check 2: resolv.conf
        on_output("[validate-dpu] Check 2/9: DNS resolv.conf...")
        r = dpu_session.execute("test -f /etc/resolv.conf", timeout=10)
        results["resolv_conf"] = "pass" if r.exit_code == 0 else "fail"
        on_output(f"[validate-dpu]   resolv_conf: {results['resolv_conf']}")

        # Check 3: Internet connectivity
        on_output("[validate-dpu] Check 3/9: Internet connectivity...")
        r = dpu_session.execute("ping -c1 -W5 8.8.8.8", timeout=15)
        results["internet"] = "pass" if r.exit_code == 0 else "fail"
        on_output(f"[validate-dpu]   internet: {results['internet']}")

        # Check 4: F5 registry
        on_output("[validate-dpu] Check 4/9: F5 registry reachability...")
        r = dpu_session.execute("curl -I --connect-timeout 5 https://repo.f5.com 2>/dev/null", timeout=15)
        results["f5_registry"] = "pass" if r.exit_code == 0 else "fail"
        on_output(f"[validate-dpu]   f5_registry: {results['f5_registry']}")

        # Check 5: Hugepages in kernel cmdline
        on_output("[validate-dpu] Check 5/9: Hugepages kernel cmdline...")
        r = dpu_session.execute("grep -q hugepages= /proc/cmdline", timeout=10)
        results["hugepages_cmdline"] = "pass" if r.exit_code == 0 else "fail"
        on_output(f"[validate-dpu]   hugepages_cmdline: {results['hugepages_cmdline']}")

        # Check 6: Hugepages allocated
        on_output("[validate-dpu] Check 6/9: Hugepages allocated...")
        r = dpu_session.execute("awk '/HugePages_Total/{print $2}' /proc/meminfo", timeout=10)
        try:
            hp_total = int(r.stdout.strip())
            results["hugepages_allocated"] = "pass" if hp_total >= hugepages_min else "fail"
            on_output(f"[validate-dpu]   hugepages_allocated: {results['hugepages_allocated']} ({hp_total} pages, min {hugepages_min})")
        except (ValueError, AttributeError):
            results["hugepages_allocated"] = "fail"
            on_output(f"[validate-dpu]   hugepages_allocated: fail (could not parse: {r.stdout.strip()[:50]})")

        # Check 7: IOMMU passthrough
        on_output("[validate-dpu] Check 7/9: IOMMU passthrough...")
        r = dpu_session.execute("grep -q iommu.passthrough=1 /proc/cmdline", timeout=10)
        results["iommu_passthrough"] = "pass" if r.exit_code == 0 else "fail"
        on_output(f"[validate-dpu]   iommu_passthrough: {results['iommu_passthrough']}")

        # Check 8: kubelet active (exact match — "inactive"/"activating" must NOT pass)
        on_output("[validate-dpu] Check 8/9: kubelet service...")
        r = dpu_session.execute("systemctl is-active kubelet", timeout=10)
        results["kubelet_active"] = "pass" if r.exit_code == 0 else "fail"
        on_output(f"[validate-dpu]   kubelet_active: {results['kubelet_active']} ({r.stdout.strip()})")

        # Check 9: containerd active (exact match — exit code 0 means "active")
        on_output("[validate-dpu] Check 9/9: containerd service...")
        r = dpu_session.execute("systemctl is-active containerd", timeout=10)
        results["containerd_active"] = "pass" if r.exit_code == 0 else "fail"
        on_output(f"[validate-dpu]   containerd_active: {results['containerd_active']} ({r.stdout.strip()})")

        return self._build_result(results, CRITICAL_CHECKS, t0, on_output)

    def _build_result(
        self,
        results: dict[str, str],
        critical_checks: list[str],
        t0: float,
        on_output: Any,
    ) -> dict[str, Any]:
        """Build the final result dict from check results."""
        critical_passed = all(results.get(c) == "pass" for c in critical_checks)
        dpu_validated = critical_passed

        summary_lines = []
        for check, result in results.items():
            severity = "CRITICAL" if check in critical_checks else "WARNING"
            status = "PASS" if result == "pass" else "FAIL"
            summary_lines.append(f"  [{severity}] {check}: {status}")

        total = time.monotonic() - t0
        on_output(f"\n[validate-dpu] {'=' * 40}")
        on_output(f"[validate-dpu] Validation {'PASSED' if dpu_validated else 'FAILED'} ({total:.1f}s)")
        for line in summary_lines:
            on_output(f"[validate-dpu] {line}")
        on_output(f"[validate-dpu] {'=' * 40}")

        return {
            "dpu_validated": dpu_validated,
            "validation_summary": "\n".join(summary_lines),
            "execution_duration_seconds": round(total, 1),
        }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return []  # Always run validation

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return True  # Always apply

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        return []  # Stubbed — execute() path

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        return {}  # Stubbed — execute() path

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        return []  # Validation IS this module
