"""
BU-016: Unit tests for schemas.helm module.

Tests all Helm response schemas — releases, operations, history,
values, manifest, repositories, charts. Real Pydantic validation, no mocks.
"""

from schemas.helm import (
    HelmChartInfo,
    HelmChartSearchResponse,
    HelmOperationResponse,
    HelmReleaseDetailResponse,
    HelmReleaseHistoryResponse,
    HelmReleaseInfo,
    HelmReleaseListResponse,
    HelmReleaseManifestResponse,
    HelmReleaseValuesResponse,
    HelmRepositoryInfo,
    HelmRepositoryListResponse,
    HelmRevisionInfo,
    HelmUploadedChartInfo,
    HelmUploadedChartListResponse,
)

# ── Release schemas ──────────────────────────────────────────────────


class TestHelmReleaseInfo:
    def test_minimal(self):
        r = HelmReleaseInfo(
            name="nginx-ingress",
            namespace="ingress",
            revision=1,
            status="deployed",
        )
        assert r.name == "nginx-ingress"
        assert r.chart is None

    def test_full(self):
        r = HelmReleaseInfo(
            name="prometheus",
            namespace="monitoring",
            revision=3,
            status="deployed",
            chart="prometheus",
            chart_version="25.8.0",
            app_version="2.48.0",
            updated="2026-02-24T10:00:00Z",
        )
        assert r.chart_version == "25.8.0"
        assert r.revision == 3


class TestHelmReleaseListResponse:
    def test_empty(self):
        r = HelmReleaseListResponse(releases=[])
        assert r.releases == []
        assert r.success is True

    def test_with_releases(self):
        r = HelmReleaseListResponse(
            releases=[
                HelmReleaseInfo(name="r1", namespace="ns1", revision=1, status="deployed"),
                HelmReleaseInfo(name="r2", namespace="ns2", revision=2, status="failed"),
            ],
            count=2,
        )
        assert len(r.releases) == 2
        assert r.count == 2


class TestHelmReleaseDetailResponse:
    def test_full_detail(self):
        r = HelmReleaseDetailResponse(
            success=True,
            release={
                "name": "cert-manager",
                "namespace": "cert-manager",
                "info": {"status": "deployed"},
                "chart": {"metadata": {"name": "cert-manager", "version": "1.13.3"}},
                "config": {"installCRDs": True, "replicaCount": 2},
            },
        )
        assert r.success is True
        assert r.release["config"]["installCRDs"] is True
        assert r.release["name"] == "cert-manager"


class TestHelmOperationResponse:
    def test_install_success(self):
        r = HelmOperationResponse(
            success=True,
            message="Chart bitnami/nginx installed as my-app",
            result={"name": "my-app", "info": {"status": "deployed"}},
        )
        assert r.success is True
        assert r.result["name"] == "my-app"

    def test_uninstall_success(self):
        r = HelmOperationResponse(
            success=True,
            message="Release old-app uninstalled successfully",
        )
        assert r.result is None

    def test_failure(self):
        r = HelmOperationResponse(
            success=False,
            message="Failed to install: timeout",
        )
        assert r.success is False


# ── History / Values / Manifest ──────────────────────────────────────


class TestHelmRevisionInfo:
    def test_revision(self):
        r = HelmRevisionInfo(
            revision=3,
            status="deployed",
            chart="nginx-1.0.0",
            app_version="1.25.0",
            updated="2026-02-24T10:00:00Z",
            description="Upgrade complete",
        )
        assert r.revision == 3
        assert r.description == "Upgrade complete"


class TestHelmReleaseHistoryResponse:
    def test_history(self):
        r = HelmReleaseHistoryResponse(
            success=True,
            history=[
                HelmRevisionInfo(revision=1, status="superseded"),
                HelmRevisionInfo(revision=2, status="deployed"),
            ],
            count=2,
        )
        assert len(r.history) == 2
        assert r.count == 2


class TestHelmReleaseValuesResponse:
    def test_values(self):
        r = HelmReleaseValuesResponse(
            release_name="my-app",
            values={"replicaCount": 3, "image": {"tag": "latest"}},
        )
        assert r.values["replicaCount"] == 3


class TestHelmReleaseManifestResponse:
    def test_manifest(self):
        manifest_yaml = "apiVersion: apps/v1\nkind: Deployment\n..."
        r = HelmReleaseManifestResponse(
            release_name="my-app",
            manifest=manifest_yaml,
        )
        assert "Deployment" in r.manifest


# ── Repository schemas ───────────────────────────────────────────────


class TestHelmRepositoryInfo:
    def test_repo(self):
        r = HelmRepositoryInfo(name="bitnami", url="https://charts.bitnami.com/bitnami")
        assert r.name == "bitnami"
        assert "bitnami" in r.url


class TestHelmRepositoryListResponse:
    def test_list(self):
        r = HelmRepositoryListResponse(
            repositories=[
                HelmRepositoryInfo(name="stable", url="https://charts.helm.sh/stable"),
                HelmRepositoryInfo(name="bitnami", url="https://charts.bitnami.com/bitnami"),
            ]
        )
        assert len(r.repositories) == 2


# ── Chart schemas ────────────────────────────────────────────────────


class TestHelmChartInfo:
    def test_minimal(self):
        c = HelmChartInfo(name="nginx")
        assert c.name == "nginx"
        assert c.version is None

    def test_full(self):
        c = HelmChartInfo(
            name="ingress-nginx",
            version="4.8.3",
            app_version="1.9.4",
            description="Ingress controller for Kubernetes",
            repository="ingress-nginx",
        )
        assert c.version == "4.8.3"


class TestHelmChartSearchResponse:
    def test_search(self):
        r = HelmChartSearchResponse(
            success=True,
            charts=[
                HelmChartInfo(name="nginx", version="1.0.0"),
                HelmChartInfo(name="nginx-ingress", version="4.0.0"),
            ],
            count=2,
        )
        assert len(r.charts) == 2
        assert r.count == 2

    def test_empty_search(self):
        r = HelmChartSearchResponse(charts=[])
        assert r.count is None


class TestHelmUploadedChartInfo:
    def test_uploaded(self):
        c = HelmUploadedChartInfo(
            id=1,
            filename="my-chart-1.0.0.tgz",
            chart_name="my-chart",
            chart_version="1.0.0",
            uploaded_at="2026-02-24T10:00:00Z",
        )
        assert c.filename == "my-chart-1.0.0.tgz"


class TestHelmUploadedChartListResponse:
    def test_list(self):
        r = HelmUploadedChartListResponse(
            charts=[
                HelmUploadedChartInfo(id=1, filename="a.tgz"),
                HelmUploadedChartInfo(id=2, filename="b.tgz"),
            ]
        )
        assert len(r.charts) == 2
