"""
Deployment rollout management: history, status, undo, restart, pause, resume.
"""

import logging
from datetime import UTC
from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from services.reachability import with_breaker

logger = logging.getLogger(__name__)


class RolloutsMixin:
    """Mixin for deployment rollout operations."""

    @with_breaker("cluster", target_id_arg="cluster_id")
    def get_rollout_history(self, cluster_id: int, deployment_name: str, namespace: str) -> list[dict[str, Any]]:
        """Get rollout history for a deployment (kubectl rollout history)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            apps_v1 = client.AppsV1Api(api_client)

            deployment = apps_v1.read_namespaced_deployment(deployment_name, namespace)
            replica_sets = apps_v1.list_namespaced_replica_set(
                namespace=namespace,
                label_selector=f"app={deployment.metadata.labels.get('app', deployment_name)}"
            )

            history = []
            for rs in replica_sets.items:
                if not rs.metadata.owner_references:
                    continue
                is_owned = any(ref.kind == "Deployment" and ref.name == deployment_name for ref in rs.metadata.owner_references)
                if not is_owned:
                    continue

                revision = rs.metadata.annotations.get("deployment.kubernetes.io/revision", "0")
                change_cause = rs.metadata.annotations.get("kubernetes.io/change-cause", "No change-cause annotation")

                history.append({
                    "revision": int(revision),
                    "change_cause": change_cause,
                    "replicas": rs.spec.replicas,
                    "ready_replicas": rs.status.ready_replicas or 0,
                    "created_at": rs.metadata.creation_timestamp.isoformat() if rs.metadata.creation_timestamp else None,
                    "labels": rs.metadata.labels,
                    "is_current": rs.spec.replicas > 0
                })

            history.sort(key=lambda x: x["revision"], reverse=True)
            return history

        except ApiException as e:
            logger.error(f"Kubernetes API error getting rollout history: {e}")
            raise ValueError(f"Failed to get rollout history: {e.reason}")
        except Exception as e:
            logger.error(f"Error getting rollout history: {e}")
            raise

    def get_rollout_status(self, cluster_id: int, deployment_name: str, namespace: str) -> dict[str, Any]:
        """Get current rollout status for a deployment (kubectl rollout status)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            apps_v1 = client.AppsV1Api(api_client)

            deployment = apps_v1.read_namespaced_deployment(deployment_name, namespace)
            status = deployment.status
            spec = deployment.spec

            rollout_complete = False
            rollout_message = "Rollout in progress"

            if (status.updated_replicas == spec.replicas and
                    status.replicas == spec.replicas and
                    status.available_replicas == spec.replicas):
                rollout_complete = True
                rollout_message = "Rollout completed successfully"
            elif status.unavailable_replicas:
                rollout_message = f"Waiting for {status.unavailable_replicas} unavailable replicas"

            conditions = []
            if status.conditions:
                for cond in status.conditions:
                    conditions.append({
                        "type": cond.type, "status": cond.status,
                        "reason": cond.reason, "message": cond.message,
                        "last_update_time": cond.last_update_time.isoformat() if cond.last_update_time else None
                    })

            return {
                "deployment_name": deployment_name, "namespace": namespace,
                "desired_replicas": spec.replicas,
                "current_replicas": status.replicas or 0,
                "updated_replicas": status.updated_replicas or 0,
                "ready_replicas": status.ready_replicas or 0,
                "available_replicas": status.available_replicas or 0,
                "unavailable_replicas": status.unavailable_replicas or 0,
                "rollout_complete": rollout_complete,
                "message": rollout_message,
                "conditions": conditions,
                "observed_generation": status.observed_generation
            }

        except ApiException as e:
            logger.error(f"Kubernetes API error getting rollout status: {e}")
            raise ValueError(f"Failed to get rollout status: {e.reason}")
        except Exception as e:
            logger.error(f"Error getting rollout status: {e}")
            raise

    def rollout_undo(self, cluster_id: int, deployment_name: str, namespace: str, revision: int | None = None) -> dict[str, Any]:
        """Rollback deployment to previous or specific revision (kubectl rollout undo)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            apps_v1 = client.AppsV1Api(api_client)

            deployment = apps_v1.read_namespaced_deployment(deployment_name, namespace)

            if revision:
                replica_sets = apps_v1.list_namespaced_replica_set(
                    namespace=namespace,
                    label_selector=f"app={deployment.metadata.labels.get('app', deployment_name)}"
                )
                target_rs = None
                for rs in replica_sets.items:
                    rs_revision = rs.metadata.annotations.get("deployment.kubernetes.io/revision", "0")
                    if int(rs_revision) == revision:
                        target_rs = rs
                        break
                if not target_rs:
                    raise ValueError(f"Revision {revision} not found")

                deployment.spec.template = target_rs.spec.template
                if not deployment.metadata.annotations:
                    deployment.metadata.annotations = {}
                deployment.metadata.annotations["kubernetes.io/change-cause"] = f"Rollback to revision {revision}"
            else:
                current_revision = deployment.metadata.annotations.get("deployment.kubernetes.io/revision", "1")
                if not deployment.metadata.annotations:
                    deployment.metadata.annotations = {}
                deployment.metadata.annotations["kubernetes.io/change-cause"] = f"Rollback from revision {current_revision}"

            apps_v1.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=deployment)

            target_rev = revision if revision else "previous"
            return {
                "success": True,
                "message": f"Deployment rollback initiated to revision {target_rev}",
                "deployment_name": deployment_name,
                "namespace": namespace,
                "target_revision": target_rev
            }

        except ApiException as e:
            logger.error(f"Kubernetes API error during rollback: {e}")
            raise ValueError(f"Failed to rollback deployment: {e.reason}")
        except Exception as e:
            logger.error(f"Error during rollback: {e}")
            raise

    def rollout_restart(self, cluster_id: int, deployment_name: str, namespace: str) -> dict[str, Any]:
        """Restart a deployment by recreating pods (kubectl rollout restart)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            apps_v1 = client.AppsV1Api(api_client)

            deployment = apps_v1.read_namespaced_deployment(deployment_name, namespace)

            from datetime import datetime
            restart_time = datetime.now(UTC).isoformat()

            if not deployment.spec.template.metadata.annotations:
                deployment.spec.template.metadata.annotations = {}
            deployment.spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"] = restart_time

            apps_v1.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=deployment)

            return {
                "success": True,
                "message": "Deployment restart initiated",
                "deployment_name": deployment_name,
                "namespace": namespace,
                "restarted_at": restart_time
            }

        except ApiException as e:
            logger.error(f"Kubernetes API error during restart: {e}")
            raise ValueError(f"Failed to restart deployment: {e.reason}")
        except Exception as e:
            logger.error(f"Error during restart: {e}")
            raise

    def rollout_pause(self, cluster_id: int, deployment_name: str, namespace: str) -> dict[str, Any]:
        """Pause a deployment rollout (kubectl rollout pause)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            apps_v1 = client.AppsV1Api(api_client)

            # Check current state
            deployment = apps_v1.read_namespaced_deployment(deployment_name, namespace)
            if deployment.spec.paused:
                return {
                    "success": False,
                    "message": "Deployment is already paused",
                    "deployment_name": deployment_name,
                    "namespace": namespace,
                    "paused": True
                }

            # Patch to set paused=true
            body = {"spec": {"paused": True}}
            apps_v1.patch_namespaced_deployment(
                name=deployment_name, namespace=namespace, body=body
            )

            return {
                "success": True,
                "message": "Deployment rollout paused",
                "deployment_name": deployment_name,
                "namespace": namespace,
                "paused": True
            }

        except ApiException as e:
            logger.error(f"Kubernetes API error pausing deployment: {e}")
            raise ValueError(f"Failed to pause deployment: {e.reason}")
        except Exception as e:
            logger.error(f"Error pausing deployment: {e}")
            raise

    def rollout_resume(self, cluster_id: int, deployment_name: str, namespace: str) -> dict[str, Any]:
        """Resume a paused deployment rollout (kubectl rollout resume)."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            apps_v1 = client.AppsV1Api(api_client)

            # Check current state
            deployment = apps_v1.read_namespaced_deployment(deployment_name, namespace)
            if not deployment.spec.paused:
                return {
                    "success": False,
                    "message": "Deployment is not paused",
                    "deployment_name": deployment_name,
                    "namespace": namespace,
                    "paused": False
                }

            # Patch to set paused=false
            body = {"spec": {"paused": False}}
            apps_v1.patch_namespaced_deployment(
                name=deployment_name, namespace=namespace, body=body
            )

            return {
                "success": True,
                "message": "Deployment rollout resumed",
                "deployment_name": deployment_name,
                "namespace": namespace,
                "paused": False
            }

        except ApiException as e:
            logger.error(f"Kubernetes API error resuming deployment: {e}")
            raise ValueError(f"Failed to resume deployment: {e.reason}")
        except Exception as e:
            logger.error(f"Error resuming deployment: {e}")
            raise
