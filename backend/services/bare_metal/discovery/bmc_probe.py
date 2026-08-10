"""BMC/IPMI probes for bare-metal host management interface."""

import logging
from dataclasses import dataclass

from services.bare_metal.ipmi_client import IPMIClient
from services.bare_metal.redfish.client import RedfishClient

logger = logging.getLogger(__name__)


@dataclass
class BmcProbeResult:
    """Structured results from BMC/IPMI probing."""

    redfish_reachable: bool = False
    ipmi_reachable: bool = False
    vendor: str | None = None  # "supermicro", "lenovo", "dell", "hpe"
    bios_attributes: dict | None = None  # Subset of BIOS attributes (NIC-related)
    nic_mode_bios: str | None = None  # NIC mode from BIOS (if Redfish available)
    power_state: str | None = None  # "On", "Off", "PoweringOn", etc.
    system_manufacturer: str | None = None
    system_model: str | None = None
    error: str | None = None


def probe_bmc(
    bmc_ip: str,
    redfish: RedfishClient | None = None,
    ipmi: IPMIClient | None = None,
) -> BmcProbeResult:
    """Run BMC/IPMI probes.

    Args:
        bmc_ip: BMC IP address (for logging)
        redfish: Optional RedfishClient (if credentials provided)
        ipmi: Optional IPMIClient (if credentials provided)
    """
    result = BmcProbeResult()

    # Probe Redfish
    if redfish:
        _probe_redfish(redfish, result)

    # Probe IPMI
    if ipmi:
        _probe_ipmi(ipmi, result)

    return result


def _probe_redfish(redfish: RedfishClient, result: BmcProbeResult) -> None:
    """Probe via Redfish API."""
    try:
        if not redfish.is_reachable():
            return
        result.redfish_reachable = True

        # Vendor detection
        result.vendor = redfish.detect_vendor()

        # System info
        info = redfish.get_system_info()
        if info:
            result.power_state = info.power_state
            result.system_manufacturer = info.manufacturer
            result.system_model = info.model

        # BIOS attributes (subset: NIC-related)
        all_attrs = redfish.get_bios_attributes()
        if all_attrs:
            nic_attrs = {
                k: v
                for k, v in all_attrs.items()
                if any(term in k.lower() for term in ["internal", "cpu", "model", "nic", "mlx", "mellanox"])
            }
            result.bios_attributes = nic_attrs if nic_attrs else None

            # Extract NIC mode if vendor plugin available
            if redfish.vendor_plugin:
                result.nic_mode_bios = redfish.vendor_plugin.parse_nic_mode(all_attrs)
    except Exception as e:
        logger.warning("Redfish probe failed: %s", e)
        result.error = f"Redfish probe error: {e}"


def _probe_ipmi(ipmi: IPMIClient, result: BmcProbeResult) -> None:
    """Probe via IPMI."""
    try:
        power = ipmi.power_status()
        if power.success:
            result.ipmi_reachable = True
            # Parse power state from output like "Chassis Power is on"
            if "on" in power.stdout.lower():
                if not result.power_state:  # Don't overwrite Redfish data
                    result.power_state = "On"
            elif "off" in power.stdout.lower():
                if not result.power_state:
                    result.power_state = "Off"
    except Exception as e:
        logger.warning("IPMI probe failed: %s", e)
        if not result.error:
            result.error = f"IPMI probe error: {e}"
