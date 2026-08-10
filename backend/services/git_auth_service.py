"""Shared Git authentication resolution + secret-safe transport helpers."""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from base64 import b64encode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from core.encryption import decrypt_value_or_none
from models import ApplicationSetting, ModuleSource
from services.github_app_auth_service import GitHubAppAuthService

TOKEN_LIKE_CREDENTIAL_TYPES = {
    "legacy_pat",
    "oauth_token",
    "service_account_token",
    "gitlab_project_token",
    "gitlab_group_token",
    "gitlab_deploy_token",
    "github_app",
}


@dataclass
class GitAuthContext:
    transport: str  # none | https_token | ssh_key
    credential_type: str
    secret: str | None = None
    username: str | None = None
    ssh_known_hosts: str | None = None

    @property
    def has_secret(self) -> bool:
        return bool(self.secret)


class GitAuthService:
    """Resolve non-secret metadata + secrets into a runtime auth context."""

    @classmethod
    def resolve_for_module_source(cls, source: ModuleSource, db: Any | None = None) -> GitAuthContext:
        credential_type = cls._derive_credential_type(source)
        secret = decrypt_value_or_none(source.auth_token_encrypted) if source.auth_token_encrypted else None

        metadata = source.credential_metadata if isinstance(source.credential_metadata, dict) else {}
        nested_gitlab = metadata.get("gitlab") if isinstance(metadata.get("gitlab"), dict) else {}
        username = str(
            metadata.get("username")
            or nested_gitlab.get("deploy_username")
            or nested_gitlab.get("username")
            or ""
        ).strip() or None

        if credential_type == "github_app":
            if not secret:
                raise RuntimeError("GitHub App private key is not configured for source")
            token, token_expires_at = GitHubAppAuthService.acquire_installation_token(
                private_key=secret,
                metadata=metadata,
            )
            if db is not None:
                try:
                    source.credential_expires_at = cls._parse_datetime_or_none(token_expires_at)
                    source.credential_validation_error = None
                    db.flush()
                except Exception:
                    # Auth resolution should not fail due to metadata timestamp persistence errors.
                    pass
            return GitAuthContext(
                transport="https_token",
                credential_type=credential_type,
                secret=token,
                username="x-access-token",
            )

        if credential_type == "ssh_deploy_key":
            ssh_metadata = metadata.get("ssh") if isinstance(metadata.get("ssh"), dict) else {}
            known_hosts_value = ssh_metadata.get("known_hosts")
            known_hosts_text = cls.normalize_known_hosts(known_hosts_value)
            return GitAuthContext(
                transport="ssh_key",
                credential_type=credential_type,
                secret=secret,
                ssh_known_hosts=known_hosts_text,
            )

        if credential_type in TOKEN_LIKE_CREDENTIAL_TYPES and secret:
            default_username = "git"
            if credential_type == "gitlab_deploy_token":
                default_username = "oauth2"
            return GitAuthContext(
                transport="https_token",
                credential_type=credential_type,
                secret=secret,
                username=username or default_username,
            )

        return GitAuthContext(transport="none", credential_type=credential_type)

    @classmethod
    def resolve_for_module_library_token_setting(cls, db) -> GitAuthContext:
        pat_setting = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == "module_library.git_token"
        ).first()
        token = ""
        if pat_setting and pat_setting.value:
            token = pat_setting.value
            if pat_setting.is_encrypted:
                token = decrypt_value_or_none(token) or token

        token = token.strip()
        if token.lower() in {"", "change_me", "your_token_here", "placeholder"}:
            token = ""

        if token:
            return GitAuthContext(
                transport="https_token",
                credential_type="legacy_pat",
                secret=token,
                username="git",
            )

        return GitAuthContext(transport="none", credential_type="none")

    @staticmethod
    def strip_url_credentials(url: str) -> str:
        """Remove userinfo component from URL if present."""
        try:
            parsed = urlsplit(url)
        except Exception:
            return url

        if not parsed.netloc or "@" not in parsed.netloc:
            return url

        netloc = parsed.netloc.rsplit("@", 1)[1]
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    @classmethod
    def classify_http_failure(cls, status_code: int, response_text: str = "") -> tuple[str, str]:
        text = (response_text or "").lower()

        if status_code == 404:
            return "repo_or_ref_not_found", "Repository or requested ref was not found"

        if status_code in {401, 403}:
            if "rate limit" in text:
                return "auth_failure", "Remote service rate limit reached or auth scope is insufficient"
            return "auth_failure", "HTTP authentication/authorization failed"

        if 500 <= status_code < 600:
            return "remote_service_error", "Remote Git service returned a server error"

        return "unknown_http_error", f"Remote Git service returned status {status_code}"

    @staticmethod
    def build_http_headers(auth: GitAuthContext, request_url: str) -> dict[str, str]:
        if auth.transport != "https_token" or not auth.secret:
            return {}

        if auth.credential_type in {"gitlab_project_token", "gitlab_group_token"}:
            return {"PRIVATE-TOKEN": auth.secret}
        if auth.credential_type == "gitlab_deploy_token":
            username = auth.username or "oauth2"
            token = b64encode(f"{username}:{auth.secret}".encode()).decode("utf-8")
            return {"Authorization": f"Basic {token}"}

        lower_url = request_url.lower()
        if "github.com" in lower_url or "raw.githubusercontent.com" in lower_url:
            return {"Authorization": f"token {auth.secret}"}
        return {"Authorization": f"Bearer {auth.secret}"}

    @staticmethod
    def classify_git_failure(error_text: str) -> tuple[str, str]:
        text = (error_text or "").lower()

        if any(msg in text for msg in [
            "could not resolve host",
            "failed to connect",
            "connection timed out",
            "name or service not known",
            "connection refused",
            "network is unreachable",
            "no route to host",
            "operation timed out",
        ]):
            return "connectivity_dns", "Git host is unreachable or DNS resolution failed from runtime network"

        if any(msg in text for msg in ["ssl certificate problem", "tls", "certificate verify failed", "x509"]):
            return "tls_trust", "TLS trust validation failed when connecting to Git host"

        if any(msg in text for msg in [
            "host key verification failed",
            "remote host identification has changed",
            "no matching host key type found",
            "host key mismatch",
            "offending",
        ]):
            return "ssh_host_key", "SSH host-key verification failed"

        if "permission denied (publickey)" in text:
            return "ssh_auth", "SSH authentication failed for the configured key"

        if any(msg in text for msg in ["authentication failed", "could not read username", "access denied", "authorization failed", "http basic: access denied"]):
            return "auth_failure", "Git authentication/authorization failed"

        if any(msg in text for msg in ["repository not found", "remote branch", "couldn't find remote ref", "not found"]):
            return "repo_or_ref_not_found", "Repository or requested ref was not found"

        return "unknown", "Git operation failed"

    @staticmethod
    def sanitize_error_text(text: str, secrets: list[str] | None = None) -> str:
        sanitized = text or ""

        # Strip credentials from URL patterns.
        sanitized = re.sub(r"(https?://)[^/\s@]+@", r"\1***@", sanitized)
        sanitized = re.sub(r"(ssh://)[^/\s@]+@", r"\1***@", sanitized)
        sanitized = re.sub(r"\b[^\s:@/]+@[^\s:/]+:", "***@***:", sanitized)
        sanitized = re.sub(r"([?&](?:access_token|token|auth|password)=)[^&\s]+", r"\1***", sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r"(authorization:\s*(?:token|bearer)\s+)[^\s]+", r"\1***", sanitized, flags=re.IGNORECASE)

        for secret in secrets or []:
            if secret:
                sanitized = sanitized.replace(secret, "***")

        return sanitized

    @classmethod
    def build_git_environment(cls, auth: GitAuthContext, base_env: dict[str, str] | None = None) -> tuple[dict[str, str], Callable[[], None]]:
        env = dict(base_env or os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"
        # Bound stalled HTTP git operations: abort if transfer drops below 1 KB/s
        # for 30 consecutive seconds. Prevents a blackholed git host from hanging
        # the boot path indefinitely (applies to both clone and fetch operations).
        env["GIT_HTTP_LOW_SPEED_LIMIT"] = "1000"
        env["GIT_HTTP_LOW_SPEED_TIME"] = "30"

        temp_dir = tempfile.mkdtemp(prefix="git-auth-")

        def _cleanup() -> None:
            shutil.rmtree(temp_dir, ignore_errors=True)

        if auth.transport == "https_token" and auth.secret:
            askpass_path = os.path.join(temp_dir, "askpass.sh")
            with open(askpass_path, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
                f.write("case \"$1\" in\n")
                f.write("  *sername*) echo \"$GIT_AUTH_USERNAME\" ;;\n")
                f.write("  *) echo \"$GIT_AUTH_PASSWORD\" ;;\n")
                f.write("esac\n")
            os.chmod(askpass_path, 0o700)

            env["GIT_ASKPASS"] = askpass_path
            env["GIT_AUTH_USERNAME"] = auth.username or "git"
            env["GIT_AUTH_PASSWORD"] = auth.secret
            return env, _cleanup

        if auth.transport == "ssh_key" and auth.secret:
            key_path = os.path.join(temp_dir, "id_rsa")
            with open(key_path, "w", encoding="utf-8") as f:
                f.write(auth.secret)
            os.chmod(key_path, 0o600)

            known_hosts = (auth.ssh_known_hosts or "").strip()
            if not known_hosts:
                _cleanup()
                raise RuntimeError(
                    "SSH known_hosts trust material is required for strict host-key verification"
                )

            known_hosts_path = os.path.join(temp_dir, "known_hosts")
            with open(known_hosts_path, "w", encoding="utf-8") as f:
                f.write(f"{known_hosts}\n")
            os.chmod(known_hosts_path, 0o600)

            ssh_wrapper = os.path.join(temp_dir, "git_ssh.sh")
            with open(ssh_wrapper, "w", encoding="utf-8") as f:
                f.write("#!/bin/sh\n")
                f.write(
                    "exec ssh "
                    f'-i "{key_path}" '
                    "-o BatchMode=yes "
                    "-o IdentitiesOnly=yes "
                    "-o StrictHostKeyChecking=yes "
                    "-o GlobalKnownHostsFile=/dev/null "
                    f'-o UserKnownHostsFile="{known_hosts_path}" '
                    '"$@"\n'
                )
            os.chmod(ssh_wrapper, 0o700)
            env["GIT_SSH"] = ssh_wrapper
            env.pop("GIT_SSH_COMMAND", None)

        return env, _cleanup

    @staticmethod
    def _derive_credential_type(source: ModuleSource) -> str:
        if source.credential_type:
            return source.credential_type

        legacy_auth = (source.auth_type or "none").lower()
        if legacy_auth == "ssh":
            return "ssh_deploy_key"
        if legacy_auth == "token":
            return "legacy_pat"
        if source.auth_token_encrypted:
            return "legacy_pat"
        return "none"

    @staticmethod
    def _parse_datetime_or_none(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def normalize_known_hosts(value: Any) -> str | None:
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or None
        if isinstance(value, list):
            entries = [str(item).strip() for item in value if str(item).strip()]
            return "\n".join(entries) if entries else None
        return None

    @staticmethod
    def parse_ssh_git_target(source_url: str) -> tuple[str, str] | None:
        """Parse conservative SSH Git URL formats and return (host, repo_path)."""
        url = (source_url or "").strip()
        if not url:
            return None

        # Conservative support for standard ssh:// URLs.
        if url.startswith("ssh://"):
            parsed = urlsplit(url)
            if parsed.scheme != "ssh" or not parsed.hostname:
                return None
            if parsed.query or parsed.fragment:
                return None
            if parsed.username and "@" in parsed.username:
                return None
            path = parsed.path.strip("/")
            if not path:
                return None
            if any(ch.isspace() for ch in path):
                return None
            host = parsed.hostname.strip().lower()
            if not host or any(ch.isspace() for ch in host):
                return None
            return host, path

        # Conservative support for SCP-style URLs: user@host:path
        if url.startswith(("http://", "https://")) or "://" in url:
            return None
        if url.count("@") != 1:
            return None
        user_host, sep, path = url.partition(":")
        if not sep:
            return None
        user, _, host = user_host.partition("@")
        user = user.strip()
        host = host.strip().lower()
        repo_path = path.strip().strip("/")
        if not user or not host or not repo_path:
            return None
        if any(ch.isspace() for ch in user) or any(ch.isspace() for ch in host) or any(ch.isspace() for ch in repo_path):
            return None
        if "@" in host or ":" in host:
            return None
        return host, repo_path
