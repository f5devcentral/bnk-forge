"""
BNK-Forge MCP tools — organized by domain.

Each module registers its tools with the FastMCP server instance.
"""

from .bnk_operations import register as register_bnk_operations
from .cloud_auth import register as register_cloud_auth
from .cluster_management import register as register_cluster_management
from .config_management import register as register_config_management
from .diagnostics_fleet import register as register_diagnostics_fleet
from .helm import register as register_helm
from .iac_operations import register as register_iac_operations
from .system import register as register_system

__all__ = [
    "register_system",
    "register_cluster_management",
    "register_bnk_operations",
    "register_diagnostics_fleet",
    "register_helm",
    "register_config_management",
    "register_iac_operations",
    "register_cloud_auth",
]
