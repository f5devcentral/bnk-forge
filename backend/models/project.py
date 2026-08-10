"""Project, ProjectModule, ProjectSecret, Environment, Deployment, and DeploymentLog models."""

from sqlalchemy import JSON, BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import deferred, relationship
from sqlalchemy.sql import func

from database import Base


class Project(Base):
    """Project configuration for OpenTofu module deployments."""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text)
    color = Column(String(20), default="blue")  # UI color theme
    icon = Column(String(50), default="cloud")  # UI icon
    is_active = Column(Boolean, default=False)  # Currently active project
    module_count = Column(Integer, default=0)
    deployed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    owner = Column(String(255))  # Display name (legacy, kept for backward compat)
    team = Column(String(255))
    visibility = Column(String(50), default="private")  # private, team, public

    # MU-001: Project ownership — FK to users table
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Module Library Framework
    workflow_type = Column(String(50))  # 'greenfield', 'partial', 'minimal'
    declared_infrastructure = Column(JSON)  # What user claims exists

    # Kubernetes settings
    k8s_context = Column(String(255))  # K8s context name
    k8s_sync_enabled = Column(Boolean, default=False)
    k8s_sync_interval_seconds = Column(Integer, default=300)

    # PLATFORM-CONTEXT-002: declared project platform context (additive)
    target_platform_profile = Column(String(50), nullable=True)
    platform_provider = Column(String(50), nullable=True)
    management_boundary = Column(String(100), nullable=True)

    # Environment configuration
    environment = Column(String(50), default="dev")  # dev, staging, prod, etc.

    # Project type — determines UI behavior and defaults
    project_type = Column(String(50))

    # Cloud provider settings
    cloud_provider = Column(String(50))  # aws, gcp, azure, on-prem, none
    cloud_sync_enabled = Column(Boolean, default=False)
    cloud_sync_interval_seconds = Column(Integer, default=600)
    cloud_credentials_encrypted = Column(Text)  # Encrypted JSON credentials (legacy)

    # Credential template (cloud providers: AWS, GCP, Azure)
    credential_template_id = Column(Integer, ForeignKey("cloud_credential_templates.id"), nullable=True)
    credential_template = relationship("CloudCredentialTemplate", back_populates="projects")

    # SSH credential — first-class SSH access, orthogonal to cloud provider
    ssh_credential_id = Column(Integer, ForeignKey("ssh_credentials.id", ondelete="SET NULL"), nullable=True)
    ssh_credential = relationship("SSHCredential", back_populates="projects", foreign_keys=[ssh_credential_id])

    # Terraform backend settings
    backend_type = Column(String(50), default="local")  # local, s3, gcs, azurerm
    region = Column(String(50))  # Set from system defaults when creating
    aws_profile = Column(String(100))  # AWS profile name (optional)

    # S3 Backend Configuration (OpenTofu 1.10+ with native locking)
    s3_state_bucket = Column(String(255))
    s3_state_key_prefix = Column(String(255))
    s3_state_region = Column(String(50))
    s3_use_native_locking = Column(Boolean, default=True)
    s3_dynamodb_table = Column(String(255))

    # OpenTofu State Encryption (v1.8+)
    state_encryption_enabled = Column(Boolean, default=True)
    encryption_provider = Column(String(50), default="pbkdf2")
    encryption_passphrase_encrypted = Column(Text)
    encryption_kms_key_id = Column(String(500))
    encryption_kms_region = Column(String(50))
    encryption_vault_url = Column(String(500))
    encryption_vault_key_name = Column(String(255))
    encryption_openbao_address = Column(String(500))
    encryption_openbao_token_encrypted = Column(Text)
    encryption_openbao_transit_key = Column(String(255))

    # IMP-013: AWS SSO + cached credential columns removed — migrated to CloudCredentialTemplate

    # On-Prem Infrastructure Host settings
    infra_enabled = Column(Boolean, default=False)
    infra_host = Column(String(500))
    infra_ssh_port = Column(Integer, default=22)
    infra_ssh_username = Column(String(255))
    infra_ssh_password_encrypted = Column(String(500))
    infra_ssh_key_encrypted = Column(Text)
    infra_auth_type = Column(String(20), default="password")
    infra_os_type = Column(String(50))

    # Project-level variables (shared across all modules)
    project_variables = Column(JSON)

    # Cross-project dependencies
    dependent_projects = Column(JSON, default=list)

    # D-022 Phase 4: Fleet rollout defaults (Project IS the Scope — no new container).
    # NULL → system literal fallback: concurrency=5, gate_mode=GATE_MODE_MANUAL.
    fleet_default_concurrency = Column(Integer, nullable=True)
    fleet_default_gate_mode = Column(String(50), nullable=True)

    meta_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="projects", foreign_keys=[user_id])
    project_modules = relationship("ProjectModule", back_populates="project", cascade="all, delete-orphan")
    environments = relationship("Environment", back_populates="project", cascade="all, delete-orphan")
    k8s_clusters = relationship("KubernetesCluster", back_populates="project", cascade="all, delete-orphan")
    drift_checks = relationship("DriftCheck", back_populates="project", cascade="all, delete-orphan")
    drift_settings = relationship("DriftSettings", back_populates="project", cascade="all, delete-orphan")
    stack_instances = relationship("StackInstance", back_populates="project", cascade="all, delete-orphan")
    secrets = relationship("ProjectSecret", back_populates="project", cascade="all, delete-orphan")
    config_snapshots = relationship("ConfigSnapshot", back_populates="project", cascade="all, delete-orphan")
    discovery_jobs = relationship("DiscoveryJob", back_populates="project", cascade="all, delete-orphan")

    # 1:1 extracted config tables (IMP-014 through IMP-019)
    backend_config = relationship("ProjectBackendConfig", back_populates="project", uselist=False, cascade="all, delete-orphan")
    encryption_config = relationship("ProjectEncryptionConfig", back_populates="project", uselist=False, cascade="all, delete-orphan")
    infra_config = relationship("ProjectInfraConfig", back_populates="project", uselist=False, cascade="all, delete-orphan")
    cloud_config = relationship("ProjectCloudConfig", back_populates="project", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_project_active", "is_active"),
    )


