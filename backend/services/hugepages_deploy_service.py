"""Deploy 2Mi HugePages onto the TMM nodes of a Kubernetes cluster.

Surfaces as a one-click action on the "Configure HugePages" recommendation in
the cluster-scan UI. The deploy primitive is a Kubernetes ``Job`` pinned to
TMM nodes (labelled ``app=f5-tmm`` or ``node-role.kubernetes.io/f5-tmm``),
with ``parallelism == completions == N_tmm_nodes`` and pod-anti-affinity on
``kubernetes.io/hostname`` so exactly one pod lands per node.

Each pod runs a tiny privileged container that:
  * writes the requested page count to ``/proc/sys/vm/nr_hugepages`` —
    immediate kernel allocation, no reboot needed for 2Mi pages;
  * best-effort writes ``/etc/sysctl.d/90-f5-bnk-hugepages.conf`` on the
    host so ``systemd-sysctl`` replays the setting across reboots.

The second write is best-effort: on Container-Optimized OS (GKE default)
``/etc`` is read-only and the write fails harmlessly. For reboot-persistent
config on COS, the cluster admin must use NodePool ``linuxNodeConfig``.

DaemonSet was considered and rejected — ``vm.nr_hugepages`` is a one-shot
sysctl write; a long-lived pod watching nothing adds zero value and occupies
a slot per node forever. A Job with ``ttlSecondsAfterFinished`` is the right
primitive: runs once, exits, auto-deletes.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from core.errors import BadRequestError, InternalError
from services.bnk.sizing import BnkDeploymentSize, hugepages_2mi_count
from services.kubernetes._base import KubernetesServiceBase

logger = logging.getLogger(__name__)


_TMM_APP_LABEL = "f5-tmm"
_TMM_ROLE_LABEL = "node-role.kubernetes.io/f5-tmm"
_SYSCTL_FILE = "90-f5-bnk-hugepages.conf"
_DEFAULT_NAMESPACE = "kube-system"
_DEFAULT_IMAGE = "busybox:1.36.1"
_JOB_TTL_SEC = 120
_JOB_BACKOFF_LIMIT = 2
_JOB_ACTIVE_DEADLINE_SEC = 300


def _resolve_image(image: str | None) -> str:
    """Pick the container image for the tuner pod.

    Precedence: explicit request override → ``BNK_FORGE_HUGEPAGES_IMAGE``
    env var → ``busybox:1.36.1``. In airgapped environments the operator
    must mirror the fallback image or set the env override to a mirrored path.
    """
    if image:
        return image
    return os.environ.get("BNK_FORGE_HUGEPAGES_IMAGE") or _DEFAULT_IMAGE


def _tuner_script(page_count: int) -> str:
    """Shell snippet run inside the tuner pod."""
    return (
        f"set -eu; "
        f"echo 'setting vm.nr_hugepages={page_count} on node '\"$NODE_NAME\"; "
        f"echo {page_count} > /host-proc/sys/vm/nr_hugepages; "
        f"current=$(cat /host-proc/sys/vm/nr_hugepages); "
        f"echo \"runtime nr_hugepages=$current\"; "
        # Persist across reboots -- best effort; fails on read-only /etc (COS).
        f"if [ -w /host-etc-sysctl ]; then "
        f"  echo 'vm.nr_hugepages={page_count}' > /host-etc-sysctl/{_SYSCTL_FILE}; "
        f"  echo 'persisted to /etc/sysctl.d/{_SYSCTL_FILE}'; "
        f"else "
        f"  echo 'warning: /etc/sysctl.d not writable; reboot persistence requires nodepool config'; "
        f"fi"
    )


def build_job_manifest(
    *,
    job_name: str,
    namespace: str,
    node_count: int,
    page_count: int,
    image: str,
) -> dict[str, Any]:
    """Render the Job spec as a plain dict (suitable for BatchV1Api)."""
    script = _tuner_script(page_count)

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "bnk-forge-hugepages-tuner",
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
                        "app.kubernetes.io/name": "bnk-forge-hugepages-tuner",
                        "job-name": job_name,
                    },
                },
                "spec": {
                    "restartPolicy": "OnFailure",
                    "hostPID": False,
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": "app",
                                                "operator": "In",
                                                "values": [_TMM_APP_LABEL],
                                            }
                                        ]
                                    },
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": _TMM_ROLE_LABEL,
                                                "operator": "Exists",
                                            }
                                        ]
                                    },
                                ]
                            }
                        },
                        # One pod per node -- combined with parallelism=N
                        # this yields one pod per TMM node.
                        "podAntiAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": [
                                {
                                    "labelSelector": {
                                        "matchLabels": {
                                            "app.kubernetes.io/name": "bnk-forge-hugepages-tuner",
                                            "job-name": job_name,
                                        }
                                    },
                                    "topologyKey": "kubernetes.io/hostname",
                                }
                            ]
                        },
                    },
                    "tolerations": [
                        # TMM nodes are commonly tainted; tolerate everything so
                        # the Job can land wherever the scanner found a TMM node.
                        {"operator": "Exists"},
                    ],
                    "containers": [
                        {
                            "name": "tuner",
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
                                {"name": "host-proc", "mountPath": "/host-proc"},
                                {
                                    "name": "host-etc-sysctl",
                                    "mountPath": "/host-etc-sysctl",
                                },
                            ],
                            "resources": {
                                "requests": {"cpu": "10m", "memory": "16Mi"},
                                "limits": {"cpu": "100m", "memory": "64Mi"},
                            },
                        }
                    ],
                    "volumes": [
                        {"name": "host-proc", "hostPath": {"path": "/proc"}},
                        {
                            "name": "host-etc-sysctl",
                            "hostPath": {
                                "path": "/etc/sysctl.d",
                                "type": "DirectoryOrCreate",
                            },
                        },
                    ],
                },
            },
        },
    }


class HugePagesDeployService(KubernetesServiceBase):
    """Configure 2Mi HugePages on TMM nodes via a one-shot Kubernetes Job."""

    def deploy(
        self,
        cluster_id: int,
        size: BnkDeploymentSize,
        namespace: str = _DEFAULT_NAMESPACE,
        image: str | None = None,
    ) -> dict[str, Any]:
        """Create the tuner Job. Returns a summary dict."""
        page_count = hugepages_2mi_count(size)
        resolved_image = _resolve_image(image)

        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)

        tmm_nodes = self._list_tmm_nodes(api_client)
        if not tmm_nodes:
            raise BadRequestError(
                "No TMM nodes found (label 'app=f5-tmm' or "
                "'node-role.kubernetes.io/f5-tmm'). Label target nodes before "
                "configuring HugePages."
            )

        job_name = f"bnk-hugepages-{size.lower()}-{int(time.time())}"
        manifest = build_job_manifest(
            job_name=job_name,
            namespace=namespace,
            node_count=len(tmm_nodes),
            page_count=page_count,
            image=resolved_image,
        )

        batch_v1 = client.BatchV1Api(api_client)
        try:
            created = batch_v1.create_namespaced_job(
                namespace=namespace, body=manifest
            )
        except ApiException as e:
            logger.error("Failed to create HugePages tuner Job: %s", e)
            raise InternalError(f"Kubernetes API error creating Job: {e.reason}") from e

        logger.info(
            "Dispatched HugePages tuner Job '%s' in namespace '%s' for %d TMM node(s) "
            "with vm.nr_hugepages=%d (size=%s, image=%s)",
            job_name,
            namespace,
            len(tmm_nodes),
            page_count,
            size,
            resolved_image,
        )

        return {
            "success": True,
            "job_name": created.metadata.name,
            "namespace": namespace,
            "size": size,
            "page_count": page_count,
            "memory_gib_per_node": page_count * 2 / 1024,
            "target_node_count": len(tmm_nodes),
            "target_nodes": [n["name"] for n in tmm_nodes],
            "image": resolved_image,
            "message": (
                f"Dispatched HugePages tuner to {len(tmm_nodes)} TMM node(s). "
                f"Each node will reserve {page_count} × 2Mi pages "
                f"({page_count * 2 // 1024} GiB). Rescan in ~30s to verify."
            ),
        }

    @staticmethod
    def _list_tmm_nodes(api_client: client.ApiClient) -> list[dict[str, Any]]:
        """Return TMM nodes matching either label scheme."""
        core_v1 = client.CoreV1Api(api_client)
        seen: dict[str, dict[str, Any]] = {}

        for selector in (f"app={_TMM_APP_LABEL}", _TMM_ROLE_LABEL):
            try:
                resp = core_v1.list_node(label_selector=selector)
            except ApiException as e:
                logger.warning("list_node(%s) failed: %s", selector, e)
                continue
            for node in resp.items:
                name = node.metadata.name
                if name and name not in seen:
                    seen[name] = {
                        "name": name,
                        "labels": dict(node.metadata.labels or {}),
                    }

        return list(seen.values())
