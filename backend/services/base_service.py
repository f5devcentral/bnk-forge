"""
Base service class providing common database lookup helpers.

IMP-001: Eliminates duplicated _get_project() across 5+ services.
"""
from sqlalchemy.orm import Session, joinedload

from core.errors import NotFoundError
from models import KubernetesCluster, ModuleLibrary, Project, ProjectModule


class BaseService:
    """Base class for database-backed services.

    Provides DRY helper methods for common entity lookups.
    Subclass and pass a db session to __init__.
    """

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Common lookup helpers
    # ------------------------------------------------------------------

    def _get_project(self, project_id: int) -> Project:
        """Get a project by ID or raise NotFoundError."""
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise NotFoundError("project", project_id)
        return project

    def _get_cluster(self, cluster_id: int) -> KubernetesCluster:
        """Get a Kubernetes cluster by ID or raise NotFoundError."""
        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == cluster_id,
        ).first()
        if not cluster:
            raise NotFoundError("cluster", cluster_id)
        return cluster

    def _get_module(self, module_id: int, *, with_library: bool = False) -> ProjectModule:
        """Get a project module by ID or raise NotFoundError.

        Args:
            module_id: The module database ID.
            with_library: If True, eagerly load the related ModuleLibrary.
        """
        query = self.db.query(ProjectModule)
        if with_library:
            query = query.options(
                joinedload(ProjectModule.library_module).load_only(
                    ModuleLibrary.id, ModuleLibrary.name
                )
            )
        module = query.filter(ProjectModule.id == module_id).first()
        if not module:
            raise NotFoundError("module", module_id)
        return module
