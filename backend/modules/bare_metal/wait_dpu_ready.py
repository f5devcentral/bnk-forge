"""
SSH module: bare-metal/wait-dpu-ready

Waits for DPU to become SSH-reachable after flash. This is a polling
step that repeatedly attempts SSH until the DPU responds.

This step is typically needed after flash-dpu to ensure the DPU is
fully booted and ready before proceeding with install-dpu-prereqs.

Two paths:
- **tmfifo** (default): DPU is at 192.168.100.2 via rshim tmfifo on
  the host. Uses paramiko jumphost chain for SSH.
- **bmc** (dual_dpu_obmc): rshim is on the BMC, not the host, so
  tmfifo from the host doesn't exist. We tunnel worker → host → BMC
  via paramiko, SSH from the BMC to the DPU over BMC-side tmfifo
  (192.168.100.2), poll the DPU's oob_net0 for a DHCP-assigned IPv4,
  persist it to BareMetalHost.dpu_mgmt_ip, and then SSH from the host
  to the DPU directly at that OOB IP. The OOB IP is plain IPv4 and
  routable from the host (host already reaches BMC on the same
  management subnet), so paramiko's jumphost chain handles it
  without any %scope_id workaround.
"""

import time
from typing import Any

from modules.base import InputSpec, OutputSpec, SSHModule


