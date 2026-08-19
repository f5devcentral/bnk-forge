"""
Python module registry — bare-metal SSH modules only.

K8s/BNK/app modules are sourced from the git catalog (see
services/module_catalog_service.py). Bare-metal SSH modules remain
Python classes until they are catalog-ized in a follow-up.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules.base import BaseModule

logger = logging.getLogger(__name__)

_MODULE_REGISTRY: dict[str, "BaseModule"] = {}
_initialized = False


def get_module_registry() -> dict[str, "BaseModule"]:
    """Get the module registry, initializing on first call."""
    global _initialized, _MODULE_REGISTRY
    if not _initialized:
        _register_all()
        _initialized = True
    return _MODULE_REGISTRY


def _register_all():
    """Import and register bare-metal SSH module definitions."""
    global _MODULE_REGISTRY

    from modules.bare_metal.bnk_cert_issuer import BnkCertIssuerSSHModule
    from modules.bare_metal.bnk_cert_manager import CertManagerSSHModule
    from modules.bare_metal.bnk_cneinstance import BnkCneInstanceSSHModule
    from modules.bare_metal.bnk_flo import BnkFloSSHModule
    from modules.bare_metal.bnk_gatewayclass import BnkGatewayClassSSHModule
    from modules.bare_metal.bnk_license import BnkLicenseSSHModule
    from modules.bare_metal.bnk_network_setup import NetworkSetupSSHModule
    from modules.bare_metal.bnk_prerequisites import BnkPrerequisitesSSHModule
    from modules.bare_metal.bnk_vlans import BnkVlansSSHModule
    from modules.bare_metal.flash_dpu import FlashDPUModule
    from modules.bare_metal.install_cni import InstallCNIModule
    from modules.bare_metal.install_dpu_prereqs import InstallDPUPrereqsModule
    from modules.bare_metal.install_gateway_api import InstallGatewayAPIModule
    from modules.bare_metal.install_k8s_prereqs import InstallK8sPrereqsModule
    from modules.bare_metal.install_multus import InstallMultusModule
    from modules.bare_metal.install_sriov import InstallSRIOVModule
    from modules.bare_metal.install_storage import InstallStorageModule
    from modules.bare_metal.kubeadm_init import KubeadmInitModule
    from modules.bare_metal.kubeadm_join import KubeadmJoinModule
    from modules.bare_metal.label_dpu_node import LabelDPUNodeModule
    from modules.bare_metal.probe_dpu import ProbeDPUModule
    from modules.bare_metal.reboot_host import RebootHostModule
    from modules.bare_metal.set_nic_mode import SetNicModeModule
    from modules.bare_metal.setup_dpu_networking import SetupDPUNetworkingModule
    from modules.bare_metal.taint_dpu_node import TaintDPUNodeModule
    from modules.bare_metal.wait_dpu_ready import WaitDPUReadyModule

    all_modules = [
        ProbeDPUModule,
        SetNicModeModule,
        RebootHostModule,
        FlashDPUModule,
        WaitDPUReadyModule,
        SetupDPUNetworkingModule,
        InstallDPUPrereqsModule,
        InstallK8sPrereqsModule,
        KubeadmInitModule,
        InstallCNIModule,
        InstallMultusModule,
        InstallGatewayAPIModule,
        InstallSRIOVModule,
        InstallStorageModule,
        KubeadmJoinModule,
        LabelDPUNodeModule,
        TaintDPUNodeModule,
        # ADR-204: SSH ports of the BNK layer (modules 18–25)
        BnkPrerequisitesSSHModule,
        NetworkSetupSSHModule,
        CertManagerSSHModule,
        BnkCertIssuerSSHModule,
        BnkFloSSHModule,
        BnkCneInstanceSSHModule,
        BnkLicenseSSHModule,
        BnkVlansSSHModule,
        BnkGatewayClassSSHModule,
    ]

    for mod_cls in all_modules:
        instance = mod_cls()
        _MODULE_REGISTRY[instance.path] = instance

    logger.info(
        f"Registered {len(_MODULE_REGISTRY)} Python module definitions: "
        f"{list(_MODULE_REGISTRY.keys())}"
    )
