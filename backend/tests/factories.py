"""
Factory Boy factories for BNK-Forge backend models.

Provides consistent, composable test data factories for all major models.
Each factory creates minimal valid objects with sensible defaults.
Use factory traits or explicit overrides for specific test scenarios.

Usage:
    from tests.factories import UserFactory, ProjectFactory, ModuleFactory

    # Simple creation
    user = UserFactory(db)
    project = ProjectFactory(db)

    # With overrides
    admin = UserFactory(db, role="admin", username="superadmin")
    aws_project = ProjectFactory(db, cloud_provider="aws", region="us-east-1")

    # With relationships
    module = ProjectModuleFactory(db, project=project)
    task = TaskFactory(db, project=project, module=module)
"""

from datetime import datetime, timezone

from models import (
    BnkUpgrade,
    KubernetesCluster,
    ModuleLibrary,
    Project,
    ProjectModule,
    StackInstance,
    StackTemplate,
    Task,
    User,
)
from models.benchmark import BenchmarkTarget, ProxyDeployment
from models.enums import BnkUpgradeStatus, ProxyDeploymentStatus
from services.auth_service import hash_password

# ---------------------------------------------------------------------------
# Sequence counters for unique values
# ---------------------------------------------------------------------------

_counters: dict[str, int] = {}


def _next_seq(name: str) -> int:
    """Thread-unsafe sequence counter (fine for tests)."""
    _counters[name] = _counters.get(name, 0) + 1
    return _counters[name]


def reset_sequences():
    """Reset all sequence counters. Call between test sessions if needed."""
    _counters.clear()


# ---------------------------------------------------------------------------
# User Factory
# ---------------------------------------------------------------------------

