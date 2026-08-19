"""Use-Case Artifact models (D-034 Phase 0 tracer).

A UseCaseArtifact is a named, versioned, portable bundle of BNK config/policy
CRs — modelled on `bf_conf_template` (named/versioned) and `BlueprintRelease`
(immutable versions; a content change is always a new version, never an
in-place edit).

  - UseCaseArtifact — the mutable container: rename/describe only.
  - UseCaseArtifactVersion — immutable once created. `cr_templates` holds the
    parameterized CRs (`${param}` tokens substituted for lifted values);
    `param_schema` describes each lifted param. `content_hash` covers the
    templated structure + param key/type/path set, NOT concrete values, so
    capture is address-independent (see docs/adr/D-034).
  - UseCaseApplication — the binding: "cluster X runs artifact-version Y with
    these injected values", so drift always compares against the exact
    desired-state that was applied.
"""

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class UseCaseArtifact(Base):
    """Mutable container for a named use-case artifact. Content lives on versions."""

    __tablename__ = "usecase_artifacts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    created_by = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    versions = relationship(
        "UseCaseArtifactVersion", back_populates="artifact", cascade="all, delete-orphan"
    )


class UseCaseArtifactVersion(Base):
    """Immutable once created — a content change always creates a new version."""

    __tablename__ = "usecase_artifact_versions"

    id = Column(Integer, primary_key=True, index=True)
    artifact_id = Column(Integer, ForeignKey("usecase_artifacts.id", ondelete="CASCADE"), nullable=False)
    version = Column(String(50), nullable=False)
    matching_bnk_version = Column(String(64), nullable=True)

    # Parameterized CRs (${param} tokens substituted for lifted values)
    cr_templates = Column(JSON, nullable=False)
    # List of param descriptors: {key, type, kind, is_list, required, source_paths}
    param_schema = Column(JSON, nullable=False)

    source = Column(String(50), nullable=False)  # "captured_from_cluster" | "authored"
    source_cluster_id = Column(
        Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True
    )
    # Hash of templated structure + param key/type/path set — excludes concrete values.
    content_hash = Column(String(64), nullable=False, index=True)

    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    artifact = relationship("UseCaseArtifact", back_populates="versions")

    __table_args__ = (
        UniqueConstraint("artifact_id", "version", name="uq_usecase_artifact_version"),
        Index("idx_usecase_artifact_version_artifact", "artifact_id"),
        Index("idx_usecase_artifact_version_content_hash", "content_hash"),
    )


class UseCaseApplication(Base):
    """Binding: cluster X runs artifact-version Y with these injected param values."""

    __tablename__ = "usecase_applications"

    id = Column(Integer, primary_key=True, index=True)
    artifact_version_id = Column(
        Integer, ForeignKey("usecase_artifact_versions.id", ondelete="CASCADE"), nullable=False
    )
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), nullable=False)
    param_values = Column(JSON, nullable=False)

    applied_by = Column(String(255), nullable=True)
    applied_at = Column(DateTime(timezone=True), server_default=func.now())

    version = relationship("UseCaseArtifactVersion")

    __table_args__ = (
        Index("idx_usecase_application_version", "artifact_version_id"),
        Index("idx_usecase_application_cluster", "cluster_id"),
    )
