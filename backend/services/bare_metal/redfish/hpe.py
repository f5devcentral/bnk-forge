"""HPE iLO BMC vendor plugin."""

from services.bare_metal.redfish.vendor_base import VendorPlugin


class HpePlugin(VendorPlugin):
    """HPE iLO-specific Redfish behavior."""

    @property
    def vendor_name(self) -> str:
        return "hpe"

    def get_nic_mode_attribute_path(self) -> str:
        return "/redfish/v1/Systems/1/Bios/Settings"

    def parse_nic_mode(self, bios_attributes: dict) -> str | None:
        for key in bios_attributes:
            if "InternalCpuModel" in key or "INTERNAL_CPU_MODEL" in key:
                val = str(bios_attributes[key])
                if "1" in val or "Embedded" in val.lower():
                    return "supernic"
                if "0" in val or "Separated" in val.lower():
                    return "dpu"
        return None

    def build_nic_mode_payload(self, target_mode: str) -> dict:
        value = "1" if target_mode == "supernic" else "0"
        return {"Attributes": {"InternalCpuModel": value}}

    def detect_vendor(self, service_root: dict) -> bool:
        oem = service_root.get("Oem", {})
        return "Hpe" in oem or "HPE" in str(oem)
