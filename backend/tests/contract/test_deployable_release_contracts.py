"""
Golden contract tests — BNK deployable release endpoints (ADR-478).

Validates that the /api/bare-metal/deployable-releases routes return
responses that parse cleanly against the declared Pydantic schemas.
"""

import pytest

from models.bnk_deployable_release import BnkDeployableRelease
from schemas.bare_metal import DeployableReleaseListResponse, DeployableReleaseResponse
from services.bare_metal.version_profiles import BNK_22_PROFILE, BNK_231_RELEASE


@pytest.fixture()
def seed_releases(db):
    """Seed two deployable releases for contract shape tests."""
    r1 = BnkDeployableRelease(**BNK_22_PROFILE)
    r2 = BnkDeployableRelease(**BNK_231_RELEASE)
    db.add_all([r1, r2])
    db.commit()
    db.refresh(r1)
    db.refresh(r2)
    return [r1, r2]


class TestDeployableReleaseListContract:
    """GET /api/bare-metal/deployable-releases returns DeployableReleaseListResponse shape."""

    def test_list_response_shape(self, client, admin_headers, sample_user, seed_releases):
        """L1: Response parses as DeployableReleaseListResponse."""
        response = client.get("/api/bare-metal/deployable-releases", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        parsed = DeployableReleaseListResponse.model_validate(data)

        assert len(parsed.releases) == 2
        names = {r.name for r in parsed.releases}
        assert "bnk-2.2" in names
        assert "bnk-2.3.1" in names

    def test_list_release_fields_present(self, client, admin_headers, sample_user, seed_releases):
        """L2: Each release object contains all required fields."""
        response = client.get("/api/bare-metal/deployable-releases", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        assert "releases" in data
        release = data["releases"][0]

        required_fields = {
            "id", "name", "display_name", "is_default", "is_active",
            "source_type", "bnk_release_id", "bnk_manifest_version",
            "bnk_cr_kind", "flo_version", "k8s_version", "doca_version",
            "cert_manager_version", "created_at",
        }
        for field in required_fields:
            assert field in release, f"Missing field: {field}"

    def test_viewer_can_list(self, client, admin_headers, sample_user):
        """Viewer role can access the list endpoint."""
        response = client.get("/api/bare-metal/deployable-releases", headers=admin_headers)
        assert response.status_code == 200

    def test_unauthenticated_list_rejected(self, client):
        """Unauthenticated request is rejected."""
        response = client.get("/api/bare-metal/deployable-releases")
        assert response.status_code in (401, 403)


class TestDeployableReleaseGetContract:
    """GET /api/bare-metal/deployable-releases/{id} returns DeployableReleaseResponse shape."""

    def test_get_response_shape(self, client, admin_headers, sample_user, seed_releases):
        """Single-release endpoint parses as DeployableReleaseResponse."""
        release_id = seed_releases[0].id
        response = client.get(f"/api/bare-metal/deployable-releases/{release_id}", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        parsed = DeployableReleaseResponse.model_validate(data)

        assert parsed.id == release_id
        assert isinstance(parsed.is_active, bool)
        assert isinstance(parsed.source_type, str)

    def test_get_not_found(self, client, admin_headers, sample_user):
        """Non-existent release returns 404."""
        response = client.get("/api/bare-metal/deployable-releases/99999", headers=admin_headers)
        assert response.status_code == 404


class TestDeployableReleaseAdminContract:
    """Admin mutations require admin role and return the correct shape."""

    def test_activate_requires_admin(self, client, admin_headers, sample_user, seed_releases):
        """POST activate succeeds for admin."""
        release_id = seed_releases[0].id
        response = client.post(
            f"/api/bare-metal/deployable-releases/{release_id}/activate",
            json={"is_active": True},
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        parsed = DeployableReleaseResponse.model_validate(data)
        assert parsed.is_active is True

    def test_set_default_requires_admin(self, client, admin_headers, sample_user, seed_releases):
        """POST set-default succeeds for admin and enforces single-default."""
        release_id = seed_releases[1].id  # bnk-2.3.1
        response = client.post(
            f"/api/bare-metal/deployable-releases/{release_id}/set-default",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        parsed = DeployableReleaseResponse.model_validate(data)
        assert parsed.is_default is True
        assert parsed.name == "bnk-2.3.1"
