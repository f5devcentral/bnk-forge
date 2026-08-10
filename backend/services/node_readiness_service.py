"""Probe BNK node readiness — CNI delegate plugins + core_pattern (issue #387 part A).

Detection-only (remediation is a later phase). Two tiers:

  * Cheap scan fields (``is_kind``/``is_local``, per-node hugepages) come from
    the Node API and are computed for free during every cluster scan —
    see ``services/scanner/nodes.py`` and ``services/scanner/prereqs.py``.
  * This service is the expensive tier: a one-shot, on-demand, privileged
    Kubernetes ``Job`` (one pod per node, ALL nodes — no TMM label filter)
    that inspects host state a normal API call cannot see:
      - presence of the ``macvlan``/``host-device``/``ipvlan`` CNI delegate
        binaries under ``/opt/cni/bin`` on each node;
      - the node's ``/proc/sys/kernel/core_pattern`` (a bare ``core`` is
        incompatible with F5's crashagent).

Unlike ``hugepages_deploy_service`` (fire-and-forget), this service WAITS for
the Job to complete and reads each pod's logs to build a per-node result —
there is nothing to observe on the API surface otherwise.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from core.errors import InternalError
from services.kubernetes._base import KubernetesServiceBase
from services.scanner.nodes import _quantity_is_positive, is_kind_cluster, is_local_cluster

logger = logging.getLogger(__name__)


_DEFAULT_NAMESPACE = "kube-system"
_DEFAULT_IMAGE = "busybox:1.36.1"
_JOB_TTL_SEC = 120
_JOB_BACKOFF_LIMIT = 2
_JOB_ACTIVE_DEADLINE_SEC = 120
_POLL_INTERVAL_SEC = 2.0
_POLL_TIMEOUT_SEC = 130.0

_REQUIRED_CNI_PLUGINS = ("macvlan", "host-device", "ipvlan")


def _resolve_image(image: str | None) -> str:
    """Pick the container image for the probe pod.

    Precedence: explicit request override -> ``BNK_FORGE_NODEPROBE_IMAGE``
    env var -> ``busybox:1.36.1``. Airgapped environments must mirror the
    fallback image or set the env override to a mirrored path.
    """
    if image:
        return image
    return os.environ.get("BNK_FORGE_NODEPROBE_IMAGE") or _DEFAULT_IMAGE


def _probe_script() -> str:
    """Shell snippet run inside each probe pod.

    Prints a small, robust key=value contract to stdout so a crashed/partial
    pod still yields something parseable rather than an opaque failure.
    """
    plugin_list = " ".join(_REQUIRED_CNI_PLUGINS)
    return (
        f"set -eu; "
        f'echo "NODE=$NODE_NAME"; '
        f'cni=""; '
        f"for p in {plugin_list}; do "
        f'  if [ -e "/host-cni-bin/$p" ]; then cni="$cni $p"; fi; '
        f"done; "
        f'echo "CNI_PLUGINS=$cni"; '
        f'core_pattern=$(cat /host-proc/sys/kernel/core_pattern 2>/dev/null || echo unknown); '
        f'echo "CORE_PATTERN=$core_pattern"'
    )


def build_probe_job_manifest(
    *,
    job_name: str,
    namespace: str,
    node_count: int,
    image: str,
) -> dict[str, Any]:
    """Render the node-readiness probe Job spec as a plain dict.

    Targets ALL nodes (no TMM label filter) via ``parallelism == completions
    == node_count`` + pod-anti-affinity on ``kubernetes.io/hostname``, and
    tolerates every taint so it lands on control-plane/tainted nodes too.
    """
    script = _probe_script()

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "bnk-forge-node-readiness-probe",
                "app.kubernetes.io/managed-by": "bnk-forge",
            },
        },
        "spec": {
            "completions": node_count,
            "parallelism": node_count,
            "backoffLimit": _JOB_BACKOFF_LIMIT,
            "activeDeadlineSeconds": _JOB_ACTIVE_DEADLINE_SEC,
            "ttlSecondsAfterFinished": _JOB_TTL_SEC,
            "template": {
                "metadata": {
                    "labels": {
                        "app.kubernetes.io/name": "bnk-forge-node-readiness-probe",
                        "job-name": job_name,
                    },
                },
                "spec": {
                    "restartPolicy": "OnFailure",
                    "hostPID": False,
                    "affinity": {
                        # One pod per node, spread across ALL nodes -- combined
                        # with parallelism=node_count this covers the fleet.
                        "podAntiAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": [
                                {
                                    "labelSelector": {
                                        "matchLabels": {
                                            "app.kubernetes.io/name": "bnk-forge-node-readiness-probe",
                                            "job-name": job_name,
                                        }
                                    },
                                    "topologyKey": "kubernetes.io/hostname",
                                }
                            ]
                        },
                    },
                    "tolerations": [
                        # Must land on every node, including tainted
                        # control-plane nodes (kind's single-node clusters).
                        {"operator": "Exists"},
                    ],
                    "containers": [
                        {
                            "name": "probe",
                            "image": image,
                            "command": ["/bin/sh", "-c", script],
                            "env": [
                                {
                                    "name": "NODE_NAME",
                                    "valueFrom": {
                                        "fieldRef": {"fieldPath": "spec.nodeName"}
                                    },
                                },
                            ],
                            "securityContext": {"privileged": True},
                            "volumeMounts": [
                                {
                                    "name": "host-cni-bin",
                                    "mountPath": "/host-cni-bin",
                                    "readOnly": True,
                                },
                                {
                                    "name": "host-proc",
                                    "mountPath": "/host-proc",
                                    "readOnly": True,
                                },
                            ],
                            "resources": {
                                "requests": {"cpu": "10m", "memory": "16Mi"},
                                "limits": {"cpu": "100m", "memory": "64Mi"},
                            },
                        }
                    ],
                    "volumes": [
                        {"name": "host-cni-bin", "hostPath": {"path": "/opt/cni/bin"}},
                        {"name": "host-proc", "hostPath": {"path": "/proc"}},
                    ],
                },
            },
        },
    }


def parse_probe_log(log_text: str) -> dict[str, Any]:
    """Parse one probe pod's stdout into {node, cni_plugins (raw list), core_pattern}."""
    node_name: str | None = None
    cni_plugins: list[str] = []
    core_pattern: str | None = None

    for line in (log_text or "").splitlines():
        line = line.strip()
        if line.startswith("NODE="):
            node_name = line[len("NODE=") :].strip() or None
        elif line.startswith("CNI_PLUGINS="):
            raw = line[len("CNI_PLUGINS=") :].strip()
            cni_plugins = raw.split() if raw else []
        elif line.startswith("CORE_PATTERN="):
            core_pattern = line[len("CORE_PATTERN=") :].strip()

    return {"node": node_name, "cni_plugins": cni_plugins, "core_pattern": core_pattern}


