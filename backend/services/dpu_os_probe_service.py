"""DPU OS probe: find the DPU OS IP via the BMC and verify SSH reach.

"DPU OS" is Nvidia's term for the BlueField-3 main-core Linux (the one
the BFB installs, e.g. Ubuntu 22.04). Distinct from the BMC's OpenBMC
running on a secondary ARMv7 core.

Flow (per DPU):
  1. SSH into the BMC (uses BMC creds with project-default fallback).
  2. Read the BMC's own `eth0` MAC + /24 subnet via `/proc/net/arp` + `ifconfig`.
  3. Ping-sweep the subnet; grep ARP for the BMC_MAC - 1 (the DPU OS uses
     the sequentially-allocated MAC on BlueField-3). Fallback to any
     Mellanox OUI MAC ≠ BMC's.
  4. SSH into the discovered DPU OS IP with the project's default OS creds.
  5. Capture `uname -a` + `ip -br addr show` and record reachability on the DPU.

Non-BF3 DPUs are out of scope (the -1 MAC-delta trick is BlueField-3-specific).
"""

import ipaddress
import logging
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none
from core.errors import BadRequestError, NotFoundError
from models.dpu import Dpu, ProjectDpuSettings
from services.dpu_credentials import patch_bmc_root_password
from services.dpu_tunnel import (
    resolve_project_jumphost_cred,
    ssh_connect_to_bmc,
)

logger = logging.getLogger(__name__)

# Mellanox/Nvidia OUI prefix shared by BMC + ARM interfaces on BlueField-3.
MELLANOX_OUI = "5c:25:73"

# Nvidia-shipped default BMC password on fresh BlueField-3 DPUs. Used as a
# fallback when the user-supplied password fails auth against the BMC.
NVIDIA_DEFAULT_BMC_PASSWORD = "0penBmc"

# SshRunner: (host, user, password, command) → stdout. Raises on connection error.
SshRunner = Callable[[str, str, str, str], str]


class DpuOsProbeError(Exception):
    """Raised when the probe cannot complete (captured on the DPU row)."""


