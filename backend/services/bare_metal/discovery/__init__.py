"""Bare-metal discovery service — orchestrates all probes."""

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from models.bare_metal import BareMetalHost
from schemas.bare_metal import BareMetalDiscoveryResponse
from services.bare_metal.discovery.bmc_probe import BmcProbeResult, probe_bmc
from services.bare_metal.discovery.dpu_probe import DpuProbeResult, probe_dpu
from services.bare_metal.discovery.host_probe import HostProbeResult, probe_host
from services.bare_metal.discovery.topology_detector import (
    TopologyRecommendation,
    detect_topology,
    is_supernic_mode,
    nic_mode_display,
)
from services.bare_metal.discovery.vlan_probe import VlanProbeResult, probe_vlan
from services.bare_metal.ipmi_client import IPMIClient
from services.bare_metal.redfish.client import RedfishClient
from services.bare_metal.ssh_session import RemoteSSHSession, SSHSession
from services.ssh.paramiko_utils import decrypt_ssh_credential

logger = logging.getLogger(__name__)


class BareMetalDiscoveryService:
    """Orchestrate bare-metal discovery using SSH + BMC + IPMI probes.

    This service:
    1. Builds SSH/Redfish/IPMI clients from host configuration
    2. Runs configured probes
    3. Auto-detects topology
    4. Builds an assessment with pass/warn/fail checks
    5. Detects version drift against the target BNK profile
    6. Updates the host's discovery cache
    """

    def __init__(self, db: Session):
        self.db = db

    def _update_stage(self, host: BareMetalHost, stage: str, detail: str | None = None) -> None:
        """Write discovery stage to the host row for frontend polling.

        Commits so the updated stage is immediately visible to concurrent
        readers (the frontend's polling GET runs in a separate DB session).
        Each stage update is independently durable — if the task crashes
        between stages, the last committed stage is the correct state.
        """
        host.last_discovery_status = stage
        host.last_discovery_error = detail
        self.db.commit()

    def run_discovery(self, host_id: int, options: dict) -> BareMetalDiscoveryResponse:
        """Run bare-metal discovery probes for a host.

        Args:
            host_id: ID of the BareMetalHost to discover
            options: Probe options (probe_bmc, probe_ipmi, probe_dpu, probe_vlan)

        Returns:
            Structured discovery response
        """
        host = self._get_host(host_id)

        try:
            self._update_stage(host, "ssh_probe", "Connecting to host via SSH…")

            # Build clients
            ssh = self._build_ssh(host)
            redfish = self._build_redfish(host) if options.get("probe_bmc") else None
            ipmi = self._build_ipmi(host) if options.get("probe_ipmi") else None

            # Run probes
            host_result = probe_host(ssh)

            self._update_stage(host, "bmc_probe", "Probing BMC via Redfish/IPMI…")

            bmc_result = None
            if (redfish or ipmi) and (options.get("probe_bmc") or options.get("probe_ipmi")):
                bmc_result = probe_bmc(
                    host.bmc_ip or host.ipmi_ip or "",
                    redfish=redfish,
                    ipmi=ipmi,
                )

            self._update_stage(host, "dpu_probe", "Probing DPU via jumphost SSH…")

            dpu_result = None
            if options.get("probe_dpu") and host_result.ssh_reachable and host_result.rshim_present:
                dpu_ssh = self._build_dpu_ssh(ssh, host)
                dpu_result = probe_dpu(dpu_ssh)

            vlan_result = None
            if options.get("probe_vlan") and host.vlan_id and host_result.pf_interfaces:
                vlan_result = probe_vlan(
                    ssh,
                    host_result.pf_interfaces[0]["name"],
                    host.vlan_id,
                    host.gateway_ip or "",
                    host.host_mgmt_ip or "",
                )

            self._update_stage(host, "assessment", "Running readiness assessment…")

            # Auto-detect topology
            topology_rec = detect_topology(host_result, bmc_result)

            # Build assessment
            assessment = self._build_assessment(host_result, bmc_result, dpu_result, vlan_result, topology_rec)

            # Version drift
            version_drift = None
            if dpu_result and host.version_profile:
                version_drift = self._check_version_drift(dpu_result, host.version_profile)

            # Build recommendations
            recommendations = self._build_recommendations(
                host_result, bmc_result, dpu_result, vlan_result, topology_rec
            )

            # Build actionable assessment
            actionable_assessment = self._build_actionable_assessment(
                host_result, bmc_result, dpu_result, topology_rec
            )

            # Update host discovery cache
            now = datetime.now(UTC)
            if host_result.ssh_reachable:
                host.os_info = {
                    "os_type": host_result.os_type,
                    "os_version": host_result.os_version,
                    "os_pretty_name": host_result.os_pretty_name,
                    "kernel": host_result.kernel_version,
                    "arch": host_result.architecture,
                }

                host.k8s_info = {
                    "installed": host_result.k8s_installed,
                    "running": host_result.k8s_running,
                    "version": host_result.k8s_version,
                }

                # Promote typed hardware facts to columns (CON-012)
                host.nic_mode = host_result.nic_mode
                host.mst_device = host_result.mst_device
                host.rshim_present = host_result.rshim_present
                host.default_route_iface = host_result.default_route_iface
                host.phase_c_deposited = host_result.phase_c_deposited
                host.phase_c_completed = host_result.phase_c_completed
                host.pf_interfaces = host_result.pf_interfaces
                host.vf_count = host_result.vf_count
                host.hugepages_host_gb = host_result.hugepages_gb

            if dpu_result:
                host.dpu_info = [{
                    "ssh_reachable": dpu_result.ssh_reachable,
                    "doca_version": dpu_result.doca_version,
                    "containerd_version": dpu_result.containerd_version,
                    "runc_version": dpu_result.runc_version,
                    "k8s_version": dpu_result.k8s_version,
                    "ovs_installed": dpu_result.ovs_installed,
                }]

            # Update topology if not manually set (auto-detected values are refreshed on re-discovery)
            if host.topology is None or host.topology_auto_detected:
                host.topology = topology_rec.topology
                host.topology_auto_detected = True

            # Auto-populate DPU selection for dual_dpu_obmc topology
            if topology_rec.topology == "dual_dpu_obmc":
                self._auto_select_deployment_dpu(host, host_result)

            # Update BMC info if probed
            if bmc_result:
                if bmc_result.redfish_reachable:
                    host.bmc_access_tier = "redfish"
                elif bmc_result.ipmi_reachable:
                    host.bmc_access_tier = "ipmi"
                if bmc_result.vendor:
                    host.bmc_vendor = bmc_result.vendor

            host.last_discovery_at = now
            self.db.flush()

            # Build probes dict
            probes: dict = {
                "host": _host_result_to_dict(host_result),
            }
            if bmc_result:
                probes["bmc"] = _bmc_result_to_dict(bmc_result)
            if dpu_result:
                probes["dpu"] = _dpu_result_to_dict(dpu_result)
            if vlan_result:
                probes["vlan"] = _vlan_result_to_dict(vlan_result)
            if host_result.doca_status:
                probes["doca"] = host_result.doca_status

            response = BareMetalDiscoveryResponse(
                host_id=host.id,
                host_ip=host.host_ip,
                topology_recommendation=topology_rec.topology,
                probes=probes,
                assessment=assessment,
                version_drift=version_drift,
                recommendations=recommendations,
                actionable_assessment=actionable_assessment,
                discovered_at=now,
            )

            # Cache full discovery result for cross-session access (CON-012)
            host.last_discovery_result = response.model_dump(mode="json")
            self.db.flush()

            self._update_stage(host, "completed")

            # D-022: re-reconcile fleet membership with freshly discovered facts.
            try:
                from services.fleet_reconcile_service import reconcile_fleet_member
                reconcile_fleet_member(self.db, "bare-metal-host", host.id)
            except Exception:
                logger.warning("Fleet reconcile failed after host discovery id=%s", host.id)

            return response

        except Exception as exc:
            try:
                self._update_stage(host, "failed", str(exc))
            except Exception:
                logger.warning("Could not update discovery stage to failed for host %s", host_id)
            raise

    def _get_host(self, host_id: int) -> BareMetalHost:
        host = self.db.query(BareMetalHost).filter(BareMetalHost.id == host_id).first()
        if not host:
            from core.errors import NotFoundError

            raise NotFoundError("BareMetalHost", str(host_id))
        return host

    def _build_ssh(self, host: BareMetalHost) -> SSHSession:
        """Build SSHSession from host config, including password auth and jumphost chain."""
        import tempfile
        from pathlib import Path

        username = "root"
        private_key_path = None
        password = None

        if host.ssh_credential_id:
            from models.ssh_credential import SSHCredential

            cred = self.db.query(SSHCredential).filter_by(id=host.ssh_credential_id).first()
            if cred:
                cred_data = decrypt_ssh_credential(cred)
                username = cred_data["username"]
                password = cred_data["password"]
                # SSHSession expects file paths, so write key to temp file if present
                if cred_data["private_key_content"]:
                    key_path = Path(tempfile.mktemp(prefix="forge-ssh-", suffix=".key"))
                    key_path.write_text(cred_data["private_key_content"])
                    key_path.chmod(0o600)
                    private_key_path = str(key_path)

        # Resolve jumphost chain — include key/password material
        jumphost_chain = None
        if host.jumphost_chain:
            jumphost_chain = []
            for jh_entry in host.jumphost_chain:
                jh_cred_id = jh_entry.get("ssh_credential_id") if isinstance(jh_entry, dict) else None
                if jh_cred_id:
                    from models.ssh_credential import SSHCredential

                    jh_cred = self.db.query(SSHCredential).filter_by(id=jh_cred_id).first()
                    if jh_cred:
                        jh_data = decrypt_ssh_credential(jh_cred)
                        jh_info: dict = {
                            "host": jh_data["host"],
                            "port": jh_data["port"],
                            "username": jh_data["username"],
                        }
                        # Include jumphost key material (write to temp file)
                        if jh_data["private_key_content"]:
                            jh_key_path = Path(tempfile.mktemp(prefix="forge-jh-", suffix=".key"))
                            jh_key_path.write_text(jh_data["private_key_content"])
                            jh_key_path.chmod(0o600)
                            jh_info["private_key_path"] = str(jh_key_path)
                        elif jh_data["password"]:
                            jh_info["password"] = jh_data["password"]
                        jumphost_chain.append(jh_info)
        elif host.project and host.project.ssh_credential_id:
            # Fall back to project-level jumphost
            from core.encryption import decrypt_value
            from models.ssh_credential import SSHCredential

            project_cred = self.db.query(SSHCredential).filter(
                SSHCredential.id == host.project.ssh_credential_id
            ).first()

            if project_cred:
                decrypted_password = None
                decrypted_key = None
                decrypted_passphrase = None

                if project_cred.password_encrypted:
                    decrypted_password = decrypt_value(project_cred.password_encrypted)
                if project_cred.private_key_encrypted:
                    decrypted_key = decrypt_value(project_cred.private_key_encrypted)
                if project_cred.key_passphrase_encrypted:
                    decrypted_passphrase = decrypt_value(project_cred.key_passphrase_encrypted)

                jumphost_chain = [{
                    "host": project_cred.host,
                    "port": project_cred.port or 22,
                    "username": project_cred.username,
                    "password": decrypted_password,
                    "private_key_content": decrypted_key,
                    "passphrase": decrypted_passphrase,
                }]

        return SSHSession(
            host=host.host_ip,
            username=username,
            port=host.ssh_port or 22,
            private_key_path=private_key_path,
            password=password,
            jumphost_chain=jumphost_chain if jumphost_chain else None,
        )

    def _build_dpu_ssh(self, host_ssh: SSHSession, host: BareMetalHost) -> "RemoteSSHSession":
        """Build a session to reach the DPU via the host's SSH connection.

        The DPU's rshim IP (192.168.100.2) is only reachable from the bare-metal
        host itself. We run DPU commands through the host:
            host_ssh.execute("ssh root@dpu_ip '<cmd>'")

        If a DPU credential is configured, its password or key is used for auth.
        The default BFB image uses password 'password' for user 'ubuntu'.
        """
        dpu_ip = host.dpu_mgmt_ip or "192.168.100.2"
        if "/" in dpu_ip:
            dpu_ip = dpu_ip.split("/")[0]

        dpu_user = "ubuntu"
        dpu_password = None

        if host.dpu_credential_id:
            from models.ssh_credential import SSHCredential

            cred = self.db.query(SSHCredential).filter_by(id=host.dpu_credential_id).first()
            if cred:
                cred_data = decrypt_ssh_credential(cred)
                dpu_user = cred_data["username"] or "ubuntu"
                if cred_data["private_key_content"]:
                    # Key needs to be on the HOST, not the container.
                    # For now, we can't easily place keys on the host.
                    # Log a warning and skip key auth for DPU.
                    logger.warning("DPU key auth not yet supported via relay — use password auth")
                dpu_password = cred_data["password"]

        return RemoteSSHSession(
            host_ssh=host_ssh,
            remote_host=dpu_ip,
            remote_user=dpu_user,
            remote_port=22,
            remote_password=dpu_password,
        )

    def _build_redfish(self, host: BareMetalHost) -> RedfishClient | None:
        """Build RedfishClient if BMC credentials are available."""
        if not host.bmc_ip:
            return None
        if not host.bmc_username_encrypted or not host.bmc_password_encrypted:
            return None

        try:
            from core.encryption import decrypt_value

            username = decrypt_value(host.bmc_username_encrypted)
            password = decrypt_value(host.bmc_password_encrypted)
            return RedfishClient(host.bmc_ip, username, password)
        except Exception as e:
            logger.warning("Failed to build Redfish client: %s", e)
            return None

    def _build_ipmi(self, host: BareMetalHost) -> IPMIClient | None:
        """Build IPMIClient if IPMI credentials are available."""
        ipmi_ip = host.ipmi_ip or host.bmc_ip
        if not ipmi_ip:
            return None

        username_enc = host.ipmi_username_encrypted or host.bmc_username_encrypted
        password_enc = host.ipmi_password_encrypted or host.bmc_password_encrypted
        if not username_enc or not password_enc:
            return None

        try:
            from core.encryption import decrypt_value

            username = decrypt_value(username_enc)
            password = decrypt_value(password_enc)
            return IPMIClient(ipmi_ip, username, password)
        except Exception as e:
            logger.warning("Failed to build IPMI client: %s", e)
            return None

    def _auto_select_deployment_dpu(
        self, host: BareMetalHost, host_result: HostProbeResult
    ) -> None:
        """Auto-select deployment DPU for dual_dpu_obmc topology.

        For MGX-class servers with 2 DPUs:
        - Management DPU: carries the default route (host connectivity)
        - Deployment DPU: the OTHER DPU (where BNK will be installed)

        We select the deployment DPU as the one NOT carrying the default route.
        Also sets rshim_source = "bmc" since host rshim doesn't work on these systems.
        """
        import re

        host.rshim_source = "bmc"  # dual_dpu_obmc always uses BMC rshim

        # Get default route interface
        mgmt_iface = host_result.default_route_iface
        if not mgmt_iface:
            logger.warning("No default route interface found for dual_dpu_obmc host %s", host.id)
            return

        # Group PFs by PCI domain (same logic as topology detector)
        mlx5_pfs = [
            pf for pf in host_result.pf_interfaces
            if pf.get("driver") == "mlx5_core" and pf.get("link_state") == "up"
        ]

        if len(mlx5_pfs) < 4:
            logger.warning("Expected 4+ mlx5 PFs for dual_dpu_obmc, found %d", len(mlx5_pfs))
            return

        # Find which domain the mgmt interface belongs to
        mgmt_domain = None
        for pf in mlx5_pfs:
            if pf.get("name") == mgmt_iface:
                m = re.match(r"(.*?)f\d+", pf.get("name", ""))
                if m:
                    mgmt_domain = m.group(1)
                break

        if not mgmt_domain:
            logger.warning("Could not determine mgmt PCI domain from interface %s", mgmt_iface)
            return

        # Find PFs NOT in the mgmt domain — these belong to the deployment DPU
        deploy_pfs = []
        for pf in mlx5_pfs:
            name = pf.get("name", "")
            m = re.match(r"(.*?)f\d+", name)
            if m and m.group(1) != mgmt_domain:
                deploy_pfs.append(pf)

        if not deploy_pfs:
            logger.warning("Could not identify deployment DPU PFs for host %s", host.id)
            return

        # Pick the first deployment PF to extract PCI address
        # Interface name like "enp3s0f0np0" → PCI domain "0000:03:00.0"
        # Interface name like "enP2p3s0f0np0" → PCI domain "0002:03:00.0"
        # This is a heuristic; production may need sysfs lookup
        deploy_iface = deploy_pfs[0].get("name", "")

        # For MGX, PCI address can be derived from interface name pattern
        # enp3s0f0np0 → bus=3 → 0000:03:00.0
        # enP2p3s0f0np0 → domain=2, bus=3 → 0002:03:00.0
        pci_match = re.match(r"en[Pp]?(\d*)p?(\d+)s0f0", deploy_iface)
        if pci_match:
            domain = pci_match.group(1) or "0000"
            bus = pci_match.group(2)
            # Pad domain to 4 digits
            domain = domain.zfill(4)
            host.deploy_dpu_pci_address = f"{domain}:{bus.zfill(2)}:00.0"
            host.deploy_dpu_index = 0 if mgmt_domain > deploy_pfs[0].get("name", "") else 1
            logger.info(
                "Auto-selected deployment DPU for host %s: PCI=%s (index=%d)",
                host.id, host.deploy_dpu_pci_address, host.deploy_dpu_index
            )
            # Auto-create Dpu row for DPU tab visibility (DD-12)
            self._ensure_dpu_row_for_host(host)
        else:
            logger.warning("Could not parse PCI address from interface %s", deploy_iface)

    def _ensure_dpu_row_for_host(self, host: BareMetalHost) -> None:
        """Ensure a Dpu row exists for the selected deployment DPU.

        This is idempotent — if a Dpu already exists with the same
        host IP + PCI address, we update it rather than create a duplicate.
        """
        if not host.deploy_dpu_pci_address:
            return  # Can't create without PCI address

        from models.dpu import Dpu

        # Look for existing Dpu by host IP + PCI address
        existing = self.db.query(Dpu).filter(
            Dpu.project_id == host.project_id,
            Dpu.host_node_ip == host.host_ip,
            Dpu.pci_address == host.deploy_dpu_pci_address,
        ).first()

        if existing:
            # Update access_mode if changed
            if existing.access_mode != "bmc" and host.rshim_source == "bmc":
                existing.access_mode = "bmc"
                logger.info("Updated Dpu %s access_mode to 'bmc'", existing.id)
            return

        # Create new Dpu row
        dpu_name = f"{host.name}-dpu"
        if host.deploy_dpu_index is not None:
            dpu_name = f"{host.name}-dpu{host.deploy_dpu_index}"

        dpu = Dpu(
            project_id=host.project_id,
            name=dpu_name,
            host_node_ip=host.host_ip,
            pci_address=host.deploy_dpu_pci_address,
            access_mode="bmc" if host.rshim_source == "bmc" else "in-band",
            bmc_ip=host.bmc_ip,
            # Inherit credentials from host
            host_ssh_credential_id=host.dpu_credential_id,
        )
        self.db.add(dpu)
        self.db.flush()
        logger.info(
            "Auto-created Dpu row %s for dual_dpu_obmc host %s (PCI=%s)",
            dpu.id, host.id, host.deploy_dpu_pci_address
        )

    def _build_assessment(
        self,
        host_result: HostProbeResult,
        bmc_result: BmcProbeResult | None,
        dpu_result: DpuProbeResult | None,
        vlan_result: VlanProbeResult | None,
        topology_rec: TopologyRecommendation,
    ) -> list[dict]:
        """Build pass/warn/fail assessment from probe results."""
        checks: list[dict] = []

        # SSH connectivity
        checks.append({
            "check": "host_ssh",
            "status": "pass" if host_result.ssh_reachable else "fail",
            "message": "Host SSH reachable" if host_result.ssh_reachable else f"Host SSH unreachable: {host_result.error}",
        })

        # Host prerequisites
        if host_result.ssh_reachable and host_result.missing_prerequisites:
            checks.append({
                "check": "host_prerequisites",
                "status": "warn",
                "message": f"Missing host packages: {', '.join(host_result.missing_prerequisites)} — install before deployment",
            })

        # OS detected
        if host_result.ssh_reachable:
            checks.append({
                "check": "os_detected",
                "status": "pass" if host_result.os_type else "warn",
                "message": f"OS: {host_result.os_pretty_name}" if host_result.os_type else "Could not detect OS",
            })

        # DPU PCIe detection
        if host_result.ssh_reachable:
            checks.append({
                "check": "dpu_pci",
                "status": "pass" if host_result.dpu_pci_detected else "warn",
                "message": "BlueField DPU detected (PCIe)" if host_result.dpu_pci_detected else "No BlueField DPU found via lspci",
            })

        # NIC mode
        if host_result.nic_mode:
            checks.append({
                "check": "nic_mode",
                "status": "pass",
                "message": f"NIC mode: {nic_mode_display(host_result.nic_mode)} (raw: {host_result.nic_mode})",
            })

        # Rshim (DPU present)
        if host_result.ssh_reachable:
            rshim_status = "pass" if host_result.rshim_present else "warn"
            rshim_message = "Rshim device present (BF3 DPU detected)" if host_result.rshim_present else "No rshim device found"
            if topology_rec.topology == "dual_dpu_obmc" and not host_result.rshim_present:
                rshim_status = "info"
                rshim_message = "Host rshim not present; DPU OpenBMC manages rshim for dual-DPU topology"
            checks.append({
                "check": "rshim_device",
                "status": rshim_status,
                "message": rshim_message,
            })

        # BMC
        if bmc_result:
            if bmc_result.redfish_reachable:
                checks.append({
                    "check": "bmc_redfish",
                    "status": "pass",
                    "message": f"Redfish BMC reachable (vendor: {bmc_result.vendor})",
                })
            elif bmc_result.ipmi_reachable:
                checks.append({
                    "check": "bmc_ipmi",
                    "status": "pass",
                    "message": "IPMI reachable (no Redfish)",
                })
            else:
                checks.append({
                    "check": "bmc",
                    "status": "warn",
                    "message": "Neither Redfish nor IPMI reachable",
                })

        # Kubernetes state
        if host_result.ssh_reachable:
            if host_result.k8s_running:
                checks.append({
                    "check": "k8s_state",
                    "status": "pass",
                    "message": f"Kubernetes running (v{host_result.k8s_version})" if host_result.k8s_version else "Kubernetes cluster running",
                })
            elif host_result.k8s_installed:
                checks.append({
                    "check": "k8s_state",
                    "status": "warn",
                    "message": f"kubectl installed (v{host_result.k8s_version}) but no cluster running — stale install or cluster down"
                    if host_result.k8s_version else "kubectl installed but no cluster running",
                })
            else:
                checks.append({
                    "check": "k8s_state",
                    "status": "info",
                    "message": "No Kubernetes installed (clean host)",
                })

        # DPU
        if dpu_result:
            checks.append({
                "check": "dpu_ssh",
                "status": "pass" if dpu_result.ssh_reachable else "fail",
                "message": "DPU SSH reachable" if dpu_result.ssh_reachable else f"DPU SSH unreachable: {dpu_result.error}",
            })
            if dpu_result.ssh_reachable:
                checks.append({
                    "check": "dpu_hugepages",
                    "status": "pass" if dpu_result.hugepages_gb >= 16 else "warn",
                    "message": f"DPU hugepages: {dpu_result.hugepages_gb}GB"
                    + (" (need 16GB for TMM)" if dpu_result.hugepages_gb < 16 else ""),
                })

        # VLAN
        if vlan_result:
            checks.append({
                "check": "vlan_path",
                "status": "pass" if vlan_result.vlan_path_valid else "fail",
                "message": f"VLAN {vlan_result.test_interface} path valid"
                if vlan_result.vlan_path_valid
                else f"VLAN path invalid: {vlan_result.error}",
            })

        return checks

    def _check_version_drift(self, dpu_result: DpuProbeResult, version_profile: object) -> list[dict]:
        """Compare installed DPU versions against target profile."""
        drift: list[dict] = []

        if dpu_result.doca_version and version_profile.doca_version:  # type: ignore[union-attr]
            if dpu_result.doca_version != version_profile.doca_version:  # type: ignore[union-attr]
                drift.append({
                    "component": "doca",
                    "installed": dpu_result.doca_version,
                    "expected": version_profile.doca_version,  # type: ignore[union-attr]
                })

        if dpu_result.containerd_version and version_profile.containerd_version:  # type: ignore[union-attr]
            if dpu_result.containerd_version != version_profile.containerd_version:  # type: ignore[union-attr]
                drift.append({
                    "component": "containerd",
                    "installed": dpu_result.containerd_version,
                    "expected": version_profile.containerd_version,  # type: ignore[union-attr]
                })

        if dpu_result.runc_version and version_profile.runc_version:  # type: ignore[union-attr]
            if dpu_result.runc_version != version_profile.runc_version:  # type: ignore[union-attr]
                drift.append({
                    "component": "runc",
                    "installed": dpu_result.runc_version,
                    "expected": version_profile.runc_version,  # type: ignore[union-attr]
                })

        if dpu_result.k8s_version and version_profile.k8s_version:  # type: ignore[union-attr]
            # Normalize versions (strip 'v' prefix)
            installed = dpu_result.k8s_version.lstrip("v")
            expected = version_profile.k8s_version.lstrip("v")  # type: ignore[union-attr]
            if installed != expected:
                drift.append({
                    "component": "kubernetes",
                    "installed": dpu_result.k8s_version,
                    "expected": version_profile.k8s_version,  # type: ignore[union-attr]
                })

        return drift

    def _build_recommendations(
        self,
        host_result: HostProbeResult,
        bmc_result: BmcProbeResult | None,  # noqa: ARG002
        dpu_result: DpuProbeResult | None,
        vlan_result: VlanProbeResult | None,
        topology_rec: TopologyRecommendation,
    ) -> list[str]:
        """Build actionable recommendations from probe results."""
        recs: list[str] = []

        if not host_result.ssh_reachable:
            recs.append("Fix SSH connectivity before proceeding with deployment")
            return recs

        recs.append(f"Recommended topology: {topology_rec.topology} (confidence: {topology_rec.confidence})")

        if host_result.missing_prerequisites:
            recs.append(f"Install missing packages: {', '.join(host_result.missing_prerequisites)} (required for DPU management)")

        if not host_result.rshim_present:
            if topology_rec.topology == "dual_dpu_obmc":
                recs.append("Host rshim is not expected for dual_dpu_obmc — use DPU OpenBMC rshim path")
            else:
                recs.append("No rshim device found — verify DPU is installed and connected")

        if dpu_result and not dpu_result.ssh_reachable:
            recs.append("DPU is not SSH-reachable — may need BFB flash first")

        if dpu_result and dpu_result.hugepages_gb < 16:
            recs.append(
                f"DPU hugepages ({dpu_result.hugepages_gb}GB) below required 16GB — BFB flash will configure this"
            )

        if vlan_result and not vlan_result.vlan_path_valid:
            recs.append("VLAN path validation failed — check VLAN configuration on upstream switch")

        return recs

    def _build_actionable_assessment(
        self,
        host_result: HostProbeResult,
        bmc_result: BmcProbeResult | None,
        dpu_result: DpuProbeResult | None,
        topology_rec: TopologyRecommendation,
    ) -> list[dict]:
        """Build actionable next-steps from probe results.

        Returns list of assessment sections, each with:
        - topic: str (e.g. "nic_mode_change", "bfb_flash", "k8s_state", "phase_c_state", "bmc_suggestion")
        - severity: "info" | "warning" | "action_required"
        - title: Human-readable section title
        - body: Markdown text with instructions
        - commands: list[str] — shell commands to display (may be empty)
        """
        sections: list[dict] = []

        if not host_result.ssh_reachable:
            return sections  # No point in recommendations if SSH is unreachable

        # Host prerequisites section (before NIC mode — mst-tools needed for mlxconfig)
        if host_result.missing_prerequisites:
            pkg_list = ", ".join(host_result.missing_prerequisites)
            install_cmd = f"sudo apt-get update && sudo apt-get install -y {' '.join(host_result.missing_prerequisites)}"
            sections.append({
                "topic": "host_prerequisites",
                "severity": "action_required",
                "title": "Missing Host Prerequisites",
                "body": f"The following packages are required for DPU management but not installed: **{pkg_list}**.\n\nInstall them before proceeding with deployment. The deployment pipeline includes an automatic install step, but discovery results will be incomplete until these are available.",
                "commands": [install_cmd],
            })

        # NIC Mode Change section
        if is_supernic_mode(host_result.nic_mode):
            device_path = host_result.mst_device or "/dev/mst/mt41692_pciconf0"

            if topology_rec.topology == "regular":
                # Regular topology: host has independent NIC, so reboot is safe
                sections.append({
                    "topic": "nic_mode_change",
                    "severity": "action_required",
                    "title": "NIC Mode Change Required",
                    "body": f"""Current NIC mode is **{nic_mode_display(host_result.nic_mode)}** — deployment requires **DPU mode** (INTERNAL_CPU_MODEL=1).

**Steps:**
1. Run the mlxconfig command below to set DPU mode
2. Reboot the host (the host has an independent management NIC, so it will remain reachable)

Since this host has independent network connectivity, a standard reboot is sufficient — no BMC/IPMI access is needed.""",
                    "commands": [
                        f"sudo mlxconfig -y -d {device_path} set INTERNAL_CPU_MODEL=1",
                        "sudo reboot",
                    ],
                })
            elif topology_rec.topology in ["bf3", "bf3_ipmi", "bmc"]:
                # Non-regular: host network goes through BF3, connectivity risk
                bios_instructions = "BIOS fallback instructions:\n"
                vendor = (bmc_result.vendor if bmc_result else None) or (bmc_result.system_manufacturer if bmc_result else None)

                if vendor and "supermicro" in vendor.lower():
                    bios_instructions += "- **Supermicro**: Advanced → Network Stack Configuration → NVIDIA BlueField NIC Mode → DPU Mode"
                elif vendor and "lenovo" in vendor.lower():
                    bios_instructions += "- **Lenovo**: System Settings → Network → NIC Configuration → NVIDIA BlueField Mode → DPU"
                else:
                    bios_instructions += "- Generic: Enter BIOS → Advanced/Network Settings → Locate NVIDIA/Mellanox NIC configuration → Set INTERNAL_CPU_MODEL to 1 (DPU mode)"

                sections.append({
                    "topic": "nic_mode_change",
                    "severity": "action_required",
                    "title": "NIC Mode Change Required (Connectivity Risk)",
                    "body": f"""Current NIC mode is **{nic_mode_display(host_result.nic_mode)}** — deployment requires **DPU mode** (INTERNAL_CPU_MODEL=1).

**⚠️ WARNING**: This host's network goes through the BlueField NIC. A mode change + reboot **will cause connectivity loss**. Out-of-band access (BMC/IPMI) is required for recovery.

**Steps:**
1. Run the mlxconfig command below to set DPU mode
2. Perform a **full power cycle** (power off + power on, NOT just reboot)
3. Re-connect via BMC/IPMI console after power cycle

{bios_instructions}

If mlxconfig fails, use BIOS as a fallback method.""",
                    "commands": [
                        f"sudo mlxconfig -y -d {device_path} set INTERNAL_CPU_MODEL=1"
                    ],
                })
            elif topology_rec.topology == "dual_dpu_obmc":
                # MGX-class dual_dpu_obmc hosts are expected to already be in DPU mode.
                # If one is discovered in SuperNIC mode, that is outside the validated path
                # for this branch and should be handled explicitly when such hardware appears.
                pass

        # BFB Flash section
        if host_result.rshim_present and (not dpu_result or not dpu_result.ssh_reachable):
            severity = "action_required" if is_supernic_mode(host_result.nic_mode) else "warning"
            message_body = ""

            if is_supernic_mode(host_result.nic_mode):
                message_body = """DPU is unreachable via SSH, but rshim device is present.

**Before BFB flash:**
- NIC mode change + power cycle must be completed first (see NIC Mode Change section above)

**After NIC mode change:**
- BFB flash will be the next step to provision the DPU OS"""
            else:
                message_body = """DPU is unreachable via SSH, but rshim device is present.

**Next step:** BFB flash to provision the DPU operating system

Refer to NVIDIA documentation for BFB flashing procedures via rshim."""

            sections.append({
                "topic": "bfb_flash",
                "severity": severity,
                "title": "DPU Not Reachable — BFB Flash May Be Required",
                "body": message_body,
                "commands": [],
            })
        elif not host_result.rshim_present and topology_rec.topology in ["bf3", "bf3_ipmi"]:
            sections.append({
                "topic": "bfb_flash",
                "severity": "warning",
                "title": "No Rshim Device Detected",
                "body": """Expected a BlueField DPU but no rshim device found.

**Possible causes:**
- DPU hardware not installed
- DPU not properly connected to host
- Driver/firmware issue

**Next steps:**
- Verify physical DPU installation
- Check PCIe device detection: `lspci | grep -i mellanox`""",
                "commands": ["lspci | grep -i mellanox"],
            })

        # K8s State section
        if host_result.k8s_installed and not host_result.k8s_running:
            sections.append({
                "topic": "k8s_state",
                "severity": "warning",
                "title": "Kubernetes Installed But Not Running",
                "body": """Kubernetes is installed but the cluster is not currently running.

**Next steps:**
- Check kubelet service status
- Review kubelet logs for errors
- Verify cluster initialization""",
                "commands": [
                    "sudo systemctl status kubelet",
                    "sudo journalctl -u kubelet -n 50"
                ],
            })

        # Phase C State section
        if host_result.phase_c_deposited or host_result.phase_c_completed:
            # Check for inconsistency: Phase C artifacts with NIC in SuperNIC mode
            if is_supernic_mode(host_result.nic_mode):
                sections.append({
                    "topic": "phase_c_state",
                    "severity": "warning",
                    "title": "Phase C Artifacts — Likely Stale",
                    "body": f"""Phase C artifacts are present but NIC is in SuperNIC mode ({nic_mode_display(host_result.nic_mode)}).

BNK requires DPU mode (INTERNAL_CPU_MODEL=1) for Phase C to be meaningful. These artifacts are likely **stale from a prior deployment** and will be replaced during the new deployment.

**No action needed** — the deployment pipeline will handle fresh Phase C provisioning.""",
                    "commands": [],
                })
            elif host_result.phase_c_deposited and not host_result.phase_c_completed:
                sections.append({
                    "topic": "phase_c_state",
                    "severity": "info",
                    "title": "Phase C Deployment In Progress",
                    "body": """Phase C (BNK platform deployment) files are present but deployment has not completed.

**Next steps:**
- Check Phase C service status
- Review deployment logs""",
                    "commands": [
                        "sudo systemctl status bnk-phase-c",
                        "sudo journalctl -u bnk-phase-c -n 50"
                    ],
                })
            elif host_result.phase_c_completed:
                sections.append({
                    "topic": "phase_c_state",
                    "severity": "info",
                    "title": "Phase C Deployment Completed",
                    "body": "Phase C deployment has completed successfully. BNK platform components should be operational.",
                    "commands": [],
                })

        # BMC Suggestion section — only for topologies where host network goes through BF3
        if (
            not bmc_result
            and is_supernic_mode(host_result.nic_mode)
            and topology_rec.topology in ["bf3", "bf3_ipmi", "bmc"]
        ):
            sections.append({
                "topic": "bmc_suggestion",
                "severity": "warning",
                "title": "BMC Credentials Recommended",
                "body": """This host's network goes through the BlueField NIC. BMC/IPMI access is **required** for NIC mode changes because:

- Mode change + reboot will cause connectivity loss
- BMC provides out-of-band console access for recovery
- Without BMC, you need physical console access

**Next steps:**
- Configure BMC credentials in host settings (IP, username, password)
- Re-run discovery with BMC probe enabled""",
                "commands": [],
            })

        return sections


