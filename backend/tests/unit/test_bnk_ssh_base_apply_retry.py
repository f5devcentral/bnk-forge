"""
Unit tests for BnkSSHModule._apply_manifests retry logic (ADR-478).

Covers:
  - Transient ResourceQuota admission race ("status unknown for quota"):
      first apply fails, second succeeds — no exception raised, retry logged.
  - Non-retriable stderr: raises RuntimeError on the first failure, no retry.

No DB, no live SSH — pure Python + MagicMock + SimpleNamespace stubs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from modules.bare_metal.bnk_ssh_base import BnkSSHModule

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _res(exit_code: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    """Minimal stub matching the attributes _apply_manifests reads."""
    return SimpleNamespace(exit_code=exit_code, stdout=stdout, stderr=stderr)


def _module() -> BnkSSHModule:
    return BnkSSHModule()


def _apply_call_count(session: MagicMock) -> int:
    """Count session.execute calls that contain 'kubectl apply'."""
    return sum(1 for c in session.execute.call_args_list if "kubectl apply" in str(c))


def _make_session(*side_effects: SimpleNamespace) -> MagicMock:
    """Return a mock session whose .execute() yields the given results in order.

    sequence:
      [0] mktemp result       — must have exit_code=0, stdout=<path>
      [1] cat-write result    — return value is discarded by _write_remote_tmp
      [2..n] apply attempts   — checked for exit_code / stderr
      [-1] shred result       — return value is discarded by _shred_remote_tmp
    """
    session = MagicMock()
    session.execute.side_effect = list(side_effects)
    return session


# ---------------------------------------------------------------------------
# Quota-status transient retry
# ---------------------------------------------------------------------------

class TestApplyManifestsQuotaStatusRetry:
    """_apply_manifests must retry when stderr contains 'status unknown for quota'."""

    _QUOTA_ERR = "status unknown for quota: f5-single-license-quota, resources: count/licenses.k8s.f5net.com"
    _MANIFEST = [{"apiVersion": "k8s.f5net.com/v1", "kind": "License", "metadata": {"name": "bnk-license"}}]

    def test_apply_manifests_quota_status_error_retries_and_succeeds(self):
        """Arrange: first apply returns quota-status Forbidden; second returns exit 0.
        Assert: no RuntimeError is raised and apply was called at least twice.
        """
        session = _make_session(
            _res(exit_code=0, stdout="/tmp/bnk.tmp12345"),         # mktemp
            _res(),                                                  # cat write
            _res(exit_code=1, stderr=self._QUOTA_ERR),              # apply attempt 1 — transient
            _res(exit_code=0, stdout="license.k8s.f5net.com/bnk-license created"),  # apply attempt 2 — ok
            _res(),                                                  # shred
        )
        logs: list[str] = []

        with patch("time.sleep"):
            # Should not raise
            _module()._apply_manifests(session, self._MANIFEST, logs.append)

        assert _apply_call_count(session) >= 2

    def test_apply_manifests_quota_status_error_emits_retry_log(self):
        """Retry must log a message containing 'transient admission error'."""
        session = _make_session(
            _res(exit_code=0, stdout="/tmp/bnk.tmp12345"),
            _res(),
            _res(exit_code=1, stderr=self._QUOTA_ERR),
            _res(exit_code=0, stdout="license.k8s.f5net.com/bnk-license created"),
            _res(),
        )
        logs: list[str] = []

        with patch("time.sleep"):
            _module()._apply_manifests(session, self._MANIFEST, logs.append)

        assert any("transient admission error" in line for line in logs)

    def test_apply_manifests_quota_status_error_sleeps_between_attempts(self):
        """time.sleep must be called once between the two attempts."""
        session = _make_session(
            _res(exit_code=0, stdout="/tmp/bnk.tmp12345"),
            _res(),
            _res(exit_code=1, stderr=self._QUOTA_ERR),
            _res(exit_code=0, stdout="license.k8s.f5net.com/bnk-license created"),
            _res(),
        )

        with patch("time.sleep") as mock_sleep:
            _module()._apply_manifests(session, self._MANIFEST, lambda _: None)

        mock_sleep.assert_called_once_with(BnkSSHModule.WEBHOOK_RETRY_SLEEP)


# ---------------------------------------------------------------------------
# Non-retriable failure
# ---------------------------------------------------------------------------

class TestApplyManifestsNonRetriableError:
    """_apply_manifests must raise RuntimeError immediately on non-retriable stderr."""

    _MANIFEST = [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "cfg"}}]

    def test_apply_manifests_nonretriable_error_raises_without_retry(self):
        """Arrange: apply returns exit 1 with unrecognised stderr.
        Assert: RuntimeError is raised and apply was called exactly once.
        """
        session = _make_session(
            _res(exit_code=0, stdout="/tmp/bnk.tmp99999"),  # mktemp
            _res(),                                           # cat write
            _res(exit_code=1, stderr="some other error"),    # apply — non-retriable
            _res(),                                           # shred (finally)
        )

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError, match="kubectl apply failed"):
                _module()._apply_manifests(session, self._MANIFEST, lambda _: None)

        # no retry → sleep never called
        mock_sleep.assert_not_called()
        # apply was attempted exactly once
        assert _apply_call_count(session) == 1