class DpuOsProbeService:
    """Orchestrates the BMC-side ARP sweep + DPU OS SSH verification.

    Inject `bmc_runner` / `os_runner` in tests to bypass SSH.
    """

    def __init__(
        self,
        db: Session,
        *,
        bmc_runner: SshRunner | None = None,
        os_runner: SshRunner | None = None,
    ):
        self.db = db
        self._bmc_runner = bmc_runner or _paramiko_runner
        self._os_runner = os_runner or _paramiko_runner

    # ── Public API ──

    def mark_probing(self, project_id: int, dpu_id: int) -> None:
        """Set dpu_os_status='probing' before the task is enqueued, so the UI
        immediately reflects the in-flight state without waiting for the
        Celery worker to pick up the job."""
        dpu = (
            self.db.query(Dpu)
            .filter(Dpu.id == dpu_id, Dpu.project_id == project_id)
            .first()
        )
        if dpu is None:
            raise NotFoundError("Dpu", dpu_id)
        dpu.dpu_os_status = "probing"
        self.db.flush()

    def run_and_persist(self, dpu_id: int) -> None:
        """Run the probe synchronously and write the result back.

        Never raises — all failures are captured on the DPU row.
        """
        dpu = self.db.get(Dpu, dpu_id)
        if dpu is None:
            logger.error("DPU %s no longer exists, skipping DPU OS probe", dpu_id)
            return

        try:
            self._run(dpu)
        except DpuOsProbeError as exc:
            self._record_failure(dpu, str(exc))
        except Exception as exc:  # noqa: BLE001 — catch everything so DB always reflects outcome
            logger.exception("Unexpected DPU OS probe error for DPU %s", dpu_id)
            self._record_failure(dpu, f"Unexpected error: {exc}")

    # ── Core logic ──

    def _run(self, dpu: Dpu) -> None:
        # In-band DPUs reach their OS over tmfifo on the host — no BMC hop.
        if dpu.access_mode == "in-band":
            self._run_inband(dpu)
            return

        bmc_user, bmc_pw = self._resolve_bmc_credentials(dpu)
        # DPU-OS credentials are OPTIONAL — without them we can still do
        # the ARP sweep + MAC resolution via the BMC, which is the part
        # users care about (finding the DPU's oob_net0 IP). We only skip
        # the SSH-level verification (uname / ip addr / route tables).
        try:
            os_user, os_pw = self._resolve_os_credentials(dpu)
        except BadRequestError:
            os_user, os_pw = None, None

        # Route SSH through the project's jumphost when one is configured.
        # Only overrides the default paramiko runner — test-injected runners
        # stay intact so unit tests don't drag in live SSH.
        jumphost_cred = resolve_project_jumphost_cred(self.db, dpu.project_id)
        self._jumphost_cred_for_patch = jumphost_cred
        if jumphost_cred is not None:
            if self._bmc_runner is _paramiko_runner:
                self._bmc_runner = _make_paramiko_runner_via(jumphost_cred)
            if self._os_runner is _paramiko_runner:
                self._os_runner = _make_paramiko_runner_via(jumphost_cred)

        bmc_mac, subnet = self._fetch_bmc_network_state(dpu.bmc_ip, bmc_user, bmc_pw)
        os_ip, os_mac = self._discover_os_ip(dpu.bmc_ip, bmc_user, bmc_pw, bmc_mac, subnet)

        if os_user and os_pw:
            os_info = self._probe_os(os_ip, os_user, os_pw)
        else:
            # No OS creds — record what the BMC knows about the DPU OS
            # and tell the operator how to enable full verification.
            os_info = {
                "uname": None,
                "hostname": None,
                "ip_addr": None,
                "ip4_route": None,
                "ip6_route": None,
                "ipmitool": None,
                "mlxconfig_settings": {},
                "raw_mlxconfig": "",
                "note": (
                    "DPU OS SSH verification skipped — set 'DPU OS Default "
                    "Username' and 'DPU OS Default Password' in the project "
                    "DPU settings to collect uname / ip addr / routes."
                ),
            }

        self._record_success(dpu, os_ip, os_mac, os_info)

    # ── In-band (via host + tmfifo) ───────────────────────────────────────

    # BlueField-3 + our bf.conf netplan template puts the DPU OS on a /30
    # tmfifo_net0 link whose subnet derives from the rshim index — the
    # host side sits on .1/30 of 192.168.{100+rshim_idx}.0, the DPU on .2.
    # On single-DPU hosts that's 192.168.100.2 (the historical default).

    def _run_inband(self, dpu: Dpu) -> None:
        """Probe the DPU OS via tmfifo: SSH to host → direct-tcpip channel
        to the per-DPU tmfifo IP:22 → SSH as ubuntu through that channel.

        Falls back gracefully when DPU-OS creds aren't set — just records
        reachability without uname / ip addr.
        """
        import paramiko

        from services.bf_conf_renderer import derive_tmfifo_dpu_host
        from services.rshim_service import open_inband_host_ssh

        os_ip = derive_tmfifo_dpu_host(getattr(dpu, "rshim_device", None), dpu=dpu)

        try:
            os_user, os_pw = self._resolve_os_credentials(dpu)
        except BadRequestError:
            os_user, os_pw = None, None

        try:
            host_client = open_inband_host_ssh(self.db, dpu)
        except Exception as exc:  # noqa: BLE001
            raise DpuOsProbeError(
                f"SSH to host {dpu.host_node_ip} failed: {exc}"
            ) from exc

        try:
            transport = host_client.get_transport()
            if transport is None:
                raise DpuOsProbeError("host SSH transport unavailable")
            try:
                channel = transport.open_channel(
                    "direct-tcpip",
                    (os_ip, 22),
                    ("127.0.0.1", 0),
                    timeout=15,
                )
            except Exception as exc:  # noqa: BLE001
                raise DpuOsProbeError(
                    f"DPU OS unreachable at {os_ip} via tmfifo from "
                    f"host {dpu.host_node_ip}: {exc}"
                ) from exc

            if not os_user or not os_pw:
                # tmfifo port is open → DPU OS is up. Record reachability
                # and tell the operator how to unlock the full probe.
                try:
                    channel.close()
                except Exception:
                    pass
                os_info = {
                    "uname": None, "hostname": None,
                    "ip_addr": None, "ip4_route": None, "ip6_route": None,
                    "ipmitool": None,
                    "mlxconfig_settings": {},
                    "raw_mlxconfig": "",
                    "note": (
                        "DPU OS tmfifo port is reachable but SSH verification "
                        "skipped — set DPU OS default username/password in "
                        "project settings."
                    ),
                }
                self._record_success(dpu, os_ip, None, os_info)
                return

            dpu_client = paramiko.SSHClient()
            dpu_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                dpu_client.connect(
                    os_ip,
                    username=os_user, password=os_pw,
                    sock=channel,
                    look_for_keys=False, allow_agent=False, timeout=15,
                )
            except paramiko.ssh_exception.AuthenticationException as exc:
                raise DpuOsProbeError(
                    f"DPU OS SSH auth failed at {os_ip}: {exc}"
                ) from exc
            except Exception as exc:  # noqa: BLE001
                raise DpuOsProbeError(
                    f"DPU OS SSH failed at {os_ip}: {exc}"
                ) from exc

            try:
                # 90s budget covers the 7 sequential ipmitool commands —
                # each invocation can take 1-2s on a slow BMC's KCS bus.
                _stdin, stdout, _stderr = dpu_client.exec_command(
                    _DPU_OS_PROBE_COMMAND,
                    timeout=90,
                )
                out = stdout.read().decode("utf-8", errors="replace")
            finally:
                try:
                    dpu_client.close()
                except Exception:
                    pass

            os_info = _parse_dpu_os_probe_output(out)
            ip_addr = os_info.get("ip_addr") or ""
            os_mac = _extract_oob_mac(ip_addr)
            # Prefer oob_net0's DHCP-assigned IPv4 — that's the routable
            # management address operators care about. tmfifo (the SSH
            # tunnel target this probe used) is host-local and isn't a
            # useful "where is this DPU" answer for the UI. Falls back
            # to tmfifo when oob_net0 hasn't leased an IPv4 yet (link
            # down, no DHCP server, fresh boot before networkd settled).
            display_ip = _extract_oob_ipv4(ip_addr) or os_ip
            self._record_success(dpu, display_ip, os_mac, os_info)
        finally:
            try:
                host_client.close()
            except Exception:
                pass

    def _fetch_bmc_network_state(self, bmc_ip: str, user: str, pw: str) -> tuple[str, str]:
        """Returns (BMC eth0 MAC, subnet in CIDR) via a single SSH call.

        Uses `head -n 3` (POSIX form) because some BlueField BMC BusyBox
        builds strip the `-3` short form — on a worker2 BMC `head -3`
        fails with a usage message while `-n 3` works.
        """
        out = self._bmc_exec(
            bmc_ip, user, pw,
            "cat /sys/class/net/eth0/address; echo ---; ifconfig eth0 | head -n 3",
        )
        parts = out.split("---")
        if len(parts) < 2:
            raise DpuOsProbeError("Failed to read BMC eth0 state — unexpected output")

        bmc_mac = parts[0].strip().lower()
        if not re.match(r"^[0-9a-f:]{17}$", bmc_mac):
            raise DpuOsProbeError(f"Could not parse BMC MAC from output: {parts[0]!r}")

        # BusyBox ifconfig: "inet addr:192.168.68.100  Bcast:...  Mask:255.255.252.0"
        ifconfig_text = parts[1]
        ip_m = re.search(r"inet addr:(\d+\.\d+\.\d+\.\d+)", ifconfig_text)
        mask_m = re.search(r"Mask:(\d+\.\d+\.\d+\.\d+)", ifconfig_text)
        if not ip_m or not mask_m:
            raise DpuOsProbeError("Could not parse BMC IP/Mask from ifconfig output")

        subnet = _ip_mask_to_cidr(ip_m.group(1), mask_m.group(1))
        return bmc_mac, subnet

    def _discover_os_ip(
        self, bmc_ip: str, user: str, pw: str, bmc_mac: str, subnet: str,
    ) -> tuple[str, str]:
        """Sweep the subnet from the BMC and return (os_ip, os_mac)."""
        expected_os_mac = _mac_delta(bmc_mac, -1)

        # Derive /24 IP range to sweep. Subnet may be larger; we scan a /24 window
        # around the BMC's own IP, which is sufficient for BF-3 deployments where
        # BMC and DPU OS always share an L2 segment.
        base = subnet.split("/")[0]
        octets = base.split(".")
        prefix3 = ".".join(octets[:3])

        # Ping sweep. BusyBox `ping -c1 -W1` is universally present.
        sweep_cmd = (
            f"for i in $(seq 1 254); do "
            f"(ping -c 1 -W 1 -q {prefix3}.$i >/dev/null 2>&1) & "
            f"done; wait; "
            f"cat /proc/net/arp"
        )
        arp_table = self._bmc_exec(bmc_ip, user, pw, sweep_cmd)

        candidate_ip = None
        candidate_mac = None
        fallback_ip = None
        fallback_mac = None

        # Scope the search to the BMC's eth0 subnet — the BMC often has
        # secondary interfaces (e.g. vlan4040 on 192.168.240/24 carrying
        # internal SoC traffic) whose neighbors would otherwise poison the
        # Mellanox-OUI fallback.
        try:
            oob_net = ipaddress.ip_network(subnet, strict=False)
        except ValueError:
            oob_net = None

        # BlueField-3 allocates the BMC and DPU-OS MACs from the same NIC,
        # differing ONLY in the last byte. Lock the fallback to that first-
        # 5-byte prefix so the ARP table of a shared-L2 subnet with multiple
        # DPUs doesn't leak another DPU's OS MAC into this DPU's answer.
        bmc_prefix5 = bmc_mac.rsplit(":", 1)[0]  # first 5 octets, no trailing colon

        for line in arp_table.splitlines()[1:]:  # skip header
            fields = line.split()
            if len(fields) < 6:
                continue
            ip, _type, _flags, mac, _mask, device = (
                fields[0], fields[1], fields[2], fields[3].lower(), fields[4], fields[5]
            )
            if device != "eth0":
                continue  # only the OOB physical interface
            if mac == "00:00:00:00:00:00":
                continue
            if mac == bmc_mac:
                continue  # BMC's own self-entry, if present
            if oob_net is not None:
                try:
                    if ipaddress.ip_address(ip) not in oob_net:
                        continue
                except ValueError:
                    continue
            if mac == expected_os_mac:
                candidate_ip, candidate_mac = ip, mac
                break
            # Fallback: MAC must share the first 5 bytes with the BMC
            # (same physical BF-3) AND carry the Mellanox OUI. Anything
            # else is a sibling DPU's OS or an unrelated Mellanox device
            # on the same L2.
            same_device = mac.startswith(bmc_prefix5 + ":")
            if same_device and mac.startswith(MELLANOX_OUI) and fallback_ip is None:
                fallback_ip, fallback_mac = ip, mac

        if candidate_ip:
            return candidate_ip, candidate_mac
        if fallback_ip:
            logger.info(
                "Using same-device fallback for DPU OS (BMC %s): %s/%s",
                bmc_ip, fallback_ip, fallback_mac,
            )
            return fallback_ip, fallback_mac

        raise DpuOsProbeError(
            f"No DPU OS candidate found on {subnet} "
            f"(expected MAC {expected_os_mac}, or any MAC sharing the BMC's "
            f"first 5 bytes {bmc_prefix5}:XX). The DPU OS may be up but "
            f"without an IP on oob_net0."
        )

    def _probe_os(self, os_ip: str, user: str, pw: str) -> dict[str, Any]:
        """SSH into the DPU OS and capture diagnostic output."""
        try:
            out = self._os_runner(
                os_ip, user, pw,
                _DPU_OS_PROBE_COMMAND,
            )
        except Exception as exc:
            raise DpuOsProbeError(f"DPU OS SSH failed at {os_ip}: {exc}") from exc
        return _parse_dpu_os_probe_output(out)

    # ── BMC SSH with Nvidia-default-password fallback ──

    def _bmc_exec(self, host: str, user: str, password: str, command: str) -> str:
        """SSH into the BMC, falling back to the Nvidia BF3 default password on auth failure.

        On successful fallback the configured password is PATCHed back onto
        the BMC's root account via Redfish (best-effort; failures are logged
        but do not abort the probe).
        """
        try:
            import paramiko
        except ImportError:  # pragma: no cover — paramiko is a hard dep, kept for defensiveness
            paramiko = None  # type: ignore[assignment]

        try:
            return self._bmc_runner(host, user, password, command)
        except Exception as exc:
            if (
                paramiko is not None
                and isinstance(exc, paramiko.ssh_exception.AuthenticationException)
                and password != NVIDIA_DEFAULT_BMC_PASSWORD
            ):
                logger.info(
                    "BMC SSH auth failed for %s with user-supplied password; "
                    "retrying with Nvidia BF3 default password",
                    host,
                )
                result = self._bmc_runner(host, user, NVIDIA_DEFAULT_BMC_PASSWORD, command)
                # Fire the PATCH once; subsequent _bmc_exec calls within the same
                # probe will succeed with the configured password.
                jh = getattr(self, "_jumphost_cred_for_patch", None)
                patch_bmc_root_password(
                    host, user, NVIDIA_DEFAULT_BMC_PASSWORD, password,
                    jumphost_cred=jh,
                )
                return result
            raise

    # ── Credentials ──

    def _resolve_bmc_credentials(self, dpu: Dpu) -> tuple[str, str]:
        user = (
            decrypt_value_or_none(dpu.bmc_username_encrypted)
            if dpu.bmc_username_encrypted else None
        )
        pw = (
            decrypt_value_or_none(dpu.bmc_password_encrypted)
            if dpu.bmc_password_encrypted else None
        )
        if not user or not pw:
            settings = self._project_settings(dpu.project_id)
            if settings:
                if not user:
                    user = settings.default_oob_username
                if not pw and settings.default_oob_password_encrypted:
                    pw = decrypt_value_or_none(settings.default_oob_password_encrypted)
        if pw and not user:
            user = "root"
        if not user or not pw:
            raise BadRequestError("No BMC password configured for DPU OS probe.")
        return user, pw

    def _resolve_os_credentials(self, dpu: Dpu) -> tuple[str, str]:
        settings = self._project_settings(dpu.project_id)
        if settings is None or not settings.default_os_username or not settings.default_os_password_encrypted:
            raise BadRequestError(
                "Project default DPU OS credentials are required for DPU OS probe."
            )
        pw = decrypt_value_or_none(settings.default_os_password_encrypted)
        if not pw:
            raise BadRequestError("Project default DPU OS password could not be decrypted.")
        return settings.default_os_username, pw

    def _project_settings(self, project_id: int) -> ProjectDpuSettings | None:
        return (
            self.db.query(ProjectDpuSettings)
            .filter(ProjectDpuSettings.project_id == project_id)
            .first()
        )

    # ── Persistence ──

    def _record_success(
        self, dpu: Dpu, os_ip: str, os_mac: str, os_info: dict[str, Any],
    ) -> None:
        dpu.dpu_os_ip = os_ip
        dpu.dpu_os_reachable = True
        dpu.dpu_os_status = "reachable"
        # Merge into last_discovery_payload under a `dpu_os` key so the
        # existing inventory-detail view surfaces it alongside Redfish data.
        payload = dict(dpu.last_discovery_payload) if dpu.last_discovery_payload else {}
        payload["dpu_os"] = {
            "ip": os_ip,
            "mac": os_mac,
            "uname": os_info.get("uname"),
            "hostname": os_info.get("hostname"),
            "ip_addr": os_info.get("ip_addr"),
            "ip4_route": os_info.get("ip4_route"),
            "ip6_route": os_info.get("ip6_route"),
            # Raw output of every `sudo ipmitool …` we ran on the DPU OS,
            # keyed by sub-command. Lets the operator confirm BMC LAN
            # config, FRU identity, sensors, and SDR state without a
            # second BMC SSH hop. Keys: sdr_type_temperature, mc_info,
            # fru_0, fru_1, sensor_list, sdr_elist, lan_print.
            "ipmitool": os_info.get("ipmitool"),
            # Phase 3 diagnostics: chosen-uplink + internet ping + DNS
            # resolution against the DPU's configured resolver, plus
            # bond0 / per-uplink link state. Each is optional so the
            # UI can quietly hide unavailable sections.
            "connectivity": os_info.get("connectivity"),
            "lacp": os_info.get("lacp"),
            # DOCA bundle / NIC FW / BMC FW captured from inside the
            # DPU OS — surfaced inline on the DPU page so operators
            # don't have to expand the inventory detail or hunt across
            # the Discovery tab to find them.
            "firmware": os_info.get("firmware"),
            # Interface name list from `ip -br addr show` — used by the
            # e2e harness to wait for bf.conf-driven VLAN sub-interfaces
            # (external/internal/extras) to come up before running the
            # post-flash matrix. None when the probe couldn't collect
            # `ip_addr` (auth-skipped path).
            "interfaces": os_info.get("interfaces"),
            "probed_at": datetime.utcnow().isoformat() + "Z",
        }
        # Carry over the explanatory note when SSH verification was skipped.
        if os_info.get("note"):
            payload["dpu_os"]["note"] = os_info["note"]
        # Promote mlxconfig settings to the top level so `_derive_link_mode`
        # (which the DpuResponse / IB chip / Convert action all consult)
        # picks them up for BMC DPUs the same way it does for in-band ones.
        # Empty dict means the probe ran but mlxconfig wasn't available
        # (e.g. MFT not installed on the DPU OS) — leave the existing
        # value in place so we don't blank out a previously-good capture.
        new_mlx = (os_info.get("mlxconfig_settings") or {})
        if new_mlx:
            existing = payload.get("mlxconfig_settings") or {}
            payload["mlxconfig_settings"] = {**existing, **new_mlx}
        # Stash the raw output too — the inventory panel's mlxconfig
        # forensic block already renders this key when present.
        if os_info.get("raw_mlxconfig"):
            payload["raw_mlxconfig"] = os_info["raw_mlxconfig"]
        dpu.last_discovery_payload = payload
        # Promote the BMC IP parsed out of `ipmitool lan print` onto the
        # DPU row when we don't already have one. In-band DPUs register
        # without a BMC IP (host-side rshim/lspci doesn't see it); the
        # BlueField BMC's own LAN config does. Don't overwrite an
        # already-set address — that would clobber the BMC-mode probe's
        # source of truth.
        if not dpu.bmc_ip:
            ipmi_lan = (os_info.get("ipmitool") or {}).get("lan_print") or ""
            parsed_bmc = _parse_ipmitool_lan_for_ip(ipmi_lan)
            if parsed_bmc:
                dpu.bmc_ip = parsed_bmc
                logger.info(
                    "DPU OS probe set bmc_ip=%s on dpu=%s (from ipmitool lan print)",
                    parsed_bmc, dpu.id,
                )
        self.db.commit()
        logger.info("DPU OS probe ok: dpu=%s ip=%s mac=%s", dpu.id, os_ip, os_mac)

    def _record_failure(self, dpu: Dpu, error: str) -> None:
        dpu.dpu_os_reachable = False
        # Distinguish "probe completed but host not reachable" from "probe itself
        # blew up". Both are fine to retry; the UI just surfaces the message.
        dpu.dpu_os_status = "unreachable" if "No DPU OS candidate" in error else "error"
        payload = dict(dpu.last_discovery_payload) if dpu.last_discovery_payload else {}
        payload["dpu_os"] = {
            "error": error,
            "probed_at": datetime.utcnow().isoformat() + "Z",
        }
        dpu.last_discovery_payload = payload
        self.db.commit()
        logger.warning("DPU OS probe failed: dpu=%s error=%s", dpu.id, error)


