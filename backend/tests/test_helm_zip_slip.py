import os
import sys
import tarfile
import tempfile
from unittest.mock import MagicMock, patch

import pytest

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models import HelmChart
from services.helm_service import HelmService


def test_update_chart_values_prevents_zip_slip():
    """
    Test that update_chart_values prevents Zip Slip (path traversal) during extraction.
    """
    # Create a malicious tarball
    with tempfile.NamedTemporaryFile(suffix='.tgz', delete=False) as malicious_tar:
        malicious_tar_path = malicious_tar.name

    dummy_file_path = None
    values_file_path = None

    try:
        # Create a dummy file to add
        with tempfile.NamedTemporaryFile(delete=False) as dummy_file:
            dummy_file.write(b"malicious content")
            dummy_file_path = dummy_file.name

        with tarfile.open(malicious_tar_path, "w:gz") as tar:
            # Add file with traversal path
            # We try to write to a file in the same directory as the tarball (usually /tmp)
            # or just 'up one level' from the extraction root.
            tar.add(dummy_file_path, arcname="../pwn.txt")

            # Also add a valid values.yaml so it processes
            with tempfile.NamedTemporaryFile(delete=False) as values_file:
                values_file.write(b"foo: bar")
                values_file_path = values_file.name
            tar.add(values_file_path, arcname="values.yaml")

        # Mock dependencies
        mock_db = MagicMock()
        service = HelmService(mock_db)

        # Mock get_uploaded_chart to return our malicious chart
        mock_chart = MagicMock(spec=HelmChart)
        mock_chart.file_path = malicious_tar_path
        mock_chart.name = "malicious-chart"
        mock_chart.version = "1.0.0"

        # Patch get_uploaded_chart on the instance or class
        with patch.object(service, 'get_uploaded_chart', return_value=mock_chart):
            # Attempt to update values
            # Expectation: With the security fix (filter='data'), this raises tarfile.OutsideDestinationError
            # Without the fix, it might succeed (vulnerability) or fail with permission error depending on OS

            # We check for FilterError which covers OutsideDestinationError and LinkOutsideDestinationError
            with pytest.raises(tarfile.FilterError):
                 service.update_chart_values(1, "new_key: new_value")

            # Verify it is indeed an outside destination error
            # assert isinstance(excinfo.value, (tarfile.OutsideDestinationError, tarfile.LinkOutsideDestinationError))

    finally:
        # Cleanup
        if os.path.exists(malicious_tar_path):
            os.unlink(malicious_tar_path)
        if dummy_file_path and os.path.exists(dummy_file_path):
            os.unlink(dummy_file_path)
        if values_file_path and os.path.exists(values_file_path):
            os.unlink(values_file_path)

        # Cleanup potential pwn.txt if it was created (vulnerability success)
        # Note: Since we don't know exactly where temp_dir was, it's hard to find ../pwn.txt
        # But usually temp_dir is in /tmp, so ../pwn.txt is /pwn.txt (likely permission denied)
        # or /tmp/pwn.txt (if temp_dir was a subdir of /tmp).
        # We won't worry too much about cleanup of the exploit file in this mocked env
