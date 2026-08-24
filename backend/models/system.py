"""System models: ApplicationSetting, SyncJob, User, AuditLog, Notification, HelmChart, CloudCredentialTemplate."""

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, false
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class ApplicationSetting(Base):
    """Application configuration and settings."""
    __tablename__ = "application_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(Text)
    value_type = Column(String(50), default="string")
    description = Column(Text)
    category = Column(String(100), default="general", index=True)
    is_encrypted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SyncJob(Base):
    """Track background sync job execution and status."""
    __tablename__ = "sync_jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_type = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100))
    status = Column(String(50), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True))
    duration_seconds = Column(Float)
    records_synced = Column(Integer, default=0)
    records_created = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    records_deleted = Column(Integer, default=0)
    error_message = Column(Text)
    error_details = Column(JSON)
    meta_data = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_sync_job_type_status", "job_type", "status"),
        Index("idx_sync_started_at", "started_at"),
    )


class CloudCredentialTemplate(Base):
    """Reusable cloud provider credential templates."""
    __tablename__ = "cloud_credential_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text)
    provider = Column(String(50), nullable=False)

    # AWS credentials
    aws_auth_method = Column(String(50))
    aws_profile = Column(String(100))
    region = Column(String(50))
    aws_access_key_id = Column(String(100))
    aws_secret_access_key_encrypted = Column(Text)
    aws_session_token_encrypted = Column(Text)
    aws_credentials_expiry = Column(DateTime(timezone=True))

    # AWS SSO
    aws_sso_enabled = Column(Boolean, default=False)
    aws_sso_start_url = Column(String(500))
    aws_sso_region = Column(String(50))
    aws_sso_account_id = Column(String(20))
    aws_sso_role_name = Column(String(255))

    # AWS SSO Session
    aws_sso_access_token_encrypted = Column(Text)
    aws_sso_refresh_token_encrypted = Column(Text)
    aws_sso_client_id = Column(String(255))
    aws_sso_client_secret_encrypted = Column(Text)
    aws_sso_token_expiry = Column(DateTime(timezone=True))
    aws_sso_authenticated_at = Column(DateTime(timezone=True))

    # GCP credentials (future)
    gcp_credentials_encrypted = Column(Text)
    gcp_project_id = Column(String(255))

    # Azure credentials (future)
    azure_subscription_id = Column(String(255))
    azure_tenant_id = Column(String(255))
    azure_credentials_encrypted = Column(Text)

    # IBM Cloud credentials
    ibmcloud_api_key_encrypted = Column(Text)
    ibmcloud_resource_group = Column(String(255))
    ibm_cos_instance_name = Column(String(255))
    ibm_cos_instance_crn = Column(Text)
    ibm_cos_hmac_access_key_id = Column(String(255))
    ibm_cos_hmac_secret_access_key_encrypted = Column(Text)
    # Cached IAM bearer token (short-lived; refreshed on demand). Mirrors
    # AWS aws_credentials_expiry / aws_sso_token_expiry. Saves a ~250ms
    # /identity/token round-trip on every IBM region listing, COS query,
    # and engine_router kubeconfig refresh.
    ibm_iam_token_encrypted = Column(Text, nullable=True)
    ibm_iam_token_expiry = Column(DateTime(timezone=True), nullable=True)

    # Terraform Cloud (private modules). Bearer token encrypted with the same
    # Fernet key as other secrets; hostname defaults to app.terraform.io and
    # supports Terraform Enterprise self-hosted instances.
    tfc_api_token_encrypted = Column(Text, nullable=True)
    tfc_hostname = Column(String(255), nullable=True)

    # SSH / On-Premises credentials
    ssh_host = Column(String(255), nullable=True)
    ssh_port = Column(Integer, default=22, nullable=True)
    ssh_username = Column(String(255), nullable=True)
    ssh_auth_type = Column(String(50), nullable=True)
    ssh_password_encrypted = Column(Text, nullable=True)
    ssh_key_encrypted = Column(Text, nullable=True)
    ssh_key_passphrase_encrypted = Column(Text, nullable=True)

    # Generic credentials field
    credentials_encrypted = Column(Text)

    # Passive cloud-API observation (RFC connectivity Phase 2). Every
    # successful boto3 call stamps last_successful_call_at; every failure
    # stamps last_error_*. UI reads these for an at-a-glance "credential
    # health" indicator without forcing a synchronous test.
    last_successful_call_at = Column(DateTime(timezone=True), nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(64), nullable=True)
    last_error_message = Column(Text, nullable=True)

    # Metadata
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255))

    # Relationship
    projects = relationship("Project", back_populates="credential_template")


