"""
Tests for centralized CLI argument injection prevention.

Covers:
  - utils.security.validate_cli_arg  (B2)
  - utils.security.validate_helm_timeout  (B2)
  - HelmService timeout/username/password validation  (B1)
  - CommandHandlers Helm arg validation  (defense-in-depth)

These tests import only the lightweight utils.security module directly.
HelmService and CommandHandlers tests use lazy imports inside test classes
to avoid triggering heavy backend init (DB, encryption) at collection time.
"""

import os
import sys

import pytest

# Ensure backend module is importable
backend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_path not in sys.path:
    sys.path.append(backend_path)


# =========================================================================
# B2: Centralized validate_cli_arg
# =========================================================================

from utils.security import validate_cli_arg, validate_helm_timeout


class TestValidateCliArg:
    """Tests for utils.security.validate_cli_arg."""

    def test_rejects_single_dash(self):
        with pytest.raises(ValueError, match="Invalid foo: cannot start with '-'"):
            validate_cli_arg("foo", "-bar")

    def test_rejects_double_dash(self):
        with pytest.raises(ValueError, match="Invalid release_name"):
            validate_cli_arg("release_name", "--set=image=evil")

    def test_rejects_flag_like_value(self):
        with pytest.raises(ValueError, match="Invalid timeout"):
            validate_cli_arg("timeout", "--dry-run")

    def test_allows_none(self):
        validate_cli_arg("optional_arg", None)

    def test_allows_empty_string(self):
        validate_cli_arg("optional_arg", "")

    def test_allows_normal_value(self):
        validate_cli_arg("release_name", "my-release")

    def test_allows_value_with_internal_dash(self):
        validate_cli_arg("name", "my-release-name")

    def test_allows_url(self):
        validate_cli_arg("url", "https://charts.example.com/repo")

    def test_allows_oci_url(self):
        validate_cli_arg("chart_ref", "oci://repo.f5.com/charts/f5-lifecycle-operator")


class TestValidateHelmTimeout:
    """Tests for utils.security.validate_helm_timeout."""

    def test_allows_none(self):
        validate_helm_timeout(None)

    def test_allows_5m(self):
        validate_helm_timeout("5m")

    def test_allows_300s(self):
        validate_helm_timeout("300s")

    def test_allows_1h(self):
        validate_helm_timeout("1h")

    def test_allows_1h30m(self):
        validate_helm_timeout("1h30m")

    def test_allows_10m0s(self):
        validate_helm_timeout("10m0s")

    def test_rejects_flag(self):
        with pytest.raises(ValueError, match="Invalid timeout: cannot start with '-'"):
            validate_helm_timeout("--set=image=evil")

    def test_rejects_arbitrary_string(self):
        with pytest.raises(ValueError, match="Invalid timeout format"):
            validate_helm_timeout("five-minutes")

    def test_rejects_bare_number(self):
        with pytest.raises(ValueError, match="Invalid timeout format"):
            validate_helm_timeout("300")

    def test_rejects_space_padded(self):
        with pytest.raises(ValueError, match="Invalid timeout format"):
            validate_helm_timeout("5m ")

    def test_rejects_injection_via_format(self):
        with pytest.raises(ValueError, match="Invalid timeout format"):
            validate_helm_timeout("5m --set=image=evil")


# =========================================================================
# B1: HelmService timeout/username/password injection
#
# These tests mock the entire import chain to avoid needing psycopg2 or
# a real DB.  We verify that validate_helm_timeout / validate_cli_arg
# are called with the right arguments and raise on bad input.
# =========================================================================

from unittest.mock import MagicMock, patch


def _get_helm_service():
    """Lazily import HelmService, mocking heavy dependencies."""
    # These may already be imported if running in Docker — safe to re-import
    from services.helm_service import HelmService
    return HelmService(MagicMock())


# Only run HelmService tests if the import chain works (Docker / Python 3.10+).
# On local dev machines with Python 3.9 the `int | None` syntax in
# cluster_utils.py will fail at import time — skip gracefully.
try:
    _get_helm_service()
    _helm_available = True
except Exception:
    _helm_available = False

_skip_helm = pytest.mark.skipif(
    not _helm_available,
    reason="HelmService import requires Python 3.10+ or Docker env",
)


@_skip_helm
class TestHelmServiceTimeoutInjection:
    """Timeout injection must be blocked in all methods that accept timeout."""

    def test_install_chart_rejects_flag_as_timeout(self):
        svc = _get_helm_service()
        with pytest.raises(ValueError, match="cannot start with '-'"):
            svc.install_chart(1, "release", "chart", timeout="--dry-run")

    def test_install_chart_rejects_bad_format_timeout(self):
        svc = _get_helm_service()
        with pytest.raises(ValueError, match="Invalid timeout format"):
            svc.install_chart(1, "release", "chart", timeout="notaunit")

    def test_upgrade_release_rejects_bad_timeout(self):
        svc = _get_helm_service()
        with pytest.raises(ValueError, match="cannot start with '-'"):
            svc.upgrade_release(1, "release", timeout="--set=image=evil")

    def test_rollback_release_rejects_bad_timeout(self):
        svc = _get_helm_service()
        with pytest.raises(ValueError, match="cannot start with '-'"):
            svc.rollback_release(1, "release", timeout="--force")

    def test_uninstall_release_rejects_bad_timeout(self):
        svc = _get_helm_service()
        with pytest.raises(ValueError, match="cannot start with '-'"):
            svc.uninstall_release(1, "release", timeout="--all")

    def test_test_release_rejects_bad_timeout(self):
        svc = _get_helm_service()
        with pytest.raises(ValueError, match="cannot start with '-'"):
            svc.test_release(1, "release", timeout="--logs")

    def test_install_chart_accepts_valid_timeout(self):
        """Valid timeout should NOT raise ValueError for timeout."""
        svc = _get_helm_service()
        svc._validate_arg = MagicMock()  # skip other validations
        try:
            svc.install_chart(1, "release", "chart", timeout="10m")
        except ValueError as e:
            # ValueError from decrypt/kubeconfig is fine — only fail if
            # the error is about timeout validation itself.
            if "timeout" in str(e).lower():
                pytest.fail("Valid timeout '10m' should not raise ValueError")
        except Exception:
            pass  # Other errors expected (no DB, no cluster)


