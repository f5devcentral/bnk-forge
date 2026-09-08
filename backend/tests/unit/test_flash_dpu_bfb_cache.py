"""Regression tests for BFB on-host cache bugs.

Bug 1: a failed download must not leave a junk file at the final cache path.
       wget/curl write directly to the target even on 404, poisoning the cache
       so the next retry treats the 0-byte file as valid and hangs bfb-install.

Bug 2: a cached file that fails size/sanity validation must be deleted and
       re-downloaded rather than passed blindly to bfb-install.
"""

from __future__ import annotations

import pytest

from modules.bare_metal.flash_dpu import FlashDPUModule, _validate_bfb_on_host


class _R:
    """Minimal SSH execute result stub."""

    def __init__(self, exit_code: int = 0, stdout: str = "", stderr: str = ""):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr


class _MockSession:
    """Records session.execute() calls and returns queued responses."""

    def __init__(self, *responses: _R) -> None:
        self._queue = list(responses)
        self.calls: list[str] = []

    def execute(self, cmd: str, timeout: int = 30) -> _R:  # noqa: ARG002
        self.calls.append(cmd)
        if not self._queue:
            raise AssertionError(
                f"Unexpected session.execute call — no responses queued.\nCmd: {cmd!r}"
            )
        return self._queue.pop(0)

    def called_with_fragment(self, fragment: str) -> bool:
        return any(fragment in c for c in self.calls)


BFB_URL = "https://example.com/bf-bundle-3.2.1.bfb"
BFB_PATH = "/tmp/bf-bundle-3.2.1.bfb"
BFB_TMP = "/tmp/bf-bundle-3.2.1.bfb.partial"

_noop = lambda *_: None  # noqa: E731


# ── Bug 1 ────────────────────────────────────────────────────────────────────

