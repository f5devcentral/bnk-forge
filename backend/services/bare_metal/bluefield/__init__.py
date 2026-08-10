"""BlueField DPU Redfish probe and inventory types.

This package targets the DPU's *own* embedded Redfish service (the OpenBMC
running on the BlueField-3 SoC), not the host server's BMC. For host-side
BMC vendor plugins, see `backend/services/bare_metal/redfish/`.
"""

from services.bare_metal.bluefield.inventory import (
    DpuBiosSettings,
    DpuFirmware,
    DpuInterface,
    DpuInventory,
    DpuManagerInfo,
)
from services.bare_metal.bluefield.probe import BluefieldRedfishProbe, ProbeError

__all__ = [
    "BluefieldRedfishProbe",
    "DpuBiosSettings",
    "DpuFirmware",
    "DpuInterface",
    "DpuInventory",
    "DpuManagerInfo",
    "ProbeError",
]
