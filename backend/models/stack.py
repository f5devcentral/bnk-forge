"""StackTemplate and StackInstance models."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class StackTemplate(Base):
    """Pre-packaged module collections for common deployment patterns."""
    __tablename__ = "stack_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    slug = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text)
    category = Column(String(50), index=True)
    cloud_provider = Column(String(50))

    icon = Column(String(50))
    color = Column(String(20))

    estimated_time = Column(String(100))
    estimated_cost = Column(String(100))
    difficulty = Column(String(50))

    modules = Column(JSON)
    variable_templates = Column(JSON)
    prerequisites = Column(JSON)

    tags = Column(JSON)
    maturity = Column(String(50))
    outcomes = Column(JSON)
    platform_defaults = Column(JSON, nullable=True, default=None, comment="Per-platform variable overrides: {platform_profile: {var: value}}")
    bnk_version = Column(String(50), nullable=True, default=None, comment="Target BNK version compatibility (e.g. '2.2', '2.1+', null for infra-only)")

    version = Column(String(20), default="1.0.0")
    created_by = Column(String(50), default="system", index=True)
    is_public = Column(Boolean, default=False, index=True)
    forked_from = Column(Integer, ForeignKey("stack_templates.id"), nullable=True)

    is_active = Column(Boolean, default=True, index=True)
    is_featured = Column(Boolean, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    stack_instances = relationship("StackInstance", back_populates="template", cascade="all, delete-orphan")
    forked_from_template = relationship("StackTemplate", remote_side=[id], foreign_keys=[forked_from])

    __table_args__ = (
        Index("idx_stack_template_category_provider", "category", "cloud_provider"),
        Index("idx_stack_template_active_featured", "is_active", "is_featured"),
        Index("idx_stack_template_created_by_public", "created_by", "is_public"),
    )


class StackInstance(Base):
    """User's deployed stack instance in a project."""
    __tablename__ = "stack_instances"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id = Column(Integer, ForeignKey("stack_templates.id"), nullable=True, index=True)
    blueprint_release_id = Column(Integer, ForeignKey("blueprint_releases.id", ondelete="SET NULL"), nullable=True)

    name = Column(String(255), nullable=False, index=True)
    status = Column(String(50), nullable=False, index=True)

    variables = Column(JSON)

    # IMP-020: deployed_modules JSON column removed.
    # Use self.modules relationship (via ProjectModule.stack_instance_id FK) instead.
    current_step = Column(Integer, default=0)
    total_steps = Column(Integer)

    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    error_message = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    project = relationship("Project", back_populates="stack_instances")
    template = relationship("StackTemplate", back_populates="stack_instances")
    modules = relationship("ProjectModule", back_populates="stack_instance", lazy="selectin")

    @property
    def template_slug(self) -> str | None:
        """Return the template's slug for API responses."""
        return self.template.slug if self.template else None

    @property
    def deployed_modules(self):
        """Backward-compatible: returns list of deployed module IDs from FK relationship."""
        return [m.id for m in self.modules]

    @deployed_modules.setter
    def deployed_modules(self, value):
        """No-op setter for backward compatibility. Module tracking is handled by FK."""
        # IMP-020: The FK relationship (ProjectModule.stack_instance_id) is the
        # source of truth. Writes to this property are intentionally ignored.
        pass

    __table_args__ = (
        Index("idx_stack_instance_project_status", "project_id", "status"),
        Index("idx_stack_instance_template", "template_id"),
        Index("idx_stack_instance_blueprint_release", "blueprint_release_id"),
    )
