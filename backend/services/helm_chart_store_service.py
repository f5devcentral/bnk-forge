"""
Helm chart store mixin — uploaded and cloned chart management.

Extracted from helm_service.py (R4-012) to keep the monolith under control.
Provides upload, clone, CRUD, and values management for custom charts.
"""

import hashlib
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from typing import Any

import yaml as pyyaml
from fastapi import UploadFile

from models import HelmChart

logger = logging.getLogger(__name__)


class HelmChartStoreMixin:
    """
    Mixin providing uploaded/cloned Helm chart management.

    Expects the host class to provide:
        - self.db  (SQLAlchemy Session)
        - self._validate_arg(name, value)
    """

    async def upload_chart(
        self,
        file: UploadFile,
        uploaded_by: str | None = None
    ) -> dict[str, Any]:
        """
        Upload a custom Helm chart (.tgz file).

        Args:
            file: Uploaded .tgz file
            uploaded_by: Username of uploader

        Returns:
            Chart information dictionary
        """
        # Validate file extension
        if not file.filename.endswith('.tgz'):
            raise ValueError("File must be a .tgz Helm chart package")

        # Create upload directory if it doesn't exist
        upload_dir = "/app/helm_charts"
        os.makedirs(upload_dir, exist_ok=True)

        # Read file content
        content = await file.read()
        file_size = len(content)

        # Calculate checksum
        checksum = hashlib.sha256(content).hexdigest()

        # Extract and validate chart
        try:
            # Write to temp file for extraction
            with tempfile.NamedTemporaryFile(suffix='.tgz', delete=False) as temp_file:
                temp_file.write(content)
                temp_path = temp_file.name

            # Extract Chart.yaml to read metadata
            with tarfile.open(temp_path, 'r:gz') as tar:
                # Find Chart.yaml
                chart_yaml_path = None
                for member in tar.getmembers():
                    if member.name.endswith('Chart.yaml'):
                        chart_yaml_path = member.name
                        break

                if not chart_yaml_path:
                    raise ValueError("Invalid Helm chart: Chart.yaml not found")

                # Extract and parse Chart.yaml
                chart_yaml_file = tar.extractfile(chart_yaml_path)
                chart_metadata = pyyaml.safe_load(chart_yaml_file.read())

            # Clean up temp file
            os.unlink(temp_path)

        except Exception as e:
            logger.error(f"Failed to parse Helm chart: {e}")
            raise ValueError(f"Invalid Helm chart: {str(e)}")

        # Extract chart information
        chart_name = chart_metadata.get('name')
        chart_version = chart_metadata.get('version')
        app_version = chart_metadata.get('appVersion')
        description = chart_metadata.get('description')

        if not chart_name or not chart_version:
            raise ValueError("Invalid Helm chart: name and version required in Chart.yaml")

        # Check if chart already exists
        existing = self.db.query(HelmChart).filter(
            HelmChart.name == chart_name,
            HelmChart.version == chart_version
        ).first()

        if existing:
            raise ValueError(f"Chart {chart_name}:{chart_version} already exists")

        # Save file
        safe_filename = f"{chart_name}-{chart_version}.tgz"
        file_path = os.path.join(upload_dir, safe_filename)

        with open(file_path, 'wb') as f:
            f.write(content)

        # Save to database
        helm_chart = HelmChart(
            name=chart_name,
            version=chart_version,
            app_version=app_version,
            description=description,
            file_path=file_path,
            file_size=file_size,
            checksum=checksum,
            chart_metadata=chart_metadata,
            uploaded_by=uploaded_by
        )

        self.db.add(helm_chart)
        self.db.flush()
        self.db.refresh(helm_chart)

        return {
            "success": True,
            "chart": {
                "id": helm_chart.id,
                "name": helm_chart.name,
                "version": helm_chart.version,
                "app_version": helm_chart.app_version,
                "description": helm_chart.description,
                "file_size": helm_chart.file_size,
                "checksum": helm_chart.checksum,
                "created_at": helm_chart.created_at.isoformat() if helm_chart.created_at else None
            }
        }

    def list_uploaded_charts(self, source_type: str | None = None) -> list[dict[str, Any]]:
        """
        List uploaded or cloned custom Helm charts.

        Args:
            source_type: Filter by source type ('uploaded', 'cloned', or None for all)

        Returns:
            List of chart dictionaries
        """
        query = self.db.query(HelmChart)

        if source_type:
            query = query.filter(HelmChart.source_type == source_type)

        charts = query.order_by(HelmChart.created_at.desc()).all()

        return [
            {
                "id": chart.id,
                "name": chart.name,
                "version": chart.version,
                "app_version": chart.app_version,
                "description": chart.description,
                "file_size": chart.file_size,
                "source_type": chart.source_type,
                "source_chart": chart.source_chart,
                "source_repository": chart.source_repository,
                "uploaded_by": chart.uploaded_by,
                "created_at": chart.created_at.isoformat() if chart.created_at else None
            }
            for chart in charts
        ]

    def get_uploaded_chart(self, chart_id: int) -> HelmChart | None:
        """
        Get an uploaded chart by ID.

        Args:
            chart_id: Chart ID

        Returns:
            HelmChart object or None
        """
        return self.db.query(HelmChart).filter(HelmChart.id == chart_id).first()

    def delete_uploaded_chart(self, chart_id: int) -> dict[str, Any]:
        """
        Delete an uploaded chart.

        Args:
            chart_id: Chart ID

        Returns:
            Success message
        """
        chart = self.get_uploaded_chart(chart_id)

        if not chart:
            raise ValueError(f"Chart with ID {chart_id} not found")

        # Delete file from filesystem
        if os.path.exists(chart.file_path):
            os.unlink(chart.file_path)

        # Delete from database
        self.db.delete(chart)
        self.db.flush()

        return {
            "success": True,
            "message": f"Chart {chart.name}:{chart.version} deleted successfully"
        }

    def get_chart_values(self, chart_id: int) -> dict[str, Any]:
        """
        Extract and return values.yaml from a chart.

        Args:
            chart_id: Chart ID

        Returns:
            Dict with values content
        """
        chart = self.get_uploaded_chart(chart_id)
        if not chart:
            raise ValueError(f"Chart with ID {chart_id} not found")

        if not os.path.exists(chart.file_path):
            raise ValueError(f"Chart file not found at {chart.file_path}")

        # Extract values.yaml from the chart
        with tarfile.open(chart.file_path, 'r:gz') as tar:
            # Find values.yaml file
            values_file = None
            for member in tar.getmembers():
                if member.name.endswith('/values.yaml') or member.name == 'values.yaml':
                    values_file = member
                    break

            if not values_file:
                return {
                    "success": True,
                    "values": "# No values.yaml found in chart\n",
                    "chart_name": chart.name,
                    "chart_version": chart.version
                }

            # Extract and read the values file
            f = tar.extractfile(values_file)
            if f:
                values_content = f.read().decode('utf-8')
                return {
                    "success": True,
                    "values": values_content,
                    "chart_name": chart.name,
                    "chart_version": chart.version
                }

        raise ValueError("Failed to read values.yaml from chart")

    def update_chart_values(self, chart_id: int, new_values: str) -> dict[str, Any]:
        """
        Update the values.yaml in a chart archive.

        Args:
            chart_id: Chart ID
            new_values: New values.yaml content

        Returns:
            Success message
        """
        chart = self.get_uploaded_chart(chart_id)
        if not chart:
            raise ValueError(f"Chart with ID {chart_id} not found")

        if not os.path.exists(chart.file_path):
            raise ValueError(f"Chart file not found at {chart.file_path}")


        # Create a temporary directory to extract the chart
        with tempfile.TemporaryDirectory() as temp_dir:
            # Extract the entire chart
            with tarfile.open(chart.file_path, 'r:gz') as tar:
                # Use filter='data' to prevent Zip Slip (path traversal) attacks
                # This ensures files are extracted only within temp_dir
                tar.extractall(temp_dir, filter='data')

            # Find and update the values.yaml file
            values_updated = False
            for root, dirs, files in os.walk(temp_dir):
                if 'values.yaml' in files:
                    values_path = os.path.join(root, 'values.yaml')
                    with open(values_path, 'w') as f:
                        f.write(new_values)
                    values_updated = True
                    break

            if not values_updated:
                raise ValueError("values.yaml not found in chart")

            # Create a new tar.gz with the updated files
            backup_path = chart.file_path + '.backup'
            shutil.copy(chart.file_path, backup_path)

            try:
                # Re-pack the chart
                with tarfile.open(chart.file_path, 'w:gz') as tar:
                    for item in os.listdir(temp_dir):
                        tar.add(os.path.join(temp_dir, item), arcname=item)

                # Remove backup if successful
                os.remove(backup_path)

                return {
                    "success": True,
                    "message": f"Chart {chart.name}:{chart.version} values updated successfully"
                }
            except Exception as e:
                # Restore from backup on error
                if os.path.exists(backup_path):
                    shutil.copy(backup_path, chart.file_path)
                    os.remove(backup_path)
                raise e

    async def clone_chart(
        self,
        chart_reference: str,
        version: str | None = None,
        cloned_by: str | None = None
    ) -> dict[str, Any]:
        """
        Clone a chart from a repository by downloading it.

        Args:
            chart_reference: Chart reference (e.g., 'bitnami/nginx')
            version: Specific version to clone (optional)
            cloned_by: Username of cloner

        Returns:
            Chart information dictionary
        """
        self._validate_arg("chart_reference", chart_reference)
        self._validate_arg("version", version)

        # Parse chart reference
        if '/' in chart_reference:
            parts = chart_reference.split('/', 1)
            repository = parts[0]
            chart_name = parts[1]
        else:
            repository = None
            chart_name = chart_reference

        # Create download directory
        upload_dir = "/app/helm_charts"
        os.makedirs(upload_dir, exist_ok=True)

        # Use helm pull to download the chart
        command = ['helm', 'pull', chart_reference, '--untar=false']

        if version:
            command.extend(['--version', version])

        # Download to specific directory
        command.extend(['--destination', upload_dir])

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            raise RuntimeError(f"Failed to clone chart: {result.stderr}")

        # Find the downloaded file
        # Helm pull creates: chartname-version.tgz
        files = [f for f in os.listdir(upload_dir) if f.endswith('.tgz')]
        if not files:
            raise RuntimeError("Chart was downloaded but file not found")

        # Get the most recently modified .tgz file
        downloaded_file = max([os.path.join(upload_dir, f) for f in files], key=os.path.getmtime)

        # Extract metadata
        with tarfile.open(downloaded_file, 'r:gz') as tar:
            # Find Chart.yaml
            chart_yaml_member = None
            for member in tar.getmembers():
                if member.name.endswith('Chart.yaml'):
                    chart_yaml_member = member
                    break

            if not chart_yaml_member:
                raise ValueError("Chart.yaml not found in package")

            # Extract and parse Chart.yaml
            chart_yaml_content = tar.extractfile(chart_yaml_member).read()
            chart_metadata = pyyaml.safe_load(chart_yaml_content)

        # Calculate checksum
        checksum = hashlib.sha256()
        with open(downloaded_file, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                checksum.update(chunk)

        file_size = os.path.getsize(downloaded_file)

        # Store in database
        helm_chart = HelmChart(
            name=chart_metadata.get('name', chart_name),
            version=chart_metadata.get('version', version or 'unknown'),
            app_version=chart_metadata.get('appVersion'),
            description=chart_metadata.get('description'),
            source_type='cloned',
            source_chart=chart_reference,
            source_repository=repository,
            file_path=downloaded_file,
            file_size=file_size,
            checksum=checksum.hexdigest(),
            chart_metadata=chart_metadata,
            uploaded_by=cloned_by
        )

        self.db.add(helm_chart)
        self.db.flush()
        self.db.refresh(helm_chart)

        return {
            "success": True,
            "chart": {
                "id": helm_chart.id,
                "name": helm_chart.name,
                "version": helm_chart.version,
                "app_version": helm_chart.app_version,
                "description": helm_chart.description,
                "source_type": helm_chart.source_type,
                "source_chart": helm_chart.source_chart,
                "source_repository": helm_chart.source_repository,
                "file_size": helm_chart.file_size,
                "checksum": helm_chart.checksum,
                "created_at": helm_chart.created_at.isoformat() if helm_chart.created_at else None
            }
        }
