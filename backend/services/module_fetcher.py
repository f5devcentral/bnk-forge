"""Fetch module sources from registries and public git URLs.

All fetchers return a uniform `FetchResult` so downstream code (catalog writer,
variable scraper, pack synthesizer) doesn't need to care which kind of source
the bytes came from.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

OPENTOFU_REGISTRY = "https://registry.opentofu.org/v1"
TERRAFORM_REGISTRY = "https://registry.terraform.io/v1"
DEFAULT_TIMEOUT = 30
USER_AGENT = "BNK-Forge/2.0"


@dataclass(frozen=True)
class FetchResult:
    tarball_bytes: bytes
    sha256: str
    canonical_url: str
    upstream_version: str
    extracted_root: str = ""  # Path inside the tarball that holds the actual module (subpath, etc.)


class ModuleFetchError(RuntimeError):
    """Raised when a registry or git source can't be fetched."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _http_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _resolve_registry_download_url(
    base: str,
    namespace: str,
    name: str,
    provider: str,
    version: str,
    *,
    auth_header: dict[str, str] | None = None,
) -> str:
    """Hit /modules/<ns>/<name>/<provider>/<version>/download and follow the X-Terraform-Get header."""
    url = f"{base}/modules/{namespace}/{name}/{provider}/{version}/download"
    session = _http_session()
    if auth_header:
        session.headers.update(auth_header)
    resp = session.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=False)
    if resp.status_code == 204 and "X-Terraform-Get" in resp.headers:
        return resp.headers["X-Terraform-Get"]
    if resp.status_code in (301, 302, 303, 307):
        return resp.headers["Location"]
    if resp.status_code == 200 and "X-Terraform-Get" in resp.headers:
        return resp.headers["X-Terraform-Get"]
    raise ModuleFetchError(
        f"Unexpected response from registry download endpoint {url}: {resp.status_code}"
    )


def _download_archive(url: str, *, auth_header: dict[str, str] | None = None) -> bytes:
    """Download a tarball or zip from a fully-resolved URL.

    Terraform-style `git::` URLs are normalized to plain https for fetching.
    """
    if url.startswith("git::"):
        url = url[len("git::"):]
    if "?" in url and url.split("?", 1)[1].startswith("ref="):
        # Registries return git URLs with `?ref=tag`; honor that as the git ref.
        repo_url, qs = url.split("?", 1)
        ref = qs.split("=", 1)[1]
        return _git_archive(repo_url, ref, subpath=None)

    session = _http_session()
    if auth_header:
        session.headers.update(auth_header)
    resp = session.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _git_archive(url: str, ref: str, subpath: str | None) -> bytes:
    """Shallow-clone a public git repo at `ref` and return a tar.gz of the working tree.

    `git archive --remote` would be cheaper but most public hosts (GitHub, GitLab) disable
    it, so we fall back to a shallow clone.
    """
    with tempfile.TemporaryDirectory(prefix="bnk-forge-git-") as tmp:
        clone_dir = Path(tmp) / "repo"
        try:
            subprocess.run(
                [
                    "git", "clone", "--depth", "1", "--branch", ref, "--single-branch",
                    url, str(clone_dir),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except subprocess.CalledProcessError as exc:
            # `--branch` only takes named refs; commits need a follow-up checkout.
            try:
                subprocess.run(
                    ["git", "clone", url, str(clone_dir)],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                subprocess.run(
                    ["git", "-C", str(clone_dir), "checkout", ref],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            except subprocess.CalledProcessError as exc2:
                raise ModuleFetchError(
                    f"git clone {url}@{ref} failed: {exc2.stderr.decode(errors='replace')}"
                ) from exc

        target_dir = clone_dir if not subpath else (clone_dir / subpath)
        if not target_dir.is_dir():
            raise ModuleFetchError(f"subpath {subpath!r} does not exist in {url}@{ref}")

        # Strip .git so it doesn't bloat the tarball.
        shutil.rmtree(clone_dir / ".git", ignore_errors=True)

        buf = BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(target_dir, arcname=".", recursive=True)
        return buf.getvalue()


# ---------------------------------------------------------------------------
# Public fetchers
# ---------------------------------------------------------------------------


def fetch_terraform_registry(
    namespace: str, name: str, provider: str, version: str
) -> FetchResult:
    download_url = _resolve_registry_download_url(
        TERRAFORM_REGISTRY, namespace, name, provider, version
    )
    data = _download_archive(download_url)
    return FetchResult(
        tarball_bytes=data,
        sha256=_sha256(data),
        canonical_url=f"{TERRAFORM_REGISTRY}/modules/{namespace}/{name}/{provider}/{version}",
        upstream_version=version,
    )


def fetch_opentofu_registry(
    namespace: str, name: str, provider: str, version: str
) -> FetchResult:
    download_url = _resolve_registry_download_url(
        OPENTOFU_REGISTRY, namespace, name, provider, version
    )
    data = _download_archive(download_url)
    return FetchResult(
        tarball_bytes=data,
        sha256=_sha256(data),
        canonical_url=f"{OPENTOFU_REGISTRY}/modules/{namespace}/{name}/{provider}/{version}",
        upstream_version=version,
    )


def fetch_tfc_private(
    hostname: str,
    namespace: str,
    name: str,
    provider: str,
    version: str,
    token: str,
) -> FetchResult:
    base = f"https://{hostname}/api/registry/v1"
    auth = {"Authorization": f"Bearer {token}"}
    download_url = _resolve_registry_download_url(
        base, namespace, name, provider, version, auth_header=auth
    )
    data = _download_archive(download_url, auth_header=auth)
    return FetchResult(
        tarball_bytes=data,
        sha256=_sha256(data),
        canonical_url=f"{base}/modules/{namespace}/{name}/{provider}/{version}",
        upstream_version=version,
    )


def fetch_public_git(url: str, ref: str, subpath: str | None = None) -> FetchResult:
    data = _git_archive(url, ref, subpath)
    canonical = url
    if subpath:
        canonical = f"{url}//{subpath}"
    canonical = f"{canonical}?ref={ref}"
    return FetchResult(
        tarball_bytes=data,
        sha256=_sha256(data),
        canonical_url=canonical,
        upstream_version=ref,
        extracted_root=subpath or "",
    )


def verify_sha256(data: bytes, expected: str) -> None:
    actual = _sha256(data)
    if actual != expected:
        raise ModuleFetchError(
            f"Tarball SHA256 mismatch: expected {expected}, got {actual}"
        )
