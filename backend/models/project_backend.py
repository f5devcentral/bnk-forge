"""ProjectBackendConfig — extracted from Project model (IMP-015).

Stores S3/remote backend configuration for OpenTofu state storage
in a dedicated 1:1 table instead of cluttering the Project model.
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from database import Base


class ProjectBackendConfig(Base):
    """Remote state backend settings for a project (1:1 with Project)."""
    __tablename__ = "project_backend_configs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True)

    s3_state_bucket = Column(String(255))
    s3_state_key_prefix = Column(String(255))
    s3_state_region = Column(String(50))
    s3_use_native_locking = Column(Boolean, default=True)
    s3_dynamodb_table = Column(String(255))
    state_storage_provider = Column(String(50))
    s3_endpoint = Column(String(500))

    # Relationship
    project = relationship("Project", back_populates="backend_config")
