"""IPMI client — subprocess wrapper for ipmitool."""

import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IPMIResult:
    """Result of an IPMI command."""

    exit_code: int
    stdout: str
    stderr: str
    success: bool


class IPMIClient:
    """Subprocess wrapper for ipmitool over LAN.

    Used for power management on hosts with IPMI but no Redfish.
    All commands use 'ipmitool -I lanplus' for IPMI v2.0.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        interface: str = "lanplus",
        timeout: int = 30,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.interface = interface
        self.timeout = timeout

    def _build_base_args(self) -> list[str]:
        """Build base ipmitool arguments."""
        return [
            "ipmitool",
            "-I", self.interface,
            "-H", self.host,
            "-U", self.username,
            "-P", self.password,
        ]

    def _execute(self, *args: str) -> IPMIResult:
        """Execute an ipmitool command."""
        cmd = self._build_base_args() + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return IPMIResult(
                exit_code=result.returncode,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                success=result.returncode == 0,
            )
        except subprocess.TimeoutExpired:
            return IPMIResult(
                exit_code=-1,
                stdout="",
                stderr=f"IPMI command timed out after {self.timeout}s",
                success=False,
            )
        except FileNotFoundError:
            return IPMIResult(
                exit_code=-1,
                stdout="",
                stderr="ipmitool not found — install with: apt-get install ipmitool",
                success=False,
            )

    # --- Connectivity ---

    def is_reachable(self) -> bool:
        """Check IPMI connectivity."""
        result = self.power_status()
        return result.success

    # --- Power Management ---

    def power_status(self) -> IPMIResult:
        """Get chassis power status."""
        return self._execute("chassis", "power", "status")

    def power_cycle(self) -> IPMIResult:
        """Cold boot: power off then on."""
        return self._execute("chassis", "power", "cycle")

    def power_on(self) -> IPMIResult:
        """Power on the chassis."""
        return self._execute("chassis", "power", "on")

    def power_off(self) -> IPMIResult:
        """Power off the chassis (hard)."""
        return self._execute("chassis", "power", "off")

    def power_soft(self) -> IPMIResult:
        """Graceful shutdown via ACPI."""
        return self._execute("chassis", "power", "soft")

    def power_reset(self) -> IPMIResult:
        """Hard reset (immediate)."""
        return self._execute("chassis", "power", "reset")

    # --- Sensors (bonus, useful for diagnostics) ---

    def get_sensor_list(self) -> IPMIResult:
        """Get sensor list (temperatures, voltages, fan speeds)."""
        return self._execute("sensor", "list")
