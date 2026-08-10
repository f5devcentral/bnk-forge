"""
SSH Credential model — first-class entity for SSH/on-prem access.

SSH is an *access method*, not a cloud provider. This model is orthogonal to
cloud_provider: an AWS cluster behind a private VPC might need SSH tunneling,
and an on-prem cluster might have direct network access with no SSH at all.

Replaces the SSH-specific columns previously bolted onto CloudCredentialTemplate.
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class SSHCredential(Base):
    """Reusable SSH credential for on-prem/bastion access."""
    __tablename__ = "ssh_credentials"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    # Connection details
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False, default=22)
    username = Column(String(255), nullable=False)

    # Authentication — key or password
    auth_type = Column(String(50), nullable=False, default="key")  # 'key' or 'password'
    password_encrypted = Column(Text, nullable=True)
    private_key_encrypted = Column(Text, nullable=True)
    key_passphrase_encrypted = Column(Text, nullable=True)

    # Metadata
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255), nullable=True)

    # Last connection-test outcome — driven by the SSH Credentials page's
    # auto-test on mount + explicit Re-test button + post-save hook.
    # Values: "ok" | "failed" | "running" | None (never tested).
    last_test_status = Column(String(16), nullable=True)
    last_test_at = Column(DateTime(timezone=True), nullable=True)
    last_test_message = Column(Text, nullable=True)

    # Relationships — clusters and projects that use this credential
    clusters = relationship(
        "KubernetesCluster",
        back_populates="ssh_credential",
        foreign_keys="KubernetesCluster.ssh_credential_id",
    )
    projects = relationship(
        "Project",
        back_populates="ssh_credential",
        foreign_keys="Project.ssh_credential_id",
    )
