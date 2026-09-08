"""DPU CRUD and per-project DPU settings services.

Handles credential encryption at the service boundary:
  - Plaintext passwords/usernames arrive on write models.
  - Stored encrypted via `core.encryption.encrypt_value`.
  - Responses never return plaintext passwords; presence is surfaced as
    boolean flags (e.g. `has_bmc_password`).
  - Usernames are decrypted in responses so the UI can display them.
"""

import ipaddress
import logging
import secrets
import string
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none, encrypt_value
from core.errors import BadRequestError, NotFoundError, ValidationError
from models.dpu import BfConfTemplate, BluefieldSoftwareImage, Dpu, ProjectDpuSettings
from schemas.dpu import (
    DEFAULT_DNS_SERVERS,
    DEFAULT_NTP_SERVERS,
    DpuCreate,
    DpuListResponse,
    DpuResponse,
    DpuUpdate,
    ProjectDpuSettingsResponse,
    ProjectDpuSettingsUpdate,
)
from services.dpu_credentials import RedfishError, redfish_post_with_bmc_fallback
from services.dpu_tunnel import resolve_project_jumphost_cred

# Reset types the UI offers — subset of the Redfish ResetActionInfo allowable
# values, picked for "reboot the ARM CPU" semantics.
VALID_RESET_TYPES = ("GracefulRestart", "ForceRestart", "PowerCycle")

logger = logging.getLogger(__name__)


def build_pristine_dpu_settings(db: Session, project_id: int) -> ProjectDpuSettings:
    """Factory for a freshly-seeded ProjectDpuSettings row.

    Used anywhere the first settings row is created for a project — both the
    explicit "open DPU Project Settings" path and implicit creations from
    BMC discovery — so defaults are consistent regardless of entry point.

    Picks the first available bf.conf template and BlueField software image
    (ordered by id) so Preview/Flash work out of the box; the operator can
    change both in the DPU Project Settings UI.
    """
    first_template = db.query(BfConfTemplate).order_by(BfConfTemplate.id).first()
    first_image = (
        db.query(BluefieldSoftwareImage).order_by(BluefieldSoftwareImage.id).first()
    )
    return ProjectDpuSettings(
        project_id=project_id,
        bf_template_id=first_template.id if first_template else None,
        bluefield_image_id=first_image.id if first_image else None,
        ntp_servers=list(DEFAULT_NTP_SERVERS),
        # NVIDIA's factory default DPU OS username is "ubuntu". Seed a random
        # 12-char password per project — the operator can reveal it in the UI
        # and overwrite if desired.
        default_os_username="ubuntu",
        default_os_password_encrypted=encrypt_value(
            "".join(
                secrets.choice(string.ascii_letters + string.digits)
                for _ in range(12)
            )
        ),
        external_vlan_id=0,
        external_vlan_ipv4_subnet="10.10.20.0/24",
        external_vlan_start_host=100,
        external_vlan_uplink="bond0",
        internal_vlan_id=100,
        internal_vlan_ipv4_subnet="10.10.10.0/24",
        internal_vlan_start_host=100,
        internal_vlan_uplink="bond0",
        # No pre-seeded extras — the operator adds them if needed (storage,
        # vdi, …) via the project defaults UI.
        extra_vlan_interfaces=[],
    )


# ─── DPU (per-device) CRUD ──────────────────────────────────────────────────

