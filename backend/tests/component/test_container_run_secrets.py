"""Component tests for container-engine run-secret persistence.

Exercises ``services/execution/container_run_secrets``:
  - resolving a pull authfile from a ContainerRegistry / ProjectSecret / default,
  - upserting the project's ``cne_pull_secret`` ProjectSecret (create + update),
  - the cluster push being a best-effort no-op when no cluster exists.

Uses the real SQLite DB from conftest with mocked encryption.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import patch

import pytest

from models import ContainerRegistry, ProjectSecret
from services.execution import container_run_secrets as crs
from tests.factories import ProjectFactory


def _decode(authfile_b64: str) -> dict:
    return json.loads(base64.b64decode(authfile_b64).decode("utf-8"))


@pytest.fixture(autouse=True)
def _identity_encryption():
    # Make encrypt/decrypt identity so we can assert on stored values directly.
    with patch("core.encryption.encrypt_value", side_effect=lambda v: v), \
         patch("core.encryption.decrypt_value", side_effect=lambda v: v), \
         patch("services.execution.container_run_secrets.decrypt_value", side_effect=lambda v: v), \
         patch("services.secrets_service.encrypt_value", side_effect=lambda v: v), \
         patch("services.secrets_service.decrypt_value", side_effect=lambda v: v):
        yield


@pytest.mark.component
class TestPersistCnePullSecret:
    def test_creates_cne_pull_secret_when_absent(self, db):
        project = ProjectFactory(db)
        authfile = base64.b64encode(b'{"auths":{}}').decode()

        secret = crs.persist_cne_pull_secret(db, project.id, authfile)

        assert secret.name == crs.CNE_PULL_SECRET_NAME
        assert secret.secret_type == "value"
        assert secret.value_encrypted == authfile

        rows = db.query(ProjectSecret).filter(
            ProjectSecret.project_id == project.id,
            ProjectSecret.name == crs.CNE_PULL_SECRET_NAME,
        ).all()
        assert len(rows) == 1

    def test_updates_existing_cne_pull_secret_in_place(self, db):
        project = ProjectFactory(db)
        first = base64.b64encode(b'{"auths":{"a":{}}}').decode()
        second = base64.b64encode(b'{"auths":{"b":{}}}').decode()

        crs.persist_cne_pull_secret(db, project.id, first)
        crs.persist_cne_pull_secret(db, project.id, second)

        rows = db.query(ProjectSecret).filter(
            ProjectSecret.project_id == project.id,
            ProjectSecret.name == crs.CNE_PULL_SECRET_NAME,
        ).all()
        assert len(rows) == 1  # upsert, not a second row
        assert rows[0].value_encrypted == second


@pytest.mark.component
class TestResolvePullAuthfile:
    def test_resolves_from_ghcr_registry(self, db):
        project = ProjectFactory(db)
        db.add(ContainerRegistry(
            name="ghcr-prod", type="ghcr", registry_host="ghcr.io",
            username="jgruberf5", token_encrypted="ghp_token",
        ))
        db.flush()

        authfile = crs.resolve_pull_authfile(db, project.id, "ghcr.io")

        assert authfile is not None
        doc = _decode(authfile)
        assert "ghcr.io" in doc["auths"]
        assert doc["auths"]["ghcr.io"]["username"] == "jgruberf5"

    def test_resolves_from_existing_project_secret(self, db):
        project = ProjectFactory(db)
        existing = base64.b64encode(b'{"auths":{"repo.f5.com":{}}}').decode()
        db.add(ProjectSecret(
            project_id=project.id, name=crs.CNE_PULL_SECRET_NAME,
            secret_type="value", value_encrypted=existing, is_active=True,
        ))
        db.flush()

        authfile = crs.resolve_pull_authfile(db, project.id, "repo.f5.com")
        assert authfile == existing

    def test_falls_back_to_global_default(self, db):
        project = ProjectFactory(db)
        fallback = base64.b64encode(b'{"auths":{"far":{}}}').decode()
        with patch("services.execution.container_run_secrets.get_default", return_value=fallback):
            authfile = crs.resolve_pull_authfile(db, project.id, "unknown.host")
        assert authfile == fallback

    def test_returns_none_for_public_image_without_credentials(self, db):
        project = ProjectFactory(db)
        with patch("services.execution.container_run_secrets.get_default", return_value=None):
            authfile = crs.resolve_pull_authfile(db, project.id, "public.host")
        assert authfile is None

    def test_derived_registry_is_skipped(self, db):
        project = ProjectFactory(db)
        db.add(ContainerRegistry(
            name="ecr-prod", type="ecr", registry_host="123.dkr.ecr.us-east-1.amazonaws.com",
        ))
        db.flush()
        with patch("services.execution.container_run_secrets.get_default", return_value=None):
            authfile = crs.resolve_pull_authfile(
                db, project.id, "123.dkr.ecr.us-east-1.amazonaws.com"
            )
        # Derived token exchange is not implemented yet → no authfile from it.
        assert authfile is None


@pytest.mark.component
class TestPushPullSecretToCluster:
    def test_noop_when_project_has_no_cluster(self, db):
        project = ProjectFactory(db)
        authfile = base64.b64encode(b'{"auths":{}}').decode()
        assert crs.push_pull_secret_to_cluster(db, project, authfile) is False