def build_node_result(
    node_name: str,
    log_text: str | None,
    hugepages_2mi: str | None,
) -> dict[str, Any]:
    """Build the typed per-node readiness record from a probe pod's log.

    ``log_text`` of None means the pod never produced output (failed/timed
    out) -- reported as ``unknown`` rather than raising.
    """
    if log_text is None:
        return {
            "node": node_name,
            "cni_plugins": {"macvlan": False, "host_device": False, "ipvlan": False},
            "cni_ok": False,
            "core_pattern": "unknown",
            "core_pattern_ok": False,
            "hugepages_2mi": hugepages_2mi,
            "hugepages_ok": _quantity_is_positive(hugepages_2mi),
        }

    parsed = parse_probe_log(log_text)
    found = set(parsed["cni_plugins"])
    cni_plugins = {
        "macvlan": "macvlan" in found,
        "host_device": "host-device" in found,
        "ipvlan": "ipvlan" in found,
    }
    core_pattern = parsed["core_pattern"]
    # Bare "core" is incompatible with F5's crashagent; anything with a
    # path/pipe (e.g. "/tmp/core.%e.%p", "|/usr/bin/handler %P") is fine.
    core_pattern_ok = core_pattern is not None and core_pattern != "core" and core_pattern != "unknown"

    return {
        "node": node_name,
        "cni_plugins": cni_plugins,
        "cni_ok": all(cni_plugins.values()),
        "core_pattern": core_pattern,
        "core_pattern_ok": core_pattern_ok,
        "hugepages_2mi": hugepages_2mi,
        "hugepages_ok": _quantity_is_positive(hugepages_2mi),
    }


