"""
SSH module: bare-metal/reboot-host

Reboots the bare-metal host if required (e.g. after mlxconfig NIC mode change)
and waits for SSH to come back. Skips if reboot is not needed.

This module sits between set-nic-mode and flash-dpu in the pipeline.
After a NIC mode change, a reboot is required for the firmware to apply
the new mode and for the rshim kernel driver to load.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class RebootHostModule(SSHModule):
    name = "Reboot Host"
    path = "bare-metal/reboot-host"
    category = "bare-metal"
    phase = "NIC Configuration"
    description = (
        "Reboot bare-metal host if required (e.g. after NIC mode change) "
        "and wait for SSH to come back"
    )
    version = "1.0.0"

    target = "host"
    estimated_duration = 180
    timeout = 600

    dependencies = ["bare-metal/set-nic-mode"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "host_ip": InputSpec(
            name="host_ip",
            source="host",
            required=True,
            description="Host IP address",
        ),
        "reboot_required": InputSpec(
            name="reboot_required",
            source="module",
            from_module="bare-metal/set-nic-mode",
            from_output="reboot_required",
            required=False,
            default=False,
            description="Whether set-nic-mode requires a reboot",
        ),
        "max_wait_seconds": InputSpec(
            name="max_wait_seconds",
            type="number",
            default=900,
            required=False,
            description=(
                "Wall-clock budget for the host to come back SSH-reachable "
                "after reboot. 900s covers slow boots (NVIDIA MGX/Grace "
                "platforms commonly take ~10 min); regular topology hosts "
                "exit much sooner."
            ),
        ),
        "rshim_source": InputSpec(
            name="rshim_source",
            source="host",
            required=False,
            default="host",
            description="Where rshim is accessible: 'host' or 'bmc' (dual_dpu_obmc)",
        ),
    }

    outputs = {
        "reboot_performed": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=False,
        ),
        "host_uptime": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return True

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        return {}

    @staticmethod
    def _ensure_rshim(session: Any, on_output: Any) -> None:
        """Make sure rshim device is available. Start the service if needed."""
        r = session.execute("ls /dev/rshim*/boot 2>/dev/null | head -1 || echo NO_RSHIM", timeout=10)
        if r.stdout.strip() and r.stdout.strip() != "NO_RSHIM":
            on_output(f"[reboot-host] rshim already available: {r.stdout.strip()}")
            return

        on_output("[reboot-host] rshim not found — attempting to start rshim service...")

        # Try modprobe first (kernel module may not be loaded)
        session.execute("sudo modprobe rshim_pcie 2>/dev/null; sudo modprobe rshim 2>/dev/null", timeout=15)

        # Enable and start the service
        session.execute("sudo systemctl enable rshim 2>/dev/null", timeout=10)
        r = session.execute("sudo systemctl start rshim 2>&1", timeout=30)
        if r.exit_code != 0:
            on_output(f"[reboot-host] WARNING: rshim service start returned exit {r.exit_code}: {r.stdout.strip()[:200]}")

        # Give it a moment to create device nodes
        import time as _time
        _time.sleep(3)

        # Verify
        r = session.execute("ls /dev/rshim*/boot 2>/dev/null | head -1 || echo NO_RSHIM", timeout=10)
        if r.stdout.strip() and r.stdout.strip() != "NO_RSHIM":
            on_output(f"[reboot-host] rshim started successfully: {r.stdout.strip()}")
        else:
            on_output(
                "[reboot-host] WARNING: rshim device still not found after starting service. "
                "flash-dpu may fail — check 'systemctl status rshim' and 'dmesg | grep rshim' on the host."
            )

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Reboot host if required, wait for SSH to come back."""
        t0 = time.monotonic()

        reboot_required = variables.get("reboot_required", False)
        # Handle string "True"/"False" from module outputs
        if isinstance(reboot_required, str):
            reboot_required = reboot_required.lower() in ("true", "1", "yes")

        if not reboot_required:
            on_output("[reboot-host] No reboot required — skipping")
            rshim_source = variables.get("rshim_source", "host")
            if rshim_source != "bmc":
                self._ensure_rshim(session, on_output)
            else:
                on_output("[reboot-host] rshim_source=bmc — skipping host rshim check")
            r = session.execute("uptime -s 2>/dev/null || uptime", timeout=10)
            return {
                "reboot_performed": False,
                "host_uptime": r.stdout.strip() if r.exit_code == 0 else None,
                "_elapsed_seconds": round(time.monotonic() - t0, 1),
            }

        on_output("[reboot-host] Reboot required — initiating host reboot...")

        # Record pre-reboot uptime for verification
        pre_r = session.execute("cat /proc/uptime | cut -d' ' -f1", timeout=10)
        pre_uptime = float(pre_r.stdout.strip()) if pre_r.exit_code == 0 else None
        if pre_uptime:
            on_output(f"[reboot-host] Pre-reboot uptime: {pre_uptime:.0f}s")

        # Issue reboot — use nohup + sleep to give SSH time to return the command
        # before the connection drops. The command will exit with an error or
        # the connection will drop — both are expected.
        on_output("[reboot-host] Sending reboot command...")
        try:
            session.execute("sudo nohup bash -c 'sleep 2 && reboot' &>/dev/null &", timeout=10)
        except Exception:
            pass  # Connection drop during reboot is expected

        # Wait for SSH to go down (host should become unreachable)
        on_output("[reboot-host] Waiting for host to go down...")
        time.sleep(10)  # Give it time to start shutting down

        # Now poll until SSH comes back. Wall-clock deadline, NOT attempt
        # count — earlier attempt-based loops actually allowed ~2x the
        # advertised max_wait because each SSH connect timeout added to
        # the per-iteration time. Time-based loop keeps the log honest.
        max_wait = int(variables.get("max_wait_seconds") or 900)
        poll_interval = 10

        from services.bare_metal.ssh_session import SSHSession

        on_output(
            f"[reboot-host] Polling for SSH (wall-clock max {max_wait}s, "
            f"interval {poll_interval}s)..."
        )

        deadline = time.monotonic() + max_wait
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            elapsed = round(time.monotonic() - t0, 1)
            try:
                new_session = SSHSession(
                    host=session.host,
                    port=session.port,
                    username=session.username,
                    password=session.password,
                    private_key_content=session.private_key_content,
                    jumphost_chain=session.jumphost_chain,
                    connect_timeout=10,
                )
                r = new_session.execute("cat /proc/uptime | cut -d' ' -f1", timeout=10)
                if r.exit_code == 0:
                    post_uptime = float(r.stdout.strip())
                    # Verify the uptime is lower than before (confirms actual reboot)
                    if pre_uptime is None or post_uptime < pre_uptime:
                        on_output(
                            f"[reboot-host] Host is back! "
                            f"(attempt {attempt}, {elapsed}s, uptime {post_uptime:.0f}s)"
                        )
                        uptime_r = new_session.execute("uptime -s 2>/dev/null || uptime", timeout=10)

                        # Ensure rshim is running post-reboot (needed for flash-dpu)
                        rshim_source = variables.get("rshim_source", "host")
                        if rshim_source != "bmc":
                            self._ensure_rshim(new_session, on_output)
                        else:
                            on_output("[reboot-host] rshim_source=bmc — skipping host rshim check")

                        return {
                            "reboot_performed": True,
                            "host_uptime": uptime_r.stdout.strip() if uptime_r.exit_code == 0 else None,
                            "_elapsed_seconds": round(time.monotonic() - t0, 1),
                        }
                    else:
                        on_output(
                            f"[reboot-host] SSH responded but uptime ({post_uptime:.0f}s) >= "
                            f"pre-reboot ({pre_uptime:.0f}s) — reboot may not have happened yet"
                        )
            except Exception as exc:
                on_output(
                    f"[reboot-host] Attempt {attempt} at {elapsed}s — "
                    f"not ready yet ({type(exc).__name__}: {str(exc)[:80]})"
                )

            # Only sleep if we still have budget for another iteration
            if time.monotonic() + poll_interval < deadline:
                time.sleep(poll_interval)
            else:
                break

        raise RuntimeError(
            f"Host did not become SSH-reachable within {max_wait}s after reboot "
            f"({attempt} attempts). Check host console for boot issues."
        )
