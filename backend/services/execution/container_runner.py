"""Container step runner — the execution primitive for artifact components.

An *artifact* (kind ``container_image``) is procedural: its lifecycle is a set
of steps, each of which invokes the artifact's OWN image with an argv vector.
This module provides the substrate that actually runs one such step.

Design:
  - ``ContainerRunner`` is the abstract contract: ``run_step(...) -> StepResult``.
  - ``DockerRunner`` implements it with the ``docker`` CLI talking to a
    docker-socket-proxy (``DOCKER_HOST=tcp://docker-socket-proxy:2375``) — the
    worker runs *sibling* containers, never mounting the raw host socket.

Security / supply-chain rules enforced here:
  - The image is always pinned by digest (``repo@sha256:...``). A floating tag
    is rejected before it reaches the docker argv (immutability).
  - The step argv runs in the image directly — no shell is interposed.
  - Pull credentials are delivered via a transient ``--authfile`` written to a
    private temp ``DOCKER_CONFIG`` dir and removed after the run; secrets never
    land in argv or the persistent workspace.
  - Resource limits (cpus / memory / pids) and a wall-clock timeout bound the
    blast radius of a misbehaving artifact image.

All methods are synchronous — Celery workers are sync.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from utils.security import validate_cli_arg

logger = logging.getLogger(__name__)


class ContainerKillUnavailableError(RuntimeError):
    """The docker endpoint could not be reached to enumerate/kill containers.

    Distinct from "no containers were running": a cancel releases the module
    lock on the strength of a kill, so an unreachable daemon must not be
    reported as a successful stop.
    """

# A digest pin looks like ``<repo>@sha256:<64 hex>``. Anything else (a floating
# tag, or a tag-only reference) is rejected — running an artifact requires an
# immutable digest so the bytes can never silently change underneath us.
_DIGEST_REF_RE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")

# Docker host the worker talks to. The socket proxy (tecnativa/docker-socket-proxy)
# exposes a scoped subset of the Engine API (containers create/start/logs/wait).
DEFAULT_DOCKER_HOST = "tcp://docker-socket-proxy:2375"

# Dedicated bridge network for artifact steps. Created by compose (see the
# `artifact-runner` network) so artifact containers do NOT land on the daemon's
# default bridge alongside whatever else the operator runs. `--network none` is
# not an option: artifacts (roksbnkctl et al.) must reach cloud control planes,
# so this network keeps NAT egress while isolating them from other containers.
DEFAULT_ARTIFACT_NETWORK = "bnk-forge-artifacts"

# Step execution is DETACHED + polled rather than attached, so no single request
# to the docker endpoint outlives a step (an attached `docker run` parks one on
# /containers/{id}/wait for the whole run). These bound the poll loop.
_POLL_INTERVAL_SECONDS = 2.0     # completion granularity; also the log-resume backoff
_DOCKER_CALL_TIMEOUT = 30        # every individual docker call is short and bounded
# How long the endpoint may stay unreachable before the step is failed. This is
# wall-clock, not a poll count: the container keeps running whether or not we
# can see it, so an unreachable endpoint costs nothing to wait out, and the
# proxy is `restart: unless-stopped` — a restart of it must not fail a step.
# A poll count would also be a misleading budget, since one failing poll can
# take anywhere from milliseconds to _DOCKER_CALL_TIMEOUT.
_POLL_FAILURE_GRACE_SECONDS = 300
_STREAM_JOIN_TIMEOUT = 5         # grace for the log follower to wind up

# Labels stamped on every detached step container. See build_run_argv.
_LABEL_STEP = "bnkforge.step"
_LABEL_WORKSPACE = "bnkforge.workspace"
_LABEL_TASK = "bnkforge.task"
_MAX_NAME_PREFIX = 48            # keep generated container names comfortably legal

# Image users that mean "root". Docker leaves Config.User empty when the image
# never declares a USER, which the daemon runs as uid 0.
_ROOT_USERS = {"", "0", "root", "0:0", "root:root"}

# Env var names: letters/digits/underscore, not starting with a digit.
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


_RFC3339_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d:.]+Z?\S*$")


def _strip_timestamp(line: str) -> tuple[str | None, str]:
    """Split a ``docker logs --timestamps`` line into (timestamp, text).

    Returns ``(None, line)`` unchanged when the line does not start with one, so
    a daemon that omits the prefix degrades to "no resume point" rather than to
    a mangled first token.
    """
    stamp, sep, rest = line.partition(" ")
    if sep and _RFC3339_PREFIX.match(stamp):
        return stamp, rest
    return None, line


def _validate_env_keys(env: dict[str, str]) -> None:
    """Reject any environment variable name that is not a valid shell/POSIX key.

    Shared by both runners (Docker delivers env via ``-e``, Kubernetes via a
    Secret) so the same rule applies regardless of substrate.
    """
    for key in env or {}:
        if not _ENV_KEY_RE.match(key):
            raise ValueError(f"Invalid environment variable name: {key!r}")


@dataclass(frozen=True)
class ResourceLimits:
    """Resource ceilings applied to a container step.

    ``None`` for any field means "do not pass that flag" (engine default).
    """

    cpus: str | None = None       # docker --cpus, e.g. "1", "0.5"
    memory: str | None = None     # docker --memory, e.g. "512m", "2g"
    pids: int | None = None       # docker --pids-limit


@dataclass
class StepResult:
    """Result of running one container step."""

    success: bool
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_seconds: float = 0.0


@dataclass
class StepSpec:
    """A fully-resolved container step ready to execute.

    The caller (the container engine/task) resolves the manifest + workspace +
    credentials into this engine-agnostic spec; the runner just executes it.
    """

    image_digest: str                              # repo@sha256:... (digest-pinned)
    args: list[str]                                # argv passed to the image
    workspace_host_path: str                       # host path (bind-mount fallback)
    mount_path: str                                # where the workspace mounts inside the container
    # Preferred persistent-workspace mount: the named volume + per-component
    # subpath. On Docker Desktop a host-path bind does NOT share storage with the
    # worker's named-volume mount, so state would not persist — mount by name.
    workspace_volume: str | None = None            # named volume (None ⟹ host-path bind)
    workspace_subpath: str | None = None           # per-component subpath in the volume
    command: str | None = None                     # optional entrypoint override token (rare)
    env: dict[str, str] = field(default_factory=dict)
    home_env: dict[str, str] = field(default_factory=dict)  # state.home_env vars
    limits: ResourceLimits = field(default_factory=ResourceLimits)
    timeout_seconds: int = 1800
    pull_authfile_json: str | None = None          # transient dockerconfigjson for the pull

    # Stable per-component identity. The KubernetesRunner uses ``component_key``
    # to name the per-component PVC, per-step Job, and per-step Secret
    # deterministically; the DockerRunner uses it to name the step container.
    component_key: str | None = None
    step_name: str | None = None
    # Celery task this step belongs to. Stamped onto the container as a label so
    # the reaper can tell a container whose task is still running from one whose
    # worker died — the same live/dead signal execution_janitor already uses.
    celery_task_id: str | None = None


class ContainerRunner(ABC):
    """Contract for executing a single artifact step in a container."""

    @abstractmethod
    def run_step(
        self,
        spec: StepSpec,
        on_output: Callable[[str], None] | None = None,
    ) -> StepResult:
        """Run one step (the artifact's own image with ``spec.args``)."""
        ...

    def health_check(self) -> bool:
        """Return True when the runtime can execute right now."""
        return True


class DockerRunner(ContainerRunner):
    """Runs artifact steps via the ``docker`` CLI against the socket proxy.

    Sibling-container model: the worker container has only ``docker-ce-cli`` and
    ``DOCKER_HOST`` pointed at the proxy. Containers it starts are siblings on
    the host's daemon, so the persistent workspace named-volume subpath must be
    addressed by its *host* path (``spec.workspace_host_path``) for the bind
    mount to resolve on the daemon side.
    """

    def __init__(
        self,
        docker_host: str | None = None,
        docker_bin: str = "docker",
        network: str | None = None,
    ):
        # Explicit arg > env > default proxy address.
        self.docker_host = docker_host or os.environ.get("DOCKER_HOST") or DEFAULT_DOCKER_HOST
        self.docker_bin = docker_bin
        # Empty string (CONTAINER_ARTIFACT_NETWORK="") explicitly opts out and
        # falls back to the daemon default — distinct from unset, which gets the
        # dedicated network.
        env_network = os.environ.get("CONTAINER_ARTIFACT_NETWORK")
        chosen = network if network is not None else env_network
        if chosen is None:
            chosen = DEFAULT_ARTIFACT_NETWORK
        self.network = chosen.strip() or None

    # -------------------------------------------------------------------------
    # argv construction (pure — unit-tested without a live daemon)
    # -------------------------------------------------------------------------
    def build_run_argv(
        self,
        spec: StepSpec,
        authfile_dir: str | None = None,
        *,
        detach: bool = False,
        container_name: str | None = None,
    ) -> list[str]:
        """Build the full ``docker run`` argv for a step.

        Pure function of the spec (+ optional transient authfile dir). Kept
        separate from execution so it can be asserted in unit tests with no
        daemon and no subprocess.

        ``detach=True`` starts the container in the background under
        ``container_name`` instead of attaching. Execution uses this form: an
        attached ``docker run`` holds ONE HTTP request open on
        ``/containers/{id}/wait`` for the whole life of the step, so any idle
        timeout anywhere on the DOCKER_HOST path (the socket proxy's haproxy
        ``timeout client`` defaults to 10m) kills a long step mid-flight. The
        detached form keeps every request short and polls for completion.

        ``--rm`` is dropped in the detached form: the exit code has to be read
        back with ``docker inspect`` after the container stops, so the runner
        removes it explicitly instead.
        """
        self._validate_spec(spec)

        argv: list[str] = [self.docker_bin]

        # --config points docker at a private dir holding the pull authfile.
        if authfile_dir:
            argv += ["--config", authfile_dir]

        if detach:
            if not container_name:
                raise ValueError("detach=True requires container_name")
            validate_cli_arg("name", container_name)
            argv += ["run", "--detach", "--name", container_name]
            # Labels, not the name, are what makes an orphan findable. Dropping
            # --rm moved cleanup out of the daemon and into a `finally` that a
            # SIGKILLed worker never reaches, so something has to be able to
            # answer "whose container is this?" afterwards:
            #   step      — this is ours to reap at all
            #   workspace — which persistent workspace it is writing to, so a
            #               retry can clear its own predecessor before mounting
            #   task      — which Celery task, so the reaper can compare against
            #               the live set the janitor already computes
            argv += ["--label", f"{_LABEL_STEP}=1"]
            if spec.workspace_subpath:
                argv += ["--label", f"{_LABEL_WORKSPACE}={spec.workspace_subpath}"]
            if spec.celery_task_id:
                argv += ["--label", f"{_LABEL_TASK}={spec.celery_task_id}"]
        else:
            argv += ["run", "--rm"]

        # Baseline hardening (mirrors the KubernetesRunner security context):
        #  - no-new-privileges: a setuid binary in the image cannot escalate.
        #  - cap-drop=ALL: strip every Linux capability; artifact CLIs
        #    (roksbnkctl et al.) provision cloud resources over the network and
        #    need no kernel capabilities.
        #  - a dedicated bridge network, NOT the daemon default: artifacts must
        #    keep egress to cloud control planes (so `--network none` is out),
        #    but they have no business sitting on the default bridge next to
        #    whatever else the host runs.
        # Non-root is enforced separately, before the run — see
        # _assert_image_non_root(). Docker's `--user` would *override* the
        # image's USER (unlike K8s runAsNonRoot, which refuses a root image
        # without changing its uid), and forcing a uid breaks state writes to
        # the workspace volume. So we reject root images rather than remap them.
        argv += ["--security-opt", "no-new-privileges"]
        argv += ["--cap-drop", "ALL"]
        if self.network:
            validate_cli_arg("network", self.network)
            argv += ["--network", self.network]

        # Resource ceilings.
        limits = spec.limits
        if limits.cpus is not None:
            validate_cli_arg("cpus", str(limits.cpus))
            argv += ["--cpus", str(limits.cpus)]
        if limits.memory is not None:
            validate_cli_arg("memory", str(limits.memory))
            argv += ["--memory", str(limits.memory)]
        if limits.pids is not None:
            argv += ["--pids-limit", str(int(limits.pids))]

        # Persistent workspace. Prefer mounting the named volume by name + a
        # per-component subpath (shares storage with the worker; correct on
        # Docker Desktop). Fall back to a host-path bind only when configured
        # (WORKSPACE_HOST_BASE → workspace_volume is None).
        if spec.workspace_volume:
            argv += [
                "--mount",
                f"type=volume,source={spec.workspace_volume},"
                f"target={spec.mount_path},volume-subpath={spec.workspace_subpath}",
            ]
        else:
            argv += ["-v", f"{spec.workspace_host_path}:{spec.mount_path}"]
        argv += ["-w", spec.mount_path]

        # Declared home_env state vars + step env. Values are passed via -e
        # NAME=VALUE; keys are validated, values are opaque (never logged here).
        for key in sorted(spec.home_env):
            self._validate_env_key(key)
            argv += ["-e", f"{key}={spec.home_env[key]}"]
        for key in sorted(spec.env):
            self._validate_env_key(key)
            argv += ["-e", f"{key}={spec.env[key]}"]

        # The step args are the FULL argv (args[0] = the image's own binary).
        # Override the entrypoint so the args run as the literal command instead
        # of being appended to the image's ENTRYPOINT (e.g. an image whose
        # ENTRYPOINT is already `roksbnkctl` would otherwise run
        # `roksbnkctl roksbnkctl init`). An explicit spec.command (rare)
        # overrides the entrypoint and keeps the full args as its arguments.
        if spec.command:
            validate_cli_arg("command", spec.command)
            entrypoint, command_args = spec.command, list(spec.args)
        else:
            entrypoint, command_args = spec.args[0], list(spec.args[1:])

        argv += ["--entrypoint", entrypoint]
        # The digest-pinned image, then the argv the step runs IN that image.
        argv += [spec.image_digest, *command_args]
        return argv

    def build_logs_argv(
        self, container_name: str, *, follow: bool = False, since: str | None = None
    ) -> list[str]:
        """Stream (or fetch) a container's merged output, timestamped.

        ``--timestamps`` is always on and the prefix is stripped before the line
        is emitted, so the caller sees the same text as before. It is there so a
        resume can be expressed in the DAEMON's clock rather than the worker's:
        ``--since`` is interpreted daemon-side, and this whole design assumes a
        remote/proxied DOCKER_HOST, so the two clocks are not the same one. With
        the worker's wall clock a resume would skip output when the worker runs
        ahead and replay a lot when it runs behind. Feeding back a timestamp the
        daemon itself emitted removes the skew entirely.

        The stamps are RFC3339 with nanosecond precision and docker compares
        ``--since`` at that precision, so a resume repeats at most the final
        LINE rather than the final second. Either way it can never affect the
        step's RESULT, which comes from the state poll.
        """
        argv = [self.docker_bin, "logs", "--timestamps"]
        if follow:
            argv += ["--follow"]
        if since:
            argv += ["--since", since]
        argv += [container_name]
        return argv

    def build_state_argv(self, container_name: str) -> list[str]:
        """Read a container's liveness + exit code in one short call."""
        return [
            self.docker_bin,
            "inspect",
            "--format",
            "{{.State.Running}} {{.State.ExitCode}}",
            container_name,
        ]

    def build_kill_argv(self, container_name: str) -> list[str]:
        return [self.docker_bin, "kill", container_name]

    def build_rm_argv(self, container_name: str) -> list[str]:
        return [self.docker_bin, "rm", "--force", container_name]

    def build_ps_argv(self, *, label: str) -> list[str]:
        """Container ids carrying ``label``, running or not."""
        return [self.docker_bin, "ps", "--all", "--quiet", "--filter", f"label={label}"]

    def build_ps_owner_argv(self, *, label: str) -> list[str]:
        """``<id> <owning task>`` for each container carrying ``label``.

        One call rather than a ``ps`` followed by an ``inspect`` per container:
        this runs on every step start, and the owner is the whole reason for
        looking.
        """
        return [
            self.docker_bin, "ps", "--all", "--filter", f"label={label}",
            "--format", '{{.ID}} {{.Label "' + _LABEL_TASK + '"}}',
        ]

    def _clear_workspace_predecessors(self, spec: StepSpec, run_env: dict[str, str]) -> None:
        """Remove containers holding this step's workspace that nothing owns.

        NOT every container on this workspace — ``workspace_subpath`` is shared
        by design. ``WorkspaceManager.artifact_workspace_key`` returns the
        deployment group for ``state: {scope: deployment}``, so every module of
        a blueprint deployment resolves to the same ``{project}/bp-<release>``
        subpath, and ``parallel_tasks`` dispatches those modules in waves onto
        workers running ``--concurrency=4``. ``module_lock`` does not serialise
        them: it is keyed on ``module.id``, so two different modules sharing one
        workspace each hold their own lock and proceed. Sweeping on the
        workspace label alone would therefore ``rm --force`` a *live sibling's*
        step container, and the victim would report "Lost contact with the
        docker endpoint" — sending an operator after haproxy for a step another
        step killed.

        What this exists for: dropping ``--rm`` moved cleanup into a ``finally``
        block, and a worker killed by SIGKILL/OOM never runs it — the container
        keeps running. Then ``reset_stale_executions`` frees the task, it is
        retried, and ``_container_name`` mints a fresh uuid, so the orphan and
        the retry execute CONCURRENTLY against the same workspace. Two
        `tofu apply`s on one state directory is a corruption shape. A periodic
        reaper cannot close that — the retry starts seconds after the worker
        comes back, long before any sweep is due — so it is closed here.

        Ownership decides, using the label the reaper already relies on:

          - owned by a DIFFERENT task that is still live → spare. A concurrent
            sibling, not an orphan.
          - owned by THIS task → remove. Celery preserves ``task_id`` across
            ``retry()``, so "the owner is live" is true of our own predecessor;
            treating that as a reason to spare would reinstate exactly the
            corruption this function exists to prevent.
          - owner is not live → remove. An orphan from a dead worker.
          - no owner label → spare. It predates the labelling, and removing on a
            guess would kill a running deployment — worse than the leak.

        Best-effort by design. If the endpoint cannot be reached the step will
        fail on its own next call, and failing here would just mean a noisier
        error for the same cause.
        """
        if not spec.workspace_subpath:
            return
        label = f"{_LABEL_WORKSPACE}={spec.workspace_subpath}"
        try:
            from services.execution_janitor import get_live_task_ids

            listed = subprocess.run(
                self.build_ps_owner_argv(label=label),
                env=run_env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
            )
            rows = [ln for ln in (listed.stdout or "").splitlines() if ln.strip()]
            if not rows:
                return

            live = get_live_task_ids()
            if not live:
                # get_live_task_ids() degrades to an empty set on ANY failure —
                # import error, no redis client, or an exception mid-scan — and
                # all three just log and return set(). Read literally that says
                # "nothing is running", and this loop would then spare nothing
                # and rm --force every sibling on a shared workspace.
                #
                # It cannot mean that here: task_prerun fires record_task_start,
                # so OUR OWN task is in the set whenever the lookup works. Empty
                # therefore means "no answer", and deleting on no answer is how
                # a redis blip turns into a killed deployment. Skipping costs a
                # leaked container the reaper picks up later.
                logger.warning(
                    "Live-task set is empty — skipping the workspace sweep for %s "
                    "rather than treating an unavailable lookup as 'nothing is running'",
                    spec.workspace_subpath,
                )
                return
            for row in rows:
                cid, _, owner = row.strip().partition(" ")
                owner = owner.strip()
                if not cid:
                    continue
                if not owner:
                    logger.info(
                        "Leaving container %s on workspace %s alone — no owning task label",
                        cid, spec.workspace_subpath,
                    )
                    continue
                if owner != spec.celery_task_id and owner in live:
                    continue  # a live sibling step, not a predecessor
                logger.warning(
                    "Removing container %s holding workspace %s (task %s) before starting a "
                    "new step on it — %s",
                    cid, spec.workspace_subpath, owner,
                    "our own predecessor from a retry" if owner == spec.celery_task_id
                    else "its task is no longer live",
                )
                subprocess.run(
                    self.build_rm_argv(cid),
                    env=run_env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
                )
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Could not sweep predecessors for workspace %s: %s",
                           spec.workspace_subpath, exc)

    def build_pull_argv(self, spec: StepSpec, authfile_dir: str | None = None) -> list[str]:
        """Pull the digest-pinned image explicitly.

        `docker run` would pull implicitly, but the image has to be present
        locally before its config can be inspected for the non-root check —
        and a step must never start on an image we have not vetted.
        """
        argv: list[str] = [self.docker_bin]
        if authfile_dir:
            argv += ["--config", authfile_dir]
        argv += ["pull", spec.image_digest]
        return argv

    def build_inspect_user_argv(self, image_digest: str) -> list[str]:
        """Read the image's declared USER out of its local config."""
        return [
            self.docker_bin,
            "image",
            "inspect",
            "--format",
            "{{.Config.User}}",
            image_digest,
        ]

    @staticmethod
    def is_root_user(image_user: str | None) -> bool:
        """True when an image's declared USER resolves to uid 0.

        An image that never declares USER reports an empty string and runs as
        root — that is the common case and must be caught.
        """
        return (image_user or "").strip().lower() in _ROOT_USERS

    def _gate_image(
        self,
        spec: StepSpec,
        authfile_dir: str | None,
        on_output: Callable[[str], None] | None,
    ) -> StepResult | None:
        """Pull the image and refuse it if it would run as root.

        Returns ``None`` when the image is cleared to run, or a failed
        StepResult explaining the refusal. Fails closed: if the image cannot be
        pulled or its user cannot be read, the step does not run.
        """
        env = dict(os.environ)
        env["DOCKER_HOST"] = self.docker_host

        def _fail(message: str, stdout: str = "") -> StepResult:
            if on_output:
                on_output(message)
            logger.warning("Container step refused: %s (%s)", message, spec.image_digest)
            return StepResult(
                success=False,
                exit_code=126,  # "command found but not executable" — closest fit
                stdout=stdout + ("\n" if stdout else "") + message,
                stderr="",
            )

        pull = subprocess.run(
            self.build_pull_argv(spec, authfile_dir=authfile_dir),
            env=env, capture_output=True, text=True, timeout=spec.timeout_seconds or 1800,
        )
        if pull.returncode != 0:
            return _fail(
                f"Failed to pull {spec.image_digest}: {(pull.stderr or pull.stdout).strip()}"
            )

        inspect = subprocess.run(
            self.build_inspect_user_argv(spec.image_digest),
            env=env, capture_output=True, text=True, timeout=60,
        )
        if inspect.returncode != 0:
            return _fail(
                f"Could not read the image's USER: {(inspect.stderr or '').strip()}"
            )

        image_user = (inspect.stdout or "").strip()
        if self.is_root_user(image_user):
            return _fail(
                f"Artifact image {spec.image_digest} runs as root "
                f"(USER={image_user or '<unset>'}). Refusing to start it: the workspace is "
                f"mounted from the host, so a root container is a host-root write primitive. "
                f"Rebuild the image with a non-root USER."
            )

        if on_output:
            on_output(f"Image user: {image_user} (non-root ✓)")
        return None

    # -------------------------------------------------------------------------
    # execution
    # -------------------------------------------------------------------------
    def run_step(
        self,
        spec: StepSpec,
        on_output: Callable[[str], None] | None = None,
    ) -> StepResult:
        """Run one step to completion.

        The container is started DETACHED and its completion is discovered by
        polling, so no single request to the docker endpoint outlives a few
        seconds. An attached `docker run` instead parks one request on
        /containers/{id}/wait for the entire step, which made any idle timeout
        on the path a hard ceiling on step duration — the socket proxy's
        haproxy `timeout client` defaults to 10m, so every step longer than that
        died with `error waiting for container: unexpected EOF` (exit 125) and
        nothing naming the transport. Polling also means a proxy or worker
        restart no longer orphans a running step.

        The step's declared `timeout_seconds` is now the ONLY thing that ends a
        step early, which is what the artifact manifest already promises.
        """
        import time

        authfile_dir = self._write_pull_authfile(spec.pull_authfile_json)

        # Pull, then vet the image BEFORE anything of it executes. A root image
        # is refused outright (the KubernetesRunner's runAsNonRoot equivalent):
        # with a bind/volume-mounted workspace, a root container is a host-root
        # write primitive.
        gate = self._gate_image(spec, authfile_dir, on_output)
        if gate is not None:
            self._cleanup_authfile(authfile_dir)
            return gate

        container_name = self._container_name(spec)
        argv = self.build_run_argv(
            spec, authfile_dir=authfile_dir, detach=True, container_name=container_name
        )

        run_env = dict(os.environ)
        run_env["DOCKER_HOST"] = self.docker_host

        if on_output:
            on_output(f"$ docker run {spec.image_digest} {' '.join(spec.args)}")

        # Before mounting the workspace, make sure nothing else still is.
        self._clear_workspace_predecessors(spec, run_env)

        started = time.monotonic()
        out_chunks: list[str] = []

        def _emit(line: str) -> None:
            out_chunks.append(line if line.endswith("\n") else line + "\n")
            if on_output:
                on_output(line.rstrip("\n"))

        try:
            create = subprocess.run(
                argv, env=run_env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT
            )
            if create.returncode != 0:
                detail = (create.stderr or create.stdout or "").strip()
                return StepResult(
                    success=False,
                    exit_code=create.returncode or 1,
                    stdout="",
                    stderr=f"Could not start the step container: {detail}",
                    duration_seconds=time.monotonic() - started,
                )

            # Follow the logs on a background thread purely for live output. It
            # is best-effort: if the stream drops (a transport hiccup), it is
            # resumed, and the step's RESULT never depends on it.
            stop_streaming = threading.Event()
            follow_state: dict[str, Any] = {"last_seen": None, "gave_up": False}
            streamer = threading.Thread(
                target=self._stream_logs,
                args=(container_name, run_env, _emit, stop_streaming, follow_state),
                daemon=True,
            )
            streamer.start()

            exit_code, timed_out, transport_error = self._await_exit(
                container_name, run_env, spec.timeout_seconds, started
            )

            stop_streaming.set()
            streamer.join(timeout=_STREAM_JOIN_TIMEOUT)

            # The follow is best-effort and the step's RESULT never depends on
            # it — but its OUTPUT does: the artifact's own stdout is the only
            # failure detail the engine surfaces. So when the follow never
            # attached, or died part-way and stopped resuming, read back what
            # it missed instead of silently truncating the step's output.
            if follow_state["gave_up"] or not out_chunks:
                # Guarded: by this point _await_exit has already determined the
                # step's outcome, so letting a log read raise would throw that
                # away and surface a successful step as a generic failure —
                # exactly the dependency on the follow the docstring says does
                # not exist. Losing some output is the lesser failure.
                try:
                    tail = subprocess.run(
                        self.build_logs_argv(
                            container_name, since=follow_state["last_seen"]
                        ),
                        env=run_env, capture_output=True, text=True,
                        timeout=_DOCKER_CALL_TIMEOUT,
                    )
                    if tail.stdout:
                        for line in tail.stdout.splitlines():
                            _emit(_strip_timestamp(line)[1])
                except Exception as exc:
                    logger.warning(
                        "Could not read back the step's remaining output (%s); the "
                        "result below is unaffected", exc,
                    )
        finally:
            self._remove_container(container_name, run_env)
            self._cleanup_authfile(authfile_dir)

        duration = time.monotonic() - started
        stdout = "".join(out_chunks)

        if timed_out:
            logger.warning(
                "Container step timed out after %ss: %s", spec.timeout_seconds, spec.image_digest
            )
            return StepResult(
                success=False, exit_code=124, stdout=stdout, stderr="",
                timed_out=True, duration_seconds=duration,
            )

        if transport_error is not None:
            # Name the transport. The old failure mode surfaced as a bare
            # `unexpected EOF` from the docker CLI, which points at nothing.
            return StepResult(
                success=False, exit_code=125, stdout=stdout,
                stderr=(
                    f"Lost contact with the docker endpoint ({self.docker_host}) while the step "
                    f"was running: {transport_error}. Container '{container_name}' may still be "
                    f"running there — `docker rm -f {container_name}` against that endpoint once "
                    f"it is reachable. If this reproduces at a consistent duration, check for an "
                    f"idle timeout on the DOCKER_HOST path (e.g. the socket proxy's haproxy "
                    f"`timeout client`)."
                ),
                duration_seconds=duration,
            )

        return StepResult(
            success=exit_code == 0,
            exit_code=exit_code,
            stdout=stdout,
            stderr="",  # merged into stdout for live ordering
            timed_out=False,
            duration_seconds=duration,
        )

    def _await_exit(
        self,
        container_name: str,
        run_env: dict[str, str],
        timeout_seconds: int | None,
        started: float,
    ) -> tuple[int, bool, str | None]:
        """Poll until the container stops. Returns (exit_code, timed_out, transport_error)."""
        import time

        first_failure_at: float | None = None
        last_error = ""
        while True:
            if timeout_seconds and (time.monotonic() - started) >= timeout_seconds:
                self._kill_container(container_name, run_env)
                return 124, True, None

            try:
                state = subprocess.run(
                    self.build_state_argv(container_name),
                    env=run_env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
                )
            except subprocess.TimeoutExpired as exc:
                state = None
                last_error = f"docker inspect timed out after {_DOCKER_CALL_TIMEOUT}s ({exc})"

            if state is not None and state.returncode == 0:
                first_failure_at = None
                running, _, code = (state.stdout or "").strip().partition(" ")
                if running.lower() == "false":
                    try:
                        return int(code.strip() or 1), False, None
                    except ValueError:
                        return 1, False, None
            else:
                # Losing sight of the container is not the same as the step
                # failing: it keeps running throughout, which is the point of
                # polling. Only a sustained loss of the endpoint is a real
                # transport failure — anything shorter (a blip, a restart of
                # the proxy in the path) is waited out.
                if state is not None:
                    last_error = (state.stderr or state.stdout or "").strip()
                if first_failure_at is None:
                    first_failure_at = time.monotonic()
                elif (time.monotonic() - first_failure_at) >= _POLL_FAILURE_GRACE_SECONDS:
                    return 125, False, last_error or "docker endpoint unreachable"

            time.sleep(_POLL_INTERVAL_SECONDS)

    def _stream_logs(
        self,
        container_name: str,
        run_env: dict[str, str],
        emit: Callable[[str], None],
        stop: threading.Event,
        state: dict[str, Any],
    ) -> None:
        """Follow the container's output, resuming if the stream drops.

        ``state`` reports progress back to the caller:
          - ``last_seen`` — the DAEMON's timestamp on the most recent line, as
            emitted by ``--timestamps``. A resume starts from there, so it
            repeats at most the final second rather than replaying everything
            since the follow attached, and it is immune to clock skew between
            the worker and a remote docker host.
          - ``gave_up`` — the follow died and will not resume, so the caller
            must read whatever it missed or the output is silently truncated.
        """
        import time

        while not stop.is_set():
            try:
                proc = subprocess.Popen(
                    self.build_logs_argv(
                        container_name, follow=True, since=state["last_seen"]
                    ),
                    env=run_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
            except Exception:  # pragma: no cover - defensive
                state["gave_up"] = True
                return
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    if stop.is_set():
                        break
                    stamp, text = _strip_timestamp(line.rstrip("\n"))
                    if stamp:
                        # The daemon's own clock, so a resume is skew-proof.
                        state["last_seen"] = stamp
                    emit(text)
            except Exception:  # pragma: no cover - transport hiccup
                pass
            finally:
                proc.kill()
            if stop.is_set():
                return
            # The follow ended while the step is still running (dropped stream).
            # Resume from the last line delivered rather than losing the rest.
            time.sleep(_POLL_INTERVAL_SECONDS)

    def _container_name(self, spec: StepSpec) -> str:
        """A unique, docker-legal name so the step can be polled and removed."""
        import uuid

        raw = f"bnkforge-{spec.component_key or 'step'}-{spec.step_name or 'run'}"
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "-", raw).strip("-_.") or "bnkforge-step"
        return f"{safe[:_MAX_NAME_PREFIX]}-{uuid.uuid4().hex[:8]}"

    def _kill_container(self, container_name: str, run_env: dict[str, str]) -> None:
        try:
            subprocess.run(
                self.build_kill_argv(container_name),
                env=run_env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
            )
        except Exception:  # pragma: no cover - best effort
            logger.warning("Could not kill container %s", container_name)

    def _remove_container(self, container_name: str, run_env: dict[str, str]) -> None:
        try:
            subprocess.run(
                self.build_rm_argv(container_name),
                env=run_env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
            )
        except Exception:  # pragma: no cover - best effort
            logger.warning("Could not remove container %s", container_name)

    def kill_task_containers(self, celery_task_id: str) -> list[str]:
        """Kill every step container owned by ``celery_task_id``.

        Returns the container ids killed (empty when none were running).

        This is the half of cancellation that Celery cannot do. ``revoke(
        terminate=True)`` SIGKILLs the worker-side client; the step container is
        detached on the host daemon and keeps running — still holding the
        workspace and still driving the vendor CLI against live infrastructure
        while Forge reports the operation cancelled (issue #462).

        Uses the same ``bnkforge.task`` ownership label the reaper reads, so a
        cancel and a reap agree on which container belongs to which task.

        Best-effort by design: a cancel must still reset DB state when the
        daemon is unreachable, so failures are logged and reported, never raised.
        """
        if not celery_task_id:
            return []

        run_env = dict(os.environ)
        run_env["DOCKER_HOST"] = self.docker_host

        try:
            listed = subprocess.run(
                self.build_ps_argv(label=f"{_LABEL_TASK}={celery_task_id}"),
                env=run_env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
            )
        except Exception as exc:
            # FileNotFoundError (no docker CLI) lands here too. Raising rather
            # than returning [] is the point: "I killed nothing" and "I could
            # not look" must not be the same answer, because the caller
            # releases the module lock on the strength of it.
            raise ContainerKillUnavailableError(
                f"cannot reach the docker daemon to kill containers for task "
                f"{celery_task_id}: {exc}"
            ) from exc

        if listed.returncode != 0:
            raise ContainerKillUnavailableError(
                f"docker ps for task {celery_task_id} exited "
                f"{listed.returncode}: {(listed.stderr or '').strip()}"
            )

        killed: list[str] = []
        for container_id in [ln.strip() for ln in (listed.stdout or "").splitlines() if ln.strip()]:
            try:
                result = subprocess.run(
                    self.build_kill_argv(container_id),
                    env=run_env, capture_output=True, text=True, timeout=_DOCKER_CALL_TIMEOUT,
                )
            except Exception as exc:
                logger.warning("kill_task_containers: kill %s failed: %s", container_id, exc)
                continue
            if result.returncode == 0:
                killed.append(container_id)
            else:
                # Already exited between ps and kill is the common case and benign.
                logger.info(
                    "kill_task_containers: kill %s exited %s: %s",
                    container_id, result.returncode, (result.stderr or "").strip(),
                )

        if killed:
            logger.info(
                "kill_task_containers: killed %d container(s) for task %s: %s",
                len(killed), celery_task_id, ", ".join(c[:12] for c in killed),
            )
        return killed

    def health_check(self) -> bool:
        """Return True when the docker CLI can reach the daemon via the proxy."""
        try:
            env = dict(os.environ)
            env["DOCKER_HOST"] = self.docker_host
            result = subprocess.run(
                [self.docker_bin, "version", "--format", "{{.Server.Version}}"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # helpers
    # -------------------------------------------------------------------------
    def _validate_spec(self, spec: StepSpec) -> None:
        if not _DIGEST_REF_RE.match(spec.image_digest or ""):
            raise ValueError(
                f"image must be digest-pinned (repo@sha256:...), got: {spec.image_digest!r}"
            )
        if not isinstance(spec.args, list) or not spec.args:
            raise ValueError("step args must be a non-empty argv list")
        for token in spec.args:
            if not isinstance(token, str):
                raise ValueError("step args entries must be strings")
        if spec.workspace_volume:
            if not spec.workspace_subpath:
                raise ValueError("workspace_subpath is required when workspace_volume is set")
            for field_name, value in (("workspace_volume", spec.workspace_volume),
                                      ("workspace_subpath", spec.workspace_subpath)):
                if "," in value or " " in value:
                    raise ValueError(f"{field_name} must not contain ',' or whitespace")
        elif not spec.workspace_host_path:
            raise ValueError("a workspace mount (workspace_volume or workspace_host_path) is required")
        if not spec.mount_path or not spec.mount_path.startswith("/"):
            raise ValueError("mount_path must be an absolute path inside the container")

    @staticmethod
    def _validate_env_key(key: str) -> None:
        if not _ENV_KEY_RE.match(key):
            raise ValueError(f"Invalid environment variable name: {key!r}")

    @staticmethod
    def _write_pull_authfile(authfile_json: str | None) -> str | None:
        """Write a transient dockerconfigjson into a private DOCKER_CONFIG dir.

        Returns the dir path (passed to ``docker --config``) or None when there
        are no pull credentials. The caller removes it after the run.
        """
        if not authfile_json:
            return None
        config_dir = tempfile.mkdtemp(prefix=".bnk_docker_auth_")
        try:
            os.chmod(config_dir, 0o700)
            config_path = os.path.join(config_dir, "config.json")
            # Producers return a base64-encoded dockerconfigjson (the cne_pull_secret
            # format). A Docker config.json on disk must be RAW JSON, so decode first.
            # Accept raw JSON too (defensive) — validate it parses so a malformed
            # secret fails fast (and we never log the contents).
            try:
                parsed = json.loads(authfile_json)
            except (json.JSONDecodeError, ValueError):
                parsed = json.loads(base64.b64decode(authfile_json).decode("utf-8"))
            with open(config_path, "w") as handle:
                json.dump(parsed, handle)
            os.chmod(config_path, 0o600)
        except Exception:
            shutil.rmtree(config_dir, ignore_errors=True)
            raise
        return config_dir

    @staticmethod
    def _cleanup_authfile(authfile_dir: str | None) -> None:
        if authfile_dir:
            shutil.rmtree(authfile_dir, ignore_errors=True)
