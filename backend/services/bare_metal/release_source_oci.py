"""OCI registry session helper for ReleaseSource live-fetch (ADR-494).

Provides a context manager that logs in to the OCI/mirror registry ONCE per
call, reusing a temporary registry config file across list + pull operations,
and cleaning it up in the finally block.

Security contract:
- The decrypted credential is passed via subprocess stdin, never via argv or
  environment variables (multi-threaded uvicorn → concurrent-sync collision if
  os.environ is mutated).
- The temp config dir is removed in the finally block regardless of outcome.
- The credential is never included in log messages or error strings returned
  to API callers.
- Per-call tempfile.mkdtemp() ensures uniqueness across concurrent requests.

Credential shape detection (mirrors bnk_ssh_base.py:400-421):
  - If the stored credential (after decryption) is a base64 string that decodes
    to JSON containing "auths" → it is a dockerconfigjson blob; extract user:pass.
  - Otherwise → treat as a raw base64-encoded GCP SA key; use
    username="_json_key_base64" with the base64 blob as password.
"""

import base64
import json
import logging
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from core.encryption import decrypt_value  # module-level import for patchability

if TYPE_CHECKING:
    from models.release_source import ReleaseSource

logger = logging.getLogger(__name__)

# Fixed registry host for OCI-kind sources (repo.f5.com).
OCI_HOST = "repo.f5.com"

# OCI repo path for the BNK manifest chart — same for oci and mirror kinds.
MANIFEST_REPO_PATH = "release/f5-bigip-k8s-manifest"

# Subprocess timeout (seconds) for network operations.
_LOGIN_TIMEOUT = 60
_TAGS_TIMEOUT = 60
_PULL_TIMEOUT = 180


def _host_for(source: "ReleaseSource") -> str:
    """Return the registry hostname for the given source.

    oci → fixed OCI_HOST ("repo.f5.com").
    mirror → parse host from source.url (strip scheme and path components).
    """
    if source.kind == "oci":
        return OCI_HOST
    url = (source.url or "").strip()
    for prefix in ("oci://", "https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    return url.split("/")[0] or OCI_HOST


def _detect_credential(cred: str) -> tuple[str, str]:
    """Return (username, password) for helm registry login.

    Two credential shapes:
    1. Base64 SA key JSON → username="_json_key_base64", password=cred (the
       raw base64 string, not decoded — helm expects base64 on stdin).
    2. Base64-encoded dockerconfigjson with "auths" → extract user:pass from
       the first matching "auth" entry.

    Falls back to the SA key shape on any decoding / parse error.
    """
    try:
        # Pad the base64 string to avoid binascii.Error on missing padding.
        decoded_bytes = base64.b64decode(cred + "==")
        decoded_str = decoded_bytes.decode("utf-8")
        if '"auths"' in decoded_str:
            data = json.loads(decoded_str)
            for _host_key, auth_data in data.get("auths", {}).items():
                if "auth" in auth_data:
                    user_pass = base64.b64decode(auth_data["auth"]).decode("utf-8")
                    username, _, password = user_pass.partition(":")
                    return username, password
    except Exception:
        pass  # Fall through to SA key shape.

    # Raw base64 SA key: helm accepts it as the password with _json_key_base64 user.
    return "_json_key_base64", cred


class OciRegistrySession:
    """Single-login session against an OCI registry.

    Do not instantiate directly — use registry_session() instead.
    """

    def __init__(self, host: str, config_dir: str) -> None:
        self._host = host
        self._config_file = str(Path(config_dir) / "config.json")

    def list_tags(self) -> list[str]:
        """List tags for the manifest repo via oras.

        Returns raw tag strings (verbatim from the registry).
        Raises RuntimeError on oras failure.
        """
        result = subprocess.run(
            [
                "oras",
                "repo",
                "tags",
                "--registry-config",
                self._config_file,
                f"{self._host}/{MANIFEST_REPO_PATH}",
            ],
            capture_output=True,
            text=True,
            timeout=_TAGS_TIMEOUT,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"oras repo tags failed (exit {result.returncode}): {result.stderr[:300]}"
            )
        return [t.strip() for t in result.stdout.strip().splitlines() if t.strip()]

    def pull_manifest_yaml(self, tag: str) -> str:
        """Pull the manifest Helm chart for *tag* and return the manifest YAML text.

        Uses helm pull --untar into a per-call temp dir, then finds the manifest
        YAML file (not Chart.yaml / values.yaml) and returns its content.

        Raises RuntimeError if the pull fails or no manifest YAML is found.
        """
        workdir = tempfile.mkdtemp(prefix="bnk-manifest-")
        try:
            result = subprocess.run(
                [
                    "helm",
                    "pull",
                    f"oci://{self._host}/{MANIFEST_REPO_PATH}",
                    "--version",
                    tag,
                    "--registry-config",
                    self._config_file,
                    "--untar",
                    "--destination",
                    workdir,
                ],
                capture_output=True,
                text=True,
                timeout=_PULL_TIMEOUT,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"helm pull {tag!r} failed (exit {result.returncode}): {result.stderr[:300]}"
                )

            # Locate the manifest YAML: prefer files with "manifest" in the name,
            # excluding Chart.yaml and values.yaml (chart metadata, not release data).
            # Both lists are sorted so file selection is deterministic across filesystems.
            _excluded = {"Chart.yaml", "values.yaml"}
            candidates = sorted(
                p
                for p in Path(workdir).rglob("*.yaml")
                if p.name not in _excluded and "manifest" in p.name.lower()
            )
            if not candidates:
                # Fallback: any yaml that is not chart metadata.
                candidates = sorted(
                    p for p in Path(workdir).rglob("*.yaml") if p.name not in _excluded
                )
            if not candidates:
                raise RuntimeError(
                    f"No manifest YAML found in chart for tag {tag!r}"
                )

            return candidates[0].read_text()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


@contextmanager
def registry_session(source: "ReleaseSource") -> Generator[OciRegistrySession, None, None]:
    """Context manager: decrypt credential, login once, yield a session, cleanup.

    Usage::

        with registry_session(source) as sess:
            tags = sess.list_tags()
            yaml = sess.pull_manifest_yaml("2.2.1-3.2226.0-0.0.511")

    The temp config dir (holding config.json with the registry token) is
    removed in the finally block regardless of outcome.
    """
    host = _host_for(source)
    config_dir = tempfile.mkdtemp(prefix="bnk-registry-")
    try:
        if not source.credential_encrypted:
            raise RuntimeError(
                f"Release source {source.id!r} has no stored credential"
            )

        cred = decrypt_value(source.credential_encrypted)
        if not cred:
            raise RuntimeError(
                f"Credential decryption returned empty value for source {source.id!r}"
            )

        username, password = _detect_credential(cred)

        config_file = str(Path(config_dir) / "config.json")
        result = subprocess.run(
            [
                "helm",
                "registry",
                "login",
                "--registry-config",
                config_file,
                "-u",
                username,
                "--password-stdin",
                host,
            ],
            input=password.encode(),
            capture_output=True,
            timeout=_LOGIN_TIMEOUT,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:300]
            raise RuntimeError(
                f"helm registry login to {host!r} failed (exit {result.returncode}): {stderr}"
            )

        yield OciRegistrySession(host=host, config_dir=config_dir)
    finally:
        # Remove the temp config dir — no credential lingers on disk.
        shutil.rmtree(config_dir, ignore_errors=True)
