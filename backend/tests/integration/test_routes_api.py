"""
Integration tests for general API routes — /, /health, /api/health, /api/logs, /api/version.

Covers: root endpoint, health checks, log listing, RBAC enforcement.
Uses FastAPI TestClient with real SQLite DB.
"""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestRootEndpoint:
    """GET /."""

    def test_root_returns_api_info(self, client, admin_headers, sample_user):
        """Root returns API message and version."""
        response = client.get("/", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "BNK-Forge" in data["message"]
        assert "version" in data

    def test_root_unauthenticated(self, client):
        """Root without auth returns 401."""
        response = client.get("/")
        assert response.status_code == 401


class TestHealthCheck:
    """GET /health and GET /api/health."""

    # The /health endpoint probes redis via redis.from_url().ping() — stub it
    # so the test container (no redis reachable) doesn't report degraded.
    _redis_ok = patch("redis.from_url", return_value=MagicMock(ping=lambda: True))

    def test_health_check(self, client, admin_headers, sample_user):
        """Health check returns healthy status."""
        with self._redis_ok:
            response = client.get("/health", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data

    def test_api_health_check(self, client, admin_headers, sample_user):
        """API health check at /api/health returns healthy."""
        with self._redis_ok:
            response = client.get("/api/health", headers=admin_headers)
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_health_unauthenticated(self, client):
        """Health check without auth returns 401."""
        response = client.get("/health")
        assert response.status_code == 401

