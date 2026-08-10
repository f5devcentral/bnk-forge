"""ProjectEncryptionConfig — extracted from Project model (IMP-014).

Stores OpenTofu state encryption configuration (provider, keys, passphrases)
in a dedicated 1:1 table instead of cluttering the Project model.
"""

from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class ProjectEncryptionConfig(Base):
    """OpenTofu state encryption settings for a project (1:1 with Project)."""
    __tablename__ = "project_encryption_configs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True)

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

    # Relationship
    project = relationship("Project", back_populates="encryption_config")
