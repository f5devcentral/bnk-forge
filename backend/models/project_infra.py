"""ProjectInfraConfig — extracted from Project model (IMP-016).

Stores on-prem infrastructure / SSH configuration for a project
in a dedicated 1:1 table instead of cluttering the Project model.
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class ProjectInfraConfig(Base):
    """On-prem infrastructure/SSH settings for a project (1:1 with Project)."""
    __tablename__ = "project_infra_configs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True)

    infra_enabled = Column(Boolean, default=False)
    infra_host = Column(String(500))
    infra_ssh_port = Column(Integer, default=22)
    infra_ssh_username = Column(String(255))
    infra_ssh_password_encrypted = Column(String(500))
    infra_ssh_key_encrypted = Column(Text)
    infra_auth_type = Column(String(20), default="password")
    infra_os_type = Column(String(50))

    # Relationship
    project = relationship("Project", back_populates="infra_config")