class WaitDPUReadyModule(SSHModule):
    name = "Wait DPU Ready"
    path = "bare-metal/wait-dpu-ready"
    category = "bare-metal"
    phase = "DPU Flash"
    description = "Wait for DPU to become SSH-reachable after flash"
    version = "1.0.0"

    # SSH config — init connects to the bare-metal host (always reachable)
    # to verify baseline connectivity.  execute() then polls the DPU.
    target = "host"
    estimated_duration = 300
    timeout = 600

    dependencies = ["bare-metal/flash-dpu"]

    inputs = {
        "bare_metal_host_id": InputSpec(
            name="bare_metal_host_id",
            source="host",
            required=True,
            description="ID of the BareMetalHost",
        ),
        "dpu_ip": InputSpec(
            name="dpu_ip",
            source="module",
            from_module="bare-metal/flash-dpu",
            from_output="dpu_ip",
            default="192.168.100.2",
            description="DPU management IP (rshim tmfifo)",
        ),
        "max_wait_seconds": InputSpec(
            name="max_wait_seconds",
            type="number",
            default=900,
            required=False,
            description=(
                "Wall-clock budget for the DPU to become SSH-reachable. "
                "First-boot after a BFB flash (kernel init, cloud-init, "
                "netplan, DHCP) commonly takes 5–10 min on MGX/Grace; "
                "BMC tmfifo path budgets ~2/3 of this for oob_net0 DHCP "
                "discovery and the rest for host→DPU SSH verification."
            ),
        ),
        "rshim_source": InputSpec(
            name="rshim_source",
            source="host",
            required=False,
            default="host",
            description=(
                "Where rshim is: 'host' (tmfifo from host) or "
                "'bmc' (rshim on DPU OpenBMC; oob_net0 discovered via BMC tmfifo)"
            ),
        ),
        "bmc_ip": InputSpec(
            name="bmc_ip",
            source="host",
            required=False,
            default="",
            description=(
                "DPU OpenBMC IP — required when rshim_source=bmc so we can "
                "tunnel through the BMC to read DPU oob_net0"
            ),
        ),
        "bmc_user": InputSpec(
            name="bmc_user",
            source="host",
            required=False,
            default="root",
            description="DPU OpenBMC SSH username (injected by variable_assembler)",
        ),
        "bmc_password": InputSpec(
            name="bmc_password",
            source="host",
            required=False,
            default="",
            description="DPU OpenBMC SSH password (injected by variable_assembler)",
        ),
    }

    outputs = {
        "dpu_ssh_ready": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=True,
        ),
        "dpu_hostname": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dpu_kernel": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
        "dpu_ip": OutputSpec(
            resource_kind="",
            resource_name="",
            static_value=None,
        ),
    }

    # ── Plan (no-op — always wait) ────────────────────────────────────

    def plan_commands(self, variables: dict[str, Any]) -> list[str]:
        return []

    def parse_plan_output(self, output: str, variables: dict[str, Any]) -> bool:
        return True

    # ── Apply / Validate stubs (Python path bypasses these) ──────────

    def apply_commands(self, variables: dict[str, Any]) -> list[str]:
        # Unused: execute() override takes precedence via Python path detection
        return []

    def validate_commands(self, variables: dict[str, Any]) -> list[str]:
        # Validation is folded into execute() — no separate validate phase needed
        return []

    def parse_apply_output(self, output: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Unused: execute() returns outputs directly
        return {}

    # ── Python execute path ───────────────────────────────────────────

    def execute(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Dispatch on rshim_source.

        - ``bmc`` (dual_dpu_obmc): discover DPU `oob_net0` IP via BMC tmfifo,
          persist to BareMetalHost.dpu_mgmt_ip, then SSH from host on the
          discovered IP.
        - anything else: tmfifo-via-host-rshim path (regular topology).
        """
        rshim_source = variables.get("rshim_source", "host")

        if rshim_source == "bmc":
            return self._execute_via_bmc_oob(session, variables, on_output)

        return self._execute_tmfifo(session, variables, on_output)

    # ── tmfifo path (default) ─────────────────────────────────────────

    def _execute_tmfifo(self, session: Any, variables: dict[str, Any], on_output: Any) -> dict[str, Any]:
        """Poll DPU SSH via paramiko jumphost chain (192.168.100.2).

        Args:
            session:    Live SSHSession connected to the bare-metal host
                        (target="host" means the engine built a host session).
            variables:  Assembled variable dict — includes dpu_ip, dpu_username,
                        dpu_password injected by build_variables_for_ssh().
            on_output:  Streaming log callback (line: str) -> None.

        Returns:
            dict with dpu_ssh_ready, dpu_hostname, dpu_kernel.

        Raises:
            RuntimeError: If the DPU does not become reachable within max_wait_seconds.
        """
        t0 = time.monotonic()

        from services.bare_metal.ssh_session import SSHSession

        # Prefer the address actually baked into bf.conf. `dpu_ip` comes from
        # flash-dpu, but a re-run or a partial chain can leave it unset, and the
        # 192.168.100.2 literal is only correct for the pool's FIRST /30 (#118).
        dpu_ip: str = str(
            variables.get("dpu_ip") or variables.get("dpu_tmfifo_ip") or "192.168.100.2"
        )
        max_wait: int = int(variables.get("max_wait_seconds") or 900)
        poll_interval: int = 10

        # DPU credentials come from variables (injected by build_variables_for_ssh
        # from BareMetalHost.dpu_credential).  Fall back to "ubuntu" with no
        # password if no DPU credential is configured — matches the old BatchMode
        # behaviour where the DPU was assumed to accept key auth.
        dpu_username: str = str(variables.get("dpu_username") or "ubuntu")
        dpu_password: str | None = variables.get("dpu_password") or None
        dpu_private_key_content: str | None = variables.get("dpu_private_key_content") or None

        auth_method = "key" if dpu_private_key_content else ("password" if dpu_password else "none")
        on_output(
            f"[wait-dpu] Waiting for DPU SSH at {dpu_ip} "
            f"(user={dpu_username}, auth={auth_method}, wall-clock max={max_wait}s, "
            f"interval={poll_interval}s)"
        )

        # Build the jumphost chain: [existing jumphosts of host session] + [host itself]
        # This mirrors SSHEngine._build_dpu_session().
        host_hop: dict[str, Any] = {
            "host": session.host,
            "port": session.port,
            "username": session.username,
            "password": session.password,
            "private_key_content": session.private_key_content,
        }
        jumphost_chain: list[dict] = list(session.jumphost_chain or []) + [host_hop]

        # Wall-clock deadline, NOT attempt-count — the SSH connect_timeout
        # (8s) plus poll_interval (10s) makes attempt-count loops drift to
        # ~2x the advertised budget. Time-based keeps the log honest.
        deadline = time.monotonic() + max_wait
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            elapsed = round(time.monotonic() - t0, 1)
            on_output(
                f"[wait-dpu] Attempt {attempt} at {elapsed}s — "
                f"connecting to {dpu_username}@{dpu_ip}..."
            )

            try:
                dpu_session = SSHSession(
                    host=dpu_ip,
                    username=dpu_username,
                    port=22,
                    password=dpu_password,
                    private_key_content=dpu_private_key_content,
                    jumphost_chain=jumphost_chain,
                    connect_timeout=8,
                )
                result = dpu_session.execute(
                    "echo 'DPU_READY' && hostname && uname -r",
                    timeout=12,
                )

                if result.exit_code == 0 and "DPU_READY" in result.stdout:
                    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
                    # Expected output: "DPU_READY\n<hostname>\n<kernel>"
                    hostname = lines[1] if len(lines) > 1 else "unknown"
                    kernel = lines[2] if len(lines) > 2 else "unknown"
                    total = time.monotonic() - t0
                    on_output(
                        f"[wait-dpu] DPU ready! "
                        f"hostname={hostname}, kernel={kernel} ({total:.1f}s total)"
                    )
                    return {
                        "dpu_ssh_ready": True,
                        "dpu_hostname": hostname,
                        "dpu_kernel": kernel,
                        "dpu_ip": dpu_ip,
                        "execution_duration_seconds": round(total, 1),
                    }

                # Got a response but the marker wasn't present — treat as transient
                detail = result.stdout[:80] if result.stdout.strip() else result.stderr[:120]
                on_output(
                    f"[wait-dpu] Unexpected output (exit={result.exit_code}): "
                    f"{detail!r}"
                )

            except Exception as exc:
                # Connection refused / timeout / auth error — DPU still booting
                on_output(f"[wait-dpu] Not ready: {str(exc)[:120]}")

            # Only sleep if we still have budget for another iteration
            if time.monotonic() + poll_interval < deadline:
                time.sleep(poll_interval)
            else:
                break

        raise RuntimeError(
            f"DPU at {dpu_ip} did not become SSH-reachable within {max_wait}s "
            f"({attempt} attempts). "
            "Check DPU boot status, rshim connectivity, and DPU SSH credentials."
        )

    # ── BMC tmfifo → oob_net0 path (dual_dpu_obmc) ───────────────────

    def _execute_via_bmc_oob(
        self, session: Any, variables: dict[str, Any], on_output: Any
    ) -> dict[str, Any]:
        """Wait for DPU via BMC tmfifo, discover oob_net0 IP, then SSH from host.

        For dual_dpu_obmc, the host PF physically terminates on the BF3 ASIC
        and does NOT share an L2 segment with the DPU OS's sshd-bound
        interfaces, so the IPv6 link-local approach cannot work. Instead:

        1. Open a paramiko SSH tunnel to the BMC (worker → jumphost → BMC,
           same chain as flash_dpu).
        2. From the BMC, SSH to the DPU OS via tmfifo (192.168.100.2) and
           poll `ip -4 -br a show oob_net0` until DHCP assigns an IPv4.
        3. Persist that IP to BareMetalHost.dpu_mgmt_ip so downstream
           modules and subsequent runs can find the DPU directly.
        4. SSH from the host (paramiko jumphost chain) to the DPU at the
           OOB IP using the project's DPU credentials; collect hostname /
           kernel and return.

        The BMC stays a tunnel-only role here — there's no `sshpass` shell
        munging in `flash_dpu`'s style because we use paramiko's
        `exec_command` against the already-authenticated BMC client.
        """
        import shlex

        from services.bare_metal.ssh_session import SSHSession
        from services.dpu_tunnel import ssh_connect_to_bmc

        t0 = time.monotonic()
        max_wait = int(variables.get("max_wait_seconds") or 900)

        bmc_ip = (variables.get("bmc_ip") or "").strip()
        if not bmc_ip:
            raise RuntimeError(
                "BMC IP required for dual_dpu_obmc wait-dpu-ready "
                "(rshim_source=bmc). Set bmc_ip on the bare-metal host."
            )
        bmc_user = str(variables.get("bmc_user") or "root")
        bmc_password = str(variables.get("bmc_password") or "0penBmc")

        dpu_username = str(variables.get("dpu_username") or "ubuntu")
        dpu_password = variables.get("dpu_password") or None
        dpu_private_key_content = variables.get("dpu_private_key_content") or None

        host_id_raw = variables.get("bare_metal_host_id")
        try:
            host_id = int(host_id_raw) if host_id_raw is not None else None
        except (TypeError, ValueError):
            host_id = None

        # --- Build BMC tunnel via the same jumphost chain as flash_dpu ---
        jumphost_cred: dict[str, Any] | None = None
        if session.jumphost_chain:
            jh = session.jumphost_chain[0]
            jumphost_cred = {
                "host": jh["host"],
                "port": jh.get("port", 22),
                "username": jh.get("username", "root"),
                "auth_type": "key" if jh.get("private_key_content") else "password",
                "private_key": jh.get("private_key_content"),
                "key_passphrase": jh.get("passphrase"),
                "password": jh.get("password"),
            }
            on_output(f"[wait-dpu] BMC tunnel: worker → {jh['host']} → {bmc_ip}:22")
        else:
            on_output(f"[wait-dpu] BMC direct connect: worker → {bmc_ip}:22")

        try:
            bmc_client = ssh_connect_to_bmc(
                bmc_ip, username=bmc_user, password=bmc_password,
                jumphost_cred=jumphost_cred, timeout=20,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to SSH to BMC at {bmc_ip}: {exc}. "
                "Check BMC IP, credentials, and reachability."
            ) from exc
        on_output("[wait-dpu] BMC SSH: connected")

        # --- Plant DPU password on BMC for sshpass (avoid shell quoting) ---
        pass_path = "/tmp/.forge_dpu_pass"
        try:
            try:
                if dpu_password:
                    sftp = bmc_client.open_sftp()
                    with sftp.file(pass_path, "w") as fp:
                        fp.write(dpu_password)
                    sftp.chmod(pass_path, 0o600)
                    sftp.close()

                def bmc_exec(cmd: str, timeout: int = 30) -> tuple[int, str, str]:
                    _in, _out, _err = bmc_client.exec_command(cmd, timeout=timeout)
                    out = _out.read().decode("utf-8", errors="replace")
                    err = _err.read().decode("utf-8", errors="replace")
                    rc = _out.channel.recv_exit_status()
                    return rc, out, err

                # BMC runs busybox/dropbear ssh which ignores `-o KEY=VALUE`
                # (warns on stderr — when this was masked behind a 120-char
                # err truncation it looked like a "DHCP not ready" timeout
                # in the outer loop). The right flag is `-y -y`:
                #   -y     accept the remote host key without prompting
                #   -y -y  also skip the known_hosts file entirely
                # That bypasses the stale-known_hosts issue after every
                # reflash, since BFB install rotates the DPU OS host key
                # and the BMC's /root/.ssh/known_hosts accumulates entries
                # across flashes.
                # Note: we deliberately do NOT issue a `rm known_hosts`
                # cleanup via exec_command after sftp.close(): paramiko +
                # dropbear can raise "Channel closed" on an exec_command
                # issued in the milliseconds after sftp.close() — likely
                # a transport-level race the dropbear side doesn't
                # tolerate. `-y -y` already side-steps known_hosts entirely.
                ssh_prefix = (
                    f"sshpass -f {pass_path} ssh -y -y "
                    if dpu_password
                    else "ssh -y -y "
                )
                ssh_target = f"{dpu_username}@192.168.100.2"

                def dpu_via_bmc(remote_cmd: str, timeout: int = 25) -> tuple[int, str, str]:
                    full = f"{ssh_prefix}{ssh_target} {shlex.quote(remote_cmd)}"
                    return bmc_exec(full, timeout=timeout)

                # --- Poll DPU OS via BMC for oob_net0 IPv4 ---
                # Budget: 2/3 of max_wait for OOB discovery, rest for the host
                # → DPU verify step.
                oob_budget = max(60, max_wait * 2 // 3)
                deadline = time.monotonic() + oob_budget
                on_output(
                    "[wait-dpu] Polling DPU via BMC tmfifo for oob_net0 IPv4 "
                    f"(max {oob_budget}s)"
                )

                oob_ip: str | None = None
                last_status: str | None = None
                while time.monotonic() < deadline:
                    # `ip -4 -br a show oob_net0` prints something like:
                    #   oob_net0  UP  10.10.10.76/22 metric 100
                    # before DHCP completes, IFACE_STATE may be DOWN or the IP
                    # column is empty.
                    rc, out, err = dpu_via_bmc(
                        "ip -4 -br a show oob_net0 2>/dev/null || true",
                        timeout=20,
                    )
                    if rc == 0 and out.strip():
                        # Parse: first non-empty line, third whitespace token,
                        # strip the /CIDR.
                        line = out.strip().splitlines()[0]
                        parts = line.split()
                        if len(parts) >= 3 and "/" in parts[2]:
                            oob_ip = parts[2].split("/", 1)[0]
                            on_output(
                                f"[wait-dpu] DPU oob_net0: {parts[2]} (state={parts[1]})"
                            )
                            break
                        status = f"oob_net0 line: {line!r}"
                    elif rc != 0:
                        # SSH from BMC to DPU might fail while DPU is still
                        # booting / sshd not up — that's fine, keep polling.
                        # Show enough of stderr to diagnose stale-known_hosts
                        # / auth failures (the dropbear warning lines alone
                        # take ~120 chars, so the real error lives past that).
                        err_tail = err.strip().splitlines()
                        err_msg = " | ".join(err_tail[-3:])[:400]
                        status = f"BMC→DPU ssh exit={rc} err={err_msg!r}"
                    else:
                        status = "oob_net0 not present yet (DPU still booting?)"

                    if status != last_status:
                        on_output(f"[wait-dpu] {status}")
                        last_status = status

                    time.sleep(5)

                if not oob_ip:
                    raise RuntimeError(
                        "DPU oob_net0 did not get an IPv4 address from DHCP "
                        f"within {oob_budget}s (via BMC tmfifo). Check the "
                        "DPU's netplan (bf.conf renders oob_net0 with "
                        "dhcp4: true) and the OOB management DHCP server."
                    )
            finally:
                if dpu_password:
                    try:
                        bmc_client.exec_command(f"rm -f {pass_path}", timeout=5)
                    except Exception:
                        pass
        finally:
            try:
                bmc_client.close()
            except Exception:
                pass

        on_output(f"[wait-dpu] Discovered DPU OOB IP: {oob_ip}")

        # --- Persist OOB IP to BareMetalHost.dpu_mgmt_ip ---
        if host_id is not None:
            try:
                from database import get_db_context
                from models.bare_metal import BareMetalHost

                with get_db_context() as db:
                    host = db.query(BareMetalHost).filter_by(id=host_id).first()
                    if host and host.dpu_mgmt_ip != oob_ip:
                        host.dpu_mgmt_ip = oob_ip
                        db.commit()
                        on_output(
                            f"[wait-dpu] Persisted dpu_mgmt_ip={oob_ip} on "
                            f"BareMetalHost id={host_id}"
                        )
            except Exception as exc:
                # Persistence failure is non-fatal — the IP still gets
                # returned as a module output for downstream consumption.
                on_output(f"[wait-dpu] WARNING: could not persist dpu_mgmt_ip: {exc}")

        # --- Verify DPU SSH from host side at the OOB IP ---
        host_hop: dict[str, Any] = {
            "host": session.host,
            "port": session.port,
            "username": session.username,
            "password": session.password,
            "private_key_content": session.private_key_content,
        }
        jumphost_chain = list(session.jumphost_chain or []) + [host_hop]

        verify_budget = max(30, max_wait - int(time.monotonic() - t0))
        poll_interval = 8
        max_attempts = max(1, verify_budget // poll_interval)
        on_output(
            f"[wait-dpu] Verifying DPU SSH at {oob_ip} from host "
            f"(user={dpu_username}, max={verify_budget}s, interval={poll_interval}s)"
        )

        last_err = ""
        for attempt in range(1, max_attempts + 1):
            try:
                dpu_session = SSHSession(
                    host=oob_ip,
                    username=dpu_username,
                    port=22,
                    password=dpu_password,
                    private_key_content=dpu_private_key_content,
                    jumphost_chain=jumphost_chain,
                    connect_timeout=8,
                )
                result = dpu_session.execute(
                    "echo 'DPU_READY' && hostname && uname -r",
                    timeout=12,
                )
                if result.exit_code == 0 and "DPU_READY" in result.stdout:
                    lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
                    hostname = lines[1] if len(lines) > 1 else "unknown"
                    kernel = lines[2] if len(lines) > 2 else "unknown"
                    total = time.monotonic() - t0
                    on_output(
                        f"[wait-dpu] DPU ready! hostname={hostname}, "
                        f"kernel={kernel}, ip={oob_ip} ({total:.1f}s total)"
                    )
                    return {
                        "dpu_ssh_ready": True,
                        "dpu_hostname": hostname,
                        "dpu_kernel": kernel,
                        "dpu_ip": oob_ip,
                        "execution_duration_seconds": round(total, 1),
                    }
                last_err = (
                    f"exit={result.exit_code} stdout={result.stdout[:80]!r}"
                )
            except Exception as exc:
                last_err = str(exc)[:160]
                on_output(f"[wait-dpu] Attempt {attempt}/{max_attempts}: {last_err}")

            if attempt < max_attempts:
                time.sleep(poll_interval)

        raise RuntimeError(
            f"DPU at {oob_ip} (oob_net0) did not become SSH-reachable from "
            f"the host within {verify_budget}s. Last error: {last_err}. "
            "Check DPU SSH credentials, host→DPU routing on the OOB "
            "management subnet, and DPU sshd state."
        )

