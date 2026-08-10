"""
Snapshot Service for Config Snapshots and Rollback (UX-011).

Provides CRUD operations for config snapshots:
- create_snapshot: Captures current config state (from cluster export or module-only)
- list_snapshots: Paginated list for a project
- get_snapshot: Single snapshot detail
- restore_snapshot: Re-applies a snapshot's config via the existing import workflow
- diff_snapshots: Compares two snapshots using existing diff logic
- delete_snapshot: Removes a snapshot

Usage:
    from services.snapshot_service import SnapshotService

    service = SnapshotService(db)
    snapshot = service.create_snapshot(project_id=1, trigger='manual', label='Before prod push')
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from models import ConfigSnapshot, KubernetesCluster, Project, ProjectModule

logger = logging.getLogger(__name__)


class SnapshotService:
    """Service for managing config snapshots."""

    def __init__(self, db: Session):
        self.db = db

    def create_snapshot(
        self,
        project_id: int,
        trigger: str = "manual",
        label: str | None = None,
        username: str | None = None,
        cluster_id: int | None = None,
    ) -> ConfigSnapshot:
        """
        Create a config snapshot for a project.

        If the project has a linked cluster, exports the full cluster config
        (CRDs + module variables). Otherwise, exports module variables only.

        Args:
            project_id: Project to snapshot
            trigger: 'manual', 'auto-before-apply', 'auto-before-upgrade'
            label: Optional user-provided label
            username: Who created this snapshot
            cluster_id: Specific cluster to export (auto-detected from project if not provided)

        Returns:
            The created ConfigSnapshot

        Raises:
            ValueError: If project not found
        """
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ValueError(f"Project {project_id} not found")

        # Determine cluster — use provided or find from project
        resolved_cluster_id = cluster_id
        if not resolved_cluster_id:
            cluster = self.db.query(KubernetesCluster).filter(
                KubernetesCluster.project_id == project_id
            ).first()
            if cluster:
                resolved_cluster_id = cluster.id

        # Try full cluster export if cluster is available
        config_data = self._export_config(project_id, resolved_cluster_id)

        # Compute summary stats
        resource_count = 0
        module_count = 0
        if "resources" in config_data:
            for _category, resources in config_data["resources"].items():
                resource_count += len(resources)
        if "module_config" in config_data:
            module_count = len(config_data["module_config"])

        snapshot = ConfigSnapshot(
            project_id=project_id,
            cluster_id=resolved_cluster_id,
            trigger=trigger,
            label=label,
            created_by=username,
            config_data=config_data,
            resource_count=resource_count,
            module_count=module_count,
        )
        self.db.add(snapshot)
        self.db.flush()
        self.db.refresh(snapshot)

        logger.info(
            f"Created config snapshot {snapshot.id} for project {project_id} "
            f"(trigger={trigger}, resources={resource_count}, modules={module_count})"
        )
        return snapshot

    def _export_config(self, project_id: int, cluster_id: int | None) -> dict[str, Any]:
        """
        Export config data for a snapshot.

        If cluster is available, uses full cluster export.
        Otherwise, falls back to module-only export.
        """
        if cluster_id:
            try:
                from services.config_export_service import export_cluster_config
                return export_cluster_config(cluster_id, self.db)
            except Exception as e:
                logger.warning(
                    f"Full cluster export failed for cluster {cluster_id}, "
                    f"falling back to module-only export: {e}"
                )

        # Module-only export (no cluster or cluster export failed)
        return self._export_modules_only(project_id)

    def _export_modules_only(self, project_id: int) -> dict[str, Any]:
        """Export module variables and status without cluster CRD data."""
        project = self.db.query(Project).filter(Project.id == project_id).first()
        modules = self.db.query(ProjectModule).filter(
            ProjectModule.project_id == project_id,
        ).all()

        module_config = {}
        for mod in modules:
            module_config[mod.path_in_project] = {
                "variables": mod.variables or {},
                "status": mod.status,
                "outputs": mod.outputs or {},
            }

        return {
            "bnk_forge_export": {
                "version": "1.0",
                "exported_at": datetime.now(UTC).isoformat() + "Z",
                "cluster": {"name": "", "id": None},
                "project": {
                    "name": project.name if project else "",
                    "id": project.id if project else None,
                    "project_type": project.project_type if project else None,
                },
                "export_metadata": {
                    "resource_counts": {},
                    "total_resources": 0,
                    "categories": [],
                    "snapshot_type": "module_only",
                },
            },
            "resources": {},
            "module_config": module_config,
        }

    def list_snapshots(
        self,
        project_id: int,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ConfigSnapshot], int]:
        """
        List snapshots for a project, ordered by most recent first.

        Returns:
            Tuple of (snapshots, total_count)
        """
        query = self.db.query(ConfigSnapshot).filter(
            ConfigSnapshot.project_id == project_id
        ).order_by(ConfigSnapshot.created_at.desc())

        total = query.count()
        snapshots = query.offset(offset).limit(limit).all()

        return snapshots, total

    def get_snapshot(self, snapshot_id: int) -> ConfigSnapshot | None:
        """Get a single snapshot by ID."""
        return self.db.query(ConfigSnapshot).filter(
            ConfigSnapshot.id == snapshot_id
        ).first()

    def restore_snapshot(self, snapshot_id: int) -> dict[str, Any]:
        """
        Restore config from a snapshot.

        This re-applies the snapshot's config data to the cluster using the
        existing config import workflow. Only applies CRD resources — module
        state is informational and not modified.

        Args:
            snapshot_id: Snapshot to restore

        Returns:
            Import results dict

        Raises:
            ValueError: If snapshot not found or no cluster to restore to
        """
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        if not snapshot.cluster_id:
            raise ValueError(
                "Cannot restore: snapshot has no associated cluster. "
                "Module-only snapshots are informational."
            )

        config_data = snapshot.config_data
        resources = config_data.get("resources", {})

        if not resources:
            return {
                "message": "Snapshot contains no cluster resources to restore",
                "results": {"applied": [], "failed": [], "skipped": []},
            }

        # Use the existing import logic from config_export route
        from kubernetes import client as k8s_client
        from kubernetes.client.rest import ApiException

        from services.kubernetes_service import KubernetesService

        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == snapshot.cluster_id
        ).first()
        if not cluster:
            raise ValueError(f"Cluster {snapshot.cluster_id} no longer exists")

        k8s_svc = KubernetesService(self.db)
        api_client = k8s_svc.load_kubeconfig(cluster)
        custom_api = k8s_client.CustomObjectsApi(api_client)

        results = {
            "applied": [],
            "failed": [],
            "skipped": [],
        }

        for category, resource_list in resources.items():
            for resource in resource_list:
                kind = resource.get("kind", "Unknown")
                name = resource.get("metadata", {}).get("name", "unknown")
                ns = resource.get("metadata", {}).get("namespace", "")
                api_version = resource.get("apiVersion", "v1")

                try:
                    if "/" in api_version:
                        group, version = api_version.rsplit("/", 1)
                    else:
                        group, version = "", api_version

                    if not group:
                        results["skipped"].append({
                            "kind": kind, "name": name, "namespace": ns,
                            "reason": "Core API import not supported",
                        })
                        continue

                    from services.execution.kubernetes_engine import KNOWN_PLURALS
                    from services.kubernetes._resources import resolve_plural_by_kind
                    plural = (
                        resolve_plural_by_kind(self.db, snapshot.cluster_id, kind, group or None)
                        or KNOWN_PLURALS.get(kind, kind.lower() + "s")
                    )

                    if ns:
                        custom_api.patch_namespaced_custom_object(
                            group=group, version=version, namespace=ns,
                            plural=plural, name=name, body=resource,
                            field_manager="bnk-forge-snapshot-restore", force=True,
                        )
                    else:
                        custom_api.patch_cluster_custom_object(
                            group=group, version=version,
                            plural=plural, name=name, body=resource,
                            field_manager="bnk-forge-snapshot-restore", force=True,
                        )

                    results["applied"].append({
                        "kind": kind, "name": name, "namespace": ns,
                    })
                except ApiException as e:
                    if e.status == 404:
                        results["skipped"].append({
                            "kind": kind, "name": name, "namespace": ns,
                            "reason": f"CRD not installed: {kind}",
                        })
                    else:
                        results["failed"].append({
                            "kind": kind, "name": name, "namespace": ns,
                            "error": str(e.reason)[:200],
                        })
                except Exception as e:
                    error_str = str(e)
                    if "404" in error_str or "resource type" in error_str.lower():
                        results["skipped"].append({
                            "kind": kind, "name": name, "namespace": ns,
                            "reason": f"CRD not installed: {kind}",
                        })
                    else:
                        results["failed"].append({
                            "kind": kind, "name": name, "namespace": ns,
                            "error": error_str[:200],
                        })

        logger.info(
            f"Snapshot {snapshot_id} restore: "
            f"{len(results['applied'])} applied, "
            f"{len(results['failed'])} failed, "
            f"{len(results['skipped'])} skipped"
        )

        return {
            "message": (
                f"Restore complete: {len(results['applied'])} applied, "
                f"{len(results['failed'])} failed, "
                f"{len(results['skipped'])} skipped"
            ),
            "results": results,
            "snapshot_id": snapshot_id,
            "target_cluster": cluster.name,
        }

    def diff_snapshots(
        self,
        snapshot_a_id: int,
        snapshot_b_id: int,
    ) -> dict[str, Any]:
        """
        Compare two snapshots using existing diff logic.

        Args:
            snapshot_a_id: First snapshot
            snapshot_b_id: Second snapshot

        Returns:
            Diff results dict

        Raises:
            ValueError: If either snapshot not found
        """
        snapshot_a = self.get_snapshot(snapshot_a_id)
        if not snapshot_a:
            raise ValueError(f"Snapshot {snapshot_a_id} not found")

        snapshot_b = self.get_snapshot(snapshot_b_id)
        if not snapshot_b:
            raise ValueError(f"Snapshot {snapshot_b_id} not found")

        from services.config_export_service import diff_configs

        diff_result = diff_configs(snapshot_a.config_data, snapshot_b.config_data)

        # Enhance with snapshot metadata
        diff_result["snapshot_a"] = {
            "id": snapshot_a.id,
            "trigger": snapshot_a.trigger,
            "label": snapshot_a.label,
            "created_at": snapshot_a.created_at.isoformat() if snapshot_a.created_at else None,
        }
        diff_result["snapshot_b"] = {
            "id": snapshot_b.id,
            "trigger": snapshot_b.trigger,
            "label": snapshot_b.label,
            "created_at": snapshot_b.created_at.isoformat() if snapshot_b.created_at else None,
        }

        return diff_result

    def delete_snapshot(self, snapshot_id: int) -> bool:
        """
        Delete a snapshot.

        Returns:
            True if deleted, False if not found
        """
        snapshot = self.get_snapshot(snapshot_id)
        if not snapshot:
            return False

        self.db.delete(snapshot)
        self.db.flush()

        logger.info(f"Deleted config snapshot {snapshot_id}")
        return True
