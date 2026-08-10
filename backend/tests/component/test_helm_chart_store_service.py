"""
BC-C52: Component tests for HelmChartStoreMixin.

Tests upload, list, get, delete, values extraction, and clone operations.
All filesystem, subprocess, and DB operations are mocked.
"""

import hashlib
import io
import os
import tarfile
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from models import HelmChart
from services.helm_chart_store_service import HelmChartStoreMixin

# ── Helpers ──────────────────────────────────────────────────────────

def _make_chart_tgz(chart_name="mychart", version="1.0.0", include_values=True):
    """Build a valid in-memory .tgz Helm chart with Chart.yaml."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        # Chart.yaml
        chart_yaml = yaml.dump({
            "name": chart_name,
            "version": version,
            "appVersion": "0.1.0",
            "description": "A test chart",
        }).encode()
        info = tarfile.TarInfo(name=f"{chart_name}/Chart.yaml")
        info.size = len(chart_yaml)
        tar.addfile(info, io.BytesIO(chart_yaml))

        if include_values:
            values_yaml = b"replicaCount: 1\n"
            info = tarfile.TarInfo(name=f"{chart_name}/values.yaml")
            info.size = len(values_yaml)
            tar.addfile(info, io.BytesIO(values_yaml))

    buf.seek(0)
    return buf.read()


def _make_service(db=None):
    """Create a HelmChartStoreMixin host object with db and _validate_arg."""

    class Host(HelmChartStoreMixin):
        pass

    host = Host()
    host.db = db or MagicMock()
    host._validate_arg = MagicMock()
    return host


def _make_upload_file(content, filename="mychart-1.0.0.tgz"):
    upload = MagicMock()
    upload.filename = filename
    upload.read = AsyncMock(return_value=content)
    return upload


# ── Upload Chart ─────────────────────────────────────────────────────

class TestUploadChart:
    @pytest.mark.asyncio
    async def test_upload_chart_success(self, tmp_path):
        content = _make_chart_tgz()
        svc = _make_service()
        svc.db.query.return_value.filter.return_value.first.return_value = None  # no existing

        # Mock the HelmChart created by flush/refresh
        svc.db.refresh = MagicMock()
        def _mock_add(obj):
            obj.id = 1
            obj.created_at = MagicMock()
            obj.created_at.isoformat.return_value = "2024-01-01T00:00:00"
        svc.db.add.side_effect = _mock_add

        file = _make_upload_file(content)

        # Redirect the upload dir to a real temp directory
        with patch("services.helm_chart_store_service.os.makedirs"), \
             patch("services.helm_chart_store_service.os.path.join",
                   return_value=str(tmp_path / "mychart-1.0.0.tgz")):
            result = await svc.upload_chart(file, uploaded_by="admin")

        assert result["success"] is True
        assert result["chart"]["name"] == "mychart"
        assert result["chart"]["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_upload_chart_invalid_extension(self):
        svc = _make_service()
        file = _make_upload_file(b"data", filename="chart.zip")

        with pytest.raises(ValueError, match="must be a .tgz"):
            await svc.upload_chart(file)

    @pytest.mark.asyncio
    async def test_upload_chart_duplicate(self):
        content = _make_chart_tgz()
        svc = _make_service()
        svc.db.query.return_value.filter.return_value.first.return_value = MagicMock()  # existing

        file = _make_upload_file(content)

        with patch("os.makedirs"), \
             pytest.raises(ValueError, match="already exists"):
            await svc.upload_chart(file)

    @pytest.mark.asyncio
    async def test_upload_chart_no_chart_yaml(self):
        # Build tgz without Chart.yaml
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"hello"
            info = tarfile.TarInfo(name="random.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        buf.seek(0)
        content = buf.read()

        svc = _make_service()
        file = _make_upload_file(content)

        with patch("os.makedirs"), \
             pytest.raises(ValueError, match="Invalid Helm chart"):
            await svc.upload_chart(file)


# ── List Charts ──────────────────────────────────────────────────────

class TestListCharts:
    def test_list_all_charts(self):
        chart = MagicMock()
        chart.id = 1
        chart.name = "nginx"
        chart.version = "1.0"
        chart.app_version = "1.0"
        chart.description = "Web server"
        chart.file_size = 1000
        chart.source_type = "uploaded"
        chart.source_chart = None
        chart.source_repository = None
        chart.uploaded_by = "admin"
        chart.created_at = MagicMock()
        chart.created_at.isoformat.return_value = "2024-01-01T00:00:00"

        svc = _make_service()
        svc.db.query.return_value.order_by.return_value.all.return_value = [chart]

        result = svc.list_uploaded_charts()
        assert len(result) == 1
        assert result[0]["name"] == "nginx"

    def test_list_filtered_by_source(self):
        svc = _make_service()
        svc.db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = svc.list_uploaded_charts(source_type="cloned")
        assert result == []


# ── Get / Delete ─────────────────────────────────────────────────────

class TestGetDeleteChart:
    def test_get_chart(self):
        chart = MagicMock(spec=HelmChart)
        chart.id = 1
        svc = _make_service()
        svc.db.query.return_value.filter.return_value.first.return_value = chart

        result = svc.get_uploaded_chart(1)
        assert result.id == 1

    def test_delete_chart_success(self):
        chart = MagicMock()
        chart.name = "nginx"
        chart.version = "1.0"
        chart.file_path = "/tmp/nginx-1.0.tgz"

        svc = _make_service()
        svc.db.query.return_value.filter.return_value.first.return_value = chart

        with patch("os.path.exists", return_value=True), patch("os.unlink"):
            result = svc.delete_uploaded_chart(1)

        assert result["success"] is True
        svc.db.delete.assert_called_once()

    def test_delete_chart_not_found(self):
        svc = _make_service()
        svc.db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="not found"):
            svc.delete_uploaded_chart(999)


# ── Get Chart Values ─────────────────────────────────────────────────

class TestGetChartValues:
    def test_get_values_success(self):
        content = _make_chart_tgz()
        # Write to temp file for tarfile to read
        with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as f:
            f.write(content)
            tmp_path = f.name

        try:
            chart = MagicMock()
            chart.name = "mychart"
            chart.version = "1.0.0"
            chart.file_path = tmp_path

            svc = _make_service()
            svc.db.query.return_value.filter.return_value.first.return_value = chart

            result = svc.get_chart_values(1)
            assert result["success"] is True
            assert "replicaCount" in result["values"]
        finally:
            os.unlink(tmp_path)

    def test_get_values_not_found(self):
        svc = _make_service()
        svc.db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(ValueError, match="not found"):
            svc.get_chart_values(999)


# ── Clone Chart ──────────────────────────────────────────────────────

class TestCloneChart:
    @pytest.mark.asyncio
    async def test_clone_chart_success(self):
        content = _make_chart_tgz(chart_name="nginx", version="2.0.0")
        # Write tgz to temp
        with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False, dir=tempfile.gettempdir()) as f:
            f.write(content)
            downloaded = f.name

        try:
            svc = _make_service()

            mock_result = MagicMock()
            mock_result.returncode = 0

            with patch("subprocess.run", return_value=mock_result), \
                 patch("os.makedirs"), \
                 patch("os.listdir", return_value=[os.path.basename(downloaded)]), \
                 patch("os.path.join", side_effect=lambda *a: downloaded), \
                 patch("os.path.getmtime", return_value=1000), \
                 patch("os.path.getsize", return_value=5000):
                result = await svc.clone_chart("bitnami/nginx", version="2.0.0")

            assert result["success"] is True
            assert result["chart"]["source_type"] == "cloned"
        finally:
            if os.path.exists(downloaded):
                os.unlink(downloaded)

    @pytest.mark.asyncio
    async def test_clone_chart_subprocess_failure(self):
        svc = _make_service()

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "no such chart"

        with patch("subprocess.run", return_value=mock_result), \
             patch("os.makedirs"), \
             pytest.raises(RuntimeError, match="Failed to clone chart"):
            await svc.clone_chart("nonexistent/chart")
