"""Kubernetes container step runner — one ``V1Job`` per artifact step.

This is the Kubernetes substrate twin of :class:`DockerRunner`. It implements
the same :class:`ContainerRunner` contract (``run_step(spec) -> StepResult``)
but, instead of running a sibling container on a docker daemon, it dispatches
**one Kubernetes Job per step** into a locked-down *runner namespace* on a
runner cluster selected by a ``RunnerKubeConfig``.

Security / supply-chain rules enforced here (mirroring DockerRunner):
  - The image is always pinned by digest (``repo@sha256:...``). A floating tag
    is rejected before it reaches the Job spec (immutability).
  - The step argv runs in the image directly via ``container.args`` — there is
    no shell, no ``command`` override unless the step explicitly asks for one.
  - ALL credentials (step ``env``) are written to a **short-lived namespaced
    ``V1Secret``** whose ``ownerReferences`` bind it to the Job, so it is
    garbage-collected the instant the Job is deleted. Secret *values* are never
    placed in the Job/Pod spec inline and never logged.
  - Pull credentials become a transient ``kubernetes.io/dockerconfigjson``
    Secret referenced as the Pod ``imagePullSecrets`` — also owner-bound to the
    Job and GC'd with it.
  - A **deny-by-default ``NetworkPolicy``** is applied to the runner namespace
    so step pods cannot reach arbitrary in-cluster endpoints. Egress to the
    cloud control plane goes out the cluster's normal egress.
  - The workspace is a **per-component ``PersistentVolumeClaim``** with a
    ``Retain``-style reclaim (the PVC itself persists across steps; only the
    Job/Secrets are ephemeral).
  - Resource limits and ``activeDeadlineSeconds`` bound the blast radius of a
    misbehaving artifact image.

All methods are synchronous — Celery workers are sync. The pure spec-builders
(``build_*``) take no client and are unit-tested without a live cluster.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass

from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

from services.execution.container_runner import (
    _DIGEST_REF_RE,
    ContainerRunner,
    ResourceLimits,
    StepResult,
    StepSpec,
    _validate_env_keys,
)
from utils.security import validate_cli_arg

logger = logging.getLogger(__name__)

# Labels every resource this runner creates carries, so an operator (or our own
# cleanup) can find and reap everything we own with a single label selector.
MANAGED_BY = "bnk-forge"
RUNNER_COMPONENT = "container-runner"

# Default runner namespace if the RunnerKubeConfig does not pin one.
DEFAULT_RUNNER_NAMESPACE = "bnk-forge-runner"

# NetworkPolicy name — one deny-by-default policy per runner namespace.
DENY_ALL_NETPOL_NAME = "bnk-forge-runner-deny-by-default"

# Namespace the cluster DNS resolver runs in. The DNS egress rule is scoped to
# it by namespaceSelector rather than left unscoped — an egress rule with ports
# and no peers permits ALL destinations on those ports. Overridable for clusters
# that run CoreDNS elsewhere; `kubernetes.io/metadata.name` is set automatically
# on every namespace since k8s 1.21.
DNS_NAMESPACE = os.environ.get("CONTAINER_RUNNER_DNS_NAMESPACE", "kube-system")

# A K8s name must be a DNS-1123 label: lowercase alnum + '-', <= 63 chars.
_MAX_NAME_LEN = 63


@dataclass
class RunnerKubeConfig:
    """Selects which cluster/namespace the per-step Jobs land in.

    The task layer resolves this (from the established kubeconfig path) and
    hands it to the runner. The runner itself does no DB access.
    """

    kubeconfig_path: str | None = None     # path to a kubeconfig file
    kubeconfig_content: str | None = None  # raw kubeconfig YAML (written to a temp file)
    context: str | None = None             # named context to select
    namespace: str = DEFAULT_RUNNER_NAMESPACE


def _sanitize_name(value: str) -> str:
    """Coerce an arbitrary identifier into a DNS-1123 label fragment."""
    out = []
    for ch in value.lower():
        if ch.isalnum() or ch == "-":
            out.append(ch)
        else:
            out.append("-")
    cleaned = "".join(out).strip("-") or "x"
    return cleaned[:40]


def _pvc_name(component_key: str) -> str:
    return f"bnk-ws-{_sanitize_name(component_key)}"[:_MAX_NAME_LEN]


def _job_name(component_key: str, step_name: str | None) -> str:
    base = _sanitize_name(component_key)
    suffix = _sanitize_name(step_name) if step_name else "step"
    stamp = str(int(time.time()))
    name = f"bnk-{base}-{suffix}-{stamp}"
    return name[:_MAX_NAME_LEN]


class KubernetesRunner(ContainerRunner):
    """Runs each artifact step as one ``V1Job`` in a locked runner namespace.

    The runner is deliberately stateless except for the resolved kube config.
    Every ``run_step`` call:
      1. ensures the per-component workspace PVC exists (Retain),
      2. creates the step Job (image digest-pinned, resource-limited, deadlined),
      3. creates owner-bound credential + pull Secrets GC'd with the Job,
      4. ensures the deny-by-default NetworkPolicy,
      5. waits for the Job to complete and returns the pod logs.
    """

    def __init__(self, runner_config: RunnerKubeConfig):
        self.runner_config = runner_config
        self.namespace = runner_config.namespace or DEFAULT_RUNNER_NAMESPACE
        self._api_client: k8s_client.ApiClient | None = None

    # -------------------------------------------------------------------------
    # api client (lazy — built via the established load_kube_config path)
    # -------------------------------------------------------------------------
    def _get_api_client(self) -> k8s_client.ApiClient:
        if self._api_client is not None:
            return self._api_client

        kubeconfig_path = self.runner_config.kubeconfig_path
        tmp_path: str | None = None
        try:
            if not kubeconfig_path:
                if not self.runner_config.kubeconfig_content:
                    raise ValueError(
                        "RunnerKubeConfig needs kubeconfig_path or kubeconfig_content"
                    )
                fd, tmp_path = tempfile.mkstemp(prefix="bnk_runner_kubeconfig_", suffix=".yaml")
                with os.fdopen(fd, "w") as handle:
                    handle.write(self.runner_config.kubeconfig_content)
                os.chmod(tmp_path, 0o600)
                kubeconfig_path = tmp_path

            k8s_config.load_kube_config(
                config_file=kubeconfig_path, context=self.runner_config.context
            )
            cfg = k8s_client.Configuration.get_default_copy()
            cfg.retries = 0
            self._api_client = k8s_client.ApiClient(cfg)
            return self._api_client
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    # -------------------------------------------------------------------------
    # pure spec-builders (no client — unit-tested without a cluster)
    # -------------------------------------------------------------------------
    def build_workspace_pvc(self, spec: StepSpec) -> k8s_client.V1PersistentVolumeClaim:
        """Per-component PVC for the persistent workspace.

        One PVC per component (named from ``component_key``), shared across all
        of that component's steps. It is intentionally NOT owner-bound to any
        Job — the workspace must survive every ephemeral step Job. (PV reclaim
        policy ``Retain`` is a property of the bound PV / StorageClass; we keep
        the PVC itself long-lived here.)
        """
        component_key = self._require_component_key(spec)
        return k8s_client.V1PersistentVolumeClaim(
            metadata=k8s_client.V1ObjectMeta(
                name=_pvc_name(component_key),
                namespace=self.namespace,
                labels=self._labels(component_key),
            ),
            spec=k8s_client.V1PersistentVolumeClaimSpec(
                access_modes=["ReadWriteOnce"],
                resources=k8s_client.V1ResourceRequirements(
                    requests={"storage": "5Gi"},
                ),
            ),
        )

    def build_credentials_secret(
        self, spec: StepSpec, owner: k8s_client.V1OwnerReference | None
    ) -> k8s_client.V1Secret | None:
        """Short-lived Opaque Secret carrying ALL step credentials (``spec.env``).

        Owner-bound to the Job so it is GC'd the instant the Job is deleted.
        Returns ``None`` when the step has no credential env (nothing to mount).
        """
        if not spec.env:
            return None
        _validate_env_keys(spec.env)
        component_key = self._require_component_key(spec)
        meta = k8s_client.V1ObjectMeta(
            name=self._credentials_secret_name(spec),
            namespace=self.namespace,
            labels=self._labels(component_key),
        )
        if owner is not None:
            meta.owner_references = [owner]
        return k8s_client.V1Secret(
            metadata=meta,
            string_data=dict(spec.env),
            type="Opaque",
        )

    def build_pull_secret(
        self, spec: StepSpec, owner: k8s_client.V1OwnerReference | None
    ) -> k8s_client.V1Secret | None:
        """Transient ``dockerconfigjson`` imagePullSecret, owner-bound to the Job.

        Returns ``None`` when the step has no pull credentials (public image).
        """
        if not spec.pull_authfile_json:
            return None
        component_key = self._require_component_key(spec)
        meta = k8s_client.V1ObjectMeta(
            name=self._pull_secret_name(spec),
            namespace=self.namespace,
            labels=self._labels(component_key),
        )
        if owner is not None:
            meta.owner_references = [owner]
        # pull_authfile_json is a base64-encoded dockerconfigjson (the cne_pull_secret
        # format). Put it in `data` (which holds base64 values directly); using
        # `string_data` would make Kubernetes base64-encode it a second time,
        # yielding an invalid imagePullSecret.
        return k8s_client.V1Secret(
            metadata=meta,
            data={".dockerconfigjson": spec.pull_authfile_json},
            type="kubernetes.io/dockerconfigjson",
        )

    def build_network_policy(self) -> k8s_client.V1NetworkPolicy:
        """Deny all ingress; allow only DNS + non-cluster-internal egress.

        The previous version declared both policy types with ``egress=[]``,
        which denies ALL egress including DNS. That contradicted the documented
        posture — an artifact provisions cloud resources over the network — and
        the two outcomes were both bad: on an enforcing CNI (Calico/Cilium) a
        provisioning artifact could not resolve DNS or reach a cloud API at all,
        and on a non-enforcing CNI the advertised isolation was fictional
        either way. The E2E ran against the Docker backend, so this path was
        under-exercised (issue #79, item 5).

        What this expresses instead:
          * ingress stays fully denied — nothing needs to reach a step pod;
          * egress to DNS (udp/tcp 53) is allowed, or nothing resolves;
          * egress to public address space is allowed, so cloud control planes
            are reachable;
          * egress to RFC1918 / loopback / link-local is DENIED, which is the
            isolation that actually matters here: it keeps a third-party
            artifact image away from cluster-internal services, the kubelet,
            and the cloud metadata endpoint (169.254.169.254).
        """
        private_cidrs = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                         "127.0.0.0/8", "169.254.0.0/16"]
        policy = k8s_client.V1NetworkPolicy(
            metadata=k8s_client.V1ObjectMeta(
                name=DENY_ALL_NETPOL_NAME,
                namespace=self.namespace,
                labels=self._labels(None),
            ),
            spec=k8s_client.V1NetworkPolicySpec(
                pod_selector=k8s_client.V1LabelSelector(),  # {} → all pods
                policy_types=["Ingress", "Egress"],
                ingress=[],
                egress=[
                    # DNS — scoped to the cluster resolver's namespace.
                    #
                    # This rule previously carried `ports` and NO `to`, which in
                    # NetworkPolicy means ALL destinations on those ports: the
                    # `except` list below binds only to its own ipBlock, so
                    # 169.254.169.254:53 and every RFC1918 host on 53 stayed
                    # reachable — TCP included, which is a clean bidirectional
                    # exfil channel out of a pod holding cloud credentials.
                    k8s_client.V1NetworkPolicyEgressRule(
                        to=[
                            k8s_client.V1NetworkPolicyPeer(
                                namespace_selector=k8s_client.V1LabelSelector(
                                    match_labels={
                                        "kubernetes.io/metadata.name": DNS_NAMESPACE
                                    }
                                )
                            )
                        ],
                        ports=[
                            k8s_client.V1NetworkPolicyPort(protocol="UDP", port=53),
                            k8s_client.V1NetworkPolicyPort(protocol="TCP", port=53),
                        ],
                    ),
                    # Everything else: public destinations only.
                    k8s_client.V1NetworkPolicyEgressRule(
                        to=[
                            k8s_client.V1NetworkPolicyPeer(
                                ip_block=k8s_client.V1IPBlock(
                                    cidr="0.0.0.0/0",
                                    _except=private_cidrs,
                                )
                            )
                        ],
                    ),
                ],
            ),
        )
        self._assert_every_egress_rule_is_scoped(policy)
        return policy

    @staticmethod
    def _assert_every_egress_rule_is_scoped(policy: k8s_client.V1NetworkPolicy) -> None:
        """Refuse to build an egress rule with no ``to``.

        A rule with ports but no peers means *all destinations* on those ports.
        That is what made the DNS rule above defeat its own ipBlock `except`
        list, and it is invisible on reading unless you know the semantics — so
        it is enforced here rather than left to review.
        """
        for index, rule in enumerate(policy.spec.egress or []):
            if not getattr(rule, "to", None):
                raise ValueError(
                    f"egress rule[{index}] has no 'to': a NetworkPolicy egress "
                    "rule without peers permits ALL destinations on its ports"
                )

    def build_job(self, spec: StepSpec) -> k8s_client.V1Job:
        """The per-step Job: the artifact's OWN digest-pinned image + argv.

        Credentials are referenced (not inlined) via ``env_from`` on the
        credentials Secret; the pull Secret is referenced via
        ``image_pull_secrets``. Both Secrets are created AFTER the Job (so they
        can carry its UID as an ownerReference) — the kubelet retries the secret
        lookup, so a brief window where they are absent is tolerated.
        """
        self._validate_spec(spec)
        component_key = self._require_component_key(spec)

        env = [
            k8s_client.V1EnvVar(name=key, value=spec.home_env[key])
            for key in sorted(spec.home_env)
        ]

        env_from = []
        if spec.env:
            env_from.append(
                k8s_client.V1EnvFromSource(
                    secret_ref=k8s_client.V1SecretEnvSource(
                        name=self._credentials_secret_name(spec)
                    )
                )
            )

        image_pull_secrets = None
        if spec.pull_authfile_json:
            image_pull_secrets = [
                k8s_client.V1LocalObjectReference(name=self._pull_secret_name(spec))
            ]

        container = k8s_client.V1Container(
            name="step",
            image=spec.image_digest,
            # args are the FULL argv (args[0] = the image's own binary). Set
            # `command` so the pod runs exactly this argv, overriding the image
            # ENTRYPOINT — otherwise K8s appends args to it (an image whose
            # ENTRYPOINT is already `roksbnkctl` would run it twice). A spec.command
            # override (rare) prefixes the argv.
            command=([spec.command, *spec.args] if spec.command else list(spec.args)),
            args=None,
            working_dir=spec.mount_path,
            env=env or None,
            env_from=env_from or None,
            resources=self._resource_requirements(spec.limits),
            volume_mounts=[
                k8s_client.V1VolumeMount(name="workspace", mount_path=spec.mount_path)
            ],
            security_context=k8s_client.V1SecurityContext(
                allow_privilege_escalation=False,
                run_as_non_root=True,
                read_only_root_filesystem=False,
                capabilities=k8s_client.V1Capabilities(drop=["ALL"]),
            ),
        )

        pod_spec = k8s_client.V1PodSpec(
            restart_policy="Never",
            automount_service_account_token=False,
            image_pull_secrets=image_pull_secrets,
            containers=[container],
            volumes=[
                k8s_client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=k8s_client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=_pvc_name(component_key),
                    ),
                )
            ],
        )

        return k8s_client.V1Job(
            metadata=k8s_client.V1ObjectMeta(
                name=_job_name(component_key, spec.step_name),
                namespace=self.namespace,
                labels=self._labels(component_key),
            ),
            spec=k8s_client.V1JobSpec(
                backoff_limit=0,
                active_deadline_seconds=spec.timeout_seconds,
                ttl_seconds_after_finished=300,
                template=k8s_client.V1PodTemplateSpec(
                    metadata=k8s_client.V1ObjectMeta(labels=self._labels(component_key)),
                    spec=pod_spec,
                ),
            ),
        )

    @staticmethod
    def owner_reference_for_job(job: k8s_client.V1Job) -> k8s_client.V1OwnerReference:
        """Build an ownerReference pointing at a created Job (for GC binding)."""
        return k8s_client.V1OwnerReference(
            api_version="batch/v1",
            kind="Job",
            name=job.metadata.name,
            uid=job.metadata.uid,
            controller=True,
            block_owner_deletion=True,
        )

    # -------------------------------------------------------------------------
    # execution
    # -------------------------------------------------------------------------
    def run_step(
        self,
        spec: StepSpec,
        on_output: Callable[[str], None] | None = None,
    ) -> StepResult:
        self._validate_spec(spec)
        api_client = self._get_api_client()
        batch_v1 = k8s_client.BatchV1Api(api_client)
        core_v1 = k8s_client.CoreV1Api(api_client)

        self._ensure_network_policy(api_client)
        self._ensure_workspace_pvc(core_v1, spec)

        job_body = self.build_job(spec)
        started = time.monotonic()
        if on_output:
            on_output(f"$ kubectl create job {job_body.metadata.name} "
                      f"({spec.image_digest} {' '.join(spec.args)})")

        created_job = batch_v1.create_namespaced_job(self.namespace, job_body)
        owner = self.owner_reference_for_job(created_job)

        # Secrets are owner-bound to the Job → GC'd with it.
        cred_secret = self.build_credentials_secret(spec, owner)
        if cred_secret is not None:
            core_v1.create_namespaced_secret(self.namespace, cred_secret)
        pull_secret = self.build_pull_secret(spec, owner)
        if pull_secret is not None:
            core_v1.create_namespaced_secret(self.namespace, pull_secret)

        result = self._wait_for_job(
            batch_v1, core_v1, created_job.metadata.name, spec.timeout_seconds, started
        )
        if on_output and result.stdout:
            on_output(result.stdout)
        return result

    def health_check(self) -> bool:
        """Return True when the runner can reach its cluster's API server."""
        try:
            api_client = self._get_api_client()
            k8s_client.CoreV1Api(api_client).read_namespace(self.namespace, _request_timeout=10)
            return True
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # cluster-touching helpers
    # -------------------------------------------------------------------------
    def _ensure_network_policy(self, api_client: k8s_client.ApiClient) -> None:
        netpol = self.build_network_policy()
        networking = k8s_client.NetworkingV1Api(api_client)
        try:
            networking.create_namespaced_network_policy(self.namespace, netpol)
            logger.info("Applied runner NetworkPolicy in %s", self.namespace)
            return
        except ApiException as exc:
            if exc.status != 409:
                # Fail CLOSED. This is the isolation boundary for a third-party
                # artifact image that receives cloud credentials via env_from —
                # running the step without it is precisely the outcome the
                # policy exists to prevent, so a failure here must stop the run
                # rather than log and continue.
                raise RuntimeError(
                    f"Could not apply the runner NetworkPolicy in namespace "
                    f"{self.namespace}: {exc.reason}. Refusing to run an "
                    "artifact step without network isolation."
                ) from exc

        # 409 = a policy of this name already exists. It may predate a hardening
        # change (an older build shipped a rule that denied DNS, and before that
        # one that permitted all destinations on port 53), so leaving it alone
        # silently pins whatever the namespace happened to have. Reconcile it.
        try:
            networking.replace_namespaced_network_policy(
                DENY_ALL_NETPOL_NAME, self.namespace, netpol
            )
            logger.info("Reconciled existing runner NetworkPolicy in %s", self.namespace)
        except ApiException as exc:
            raise RuntimeError(
                f"Could not reconcile the existing runner NetworkPolicy in "
                f"namespace {self.namespace}: {exc.reason}. Refusing to run an "
                "artifact step against an unverified policy."
            ) from exc

    def _ensure_workspace_pvc(self, core_v1: k8s_client.CoreV1Api, spec: StepSpec) -> None:
        pvc = self.build_workspace_pvc(spec)
        try:
            core_v1.create_namespaced_persistent_volume_claim(self.namespace, pvc)
            logger.info("Created workspace PVC %s in %s", pvc.metadata.name, self.namespace)
        except ApiException as exc:
            if exc.status != 409:  # already exists — reuse it
                raise

    def _wait_for_job(
        self,
        batch_v1: k8s_client.BatchV1Api,
        core_v1: k8s_client.CoreV1Api,
        job_name: str,
        timeout_seconds: int,
        started: float,
    ) -> StepResult:
        deadline = time.monotonic() + timeout_seconds + 30  # grace over the Job deadline
        while time.monotonic() < deadline:
            job = batch_v1.read_namespaced_job_status(job_name, self.namespace)
            status = job.status
            if status and status.succeeded:
                logs = self._collect_logs(core_v1, job_name)
                return StepResult(
                    success=True,
                    exit_code=0,
                    stdout=logs,
                    duration_seconds=time.monotonic() - started,
                )
            if status and status.failed:
                logs = self._collect_logs(core_v1, job_name)
                timed_out = self._is_deadline_exceeded(status)
                return StepResult(
                    success=False,
                    exit_code=124 if timed_out else 1,
                    stdout=logs,
                    stderr="Job failed (deadline exceeded)" if timed_out else "Job failed",
                    timed_out=timed_out,
                    duration_seconds=time.monotonic() - started,
                )
            time.sleep(3)

        logs = self._collect_logs(core_v1, job_name)
        return StepResult(
            success=False,
            exit_code=124,
            stdout=logs,
            stderr="Timed out waiting for Job to complete",
            timed_out=True,
            duration_seconds=time.monotonic() - started,
        )

    @staticmethod
    def _is_deadline_exceeded(status: object) -> bool:
        conditions = getattr(status, "conditions", None) or []
        for cond in conditions:
            if getattr(cond, "type", None) == "Failed" and getattr(cond, "reason", None) == "DeadlineExceeded":
                return True
        return False

    def _collect_logs(self, core_v1: k8s_client.CoreV1Api, job_name: str) -> str:
        """Read the step pod's logs (the artifact's own stdout/stderr).

        Secrets are mounted only as env vars referenced from the credentials
        Secret — they are never echoed here unless the artifact image itself
        prints them, which is the artifact's responsibility, not the runner's.
        """
        try:
            pods = core_v1.list_namespaced_pod(
                self.namespace, label_selector=f"job-name={job_name}"
            )
        except ApiException:
            return ""
        chunks: list[str] = []
        for pod in pods.items:
            try:
                log = core_v1.read_namespaced_pod_log(pod.metadata.name, self.namespace)
                if log:
                    chunks.append(log)
            except ApiException:
                continue
        return "\n".join(chunks)

    # -------------------------------------------------------------------------
    # internal helpers
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
        if not spec.mount_path or not spec.mount_path.startswith("/"):
            raise ValueError("mount_path must be an absolute path inside the container")
        _validate_env_keys(spec.env)
        _validate_env_keys(spec.home_env)
        if spec.command:
            validate_cli_arg("command", spec.command)

    @staticmethod
    def _require_component_key(spec: StepSpec) -> str:
        if not spec.component_key:
            raise ValueError("component_key is required for the KubernetesRunner")
        return spec.component_key

    def _credentials_secret_name(self, spec: StepSpec) -> str:
        component_key = self._require_component_key(spec)
        suffix = _sanitize_name(spec.step_name) if spec.step_name else "step"
        return f"bnk-creds-{_sanitize_name(component_key)}-{suffix}"[:_MAX_NAME_LEN]

    def _pull_secret_name(self, spec: StepSpec) -> str:
        component_key = self._require_component_key(spec)
        suffix = _sanitize_name(spec.step_name) if spec.step_name else "step"
        return f"bnk-pull-{_sanitize_name(component_key)}-{suffix}"[:_MAX_NAME_LEN]

    @staticmethod
    def _resource_requirements(limits: ResourceLimits) -> k8s_client.V1ResourceRequirements:
        req: dict[str, str] = {}
        lim: dict[str, str] = {}
        if limits.cpus is not None:
            validate_cli_arg("cpus", str(limits.cpus))
            lim["cpu"] = str(limits.cpus)
        if limits.memory is not None:
            validate_cli_arg("memory", str(limits.memory))
            lim["memory"] = str(limits.memory)
        return k8s_client.V1ResourceRequirements(
            requests=req or None,
            limits=lim or None,
        )

    @staticmethod
    def _labels(component_key: str | None) -> dict[str, str]:
        labels = {
            "app.kubernetes.io/managed-by": MANAGED_BY,
            "app.kubernetes.io/component": RUNNER_COMPONENT,
        }
        if component_key:
            labels["bnk-forge/component-key"] = _sanitize_name(component_key)
        return labels
