"""
CT-033: Negative schema tests for Helm request schemas.

Tests that InstallChartRequest, UpgradeReleaseRequest, RollbackReleaseRequest,
and AddRepositoryRequest reject wrong payload shapes with ValidationError.
"""

import pytest
from pydantic import ValidationError

from routes.helm import (
    AddRepositoryRequest,
    InstallChartRequest,
    RollbackReleaseRequest,
    UpgradeReleaseRequest,
)


class TestInstallChartRequestNegative:
    def test_valid_install(self):
        req = InstallChartRequest(release_name="nginx", chart="bitnami/nginx")
        assert req.release_name == "nginx"
        assert req.namespace == "default"
        assert req.create_namespace is True
        assert req.wait is True
        assert req.timeout == "5m"

    def test_missing_release_name_rejected(self):
        with pytest.raises(ValidationError):
            InstallChartRequest(chart="bitnami/nginx")  # type: ignore[call-arg]

    def test_missing_chart_rejected(self):
        with pytest.raises(ValidationError):
            InstallChartRequest(release_name="nginx")  # type: ignore[call-arg]

    def test_missing_both_required_rejected(self):
        with pytest.raises(ValidationError):
            InstallChartRequest()  # type: ignore[call-arg]

    def test_release_name_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            InstallChartRequest(release_name=123, chart="bitnami/nginx")  # type: ignore[arg-type]

    def test_chart_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            InstallChartRequest(release_name="nginx", chart=123)  # type: ignore[arg-type]

    def test_values_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            InstallChartRequest(
                release_name="nginx", chart="bitnami/nginx", values="not-a-dict"
            )  # type: ignore[arg-type]

    def test_invalid_timeout_rejected(self):
        """Helm timeout validator rejects garbage strings."""
        with pytest.raises(ValidationError):
            InstallChartRequest(
                release_name="nginx", chart="bitnami/nginx", timeout="not-a-timeout"
            )

    def test_timeout_shell_injection_rejected(self):
        """Timeout validator should reject shell injection attempts."""
        with pytest.raises(ValidationError):
            InstallChartRequest(
                release_name="nginx", chart="bitnami/nginx", timeout="--set=evil"
            )

    def test_valid_timeout_formats(self):
        """Valid timeout formats should be accepted."""
        for timeout in ["5m", "300s", "1h"]:
            req = InstallChartRequest(
                release_name="nginx", chart="bitnami/nginx", timeout=timeout
            )
            assert req.timeout == timeout


class TestUpgradeReleaseRequestNegative:
    def test_valid_upgrade(self):
        req = UpgradeReleaseRequest(chart="bitnami/nginx", values={"replicas": 3})
        assert req.chart == "bitnami/nginx"
        assert req.install is True
        assert req.timeout == "5m"

    def test_all_optional(self):
        """Upgrade request with all defaults is valid."""
        req = UpgradeReleaseRequest()
        assert req.chart is None
        assert req.values is None

    def test_values_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            UpgradeReleaseRequest(values="not-a-dict")  # type: ignore[arg-type]

    def test_invalid_timeout_rejected(self):
        with pytest.raises(ValidationError):
            UpgradeReleaseRequest(timeout="abc")

    def test_timeout_empty_string_rejected(self):
        with pytest.raises(ValidationError):
            UpgradeReleaseRequest(timeout="")

    def test_chart_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            UpgradeReleaseRequest(chart=123)  # type: ignore[arg-type]


class TestRollbackReleaseRequestNegative:
    def test_valid_rollback(self):
        req = RollbackReleaseRequest(revision=2)
        assert req.revision == 2
        assert req.wait is True
        assert req.timeout == "5m"

    def test_all_defaults_valid(self):
        req = RollbackReleaseRequest()
        assert req.revision is None

    def test_revision_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            RollbackReleaseRequest(revision="two")  # type: ignore[arg-type]

    def test_invalid_timeout_rejected(self):
        with pytest.raises(ValidationError):
            RollbackReleaseRequest(timeout="bad")

    def test_timeout_injection_rejected(self):
        with pytest.raises(ValidationError):
            RollbackReleaseRequest(timeout="; rm -rf /")


class TestAddRepositoryRequestNegative:
    def test_valid_repo(self):
        req = AddRepositoryRequest(name="bitnami", url="https://charts.bitnami.com/bitnami")
        assert req.name == "bitnami"
        assert req.username is None
        assert req.password is None

    def test_missing_name_rejected(self):
        with pytest.raises(ValidationError):
            AddRepositoryRequest(url="https://example.com")  # type: ignore[call-arg]

    def test_missing_url_rejected(self):
        with pytest.raises(ValidationError):
            AddRepositoryRequest(name="test")  # type: ignore[call-arg]

    def test_missing_both_rejected(self):
        with pytest.raises(ValidationError):
            AddRepositoryRequest()  # type: ignore[call-arg]

    def test_name_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            AddRepositoryRequest(name=123, url="https://example.com")  # type: ignore[arg-type]

    def test_url_wrong_type_rejected(self):
        with pytest.raises(ValidationError):
            AddRepositoryRequest(name="test", url=123)  # type: ignore[arg-type]

    def test_with_auth_valid(self):
        req = AddRepositoryRequest(
            name="private", url="https://charts.example.com",
            username="user", password="pass",
        )
        assert req.username == "user"
        assert req.password == "pass"
