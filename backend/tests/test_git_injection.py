import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if backend_path not in sys.path:
    sys.path.append(backend_path)

from services.git_auth_service import GitAuthContext
from services.variable_parser import VariableParser


def test_clone_uses_separator():
    """
    Test that clone_and_parse_git_module uses '--' separator for valid URLs
    """
    valid_url = "https://github.com/org/repo.git"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("tempfile.mkdtemp", return_value="/tmp/mock_dir"), \
             patch("shutil.rmtree"), \
             patch("os.path.exists", return_value=False): # Don't find files, just return

            VariableParser.clone_and_parse_git_module(valid_url)

            assert mock_run.called
            args, _ = mock_run.call_args
            cmd_list = args[0]

            assert "--" in cmd_list, "Git clone command must use '--' separator"
            idx_separator = cmd_list.index("--")

            found_url = False
            idx_url = -1
            for i, arg in enumerate(cmd_list):
                if "github.com/org/repo.git" in arg:
                    idx_url = i
                    found_url = True
                    break

            assert found_url, "URL not found in command"
            assert idx_separator < idx_url, "'--' separator must appear before the URL"

def test_clone_rejects_argument_injection():
    """
    Test that clone_and_parse_git_module rejects URLs starting with '-'
    """
    malicious_url = "-oProxyCommand=calc.exe"

    with patch("subprocess.run") as mock_run:
        success, vars, error = VariableParser.clone_and_parse_git_module(malicious_url)

        assert success is False
        assert "Invalid git source" in error
        assert "cannot start with '-'" in error
        assert not mock_run.called, "Should not execute git command for suspicious URL"


def test_clone_does_not_put_token_in_url_args():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch("shutil.rmtree"), \
             patch("os.path.exists", return_value=False):
            auth_ctx = GitAuthContext(
                transport="https_token",
                credential_type="legacy_pat",
                secret="super-secret-token",
                username="git",
            )
            VariableParser.clone_and_parse_git_module(
                "https://github.com/org/repo.git",
                auth_context=auth_ctx,
            )

            args, kwargs = mock_run.call_args
            cmd_list = args[0]
            assert all("super-secret-token" not in str(item) for item in cmd_list)
            assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
