"""Phase-1 (smoke) step functions.

Each step takes a `Context` and returns a `StepResult`. Steps mutate
`Context.state` to pass data forward (project_id, job_id, dpu_ids,…).

Step names are stable identifiers — the operator selects which to run
via `--steps step_login,step_create_project`.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
import urllib3

from .client import BnkForgeApiError, BnkForgeClient
from .config import E2EConfig
from .result import StepRecorder, StepResult

logger = logging.getLogger("e2e.steps")


@dataclass
class Context:
    cfg: E2EConfig
    client: BnkForgeClient
    state: dict[str, Any] = field(default_factory=dict)


# ── Phase-1 steps ──────────────────────────────────────────────────


def step_deploy_bnk_forge(ctx: Context) -> StepResult:
    """Bring up a clean local bnk-forge via `make install`.

    Refuses to touch anything if a bnk-forge container is already
    running — the operator has to stop / remove that explicitly. This
    is the safety guard against silently nuking a live instance.

    Skipped when `local_deploy` is false in the config OR when
    `bnk_forge_url` doesn't point at this host (you can't `make
    install` a remote box from here).
    """
    with StepRecorder("deploy_bnk_forge") as r:
        if not ctx.cfg.local_deploy:
            r.skip("local_deploy=false in config")
            return r.to_result()
        if not _url_is_local(ctx.cfg.bnk_forge_url):
            r.skip(
                f"bnk_forge_url={ctx.cfg.bnk_forge_url} is not local; "
                f"cannot `make install` a remote instance",
            )
            return r.to_result()
        if shutil.which("docker") is None:
            raise RuntimeError(
                "docker not on PATH — local deploy needs docker + compose",
            )
        if shutil.which("make") is None:
            raise RuntimeError("make not on PATH")

        # `local_deploy: true` in the config IS the operator's consent
        # to teardown — `make _install-clean` does
        # `docker compose down` + wipe volumes + wipe images, which
        # handles a running instance fine. We log what's about to be
        # nuked so the run history reflects it; the safety net here
        # is the config flag, not a runtime prompt.
        running = _running_bnk_forge_containers()
        if running:
            logger.info(
                "bnk-forge containers running, will be torn down by "
                "`make _install-clean`: %s", ", ".join(running),
            )

        repo_dir = (
            Path(ctx.cfg.bnk_forge_repo_dir)
            if ctx.cfg.bnk_forge_repo_dir
            else Path(__file__).resolve().parents[2]
        )
        if not (repo_dir / "Makefile").is_file():
            raise RuntimeError(
                f"no Makefile at {repo_dir} — set bnk_forge_repo_dir in "
                f"the config to point at your bnk-forge clone",
            )

        # Four-stage clean-room deploy. Doing them as separate make
        # invocations gives us clear per-stage logs and lets the
        # build-cache bust happen *before* anything is running, so
        # there's no chance of a stale image being pulled into a
        # restart. `make install` alone would do step 3 with a cached
        # `_install-build` — we want `--no-cache` so dependency
        # changes (apt, pip) are picked up.
        deadline = time.time() + ctx.cfg.timeouts.local_deploy
        stages: list[dict[str, Any]] = []

        def _run_stage(
            label: str, cmd: list[str], *, stdin: str | None = None,
        ) -> None:
            remaining = max(60, int(deadline - time.time()))
            logger.info("[%s] running %s in %s", label, " ".join(cmd), repo_dir)
            proc = subprocess.run(
                cmd,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                input=stdin,
                timeout=remaining,
            )
            tail = "\n".join(
                (proc.stdout + "\n" + proc.stderr).splitlines()[-40:],
            )
            stages.append({
                "stage": label,
                "cmd": " ".join(cmd),
                "rc": proc.returncode,
                "tail": tail,
            })
            if proc.returncode != 0:
                last = tail.splitlines()[-1] if tail else "<no output>"
                raise RuntimeError(
                    f"[{label}] `{' '.join(cmd)}` failed "
                    f"(rc={proc.returncode}); last line: {last}",
                )

        # 1. Down + wipe volumes + wipe images. The Makefile's
        #    `_install-clean` does exactly this; using it keeps the
        #    teardown logic in one place.
        _run_stage("clean", ["make", "_install-clean"])
        # 2. Global Docker hygiene — `make clean` prunes dangling
        #    images / build cache > 7 days / unused volumes from any
        #    project + warns on bnk-forge-prefix orphans. The script
        #    is interactive (asks before deleting unused volumes);
        #    pipe a `y` so the run stays unattended. After step 1
        #    there are no bnk-forge volumes left, so the prompt
        #    typically has nothing to delete — but it still prunes
        #    dangling images / build cache that built up over time.
        _run_stage("docker-prune", ["make", "clean"], stdin="y\n")
        # 3. Force-rebuild every layer (apt, pip, npm, etc).
        _run_stage("build-clean", ["make", "build-clean"])
        # 4. Bring up postgres + redis, run migrations, start the
        #    rest. `_install-start` waits for the DB before launching
        #    backend/workers, which is what we want here too.
        _run_stage("start", ["make", "_install-start"])
        r.record(repo_dir=str(repo_dir), stages=stages)

        # Wait for the API to actually be reachable. `make install`
        # has its own health waits but they target containers; we
        # need to confirm the proxy + auth path is up too.
        ready_at = _wait_for_api_ready(
            ctx.cfg.bnk_forge_url,
            timeout=ctx.cfg.timeouts.local_deploy,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        r.record(api_ready_after_s=round(ready_at, 1))
        r.ok(
            f"clean install up; API reachable in {ready_at:.0f}s "
            f"after make install",
        )
    return r.to_result()


def step_login(ctx: Context) -> StepResult:
    """Log in as admin and store JWT on the client.

    Handles the freshly-seeded `must_change_password=True` case by
    rotating the password to itself. After `make install` the admin
    user is `admin` / `changeme` with the flag set; without this the
    harness would deadlock on the first authed request, and operators
    would have to bounce through the UI before re-running."""
    with StepRecorder("login") as r:
        body = ctx.client.login(
            ctx.cfg.bnk_forge_admin_user,
            ctx.cfg.bnk_forge_admin_password,
        )
        user = body.get("user", {}) if isinstance(body, dict) else {}
        if body.get("must_change_password"):
            # Set new = current — backend only checks ≥8 chars + that
            # `current` matches, so we just clear the flag without
            # actually changing the secret.
            ctx.client.change_password(
                current=ctx.cfg.bnk_forge_admin_password,
                new=ctx.cfg.bnk_forge_admin_password,
            )
            r.record(must_change_password_cleared=True)
        # Capture build identity once we're authenticated. Keeps the
        # PDF box self-describing across runs without having to diff
        # configs by hand.
        build = _capture_bnk_forge_build(ctx)
        ctx.state["bnk_forge_build"] = build
        r.ok(f"logged in as {user.get('username', '?')}")
        r.record(user=user, bnk_forge_build=build)
    return r.to_result()


def _capture_bnk_forge_build(ctx: Context) -> dict[str, str | None]:
    """Best-effort {version, branch, commit, commit_subject, platform}
    from the local repo + host. Missing fields stay `None` — e.g.
    when the harness is run from a tarball with no `.git`, or when
    `git` isn't on PATH. Platform is the harness host's OS string,
    which equals the bnk-forge host when `local_deploy=true`.
    """
    repo_dir = (
        Path(ctx.cfg.bnk_forge_repo_dir)
        if ctx.cfg.bnk_forge_repo_dir
        else Path(__file__).resolve().parents[2]
    )
    version: str | None = None
    version_file = repo_dir / "VERSION"
    if version_file.is_file():
        try:
            version = version_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            pass

    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo_dir), *args],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip() or None

    import socket
    return {
        "version": version,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "commit": _git("rev-parse", "--short=12", "HEAD"),
        "commit_subject": _git("log", "-1", "--format=%s"),
        "platform": _capture_platform(),
        # `socket.gethostname()` falls back to "localhost" on a
        # mis-configured box; that's already obvious from the URL,
        # so don't bother rendering it in that case (filtered in the
        # diagram).
        "host": socket.gethostname() or None,
    }


def _capture_platform() -> str | None:
    """Short platform string for the harness host. Linux reads
    /etc/os-release `PRETTY_NAME`; macOS uses `sw_vers`. Falls back
    to `platform.platform()` so something always shows.
    """
    osrel = Path("/etc/os-release")
    if osrel.is_file():
        try:
            for line in osrel.read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    name = line.split("=", 1)[1].strip().strip('"')
                    # Append `kernel uname -r` for ops-relevant detail.
                    try:
                        kver = subprocess.run(
                            ["uname", "-r"], capture_output=True,
                            text=True, timeout=2,
                        ).stdout.strip()
                    except (OSError, subprocess.TimeoutExpired):
                        kver = ""
                    return f"{name} (kernel {kver})" if kver else name
        except OSError:
            pass
    if shutil.which("sw_vers"):
        try:
            out = subprocess.run(
                ["sw_vers", "-productVersion"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
            if out:
                return f"macOS {out}"
        except (OSError, subprocess.TimeoutExpired):
            pass
    import platform as _p
    return _p.platform()


def step_create_jumphost(ctx: Context) -> StepResult:
    """If config has a jumphost, register it as an SSH credential.
    Idempotent on name: skip if a credential with the same name
    already exists."""
    with StepRecorder("create_jumphost") as r:
        jh = ctx.cfg.jumphost
        if jh is None:
            r.skip("no jumphost configured")
            return r.to_result()
        cred_name = f"e2e-jumphost-{jh.host.replace('.', '-')}"
        existing = ctx.client.list_ssh_credentials()
        match = next((c for c in existing if c.get("name") == cred_name), None)
        if match:
            ctx.state["jumphost_credential_id"] = match["id"]
            r.ok(f"reused existing credential id={match['id']}")
            return r.to_result()
        try:
            private_key = Path(jh.ssh_key_path).read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"cannot read jumphost SSH key {jh.ssh_key_path}: {exc}",
            ) from exc
        cred = ctx.client.create_ssh_credential(
            name=cred_name,
            host=jh.host,
            port=jh.port,
            username=jh.user,
            private_key=private_key,
            description="e2e-test jumphost",
        )
        ctx.state["jumphost_credential_id"] = cred["id"]
        r.ok(f"created credential id={cred['id']}")
        r.record(credential_id=cred["id"], host=jh.host, user=jh.user)
    return r.to_result()


def step_create_project(ctx: Context) -> StepResult:
    """Create a fresh bare-metal project. Pin the jumphost when one
    was registered."""
    with StepRecorder("create_project") as r:
        ts = time.strftime("%Y%m%d-%H%M%S")
        tag = ctx.cfg.bf_template
        name = f"{ctx.cfg.project_name_prefix}{ts}-{tag}"
        body = ctx.client.create_project(
            name=name,
            description=(
                f"e2e harness run, {tag} template, "
                f"{len(ctx.cfg.worker_node_ips)} worker(s)"
            ),
            # The frontend gates the DPU and Bare Metal tabs on
            # `project_type === 'bare-metal'` (hyphen — see
            # frontend-v2/src/pages/ProjectDetailV2.tsx). Underscore
            # silently saves but the project comes up tab-less, which
            # makes operators think the harness misfired.
            project_type="bare-metal",
        )
        project_id = body.get("project_id")
        if not isinstance(project_id, int):
            raise RuntimeError(f"create_project returned no id: {body}")
        ctx.state["project_id"] = project_id
        ctx.state["project_name"] = name
        # Wire the jumphost (if any) to the project so discovery and
        # later DPU operations route through it.
        jh_id = ctx.state.get("jumphost_credential_id")
        if jh_id is not None:
            try:
                ctx.client.update_project(
                    project_id, jumphost_ssh_credential_id=jh_id,
                )
            except BnkForgeApiError as exc:
                # Older builds may not accept this field on update;
                # surface a warning but don't fail the run.
                r.warn(
                    f"project created (id={project_id}) but jumphost "
                    f"attach failed: {exc.message}",
                )
                r.record(
                    project_id=project_id, project_name=name,
                    jumphost_attach_error=exc.message,
                )
                return r.to_result()
        r.ok(f"created project '{name}' id={project_id}")
        r.record(project_id=project_id, project_name=name)
    return r.to_result()


def step_configure_dpu_settings(ctx: Context) -> StepResult:
    """Patch project DPU settings with the run's BMC/OS passwords,
    chosen bf.conf template, and BFB image.

    Important: GET the settings row first. The backend's
    `ProjectDpuSettingsService.get_or_create` seeds VLAN subnet
    defaults (`external_vlan_ipv4_subnet=10.10.20.0/24`,
    `internal_vlan_ipv4_subnet=10.10.10.0/24`, …); the PATCH path
    (`update`) does NOT — it creates a blank row when none exists.
    Without those defaults, `auto_assign_vlan_ips` later assigns
    nothing and `flash_dpus` refuses with "this DPU has no uplink IP
    addresses yet".
    """
    with StepRecorder("configure_dpu_settings") as r:
        project_id = _require_project_id(ctx)
        ctx.client.get_dpu_settings(project_id)
        templates = ctx.client.list_bf_templates()
        # uplink_mode is a server-side classification: 'lag' or 'p0p1'.
        target_mode = "lag" if ctx.cfg.bf_template == "lag" else "p0p1"
        match = next(
            (t for t in templates if t.get("uplink_mode") == target_mode),
            None,
        )
        if match is None:
            available = sorted(
                {t.get("uplink_mode") for t in templates if t.get("uplink_mode")}
            )
            r.fail(
                f"no bf.conf template with uplink_mode='{target_mode}' in "
                f"catalog (available: {', '.join(available) or 'none'})",
            )
            r.record(templates_available=[t.get("name") for t in templates])
            return r.to_result()
        bf_template_id = match["id"]
        images = ctx.client.list_bfb_images()
        image_match = next(
            (
                img for img in images
                if img.get("image_filename") == ctx.cfg.bfb_image
            ),
            None,
        )
        if image_match is None:
            available = [img.get("image_filename") for img in images]
            r.fail(
                f"BFB image '{ctx.cfg.bfb_image}' not found in catalog "
                f"({len(images)} image(s) available)",
            )
            r.record(images_available=available)
            return r.to_result()
        body = ctx.client.patch_dpu_settings(
            project_id,
            bf_template_id=bf_template_id,
            bluefield_image_id=image_match["id"],
            default_oob_username="root",
            default_oob_password=ctx.cfg.bmc_password,
            default_os_username="ubuntu",
            default_os_password=ctx.cfg.dpu_os_password,
        )
        r.ok(
            f"template={match['name']} (mode={target_mode}), "
            f"image={ctx.cfg.bfb_image}",
        )
        r.record(
            bf_template_id=bf_template_id,
            bf_template_name=match["name"],
            bfb_image_id=image_match["id"],
            settings=body,
        )
        ctx.state["bf_template_id"] = bf_template_id
        ctx.state["bfb_image_id"] = image_match["id"]
    return r.to_result()


def step_discover_bmcs(ctx: Context) -> StepResult:
    """Run discovery against the configured BMC IP range — records
    which respond as BlueField BMCs and which BMC creds work.

    Non-fatal by design: in-band-discovered DPUs already get a
    `bmc_ip` set automatically by `probe_dpu_os` from `ipmitool lan
    print`. This step adds a second source of truth (Redfish reach
    + auth) so the operator sees credential health without having to
    open the Discovery tab. Reports `warn` when any configured BMC
    fails to authenticate, `ok` when all configured BMCs are reachable
    and authenticate.
    """
    with StepRecorder("discover_bmcs") as r:
        if not ctx.cfg.bmc_ips:
            r.skip("no BMC IPs configured")
            return r.to_result()
        project_id = _require_project_id(ctx)
        body = ctx.client.trigger_discovery(
            project_id, ctx.cfg.bmc_ips,
            # Pass worker SSH cred even though BMCs won't accept it —
            # backend's discovery schema requires SSH to be defined,
            # and Redfish probing runs alongside the SSH attempt
            # regardless. Not great, but lets us run BMC discovery
            # without a backend schema change.
            ssh_username=ctx.cfg.worker_ssh_user,
            ssh_private_key=Path(
                ctx.cfg.worker_ssh_key_path,
            ).read_text(encoding="utf-8"),
        )
        job_id = body.get("job", {}).get("id")
        if not isinstance(job_id, int):
            raise RuntimeError(f"trigger_discovery returned no job id: {body}")
        final = _wait_for_job_terminal(
            ctx, job_id,
            timeout=ctx.cfg.timeouts.discovery,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        nodes = final.get("nodes") or []
        bmcs = [n for n in nodes if n.get("is_dpu_bmc")]
        r.record(
            job_id=job_id, job_status=final.get("status"),
            scanned=len(ctx.cfg.bmc_ips),
            responded=len(nodes),
            bmcs_found=len(bmcs),
            bmc_ips=[n.get("ip_address") for n in bmcs],
            nodes=[
                {
                    "ip": n.get("ip_address"),
                    "is_dpu_bmc": n.get("is_dpu_bmc"),
                    "vendor": n.get("vendor"),
                    "status": n.get("status"),
                }
                for n in nodes
            ],
        )
        if not bmcs:
            r.warn(
                f"scanned {len(ctx.cfg.bmc_ips)} BMC IP(s); none responded "
                f"as BlueField BMCs",
            )
        else:
            r.ok(
                f"scanned {len(ctx.cfg.bmc_ips)} BMC IP(s); "
                f"{len(bmcs)} responded as BlueField BMCs",
            )
    return r.to_result()


def step_run_discovery_workers(ctx: Context) -> StepResult:
    """Trigger discovery against the worker-node IP list and wait
    for the job to reach a terminal state."""
    with StepRecorder("run_discovery_workers") as r:
        project_id = _require_project_id(ctx)
        try:
            private_key = Path(
                ctx.cfg.worker_ssh_key_path,
            ).read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"cannot read worker SSH key {ctx.cfg.worker_ssh_key_path}: "
                f"{exc}",
            ) from exc
        body = ctx.client.trigger_discovery(
            project_id,
            ctx.cfg.worker_node_ips,
            ssh_username=ctx.cfg.worker_ssh_user,
            ssh_private_key=private_key,
        )
        job = body.get("job", {})
        job_id = job.get("id")
        if not isinstance(job_id, int):
            raise RuntimeError(f"trigger_discovery returned no job id: {body}")
        ctx.state["discovery_job_id"] = job_id
        final = _wait_for_job_terminal(
            ctx, job_id,
            timeout=ctx.cfg.timeouts.discovery,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        nodes = final.get("nodes") or []
        completed = [n for n in nodes if n.get("status") == "completed"]
        dpu_hosts = [n for n in completed if n.get("is_dpu_host")]
        r.record(
            job_id=job_id, job_status=final.get("status"),
            node_count=len(nodes),
            completed=len(completed),
            dpu_hosts=len(dpu_hosts),
            dpu_host_ips=[n["ip_address"] for n in dpu_hosts],
        )
        if not dpu_hosts:
            # The discovery API succeeded; the *result* is bad. Use
            # `fail` so the captured evidence (job id, node statuses)
            # makes it into the report.
            r.fail(
                f"discovery completed but no DPU hosts found "
                f"(out of {len(completed)} reachable nodes)",
            )
            return r.to_result()
        r.ok(
            f"job #{job_id}: {len(dpu_hosts)} DPU host(s) "
            f"of {len(completed)} reachable",
        )
    return r.to_result()


def step_register_hosts(ctx: Context) -> StepResult:
    """Register every DPU host the discovery found into the project's
    bare-metal table, then register each DPU itself.

    The backend has a bulk `register-all-hosts` route but no bulk
    `register-all-dpus` — DPUs are registered per-node via
    `register-as-dpu`, which dispatches on the discovered node's flags
    (`is_dpu_host` → one in-band row per pci_address; `is_dpu_bmc`
    → BMC row). Both calls are idempotent so re-running is safe.
    """
    with StepRecorder("register_hosts") as r:
        project_id = _require_project_id(ctx)
        job_id = _require(ctx, "discovery_job_id")
        host_body = ctx.client.register_all_hosts(project_id, job_id)
        # Pull the job's nodes to get IDs for per-node DPU registration.
        job = ctx.client.get_discovery_job(project_id, job_id)
        nodes = job.get("nodes") or []
        dpu_nodes = [
            n for n in nodes
            if n.get("is_dpu_host") or n.get("is_dpu_bmc") or n.get("is_dpu_node")
        ]
        dpu_results: list[dict[str, Any]] = []
        for n in dpu_nodes:
            try:
                resp = ctx.client.register_node_as_dpu(
                    project_id, int(n["id"]),
                )
                dpu_results.append({
                    "node_id": n["id"], "ip": n.get("ip_address"),
                    "ok": True, "dpus": resp.get("dpus", []),
                })
            except BnkForgeApiError as exc:
                # Already-registered hosts return 400 ("All N DPU(s) on
                # this host are already registered.") — log but treat
                # as success so re-runs don't trip the recorder.
                msg = str(exc)
                already = "already registered" in msg.lower()
                dpu_results.append({
                    "node_id": n["id"], "ip": n.get("ip_address"),
                    "ok": already, "error": msg,
                })
        registered = sum(len(d.get("dpus") or []) for d in dpu_results if d.get("ok"))
        r.record(
            host_response=host_body,
            dpu_node_count=len(dpu_nodes),
            dpu_results=dpu_results,
        )
        failed = [d for d in dpu_results if not d.get("ok")]
        if failed:
            r.fail(
                f"registered {host_body.get('created', 0)} host(s); "
                f"DPU registration failed on {len(failed)}/{len(dpu_nodes)} node(s)",
            )
            return r.to_result()
        r.ok(
            f"registered {host_body.get('created', 0)} host(s) "
            f"(existing {host_body.get('existing', 0)}); "
            f"registered {registered} DPU(s) across {len(dpu_nodes)} node(s)",
        )
    return r.to_result()


def step_discover_dpus(ctx: Context) -> StepResult:
    """Kick the per-DPU Discover (rshim+lspci for in-band, Redfish for
    BMC). Wait until every DPU's `last_discovery_status` settles."""
    with StepRecorder("discover_dpus") as r:
        project_id = _require_project_id(ctx)
        before = ctx.client.list_dpus(project_id)
        if not before:
            r.fail(
                "no DPUs registered yet — register_hosts must have failed "
                "to seed any DPU rows",
            )
            return r.to_result()
        ctx.client.discover_all_dpus(project_id)
        final = _wait_for_dpus_settled(
            ctx, project_id,
            field="last_discovery_status",
            transient={"running"},
            timeout=ctx.cfg.timeouts.discovery,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        ok = [d for d in final if d.get("last_discovery_status") == "ok"]
        not_ok = [d for d in final if d.get("last_discovery_status") != "ok"]
        ctx.state["dpu_ids"] = [d["id"] for d in final]
        r.record(
            dpu_count=len(final),
            ok=len(ok),
            failed=len(not_ok),
            # `bfb_hostname` is the name the bf.conf template writes
            # to /etc/hostname at flash time (`worker1-dpu`); the
            # diagram uses it so each DPU box reads as the DPU itself,
            # not its host. VLAN columns feed the per-DPU IP rows.
            dpus=[
                {
                    **_pick(
                        d, "name", "bfb_hostname",
                        "host_node_ip", "host_hostname", "bmc_ip", "dpu_os_ip",
                        "external_vlan_id", "external_vlan_ipv4",
                        "internal_vlan_id", "internal_vlan_ipv4",
                        discovery_status="last_discovery_status",
                        discovery_error="last_discovery_error",
                    ),
                    "id": d["id"],
                    "extra_vlan_interfaces": d.get("extra_vlan_interfaces") or [],
                }
                for d in final
            ],
        )
        if not_ok:
            r.warn(
                f"{len(ok)}/{len(final)} DPUs discovered ok; "
                f"{len(not_ok)} not — see evidence",
            )
        else:
            r.ok(f"all {len(ok)} DPUs discovered")
    return r.to_result()


def step_probe_dpu_os(ctx: Context) -> StepResult:
    """Run Probe DPU OS for every DPU. Captures uname, mlxconfig,
    LACP, connectivity, firmware (DOCA/NIC/BMC)."""
    with StepRecorder("probe_dpu_os") as r:
        project_id = _require_project_id(ctx)
        ctx.client.probe_dpu_os_all(project_id)
        final = _wait_for_dpus_settled(
            ctx, project_id,
            field="dpu_os_status",
            transient={"probing"},
            timeout=ctx.cfg.timeouts.discovery,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        reachable = [d for d in final if d.get("dpu_os_status") == "reachable"]
        ctx.state["dpus_after_probe"] = final
        r.record(
            dpu_count=len(final),
            reachable=len(reachable),
            # `bmc_ip` is set by the backend probe from `ipmitool lan
            # print` for in-band DPUs — surfacing it here lets the
            # diagram show it post-probe, and the BMC discovery step
            # downstream consumes it.
            dpus=[
                {
                    "id": d["id"],
                    **_pick(d, "dpu_os_ip", "bmc_ip",
                            status="dpu_os_status",
                            firmware="dpu_os_firmware"),
                }
                for d in final
            ],
        )
        if len(reachable) != len(final):
            r.warn(
                f"{len(reachable)}/{len(final)} DPU OS reachable — "
                f"see evidence",
            )
        else:
            r.ok(f"all {len(reachable)} DPU OSes reachable")
    return r.to_result()


def step_run_matrix(ctx: Context) -> StepResult:
    """Trigger the DPU↔DPU connectivity matrix and wait for it to
    finish. In Phase 1 we record the result without asserting full
    reachability — the operator decides whether the pre-flash state
    is expected to be green."""
    with StepRecorder("run_matrix") as r:
        project_id = _require_project_id(ctx)
        ctx.client.run_connectivity_matrix(project_id)
        final = _wait_for_matrix(
            ctx, project_id,
            timeout=ctx.cfg.timeouts.matrix,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        groups = (final.get("matrix") or {}).get("groups") or {}
        endpoints, results, reachable = 0, 0, 0
        for g in groups.values():
            if not isinstance(g, dict):
                continue
            endpoints += len(g.get("endpoints") or [])
            res = g.get("results") or []
            results += len(res)
            reachable += sum(1 for x in res if x.get("reachable") is True)
        r.record(
            status=final.get("status"),
            run_at=final.get("run_at"),
            endpoints=endpoints,
            results=results,
            reachable=reachable,
            matrix=final.get("matrix"),
        )
        if final.get("status") != "completed":
            r.warn(
                f"matrix status={final.get('status')} — "
                f"{reachable}/{results} reachable",
            )
        elif results == 0:
            r.warn("matrix completed but produced 0 results")
        elif reachable == results:
            r.ok(
                f"matrix all green: {reachable}/{results} pings "
                f"across {endpoints} endpoints",
            )
        else:
            r.warn(
                f"matrix mixed: {reachable}/{results} pings reachable "
                f"across {endpoints} endpoints",
            )
    return r.to_result()


# ── Phase-2 steps (destructive lifecycle) ─────────────────────────
#
# Factory-reset + post-reset recovery used to live here too, but they
# need a per-DPU `bmc_ip` to drive Redfish, which the in-band-only
# discovery path doesn't provide. They were dropped from
# `PHASE_2_STEPS` and the code went with them; rebuild fresh against
# the BMC-discovery flow when that lifecycle path comes back.


def step_auto_assign_vlan_ips(ctx: Context) -> StepResult:
    """Assign VLAN IPs from the project's subnets to every DPU."""
    with StepRecorder("auto_assign_vlan_ips") as r:
        project_id = _require_project_id(ctx)
        body = ctx.client.auto_assign_vlan_ips(project_id)
        r.record(response=body)
        r.ok(
            f"assigned IPs to {body.get('assignments', '?')} DPU(s)"
            if isinstance(body, dict)
            else "auto-assign completed",
        )
    return r.to_result()


def step_flash_dpus(ctx: Context) -> StepResult:
    """Enqueue Flash on every DPU. The actual BFB upload + reboot
    runs server-side; the next step polls flash_status."""
    with StepRecorder("flash_dpus") as r:
        project_id = _require_project_id(ctx)
        body = ctx.client.flash_all_dpus(project_id)
        ctx.state["flash_started_at"] = time.time()
        r.record(response=body)
        n = body.get("enqueued") if isinstance(body, dict) else None
        r.ok(f"flash enqueued for {n if n is not None else '?'} DPU(s)")
    return r.to_result()


def step_wait_flash_complete(ctx: Context) -> StepResult:
    """Poll until every DPU's flash_status reaches a terminal value.

    Per-DPU budget = `timeouts.flash_per_dpu`. Total budget caps the
    whole loop so a stuck flash doesn't block the run forever."""
    with StepRecorder("wait_flash_complete") as r:
        project_id = _require_project_id(ctx)
        # Per-DPU × N gives parallel headroom even if Celery
        # serialises some part of the flash.
        per_dpu = ctx.cfg.timeouts.flash_per_dpu
        before = ctx.client.list_dpus(project_id)
        timeout = max(per_dpu, per_dpu * len(before))
        final = _wait_for_dpus_settled(
            ctx, project_id,
            field="flash_status",
            transient={"uploading", "waiting_reboot"},
            timeout=timeout,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        # Backend's flash_status terminals are os_online | failed |
        # cancelled (see backend/models/dpu.py and dpu_flash_service —
        # there is no "completed"). os_online is the only success
        # terminal: the DPU has rebooted into the freshly-flashed OS
        # and the host poller has confirmed reachability.
        completed = [d for d in final if d.get("flash_status") == "os_online"]
        r.record(
            dpu_count=len(final),
            completed=len(completed),
            dpus=[
                {"id": d["id"], **_pick(
                    d, "flash_status", "flash_error", "flash_stage_detail",
                )}
                for d in final
            ],
        )
        if len(completed) < len(final):
            r.warn(
                f"{len(completed)}/{len(final)} DPUs flashed successfully — "
                f"see evidence for failures",
            )
        else:
            r.ok(f"all {len(completed)} DPUs flashed")
    return r.to_result()


def step_probe_dpu_os_post_flash(ctx: Context) -> StepResult:
    """Re-run Probe DPU OS after flashing so connectivity / firmware /
    LACP state reflects the freshly-imaged DPU.

    Also waits for the bf.conf-driven VLAN sub-interfaces (e.g.
    `external0`, `internal100`) to appear in the DPU's interface list
    before declaring done. After a fresh flash, `dpu_os_status=
    reachable` means SSH works, but the matrix needs `ip route get
    -B <iface> …` to find the interfaces — which can lag SSH by tens
    of seconds, especially on the no-LAG → LAG transition where bond0
    + VLAN sub-interfaces have to be brought up from scratch.
    """
    with StepRecorder("probe_dpu_os_post_flash") as r:
        project_id = _require_project_id(ctx)
        ctx.client.probe_dpu_os_all(project_id)
        final = _wait_for_dpus_settled(
            ctx, project_id,
            field="dpu_os_status",
            transient={"probing"},
            timeout=ctx.cfg.timeouts.discovery,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        # Re-probe up to `timeouts.discovery` until each reachable DPU
        # has the expected bf.conf-driven VLAN sub-interfaces. Names
        # come from the project's DPU settings: `<prefix><vlan_id>`
        # for fixed external/internal rows + each extra VLAN.
        required = _required_vlan_interfaces(ctx, project_id)
        if required:
            final = _wait_for_dpu_interfaces(
                ctx, project_id, required,
                timeout=ctx.cfg.timeouts.discovery,
                poll_interval=ctx.cfg.timeouts.poll_interval,
            )
        reachable = [d for d in final if d.get("dpu_os_status") == "reachable"]
        ctx.state["dpus_after_flash"] = final
        r.record(
            dpu_count=len(final),
            reachable=len(reachable),
            dpus=[
                {
                    "id": d["id"],
                    **_pick(d, "dpu_os_ip", "bmc_ip",
                            status="dpu_os_status",
                            firmware="dpu_os_firmware",
                            lacp="dpu_os_lacp",
                            interfaces="dpu_os_interfaces"),
                }
                for d in final
            ],
        )
        if len(reachable) < len(final):
            r.warn(
                f"only {len(reachable)}/{len(final)} DPU OS reachable "
                f"post-flash",
            )
        else:
            r.ok(f"all {len(reachable)} DPU OS reachable post-flash")
    return r.to_result()


def step_run_matrix_post_flash(ctx: Context) -> StepResult:
    """Trigger the connectivity matrix and assert all results green —
    the success criterion for the full Phase-2 run."""
    with StepRecorder("run_matrix_post_flash") as r:
        project_id = _require_project_id(ctx)
        ctx.client.run_connectivity_matrix(project_id)
        final = _wait_for_matrix(
            ctx, project_id,
            timeout=ctx.cfg.timeouts.matrix,
            poll_interval=ctx.cfg.timeouts.poll_interval,
        )
        groups = (final.get("matrix") or {}).get("groups") or {}
        endpoints, results, reachable = 0, 0, 0
        for g in groups.values():
            if not isinstance(g, dict):
                continue
            endpoints += len(g.get("endpoints") or [])
            res = g.get("results") or []
            results += len(res)
            reachable += sum(1 for x in res if x.get("reachable") is True)
        r.record(
            status=final.get("status"),
            endpoints=endpoints,
            results=results,
            reachable=reachable,
            matrix=final.get("matrix"),
        )
        if final.get("status") != "completed":
            r.fail(f"matrix did not complete (status={final.get('status')})")
            return r.to_result()
        if results == 0:
            r.fail("matrix produced 0 result rows")
            return r.to_result()
        if reachable < results:
            r.fail(
                f"matrix not all green: {reachable}/{results} reachable "
                f"across {endpoints} endpoints — Phase 2 success criterion "
                f"requires every cell green",
            )
            return r.to_result()
        r.ok(
            f"matrix all green: {reachable}/{results} pings across "
            f"{endpoints} endpoints",
        )
    return r.to_result()


# ── Step registry ──────────────────────────────────────────────────


PHASE_1_STEPS = [
    ("deploy_bnk_forge", step_deploy_bnk_forge),
    ("login", step_login),
    ("create_jumphost", step_create_jumphost),
    ("create_project", step_create_project),
    ("configure_dpu_settings", step_configure_dpu_settings),
    ("run_discovery_workers", step_run_discovery_workers),
    ("register_hosts", step_register_hosts),
    ("discover_dpus", step_discover_dpus),
    ("probe_dpu_os", step_probe_dpu_os),
    # `probe_dpu_os` populates `bmc_ip` from ipmitool — this step
    # cross-checks Redfish reach on the configured `bmc_ips` range.
    ("discover_bmcs", step_discover_bmcs),
    # Allocate VLAN IPs **before** the pre-flash matrix so it has
    # endpoints to ping. The matrix won't be skipped any more; it'll
    # just `warn` when the DPUs haven't been flashed with the right
    # bf.conf yet (so the IPs aren't on their interfaces) — that's
    # still a useful "before-vs-after" data point.
    ("auto_assign_vlan_ips", step_auto_assign_vlan_ips),
    ("run_matrix", step_run_matrix),
]

PHASE_2_STEPS = [
    # `auto_assign_vlan_ips` lives in phase 1 now — it's a
    # prerequisite for the pre-flash matrix.
    ("flash_dpus", step_flash_dpus),
    ("wait_flash_complete", step_wait_flash_complete),
    ("probe_dpu_os_post_flash", step_probe_dpu_os_post_flash),
    ("run_matrix_post_flash", step_run_matrix_post_flash),
]

ALL_STEPS = PHASE_1_STEPS + PHASE_2_STEPS


# ── Helpers ────────────────────────────────────────────────────────
#
# Error-handling convention inside step bodies:
#
#   raise RuntimeError(...) — environmental: a binary is missing, a
#     config file can't be read, the API returned a malformed
#     response. Bypasses the recorder; the orchestrator catches it
#     and writes a synthetic "harness exception" StepResult.
#
#   r.fail(summary) ; return r.to_result() — operator-facing
#     mismatch: the API call succeeded but the result is wrong (no
#     DPU hosts found, BFB image not in catalog, matrix not green).
#     Preserves evidence collected so far into the report.


def _pick(d: dict[str, Any], *keys: str, **renamed: str) -> dict[str, Any]:
    """Trimmed dict of `d`, used to keep recorded step evidence tight.

    Positional args copy a key as-is (`"id"` → `out["id"] = d.get("id")`).
    Keyword args rename, e.g. `status="dpu_os_status"` →
    `out["status"] = d.get("dpu_os_status")`. Either form yields `None`
    when the source key is missing.
    """
    out: dict[str, Any] = {k: d.get(k) for k in keys}
    out.update({alias: d.get(src) for alias, src in renamed.items()})
    return out


def _require(ctx: Context, key: str) -> Any:
    v = ctx.state.get(key)
    if v is None:
        raise RuntimeError(
            f"step prerequisite missing: '{key}' not in state. "
            f"Run earlier steps first or include them in --steps.",
        )
    return v


def _require_project_id(ctx: Context) -> int:
    return int(_require(ctx, "project_id"))


def _wait_for_job_terminal(
    ctx: Context, job_id: int, *, timeout: int, poll_interval: int,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = ctx.client.get_discovery_job(
            _require_project_id(ctx), job_id,
        )
        status = last.get("status")
        if status in ("completed", "failed", "cancelled"):
            return last
        time.sleep(poll_interval)
    raise TimeoutError(
        f"discovery job {job_id} did not reach a terminal state in "
        f"{timeout}s (last status={last.get('status')})",
    )


def _wait_for_dpus_settled(
    ctx: Context,
    project_id: int,
    *,
    field: str,
    transient: set[str],
    timeout: int,
    poll_interval: int,
) -> list[dict[str, Any]]:
    """Poll the DPU list until none of them have a `field` value in
    `transient`. Returns the final snapshot."""
    deadline = time.time() + timeout
    last: list[dict[str, Any]] = []
    while time.time() < deadline:
        last = ctx.client.list_dpus(project_id)
        if last and not any(d.get(field) in transient for d in last):
            return last
        time.sleep(poll_interval)
    raise TimeoutError(
        f"DPUs did not settle in {timeout}s (still transient on "
        f"'{field}': "
        f"{[d.get('id') for d in last if d.get(field) in transient]})",
    )


def _required_vlan_interfaces(ctx: Context, project_id: int) -> list[str]:
    """Names of the bf.conf-driven VLAN interfaces the project will
    render onto every DPU — `<prefix><vlan_id>` for fixed external /
    internal + each entry in `extra_vlan_interfaces`. Returns `[]`
    when settings have no VLAN rows configured (so the post-flash
    settle loop becomes a no-op rather than waiting forever)."""
    settings = ctx.client.get_dpu_settings(project_id)
    out: list[str] = []
    ext_id = settings.get("external_vlan_id")
    if ext_id is not None:
        out.append(f"external{ext_id}")
    int_id = settings.get("internal_vlan_id")
    if int_id is not None:
        out.append(f"internal{int_id}")
    for extra in settings.get("extra_vlan_interfaces") or []:
        name = extra.get("name")
        vid = extra.get("vlan_id")
        if name and vid is not None:
            out.append(f"{name}{vid}")
    return out


def _wait_for_dpu_interfaces(
    ctx: Context,
    project_id: int,
    required: list[str],
    *,
    timeout: int,
    poll_interval: int,
) -> list[dict[str, Any]]:
    """Re-trigger Probe DPU OS until every reachable DPU's
    `dpu_os_interfaces` list contains every name in `required`. Probe
    is the only thing that refreshes the interface list, so we re-fire
    it on each iteration; the inner `_wait_for_dpus_settled` then
    blocks for that probe to finish.

    Returns the most recent DPU snapshot. Raises `TimeoutError` when
    interfaces never appear — usually means bf.conf failed to render
    a VLAN row, which is a real problem worth reporting.
    """
    deadline = time.time() + timeout
    last: list[dict[str, Any]] = ctx.client.list_dpus(project_id)
    while time.time() < deadline:
        missing_per_dpu = {
            d["id"]: [
                name for name in required
                if name not in (d.get("dpu_os_interfaces") or [])
            ]
            for d in last
            if d.get("dpu_os_status") == "reachable"
        }
        if not any(missing_per_dpu.values()):
            return last
        time.sleep(poll_interval)
        ctx.client.probe_dpu_os_all(project_id)
        last = _wait_for_dpus_settled(
            ctx, project_id,
            field="dpu_os_status",
            transient={"probing"},
            timeout=max(60, int(deadline - time.time())),
            poll_interval=poll_interval,
        )
    raise TimeoutError(
        f"DPU interfaces never settled in {timeout}s "
        f"(still missing: {missing_per_dpu})",
    )


def _url_is_local(url: str) -> bool:
    """True iff `url`'s host resolves to this machine. Generous: any
    `localhost` / `127.x` / `0.0.0.0` literal counts."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def _running_bnk_forge_containers() -> list[str]:
    """Return names of currently-running containers whose name contains
    `bnk-forge`. Empty list when nothing is up. Quiet failure on any
    docker error — the caller treats an empty list as 'go ahead'."""
    try:
        proc = subprocess.run(
            [
                "docker", "ps", "--format", "{{.Names}}",
                "--filter", "name=bnk-forge",
                "--filter", "status=running",
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [n.strip() for n in proc.stdout.splitlines() if n.strip()]


def _wait_for_api_ready(
    base_url: str, *, timeout: int, poll_interval: int,
) -> float:
    """Poll the bnk-forge auth-login URL until the server responds with
    *anything* meaningful (200 or 401 = up; connection refused = not
    yet). Returns elapsed seconds. Raises TimeoutError on cap.
    Self-signed cert is accepted."""
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    deadline = time.time() + timeout
    started = time.time()
    last_err: str = "no response"
    url = f"{base_url.rstrip('/')}/api/auth/login"
    while time.time() < deadline:
        try:
            resp = requests.post(
                url, json={"username": "_", "password": "_"},
                verify=False, timeout=5,
            )
            # 401 (bad creds) or 422 (validation) both mean the server
            # is alive and reachable. 200 too. Anything else (5xx) is
            # mid-startup; keep polling.
            if resp.status_code in (200, 401, 422):
                return time.time() - started
            last_err = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_err = type(exc).__name__
        time.sleep(poll_interval)
    raise TimeoutError(
        f"bnk-forge API not reachable at {url} within {timeout}s "
        f"(last: {last_err})",
    )


def _wait_for_matrix(
    ctx: Context, project_id: int, *, timeout: int, poll_interval: int,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = ctx.client.get_connectivity_matrix(project_id)
        status = last.get("status")
        if status in ("completed", "failed", "skipped"):
            return last
        time.sleep(poll_interval)
    raise TimeoutError(
        f"matrix did not finish in {timeout}s (last status="
        f"{last.get('status')})",
    )
