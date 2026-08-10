"""
Golden contract tests — module actions (D-034).

Validates that the module-action endpoints return responses matching their
declared Pydantic response_model schemas.

Endpoints tested:
- GET  /api/project-modules/{id}/actions                 → ModuleActionsListResponse
- POST /api/project-modules/{id}/actions/{action_name}   → ModuleActionSubmitResponse
"""

from unittest.mock import MagicMock, patch

import pytest

from schemas.projects import ModuleActionsListResponse, ModuleActionSubmitResponse

ACTION_ARTIFACT_MANIFEST = {
    "schema_version": 1,
    "name": "ocibnkctl-runner",
    "version": "1.0.0",
    "kind": "container_image",
    "container_image": {
        "registry_host": "ghcr.io",
        "repository": "jgruberf5/ocibnkctl-runner",
        "digest": "sha256:" + "a" * 64,
    },
    "steps": {"apply": [{"name": "up", "args": ["ocibnkctl", "e2e"]}]},
    "actions": {
        "run-scenario": {
            "title": "Run a functional scenario",
            "description": "Runs one scenario by name",
            "rating": "amber",
            "steps": [{"name": "run", "args": ["ocibnkctl", "scenario", "run", "{{inputs.scenario}}"]}],
            "inputs": [{"name": "scenario", "type": "string", "source": "user"}],
        },
    },
}


@pytest.fixture()
def action_module(db, make_project, make_module_library, make_project_module):
    """An applied container module whose manifest declares an action."""
    project = make_project(name="Actions Contract Project")
    lib = make_module_library(
        name="ocibnkctl-runner",
        category="bnk",
        execution_engine="container",
        pack_manifest=ACTION_ARTIFACT_MANIFEST,
    )
    module = make_project_module(project=project, library_module=lib, status="applied")
    db.commit()
    return module


# ------------------------------------------------------------------ #
# GET /api/project-modules/{id}/actions → ModuleActionsListResponse
# ------------------------------------------------------------------ #


class TestModuleActionsListContract:
    """GET /api/project-modules/{id}/actions returns ModuleActionsListResponse shape."""

    def test_actions_list_response_shape(self, client, admin_headers, sample_user, action_module):
        """L1+L2: Response parses through the schema, declared action surfaced."""
        response = client.get(
            f"/api/project-modules/{action_module.id}/actions", headers=admin_headers
        )
        assert response.status_code == 200

        parsed = ModuleActionsListResponse.model_validate(response.json())
        assert parsed.module_id == action_module.id
        assert parsed.total == len(parsed.actions) == 1

        action = parsed.actions[0]
        assert action.name == "run-scenario"
        assert action.title == "Run a functional scenario"
        assert action.rating in {"green", "amber"}
        assert action.inputs[0].name == "scenario"
        assert action.inputs[0].type == "string"

    def test_actions_list_empty_for_module_without_actions(
        self, client, admin_headers, sample_user, db, make_project, make_project_module
    ):
        """A module with no actions block returns an empty, valid shape."""
        project = make_project(name="No Actions Project")
        module = make_project_module(project=project)
        db.commit()

        response = client.get(
            f"/api/project-modules/{module.id}/actions", headers=admin_headers
        )
        assert response.status_code == 200

        parsed = ModuleActionsListResponse.model_validate(response.json())
        assert parsed.actions == []
        assert parsed.total == 0


# ------------------------------------------------------------------ #
# POST /api/project-modules/{id}/actions/{name} → ModuleActionSubmitResponse
# ------------------------------------------------------------------ #


class TestModuleActionSubmitContract:
    """POST /api/project-modules/{id}/actions/{action_name} returns ModuleActionSubmitResponse shape."""

    def test_action_submit_response_shape(self, client, admin_headers, sample_user, action_module):
        """L1+L2: Response parses through the schema with the queued task ids."""
        with patch("services.execution.task_dispatch.dispatch_container_action") as mock_dispatch:
            mock_dispatch.return_value = MagicMock(id="celery-action-1")
            response = client.post(
                f"/api/project-modules/{action_module.id}/actions/run-scenario",
                headers=admin_headers,
                json={"inputs": {"scenario": "tcpl4lb"}},
            )

        assert response.status_code == 200

        parsed = ModuleActionSubmitResponse.model_validate(response.json())
        assert parsed.success is True
        assert parsed.action == "run-scenario"
        assert isinstance(parsed.task_id, int)
        assert parsed.celery_task_id == "celery-action-1"
        assert parsed.status == "queued"

    def test_action_submit_unknown_action_is_client_error(
        self, client, admin_headers, sample_user, action_module
    ):
        response = client.post(
            f"/api/project-modules/{action_module.id}/actions/does-not-exist",
            headers=admin_headers,
            json={},
        )
        assert response.status_code == 400