class User(Base):
    """User accounts for authentication and authorization."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default="operator")
    is_active = Column(Boolean, default=True, nullable=False)
    must_change_password = Column(Boolean, default=False, nullable=False)
    # bonnyr-f5 #188: provenance for service accounts (mcp). ensure_service_user
    # refuses to reconcile a row it did NOT create as a service account, so
    # pointing MCP_SERVICE_USERNAME at a human row can't take it over.
    # server_default mirrors migration v2_154 so fresh (create_all) and migrated
    # installs agree on the DB-level default (bonnyr-f5 #188 nit).
    is_service_account = Column(Boolean, default=False, server_default=false(), nullable=False)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # MU-001: Projects owned by this user
    projects = relationship("Project", back_populates="user", foreign_keys="Project.user_id")
    api_tokens = relationship("ApiToken", back_populates="user", cascade="all, delete-orphan")


class ApiToken(Base):
    """Long-lived API tokens for non-interactive (CLI / CI/CD) access.

    The plaintext token is shown only once at creation; the DB stores a
    SHA-256 hash. Token format: ``bnk_<32-char-base32>`` for visual recognition
    in logs and audit trails, plus a leading prefix Forge can fast-reject
    without an O(n) hash compare.
    """
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    token_prefix = Column(String(16), nullable=False, index=True)
    role = Column(String(50), nullable=False, default="operator")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="api_tokens")


class AuditLog(Base):
    """Audit trail for user actions and system events."""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    user = Column(String(255), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False, index=True)
    resource_type = Column(String(100), index=True)
    resource_id = Column(String(255))
    resource_name = Column(String(255))
    status = Column(String(50))
    details = Column(JSON)
    ip_address = Column(String(50))
    user_agent = Column(String(500))
    http_method = Column(String(10))
    http_path = Column(String(500))
    http_status_code = Column(Integer)
    duration_ms = Column(Integer)
    request_id = Column(String(36), nullable=True, index=True)  # OBS-001: Correlation ID
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user_account = relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_audit_timestamp_action", "timestamp", "action"),
        Index("idx_audit_user_action", "user", "action"),
        Index("idx_audit_resource", "resource_type", "resource_id"),
    )


class Notification(Base):
    """User notifications for deployment events and system alerts."""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user = Column(String(255), index=True)
    type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    resource_type = Column(String(100))
    resource_id = Column(Integer)
    is_read = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    read_at = Column(DateTime(timezone=True))

    # D-025 P1: extended notification fields
    severity = Column(String(20), nullable=False, server_default="info")
    category = Column(String(50), nullable=False, server_default="general", index=True)
    action_url = Column(String(512), nullable=True)
    dedupe_key = Column(String(255), nullable=True, index=True)
    # "metadata" is a reserved name on SQLAlchemy Declarative; use attribute name
    # extra_metadata mapped to column name "metadata"
    extra_metadata = Column("metadata", JSON, nullable=True)

    __table_args__ = (
        Index("idx_notification_user_unread", "user", "is_read"),
        Index("idx_notification_user_dedupe", "user", "dedupe_key"),
    )


class HelmChart(Base):
    """Uploaded or cloned custom Helm charts."""
    __tablename__ = "helm_charts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    version = Column(String(100), nullable=False)
    app_version = Column(String(100))
    description = Column(Text)

    source_type = Column(String(50), default="uploaded", index=True)
    source_chart = Column(String(255))
    source_repository = Column(String(255))

    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)
    checksum = Column(String(64))

    chart_metadata = Column(JSON)

    uploaded_by = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_helm_chart_name_version", "name", "version", unique=True),
        Index("idx_helm_chart_source", "source_type"),
    )
