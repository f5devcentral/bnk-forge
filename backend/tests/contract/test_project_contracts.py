"""
Golden contract tests — Projects domain.

Validates that project endpoints return responses matching their declared
Pydantic response_model schemas.

Endpoints tested:
- GET /api/projects            → ProjectListResponse
- GET /api/projects/{id}       → ProjectDetailResponse
"""

from schemas.projects import ProjectDetailResponse, ProjectListResponse

# ------------------------------------------------------------------ #
# GET /api/projects → ProjectListResponse
# ------------------------------------------------------------------ #


class TestProjectListContract:
    """GET /api/projects returns shape matching ProjectListResponse."""

    def test_project_list_response_shape(
        self, client, admin_headers, sample_user, sample_project
    ):
        """L1+L2: Response parses through ProjectListResponse, required fields present."""
        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable through Pydantic schema
        parsed = ProjectListResponse.model_validate(data)

        # L2: Required fields
        assert isinstance(parsed.projects, list)
        assert len(parsed.projects) >= 1
        assert isinstance(parsed.total, int)
        assert parsed.total == len(parsed.projects)

        # Each project has required fields
        for project in parsed.projects:
            assert isinstance(project.id, int)
            assert isinstance(project.name, str)
            assert project.name != ""
            assert project.target_platform_profile in {
                None,
                "generic_onprem",
                "eks",
                "aks",
                "gke",
                "ocp",
                "unknown",
            }

    def test_project_list_empty(self, client, admin_headers, sample_user):
        """ProjectListResponse works with zero projects."""
        response = client.get("/api/projects", headers=admin_headers)
        assert response.status_code == 200

        data = response.json()
        parsed = ProjectListResponse.model_validate(data)
        assert isinstance(parsed.projects, list)
        assert parsed.total == len(parsed.projects)


# ------------------------------------------------------------------ #
# GET /api/projects/{id} → ProjectDetailResponse
# ------------------------------------------------------------------ #


class TestProjectDetailContract:
    """GET /api/projects/{id} returns shape matching ProjectDetailResponse."""

    def test_project_detail_response_shape(
        self, client, admin_headers, sample_user, sample_project
    ):
        """L1+L2: Response parses through ProjectDetailResponse, required fields present."""
        response = client.get(
            f"/api/projects/{sample_project.id}", headers=admin_headers
        )
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable
        parsed = ProjectDetailResponse.model_validate(data)

        # L2: Required fields
        assert parsed.id == sample_project.id
        assert isinstance(parsed.name, str)
        assert parsed.name == "Test Project"
        assert parsed.target_platform_profile == "generic_onprem"
