"""
Bare-metal DPU deployment services.

Package structure (see docs/DPU_DEPLOY_SPEC.md for full architecture):
- ssh_session.py      — SSH execution via paramiko
- version_profiles.py — BNK version profile CRUD + seed data
- orchestrator.py     — Step registries + deployment state machine
"""

from services.bare_metal.orchestrator import (
    TOPOLOGY_STEPS,
    BareMetalDeploymentService,
    StepDefinition,
    get_steps_for_topology,
)
from services.bare_metal.ssh_session import RemoteSSHSession, SSHResult, SSHSession
from services.bare_metal.version_profiles import BnkVersionProfileService

__all__ = [
    "RemoteSSHSession",
    "SSHSession",
    "SSHResult",
    "BnkVersionProfileService",
    "BareMetalDeploymentService",
    "StepDefinition",
    "TOPOLOGY_STEPS",
    "get_steps_for_topology",
]
