"""
Kubernetes cluster management tools.

Tools for managing clusters, browsing resources, and K8s operations.
Maps to: routes/k8s/clusters.py, routes/k8s/resources.py, routes/k8s/tunnels.py
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register cluster management tools with the MCP server."""

    # ------------------------------------------------------------------
    # Cluster CRUD
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_clusters() -> str:
        """List all Kubernetes clusters registered in BNK-Forge.

        Returns cluster names, IDs, connectivity status, and BNK installation status.
        """
        result = await client.get("/api/k8s/clusters")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_cluster(
        project_id: int,
        name: str,
        kubeconfig: str,
        cloud_provider: str = "",
        region: str = "",
        context: str = "",
        default_namespace: str = "default",
        ssh_tunnel_enabled: bool = False,
        ssh_remote_k8s_host: str = "localhost",
        ssh_remote_k8s_port: int = 6443,
        ssh_credential_id: int = 0,
        ssh_host_override: str = "",
    ) -> str:
        """Register a Kubernetes cluster under a project.

        Use this when a cluster has been provisioned out-of-band (e.g. by
        `awsbnkctl up` against AWS EKS) and now needs to be attached to a
        BNK-Forge project so the rest of the platform (scan, BNK ops, helm,
        config) can manage it. The kubeconfig is the source of truth for
        connectivity.

        Args:
            project_id: Project the cluster belongs to.
            name: Cluster display name (unique per project).
            kubeconfig: Base64-encoded kubeconfig YAML. For EKS, use the
                output of `aws eks update-kubeconfig --print --region <r>
                --name <c> | base64`.
            cloud_provider: One of "aws", "azure", "gcp", "ibm", "on-prem",
                "other". Empty leaves unset. AWS triggers region validation.
            region: Cloud region (e.g. "us-east-1"). Required when
                cloud_provider is "aws" or "eks".
            context: kubectl context name. Empty auto-detects from kubeconfig.
            default_namespace: Default namespace to operate in (default "default").
            ssh_tunnel_enabled: Set True to access the API server via SSH
                jumphost (on-prem). Default False (direct connection).
            ssh_remote_k8s_host: Remote K8s API host visible from the SSH
                target (default "localhost"). Only consulted when
                ssh_tunnel_enabled.
            ssh_remote_k8s_port: Remote API port (default 6443).
            ssh_credential_id: First-class SSH credential ID. Pass 0 to skip
                SSH (direct connection).
            ssh_host_override: Optional SSH endpoint override (empty for default).
        """
        body: dict[str, Any] = {
            "name": name,
            "kubeconfig": kubeconfig,
            "default_namespace": default_namespace,
            "ssh_tunnel_enabled": ssh_tunnel_enabled,
            "ssh_remote_k8s_host": ssh_remote_k8s_host,
            "ssh_remote_k8s_port": ssh_remote_k8s_port,
        }
        if cloud_provider:
            body["cloud_provider"] = cloud_provider
        if region:
            body["region"] = region
        if context:
            body["context"] = context
        if ssh_credential_id:
            body["ssh_credential_id"] = ssh_credential_id
        if ssh_host_override:
            body["ssh_host_override"] = ssh_host_override
        result = await client.post(
            f"/api/projects/{project_id}/k8s/clusters",
            json=body,
        )
        # Normalize to {success, cluster, message} envelope.
        # Breaking change vs prior bare-object shape — coordinated with awsbnkctl client.
        if not isinstance(result, dict):
            return json.dumps({"success": False, "raw": result}, indent=2)
        if result.get("ok") is False and "error" in result:
            # Structured MCP error envelope from backend — pass through as-is.
            return json.dumps(result, indent=2)
        message = result.get("message")
        cluster_entity = {k: v for k, v in result.items() if k != "message"}
        envelope: dict = {"success": True, "cluster": cluster_entity}
        if message is not None:
            envelope["message"] = message
        return json.dumps(envelope, indent=2)

    @mcp.tool()
    async def detect_eks_clusters(project_id: int) -> str:
        """Auto-discover and register EKS clusters from a project's deployed modules.

        Walks the project's IaC modules looking for managed-cluster outputs
        (cluster endpoint, name, kubeconfig material), then registers each
        discovered cluster as a Kubernetes cluster under the project. Idempotent:
        re-running surfaces existing registrations rather than duplicating.

        Preferred over `create_cluster` when the cluster was provisioned by a
        project module that already exposes the right outputs — saves the
        caller from constructing the kubeconfig.

        Args:
            project_id: Project to scan for managed-cluster modules.
        """
        result = await client.post(
            f"/api/projects/{project_id}/k8s/clusters/detect-eks",
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_cluster(cluster_id: int) -> str:
        """Get detailed information about a specific Kubernetes cluster.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def update_cluster(
        cluster_id: int,
        name: str = "",
        kubeconfig: str = "",
        cloud_provider: str = "",
        region: str = "",
        context: str = "",
        default_namespace: str = "",
        enabled_prerequisites: list[str] | None = None,
    ) -> str:
        """Update an existing cluster configuration.

        Only the fields you pass are updated; empty strings are skipped.
        Common uses: rename, swap kubeconfig (EKS auth tokens rotate),
        change default namespace, change `enabled_prerequisites`.

        Args:
            cluster_id: Cluster to update.
            name: New display name (empty leaves unchanged).
            kubeconfig: New base64-encoded kubeconfig YAML (empty leaves unchanged).
            cloud_provider: One of "aws", "azure", "gcp", "ibm", "on-prem", "other".
            region: Cloud region (validated against AWS regions when cloud_provider is "aws"/"eks").
            context: kubectl context name override.
            default_namespace: New default namespace.
            enabled_prerequisites: Per-cluster prereq selection (e.g.
                ["cert-manager", "multus", "storage"]). None leaves
                unchanged; an empty list disables all optional prereqs.
        """
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if kubeconfig:
            body["kubeconfig"] = kubeconfig
        if cloud_provider:
            body["cloud_provider"] = cloud_provider
        if region:
            body["region"] = region
        if context:
            body["context"] = context
        if default_namespace:
            body["default_namespace"] = default_namespace
        if enabled_prerequisites is not None:
            body["enabled_prerequisites"] = enabled_prerequisites
        result = await client.put(f"/api/k8s/clusters/{cluster_id}", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def delete_cluster(cluster_id: int) -> str:
        """Remove a Kubernetes cluster registration from BNK-Forge.

        Does NOT delete the underlying cluster — only forge's record. The
        cluster's helm releases / BNK install remain in place. Use this for
        deregistering an EKS cluster owned by an external tool (e.g.
        `awsbnkctl forge unregister`).

        Args:
            cluster_id: Cluster to remove.
        """
        result = await client.delete(f"/api/k8s/clusters/{cluster_id}")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def refresh_kubeconfig(cluster_id: int) -> str:
        """Refresh a cluster's kubeconfig (EKS auth token rotation, etc.).

        For EKS: calls `aws eks update-kubeconfig` using the project's
        AWS credentials. For on-prem: re-probes the SSH endpoint. Use this
        when API calls start returning 401 — the kubeconfig has likely expired.

        Args:
            cluster_id: Cluster whose kubeconfig to refresh.
        """
        result = await client.post(f"/api/k8s/clusters/{cluster_id}/refresh-kubeconfig")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def test_cluster_connectivity(cluster_id: int) -> str:
        """Test connectivity to a Kubernetes cluster.

        Verifies the kubeconfig is valid and the cluster API server is reachable.

        Args:
            cluster_id: The cluster ID to test
        """
        result = await client.post(f"/api/k8s/clusters/{cluster_id}/test")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def scan_cluster(cluster_id: int) -> str:
        """Scan a cluster for installed components, prerequisites, and recommendations.

        Detects BNK installation, checks prerequisites (cert-manager, Gateway API CRDs),
        node capacity, and provides module recommendations.

        Args:
            cluster_id: The cluster ID to scan
        """
        result = await client.post(f"/api/k8s/clusters/{cluster_id}/scan")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def list_namespaces(cluster_id: int) -> str:
        """List all namespaces in a Kubernetes cluster.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/namespaces")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # K8s Resources (generic)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_resources(
        cluster_id: int,
        resource_type: str,
        namespace: str = "",
    ) -> str:
        """List Kubernetes resources of a given type.

        Supports any K8s resource type: pods, deployments, services, configmaps,
        secrets, gateways, httproutes, etc.

        Args:
            cluster_id: The cluster ID
            resource_type: K8s resource type (e.g. "pods", "deployments", "services", "gateways", "httproutes")
            namespace: Namespace to filter by (empty for all namespaces)
        """
        params: dict[str, Any] = {}
        if namespace:
            params["namespace"] = namespace
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}", params=params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_resource(
        cluster_id: int,
        resource_type: str,
        name: str,
        namespace: str = "default",
    ) -> str:
        """Get a specific Kubernetes resource by name.

        Args:
            cluster_id: The cluster ID
            resource_type: K8s resource type (e.g. "pods", "deployments", "services")
            name: Resource name
            namespace: Resource namespace (default: "default")
        """
        params: dict[str, Any] = {"namespace": namespace}
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{name}/describe", params=params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_pod_logs(
        cluster_id: int,
        pod_name: str,
        namespace: str = "default",
        container: str = "",
        tail_lines: int = 100,
    ) -> str:
        """Get logs from a Kubernetes pod.

        Args:
            cluster_id: The cluster ID
            pod_name: Name of the pod
            namespace: Pod namespace (default: "default")
            container: Specific container name (empty for default container)
            tail_lines: Number of lines from the end (default 100)
        """
        params: dict[str, Any] = {
            "namespace": namespace,
            "tail_lines": tail_lines,
        }
        if container:
            params["container"] = container
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/pods/{pod_name}/logs", params=params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def describe_resource(
        cluster_id: int,
        resource_type: str,
        name: str,
        namespace: str = "default",
    ) -> str:
        """Describe a Kubernetes resource (similar to kubectl describe).

        Returns detailed information including events, conditions, and status.

        Args:
            cluster_id: The cluster ID
            resource_type: K8s resource type
            name: Resource name
            namespace: Resource namespace (default: "default")
        """
        params: dict[str, Any] = {"namespace": namespace}
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{name}/describe", params=params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_cluster_events(
        cluster_id: int,
        namespace: str = "",
        limit: int = 50,
    ) -> str:
        """Get recent events from a Kubernetes cluster.

        Useful for troubleshooting — shows warnings, errors, and state changes.

        Args:
            cluster_id: The cluster ID
            namespace: Filter by namespace (empty for all)
            limit: Maximum number of events (default 50)
        """
        params: dict[str, Any] = {"limit": limit}
        if namespace:
            params["namespace"] = namespace
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/events", params=params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_node_metrics(cluster_id: int) -> str:
        """Get node resource usage (CPU, memory) for a cluster.

        Equivalent to 'kubectl top nodes'. Requires metrics-server installed.

        Args:
            cluster_id: The cluster ID
        """
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/top/nodes")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_pod_metrics(
        cluster_id: int,
        namespace: str = "",
    ) -> str:
        """Get pod resource usage (CPU, memory) for a cluster.

        Equivalent to 'kubectl top pods'. Requires metrics-server installed.

        Args:
            cluster_id: The cluster ID
            namespace: Filter by namespace (empty for all)
        """
        params: dict[str, Any] = {}
        if namespace:
            params["namespace"] = namespace
        result = await client.get(f"/api/k8s/clusters/{cluster_id}/top/pods", params=params)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def restart_pod(
        cluster_id: int,
        pod_name: str,
        namespace: str = "default",
    ) -> str:
        """Restart (delete) a Kubernetes pod. The controller will recreate it.

        Args:
            cluster_id: The cluster ID
            pod_name: Name of the pod to restart
            namespace: Pod namespace (default: "default")
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/pods/{pod_name}/restart",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def scale_deployment(
        cluster_id: int,
        name: str,
        replicas: int,
        namespace: str = "default",
    ) -> str:
        """Scale a Kubernetes deployment to a specific number of replicas.

        Args:
            cluster_id: The cluster ID
            name: Deployment name
            replicas: Desired replica count
            namespace: Deployment namespace (default: "default")
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/deployments/{name}/scale",
            json={"replicas": replicas, "namespace": namespace},
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # K8s Resource Lifecycle (apply / update / delete / patch / label / annotate)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def apply_resource(
        cluster_id: int,
        resource_type: str,
        resource_yaml: str,
        namespace: str = "",
        dry_run: bool = False,
    ) -> str:
        """Create a Kubernetes resource from YAML (kubectl apply equivalent).

        Args:
            cluster_id: Target cluster.
            resource_type: K8s resource type (e.g. "pods", "deployments", "services").
            resource_yaml: Full YAML definition of the resource.
            namespace: Override namespace (empty = use namespace from YAML).
            dry_run: True returns the validation result without persisting.
        """
        body: dict[str, Any] = {"resource_yaml": resource_yaml, "dry_run": dry_run}
        if namespace:
            body["namespace"] = namespace
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}",
            json=body,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def update_resource(
        cluster_id: int,
        resource_type: str,
        resource_name: str,
        resource_yaml: str,
        namespace: str = "",
        dry_run: bool = False,
    ) -> str:
        """Replace a Kubernetes resource with new YAML (kubectl replace equivalent).

        Use `patch_resource` for partial updates.

        Args:
            cluster_id: Target cluster.
            resource_type: K8s resource type.
            resource_name: Name of the resource to replace.
            resource_yaml: Full YAML definition of the new state.
            namespace: Override namespace (empty = use namespace from YAML).
            dry_run: True validates without persisting.
        """
        body: dict[str, Any] = {"resource_yaml": resource_yaml, "dry_run": dry_run}
        if namespace:
            body["namespace"] = namespace
        result = await client.put(
            f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}",
            json=body,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def patch_resource(
        cluster_id: int,
        resource_type: str,
        resource_name: str,
        patch_data: dict[str, Any],
        namespace: str = "default",
        patch_type: str = "strategic",
    ) -> str:
        """Patch a Kubernetes resource (kubectl patch equivalent).

        Args:
            cluster_id: Target cluster.
            resource_type: K8s resource type.
            resource_name: Name of the resource to patch.
            patch_data: Patch document (shape depends on patch_type).
            namespace: Resource namespace (default "default").
            patch_type: "strategic" (default), "merge", or "json".
        """
        body: dict[str, Any] = {
            "patch_data": patch_data,
            "namespace": namespace,
            "patch_type": patch_type,
        }
        result = await client.patch(
            f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}",
            json=body,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def delete_resource(
        cluster_id: int,
        resource_type: str,
        resource_name: str,
        namespace: str = "default",
        dry_run: bool = False,
    ) -> str:
        """Delete a Kubernetes resource (kubectl delete equivalent).

        Args:
            cluster_id: Target cluster.
            resource_type: K8s resource type.
            resource_name: Resource name.
            namespace: Resource namespace (default "default").
            dry_run: True returns what would be deleted without persisting.
        """
        params: dict[str, Any] = {"namespace": namespace, "dry_run": dry_run}
        result = await client.delete(
            f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}",
            params=params,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def label_resource(
        cluster_id: int,
        resource_type: str,
        resource_name: str,
        labels: dict[str, str],
        namespace: str = "default",
        overwrite: bool = False,
    ) -> str:
        """Add or update labels on a Kubernetes resource (kubectl label equivalent).

        Args:
            cluster_id: Target cluster.
            resource_type: K8s resource type.
            resource_name: Resource name.
            labels: Map of label key → value to apply.
            namespace: Resource namespace (default "default").
            overwrite: True replaces existing labels with the same keys.
        """
        body: dict[str, Any] = {"labels": labels, "namespace": namespace, "overwrite": overwrite}
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/label",
            json=body,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def annotate_resource(
        cluster_id: int,
        resource_type: str,
        resource_name: str,
        annotations: dict[str, str],
        namespace: str = "default",
        overwrite: bool = False,
    ) -> str:
        """Add or update annotations on a Kubernetes resource (kubectl annotate equivalent).

        Args:
            cluster_id: Target cluster.
            resource_type: K8s resource type.
            resource_name: Resource name.
            annotations: Map of annotation key → value to apply.
            namespace: Resource namespace (default "default").
            overwrite: True replaces existing annotations with the same keys.
        """
        body: dict[str, Any] = {
            "annotations": annotations,
            "namespace": namespace,
            "overwrite": overwrite,
        }
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/resources/{resource_type}/{resource_name}/annotate",
            json=body,
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Deployment Rollouts
    # ------------------------------------------------------------------

    @mcp.tool()
    async def rollout_history(
        cluster_id: int,
        deployment_name: str,
        namespace: str = "default",
    ) -> str:
        """Get the revision history of a deployment (kubectl rollout history).

        Args:
            cluster_id: Target cluster.
            deployment_name: Deployment name.
            namespace: Deployment namespace (default "default").
        """
        result = await client.get(
            f"/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/history",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def rollout_status(
        cluster_id: int,
        deployment_name: str,
        namespace: str = "default",
    ) -> str:
        """Check rollout status for a deployment (kubectl rollout status).

        Returns whether the deployment is progressing, complete, or stuck.

        Args:
            cluster_id: Target cluster.
            deployment_name: Deployment name.
            namespace: Deployment namespace (default "default").
        """
        result = await client.get(
            f"/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/status",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def rollout_undo(
        cluster_id: int,
        deployment_name: str,
        namespace: str = "default",
        to_revision: int = 0,
    ) -> str:
        """Roll back a deployment to a previous revision (kubectl rollout undo).

        Args:
            cluster_id: Target cluster.
            deployment_name: Deployment name.
            namespace: Deployment namespace (default "default").
            to_revision: Specific revision to roll back to. Pass 0 for the
                immediately-prior revision.
        """
        params: dict[str, Any] = {"namespace": namespace}
        if to_revision:
            params["to_revision"] = to_revision
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/undo",
            params=params,
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def rollout_restart(
        cluster_id: int,
        deployment_name: str,
        namespace: str = "default",
    ) -> str:
        """Trigger a rolling restart of a deployment (kubectl rollout restart).

        Forces each pod in the deployment to recreate, using the rolling
        update strategy. Useful after a ConfigMap/Secret change.

        Args:
            cluster_id: Target cluster.
            deployment_name: Deployment name.
            namespace: Deployment namespace (default "default").
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/restart",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def rollout_pause(
        cluster_id: int,
        deployment_name: str,
        namespace: str = "default",
    ) -> str:
        """Pause an in-progress deployment rollout (kubectl rollout pause).

        Args:
            cluster_id: Target cluster.
            deployment_name: Deployment name.
            namespace: Deployment namespace (default "default").
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/pause",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def rollout_resume(
        cluster_id: int,
        deployment_name: str,
        namespace: str = "default",
    ) -> str:
        """Resume a paused deployment rollout (kubectl rollout resume).

        Args:
            cluster_id: Target cluster.
            deployment_name: Deployment name.
            namespace: Deployment namespace (default "default").
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/deployments/{deployment_name}/rollout/resume",
            params={"namespace": namespace},
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Node Operations
    # ------------------------------------------------------------------

    @mcp.tool()
    async def cordon_node(cluster_id: int, node_name: str) -> str:
        """Mark a node as unschedulable (kubectl cordon).

        Prevents new pods from being scheduled on the node; existing pods
        continue running.

        Args:
            cluster_id: Target cluster.
            node_name: Node name.
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/nodes/{node_name}/cordon",
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def uncordon_node(cluster_id: int, node_name: str) -> str:
        """Mark a node as schedulable (kubectl uncordon).

        Args:
            cluster_id: Target cluster.
            node_name: Node name.
        """
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/nodes/{node_name}/uncordon",
        )
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def drain_node(
        cluster_id: int,
        node_name: str,
        ignore_daemonsets: bool = True,
        delete_emptydir_data: bool = False,
    ) -> str:
        """Drain a node (kubectl drain) — evict pods + cordon.

        Cordons the node and gracefully evicts all pods, respecting
        PodDisruptionBudgets. Use before maintenance.

        Args:
            cluster_id: Target cluster.
            node_name: Node name.
            ignore_daemonsets: True (default) skips DaemonSet-owned pods.
            delete_emptydir_data: False (default) refuses to drain pods
                using emptyDir volumes; True deletes that data along with
                the pods.
        """
        params: dict[str, Any] = {
            "ignore_daemonsets": ignore_daemonsets,
            "delete_emptydir_data": delete_emptydir_data,
        }
        result = await client.post(
            f"/api/k8s/clusters/{cluster_id}/nodes/{node_name}/drain",
            params=params,
        )
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # SSH Tunnels (for on-prem clusters behind a jumphost)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def open_tunnel(cluster_id: int) -> str:
        """Explicitly open an SSH tunnel to a cluster's API server.

        Required for on-prem clusters whose API endpoint isn't directly
        reachable from forge. Tunnels are normally opened on-demand by
        the first request — call this to pre-warm or to verify SSH connectivity.

        Args:
            cluster_id: Cluster whose tunnel to open. The cluster's
                `ssh_credential_id` and `ssh_remote_k8s_*` config drive
                the tunnel setup.
        """
        result = await client.post(f"/api/k8s/clusters/{cluster_id}/tunnel/open")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def close_tunnel(cluster_id: int) -> str:
        """Close an open SSH tunnel for a cluster.

        Subsequent requests will re-open on demand. Use this to drop a
        stuck tunnel without restarting the forge backend.

        Args:
            cluster_id: Cluster whose tunnel to close.
        """
        result = await client.post(f"/api/k8s/clusters/{cluster_id}/tunnel/close")
        return json.dumps(result, indent=2)