# ── Helpers ────────────────────────────────────────────────────────────────

# Shared probe command used by both the BMC and in-band paths.
#
# Bundles every diagnostic into a single SSH round-trip. The shell echoes a
# `=== <key> ===` marker before each command's output so the parser can
# slice the result reliably even when output contains stray `---` lines.
#
# OS info: uname, hostname, ip addr (also feeds oob_net0 MAC extraction),
# v4/v6 routes. ipmitool sub-commands: temperature sensors first (most
# operationally interesting), then MC info, FRU 0/1, full sensor list,
# SDR elist, LAN config. Every ipmitool invocation runs under `sudo -n`
# (so we fail fast instead of waiting for a tty prompt) with `2>&1 || true`
# (so a missing tool, BMC error, or non-existent FRU 1 lands on stdout
# rather than aborting the rest of the probe).
_DPU_OS_PROBE_COMMAND = "\n".join([
    "echo === uname ===",
    "uname -a",
    "echo === hostname ===",
    "hostname",
    "echo === ip_addr ===",
    "ip -br addr show 2>/dev/null || ifconfig",
    "echo === ip4_route ===",
    "ip -4 route 2>/dev/null",
    "echo === ip6_route ===",
    "ip -6 route 2>/dev/null",
    # ── Connectivity diagnostics (Phase 3) ────────────────────────────
    # `ip route get <pub>` returns one line like
    #   8.8.8.8 via 192.168.68.1 dev p0 src 192.168.68.96 uid 0
    # which we parse to discover the chosen uplink + next hop.
    # Pings are short (-c 2 -W 2) so a dead link doesn't stretch the
    # probe — `_parse_ping_output` already handles 0% loss + RTT
    # extraction. `getent hosts f5.com` exercises the DPU's
    # configured resolver (per /etc/resolv.conf), which is what the
    # operator actually cares about ("does name resolution work
    # _here_"), independent of upstream public-DNS reachability.
    "echo === route_v4_pub ===",
    "ip route get 8.8.8.8 2>&1 || true",
    "echo === ping_v4_pub ===",
    "ping -c 2 -W 2 8.8.8.8 2>&1 || true",
    "echo === dns_f5 ===",
    "getent hosts f5.com 2>&1 || true",
    # ── LACP / link diagnostics (Phase 3) ─────────────────────────────
    # Reading /proc/net/bonding/bond0 gives the kernel's authoritative
    # view of mode (802.3ad vs others), LACP rate (fast/slow), and
    # per-slave MII Status / Speed / Duplex / Link Failure Count.
    # Falling back to ethtool per-uplink covers the no-LAG template
    # case where p0 / p1 are direct.
    "echo === bond0 ===",
    "cat /proc/net/bonding/bond0 2>/dev/null || true",
    "echo === ethtool_p0 ===",
    "ethtool p0 2>&1 || true",
    "echo === ethtool_p1 ===",
    "ethtool p1 2>&1 || true",
    # ── Firmware versions captured from inside the DPU OS ──────────────
    # `cat /etc/mlnx-release` returns the DOCA / bf-bundle string
    # (e.g. `bf-bundle-3.1.0-76_25.07_ubuntu-22.04_prod`).
    # `ethtool -i p0` exposes the NIC firmware-version line. BMC
    # firmware is parsed out of the existing `ipmi_mc` block (ipmitool
    # talks to the local BMC over IPMB so no extra SSH command is
    # needed for that one).
    "echo === mlnx_release ===",
    "cat /etc/mlnx-release 2>/dev/null || true",
    "echo === ethtool_drv_p0 ===",
    "ethtool -i p0 2>&1 || true",
    "echo === ipmi_sdr_temp ===",
    "sudo -n ipmitool sdr type Temperature 2>&1 || true",
    "echo === ipmi_mc ===",
    "sudo -n ipmitool mc info 2>&1 || true",
    "echo === ipmi_fru0 ===",
    "sudo -n ipmitool fru print 0 2>&1 || true",
    "echo === ipmi_fru1 ===",
    "sudo -n ipmitool fru print 1 2>&1 || true",
    "echo === ipmi_sensor ===",
    "sudo -n ipmitool sensor list 2>&1 || true",
    "echo === ipmi_sdr ===",
    "sudo -n ipmitool sdr elist 2>&1 || true",
    "echo === ipmi_lan ===",
    "sudo -n ipmitool lan print 2>&1 || true",
    # mlxconfig captures LINK_TYPE_P1/P2 (and every other firmware
    # setting) so the IB-mode badge + Convert-to-Ethernet action work
    # for BMC DPUs too — without this section the link mode is only
    # known for in-band DPUs (whose host's rshim probe runs mlxconfig
    # directly). DPU OS sees only its own NIC silicon, so the first
    # `pciconf0` entry is unambiguous; no per-host BDF disambiguation
    # needed. `mst start` is idempotent on running modules.
    "echo === mlx_q ===",
    "sudo -n mst start 2>&1 || true",
    "MSTDEV=$(ls /dev/mst/mt*pciconf0 2>/dev/null | head -1); "
    "[ -n \"$MSTDEV\" ] && sudo -n mlxconfig -d \"$MSTDEV\" q 2>&1 "
    "|| echo '(no MST device — MFT not installed?)'",
])


