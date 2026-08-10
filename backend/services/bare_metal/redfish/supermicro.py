"""Supermicro BMC vendor plugin."""

from services.bare_metal.redfish.vendor_base import VendorPlugin


class SupermicroPlugin(VendorPlugin):
    """Supermicro-specific Redfish behavior."""

    @property
    def vendor_name(self) -> str:
        return "supermicro"

    def get_nic_mode_attribute_path(self) -> str:
        # Supermicro exposes NIC mode via standard BIOS attributes
        # The attribute is actually from the Mellanox firmware, exposed through BIOS
        return "/redfish/v1/Systems/1/Bios/Settings"

    def parse_nic_mode(self, bios_attributes: dict) -> str | None:
        # Look for Mellanox NIC mode attribute
        # Attribute name varies but common pattern:
        for key in bios_attributes:
            if "InternalCpuModel" in key or "INTERNAL_CPU_MODEL" in key:
                val = str(bios_attributes[key])
                if "1" in val or "Embedded" in val.lower():
                    return "supernic"
                if "0" in val or "Separated" in val.lower():
                    return "dpu"
        return None

    def build_nic_mode_payload(self, target_mode: str) -> dict:
        # Map to the BIOS attribute value
        value = "1" if target_mode == "supernic" else "0"
        return {"Attributes": {"InternalCpuModel": value}}

    def detect_vendor(self, service_root: dict) -> bool:
        oem = service_root.get("Oem", {})
        return "Supermicro" in oem
