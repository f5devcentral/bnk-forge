"""
Integration tests for project secret routes — /api/projects/{id}/secrets.

Covers: create value secret, list, delete, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB + encryption.
"""

import json
import tarfile
from io import BytesIO
from unittest.mock import patch

import pytest

from models import ModuleLibrary, StackTemplate


class TestSecretCreate:
    """POST /api/projects/{id}/secrets/value."""

    def test_create_value_secret(self, client, admin_headers, sample_user, sample_project, db):
        """Admin can create a value secret."""
        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/value",
            json={
                "name": "AWS_ACCESS_KEY",
                "value": "AKIAIOSFODNN7EXAMPLE",
                "description": "Test AWS key",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    def test_create_secret_viewer_denied(self, client, viewer_headers, all_test_users, sample_project):
        """Viewer cannot create secrets."""
        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/value",
            json={
                "name": "SNEAKY_SECRET",
                "value": "hidden",
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestSpecialSecretImport:
    """POST /api/projects/{id}/secrets/import."""

    def test_import_jwt_token_file(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/import",
            data={"secret_name": "jwt_token"},
            files={"file": ("license.jwt", b"header.payload.signature", "text/plain")},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["secret"]["name"] == "jwt_token"

    def test_import_cne_pull_secret_json_file(self, client, admin_headers, sample_user, sample_project):
        docker_cfg = b'{"auths":{"repo.f5.com":{"auth":"dGVzdDo="}}}'
        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/import",
            data={"secret_name": "cne_pull_secret"},
            files={"file": ("cne_pull.json", docker_cfg, "application/json")},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["secret"]["name"] == "cne_pull_secret"

    def test_import_invalid_jwt_file_rejected(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/import",
            data={"secret_name": "jwt_token"},
            files={"file": ("invalid.jwt", b"not-a-jwt", "text/plain")},
            headers=admin_headers,
        )
        assert response.status_code == 400

    def test_import_invalid_cne_file_rejected(self, client, admin_headers, sample_user, sample_project):
        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/import",
            data={"secret_name": "cne_pull_secret"},
            files={"file": ("invalid.json", b"{}", "application/json")},
            headers=admin_headers,
        )
        assert response.status_code == 400

    def test_import_cne_pull_secret_tgz_with_multiple_files(self, client, admin_headers, sample_user, sample_project):
        buffer = BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            invalid_payload = b'{"foo":"bar"}'
            invalid_info = tarfile.TarInfo(name="readme.json")
            invalid_info.size = len(invalid_payload)
            tar.addfile(invalid_info, BytesIO(invalid_payload))

            valid_payload = b'{"auths":{"repo.f5.com":{"auth":"dGVzdDo="}}}'
            valid_info = tarfile.TarInfo(name="cne_pull_64.json")
            valid_info.size = len(valid_payload)
            tar.addfile(valid_info, BytesIO(valid_payload))

        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/import",
            data={"secret_name": "cne_pull_secret"},
            files={"file": ("f5-far-auth-key.tgz", buffer.getvalue(), "application/gzip")},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["secret"]["name"] == "cne_pull_secret"

    def test_import_cne_pull_secret_gcp_service_account_key_b64(self, client, admin_headers, sample_user, sample_project):
        """The real FAR file: base64-encoded GCP service account JSON (not docker config)."""
        import base64 as b64mod
        sa_key = json.dumps({
            "type": "service_account",
            "project_id": "f5-gcs-test",
            "private_key_id": "abc123",
            "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
            "client_email": "test@f5-gcs-test.iam.gserviceaccount.com",
            "client_id": "123",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/test",
        })
        sa_b64 = b64mod.b64encode(sa_key.encode()).decode()

        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/import",
            data={"secret_name": "cne_pull_secret"},
            files={"file": ("cne_pull_64.json", sa_b64.encode(), "application/json")},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["secret"]["name"] == "cne_pull_secret"

    def test_import_cne_pull_secret_gcp_sa_key_in_tgz(self, client, admin_headers, sample_user, sample_project):
        """The real FAR tgz: contains a file with base64-encoded GCP service account key."""
        import base64 as b64mod
        sa_key = json.dumps({
            "type": "service_account",
            "project_id": "f5-gcs-test",
            "private_key_id": "abc123",
            "private_key": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
            "client_email": "test@f5-gcs-test.iam.gserviceaccount.com",
            "client_id": "123",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/test",
        })
        sa_b64 = b64mod.b64encode(sa_key.encode()).decode()

        buffer = BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            payload_bytes = sa_b64.encode()
            info = tarfile.TarInfo(name="cne_pull_64.json")
            info.size = len(payload_bytes)
            tar.addfile(info, BytesIO(payload_bytes))

        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/import",
            data={"secret_name": "cne_pull_secret"},
            files={"file": ("f5-far-auth-key.tgz", buffer.getvalue(), "application/gzip")},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["secret"]["name"] == "cne_pull_secret"

    def test_import_cne_pull_secret_tgz_with_nested_wrapped_payload(self, client, admin_headers, sample_user, sample_project):
        b64_payload = "eyJhdXRocyI6eyJyZXBvLmY1LmNvbSI6eyJhdXRoIjoiZEdWemREbz0ifX19"
        wrapped_json = (
            '{"metadata":{"name":"far"},"secrets":{"cne_pull_64.json":"' + b64_payload + '"}}'
        ).encode("utf-8")

        buffer = BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            wrapped_info = tarfile.TarInfo(name="bundle.json")
            wrapped_info.size = len(wrapped_json)
            tar.addfile(wrapped_info, BytesIO(wrapped_json))

        response = client.post(
            f"/api/projects/{sample_project.id}/secrets/import",
            data={"secret_name": "cne_pull_secret"},
            files={"file": ("f5-far-auth-key.tgz", buffer.getvalue(), "application/gzip")},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["success"] is True
        assert payload["secret"]["name"] == "cne_pull_secret"


class TestSecretList:
    """GET /api/projects/{id}/secrets."""

    def test_list_secrets(self, client, admin_headers, sample_user, sample_project):
        """List secrets returns result (empty for fresh project)."""
        response = client.get(
            f"/api/projects/{sample_project.id}/secrets",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        # May be a list or dict with 'secrets' key
        secrets = data.get("secrets", data) if isinstance(data, dict) else data
        assert isinstance(secrets, list)

    def test_list_secrets_no_values_exposed(self, client, admin_headers, sample_user, sample_project):
        """Create a secret then list — value should NOT be in response."""
        # Create
        client.post(
            f"/api/projects/{sample_project.id}/secrets/value",
            json={"name": "MY_SECRET", "value": "supersecret123"},
            headers=admin_headers,
        )
        # List
        response = client.get(
            f"/api/projects/{sample_project.id}/secrets",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        secrets = data.get("secrets", data) if isinstance(data, dict) else data
        for secret in secrets:
            # Value should not be returned in list
            if isinstance(secret, dict):
                assert secret.get("value") != "supersecret123"


class TestSecretDelete:
    """DELETE /api/projects/{id}/secrets/{secret_id}."""

    def test_delete_secret(self, client, admin_headers, sample_user, sample_project, db):
        """Create then delete a secret."""
        # Create
        create_resp = client.post(
            f"/api/projects/{sample_project.id}/secrets/value",
            json={"name": "TEMP_SECRET", "value": "tempvalue"},
            headers=admin_headers,
        )
        assert create_resp.status_code == 200
        secret_id = create_resp.json().get("id") or create_resp.json().get("secret_id")

        if secret_id:
            # Delete
            response = client.delete(
                f"/api/projects/{sample_project.id}/secrets/{secret_id}",
                headers=admin_headers,
            )
            assert response.status_code == 200


class TestRequiredSecretsForStack:
    """GET /api/projects/{id}/secrets/required?stack_slug=..."""

    def test_stack_required_secrets_include_template_prereqs_even_if_module_marks_optional(
        self,
        client,
        admin_headers,
        sample_user,
        sample_project,
        db,
    ):
        flo = db.query(ModuleLibrary).filter(ModuleLibrary.path == "bnk/flo").first()
        if flo is None:
            flo = ModuleLibrary(
                name="FLO",
                category="bnk",
                path="bnk/flo",
                provider="bnk-forge",
                description="FLO module",
                git_source="https://example.invalid/modules.git",
                is_official=True,
                is_active=True,
            )
            db.add(flo)
            db.flush()
        flo.inputs_metadata = {
            "required": [],
            "optional": [
                {"name": "jwt_token", "description": "License JWT", "sensitive": True, "validation": {}},
            ],
        }

        prereq = db.query(ModuleLibrary).filter(ModuleLibrary.path == "k8s/bnk-prerequisites").first()
        if prereq is None:
            prereq = ModuleLibrary(
                name="BNK Prerequisites",
                category="kubernetes",
                path="k8s/bnk-prerequisites",
                provider="bnk-forge",
                description="BNK prereqs module",
                git_source="https://example.invalid/modules.git",
                is_official=True,
                is_active=True,
            )
            db.add(prereq)
            db.flush()
        prereq.inputs_metadata = {
            "required": [
                {"name": "cne_pull_secret", "description": "FAR pull secret", "sensitive": True, "validation": {}},
            ],
            "optional": [],
        }

        template = db.query(StackTemplate).filter(StackTemplate.slug == "bnk-on-k8s").first()
        if template is None:
            template = StackTemplate(
                name="BNK on K8s",
                slug="bnk-on-k8s",
                description="Existing-cluster blueprint",
                category="bnk",
                cloud_provider="any",
                modules=[],
                is_public=True,
                is_active=True,
            )
            db.add(template)
            db.flush()

        template.modules = [
            {"path": "bnk/flo", "name": "FLO"},
            {"path": "k8s/bnk-prerequisites", "name": "BNK Prerequisites"},
        ]
        template.prerequisites = [
            {"type": "project_secret", "name": "jwt_token", "description": "License JWT"},
            {"type": "project_secret", "name": "cne_pull_secret", "description": "FAR pull secret"},
        ]
        template.is_active = True
        db.commit()

        response = client.get(
            f"/api/projects/{sample_project.id}/secrets/required?stack_slug=bnk-on-k8s",
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text

        payload = response.json()
        required = {entry["variable_name"]: entry for entry in payload["required_secrets"]}

        assert "jwt_token" in required
        assert "cne_pull_secret" in required
        assert required["jwt_token"]["required"] is True
        assert required["cne_pull_secret"]["required"] is True
        assert required["jwt_token"]["exists"] is False
        assert required["cne_pull_secret"]["exists"] is False
        assert payload["all_satisfied"] is False
        assert payload["missing_count"] == 2