_SECTION_HEADER_RE = re.compile(r"^=== (\S+) ===\s*$")


def _split_marked_sections(out: str) -> dict[str, str]:
    """Slice probe output by `=== <key> ===` headers.

    Output before the first header is dropped (login banners on shells
    that send any). Sections preserve internal blank lines but the
    leading/trailing whitespace on the section body is trimmed.
    """
    sections: dict[str, str] = {}
    current_key: str | None = None
    buf: list[str] = []
    for line in out.splitlines():
        m = _SECTION_HEADER_RE.match(line)
        if m:
            if current_key is not None:
                sections[current_key] = "\n".join(buf).strip()
            current_key = m.group(1)
            buf = []
        elif current_key is not None:
            buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(buf).strip()
    return sections


def _parse_mlxconfig_settings(raw: str) -> dict[str, str]:
    """Extract the `Configurations` block of `mlxconfig … q` into a dict.

    `mlxconfig` output is roughly:

        Device #1:
        ----------
        Device type:   BlueField3
        ...
        Configurations:                  Next Boot
                LINK_TYPE_P1                 ETH(2)
                LINK_TYPE_P2                 ETH(2)
                INTERNAL_CPU_MODEL           EMBEDDED_CPU(1)
                ...

    Every line under `Configurations:` that looks like
    `<TOKEN> <value>` becomes an entry. Anything before the
    `Configurations:` header is ignored.
    """
    import re as _re

    settings: dict[str, str] = {}
    in_block = False
    for line in raw.splitlines():
        if _re.match(r"^\s*Configurations:\s", line):
            in_block = True
            continue
        if not in_block:
            continue
        m = _re.match(r"^\s+([A-Z][A-Z0-9_]+)\s+(\S.*?)\s*$", line)
        if m:
            settings[m.group(1)] = m.group(2)
    return settings