# --- Serialization helpers ---


def _host_result_to_dict(r: HostProbeResult) -> dict:
    return {
        "ssh_reachable": r.ssh_reachable,
        "os_type": r.os_type,
        "os_version": r.os_version,
        "os_pretty_name": r.os_pretty_name,
        "kernel_version": r.kernel_version,
        "architecture": r.architecture,
        "nic_mode": r.nic_mode,
        "mst_device": r.mst_device,
        "pf_interfaces": r.pf_interfaces,
        "vf_count": r.vf_count,
        "hugepages_gb": r.hugepages_gb,
        "rshim_present": r.rshim_present,
        "k8s_installed": r.k8s_installed,
        "k8s_running": r.k8s_running,
        "k8s_version": r.k8s_version,
        "default_route": r.default_route,
        "default_route_iface": r.default_route_iface,
        "phase_c_deposited": r.phase_c_deposited,
        "phase_c_completed": r.phase_c_completed,
        "missing_prerequisites": r.missing_prerequisites,
        "error": r.error,
    }


def _bmc_result_to_dict(r: BmcProbeResult) -> dict:
    return {
        "redfish_reachable": r.redfish_reachable,
        "ipmi_reachable": r.ipmi_reachable,
        "vendor": r.vendor,
        "nic_mode_bios": r.nic_mode_bios,
        "power_state": r.power_state,
        "system_manufacturer": r.system_manufacturer,
        "system_model": r.system_model,
        "error": r.error,
    }


def _dpu_result_to_dict(r: DpuProbeResult) -> dict:
    return {
        "ssh_reachable": r.ssh_reachable,
        "doca_version": r.doca_version,
        "containerd_version": r.containerd_version,
        "runc_version": r.runc_version,
        "k8s_version": r.k8s_version,
        "ovs_installed": r.ovs_installed,
        "ovs_bridge_ports": r.ovs_bridge_ports,
        "vf_representors": r.vf_representors,
        "hugepages_gb": r.hugepages_gb,
        "error": r.error,
    }


def _vlan_result_to_dict(r: VlanProbeResult) -> dict:
    return {
        "vlan_path_valid": r.vlan_path_valid,
        "test_interface": r.test_interface,
        "gateway_reachable": r.gateway_reachable,
        "error": r.error,
    }
