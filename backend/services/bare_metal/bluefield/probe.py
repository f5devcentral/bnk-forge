"""BlueField DPU Redfish probe.

Single-shot read-only probe that populates `DpuInventory` from a live DPU
BMC or from injected fixtures (tests). Non-existent resources are tolerated
— optional sub-resources set their fields to None rather than failing.
"""

import logging
from collections.abc import Callable
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from services.bare_metal.bluefield.inventory import (
    DpuBiosSettings,
    DpuFirmware,
    DpuInterface,
    DpuInventory,
    DpuManagerInfo,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15

Fetcher = Callable[[str], dict[str, Any]]

# Firmware inventory resource ids to probe. Absent ids are silently skipped.
_FIRMWARE_IDS = [
    "BMC_Firmware",
    "DPU_BSP",
    "DPU_OS",
    "DPU_UEFI",
    "DPU_ATF",
    "DPU_NIC",
    "DPU_OFED",
    "Bluefield_FW_ERoT",
    "DPU_BOARD",
]


class ProbeError(Exception):
    """Raised when the probe cannot establish enough state to return an inventory."""


# Nvidia-shipped default BMC password on fresh BlueField-3 DPUs. We try this
# after the user-supplied password fails auth so freshly-provisioned DPUs
# discovered via the Discovery tab can be probed without the user having to
# know that fact.
NVIDIA_DEFAULT_BMC_PASSWORD = "0penBmc"


class BluefieldRedfishProbe:
    """Probe a BlueField DPU's embedded Redfish service.

    Inject `fetcher` in tests to bypass HTTP; production callers use the
    default live-HTTP fetcher.
    """

    def __init__(
        self,
        bmc_ip: str,
        username: str,
        password: str,
        *,
        verify_tls: bool = False,
        timeout: int = DEFAULT_TIMEOUT,
        fetcher: Fetcher | None = None,
    ):
        self.bmc_ip = bmc_ip
        self.username = username
        self.password = password
        self.verify_tls = verify_tls
        self.timeout = timeout
        self._fetcher = fetcher or self._default_fetcher
        self._session: requests.Session | None = None
        self._tried_default_password = False
        self._rotated_default_password = False

    def probe(self) -> DpuInventory:
        """Run the probe and return a populated DpuInventory."""
        inv = DpuInventory()

        root = self._safe_get("/redfish/v1/")
        if root is None:
            raise ProbeError(f"Redfish service root unreachable at {self.bmc_ip}")
        inv.redfish_version = root.get("RedfishVersion")
        inv.redfish_product = root.get("Product")
        inv.raw["/redfish/v1/"] = root

        self._populate_system(inv)
        self._populate_interfaces(inv)
        self._populate_bios(inv)
        self._populate_manager(inv)
        self._populate_firmware(inv)

        return inv

    # ── Section populators ──────────────────────────────────────────────────

    def _populate_system(self, inv: DpuInventory) -> None:
        sys_obj = self._safe_get("/redfish/v1/Systems/Bluefield")
        if sys_obj is None:
            return
        inv.manufacturer = sys_obj.get("Manufacturer")
        inv.model = sys_obj.get("Model")
        inv.serial_number = sys_obj.get("SerialNumber")
        inv.part_number = sys_obj.get("PartNumber")
        inv.uuid = sys_obj.get("UUID")
        inv.power_state = sys_obj.get("PowerState")
        inv.system_health = (sys_obj.get("Status") or {}).get("Health")
        inv.raw["/redfish/v1/Systems/Bluefield"] = sys_obj

    def _populate_interfaces(self, inv: DpuInventory) -> None:
        # BSP variation: newer builds list interfaces under the System; older
        # builds only expose them under the Manager. Try System first; fall
        # back to Manager if the collection is empty.
        collection_paths = [
            "/redfish/v1/Systems/Bluefield/EthernetInterfaces",
            "/redfish/v1/Managers/Bluefield_BMC/EthernetInterfaces",
        ]
        for coll_path in collection_paths:
            coll = self._safe_get(coll_path)
            if coll is None:
                continue
            members = coll.get("Members") or []
            if not members:
                continue
            for member in members:
                odata_id = member.get("@odata.id")
                if not odata_id:
                    continue
                iface = self._safe_get(odata_id)
                if iface is None:
                    continue
                inv.interfaces.append(_parse_interface(iface))
                inv.raw[odata_id] = iface
            inv.raw[coll_path] = coll
            if inv.interfaces:
                return  # stop once we have data

    def _populate_bios(self, inv: DpuInventory) -> None:
        bios = self._safe_get("/redfish/v1/Systems/Bluefield/Bios")
        if bios is None:
            return
        attrs = bios.get("Attributes") or {}
        inv.bios = DpuBiosSettings(
            host_privilege_level=attrs.get("HostPrivilegeLevel"),
            nic_mode=attrs.get("NicMode"),
            internal_cpu_model=attrs.get("InternalCPUModel"),
            field_mode=attrs.get("FieldMode"),
            enable_smmu=attrs.get("EnableSMMU"),
            enable_optee=attrs.get("EnableOPTEE"),
            disable_pcie=attrs.get("DisablePCIe"),
            boot_partition_protection=attrs.get("BootPartitionProtection"),
            spcr_uart=attrs.get("SPCR_UART"),
            uefi_args=attrs.get("UefiArgs"),
            os_args=attrs.get("OsArgs"),
            all_attributes=attrs,
        )
        inv.raw["/redfish/v1/Systems/Bluefield/Bios"] = bios

    def _populate_manager(self, inv: DpuInventory) -> None:
        mgr = self._safe_get("/redfish/v1/Managers/Bluefield_BMC")
        if mgr is None:
            return
        oem = ((mgr.get("Oem") or {}).get("Nvidia") or {})
        inv.manager = DpuManagerInfo(
            firmware_version=mgr.get("FirmwareVersion"),
            manager_type=mgr.get("ManagerType"),
            model=mgr.get("Model"),
            health=(mgr.get("Status") or {}).get("Health"),
            datetime=mgr.get("DateTime"),
            otp_provisioned=oem.get("OTPProvisioned"),
        )
        inv.raw["/redfish/v1/Managers/Bluefield_BMC"] = mgr

    def _populate_firmware(self, inv: DpuInventory) -> None:
        fw = DpuFirmware(redfish_spec=inv.redfish_version)
        field_by_id = {
            "BMC_Firmware": "bmc",
            "DPU_BSP": "bsp",
            "DPU_OS": "os_image",
            "DPU_UEFI": "uefi",
            "DPU_ATF": "atf",
            "DPU_NIC": "nic",
            "DPU_OFED": "ofed",
            "Bluefield_FW_ERoT": "erot",
            "DPU_BOARD": "board",
        }
        for fw_id in _FIRMWARE_IDS:
            entry = self._safe_get(f"/redfish/v1/UpdateService/FirmwareInventory/{fw_id}")
            if entry is None:
                continue
            inv.raw[f"/redfish/v1/UpdateService/FirmwareInventory/{fw_id}"] = entry
            version = entry.get("Version")
            attr = field_by_id.get(fw_id)
            if attr and version is not None:
                setattr(fw, attr, version)
        inv.firmware = fw

    # ── HTTP plumbing ───────────────────────────────────────────────────────

    def _safe_get(self, path: str) -> dict[str, Any] | None:
        try:
            data = self._fetcher(path)
        except Exception as exc:
            logger.debug("Redfish GET %s failed: %s", path, exc)
            return None
        # Redfish returns errors as a payload with an "error" key.
        if isinstance(data, dict) and "error" in data and "@odata.id" not in data:
            return None
        return data

    def _default_fetcher(self, path: str) -> dict[str, Any]:
        if self._session is None:
            self._session = self._build_session(self._effective_password())
        url = f"https://{self.bmc_ip}{path}"
        resp = self._session.get(url, timeout=self.timeout)

        # If the user-supplied password failed auth, try the Nvidia default
        # and rebuild the session. Only attempted once per probe.
        if (
            resp.status_code == 401
            and self.password != NVIDIA_DEFAULT_BMC_PASSWORD
            and not self._tried_default_password
        ):
            logger.info(
                "Redfish 401 on %s with user-supplied password; retrying with "
                "Nvidia BF3 default password",
                self.bmc_ip,
            )
            self._tried_default_password = True
            self._session = self._build_session(NVIDIA_DEFAULT_BMC_PASSWORD)
            resp = self._session.get(url, timeout=self.timeout)

            # Factory-fresh BMC: 0penBmc auth was accepted but the BMC refuses
            # to serve any resource until the password is rotated. PATCH the
            # configured password and retry once with it.
            if (
                resp.status_code == 403
                and _response_says_pcr(resp)
                and self.password != NVIDIA_DEFAULT_BMC_PASSWORD
                and not self._rotated_default_password
            ):
                self._rotated_default_password = True
                if self._rotate_root_password_from_default():
                    self._session = self._build_session(self.password)
                    resp = self._session.get(url, timeout=self.timeout)

        resp.raise_for_status()
        return resp.json()

    def _rotate_root_password_from_default(self) -> bool:
        """PATCH BMC root account from 0penBmc to the configured password.

        Returns True when the PATCH appeared to succeed. Best-effort: a
        failure is logged and we keep the old session so the caller still
        gets a clean 403 from the next GET.
        """
        from services.dpu_credentials import patch_bmc_root_password

        logger.info(
            "BlueField %s requires password change; PATCHing root from default "
            "to configured password",
            self.bmc_ip,
        )
        try:
            patch_bmc_root_password(
                self.bmc_ip,
                self.username,
                NVIDIA_DEFAULT_BMC_PASSWORD,
                self.password,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — logged + non-fatal
            logger.warning(
                "Auto-rotate of BMC %s root password failed: %s", self.bmc_ip, exc,
            )
            return False

    def _effective_password(self) -> str:
        return self.password

    def _build_session(self, password: str) -> requests.Session:
        s = requests.Session()
        s.auth = HTTPBasicAuth(self.username, password)
        s.verify = self.verify_tls
        s.headers.update({"Accept": "application/json"})
        return s


def _response_says_pcr(resp: requests.Response) -> bool:
    """True when a Redfish HTTP response carries `PasswordChangeRequired`.

    BlueField OpenBMC returns HTTP 403 with this MessageId when the
    factory-default `0penBmc` authenticated but the BMC refuses to serve
    any resource until the password is rotated. The MessageId can sit at
    the top level, under `@Message.ExtendedInfo`, or wrapped inside an
    `error` object — search all three.
    """
    try:
        payload = resp.json()
    except (ValueError, TypeError):
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


def _parse_interface(iface: dict[str, Any]) -> DpuInterface:
    """Map a Redfish EthernetInterface JSON object to a DpuInterface."""
    dhcp_v4 = iface.get("DHCPv4") or {}
    dhcp_v6 = iface.get("DHCPv6") or {}
    status = iface.get("Status") or {}
    return DpuInterface(
        name=iface.get("Id") or "unknown",
        mac=iface.get("MACAddress"),
        link_status=iface.get("LinkStatus"),
        speed_mbps=iface.get("SpeedMbps"),
        mtu=iface.get("MTUSize"),
        interface_enabled=iface.get("InterfaceEnabled"),
        state=status.get("State"),
        dhcp_v4_enabled=dhcp_v4.get("DHCPEnabled"),
        dhcp_v6_mode=dhcp_v6.get("OperatingMode"),
        ipv4_addresses=list(iface.get("IPv4Addresses") or []),
        ipv4_static_addresses=list(iface.get("IPv4StaticAddresses") or []),
        ipv6_addresses=list(iface.get("IPv6Addresses") or []),
        ipv6_default_gateway=iface.get("IPv6DefaultGateway"),
    )