class NodeReadinessService(KubernetesServiceBase):
    """Dispatch the node-readiness probe Job and collect per-node results."""

    def probe(
        self,
        cluster_id: int,
        namespace: str = _DEFAULT_NAMESPACE,
        image: str | None = None,
    ) -> dict[str, Any]:
        """Run the privileged node-readiness probe against every node. Returns a summary dict."""
        resolved_image = _resolve_image(image)

        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)
        core_v1 = client.CoreV1Api(api_client)

        node_list = core_v1.list_node(_request_timeout=(3, 15))
        nodes = [
            {
                "name": n.metadata.name,
                "provider_id": getattr(n.spec, "provider_id", None) if n.spec else None,
                "hugepages_2mi": (n.status.capacity or {}).get("hugepages-2Mi") if n.status else None,
            }
            for n in node_list.items
        ]
        node_count = len(nodes)
        if node_count == 0:
            raise InternalError("Cluster reported zero nodes; cannot probe node readiness.")

        cluster_is_kind = is_kind_cluster(nodes)
        cluster_is_local = is_local_cluster(nodes)
        hugepages_by_node = {n["name"]: n["hugepages_2mi"] for n in nodes}

        job_name = f"bnk-node-readiness-{int(time.time())}"
        manifest = build_probe_job_manifest(
            job_name=job_name,
            namespace=namespace,
            node_count=node_count,
            image=resolved_image,
        )

        batch_v1 = client.BatchV1Api(api_client)
        try:
            batch_v1.create_namespaced_job(namespace=namespace, body=manifest)
        except ApiException as e:
            logger.error("Failed to create node-readiness probe Job: %s", e)
            raise InternalError(f"Kubernetes API error creating Job: {e.reason}") from e

        logger.info(
            "Dispatched node-readiness probe Job '%s' in namespace '%s' for %d node(s) (image=%s)",
            job_name, namespace, node_count, resolved_image,
        )

        try:
            self._wait_for_job(batch_v1, job_name, namespace)
            pod_logs_by_node = self._collect_pod_logs(core_v1, job_name, namespace)
        finally:
            # Always clean up the privileged Job, even if wait/collect raised
            # something other than ApiException (e.g. a urllib3
            # ProtocolError) — otherwise it leaks, bounded only by ttl.
            self._cleanup_job(batch_v1, job_name, namespace)

        node_results = [
            build_node_result(name, pod_logs_by_node.get(name), hugepages_by_node.get(name))
            for name in sorted(hugepages_by_node)
        ]
        all_ready = all(
            r["cni_ok"] and r["core_pattern_ok"] and r["hugepages_ok"] for r in node_results
        )

        return {
            "cluster_id": cluster_id,
            "job_name": job_name,
            "is_kind": cluster_is_kind,
            "is_local": cluster_is_local,
            "nodes": node_results,
            "all_ready": all_ready,
            "message": (
                f"Node readiness probed across {node_count} node(s). "
                f"{'All ready.' if all_ready else 'Some nodes are not ready — see per-node detail.'}"
            ),
        }

    @staticmethod
    def _wait_for_job(batch_v1: client.BatchV1Api, job_name: str, namespace: str) -> None:
        """Poll the Job until it completes (succeeded or failed) or the bounded timeout elapses.

        Never raises on failure/timeout -- callers handle a partial/failed Job
        by reporting per-node "unknown" for pods with no log output.
        """
        deadline = time.monotonic() + _POLL_TIMEOUT_SEC
        while time.monotonic() < deadline:
            try:
                job = batch_v1.read_namespaced_job_status(job_name, namespace)
            except ApiException as e:
                logger.warning("Failed to poll node-readiness probe Job '%s': %s", job_name, e)
                return
            status = job.status
            if status and ((status.succeeded or 0) + (status.failed or 0)) >= (job.spec.completions or 0):
                return
            time.sleep(_POLL_INTERVAL_SEC)
        logger.warning("Node-readiness probe Job '%s' did not complete within %ss", job_name, _POLL_TIMEOUT_SEC)

    @staticmethod
    def _collect_pod_logs(
        core_v1: client.CoreV1Api, job_name: str, namespace: str
    ) -> dict[str, str]:
        """Read each probe pod's stdout, keyed by the node name parsed out of it."""
        logs_by_node: dict[str, str] = {}
        try:
            pods = core_v1.list_namespaced_pod(
                namespace, label_selector=f"job-name={job_name}", _request_timeout=(3, 15)
            )
        except ApiException as e:
            logger.warning("Failed to list pods for node-readiness probe Job '%s': %s", job_name, e)
            return logs_by_node

        for pod in pods.items:
            pod_name = pod.metadata.name
            try:
                log_text = core_v1.read_namespaced_pod_log(
                    pod_name, namespace, _request_timeout=(3, 15)
                )
            except ApiException as e:
                logger.warning("Failed to read logs for probe pod '%s': %s", pod_name, e)
                continue
            parsed = parse_probe_log(log_text)
            node_name = parsed["node"] or (pod.spec.node_name if pod.spec else None)
            if node_name:
                logs_by_node[node_name] = log_text
        return logs_by_node

    @staticmethod
    def _cleanup_job(batch_v1: client.BatchV1Api, job_name: str, namespace: str) -> None:
        """Best-effort delete of the probe Job (its pods too, via Background propagation).

        ``ttlSecondsAfterFinished`` is a safety net if this delete fails or
        the caller's process dies before reaching this line.
        """
        try:
            batch_v1.delete_namespaced_job(
                job_name, namespace, propagation_policy="Background"
            )
        except ApiException as e:
            logger.warning("Failed to delete node-readiness probe Job '%s': %s", job_name, e)
