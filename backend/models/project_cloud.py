"""ProjectCloudConfig — extracted from Project model (IMP-019).

Stores cloud sync settings and legacy encrypted credentials
in a dedicated 1:1 table. The cloud_provider and credential_template_id
columns intentionally remain on Project as they are FK-like identifiers.

Columns extracted:
- cloud_sync_enabled, cloud_sync_interval_seconds
- cloud_credentials_encrypted (legacy — new projects use CloudCredentialTemplate)
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from database import Base


class ProjectCloudConfig(Base):
    """Cloud sync and legacy credential settings for a project (1:1 with Project)."""
    __tablename__ = "project_cloud_configs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True)

    cloud_sync_enabled = Column(Boolean, default=False)
    cloud_sync_interval_seconds = Column(Integer, default=600)
    cloud_credentials_encrypted = Column(Text)  # Legacy encrypted JSON credentials

    # Relationship
    project = relationship("Project", back_populates="cloud_config")
