"""DPU discovery orchestration — runs the BlueField Redfish probe.

Resolves credentials (per-DPU → project default → error), calls the Phase 0
probe, and persists the snapshot on the `dpus` row. Denormalizes a handful
of high-value fields (serial, oob0 MAC, NicMode, HostPrivilegeLevel) out of
`last_discovery_payload` so the compact table view doesn't need to re-parse
the JSON.
"""

import dataclasses
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none
from core.errors import BadRequestError, NotFoundError
from models.dpu import Dpu, ProjectDpuSettings
from services.bare_metal.bluefield import (
    BluefieldRedfishProbe,
    DpuInventory,
    ProbeError,
)
from services.dpu_tunnel import (
    bmc_http_request,
    resolve_project_jumphost_cred,
)

logger = logging.getLogger(__name__)


class DpuDiscoveryService:
    """Run a Redfish probe against a DPU and persist the result."""

    def __init__(self, db: Session):
        self.db = db

    def mark_running(self, project_id: int, dpu_id: int) -> None:
        """Flip `last_discovery_status = 'running'` before dispatching the task.

        Clears `last_discovery_error`. Called from the request handler so
        the UI immediately sees the running state without a race.
        """
        dpu = self._require_dpu(project_id, dpu_id)
        dpu.last_discovery_status = "running"
        dpu.last_discovery_error = None
        self.db.flush()

    def run_and_persist(self, dpu_id: int) -> None:
        """Run the probe synchronously and write the result back.

        Intended to be called from a Celery worker — self-contained, takes a
        bare `dpu_id`, handles its own credential lookup. Never raises; all
        failures are captured on the DPU row.
        """
        dpu = self.db.get(Dpu, dpu_id)
        if dpu is None:
            logger.error("DPU %s no longer exists, skipping probe", dpu_id)
            return

        if dpu.access_mode == "in-band":
            self._run_inband(dpu)
            return

        try:
            user, pw = self._resolve_credentials(dpu)
        except BadRequestError as exc:
            self._record_failure(dpu, str(exc))
            return

        jumphost_cred = resolve_project_jumphost_cred(self.db, dpu.project_id)
        probe = BluefieldRedfishProbe(
            bmc_ip=dpu.bmc_ip, username=user, password=pw,
            fetcher=_build_tunneled_fetcher(dpu.bmc_ip, user, pw, jumphost_cred)
            if jumphost_cred else None,
        )
        try:
            inventory = probe.probe()
        except ProbeError as exc:
            self._record_failure(dpu, f"Redfish probe failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 — we want every failure captured
            logger.exception("Unexpected probe error for DPU %s", dpu_id)
            self._record_failure(dpu, f"Unexpected probe error: {exc}")
            return

        self._record_success(dpu, inventory)

    # ── helpers ──

    def _run_inband(self, dpu: Dpu) -> None:
        """Discover path for in-band DPUs: SSH to the host, read rshim + lspci.

        Produces clear, actionable status text so the operator sees why a
        probe failed without having to read logs:

          * no credential set → failed, error names the fix.
          * SSH to host failed (auth / network / jumphost) → failed with
            the underlying reason.
          * SSH ok but rshim not ready → failed with "click Install rshim".
          * SSH ok + rshim present → ok; DPU's serial / oob0 MAC /
            firmware identity are populated from /dev/rshim0/misc +
            lspci + mlxfwmanager (best-effort).
        """
        from dataclasses import asdict

        from services.rshim_service import RshimService

        try:
            inv = RshimService(self.db).probe_inventory(dpu.project_id, dpu.id)
        except BadRequestError as exc:
            self._record_failure(dpu, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — capture every failure
            logger.warning(
                "in-band probe SSH failed for DPU %s (host %s): %s",
                dpu.id, dpu.host_node_ip, exc,
            )
            self._record_failure(
                dpu,
                f"SSH to host {dpu.host_node_ip} failed: {exc}",
            )
            return

        if not inv.rshim_device_present:
            self._record_failure(
                dpu,
                f"rshim is not ready on {dpu.host_node_ip} — click Actions → Install rshim + MFT.",
            )
            return

        # Populate denormalized columns only when the probe actually found
        # something — never clobber a good existing value with None.
        if inv.serial:
            dpu.serial_number = inv.serial
        elif inv.psid and not dpu.serial_number:
            dpu.serial_number = inv.psid
        if inv.mac and not dpu.oob0_mac:
            dpu.oob0_mac = inv.mac

        dpu.last_discovery_status = "ok"
        dpu.last_discovery_error = None
        dpu.last_discovery_at = datetime.utcnow()
        dpu.last_discovery_payload = asdict(inv)
        self.db.commit()
        logger.info(
            "in-band DPU %s: inventory collected (host=%s, fw=%s, opn=%s)",
            dpu.id, dpu.host_node_ip, inv.fw_version, inv.opn,
        )

        # D-022: re-reconcile fleet membership with freshly discovered facts.
        try:
            from services.fleet_reconcile_service import reconcile_fleet_member
            reconcile_fleet_member(self.db, "dpu", dpu.id)
        except Exception:
            logger.warning("Fleet reconcile failed after DPU in-band discovery id=%s", dpu.id)

    def _resolve_credentials(self, dpu: Dpu) -> tuple[str, str]:
        """DPU-level credentials take precedence; fall back to project defaults.

        BlueField BMCs only ship with the `root` account, so when only a
        password is configured we default the username to `root` rather
        than failing with a confusing "no credentials" error.
        """
        user = decrypt_value_or_none(dpu.bmc_username_encrypted) if dpu.bmc_username_encrypted else None
        pw = decrypt_value_or_none(dpu.bmc_password_encrypted) if dpu.bmc_password_encrypted else None

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
                "No BMC password configured — set a per-DPU password or a project default OOB password."
            )
        return user, pw

    def _record_failure(self, dpu: Dpu, error: str) -> None:
        dpu.last_discovery_status = "failed"
        dpu.last_discovery_error = error
        dpu.last_discovery_at = datetime.utcnow()
        self.db.commit()
        logger.warning("DPU %s discovery failed: %s", dpu.id, error)

    def _record_success(self, dpu: Dpu, inv: DpuInventory) -> None:
        dpu.last_discovery_status = "ok"
        dpu.last_discovery_error = None
        dpu.last_discovery_at = datetime.utcnow()
        dpu.last_discovery_payload = _serialize_inventory(inv)
        # Denormalize high-value fields
        if inv.serial_number:
            dpu.serial_number = inv.serial_number
        oob = _pick_oob_interface(inv.interfaces)
        if oob and oob.mac:
            dpu.oob0_mac = oob.mac
        self.db.commit()
        logger.info(
            "DPU %s discovery ok: serial=%s nic_mode=%s priv=%s",
            dpu.id, inv.serial_number, inv.bios.nic_mode, inv.bios.host_privilege_level,
        )

        # D-022: re-reconcile fleet membership with freshly discovered facts.
        try:
            from services.fleet_reconcile_service import reconcile_fleet_member
            reconcile_fleet_member(self.db, "dpu", dpu.id)
        except Exception:
            logger.warning("Fleet reconcile failed after DPU BMC discovery id=%s", dpu.id)

        # Read-only rshim probe on the BMC side — swallows every error so
        # Discover never fails because the rshim probe couldn't reach the
        # BMC over SSH. The probe persists its own outcome (ready /
        # inactive / unreachable) on the DPU row.
        try:
            from services.rshim_service import RshimService
            RshimService(self.db).probe_status_on_bmc(dpu)
        except Exception as exc:  # noqa: BLE001 — never surface to the user
            logger.debug("BMC rshim probe skipped for DPU %s: %s", dpu.id, exc)

    def _require_dpu(self, project_id: int, dpu_id: int) -> Dpu:
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("Dpu", dpu_id)
        return dpu


def _serialize_inventory(inv: DpuInventory) -> dict[str, Any]:
    """Convert a DpuInventory dataclass (with nested dataclasses) to plain JSON."""
    return dataclasses.asdict(inv)


def _build_tunneled_fetcher(bmc_ip, user, pw, jumphost_cred):
    """Return a fetcher(path) → dict callable for BluefieldRedfishProbe
    that routes each GET through the project jumphost.

    Handles Nvidia-default-password fallback on 401 the same way
    BluefieldRedfishProbe._default_fetcher does, plus auto-rotation when
    the BMC is still in factory state and returns 403 PasswordChangeRequired.
    """
    import json

    from services.dpu_credentials import (
        NVIDIA_DEFAULT_BMC_PASSWORD,
        patch_bmc_root_password,
    )

    state = {"tried_default": False, "rotated_default": False, "effective_pw": pw}

    def fetch(path: str) -> dict[str, Any]:
        r = bmc_http_request(
            bmc_ip=bmc_ip, method="GET", path=path,
            auth=(user, state["effective_pw"]),
            jumphost_cred=jumphost_cred, timeout=10,
        )
        if r is None:
            raise ProbeError(f"Redfish GET via jumphost failed: {path}")
        status, body = r
        if (
            status == 401
            and state["effective_pw"] != NVIDIA_DEFAULT_BMC_PASSWORD
            and not state["tried_default"]
        ):
            state["tried_default"] = True
            state["effective_pw"] = NVIDIA_DEFAULT_BMC_PASSWORD
            r = bmc_http_request(
                bmc_ip=bmc_ip, method="GET", path=path,
                auth=(user, NVIDIA_DEFAULT_BMC_PASSWORD),
                jumphost_cred=jumphost_cred, timeout=10,
            )
            if r is None:
                raise ProbeError(f"Redfish GET via jumphost failed (fallback): {path}")
            status, body = r

            # Factory-fresh BMC: 0penBmc auth was accepted but the BMC refuses
            # to serve any resource until the password is rotated. PATCH to
            # the configured password and retry once.
            if (
                status == 403
                and _body_says_pcr(body)
                and pw != NVIDIA_DEFAULT_BMC_PASSWORD
                and not state["rotated_default"]
            ):
                state["rotated_default"] = True
                logger.info(
                    "BlueField %s requires password change; PATCHing root from "
                    "default to configured password",
                    bmc_ip,
                )
                try:
                    patch_bmc_root_password(
                        bmc_ip, user,
                        NVIDIA_DEFAULT_BMC_PASSWORD, pw,
                        jumphost_cred=jumphost_cred,
                    )
                except Exception as exc:  # noqa: BLE001 — best-effort
                    logger.warning(
                        "Auto-rotate of BMC %s root password failed: %s", bmc_ip, exc,
                    )
                else:
                    state["effective_pw"] = pw
                    r = bmc_http_request(
                        bmc_ip=bmc_ip, method="GET", path=path,
                        auth=(user, pw),
                        jumphost_cred=jumphost_cred, timeout=10,
                    )
                    if r is None:
                        raise ProbeError(
                            f"Redfish GET via jumphost failed after rotate: {path}"
                        )
                    status, body = r
        if status >= 400:
            raise ProbeError(f"HTTP {status} on {path}")
        try:
            return json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise ProbeError(f"Malformed JSON on {path}: {exc}") from exc

    return fetch


def _body_says_pcr(body: bytes) -> bool:
    """True when a Redfish error body carries `PasswordChangeRequired`."""
    import json
    if not body:
        return False
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError):
        return False
    if not isinstance(payload, dict):
        return False

    def _has_pcr(d: dict) -> bool:
        mid = d.get("MessageId")
        return isinstance(mid, str) and "PasswordChangeRequired" in mid

    if _has_pcr(payload):
        return True
    info = payload.get("@Message.ExtendedInfo")
    if isinstance(info, list):
        for entry in info:
            if isinstance(entry, dict) and _has_pcr(entry):
                return True
    err = payload.get("error")
    if isinstance(err, dict):
        if _has_pcr(err):
            return True
        nested = err.get("@Message.ExtendedInfo")
        if isinstance(nested, list):
            for entry in nested:
                if isinstance(entry, dict) and _has_pcr(entry):
                    return True
    return False


def _pick_oob_interface(interfaces: list) -> Any:
    """Pick the interface that represents the OOB management NIC.

    BSP variation: newer builds expose a dedicated `oob0` alongside `eth0`
    and `eth1`; older builds only expose `eth0` which IS the OOB NIC.
    Preference order:
      1. A dedicated `oob0` interface, if present.
      2. `eth0` — on BSPs without `oob0`, this is the OOB NIC.
      3. The first interface in the list as a last resort.
    """
    by_name = {i.name: i for i in interfaces if i.name}
    if "oob0" in by_name:
        return by_name["oob0"]
    if "eth0" in by_name:
        return by_name["eth0"]
    return interfaces[0] if interfaces else None
