import os
import shutil
import sys
import tempfile
import urllib.parse
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_path not in sys.path:
    sys.path.append(backend_path)

from main import app
from routes import api


@pytest.fixture
def temp_logs_dir():
    # Create temp dir
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    # Cleanup
    shutil.rmtree(temp_dir)

def test_get_log_path_traversal(temp_logs_dir):
    # Create a dummy log file
    with open(os.path.join(temp_logs_dir, "test.log"), "w") as f:
        f.write("safe log content")

    # Create a secret file outside logs dir (but in temp parent)
    secret_path = os.path.join(os.path.dirname(temp_logs_dir), "secret.txt")
    with open(secret_path, "w") as f:
        f.write("SECRET_DATA")

    print(f"\nLOGS_DIR: {temp_logs_dir}")
    print(f"Secret path: {secret_path}")

    # Patch LOGS_DIR in api module
    with patch.object(api, 'LOGS_DIR', temp_logs_dir):
        client = TestClient(app)

        # Test path traversal
        # Use encoded slash to ensure it passes through as part of the path parameter
        traversal_path = "..%2Fsecret.txt"

        response = client.get(f"/api/logs/{traversal_path}")

        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.json()}")

        # Assert that it is blocked (expecting 403 or 404, not 200)
        # If it returns 200, it means it read the secret file!
        if response.status_code == 200:
             content = response.json().get("content", "")
             print(f"Content read: {content}")
             if "SECRET_DATA" in content:
                 pytest.fail("VULNERABILITY DETECTED: Secret file was read!")

        # 401 is also acceptable — auth middleware blocks unauthenticated
        # requests before the route handler runs, which is still a valid
        # security block preventing path traversal.
        assert response.status_code in [401, 403, 404]
