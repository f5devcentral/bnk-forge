"""Kubernetes cluster and F5 BNK networking models."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import ClusterStatus


class KubernetesCluster(Base):
    """Kubernetes cluster configuration and connection info."""
    __tablename__ = "kubernetes_clusters"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    context = Column(String(255), nullable=False)  # kubectl context name
    api_server = Column(String(500))
    version = Column(String(50))
    status = Column(String(50), default=ClusterStatus.ACTIVE)
    last_synced_at = Column(DateTime(timezone=True))
    meta_data = Column(JSON)  # Additional cluster metadata

    # Project integration (Phase 1)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    kubeconfig_encrypted = Column(Text, nullable=True)  # Base64 encoded encrypted kubeconfig
    cloud_provider = Column(String(50))  # aws, azure, gcp, on-prem
    region = Column(String(100))  # Cloud region
    default_namespace = Column(String(255), default="default")

    # PLATFORM-CONTEXT-002: detected cluster platform context (additive)
    detected_platform_profile = Column(String(50), nullable=True)
    detected_platform_provider = Column(String(50), nullable=True)
    platform_capabilities = Column(JSON, nullable=True)
    platform_constraints = Column(JSON, nullable=True)

    # Per-cluster opt-in: which prerequisite checks the scanner runs.
    # NULL means "use the global default set". Stored as a list of prereq
    # IDs (e.g. ["cert-manager", "multus", "storage", "gateway-api"]).
    enabled_prerequisites = Column(JSON, nullable=True)

    # Namespaces where BNK/F5 components were actually discovered on this cluster.
    # Written back after each discovery run. NULL / [] = not yet discovered.
    # Used as the fast-path seed for subsequent discovery runs (in addition to
    # the static BNK_NAMESPACES fallback).
    discovered_namespaces = Column(JSON, nullable=True)

    # SSH tunnel: opt-in per-cluster toggle + remote K8s endpoint
    ssh_tunnel_enabled = Column(Boolean, default=False, nullable=False, server_default='false')
    ssh_remote_k8s_host = Column(String(255), default='localhost', nullable=True)
    ssh_remote_k8s_port = Column(Integer, default=6443, nullable=True)
    # Per-cluster SSH credential — first-class SSH access (orthogonal to cloud_provider)
    ssh_credential_id = Column(Integer, ForeignKey("ssh_credentials.id", ondelete="SET NULL"), nullable=True)
    # Optional: override the SSH endpoint host/port from the credential (e.g. kind on a different
    # node that shares the same SSH key as the stored credential).
    ssh_host_override = Column(String(255), nullable=True)
    # Legacy: kept for backward compatibility during migration
    ssh_credential_template_id = Column(Integer, ForeignKey("cloud_credential_templates.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    project = relationship("Project", back_populates="k8s_clusters")
    ssh_credential = relationship("SSHCredential", back_populates="clusters", foreign_keys=[ssh_credential_id])
    ssh_credential_template = relationship("CloudCredentialTemplate", foreign_keys=[ssh_credential_template_id])
    gateways = relationship("K8sGateway", back_populates="cluster", cascade="all, delete-orphan")
    firewall_policies = relationship("FirewallPolicy", back_populates="cluster", cascade="all, delete-orphan")


class K8sGateway(Base):
    """F5 BIG-IP Next Gateway resources."""
    __tablename__ = "k8s_gateways"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    namespace = Column(String(255), nullable=False, index=True)
    gateway_class = Column(String(255))
    listeners = Column(JSON)  # Array of listener configurations
    addresses = Column(JSON)  # Array of gateway addresses
    status = Column(String(50))  # ready, pending, error
    conditions = Column(JSON)  # Status conditions
    annotations = Column(JSON)
    labels = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    cluster = relationship("KubernetesCluster", back_populates="gateways")

    __table_args__ = (
        Index("idx_gateway_cluster_namespace", "cluster_id", "namespace"),
    )


class FirewallPolicy(Base):
    """F5 firewall policies and security rules."""
    __tablename__ = "firewall_policies"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    namespace = Column(String(255), nullable=False, index=True)
    policy_type = Column(String(50))  # SecurityPolicy, FirewallPolicy, etc.
    rules = Column(JSON)  # Array of firewall rules
    default_action = Column(String(50))  # allow, deny
    priority = Column(Integer)
    target_ref = Column(JSON)  # Reference to target Gateway/HTTPRoute
    status = Column(String(50))  # active, inactive, error
    annotations = Column(JSON)
    labels = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    cluster = relationship("KubernetesCluster", back_populates="firewall_policies")

    __table_args__ = (
        Index("idx_policy_cluster_namespace", "cluster_id", "namespace"),
        Index("idx_policy_type", "policy_type"),
    )


class EgressConfiguration(Base):
    """Egress gateway configurations."""
    __tablename__ = "egress_configurations"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    namespace = Column(String(255), nullable=False, index=True)
    gateway_ref = Column(String(255))  # Reference to Gateway
    snat_pool_ref = Column(String(255))  # Reference to SNATPool
    routes = Column(JSON)  # Array of egress routes
    status = Column(String(50))  # active, inactive, error
    annotations = Column(JSON)
    labels = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_egress_cluster_namespace", "cluster_id", "namespace"),
    )


class SnatPool(Base):
    """SNAT pool configurations for egress traffic."""
    __tablename__ = "snat_pools"

    id = Column(Integer, primary_key=True, index=True)
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id"), nullable=False)
    name = Column(String(255), nullable=False, index=True)
    namespace = Column(String(255), nullable=False, index=True)
    ip_addresses = Column(JSON)  # Array of IP addresses in the pool
    status = Column(String(50))  # active, inactive, error
    annotations = Column(JSON)
    labels = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_snat_cluster_namespace", "cluster_id", "namespace"),
    )
