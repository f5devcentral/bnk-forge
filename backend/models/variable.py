"""VariableMapping and VariableMappingTemplate models."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class VariableMapping(Base):
    """Variable mappings for ProjectModules."""
    __tablename__ = "variable_mappings"

    id = Column(Integer, primary_key=True, index=True)
    project_module_id = Column(Integer, ForeignKey("project_modules.id", ondelete="CASCADE"), nullable=False)

    source_variable_name = Column(String(255), nullable=False)
    target_variable_name = Column(String(255), nullable=False)
    transform_type = Column(String(50), default="none")
    is_active = Column(Boolean, default=True)

    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship
    project_module = relationship("ProjectModule", back_populates="variable_mappings")

    __table_args__ = (
        Index("idx_variable_mapping_module", "project_module_id"),
        Index("idx_variable_mapping_source", "source_variable_name"),
        Index("idx_variable_mapping_target", "target_variable_name"),
    )


class VariableMappingTemplate(Base):
    """Reusable variable mapping templates."""
    __tablename__ = "variable_mapping_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text)

    mappings = Column(JSON, nullable=False)

    is_public = Column(Boolean, default=True)
    created_by = Column(String(255))

    usage_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_variable_template_name", "name"),
        Index("idx_variable_template_public", "is_public"),
    )