class ProjectModule(Base):
    """User's selected modules from the library for a specific project."""
    __tablename__ = "project_modules"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    module_library_id = Column(Integer, ForeignKey("module_library.id"), nullable=False)

    # Deployment configuration
    path_in_project = Column(String(1000), nullable=False)
    variables = Column(JSON)
    variable_overrides = Column(JSON)  # User-specified variable overrides for this module
    deployment_order = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)

    # Dependency management
    dependencies = Column(JSON, default=list)

    # Stack association (optional)
    stack_instance_id = Column(Integer, ForeignKey("stack_instances.id", ondelete="SET NULL"), nullable=True, index=True)

    # Captured outputs after successful apply
    outputs = Column(JSON)

    # Status
    status = Column(String(50), default="not_initialized")
    last_deployed_at = Column(DateTime(timezone=True))
    deployment_error = Column(Text)
    stage_detail = Column(String(255))

    # Persistent workspace fields (WORK-001)
    workspace_path = Column(String(500))
    last_init_at = Column(DateTime(timezone=True))
    plan_output = Column(Text)
    plan_serial = Column(Integer, default=0)
    vars_hash = Column(String(64))

    # Heartbeat lock + fencing token (v2_094/v2_097) — see services/entity_lock.py
    holding_task_id = Column(Integer, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    lock_fence_token = Column(BigInteger, nullable=False, server_default="0")
    lock_acquired_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    project = relationship("Project", back_populates="project_modules")
    library_module = relationship("ModuleLibrary", back_populates="project_modules", lazy="joined")
    stack_instance = relationship("StackInstance", back_populates="modules")
    variable_mappings = relationship("VariableMapping", back_populates="project_module", cascade="all, delete-orphan")
    snapshots = relationship("ModuleSnapshot", back_populates="module", cascade="all, delete-orphan")
    logs = relationship("DeploymentLog", back_populates="module", cascade="all, delete-orphan")
    drift_checks = relationship("DriftCheck", back_populates="module", cascade="all, delete-orphan")
    deployments = relationship("Deployment", back_populates="module", cascade="all, delete-orphan")
    state_transitions = relationship(
        "ModuleStateTransition", back_populates="module", cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("idx_project_module_project", "project_id"),
        Index("idx_project_module_library", "module_library_id"),
        Index("idx_project_module_path", "project_id", "path_in_project", unique=True),
    )


class ModuleStateTransition(Base):
    """Audit log of every project_modules.status change.

    Inserted by services.module_state.transition_module_status. The point
    of this table is debuggability: stuck-state forensics becomes
    `SELECT * FROM module_state_transitions WHERE module_id = X
     ORDER BY at DESC LIMIT 20` instead of grepping worker stderr.

    See memory: rfc_robust_module_locking.md (Phase 2).
    """
    __tablename__ = "module_state_transitions"

    # SQLite needs INTEGER (not BIGINT) for autoincrement to work on the
    # primary key; Postgres uses BIGSERIAL via the alembic migration. The
    # variant keeps both happy without a schema mismatch beyond integer
    # range, which we'll never hit on either dialect.
    id = Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    module_id = Column(
        Integer,
        ForeignKey("project_modules.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_status = Column(String(50), nullable=False)
    to_status = Column(String(50), nullable=False)
    # task_id and fence_token are nullable because HTTP-time pre-writes
    # (e.g. project_module_service.deploy_module marking the module
    # "applying" before the celery task picks up) happen outside any lock.
    task_id = Column(
        Integer,
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
    )
    fence_token = Column(BigInteger, nullable=True)
    reason = Column(String(500), nullable=True)
    at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    module = relationship("ProjectModule", back_populates="state_transitions")

    __table_args__ = (
        Index("idx_module_state_transitions_module_at", "module_id", "at"),
    )


class ProjectSecret(Base):
    """
    Project-level secrets for module inputs.
    Supports file secrets and value secrets.
    """
    __tablename__ = "project_secrets"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(255), nullable=False)
    description = Column(Text)
    secret_type = Column(String(20), nullable=False)  # 'file' or 'value'

    # For file secrets
    filename = Column(String(255))
    file_content_encrypted = Column(Text)
    file_size = Column(Integer)
    mime_type = Column(String(100))

    # For value secrets
    value_encrypted = Column(Text)

    # Metadata
    target_module_path = Column(String(500))
    target_variable_name = Column(String(255))

    is_active = Column(Boolean, default=True)
    last_used_at = Column(DateTime(timezone=True))

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255))

    # Relationships
    project = relationship("Project", back_populates="secrets")

    __table_args__ = (
        Index("idx_project_secret_project", "project_id"),
        Index("idx_project_secret_name", "project_id", "name", unique=True),
        Index("idx_project_secret_target", "project_id", "target_module_path", "target_variable_name"),
    )