def _parse_dpu_os_probe_output(out: str) -> dict[str, Any]:
    """Parse the marked output of `_DPU_OS_PROBE_COMMAND`.

    Missing sections (older probes, SSH truncation, ipmitool absent) are
    represented as empty strings / empty dicts so downstream callers
    don't have to null-check every key.
    """
    s = _split_marked_sections(out)
    raw_mlx = s.get("mlx_q", "")
    return {
        "uname": s.get("uname", ""),
        "hostname": s.get("hostname", ""),
        "ip_addr": s.get("ip_addr", ""),
        "ip4_route": s.get("ip4_route", ""),
        "ip6_route": s.get("ip6_route", ""),
        "ipmitool": {
            # Order intentional — UI renders sub-sections in this order,
            # with the temperature reading surfaced first.
            "sdr_type_temperature": s.get("ipmi_sdr_temp", ""),
            "mc_info": s.get("ipmi_mc", ""),
            "fru_0": s.get("ipmi_fru0", ""),
            "fru_1": s.get("ipmi_fru1", ""),
            "sensor_list": s.get("ipmi_sensor", ""),
            "sdr_elist": s.get("ipmi_sdr", ""),
            "lan_print": s.get("ipmi_lan", ""),
        },
        # Raw mlxconfig output goes alongside the ipmitool blob for the
        # forensic viewer; the parsed dict drives the IB-mode badge.
        "raw_mlxconfig": raw_mlx,
        "mlxconfig_settings": _parse_mlxconfig_settings(raw_mlx),
        # Phase 3 diagnostics — connectivity + LACP/link.
        "connectivity": _parse_connectivity_sections(
            route_pub=s.get("route_v4_pub", ""),
            ping_pub=s.get("ping_v4_pub", ""),
            dns_f5=s.get("dns_f5", ""),
        ),
        "lacp": _parse_lacp_sections(
            bond0=s.get("bond0", ""),
            ethtool_p0=s.get("ethtool_p0", ""),
            ethtool_p1=s.get("ethtool_p1", ""),
        ),
        "firmware": _parse_firmware_section(
            mlnx_release=s.get("mlnx_release", ""),
            ethtool_drv_p0=s.get("ethtool_drv_p0", ""),
            ipmi_mc=s.get("ipmi_mc", ""),
        ),
        # Names of every interface the kernel knows about, parsed off
        # `ip -br addr show`. Lets callers wait for bf.conf-driven VLAN
        # sub-interfaces (`external0`, `internal100`, …) to come up
        # post-flash before pinging through them — `dpu_os_status=
        # reachable` only means SSH works, not that netplan finished
        # bringing up everything bf.conf rendered.
        "interfaces": _parse_interface_names(s.get("ip_addr", "")),
    }


