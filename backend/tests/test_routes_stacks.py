"""
Integration tests for stack template routes.

Tests:
  10. test_list_stacks — GET /api/stacks/templates returns stack templates
  11. test_get_stack_detail — GET /api/stacks/templates/{slug} returns specific stack
"""

from datetime import UTC, datetime, timezone
from unittest.mock import patch

import pytest

from models import StackTemplate


class TestStackRoutes:
    """Route-level integration tests for /api/stacks."""

    @pytest.fixture()
    def sample_stack(self, db):
        """Create a stack template in the test DB."""
        stack = StackTemplate(
            name="Full BNK Stack",
            slug="full-bnk",
            description="Complete BIG-IP Next for Kubernetes deployment",
            category="bnk",
            cloud_provider="aws",
            icon="shield",
            color="#0078d4",
            estimated_time="30 min",
            estimated_cost="$450/mo",
            difficulty="intermediate",
            modules=[
                {"module": "vpc", "order": 1},
                {"module": "eks", "order": 2},
                {"module": "bnk", "order": 3},
            ],
            tags=["bnk", "aws", "production"],
            is_active=True,
            is_featured=True,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(stack)
        db.commit()
        db.refresh(stack)
        return stack

    def test_list_stacks(self, client, admin_headers, sample_user, sample_stack):
        """GET /api/stacks/templates returns a list of stack templates."""
        # Patch StackService to use our test DB data directly
        with patch("routes.stacks.StackService") as MockService:
            MockService.return_value.list_templates.return_value = [
                {
                    "id": sample_stack.id,
                    "name": "Full BNK Stack",
                    "slug": "full-bnk",
                    "description": "Complete BIG-IP Next for Kubernetes deployment",
                    "category": "bnk",
                    "cloud_provider": "aws",
                    "icon": "shield",
                    "color": "#0078d4",
                    "estimated_time": "30 min",
                    "estimated_cost": "$450/mo",
                    "difficulty": "intermediate",
                    "tags": ["bnk", "aws", "production"],
                    "is_active": True,
                    "is_featured": True,
                    "version": "1.0.0",
                    "created_by": "system",
                    "is_public": False,
                    "forked_from": None,
                }
            ]
            response = client.get("/api/stacks/templates", headers=admin_headers)

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        slugs = [s["slug"] for s in data]
        assert "full-bnk" in slugs

    def test_get_stack_detail(self, client, admin_headers, sample_user, sample_stack):
        """GET /api/stacks/templates/{slug} returns the specific stack template."""
        with patch("routes.stacks.StackService") as MockService:
            MockService.return_value.get_template.return_value = {
                "id": sample_stack.id,
                "name": "Full BNK Stack",
                "slug": "full-bnk",
                "description": "Complete BIG-IP Next for Kubernetes deployment",
                "category": "bnk",
                "cloud_provider": "aws",
                "icon": "shield",
                "color": "#0078d4",
                "estimated_time": "30 min",
                "estimated_cost": "$450/mo",
                "difficulty": "intermediate",
                "modules": [
                    {"module": "vpc", "order": 1},
                    {"module": "eks", "order": 2},
                    {"module": "bnk", "order": 3},
                ],
                "variable_templates": None,
                "prerequisites": None,
                "tags": ["bnk", "aws", "production"],
                "is_active": True,
                "is_featured": True,
                "version": "1.0.0",
                "created_by": "system",
                "is_public": False,
                "forked_from": None,
                "created_at": "2026-02-18T00:00:00",
                "updated_at": "2026-02-18T00:00:00",
            }

            response = client.get(
                "/api/stacks/templates/full-bnk", headers=admin_headers
            )

        assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

        data = response.json()
        assert data["slug"] == "full-bnk"
        assert data["name"] == "Full BNK Stack"
        assert data["category"] == "bnk"