class TestBug1FailedDownloadLeavesNoJunk:
    """Failed downloads must not leave a junk file at the final cache path."""

    def test_failedDownload_cleansUpAndRaises(self):
        """curl exits non-zero → rm only the final path (keep .partial for resume), then raise."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),              # cache check
            _R(stdout="HTTP/1.1 200 OK"),              # HEAD pre-flight
            _R(exit_code=1, stdout="curl: (22) 404"),  # curl download fails
            _R(),                                      # rm -f final path only
        )
        with pytest.raises(RuntimeError, match="BFB download failed"):
            FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        # Only the final path is removed; .partial is kept for -C - resume on retry.
        assert session.called_with_fragment(BFB_PATH), (
            f"Expected rm -f of final path {BFB_PATH!r}; calls: {session.calls}"
        )
        assert not any(
            f"rm -f '{BFB_TMP}'" in c or (f"'{BFB_TMP}'" in c and "rm" in c)
            for c in session.calls
        ), f".partial must NOT be removed on download failure; calls: {session.calls}"

    def test_failedDownload_doesNotPromoteTempToFinalPath(self):
        """After a failed download, mv must never be called."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),
            _R(stdout="HTTP/1.1 200 OK"),  # HEAD pre-flight
            _R(exit_code=1, stdout="ERROR"),
            _R(),  # rm -f
        )
        with pytest.raises(RuntimeError):
            FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        assert not session.called_with_fragment("mv"), (
            f"mv must not be called after a failed download; calls: {session.calls}"
        )

    def test_successfulDownload_promotesTempToFinalPath(self):
        """A valid download is atomically moved to the final cache path."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),     # cache check
            _R(stdout="HTTP/1.1 200 OK"),     # HEAD pre-flight
            _R(),                             # curl download succeeds (exit_code=0)
            _R(stdout="1500000000"),          # stat on .partial → valid size
            _R(),                             # mv .partial → final
        )
        FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        assert session.called_with_fragment(f"mv '{BFB_TMP}' '{BFB_PATH}'"), (
            f"Expected atomic mv from temp to final; calls: {session.calls}"
        )


# ── Bug 2 ────────────────────────────────────────────────────────────────────

class TestBug2CachedInvalidFileRedownloads:
    """A cached file that fails validation must be deleted and re-downloaded."""

    def test_cachedTooSmall_deletesAndRedownloads(self):
        """Cached file with size < 1 MB triggers delete + re-download."""
        session = _MockSession(
            _R(stdout="CACHED"),              # cache check → exists
            _R(stdout="500"),                 # stat on cached file → tiny
            _R(stdout="<html>404</html>"),    # head preview (validate_bfb_on_host)
            _R(),                             # rm -f stale cached file
            _R(stdout="HTTP/1.1 200 OK"),     # HEAD pre-flight before re-download
            _R(),                             # curl re-download succeeds
            _R(stdout="1500000000"),          # stat on .partial → valid
            _R(),                             # mv .partial → final
        )
        FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        # Stale file was removed
        assert session.called_with_fragment(f"rm -f '{BFB_PATH}'"), (
            f"Expected rm -f of stale cached file; calls: {session.calls}"
        )
        # Re-download was triggered (curl is always present from HEAD too, check for -C -)
        assert session.called_with_fragment("-C -"), (
            f"Expected resilient curl re-download; calls: {session.calls}"
        )
        # Atomic promotion happened
        assert session.called_with_fragment(f"mv '{BFB_TMP}' '{BFB_PATH}'"), (
            f"Expected mv after re-download; calls: {session.calls}"
        )

    def test_cachedValidFile_skipsDownload(self):
        """A cached file that passes validation is used as-is (no download)."""
        session = _MockSession(
            _R(stdout="CACHED"),              # cache check → exists
            _R(stdout="1500000000"),          # stat → valid size
        )
        FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        assert not session.called_with_fragment("wget"), (
            f"wget must not run on a valid cached file; calls: {session.calls}"
        )
        assert not session.called_with_fragment("curl"), (
            f"curl must not run on a valid cached file; calls: {session.calls}"
        )


# ── _validate_bfb_on_host unit tests ─────────────────────────────────────────

class TestValidateBfbOnHost:
    def test_validSize_returnsFileSize(self):
        session = _MockSession(_R(exit_code=0, stdout="1500000000"))
        result = _validate_bfb_on_host(session, BFB_PATH, BFB_URL)
        assert result == 1_500_000_000

    def test_tooSmall_raisesRuntimeError(self):
        session = _MockSession(
            _R(exit_code=0, stdout="500"),
            _R(stdout="<html>error</html>"),
        )
        with pytest.raises(RuntimeError, match="500 bytes"):
            _validate_bfb_on_host(session, BFB_PATH, BFB_URL)

    def test_statFails_treatsAsTooSmall(self):
        """If stat fails (exit_code != 0), file_size defaults to 0 → raises."""
        session = _MockSession(
            _R(exit_code=1, stdout=""),
            _R(stdout=""),  # head -c 200
        )
        with pytest.raises(RuntimeError, match="0 bytes"):
            _validate_bfb_on_host(session, BFB_PATH, BFB_URL)


# ── HEAD pre-flight tests ─────────────────────────────────────────────────────


class TestHeadPreflight:
    """HEAD pre-flight check behaviour in _ensure_bfb."""

    def test_getFailsNonZero_errorMessageContainsUrl(self):
        """GET exits non-zero → RuntimeError message must include the URL."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),             # cache check
            _R(stdout="HTTP/1.1 200 OK"),             # HEAD → 200 (proceed)
            _R(exit_code=1, stdout="curl: (22) 404"), # curl download fails
            _R(),                                     # rm -f final path only
        )
        with pytest.raises(RuntimeError, match=BFB_URL):
            FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

    def test_head404_raisesWithProbeDpuHintAndUrl(self):
        """HEAD 404 → RuntimeError mentions 'probe-dpu' and the URL; download never starts."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),              # cache check
            _R(stdout="HTTP/1.1 404 Not Found"),       # HEAD → 404
        )
        with pytest.raises(RuntimeError) as exc_info:
            FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        msg = str(exc_info.value)
        assert "probe-dpu" in msg, f"Expected 'probe-dpu' hint in error; got: {msg!r}"
        assert BFB_URL in msg, f"Expected URL in error; got: {msg!r}"

        # The big download must not have been attempted.
        assert not session.called_with_fragment(f"-O '{BFB_TMP}'"), (
            f"wget download must not run after 404; calls: {session.calls}"
        )
        assert not session.called_with_fragment(f"-o '{BFB_TMP}'"), (
            f"curl download must not run after 404; calls: {session.calls}"
        )

    def test_head403_raisesAndBlocksDownload(self):
        """HEAD 403 is treated the same as 404 — blocks the download."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),
            _R(stdout="HTTP/1.1 403 Forbidden"),
        )
        with pytest.raises(RuntimeError, match="403"):
            FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        assert not session.called_with_fragment(f"-O '{BFB_TMP}'"), (
            f"wget download must not run after 403; calls: {session.calls}"
        )

    def test_headInconclusive405_proceedsToDownloadAndPromotes(self):
        """HEAD 405 Method Not Allowed (mirror doesn't support HEAD) — download proceeds."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),                  # cache check
            _R(stdout="HTTP/1.1 405 Method Not Allowed"),  # HEAD → 405 (non-blocking)
            _R(),                                          # wget||curl succeeds
            _R(stdout="1500000000"),                       # stat on .partial → valid
            _R(),                                          # mv .partial → final
        )
        FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        assert session.called_with_fragment(f"mv '{BFB_TMP}' '{BFB_PATH}'"), (
            f"Expected mv to final path; calls: {session.calls}"
        )

    def test_headConnectionError_proceedsToDownload(self):
        """HEAD exit_code != 0 with no HTTP status is inconclusive — download proceeds."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),
            _R(exit_code=7, stdout="curl: (7) Failed to connect"),  # HEAD connection error
            _R(),                                                    # wget||curl download
            _R(stdout="1500000000"),                                 # stat → valid
            _R(),                                                    # mv
        )
        FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        assert session.called_with_fragment(f"mv '{BFB_TMP}' '{BFB_PATH}'"), (
            f"Expected mv after inconclusive HEAD; calls: {session.calls}"
        )

    def test_head200_downloadValidatesAndPromotes(self):
        """HEAD 200 → download proceeds, file validated, atomically promoted."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),         # cache check
            _R(stdout="HTTP/1.1 200 OK"),         # HEAD → 200
            _R(),                                 # curl download succeeds
            _R(stdout="1500000000"),              # stat on .partial → valid
            _R(),                                 # mv .partial → final
        )
        FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        assert session.called_with_fragment(f"mv '{BFB_TMP}' '{BFB_PATH}'"), (
            f"Expected atomic mv from temp to final; calls: {session.calls}"
        )


# ── Resilient download (Fix 3) tests ─────────────────────────────────────────


class TestResilientDownload:
    """Stall-resilient curl command and selective cleanup semantics."""

    def test_downloadCommand_containsResilienceFlags(self):
        """Issued curl command must carry -C -, --retry, and --speed-time."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),
            _R(stdout="HTTP/1.1 200 OK"),  # HEAD
            _R(),                          # curl download
            _R(stdout="1500000000"),       # stat → valid
            _R(),                          # mv
        )
        FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        dl_cmd = next(c for c in session.calls if "-C -" in c)
        assert "--retry" in dl_cmd, f"Expected --retry in download cmd; got: {dl_cmd!r}"
        assert "--speed-time" in dl_cmd, f"Expected --speed-time in download cmd; got: {dl_cmd!r}"
        assert "-C -" in dl_cmd, f"Expected -C - (resume) in download cmd; got: {dl_cmd!r}"

    def test_downloadFailure_keepsTmpRemovesFinalPath(self):
        """On curl failure, .partial is kept for -C - resume; only bfb_path is removed."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),
            _R(stdout="HTTP/1.1 200 OK"),             # HEAD
            _R(exit_code=1, stdout="transfer stall"), # curl stalls/fails
            _R(),                                     # rm -f final path only
        )
        with pytest.raises(RuntimeError):
            FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        rm_calls = [c for c in session.calls if "rm" in c]
        assert rm_calls, "Expected at least one rm call"
        # Final path must be removed
        assert any(BFB_PATH in c for c in rm_calls), (
            f"Expected rm to target {BFB_PATH!r}; rm calls: {rm_calls}"
        )
        # .partial must NOT be in any rm command
        assert not any(BFB_TMP in c for c in rm_calls), (
            f".partial must be kept for resume; rm calls: {rm_calls}"
        )

    def test_validationFailure_deletesTmpAndFinalPath(self):
        """On validation failure (corrupt content), .partial is deleted so next attempt starts fresh."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),
            _R(stdout="HTTP/1.1 200 OK"),   # HEAD
            _R(),                           # curl download (exit 0)
            _R(stdout="500"),               # stat on .partial → tiny (validation fails)
            _R(stdout="<html>err</html>"),  # head preview (validate_bfb_on_host)
            _R(),                           # rm -f .partial and final path
        )
        with pytest.raises(RuntimeError):
            FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        rm_calls = [c for c in session.calls if "rm" in c]
        assert rm_calls, "Expected rm call after validation failure"
        # Both .partial and final path must be cleaned up
        assert any(BFB_TMP in c for c in rm_calls), (
            f".partial must be deleted on validation failure; rm calls: {rm_calls}"
        )
        assert any(BFB_PATH in c for c in rm_calls), (
            f"Final path must be deleted on validation failure; rm calls: {rm_calls}"
        )

    def test_happyPath_stillPromotes(self):
        """HEAD 200 → download → validate → mv; unaffected by resilience changes."""
        session = _MockSession(
            _R(stdout="DOWNLOAD_NEEDED"),
            _R(stdout="HTTP/1.1 200 OK"),  # HEAD
            _R(),                          # curl download
            _R(stdout="1500000000"),       # stat → valid
            _R(),                          # mv
        )
        FlashDPUModule._ensure_bfb(session, BFB_PATH, BFB_URL, _noop)

        assert session.called_with_fragment(f"mv '{BFB_TMP}' '{BFB_PATH}'"), (
            f"Expected atomic mv to final path; calls: {session.calls}"
        )
