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
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from utils.security import validate_cli_arg

logger = logging.getLogger(__name__)

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

# Image users that mean "root". Docker leaves Config.User empty when the image
# never declares a USER, which the daemon runs as uid 0.
_ROOT_USERS = {"", "0", "root", "0:0", "root:root"}

# Env var names: letters/digits/underscore, not starting with a digit.
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


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

    # Stable per-component identity. The DockerRunner ignores these; the
    # KubernetesRunner uses ``component_key`` to name the per-component PVC,
    # per-step Job, and per-step Secret deterministically.
    component_key: str | None = None
    step_name: str | None = None


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
    def build_run_argv(self, spec: StepSpec, authfile_dir: str | None = None) -> list[str]:
        """Build the full ``docker run`` argv for a step.

        Pure function of the spec (+ optional transient authfile dir). Kept
        separate from execution so it can be asserted in unit tests with no
        daemon and no subprocess.
        """
        self._validate_spec(spec)

        argv: list[str] = [self.docker_bin]

        # --config points docker at a private dir holding the pull authfile.
        if authfile_dir:
            argv += ["--config", authfile_dir]

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
        import threading
        import time

        authfile_dir = self._write_pull_authfile(spec.pull_authfile_json)
        argv = self.build_run_argv(spec, authfile_dir=authfile_dir)

        # Pull, then vet the image BEFORE anything of it executes. A root image
        # is refused outright (the KubernetesRunner's runAsNonRoot equivalent):
        # with a bind/volume-mounted workspace, a root container is a host-root
        # write primitive.
        gate = self._gate_image(spec, authfile_dir, on_output)
        if gate is not None:
            self._cleanup_authfile(authfile_dir)
            return gate

        run_env = dict(os.environ)
        run_env["DOCKER_HOST"] = self.docker_host

        if on_output:
            on_output(f"$ docker run {spec.image_digest} {' '.join(spec.args)}")

        started = time.monotonic()
        # Stream stdout+stderr (merged for ordering) line-by-line to on_output so
        # the task log updates live and a crash/kill still leaves the output so far.
        # A watchdog timer enforces the step timeout even when the container is
        # silent — a plain readline loop would block past the deadline waiting for
        # the next line.
        out_chunks: list[str] = []
        timed_out = threading.Event()
        proc: subprocess.Popen[str] | None = None
        timer: threading.Timer | None = None
        try:
            proc = subprocess.Popen(
                argv,
                env=run_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if spec.timeout_seconds:
                def _on_timeout(p: subprocess.Popen[str] = proc) -> None:
                    timed_out.set()
                    p.kill()
                timer = threading.Timer(spec.timeout_seconds, _on_timeout)
                timer.start()
            assert proc.stdout is not None
            for line in proc.stdout:
                out_chunks.append(line)
                if on_output:
                    on_output(line.rstrip("\n"))
            proc.wait()
        finally:
            if timer is not None:
                timer.cancel()
            self._cleanup_authfile(authfile_dir)

        duration = time.monotonic() - started
        stdout = "".join(out_chunks)

        if timed_out.is_set():
            logger.warning("Container step timed out after %ss: %s", spec.timeout_seconds, spec.image_digest)
            return StepResult(
                success=False,
                exit_code=124,
                stdout=stdout,
                stderr="",
                timed_out=True,
                duration_seconds=duration,
            )

        exit_code = proc.returncode if proc is not None else 1
        return StepResult(
            success=exit_code == 0,
            exit_code=exit_code,
            stdout=stdout,
            stderr="",  # merged into stdout for live ordering
            timed_out=False,
            duration_seconds=duration,
        )

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
