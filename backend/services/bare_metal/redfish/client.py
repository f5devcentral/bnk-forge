"""Redfish HTTP client for BMC operations."""

import logging
from dataclasses import dataclass

import requests
from requests.auth import HTTPBasicAuth

from services.bare_metal.redfish.vendor_base import VendorPlugin

logger = logging.getLogger(__name__)

# Default timeout for Redfish requests (BMC can be slow)
DEFAULT_TIMEOUT = 30


@dataclass
class RedfishSystemInfo:
    """Key system information from Redfish."""

    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    power_state: str | None = None  # "On", "Off", "PoweringOn", "PoweringOff"
    bios_version: str | None = None


class RedfishClient:
    """HTTP client for Redfish BMC API.

    Supports:
    - Service root discovery
    - System information
    - BIOS attribute read/write
    - Power management (graceful shutdown, power on, force restart)
    - Vendor detection and vendor-specific behavior
    """

    def __init__(
        self,
        bmc_ip: str,
        username: str,
        password: str,
        *,
        verify_ssl: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
        vendor_plugin: VendorPlugin | None = None,
    ):
        self.base_url = f"https://{bmc_ip}"
        self.auth = HTTPBasicAuth(username, password)
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.vendor_plugin = vendor_plugin
        self._session = requests.Session()
        self._session.auth = self.auth
        self._session.verify = self.verify_ssl
        self._session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _url(self, path: str) -> str:
        """Build full URL from Redfish path."""
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path}"

    def _get(self, path: str) -> dict:
        """GET request to Redfish endpoint."""
        resp = self._session.get(self._url(path), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, payload: dict) -> dict:
        """PATCH request to Redfish endpoint."""
        resp = self._session.patch(self._url(path), json=payload, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _post(self, path: str, payload: dict | None = None) -> dict:
        """POST request to Redfish endpoint (actions)."""
        resp = self._session.post(self._url(path), json=payload or {}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    # --- Discovery ---

    def get_service_root(self) -> dict:
        """Get Redfish service root (/redfish/v1)."""
        return self._get("/redfish/v1")

    def is_reachable(self) -> bool:
        """Check if Redfish API is reachable."""
        try:
            self.get_service_root()
            return True
        except Exception:
            return False

    def detect_vendor(self) -> str | None:
        """Auto-detect BMC vendor from service root.

        Returns vendor name string or None.
        """
        try:
            root = self.get_service_root()
            # Check Oem field for vendor hints
            oem = root.get("Oem", {})
            if "Supermicro" in oem:
                return "supermicro"

            # Check system manufacturer
            systems = self.get_system_info()
            if systems and systems.manufacturer:
                mfr = systems.manufacturer.lower()
                if "supermicro" in mfr:
                    return "supermicro"
                if "lenovo" in mfr:
                    return "lenovo"
                if "dell" in mfr:
                    return "dell"
                if "hpe" in mfr or "hewlett" in mfr:
                    return "hpe"
            return None
        except Exception as e:
            logger.warning("Vendor detection failed: %s", e)
            return None

    # --- System Info ---

    def get_system_info(self) -> RedfishSystemInfo | None:
        """Get system information from first system in collection."""
        try:
            systems = self._get("/redfish/v1/Systems")
            members = systems.get("Members", [])
            if not members:
                return None

            system_path = members[0].get("@odata.id")
            if not system_path:
                return None

            system = self._get(system_path)
            return RedfishSystemInfo(
                manufacturer=system.get("Manufacturer"),
                model=system.get("Model"),
                serial_number=system.get("SerialNumber"),
                power_state=system.get("PowerState"),
                bios_version=system.get("BiosVersion"),
            )
        except Exception as e:
            logger.warning("Failed to get system info: %s", e)
            return None

    def get_power_state(self) -> str | None:
        """Get current power state."""
        info = self.get_system_info()
        return info.power_state if info else None

    # --- BIOS Attributes ---

    def get_bios_attributes(self) -> dict:
        """Get all BIOS attributes."""
        try:
            systems = self._get("/redfish/v1/Systems")
            members = systems.get("Members", [])
            if not members:
                return {}

            system_path = members[0].get("@odata.id", "")
            bios = self._get(f"{system_path}/Bios")
            return bios.get("Attributes", {})
        except Exception as e:
            logger.warning("Failed to get BIOS attributes: %s", e)
            return {}

    def set_bios_attribute(self, attribute_name: str, value: str) -> bool:
        """Set a BIOS attribute (requires reboot to take effect).

        Returns True if the setting was accepted.
        """
        try:
            systems = self._get("/redfish/v1/Systems")
            members = systems.get("Members", [])
            if not members:
                return False

            system_path = members[0].get("@odata.id", "")
            settings_path = f"{system_path}/Bios/Settings"

            payload = {"Attributes": {attribute_name: value}}
            self._patch(settings_path, payload)
            logger.info("Set BIOS attribute %s=%s (pending reboot)", attribute_name, value)
            return True
        except Exception as e:
            logger.error("Failed to set BIOS attribute %s: %s", attribute_name, e)
            return False

    # --- Power Management ---

    def _get_reset_action_path(self) -> str | None:
        """Get the ComputerSystem.Reset action target path."""
        try:
            systems = self._get("/redfish/v1/Systems")
            members = systems.get("Members", [])
            if not members:
                return None

            system_path = members[0].get("@odata.id", "")
            system = self._get(system_path)
            actions = system.get("Actions", {})
            reset = actions.get("#ComputerSystem.Reset", {})
            return reset.get("target")
        except Exception:
            return None

    def graceful_shutdown(self) -> bool:
        """Initiate graceful shutdown."""
        return self._power_action("GracefulShutdown")

    def power_on(self) -> bool:
        """Power on the system."""
        return self._power_action("On")

    def force_restart(self) -> bool:
        """Force restart (immediate, ungraceful)."""
        return self._power_action("ForceRestart")

    def cold_boot(self) -> bool:
        """Perform cold boot: GracefulShutdown → wait → On.

        Note: This is a single API call for the shutdown part.
        The caller must poll power state and send On when Off.
        Use the orchestrator for the full sequence.
        """
        return self.graceful_shutdown()

    def _power_action(self, reset_type: str) -> bool:
        """Execute a power action via Redfish Reset."""
        target = self._get_reset_action_path()
        if not target:
            logger.error("Cannot find Reset action path")
            return False

        try:
            self._post(target, {"ResetType": reset_type})
            logger.info("Power action %s executed", reset_type)
            return True
        except Exception as e:
            logger.error("Power action %s failed: %s", reset_type, e)
            return False

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()
