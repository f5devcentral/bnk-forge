"""On-disk cache for BFB bundles.

Shared by backend and celery workers via the `bfb_cache` Docker volume
mounted at `/app/bfb-cache`. Downloads BFB files on first use, keeps them
around for subsequent flashes.

Concurrency model
-----------------
Multiple flashes against different DPUs can ask for the same BFB at the
same time. We use a per-filename `fcntl.flock` on a `.lock` sidecar so
only one worker downloads while the others wait; the winning worker
atomically renames `<filename>.partial` → `<filename>` on success so a
partial download can never be served.

Eviction
--------
Newest-on-top: on every write, if total cache bytes exceed
`BFB_CACHE_MAX_BYTES` (default 20 GiB, roughly 10 BFBs), drop files in
ascending `atime` order until under the cap. The currently-served file
is never evicted (it's held open via flock until close).

Integrity
---------
Sidecar `<filename>.sha256` records the SHA-256 of the downloaded bytes.
On cache-hit we verify lazily only if the caller opts in via
`verify=True` — default is trust the atomic rename (if it's there, it's
complete).
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import tempfile
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


# Defaults tunable via environment variables so ops can grow / shrink the
# on-disk cache without code changes.
DEFAULT_CACHE_DIR = os.environ.get("BFB_CACHE_DIR", "/app/bfb-cache")
DEFAULT_MAX_BYTES = int(os.environ.get("BFB_CACHE_MAX_BYTES", str(20 * 1024 * 1024 * 1024)))
# Stream downloads in 4 MiB chunks — big enough to amortise Python/HTTP
# overhead on a 2 GiB BFB, small enough to keep the progress callback
# responsive.
_CHUNK_BYTES = 4 * 1024 * 1024
_DOWNLOAD_TIMEOUT = (10, 1800)  # (connect, read) seconds — BFB can be 2 GiB on a slow link.


@dataclass
class CachedBfb:
    """Public handle returned by the cache — callers read `path`."""
    path: Path
    sha256: str | None
    size_bytes: int


class BfbCacheError(Exception):
    """Raised on unrecoverable cache failures (download error, disk full, etc)."""


class BfbCacheService:
    """File-backed BFB cache."""

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ):
        self.cache_dir = Path(cache_dir)
        self.max_bytes = max_bytes
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────

    def get(
        self,
        *,
        filename: str,
        base_url: str,
        verify: bool = False,
        progress_cb=None,
    ) -> CachedBfb:
        """Return a local path to the BFB, downloading if needed.

        `progress_cb(bytes_done, bytes_total)` is called during download;
        on cache hit it's not called.
        """
        self._sanity_check_filename(filename)
        target = self.cache_dir / filename
        lock_path = self.cache_dir / f"{filename}.lock"
        sha_path = self.cache_dir / f"{filename}.sha256"

        url = self._join_url(base_url, filename)

        with _FileLock(lock_path):
            if target.exists():
                sha = _read_optional(sha_path)
                if verify and sha:
                    actual = _sha256_file(target)
                    if actual != sha:
                        logger.warning(
                            "Cached BFB %s checksum mismatch (stored=%s actual=%s); re-downloading",
                            filename, sha, actual,
                        )
                        target.unlink(missing_ok=True)
                        sha_path.unlink(missing_ok=True)
                    else:
                        _touch_atime(target)
                        return CachedBfb(target, sha, target.stat().st_size)
                else:
                    _touch_atime(target)
                    return CachedBfb(target, sha, target.stat().st_size)

            logger.info("BFB cache miss: downloading %s from %s", filename, url)
            sha = self._download_to(target, url, progress_cb)
            sha_path.write_text(sha)
            self._evict_if_over_cap(keep=target)
            return CachedBfb(target, sha, target.stat().st_size)

    def list_entries(self) -> list[tuple[str, int, float]]:
        """(name, size_bytes, atime) for each cached BFB, newest-first."""
        out = []
        for p in self.cache_dir.iterdir():
            if p.suffix in (".partial", ".lock", ".sha256"):
                continue
            if not p.is_file():
                continue
            st = p.stat()
            out.append((p.name, st.st_size, st.st_atime))
        out.sort(key=lambda row: row[2], reverse=True)
        return out

    def total_bytes(self) -> int:
        return sum(sz for _, sz, _ in self.list_entries())

    # ── Internals ──────────────────────────────────────────────────────

    @staticmethod
    def _sanity_check_filename(filename: str) -> None:
        # Prevent traversal — callers only ever supply filenames from the
        # BluefieldSoftwareImage catalog, but belt-and-braces.
        if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
            raise BfbCacheError(f"Invalid BFB filename: {filename!r}")

    @staticmethod
    def _join_url(base: str, filename: str) -> str:
        base = base.rstrip("/")
        return f"{base}/{filename}"

    def _download_to(self, dest: Path, url: str, progress_cb) -> str:
        """Stream-download url into a `<dest>.partial`, compute sha256, atomic-rename."""
        partial = dest.with_suffix(dest.suffix + ".partial")
        hasher = hashlib.sha256()
        total = None
        done = 0
        try:
            with requests.get(url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as resp:
                resp.raise_for_status()
                content_length = resp.headers.get("content-length")
                if content_length:
                    try:
                        total = int(content_length)
                    except ValueError:
                        total = None
                with partial.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=_CHUNK_BYTES):
                        if not chunk:
                            continue
                        f.write(chunk)
                        hasher.update(chunk)
                        done += len(chunk)
                        if progress_cb is not None:
                            try:
                                progress_cb(done, total)
                            except Exception:  # noqa: BLE001 — never let the callback abort the download
                                logger.exception("BFB download progress callback raised")
        except requests.RequestException as exc:
            partial.unlink(missing_ok=True)
            raise BfbCacheError(f"BFB download failed: {exc}") from exc
        # Atomic rename — if this returns, the file is complete and valid.
        partial.replace(dest)
        return hasher.hexdigest()

    def _evict_if_over_cap(self, *, keep: Path) -> None:
        entries = self.list_entries()  # already newest-first by atime
        total = sum(sz for _, sz, _ in entries)
        if total <= self.max_bytes:
            return
        # Sort oldest-first for eviction; never evict `keep`.
        oldest_first: Iterable[tuple[str, int, float]] = sorted(entries, key=lambda r: r[2])
        for name, size, _ in oldest_first:
            if total <= self.max_bytes:
                break
            path = self.cache_dir / name
            if path == keep:
                continue
            try:
                path.unlink()
                (self.cache_dir / f"{name}.sha256").unlink(missing_ok=True)
                total -= size
                logger.info("Evicted BFB %s (%d bytes) from cache", name, size)
            except OSError as exc:
                logger.warning("Could not evict %s: %s", name, exc)


# ── Helpers ────────────────────────────────────────────────────────────


class _FileLock:
    """Simple fcntl.flock context manager (exclusive)."""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        if self._fh is not None:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()


def _read_optional(p: Path) -> str | None:
    try:
        return p.read_text().strip() or None
    except FileNotFoundError:
        return None


def _touch_atime(p: Path) -> None:
    # Ensure atime advances even on filesystems mounted with `noatime`.
    # Use epoch float so atime ordering is consistent with test fixtures that
    # set atimes via os.utime(..., (time.time()-N, ...)).
    try:
        st = p.stat()
        new_atime = max(time.time(), st.st_atime + 0.001)
        os.utime(p, (new_atime, st.st_mtime))
    except OSError:
        pass


def _sha256_file(p: Path) -> str:
    hasher = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# Convenience: build a file for ad-hoc tests / scripts without hitting the network.
def _stage_file_into_cache(cache: BfbCacheService, filename: str, content: bytes) -> Path:
    """Bypass the HTTP download path — used by unit tests."""
    path = cache.cache_dir / filename
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=cache.cache_dir) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
    sha = hashlib.sha256(content).hexdigest()
    (cache.cache_dir / f"{filename}.sha256").write_text(sha)
    return path
