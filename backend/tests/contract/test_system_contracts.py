"""
Golden contract tests — System domain.

Validates that system endpoints return responses matching their declared
Pydantic response_model schemas.

Endpoints tested:
- GET /api/system/health → SystemHealthResponse
"""

from schemas.system import ServiceHealth, SystemHealthResponse

# ------------------------------------------------------------------ #
# GET /api/system/health → SystemHealthResponse
# ------------------------------------------------------------------ #


class TestSystemHealthContract:
    """GET /api/system/health returns shape matching SystemHealthResponse."""

    def test_health_response_shape(self, client, mock_system_health):
        """L1+L2: Response parses through SystemHealthResponse, required fields present.

        Note: /api/system/health is on public_router — no auth required.
        """
        response = client.get("/api/system/health")
        assert response.status_code == 200

        data = response.json()

        # L1: Parseable through Pydantic schema
        parsed = SystemHealthResponse.model_validate(data)

        # L2: Required fields
        assert isinstance(parsed.services, dict)
        assert isinstance(parsed.timestamp, str)
        assert len(parsed.services) > 0

        # Each service entry is a valid ServiceHealth
        for name, service in parsed.services.items():
            assert isinstance(name, str)
            svc = ServiceHealth.model_validate(service.model_dump() if hasattr(service, "model_dump") else service)
            assert isinstance(svc.status, str)

    def test_health_service_vocabulary(self, client, mock_system_health):
        """L3: Service status uses known health vocabulary."""
        response = client.get("/api/system/health")
        data = response.json()
        parsed = SystemHealthResponse.model_validate(data)

        valid_statuses = {"healthy", "degraded", "offline", "unknown"}
        for name, service in parsed.services.items():
            assert service.status in valid_statuses, (
                f"Unexpected service status for '{name}': {service.status}"
            )

    def test_health_offline_service_has_error(self, client, mock_system_health):
        """Offline services should include an error message."""
        response = client.get("/api/system/health")
        data = response.json()
        parsed = SystemHealthResponse.model_validate(data)

        for name, service in parsed.services.items():
            if service.status == "offline":
                assert service.error is not None, (
                    f"Offline service '{name}' should have error field"
                )