class Environment(Base):
    """Environment configuration for multi-environment deployments."""
    __tablename__ = "environments"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    name = Column(String(50), nullable=False, index=True)
    description = Column(Text)
    is_default = Column(Boolean, default=False)

    # Cloud provider credentials (environment-specific, encrypted at rest)
    cloud_provider = Column(String(50))
    aws_access_key_id = Column(Text)
    aws_secret_access_key_encrypted = Column(Text)
    region = Column(String(50))
    gcp_credentials_json_encrypted = Column(Text)
    gcp_project_id = Column(String(255))
    azure_subscription_id = Column(String(255))
    azure_credentials_json_encrypted = Column(Text)

    # Environment-specific variables
    variables_json = Column(Text)
    tags_json = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    project = relationship("Project", back_populates="environments")

    __table_args__ = (
        Index("idx_environments_project_id", "project_id"),
        Index("idx_environments_name", "project_id", "name", unique=True),
    )


class Deployment(Base):
    """Track Terragrunt/Terraform deployments and executions."""
    __tablename__ = "deployments"

    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("project_modules.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String(50), nullable=False, index=True)
    status = Column(String(50), nullable=False, index=True)
    triggered_by = Column(String(255))
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Float)

    command = Column(Text)
    exit_code = Column(Integer)
    stdout = deferred(Column(Text))
    stderr = deferred(Column(Text))

    resources_to_add = Column(Integer, default=0)
    resources_to_change = Column(Integer, default=0)
    resources_to_destroy = Column(Integer, default=0)

    plan_file_path = Column(String(1000))
    git_commit = Column(String(64))
    git_branch = Column(String(255))
    environment = Column(String(100))
    tags = Column(JSON)
    meta_data = Column(JSON)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    module = relationship("ProjectModule", back_populates="deployments")

    __table_args__ = (
        Index("idx_deployment_module_status", "module_id", "status"),
        Index("idx_deployment_action_status", "action", "status"),
        Index("idx_deployment_started", "started_at"),
    )


class DeploymentLog(Base):
    """Deployment log entries for historical tracking and real-time streaming."""
    __tablename__ = "deployment_logs"

    id = Column(Integer, primary_key=True, index=True)
    module_id = Column(Integer, ForeignKey("project_modules.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    level = Column(String(20), nullable=False, index=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    module = relationship("ProjectModule", back_populates="logs")

    __table_args__ = (
        Index("idx_deployment_logs_module_id", "module_id"),
        Index("idx_deployment_logs_timestamp", "timestamp"),
        Index("idx_deployment_logs_level", "level"),
    )
