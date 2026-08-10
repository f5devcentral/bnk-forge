"""
Container Registry model — first-class entity for OCI registry access.

A container registry is an *access method* for pulling/pushing artifacts
(images, helm charts, manifests) used by container-engine deployments. It is
orthogonal to cloud_provider — mirrors SSHCredential (global, name-unique,
multiple entries, encrypted secrets never serialized).

Two families of registry type:
  * Standalone (ghcr | quay | far) — carry their own encrypted secret.
  * Derived  (ecr | acr | icr | gar) — reference a CloudCredentialTemplate
    and exchange it for a short-lived registry token at pull time. Token
    exchange lands in the Declarative + supply-chain phase; for now /test
    returns a structured 'not yet implemented'.

FAR (F5 Artifact Registry) ingests a gzip tarball containing a single
Google-style service-account JSON; auth is HTTP Basic with username
'_json_key_base64' and password = base64(SA JSON).
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from database import Base

# Standalone types carry their own secret; derived types reference a template.
# Basic-auth standalone registries (ghcr/quay + the self-hostable
# artifactory/harbor/distribution/oci) authenticate to /v2/ with username + token;
# far uses the _json_key_base64 scheme. Derived types exchange a cloud credential.
STANDALONE_TYPES = (
    "ghcr",
    "quay",
    "dockerhub",
    "far",
    "artifactory",
    "harbor",
    "distribution",
    "oci",
)
# Derived types exchange a referenced CloudCredentialTemplate for a short-lived
# registry token. Only the types with a real exchange implementation are listed
# (icr → IBM IAM, ecr → AWS GetAuthorizationToken).
DERIVED_TYPES = ("ecr", "icr")
REGISTRY_TYPES = STANDALONE_TYPES + DERIVED_TYPES


class ContainerRegistry(Base):
    """Reusable container registry access method (global, name-unique)."""
    __tablename__ = "container_registries"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    # ghcr | quay | ecr | acr | icr | gar | far
    type = Column(String(20), nullable=False)
    registry_host = Column(String(255), nullable=False)

    # Standalone secrets (encrypted at rest, never serialized).
    # ghcr/quay: a PAT/robot token (with optional username).
    # far: the raw service-account JSON ingested from the *.tgz auth key.
    username = Column(String(255), nullable=True)
    token_encrypted = Column(Text, nullable=True)
    far_service_account_encrypted = Column(Text, nullable=True)

    # Derived types reference a cloud credential template for token exchange.
    credential_template_id = Column(
        Integer,
        ForeignKey("cloud_credential_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255), nullable=True)

    # Last connection-test outcome — "ok" | "failed" | "running" | None.
    last_test_status = Column(String(32), nullable=True)
    last_test_at = Column(DateTime(timezone=True), nullable=True)
    last_test_message = Column(Text, nullable=True)