class DpuService:
    """CRUD for per-project DPU records."""

    def __init__(self, db: Session):
        self.db = db

    def list_dpus(self, project_id: int) -> DpuListResponse:
        rows = (
            self.db.query(Dpu)
            .filter(Dpu.project_id == project_id)
            .order_by(Dpu.bmc_ip)
            .all()
        )
        return DpuListResponse(dpus=[self._to_response(d) for d in rows])

    def get_dpu(self, project_id: int, dpu_id: int) -> DpuResponse:
        return self._to_response(self._require_dpu(project_id, dpu_id))

    def create_dpu(self, project_id: int, data: DpuCreate) -> DpuResponse:
        # Count existing DPUs to derive this one's ordinal for IP auto-assign.
        existing_count = (
            self.db.query(Dpu).filter(Dpu.project_id == project_id).count()
        )
        ordinal = existing_count + 1
        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project_id)
            .first()
        )

        # Seed extras from whatever the caller supplied (typically empty).
        extras_payload: list[dict] = []
        if data.extra_vlan_interfaces is not None:
            extras_payload = [
                {
                    "name": e.name,
                    "vlan_id": e.vlan_id,
                    "ipv4": e.ipv4,
                    "ipv6": e.ipv6,
                }
                for e in data.extra_vlan_interfaces
            ]

        dpu = Dpu(
            project_id=project_id,
            name=data.name,
            access_mode=data.access_mode or "bmc",
            bmc_ip=data.bmc_ip,
            oob0_ipv4=data.oob0_ipv4 or "dhcp",
            host_node_ip=data.host_node_ip,
            host_ssh_credential_id=data.host_ssh_credential_id,
            host_ssh_username=data.host_ssh_username,
            host_ssh_port=data.host_ssh_port,
            host_ssh_auth_type=data.host_ssh_auth_type,
            pci_address=data.pci_address,
            external_vlan_id=data.external_vlan_id,
            external_vlan_ipv4=data.external_vlan_ipv4,
            external_vlan_ipv6=data.external_vlan_ipv6,
            internal_vlan_id=data.internal_vlan_id,
            internal_vlan_ipv4=data.internal_vlan_ipv4,
            internal_vlan_ipv6=data.internal_vlan_ipv6,
            extra_vlan_interfaces=extras_payload,
        )

        # Auto-populate per-DPU VLAN IPs from project-level subnets when the
        # caller didn't supply them. Uses the DPU's ordinal (1-based) as the
        # host offset. Subnet math tolerates malformed input — just skips.
        if settings is not None:
            for iface in ("external", "internal"):
                subnet_v4 = getattr(settings, f"{iface}_vlan_ipv4_subnet", None)
                subnet_v6 = getattr(settings, f"{iface}_vlan_ipv6_subnet", None)
                if subnet_v4 and getattr(dpu, f"{iface}_vlan_ipv4") is None:
                    v4 = _suggest_ip_in_subnet(subnet_v4, ordinal)
                    if v4:
                        setattr(dpu, f"{iface}_vlan_ipv4", v4)
                if subnet_v6 and getattr(dpu, f"{iface}_vlan_ipv6") is None:
                    v6 = _suggest_ip_in_subnet(subnet_v6, ordinal)
                    if v6:
                        setattr(dpu, f"{iface}_vlan_ipv6", v6)

            # Populate extras from the project's `extra_vlan_interfaces` list,
            # keyed by name so per-row order in the UI doesn't matter.
            by_name = {e["name"]: e for e in (dpu.extra_vlan_interfaces or [])}
            for proj_entry in (settings.extra_vlan_interfaces or []):
                name = proj_entry.get("name")
                if not name or name in by_name:
                    continue
                assignment = {
                    "name": name,
                    "vlan_id": proj_entry.get("vlan_id"),
                    "ipv4": None,
                    "ipv6": None,
                }
                subnet_v4 = proj_entry.get("ipv4_subnet")
                subnet_v6 = proj_entry.get("ipv6_subnet")
                start = proj_entry.get("start_host") or 100
                offset = start + (ordinal - 1)
                if subnet_v4:
                    assignment["ipv4"] = _suggest_ip_in_subnet(subnet_v4, offset)
                if subnet_v6:
                    assignment["ipv6"] = _suggest_ip_in_subnet(subnet_v6, offset)
                by_name[name] = assignment
            dpu.extra_vlan_interfaces = list(by_name.values())

        if data.bmc_username:
            dpu.bmc_username_encrypted = encrypt_value(data.bmc_username)
        if data.bmc_password:
            dpu.bmc_password_encrypted = encrypt_value(data.bmc_password)
        if data.host_ssh_password:
            dpu.host_ssh_password_encrypted = encrypt_value(data.host_ssh_password)
        if data.host_ssh_private_key:
            dpu.host_ssh_private_key_encrypted = encrypt_value(data.host_ssh_private_key)
        if data.host_ssh_key_passphrase:
            dpu.host_ssh_key_passphrase_encrypted = encrypt_value(data.host_ssh_key_passphrase)

        self.db.add(dpu)
        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValidationError(
                "bmc_ip",
                f"DPU with BMC IP '{data.bmc_ip}' already exists in this project",
            ) from exc

        logger.info("Created DPU %s (bmc_ip=%s) in project %s", dpu.id, dpu.bmc_ip, project_id)

        # D-022: upsert fleet membership for the newly created DPU.
        try:
            from services.fleet_reconcile_service import reconcile_fleet_member
            reconcile_fleet_member(self.db, "dpu", dpu.id)
        except Exception:
            logger.exception("Fleet reconcile failed for new DPU id=%s", dpu.id)

        return self._to_response(dpu)

    def update_dpu(self, project_id: int, dpu_id: int, data: DpuUpdate) -> DpuResponse:
        dpu = self._require_dpu(project_id, dpu_id)

        # Simple scalar fields — only set when explicitly provided in the payload
        provided = data.model_dump(exclude_unset=True)
        for field in (
            "name", "bmc_ip",
            "host_node_ip", "host_ssh_credential_id",
            "host_ssh_username", "host_ssh_port", "host_ssh_auth_type",
            "external_vlan_id", "external_vlan_ipv4", "external_vlan_ipv6",
            "internal_vlan_id", "internal_vlan_ipv4", "internal_vlan_ipv6",
        ):
            if field in provided:
                setattr(dpu, field, provided[field])

        if "extra_vlan_interfaces" in provided:
            # Replace wholesale — the caller sends the full list every time.
            dpu.extra_vlan_interfaces = [
                {
                    "name": e["name"],
                    "vlan_id": e.get("vlan_id"),
                    "ipv4": e.get("ipv4"),
                    "ipv6": e.get("ipv6"),
                }
                for e in provided["extra_vlan_interfaces"]
            ]

        if "bmc_username" in provided:
            val = provided["bmc_username"]
            dpu.bmc_username_encrypted = encrypt_value(val) if val else None
        if "bmc_password" in provided:
            val = provided["bmc_password"]
            dpu.bmc_password_encrypted = encrypt_value(val) if val else None
        if "host_ssh_password" in provided:
            val = provided["host_ssh_password"]
            dpu.host_ssh_password_encrypted = encrypt_value(val) if val else None
        if "host_ssh_private_key" in provided:
            val = provided["host_ssh_private_key"]
            dpu.host_ssh_private_key_encrypted = encrypt_value(val) if val else None
        if "host_ssh_key_passphrase" in provided:
            val = provided["host_ssh_key_passphrase"]
            dpu.host_ssh_key_passphrase_encrypted = encrypt_value(val) if val else None

        try:
            self.db.flush()
        except IntegrityError as exc:
            self.db.rollback()
            raise ValidationError(
                "bmc_ip",
                f"DPU with BMC IP '{data.bmc_ip}' already exists in this project",
            ) from exc

        logger.info("Updated DPU %s in project %s", dpu_id, project_id)
        return self._to_response(dpu)

    def auto_assign_vlan_ips(
        self, project_id: int,
    ) -> tuple[DpuListResponse, int]:
        """Assign per-DPU IPs across external/internal/storage from project subnets.

        Order: DPUs sorted by id (oldest first). Each DPU gets
        `<network>.<start_host + index>/<prefix>` for every configured
        subnet. Both v4 and v6 are assigned when subnets are set. Existing
        per-DPU values are **overwritten** (the point of the button is to
        snap to a clean scheme).

        Returns `(response, assignments_count)`. When no subnets are
        configured on the project this is a no-op (count == 0), not an
        error — the UI surfaces the count so the operator can act if
        needed.
        """
        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project_id)
            .first()
        )

        dpus = (
            self.db.query(Dpu)
            .filter(Dpu.project_id == project_id)
            .order_by(Dpu.id)
            .all()
        )

        assignments = 0
        if settings is None:
            # No settings row → nothing to assign. Return the DPU list unchanged.
            return DpuListResponse(dpus=[self._to_response(d) for d in dpus]), 0
        extras = list(settings.extra_vlan_interfaces or [])
        for idx, dpu in enumerate(dpus):
            # Fixed external + internal rows.
            for iface in ("external", "internal"):
                subnet_v4 = getattr(settings, f"{iface}_vlan_ipv4_subnet", None)
                subnet_v6 = getattr(settings, f"{iface}_vlan_ipv6_subnet", None)
                start = getattr(settings, f"{iface}_vlan_start_host", None) or 100
                offset = start + idx
                if subnet_v4:
                    v4 = _suggest_ip_in_subnet(subnet_v4, offset)
                    if v4:
                        setattr(dpu, f"{iface}_vlan_ipv4", v4)
                        assignments += 1
                if subnet_v6:
                    v6 = _suggest_ip_in_subnet(subnet_v6, offset)
                    if v6:
                        setattr(dpu, f"{iface}_vlan_ipv6", v6)
                        assignments += 1

            # Extras — snap to the same network math, and prune any per-DPU
            # extras whose name is no longer in the project list.
            by_name: dict[str, dict] = {
                e["name"]: dict(e) for e in (dpu.extra_vlan_interfaces or [])
            }
            new_list: list[dict] = []
            for extra in extras:
                name = extra.get("name")
                if not name:
                    continue
                start = extra.get("start_host") or 100
                offset = start + idx
                entry = by_name.get(name, {"name": name, "vlan_id": None,
                                           "ipv4": None, "ipv6": None})
                entry["name"] = name
                entry["vlan_id"] = extra.get("vlan_id")
                subnet_v4 = extra.get("ipv4_subnet")
                subnet_v6 = extra.get("ipv6_subnet")
                if subnet_v4:
                    v4 = _suggest_ip_in_subnet(subnet_v4, offset)
                    if v4:
                        entry["ipv4"] = v4
                        assignments += 1
                else:
                    entry["ipv4"] = None
                if subnet_v6:
                    v6 = _suggest_ip_in_subnet(subnet_v6, offset)
                    if v6:
                        entry["ipv6"] = v6
                        assignments += 1
                else:
                    entry["ipv6"] = None
                new_list.append(entry)
            dpu.extra_vlan_interfaces = new_list

        self.db.flush()
        logger.info(
            "Auto-assigned %d VLAN IPs across %d DPU(s) in project %s",
            assignments, len(dpus), project_id,
        )
        return DpuListResponse(dpus=[self._to_response(d) for d in dpus]), assignments

    def delete_dpu(self, project_id: int, dpu_id: int) -> None:
        dpu = self._require_dpu(project_id, dpu_id)
        self.db.delete(dpu)
        self.db.flush()
        logger.info("Deleted DPU %s from project %s", dpu_id, project_id)

    def reset_dpu(
        self, project_id: int, dpu_id: int, reset_type: str,
    ) -> dict[str, object]:
        """Trigger a reset on the DPU's ARM CPU.

        BMC DPUs: Redfish POST to ComputerSystem.Reset.
        In-band DPUs: equivalent actions via the host —
          * GracefulRestart → SSH-to-DPU-OS `sudo reboot`.
          * ForceRestart    → `sudo echo SW_RESET 1 > /dev/{rshim_device}/misc`.
          * PowerCycle      → `sudo mlxfwreset -l 3 --sync 1 -y reset`.
        """
        if reset_type not in VALID_RESET_TYPES:
            raise ValidationError(
                "reset_type",
                f"Invalid reset_type '{reset_type}'; must be one of {list(VALID_RESET_TYPES)}",
            )

        dpu = self._require_dpu(project_id, dpu_id)

        if dpu.access_mode == "in-band":
            return self._reset_dpu_inband(dpu, reset_type)

        user, pw = self._resolve_bmc_credentials(dpu)

        url = (
            f"https://{dpu.bmc_ip}"
            "/redfish/v1/Systems/Bluefield/Actions/ComputerSystem.Reset"
        )
        body = {"ResetType": reset_type}

        jumphost_cred = resolve_project_jumphost_cred(self.db, dpu.project_id)
        try:
            resp = redfish_post_with_bmc_fallback(
                dpu.bmc_ip, user, pw, url=url, json_body=body,
                jumphost_cred=jumphost_cred,
            )
        except RedfishError as exc:
            raise BadRequestError(str(exc)) from exc

        if resp.status_code >= 400:
            raise BadRequestError(
                f"Redfish reset rejected by BMC {dpu.bmc_ip}: "
                f"HTTP {resp.status_code} - {resp.text[:300]}"
            )

        logger.info(
            "DPU %s (bmc=%s) reset requested: %s (status=%d)",
            dpu_id, dpu.bmc_ip, reset_type, resp.status_code,
        )
        return {
            "dpu_id": dpu_id,
            "bmc_ip": dpu.bmc_ip,
            "reset_type": reset_type,
            "status": "initiated",
            "http_status": resp.status_code,
        }

    def _reset_dpu_inband(self, dpu: Dpu, reset_type: str) -> dict[str, object]:
        """Run the in-band equivalent of a Redfish reset via the host."""
        import paramiko

        from services.rshim_service import _short_bdf_for_mst, open_inband_host_ssh  # noqa: F401

        try:
            host_client = open_inband_host_ssh(self.db, dpu)
        except BadRequestError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BadRequestError(
                f"SSH to host {dpu.host_node_ip} failed: {exc}"
            ) from exc

        try:
            if reset_type == "GracefulRestart":
                rc, out, err = _reset_dpu_os_via_tmfifo(self.db, dpu, host_client)
                action = "ssh ubuntu@192.168.100.2 sudo reboot"
            elif reset_type == "ForceRestart":
                # rshim SW_RESET — exactly the same command we already use
                # on the flash path to put the DPU into BFB-receive mode,
                # but here without the BOOT_MODE override, so the DPU just
                # reboots into its current OS. Use this DPU's own rshim
                # device so multi-DPU hosts target the right SoC.
                rshim = (getattr(dpu, "rshim_device", None) or "rshim0")
                rc, out, err = _exec_sudo(
                    host_client,
                    f"echo 'SW_RESET 1' > /dev/{rshim}/misc",
                    timeout=20,
                )
                action = f"echo SW_RESET 1 > /dev/{rshim}/misc"
            elif reset_type == "PowerCycle":
                pci = dpu.pci_address or ""
                if not pci:
                    raise BadRequestError(
                        "DPU has no pci_address recorded — cannot resolve MST device."
                    )
                # Resolve /dev/mst/<...>pciconf0 for this PCI address, then
                # run mlxfwreset level 3 (PCI reset — flaps the link to the
                # host, same semantics as the BMC PowerCycle).
                rc, out, err = _exec_sudo(
                    host_client,
                    f"MSTDEV=$(sudo -n mst status -v 2>/dev/null | "
                    f"awk -v bdf='{_short_bdf_for_mst(pci)}' "
                    "'$0 ~ bdf {for (i=1;i<=NF;i++) "
                    "if ($i ~ /^\\/dev\\/mst\\//) print $i}' | head -1); "
                    "if [ -z \"$MSTDEV\" ]; then "
                    "  echo 'MST device not resolved — run Install rshim + MFT first' >&2; "
                    "  exit 2; "
                    "fi; "
                    "mlxfwreset -d \"$MSTDEV\" -l 3 --sync 1 -y reset",
                    timeout=120,
                )
                action = "mlxfwreset -l 3 reset"
            else:
                raise ValidationError("reset_type", f"unsupported in-band reset {reset_type!r}")
        finally:
            try:
                host_client.close()
            except Exception:
                pass

        if rc != 0:
            raise BadRequestError(
                f"In-band {reset_type} on {dpu.host_hostname or dpu.host_node_ip} "
                f"failed (exit={rc}): {(err or out or '<no output>').strip()[:400]}"
            )

        logger.info(
            "DPU %s (in-band on %s) reset requested: %s",
            dpu.id, dpu.host_node_ip, reset_type,
        )
        # Quieten unused-import linter about paramiko — keeps the import
        # near the callers that actually need it for mypy hints.
        _ = paramiko
        return {
            "dpu_id": dpu.id,
            "host_node_ip": dpu.host_node_ip,
            "reset_type": reset_type,
            "status": "initiated",
            "action": action,
        }

    def reset_dpu_nic(self, project_id: int, dpu_id: int) -> dict[str, object]:
        """Reset the BlueField NIC silicon via ``mlxfwreset -l 4 -t 0``.

        Used to recover from a stuck NIC firmware pre-init state where
        ``mlxfwmanager`` reports ``Failed to open device`` /
        ``MFE_NO_FLASH_DETECTED``. Different from PowerCycle (-l 3 PCI
        reset): level 4 is a warm reboot of the full chip.

        For in-band DPUs the command runs on the worker host. For
        BMC-mode DPUs it runs on the DPU OS itself (which sees its own
        NIC silicon). BMC-mode requires ``dpu_os_status='reachable'``;
        it raises ``BadRequestError`` otherwise.
        """
        from services.dpu_link_mode_service import _connect_dpu_os
        from services.rshim_service import _short_bdf_for_mst, open_inband_host_ssh

        dpu = self._require_dpu(project_id, dpu_id)

        # Level 4 = "Warm Reboot" (full chip reset). Type 0 = "Full chip
        # reset". --sync 1 lets the FW own the cross-domain handshake.
        # 180s budget covers the PCI flap + FW re-init.
        if dpu.access_mode == "in-band":
            if not dpu.host_node_ip:
                raise BadRequestError(
                    f"DPU {dpu.id} has no host_node_ip — cannot reach the host."
                )
            pci = dpu.pci_address or ""
            if not pci:
                raise BadRequestError(
                    "DPU has no pci_address recorded — cannot resolve MST device. "
                    "Run Discover to populate it."
                )
            try:
                client = open_inband_host_ssh(self.db, dpu)
            except BadRequestError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise BadRequestError(
                    f"SSH to host {dpu.host_node_ip} failed: {exc}"
                ) from exc
            try:
                rc, out, err = _exec_sudo(
                    client,
                    f"MSTDEV=$(sudo -n mst status -v 2>/dev/null | "
                    f"awk -v bdf='{_short_bdf_for_mst(pci)}' "
                    "'$0 ~ bdf {for (i=1;i<=NF;i++) "
                    "if ($i ~ /^\\/dev\\/mst\\//) print $i}' | head -1); "
                    "if [ -z \"$MSTDEV\" ]; then "
                    "  echo 'MST device not resolved — install MFT and run Discover' >&2; "
                    "  exit 2; "
                    "fi; "
                    "mlxfwreset -d \"$MSTDEV\" -l 4 -t 0 --sync 1 -y reset 2>&1",
                    timeout=180,
                )
                ssh_target = f"host {dpu.host_node_ip}"
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        else:
            if dpu.dpu_os_status != "reachable" or not dpu.dpu_os_ip:
                raise BadRequestError(
                    "BMC-mode NIC reset needs the DPU OS reachable for SSH. "
                    "Run *Probe DPU OS* first; if it stays unreachable, the "
                    "NIC may need a cold power cycle of the chassis."
                )
            settings = (
                self.db.query(ProjectDpuSettings)
                .filter(ProjectDpuSettings.project_id == dpu.project_id)
                .first()
            )
            os_user = settings.default_os_username if settings else None
            os_pw = (
                decrypt_value_or_none(settings.default_os_password_encrypted)
                if settings and settings.default_os_password_encrypted else None
            )
            if not os_user or not os_pw:
                raise BadRequestError(
                    "Project DPU settings are missing the DPU OS default "
                    "username or password — set them in DPU Project Settings."
                )
            try:
                client = _connect_dpu_os(dpu.dpu_os_ip, os_user, os_pw)
            except Exception as exc:  # noqa: BLE001
                raise BadRequestError(
                    f"SSH to DPU OS at {dpu.dpu_os_ip} failed: {exc}"
                ) from exc
            try:
                rc, out, err = _exec_sudo(
                    client,
                    "mlxfwreset -d "
                    "$(ls /dev/mst/mt*pciconf0 2>/dev/null | head -1) "
                    "-l 4 -t 0 --sync 1 -y reset 2>&1",
                    timeout=180,
                )
                ssh_target = f"DPU OS {dpu.dpu_os_ip}"
            finally:
                try:
                    client.close()
                except Exception:
                    pass

        if rc != 0:
            raise BadRequestError(
                f"mlxfwreset on {ssh_target} failed (exit={rc}): "
                f"{(err or out or '<no output>').strip()[:600]}"
            )

        logger.info(
            "DPU %s NIC reset (mlxfwreset -l 4) requested via %s",
            dpu.id, ssh_target,
        )
        return {
            "dpu_id": dpu.id,
            "ssh_target": ssh_target,
            "status": "initiated",
            "action": "mlxfwreset -l 4 -t 0 reset",
            "output": (out or "").strip()[:2000],
        }

    def _resolve_bmc_credentials(self, dpu: Dpu) -> tuple[str, str]:
        """DPU-level creds take precedence; fall back to project OOB defaults."""
        user = (
            decrypt_value_or_none(dpu.bmc_username_encrypted)
            if dpu.bmc_username_encrypted else None
        )
        pw = (
            decrypt_value_or_none(dpu.bmc_password_encrypted)
            if dpu.bmc_password_encrypted else None
        )
        if not user or not pw:
            settings = (
                self.db.query(ProjectDpuSettings)
                .filter(ProjectDpuSettings.project_id == dpu.project_id)
                .first()
            )
            if settings:
                if not user:
                    user = settings.default_oob_username
                if not pw and settings.default_oob_password_encrypted:
                    pw = decrypt_value_or_none(settings.default_oob_password_encrypted)
        if pw and not user:
            user = "root"
        if not user or not pw:
            raise BadRequestError(
                "No BMC password configured — set a per-DPU password or a project default OOB password.",
            )
        return user, pw

    def reveal_bmc_password(self, project_id: int, dpu_id: int) -> str:
        """Decrypt and return the DPU's BMC password (operator-only).

        Per-DPU password takes precedence; falls back to the project default OOB
        password when no per-DPU password is set (FEAT-0184 / ERR-0021), mirroring
        _resolve_bmc_credentials. 404s only when neither is configured.
        """
        dpu = self._require_dpu(project_id, dpu_id)
        if dpu.bmc_password_encrypted:
            pw = decrypt_value_or_none(dpu.bmc_password_encrypted)
            if pw is not None:
                return pw
        # Fall back to the project default OOB password.
        settings = (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == dpu.project_id)
            .first()
        )
        if settings and settings.default_oob_password_encrypted:
            pw = decrypt_value_or_none(settings.default_oob_password_encrypted)
            if pw is not None:
                return pw
        raise NotFoundError("bmc_password", dpu_id)

    # ── helpers ──

    def _require_dpu(self, project_id: int, dpu_id: int) -> Dpu:
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("Dpu", dpu_id)
        return dpu

    def register_host_from_dpu(
        self, project_id: int, dpu_id: int, *, name: str | None = None,
    ) -> dict[str, Any]:
        """Add this DPU's host to the Bare Metal tab.

        Only valid for in-band DPUs — those are the rows where the
        host IP, hostname, and SSH credentials are recorded. BMC-mode
        DPUs don't carry usable host info (the BMC IP is not the x86
        host's IP), so they can't be registered via this path.

        Idempotent on (project, host_ip): re-clicking on a DPU whose
        host is already registered returns the existing row with
        status='exists'. Multiple DPUs sharing the same host all
        succeed silently — Forge points each at the same Bare Metal row.
        """
        from services.bare_metal.host_registration import register_or_lookup

        dpu = self._require_dpu(project_id, dpu_id)
        if dpu.access_mode != "in-band":
            raise BadRequestError(
                f"DPU {dpu.id} is access_mode={dpu.access_mode!r} — only "
                "in-band DPUs carry the host IP needed to register a "
                "bare-metal host."
            )
        if not dpu.host_node_ip:
            raise BadRequestError(
                f"DPU {dpu.id} has no host_node_ip recorded — run Discover "
                "first or fill in the host SSH details on the DPU edit form."
            )

        snapshot = self._snapshot_dpu_host_credentials(dpu)
        return register_or_lookup(
            self.db,
            project_id=project_id,
            host_ip=dpu.host_node_ip,
            hostname=dpu.host_hostname,
            snapshot=snapshot,
            name=name,
            description=f"Registered from DPU tab (DPU {dpu.id})",
        )

    def _snapshot_dpu_host_credentials(self, dpu: Dpu) -> dict[str, Any]:
        """Return the credential snapshot the registration helper expects.

        Mirrors `DiscoveryService._snapshot_discovery_credentials` so
        both paths feed `register_or_lookup` the same shape. Saved
        credential FK wins over inline fields when both are present.
        """
        if dpu.host_ssh_credential_id is not None:
            from models.ssh_credential import SSHCredential

            cred = self.db.get(SSHCredential, dpu.host_ssh_credential_id)
            if cred is not None:
                return {
                    "credential_id": dpu.host_ssh_credential_id,
                    "username": cred.username,
                    "port": int(cred.port or 22),
                    "auth_type": cred.auth_type or "password",
                    "password_encrypted": cred.password_encrypted,
                    "private_key_encrypted": cred.private_key_encrypted,
                    "key_passphrase_encrypted": cred.key_passphrase_encrypted,
                }
        # Inline path — same shape, encrypted blobs passed through.
        return {
            "credential_id": None,
            "username": dpu.host_ssh_username,
            "port": int(dpu.host_ssh_port or 22),
            "auth_type": dpu.host_ssh_auth_type or "password",
            "password_encrypted": dpu.host_ssh_password_encrypted,
            "private_key_encrypted": dpu.host_ssh_private_key_encrypted,
            "key_passphrase_encrypted": dpu.host_ssh_key_passphrase_encrypted,
        }

    def _derive_bfb_hostname_for_response(self, dpu: Dpu) -> str:
        """Compute the hostname this DPU's bf.conf renders into the OS.

        The DPU tab surfaces this so the operator sees exactly what name
        will be flashed (`worker1-dpu` for single-DPU hosts,
        `worker1-dpu1` / `worker1-dpu2` for multi-DPU). Mirrors the logic
        in `bf_conf_renderer._derive_bfb_hostname`, but counts peers from
        the DB so the result matches what the renderer will produce at
        flash time.
        """
        from services.bf_conf_renderer import _derive_bfb_hostname

        peer_count = 1
        if dpu.access_mode == "in-band" and dpu.host_node_ip:
            peer_count = (
                self.db.query(Dpu)
                .filter(
                    Dpu.project_id == dpu.project_id,
                    Dpu.access_mode == "in-band",
                    Dpu.host_node_ip == dpu.host_node_ip,
                )
                .count()
            )
        return _derive_bfb_hostname(dpu, peer_count=peer_count)

    def _to_response(self, dpu: Dpu) -> DpuResponse:
        nic_mode, host_privilege_level = _derive_bios_fields(dpu.last_discovery_payload)
        link_mode = _derive_link_mode(dpu.last_discovery_payload)
        bfb_hostname = self._derive_bfb_hostname_for_response(dpu)
        dpu_os_error: str | None = None
        dpu_os_connectivity: dict[str, Any] | None = None
        dpu_os_lacp: dict[str, Any] | None = None
        dpu_os_firmware: dict[str, str | None] | None = None
        dpu_os_interfaces: list[str] | None = None
        payload = dpu.last_discovery_payload
        if isinstance(payload, dict):
            section = payload.get("dpu_os")
            if isinstance(section, dict):
                err = section.get("error")
                if isinstance(err, str) and err.strip():
                    dpu_os_error = err
                conn = section.get("connectivity")
                if isinstance(conn, dict):
                    dpu_os_connectivity = conn
                lacp = section.get("lacp")
                if isinstance(lacp, dict):
                    dpu_os_lacp = lacp
                fw = section.get("firmware")
                if isinstance(fw, dict):
                    dpu_os_firmware = fw
                ifs = section.get("interfaces")
                if isinstance(ifs, list):
                    dpu_os_interfaces = [str(x) for x in ifs]
        return DpuResponse(
            id=dpu.id,
            project_id=dpu.project_id,
            name=dpu.name,
            access_mode=dpu.access_mode,
            bmc_ip=dpu.bmc_ip,
            bmc_username=decrypt_value_or_none(dpu.bmc_username_encrypted)
            if dpu.bmc_username_encrypted
            else None,
            has_bmc_password=bool(dpu.bmc_password_encrypted),
            host_node_ip=dpu.host_node_ip,
            host_hostname=dpu.host_hostname,
            host_ssh_credential_id=dpu.host_ssh_credential_id,
            host_ssh_username=dpu.host_ssh_username,
            host_ssh_port=dpu.host_ssh_port,
            host_ssh_auth_type=dpu.host_ssh_auth_type,
            has_host_ssh_password=bool(dpu.host_ssh_password_encrypted),
            has_host_ssh_private_key=bool(dpu.host_ssh_private_key_encrypted),
            pci_address=dpu.pci_address,
            serial_number=dpu.serial_number,
            oob0_mac=dpu.oob0_mac,
            oob0_ipv4=dpu.oob0_ipv4,
            external_vlan_id=dpu.external_vlan_id,
            external_vlan_ipv4=dpu.external_vlan_ipv4,
            external_vlan_ipv6=dpu.external_vlan_ipv6,
            internal_vlan_id=dpu.internal_vlan_id,
            internal_vlan_ipv4=dpu.internal_vlan_ipv4,
            internal_vlan_ipv6=dpu.internal_vlan_ipv6,
            extra_vlan_interfaces=list(dpu.extra_vlan_interfaces or []),
            last_discovery_at=dpu.last_discovery_at,
            last_discovery_status=dpu.last_discovery_status,
            last_discovery_error=dpu.last_discovery_error,
            nic_mode=nic_mode,
            host_privilege_level=host_privilege_level,
            link_mode=link_mode,
            bfb_hostname=bfb_hostname,
            dpu_os_ip=dpu.dpu_os_ip,
            dpu_os_reachable=dpu.dpu_os_reachable,
            dpu_os_status=dpu.dpu_os_status,
            dpu_os_error=dpu_os_error,
            dpu_os_connectivity=dpu_os_connectivity,
            dpu_os_lacp=dpu_os_lacp,
            dpu_os_firmware=dpu_os_firmware,
            dpu_os_interfaces=dpu_os_interfaces,
            flash_status=dpu.flash_status,
            flash_stage_detail=dpu.flash_stage_detail,
            flash_started_at=dpu.flash_started_at,
            flash_completed_at=dpu.flash_completed_at,
            flash_error=dpu.flash_error,
            last_rendered_bf_conf_at=dpu.last_rendered_bf_conf_at,
            rshim_state=dpu.rshim_state,
            rshim_state_detail=dpu.rshim_state_detail,
            rshim_checked_at=dpu.rshim_checked_at,
            rshim_device=dpu.rshim_device,
            created_at=dpu.created_at,
            updated_at=dpu.updated_at,
        )


