"""Redfish BMC client with vendor plugin system."""

from services.bare_metal.redfish.client import RedfishClient
from services.bare_metal.redfish.vendor_base import VendorPlugin

__all__ = ["RedfishClient", "VendorPlugin"]
