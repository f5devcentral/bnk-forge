"""
F5 BIG-IP Device model — classic TMOS appliance / VE registered with Forge.

Forge probes the device read-only via iControl REST (GET only).
Device facts are cached in `device_info`; failures are recorded in
`last_discovery_error` (D-017: never a silent empty success).

Schema mirrors BareMetalHost for discovery-column conventions.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from database import Base


class F5BIGIPDevice(Base):
    """A registered classic F5 BIG-IP device."""

    __tablename__ = "f5_bigip_devices"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    # Identity
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Management access
    mgmt_host = Column(String(255), nullable=False)
    mgmt_port = Column(Integer, nullable=False, default=443)
    mgmt_credential_id = Column(
        Integer, ForeignKey("f5_credentials.id", ondelete="SET NULL"), nullable=True
    )

    # Per-device TLS policy — NEVER a global verify=False
    verify_https_cert = Column(Boolean, nullable=False, default=True)

    # Cached device facts — updated on each successful probe
    # Shape: {model, version, ha_state, partitions, as3_tenants}
    device_info = Column(JSON, nullable=True)

    # Discovery state — mirrors BareMetalHost conventions (D-017)
    last_discovery_at = Column(DateTime(timezone=True), nullable=True)
    last_discovery_status = Column(String(32), nullable=True)  # pending/probing/completed/failed
    last_discovery_error = Column(Text, nullable=True)

    # Audit
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255), nullable=True)

    # Relationships.
    # passive_deletes=True defers to the DB's ON DELETE CASCADE — without it,
    # SQLAlchemy iterates the backref on project delete and emits
    # UPDATE f5_bigip_devices SET project_id=NULL, which the NOT NULL
    # constraint rejects (same pattern as BareMetalHost / Project.bare_metal_hosts).
    project = relationship(
        "Project",
        backref=backref(
            "f5_bigip_devices",
            cascade="all, delete-orphan",
            passive_deletes=True,
        ),
    )
    credential = relationship("F5Credential", foreign_keys=[mgmt_credential_id])

    __table_args__ = (
        Index("idx_f5_device_project_name", "project_id", "name", unique=True),
    )