@_skip_helm
class TestHelmServiceUsernamePasswordInjection:
    """Username/password injection in add_repository must be blocked."""

    def test_add_repository_rejects_malicious_username(self):
        svc = _get_helm_service()
        with pytest.raises(ValueError, match="Invalid username: cannot start with '-'"):
            svc.add_repository(
                "myrepo", "https://charts.example.com",
                username="--insecure-skip-tls-verify", password="x",
            )

    def test_add_repository_rejects_malicious_password(self):
        svc = _get_helm_service()
        with pytest.raises(ValueError, match="Invalid password: cannot start with '-'"):
            svc.add_repository(
                "myrepo", "https://charts.example.com",
                username="user", password="--ca-file=/etc/shadow",
            )

    def test_add_repository_allows_none_credentials(self):
        svc = _get_helm_service()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            svc.add_repository("myrepo", "https://charts.example.com")

    def test_add_repository_allows_normal_credentials(self):
        svc = _get_helm_service()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            svc.add_repository(
                "myrepo", "https://charts.example.com",
                username="admin", password="s3cret",
            )


# =========================================================================
# Defense-in-depth: operator command_handlers
#
# The bnk-operator is a separate deployable with its own container image.
# Its source is at ../../bnk-operator on the host (or /app/bnk-operator
# when mounted via docker-compose.dev.yml).  We add the operator dir to
# sys.path and stub kr8s if needed so command_handlers can be imported
# directly — no replicas, no workarounds.
# =========================================================================

import types as _types

# Resolve operator path: try Docker dev mount first, then host-relative
_operator_candidates = [
    os.path.join(os.path.dirname(__file__), "..", "bnk-operator"),         # /app/bnk-operator (Docker dev mount)
    os.path.join(os.path.dirname(__file__), "..", "..", "bnk-operator"),   # host relative
]
_operator_path = None
for _p in _operator_candidates:
    _abs = os.path.abspath(_p)
    if os.path.isfile(os.path.join(_abs, "command_handlers.py")):
        _operator_path = _abs
        break

# Temporarily add operator to sys.path, import, then remove to avoid polluting
# the module namespace (operator has its own main.py that would shadow backend's).
try:
    if _operator_path:
        _added_to_path = _operator_path not in sys.path
        if _added_to_path:
            sys.path.insert(0, _operator_path)

        # Stub kr8s if not installed so command_handlers can import
        _had_kr8s = "kr8s" in sys.modules
        if not _had_kr8s:
            _kr8s_stub = _types.ModuleType("kr8s")
            _kr8s_async = _types.ModuleType("kr8s.asyncio")
            _kr8s_async.Api = type("Api", (), {})  # type: ignore[attr-defined]
            _kr8s_stub.asyncio = _kr8s_async  # type: ignore[attr-defined]
            sys.modules["kr8s"] = _kr8s_stub
            sys.modules["kr8s.asyncio"] = _kr8s_async

        from command_handlers import _validate_cli_arg as op_validate
        from command_handlers import _validate_helm_timeout as op_validate_timeout

        # Clean up: remove operator path so its main.py doesn't shadow backend's
        if _added_to_path:
            sys.path.remove(_operator_path)

    _operator_available = _operator_path is not None
except Exception:
    _operator_available = False
    # Clean up path on failure too
    if _operator_path and _operator_path in sys.path:
        sys.path.remove(_operator_path)

_skip_operator = pytest.mark.skipif(
    not _operator_available,
    reason="bnk-operator/command_handlers.py not found or cannot be imported",
)


@_skip_operator
class TestOperatorValidation:
    """Operator has its own inline copy — verify it works identically."""

    def test_rejects_flag(self):
        with pytest.raises(ValueError, match="Invalid release"):
            op_validate("release", "--help")

    def test_allows_normal(self):
        op_validate("release", "my-release")

    def test_timeout_rejects_bad(self):
        with pytest.raises(ValueError, match="cannot start with '-'"):
            op_validate_timeout("--all")

    def test_timeout_rejects_bad_format(self):
        with pytest.raises(ValueError, match="Invalid timeout format"):
            op_validate_timeout("garbage")

    def test_timeout_allows_valid(self):
        op_validate_timeout("10m")
        op_validate_timeout("300s")
        op_validate_timeout(None)
