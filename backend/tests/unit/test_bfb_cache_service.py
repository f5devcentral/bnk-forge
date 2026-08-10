"""Unit tests for the BFB on-disk cache.

Covers cache-miss download (mocked via requests-monkeypatch), atomic
rename on success, partial cleanup on failure, concurrency via the file
lock, and LRU eviction when the cache exceeds its cap.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import requests

from services.bfb_cache_service import (
    BfbCacheError,
    BfbCacheService,
    _stage_file_into_cache,
)


@pytest.fixture
def cache(tmp_path: Path) -> BfbCacheService:
    return BfbCacheService(cache_dir=tmp_path)


class _FakeResp:
    def __init__(self, body: bytes, status_code: int = 200, length_header: bool = True):
        self._body = body
        self.status_code = status_code
        self.headers = {"content-length": str(len(body))} if length_header else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _mock_requests_get(monkeypatch, body: bytes, *, status_code: int = 200):
    def fake_get(url, stream, timeout):  # noqa: ARG001
        return _FakeResp(body, status_code=status_code)
    monkeypatch.setattr("services.bfb_cache_service.requests.get", fake_get)


class TestCacheHit:
    def test_miss_downloads_then_hit_does_not(self, cache: BfbCacheService, monkeypatch):
        body = b"FAKE_BFB_CONTENT" * 1024
        calls = {"n": 0}

        original_mock = _FakeResp(body)

        def fake_get(url, stream, timeout):  # noqa: ARG001
            calls["n"] += 1
            return original_mock

        monkeypatch.setattr("services.bfb_cache_service.requests.get", fake_get)

        first = cache.get(filename="x.bfb", base_url="https://example.com/")
        assert first.path.exists()
        assert first.size_bytes == len(body)
        assert first.sha256 and len(first.sha256) == 64

        # Need to supply a fresh _FakeResp for the second call (its iterator
        # was exhausted), but the cache hit shouldn't invoke it at all.
        monkeypatch.setattr(
            "services.bfb_cache_service.requests.get",
            lambda *a, **k: _FakeResp(body),
        )

        second = cache.get(filename="x.bfb", base_url="https://example.com/")
        assert second.path == first.path
        assert calls["n"] == 1  # second call was a hit


class TestDownloadErrors:
    def test_network_error_raises_and_cleans_up_partial(self, cache: BfbCacheService, monkeypatch):
        def raise_it(url, stream, timeout):  # noqa: ARG001
            raise requests.ConnectionError("boom")

        monkeypatch.setattr("services.bfb_cache_service.requests.get", raise_it)

        with pytest.raises(BfbCacheError):
            cache.get(filename="x.bfb", base_url="https://example.com/")

        # No residual .partial or final file — a subsequent retry should not
        # be misled into thinking the file is ready.
        assert not (cache.cache_dir / "x.bfb").exists()
        assert not (cache.cache_dir / "x.bfb.partial").exists()

    def test_http_error_raises(self, cache: BfbCacheService, monkeypatch):
        _mock_requests_get(monkeypatch, b"", status_code=404)
        with pytest.raises(BfbCacheError):
            cache.get(filename="x.bfb", base_url="https://example.com/")
        assert not (cache.cache_dir / "x.bfb").exists()


class TestFilenameValidation:
    @pytest.mark.parametrize("bad", ["", ".", "..", "/abs", "a/b", "a\\b"])
    def test_rejects_unsafe_filenames(self, cache: BfbCacheService, bad: str):
        with pytest.raises(BfbCacheError):
            cache.get(filename=bad, base_url="https://example.com/")


class TestEviction:
    def test_lru_eviction_drops_oldest(self, tmp_path: Path):
        import os
        import time

        cache = BfbCacheService(cache_dir=tmp_path, max_bytes=200)

        _stage_file_into_cache(cache, "a.bfb", b"A" * 100)
        _stage_file_into_cache(cache, "b.bfb", b"B" * 100)
        path_c = _stage_file_into_cache(cache, "c.bfb", b"C" * 100)

        # Set atimes AFTER staging all three files so no subsequent directory
        # walk (e.g. inside _stage_file_into_cache) can reset them on macOS APFS.
        # a oldest (t-1000), b middle (t-10), c newest (t).
        now = time.time()
        os.utime(cache.cache_dir / "a.bfb", (now - 1000, now - 1000))
        os.utime(cache.cache_dir / "b.bfb", (now - 10, now - 10))
        os.utime(cache.cache_dir / "c.bfb", (now, now))

        # total 300 bytes > cap(200) → evict oldest (a), keep b + c.
        cache._evict_if_over_cap(keep=path_c)

        remaining = {n for n, _, _ in cache.list_entries()}
        assert "c.bfb" in remaining
        assert "b.bfb" in remaining
        assert "a.bfb" not in remaining


class TestProgressCallback:
    def test_progress_callback_invoked(self, cache: BfbCacheService, monkeypatch):
        body = b"x" * (5 * 1024 * 1024)  # > 1 chunk at 4 MiB
        _mock_requests_get(monkeypatch, body)

        seen: list[tuple[int, int | None]] = []
        cache.get(
            filename="x.bfb",
            base_url="https://example.com/",
            progress_cb=lambda done, total: seen.append((done, total)),
        )
        # Callback should have fired at least twice with increasing `done`.
        assert len(seen) >= 2
        assert seen[0][0] < seen[-1][0]
        assert seen[-1][0] == len(body)


class TestConcurrency:
    def test_concurrent_gets_serialise_download_via_flock(
        self, cache: BfbCacheService, monkeypatch,
    ):
        # Thread A starts a "slow" download; thread B begins shortly after. Both
        # should get the same file, but `requests.get` should only run once
        # because the flock serialises the download and thread B sees the
        # cached file on its turn.
        calls: list[str] = []
        gate = threading.Event()
        done_gate = threading.Event()
        body = b"Z" * 8192

        def slow_get(url, stream, timeout):  # noqa: ARG001
            calls.append(url)
            gate.set()          # signal that A is inside the download
            done_gate.wait(2)   # hold the lock briefly so B piles up
            return _FakeResp(body)

        monkeypatch.setattr("services.bfb_cache_service.requests.get", slow_get)

        result_a: list = []
        result_b: list = []

        def worker(out: list):
            out.append(cache.get(filename="x.bfb", base_url="https://example.com/"))

        t_a = threading.Thread(target=worker, args=(result_a,))
        t_b = threading.Thread(target=worker, args=(result_b,))
        t_a.start()
        gate.wait(2)
        t_b.start()
        done_gate.set()
        t_a.join(5)
        t_b.join(5)

        assert len(calls) == 1, "download should only run once"
        assert result_a and result_b
        assert result_a[0].path == result_b[0].path