def _exec_sudo(client, cmd: str, *, timeout: int = 30) -> tuple[int, str, str]:
    """Run `sudo -n sh -c <cmd>` on an open paramiko client. Returns
    (exit_code, stdout, stderr) — all three always present."""
    full = f"sudo -n sh -c {_shquote(cmd)}"
    _stdin, stdout, stderr = client.exec_command(full, timeout=timeout)
    rc = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return rc, out, err


def _shquote(s: str) -> str:
    """Minimal POSIX shell single-quote escaper."""
    return "'" + s.replace("'", "'\\''") + "'"


def _reset_dpu_os_via_tmfifo(db, dpu: Dpu, host_client) -> tuple[int, str, str]:
    """Graceful DPU-OS reboot: open a nested SSH channel from the host to
    the DPU's tmfifo IP and run `sudo reboot` as ubuntu. Returns the
    (rc, out, err) tuple from the reboot command."""
    import paramiko

    from core.encryption import decrypt_value_or_none
    from models.dpu import ProjectDpuSettings
    from services.bf_conf_renderer import derive_tmfifo_dpu_host

    settings = (
        db.query(ProjectDpuSettings)
        .filter(ProjectDpuSettings.project_id == dpu.project_id)
        .first()
    )
    if settings is None:
        return 1, "", "Project DPU settings missing — set DPU OS username/password."
    os_user = settings.default_os_username
    os_pw = (
        decrypt_value_or_none(settings.default_os_password_encrypted)
        if settings.default_os_password_encrypted else None
    )
    if not os_user or not os_pw:
        return 1, "", (
            "DPU OS credentials not set — fill in project DPU settings "
            "before a graceful restart."
        )

    os_ip = dpu.dpu_os_ip or derive_tmfifo_dpu_host(getattr(dpu, "rshim_device", None), dpu=dpu)
    transport = host_client.get_transport()
    if transport is None:
        return 1, "", "host SSH transport unavailable"
    try:
        chan = transport.open_channel(
            "direct-tcpip", (os_ip, 22), ("127.0.0.1", 0), timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        return 1, "", f"Could not open tmfifo channel to {os_ip}: {exc}"

    dpu_client = paramiko.SSHClient()
    dpu_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        dpu_client.connect(
            os_ip, username=os_user, password=os_pw, sock=chan,
            look_for_keys=False, allow_agent=False, timeout=15,
        )
    except Exception as exc:  # noqa: BLE001
        try:
            chan.close()
        except Exception:
            pass
        return 1, "", f"SSH to DPU OS at {os_ip} failed: {exc}"

    try:
        # `sudo reboot` exits before the SSH teardown completes — swallow
        # paramiko's "connection closed by peer" as success.
        _stdin, stdout, stderr = dpu_client.exec_command(
            "sudo -n reboot 2>&1 || sudo -n /sbin/reboot 2>&1",
            timeout=10,
        )
        try:
            rc = stdout.channel.recv_exit_status()
        except Exception:
            rc = 0
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return rc, out, err
    finally:
        try:
            dpu_client.close()
        except Exception:
            pass


def _suggest_ip_in_subnet(subnet: str, ordinal: int) -> str | None:
    """Return `network+ordinal/prefix` for an IPv4 or IPv6 subnet.

    Used to auto-populate per-DPU VLAN IPs from project-level subnets. The
    user can edit the host portion later — this just offers a unique-per-DPU
    starting value so newly-added DPUs don't collide.
    Returns None on malformed input or if the resulting IP falls outside
    the network's usable range.
    """
    try:
        net = ipaddress.ip_network(subnet.strip(), strict=False)
    except (ValueError, AttributeError):
        return None
    candidate = net.network_address + ordinal
    if candidate not in net:
        return None
    return f"{candidate}/{net.prefixlen}"


def _derive_bios_fields(payload: dict | None) -> tuple[str | None, str | None]:
    """Extract (nic_mode, host_privilege_level) from a discovery payload.

    For BMC-managed DPUs these come from the Redfish BIOS attributes. For
    in-band DPUs we can't read BIOS, but rshim *working* is proof that the
    host already has privileged / trusted access to the DPU — so we surface
    that implication on the DPU row instead of leaving both columns empty.
    """
    if not isinstance(payload, dict):
        return None, None
    # In-band inventory payload has the rshim_device_present marker.
    if payload.get("rshim_device_present") is True:
        return "Trusted", "Privileged"
    bios = payload.get("bios") or {}
    nic_mode = bios.get("nic_mode") if isinstance(bios, dict) else None
    priv = bios.get("host_privilege_level") if isinstance(bios, dict) else None
    return (nic_mode if isinstance(nic_mode, str) else None,
            priv if isinstance(priv, str) else None)


def _link_type_from_mlxconfig(value: str | None) -> str | None:
    """Map one mlxconfig LINK_TYPE_PN value to 'eth' | 'ib' | None.

    mlxconfig prints these as 'ETH(2)' or 'IB(1)'; older firmware can
    print the bare numeric. Anything else (or missing) → None so the
    aggregate doesn't claim a mode it doesn't actually know.
    """
    if not isinstance(value, str):
        return None
    v = value.strip().upper()
    if "ETH" in v or v == "2":
        return "eth"
    if "IB" in v or v == "1":
        return "ib"
    return None


def _derive_link_mode(payload: dict | None) -> str | None:
    """Return 'eth' | 'ib' | 'mixed' | None for a DPU's port link types.

    Sourced from the in-band probe's mlxconfig snapshot — the
    next-boot LINK_TYPE_P1 / LINK_TYPE_P2 values are the authoritative
    "what mode is this DPU in" indicator. BMC-mode DPUs don't yet
    capture mlxconfig (Redfish doesn't expose it), so they return None
    until that path is wired up — the UI treats None as "unknown" and
    shows no chip rather than implying Ethernet.
    """
    if not isinstance(payload, dict):
        return None
    settings = payload.get("mlxconfig_settings")
    if not isinstance(settings, dict):
        return None
    p1 = _link_type_from_mlxconfig(settings.get("LINK_TYPE_P1"))
    p2 = _link_type_from_mlxconfig(settings.get("LINK_TYPE_P2"))
    seen = {x for x in (p1, p2) if x is not None}
    if not seen:
        return None
    if seen == {"eth"}:
        return "eth"
    if seen == {"ib"}:
        return "ib"
    return "mixed"


# ─── ProjectDpuSettings (per-project defaults) ──────────────────────────────

class ProjectDpuSettingsService:
    """Get-or-create semantics: every project has exactly one settings row.

    The row is lazily created on first GET so PATCH can always target it.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_or_create(self, project_id: int) -> ProjectDpuSettingsResponse:
        settings = self._find(project_id)
        if settings is None:
            settings = build_pristine_dpu_settings(self.db, project_id)
            self.db.add(settings)
            self.db.flush()
            logger.info(
                "Initialized DPU settings row for project %s with defaults", project_id,
            )
        return self._to_response(settings)

    def update(
        self, project_id: int, data: ProjectDpuSettingsUpdate
    ) -> ProjectDpuSettingsResponse:
        settings = self._find(project_id)
        if settings is None:
            settings = ProjectDpuSettings(project_id=project_id)
            self.db.add(settings)

        provided = data.model_dump(exclude_unset=True)
        for field in (
            "bluefield_image_id", "bf_template_id", "high_speed_mtu",
            "dns_servers", "ntp_servers",
            "default_oob_username", "default_os_username",
            "default_os_ssh_credential_id",
            "external_vlan_id", "external_vlan_ipv4_subnet", "external_vlan_ipv6_subnet",
            "external_vlan_start_host", "external_vlan_uplink",
            "internal_vlan_id", "internal_vlan_ipv4_subnet", "internal_vlan_ipv6_subnet",
            "internal_vlan_start_host", "internal_vlan_uplink",
        ):
            if field in provided:
                setattr(settings, field, provided[field])

        if "extra_vlan_interfaces" in provided:
            # Replace the extras list wholesale — the UI sends the full
            # post-edit list every time. We also prune per-DPU extras whose
            # name no longer appears in the project list, so stale assignments
            # don't leak into bf.conf rendering.
            settings.extra_vlan_interfaces = [
                {
                    "name": e["name"],
                    "vlan_id": e.get("vlan_id"),
                    "ipv4_subnet": e.get("ipv4_subnet"),
                    "ipv6_subnet": e.get("ipv6_subnet"),
                    "start_host": e.get("start_host"),
                    "uplink": e.get("uplink"),
                }
                for e in provided["extra_vlan_interfaces"]
            ]
            kept_names = {e["name"] for e in settings.extra_vlan_interfaces}
            for dpu in (
                self.db.query(Dpu).filter(Dpu.project_id == project_id).all()
            ):
                current = dpu.extra_vlan_interfaces or []
                filtered = [c for c in current if c.get("name") in kept_names]
                if len(filtered) != len(current):
                    dpu.extra_vlan_interfaces = filtered

        if "default_oob_password" in provided:
            val = provided["default_oob_password"]
            settings.default_oob_password_encrypted = encrypt_value(val) if val else None
        if "default_os_password" in provided:
            val = provided["default_os_password"]
            settings.default_os_password_encrypted = encrypt_value(val) if val else None

        # Cross-field check on the merged row (catches the case where a
        # partial PATCH would produce an inconsistent bond0 + p0/p1 mix).
        self._assert_uplink_consistency(settings)

        self.db.flush()
        logger.info("Updated DPU settings for project %s", project_id)
        return self._to_response(settings)

    @staticmethod
    def _assert_uplink_consistency(s: ProjectDpuSettings) -> None:
        uplinks: list[str | None] = [s.external_vlan_uplink, s.internal_vlan_uplink]
        for extra in (s.extra_vlan_interfaces or []):
            uplinks.append(extra.get("uplink"))
        chosen = [u for u in uplinks if u]
        if not chosen:
            return
        # Both External and Internal must have an uplink — a VLAN without
        # an uplink can't carry traffic, and rendering bf.conf with one
        # missing produces broken netplan / ovs-vsctl output. Catch it
        # here so the operator gets a clear error instead of a confused
        # DPU after flash.
        if not s.external_vlan_uplink or not s.internal_vlan_uplink:
            raise BadRequestError(
                "Both External and Internal VLANs must have an uplink "
                "port assigned before saving.",
            )
        has_bond = any(u == "bond0" for u in chosen)
        has_phys = any(u in ("p0", "p1") for u in chosen)
        if has_bond and has_phys:
            raise BadRequestError(
                "VLAN uplink ports must all be 'bond0' (LAG path) "
                "or all be 'p0'/'p1' (non-LAG path); mixing is not allowed."
            )
        # Non-LAG path constraints: extras and "router-on-a-stick"
        # (multiple VLANs sharing one physical uplink) aren't supported
        # by the non-LAG bf.conf template — each uplink owns its own
        # OVS bridge with no trunk between the two. Force operators
        # back to the LAG template if they need either.
        if has_phys and not has_bond:
            if s.extra_vlan_interfaces:
                raise BadRequestError(
                    "Non-LAG mode (uplinks via p0/p1) does not support "
                    "extra VLAN interfaces yet. Use the LAG template if "
                    "you need more than one VLAN per uplink.",
                )
            phys_chosen = [u for u in chosen if u in ("p0", "p1")]
            if len(set(phys_chosen)) != len(phys_chosen):
                raise BadRequestError(
                    "Non-LAG mode requires each VLAN to have a unique "
                    "uplink port — 'router-on-a-stick' (multiple VLANs "
                    "sharing one uplink) isn't supported. Use the LAG "
                    "template if you need that.",
                )

    def _find(self, project_id: int) -> ProjectDpuSettings | None:
        return (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project_id)
            .first()
        )

    def reveal_default_oob_password(self, project_id: int) -> str:
        """Decrypt and return the project's default BMC password (operator-only)."""
        s = self._find(project_id)
        if s is None or not s.default_oob_password_encrypted:
            raise NotFoundError("default_oob_password", project_id)
        pw = decrypt_value_or_none(s.default_oob_password_encrypted)
        if pw is None:
            raise NotFoundError("default_oob_password", project_id)
        return pw

    def reveal_default_os_password(self, project_id: int) -> str:
        """Decrypt and return the project's default DPU OS password (operator-only)."""
        s = self._find(project_id)
        if s is None or not s.default_os_password_encrypted:
            raise NotFoundError("default_os_password", project_id)
        pw = decrypt_value_or_none(s.default_os_password_encrypted)
        if pw is None:
            raise NotFoundError("default_os_password", project_id)
        return pw

    @staticmethod
    def vlan_subnet(s: ProjectDpuSettings, interface: str, family: str) -> str | None:
        """Read `<interface>_vlan_<family>_subnet` off the settings row.

        For external/internal this is a flat column; for any other name,
        search the extras list.
        """
        if interface in ("external", "internal"):
            attr = f"{interface}_vlan_{family}_subnet"
            val = getattr(s, attr, None)
            return val if isinstance(val, str) and val else None
        key = f"{family}_subnet"
        for extra in (s.extra_vlan_interfaces or []):
            if extra.get("name") == interface:
                val = extra.get(key)
                return val if isinstance(val, str) and val else None
        return None

    @staticmethod
    def _to_response(s: ProjectDpuSettings) -> ProjectDpuSettingsResponse:
        # Ensure timestamps are non-null for freshly-created rows whose server
        # defaults haven't materialized in this transaction yet.
        now = datetime.utcnow()
        return ProjectDpuSettingsResponse(
            id=s.id,
            project_id=s.project_id,
            bluefield_image_id=s.bluefield_image_id,
            bf_template_id=s.bf_template_id,
            high_speed_mtu=s.high_speed_mtu,
            dns_servers=list(s.dns_servers) if s.dns_servers else list(DEFAULT_DNS_SERVERS),
            ntp_servers=list(s.ntp_servers) if s.ntp_servers else list(DEFAULT_NTP_SERVERS),
            default_oob_username=s.default_oob_username,
            default_os_username=s.default_os_username,
            has_default_oob_password=bool(s.default_oob_password_encrypted),
            has_default_os_password=bool(s.default_os_password_encrypted),
            default_os_ssh_credential_id=s.default_os_ssh_credential_id,
            external_vlan_id=s.external_vlan_id,
            external_vlan_ipv4_subnet=s.external_vlan_ipv4_subnet,
            external_vlan_ipv6_subnet=s.external_vlan_ipv6_subnet,
            external_vlan_start_host=s.external_vlan_start_host,
            external_vlan_uplink=s.external_vlan_uplink,
            internal_vlan_id=s.internal_vlan_id,
            internal_vlan_ipv4_subnet=s.internal_vlan_ipv4_subnet,
            internal_vlan_ipv6_subnet=s.internal_vlan_ipv6_subnet,
            internal_vlan_start_host=s.internal_vlan_start_host,
            internal_vlan_uplink=s.internal_vlan_uplink,
            extra_vlan_interfaces=list(s.extra_vlan_interfaces or []),
            created_at=s.created_at or now,
            updated_at=s.updated_at or now,
        )