def UserFactory(
    db,
    *,
    username: str | None = None,
    email: str | None = None,
    password: str = "testpassword123",
    role: str = "operator",
    is_active: bool = True,
    must_change_password: bool = False,
) -> User:
    """Create a User with a hashed password."""
    n = _next_seq("user")
    user = User(
        username=username or f"user-{n}",
        email=email or f"user-{n}@bnk-forge.test",
        hashed_password=hash_password(password),
        role=role,
        is_active=is_active,
        must_change_password=must_change_password,
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# Project Factory
# ---------------------------------------------------------------------------

def ProjectFactory(
    db,
    *,
    name: str | None = None,
    description: str = "Test project",
    project_type: str = "kubernetes",
    cloud_provider: str = "on-prem",
    environment: str = "dev",
    backend_type: str = "local",
    region: str | None = None,
    is_active: bool = True,
    color: str = "#2563eb",
    icon: str = "cloud",
    **kwargs,
) -> Project:
    """Create a minimal valid Project."""
    n = _next_seq("project")
    project = Project(
        name=name or f"test-project-{n}",
        description=description,
        project_type=project_type,
        cloud_provider=cloud_provider,
        environment=environment,
        backend_type=backend_type,
        region=region,
        is_active=is_active,
        color=color,
        icon=icon,
        **kwargs,
    )
    db.add(project)
    db.flush()
    return project


# ---------------------------------------------------------------------------
# ModuleLibrary Factory (catalog entry)
# ---------------------------------------------------------------------------

def ModuleLibraryFactory(
    db,
    *,
    name: str | None = None,
    category: str = "kubernetes",
    path: str | None = None,
    provider: str = "bnk-forge",
    description: str = "A test module",
    git_source: str = "https://github.com/example/modules.git",
    is_official: bool = True,
    is_active: bool = True,
    **kwargs,
) -> ModuleLibrary:
    """Create a ModuleLibrary catalog entry."""
    n = _next_seq("module_library")
    module = ModuleLibrary(
        name=name or f"test-module-{n}",
        category=category,
        path=path or f"modules/test-module-{n}",
        provider=provider,
        description=description,
        git_source=git_source,
        is_official=is_official,
        is_active=is_active,
        **kwargs,
    )
    db.add(module)
    db.flush()
    return module


# ---------------------------------------------------------------------------
# ProjectModule Factory (module instance within a project)
# ---------------------------------------------------------------------------

def ProjectModuleFactory(
    db,
    *,
    project: Project | None = None,
    library_module: ModuleLibrary | None = None,
    path_in_project: str | None = None,
    status: str = "not_initialized",
    variables: dict | None = None,
    deployment_order: int = 0,
    enabled: bool = True,
    **kwargs,
) -> ProjectModule:
    """Create a ProjectModule linked to a project and library module."""
    n = _next_seq("project_module")
    if project is None:
        project = ProjectFactory(db)
    if library_module is None:
        library_module = ModuleLibraryFactory(db)

    module = ProjectModule(
        project_id=project.id,
        module_library_id=library_module.id,
        path_in_project=path_in_project or f"modules/{library_module.name}",
        status=status,
        variables=variables or {},
        deployment_order=deployment_order,
        enabled=enabled,
        **kwargs,
    )
    db.add(module)
    db.flush()
    return module


# ---------------------------------------------------------------------------
# Task Factory
# ---------------------------------------------------------------------------

def TaskFactory(
    db,
    *,
    project: Project | None = None,
    module: ProjectModule | None = None,
    task_type: str = "apply",
    status: str = "queued",
    triggered_by: str = "user",
    celery_task_id: str | None = None,
    logs: str | None = None,
    error: str | None = None,
    **kwargs,
) -> Task:
    """Create a Task record."""
    n = _next_seq("task")
    if project is None:
        project = ProjectFactory(db)

    task = Task(
        task_type=task_type,
        project_id=project.id,
        module_id=module.id if module else None,
        status=status,
        triggered_by=triggered_by,
        celery_task_id=celery_task_id or f"celery-task-{n}",
        logs=logs,
        error=error,
        **kwargs,
    )
    db.add(task)
    db.flush()
    return task


# ---------------------------------------------------------------------------
# StackTemplate Factory
# ---------------------------------------------------------------------------

def StackTemplateFactory(
    db,
    *,
    name: str | None = None,
    slug: str | None = None,
    description: str = "A test stack template",
    category: str = "network",
    cloud_provider: str = "aws",
    modules: list | None = None,
    is_public: bool = True,
    is_active: bool = True,
    **kwargs,
) -> StackTemplate:
    """Create a StackTemplate."""
    n = _next_seq("stack_template")
    template = StackTemplate(
        name=name or f"Test Stack {n}",
        slug=slug or f"test-stack-{n}",
        description=description,
        category=category,
        cloud_provider=cloud_provider,
        modules=modules or [],
        is_public=is_public,
        is_active=is_active,
        **kwargs,
    )
    db.add(template)
    db.flush()
    return template


# ---------------------------------------------------------------------------
# StackInstance Factory
# ---------------------------------------------------------------------------

def StackInstanceFactory(
    db,
    *,
    project: Project | None = None,
    template: StackTemplate | None = None,
    name: str | None = None,
    status: str = "pending",
    **kwargs,
) -> StackInstance:
    """Create a StackInstance linked to a project and template."""
    n = _next_seq("stack_instance")
    if project is None:
        project = ProjectFactory(db)
    if template is None:
        template = StackTemplateFactory(db)

    instance = StackInstance(
        project_id=project.id,
        template_id=template.id,
        name=name or f"test-instance-{n}",
        status=status,
        **kwargs,
    )
    db.add(instance)
    db.flush()
    return instance


# ---------------------------------------------------------------------------
# KubernetesCluster Factory
# ---------------------------------------------------------------------------

def KubernetesClusterFactory(
    db,
    *,
    project: Project | None = None,
    name: str | None = None,
    context: str | None = None,
    api_server: str | None = None,
    cluster_type: str = "generic",
    status: str = "active",
    version: str = "1.28",
    **kwargs,
) -> KubernetesCluster:
    """Create a KubernetesCluster."""
    n = _next_seq("k8s_cluster")
    cluster = KubernetesCluster(
        name=name or f"test-cluster-{n}",
        context=context or f"test-context-{n}",
        project_id=project.id if project else None,
        api_server=api_server or f"https://k8s-{n}.example.com:6443",
        status=status,
        version=version,
        **kwargs,
    )
    db.add(cluster)
    db.flush()
    return cluster


# ---------------------------------------------------------------------------
# BenchmarkTarget Factory
# ---------------------------------------------------------------------------

def BenchmarkTargetFactory(
    db,
    *,
    cluster: KubernetesCluster | None = None,
    name: str | None = None,
    llm_base_url: str = "http://vllm-svc.default:8000",
    llm_model: str = "openai/gpt-test-model",
    llm_namespace: str = "default",
    status: str = "active",
    **kwargs,
) -> BenchmarkTarget:
    """Create a BenchmarkTarget."""
    n = _next_seq("benchmark_target")
    if cluster is None:
        cluster = KubernetesClusterFactory(db)
    target = BenchmarkTarget(
        name=name or f"test-target-{n}",
        cluster_id=cluster.id,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_namespace=llm_namespace,
        status=status,
        **kwargs,
    )
    db.add(target)
    db.flush()
    return target


# ---------------------------------------------------------------------------
# ProxyDeployment Factory (Phase 1.6 — includes lock columns at defaults)
# ---------------------------------------------------------------------------

def ProxyDeploymentFactory(
    db,
    *,
    target: BenchmarkTarget | None = None,
    proxy_type: str = "nginx",
    status: str = ProxyDeploymentStatus.PENDING,
    **kwargs,
) -> ProxyDeployment:
    """Create a ProxyDeployment row with lock columns at default (0 / NULL) values."""
    if target is None:
        target = BenchmarkTargetFactory(db)
    deploy = ProxyDeployment(
        target_id=target.id,
        proxy_type=proxy_type,
        status=status,
        lock_fence_token=0,
        holding_task_id=None,
        heartbeat_at=None,
        lock_acquired_at=None,
        **kwargs,
    )
    db.add(deploy)
    db.flush()
    return deploy


# ---------------------------------------------------------------------------
# BnkUpgrade Factory (Phase 1.6 — includes lock columns at defaults)
# ---------------------------------------------------------------------------

def BnkUpgradeFactory(
    db,
    *,
    cluster: KubernetesCluster | None = None,
    from_version: str = "2.1.0",
    to_version: str = "2.2.0",
    status: str = BnkUpgradeStatus.READY,
    **kwargs,
) -> BnkUpgrade:
    """Create a BnkUpgrade row with lock columns at default (0 / NULL) values."""
    if cluster is None:
        cluster = KubernetesClusterFactory(db)
    upgrade = BnkUpgrade(
        cluster_id=cluster.id,
        from_version=from_version,
        to_version=to_version,
        status=status,
        lock_fence_token=0,
        holding_task_id=None,
        heartbeat_at=None,
        lock_acquired_at=None,
        **kwargs,
    )
    db.add(upgrade)
    db.flush()
    return upgrade