def _parse_interface_names(ip_addr_text: str) -> list[str]:
    """Pull interface names out of `ip -br addr show` output.

    Each non-blank line starts with the interface name followed by
    whitespace; we tolerate the rare `@parent` link suffix
    (`vlan100@bond0`). Returns names in the order the kernel listed
    them, deduplicated.
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in (ip_addr_text or "").splitlines():
        token = line.split(maxsplit=1)[:1]
        if not token:
            continue
        name = token[0].split("@", 1)[0]
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


# ── Phase 3 parsers: connectivity + LACP/link diagnostics ─────────────────

_ROUTE_GET_RE = re.compile(
    r"^\S+(?:\s+via\s+(?P<via>\S+))?\s+dev\s+(?P<dev>\S+)(?:\s+src\s+(?P<src>\S+))?",
)


def _parse_route_get(out: str) -> dict[str, str | None]:
    """Extract chosen interface, next hop, and src from `ip route get` output.

    Sample input: ``8.8.8.8 via 192.168.68.1 dev p0 src 192.168.68.96 uid 0``.
    Direct-attached destinations omit `via` (returns next_hop=None).
    """
    for line in (out or "").splitlines():
        line = line.strip()
        if not line or line.startswith("RTNETLINK") or "Network is unreachable" in line:
            continue
        m = _ROUTE_GET_RE.match(line)
        if m:
            return {
                "via_interface": m.group("dev"),
                "next_hop": m.group("via"),
                "src": m.group("src"),
            }
    return {"via_interface": None, "next_hop": None, "src": None}


def _parse_getent_hosts(out: str) -> dict[str, Any]:
    """Parse `getent hosts <name>` output.

    Successful lookup returns one or more ``<address>  <name> [<aliases>...]``
    lines; failure is empty stdout (getent exits non-zero, but we run with
    `|| true` so we just see no output).
    """
    addresses: list[str] = []
    for line in (out or "").splitlines():
        parts = line.strip().split()
        if not parts:
            continue
        addr = parts[0]
        # IPv4 / IPv6 sniff — anything else is an error message we ignore.
        if ":" in addr or addr.count(".") == 3:
            addresses.append(addr)
    return {
        "ok": bool(addresses),
        "addresses": addresses,
        "error": None if addresses else (out.strip() or "no addresses returned"),
    }


def _parse_connectivity_sections(
    *, route_pub: str, ping_pub: str, dns_f5: str,
) -> dict[str, Any]:
    """Combine the three connectivity probes into a single structured view."""
    route = _parse_route_get(route_pub)
    # Ping rc isn't captured separately here — derive it from output: a
    # successful run prints ``rtt min/avg/max``, every other case is a
    # failure. _parse_ping_output already encodes that.
    ping_ok = "rtt" in (ping_pub or "") and "min/avg/max" in (ping_pub or "")
    rtt_ms: float | None = None
    if ping_ok:
        # Report `min` so a first-packet ARP outlier doesn't dominate.
        m = re.search(r"rtt[^=]*=\s*([0-9.]+)/", ping_pub)
        if m:
            rtt_ms = float(m.group(1))
    return {
        "internet": {
            "target": "8.8.8.8",
            "via_interface": route["via_interface"],
            "next_hop": route["next_hop"],
            "src": route["src"],
            "ping_ok": ping_ok,
            "rtt_ms": rtt_ms,
        },
        "dns": {"f5_com": _parse_getent_hosts(dns_f5)},
    }


def _parse_bond0(text: str) -> dict[str, Any] | None:
    """Parse `/proc/net/bonding/bond0` into mode + LACP rate + slaves.

    Returns None when the bond file isn't present (bond0 missing → empty
    text). The kernel format is well-known and stable across recent
    versions; we look for these keys:

        Bonding Mode: <text>
        LACP rate: fast | slow
        Slave Interface: <name>
        MII Status: up | down
        Speed: <text>
        Duplex: full | half
        Link Failure Count: <int>

    Slaves are emitted in section blocks separated by ``Slave Interface:``.
    """
    if not (text or "").strip():
        return None
    mode: str | None = None
    lacp_rate: str | None = None
    members: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Bonding Mode:"):
            mode = line.split(":", 1)[1].strip()
        elif line.startswith("LACP rate:") or line.startswith("LACP active:"):
            # Some kernels print "LACP active: on" + "LACP rate: fast";
            # keep the rate one, fall through if we already have it.
            if line.startswith("LACP rate:"):
                lacp_rate = line.split(":", 1)[1].strip()
        elif line.startswith("Slave Interface:"):
            if current is not None:
                members.append(current)
            current = {
                "name": line.split(":", 1)[1].strip(),
                "mii_status": None,
                "speed": None,
                "duplex": None,
                "link_failure_count": None,
            }
        elif current is not None and line.startswith("MII Status:"):
            current["mii_status"] = line.split(":", 1)[1].strip()
        elif current is not None and line.startswith("Speed:"):
            current["speed"] = line.split(":", 1)[1].strip()
        elif current is not None and line.startswith("Duplex:"):
            current["duplex"] = line.split(":", 1)[1].strip()
        elif current is not None and line.startswith("Link Failure Count:"):
            try:
                current["link_failure_count"] = int(
                    line.split(":", 1)[1].strip(),
                )
            except ValueError:
                pass
    if current is not None:
        members.append(current)
    return {
        "bond_present": True,
        "bond_name": "bond0",
        "mode": mode,
        "lacp_rate": lacp_rate,
        "members": members,
    }


_ETHTOOL_SPEED_RE = re.compile(r"^\s*Speed:\s*(.+?)\s*$")
_ETHTOOL_DUPLEX_RE = re.compile(r"^\s*Duplex:\s*(.+?)\s*$")
_ETHTOOL_LINK_RE = re.compile(r"^\s*Link detected:\s*(yes|no)\s*$", re.I)


def _parse_ethtool(text: str) -> dict[str, str | None]:
    """Extract Speed / Duplex / Link detected from `ethtool <iface>` output.

    Missing fields stay `None`; the caller decides whether the row is
    worth showing.
    """
    speed: str | None = None
    duplex: str | None = None
    link: str | None = None
    for line in (text or "").splitlines():
        m = _ETHTOOL_SPEED_RE.match(line)
        if m:
            speed = m.group(1)
            continue
        m = _ETHTOOL_DUPLEX_RE.match(line)
        if m:
            duplex = m.group(1)
            continue
        m = _ETHTOOL_LINK_RE.match(line)
        if m:
            link = m.group(1).lower()
    return {"speed": speed, "duplex": duplex, "link": link}


_IPMITOOL_LAN_IP_RE = re.compile(
    r"^\s*IP Address\s*:\s*(\d+\.\d+\.\d+\.\d+)\s*$", re.MULTILINE,
)


def _parse_ipmitool_lan_for_ip(text: str) -> str | None:
    """Pull the BMC's IP out of `sudo ipmitool lan print` output.

    The relevant line is:

        IP Address              : 192.168.x.y

    Returns `None` for unset/link-local/uninitialised values
    (`0.0.0.0`, `169.254.*`) so we never persist garbage.
    """
    if not text:
        return None
    m = _IPMITOOL_LAN_IP_RE.search(text)
    if not m:
        return None
    ip = m.group(1)
    if ip == "0.0.0.0" or ip.startswith("169.254."):
        return None
    return ip


def _parse_lacp_sections(
    *, bond0: str, ethtool_p0: str, ethtool_p1: str,
) -> dict[str, Any]:
    """LAG → bond0 details. No LAG → per-uplink ethtool snapshot."""
    bond = _parse_bond0(bond0)
    if bond is not None:
        return bond
    uplinks: list[dict[str, Any]] = []
    for name, raw in (("p0", ethtool_p0), ("p1", ethtool_p1)):
        et = _parse_ethtool(raw)
        # Only surface the uplink when ethtool said something — a missing
        # interface yields the unhelpful "Settings for p0: Cannot get
        # device settings: No such device" line.
        if any(et.values()):
            uplinks.append({"name": name, **et})
    return {"bond_present": False, "uplinks": uplinks}


# ── Firmware version parsers (DOCA / NIC FW / BMC FW) ────────────────


def _parse_doca_release(text: str) -> str | None:
    """`/etc/mlnx-release` is one line on a freshly-flashed bf-bundle:

        bf-bundle-3.1.0-76_25.07_ubuntu-22.04_prod

    Older builds may have leading whitespace or trailing newlines —
    return the first non-empty stripped line.
    """
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s
    return None


_ETHTOOL_DRV_FW_RE = re.compile(r"^\s*firmware-version:\s*(.+?)\s*$")


def _parse_ethtool_drv_firmware(text: str) -> str | None:
    """`ethtool -i <iface>` exposes the NIC firmware-version line.

    Sample line:
        firmware-version: 32.46.1006 (MT_0000000xxx)
    Returns the value verbatim (including any parenthesised PSID).
    """
    for line in (text or "").splitlines():
        m = _ETHTOOL_DRV_FW_RE.match(line)
        if m:
            return m.group(1)
    return None


_IPMI_MC_FW_RE = re.compile(r"^\s*Firmware Revision\s*:\s*(.+?)\s*$")


def _parse_ipmi_mc_firmware(text: str) -> str | None:
    """`ipmitool mc info` reports the local BMC's firmware revision.

    Sample line (exists in the existing `ipmi_mc` capture):
        Firmware Revision         : 25.10
    """
    for line in (text or "").splitlines():
        m = _IPMI_MC_FW_RE.match(line)
        if m:
            return m.group(1)
    return None


def _parse_firmware_section(
    *, mlnx_release: str, ethtool_drv_p0: str, ipmi_mc: str,
) -> dict[str, str | None]:
    """Roll the three firmware probes into one dict the UI can render
    flat. Each value is None when the source command wasn't available
    (fresh BFB without DOCA tools, ethtool absent, ipmitool denied)."""
    return {
        "doca": _parse_doca_release(mlnx_release),
        "nic": _parse_ethtool_drv_firmware(ethtool_drv_p0),
        "bmc": _parse_ipmi_mc_firmware(ipmi_mc),
    }


def _extract_oob_mac(ip_br_addr_output: str) -> str | None:
    """Find oob_net0's MAC in `ip -br addr show` output.

    Example line:
        oob_net0  UP  5c:25:73:aa:bb:cc  10.0.0.66/24
    Returns None when oob_net0 isn't present (no DHCP lease) — the MAC is
    still a later-phase enhancement via rshim console once we add that.
    """
    for line in ip_br_addr_output.splitlines():
        if not line.startswith("oob_net0"):
            continue
        for tok in line.split():
            if re.fullmatch(r"[0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5}", tok):
                return tok.lower()
    return None


def _extract_oob_ipv4(ip_br_addr_output: str) -> str | None:
    """Find oob_net0's first IPv4 (without CIDR prefix) in `ip -br addr` output.

    Example line:
        oob_net0  UP  5c:25:73:aa:bb:cc  192.168.68.79/22 fe80::.../64
    Returns None when oob_net0 has no IPv4 (DHCP didn't lease, link
    down, etc.) — caller falls back to the tmfifo loopback for SSH.
    """
    for line in ip_br_addr_output.splitlines():
        if not line.startswith("oob_net0"):
            continue
        for tok in line.split():
            m = re.fullmatch(
                r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})/\d+", tok,
            )
            if m:
                return m.group(1)
    return None


def _mac_delta(mac: str, delta: int) -> str:
    """Return MAC with `delta` added to the last octet (mod 256)."""
    parts = mac.lower().split(":")
    parts[-1] = f"{(int(parts[-1], 16) + delta) & 0xFF:02x}"
    return ":".join(parts)


def _ip_mask_to_cidr(ip: str, mask: str) -> str:
    """Convert dotted-quad mask to CIDR."""
    octets = [int(p) for p in mask.split(".")]
    prefix = sum(bin(o).count("1") for o in octets)
    return f"{ip}/{prefix}"


def _paramiko_runner(host: str, user: str, password: str, command: str) -> str:
    """Default SSH runner using paramiko — one-shot connect, run, close."""
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host, username=user, password=password, timeout=15,
            allow_agent=False, look_for_keys=False,
        )
        _stdin, stdout, stderr = client.exec_command(command, timeout=90)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        if err and not out:
            raise DpuOsProbeError(f"SSH command produced only stderr: {err.strip()}")
        return out
    finally:
        client.close()


def _make_paramiko_runner_via(jumphost_cred: dict):
    """Build a `(host, user, password, command) -> str` runner that SSHes
    through the project's jumphost using paramiko direct-tcpip. Same
    one-shot connect/exec/close semantics as `_paramiko_runner`."""

    def run(host: str, user: str, password: str, command: str) -> str:
        client = ssh_connect_to_bmc(
            host, username=user, password=password,
            jumphost_cred=jumphost_cred, timeout=15,
        )
        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=90)
            out = stdout.read().decode(errors="replace")
            err = stderr.read().decode(errors="replace")
            if err and not out:
                raise DpuOsProbeError(f"SSH command produced only stderr: {err.strip()}")
            return out
        finally:
            client.close()

    return run
