"""Abstract base class for vendor-specific Redfish behavior."""

from abc import ABC, abstractmethod


class VendorPlugin(ABC):
    """Vendor-specific Redfish behavior.

    Each BMC vendor (Supermicro, Lenovo, Dell, HPE) has quirks in how
    they expose BIOS attributes and handle power management. This ABC
    defines the interface that vendor plugins must implement.
    """

    @property
    @abstractmethod
    def vendor_name(self) -> str:
        """Human-readable vendor name."""
        ...

    @abstractmethod
    def get_nic_mode_attribute_path(self) -> str:
        """Return the Redfish path to the NIC mode BIOS attribute.

        This is vendor-specific. Some vendors use BIOS attributes,
        others use OEM extensions.
        """
        ...

    @abstractmethod
    def parse_nic_mode(self, bios_attributes: dict) -> str | None:
        """Extract the NIC mode value from BIOS attributes dict.

        Returns "supernic", "dpu", or None if not found.
        """
        ...

    @abstractmethod
    def build_nic_mode_payload(self, target_mode: str) -> dict:
        """Build the PATCH payload to change NIC mode.

        Args:
            target_mode: "supernic" or "dpu"

        Returns:
            Dict suitable for PATCH to the BIOS settings endpoint.
        """
        ...

    def detect_vendor(self, service_root: dict) -> bool:
        """Check if this plugin matches the given Redfish service root.

        Override in subclasses to check Oem fields, product name, etc.
        """
        return False
