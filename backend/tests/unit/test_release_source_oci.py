"""Unit tests for services.bare_metal.release_source_oci (ADR-494).

All subprocess calls are mocked — NO network access during these tests.
Security assertions:
  - credential never appears in argv
  - temp config dir is removed after the session (even on failure)
  - host selection follows source.kind (oci → repo.f5.com, mirror → url host)
"""

import base64
import json
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from services.bare_metal.release_source_oci import (
    OCI_HOST,
    OciRegistrySession,
    _detect_credential,
    _host_for,
    registry_session,
)
from services.release_source_service import _base_version, _is_prerelease

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source(kind: str = "oci", url: str | None = None, credential_encrypted: str | None = "enc") -> SimpleNamespace:
    """Minimal fake ReleaseSource ORM object."""
    return SimpleNamespace(id=1, kind=kind, url=url, credential_encrypted=credential_encrypted)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


# ---------------------------------------------------------------------------
# _host_for
# ---------------------------------------------------------------------------


class TestHostFor:
    @pytest.mark.unit
    def test_oci_kind_returns_fixed_host(self):
        source = _make_source(kind="oci")
        assert _host_for(source) == OCI_HOST

    @pytest.mark.unit
    def test_mirror_kind_parses_host_from_url(self):
        source = _make_source(kind="mirror", url="https://internal-mirror.example.com/some/path")
        assert _host_for(source) == "internal-mirror.example.com"

    @pytest.mark.unit
    def test_mirror_kind_strips_oci_scheme(self):
        source = _make_source(kind="mirror", url="oci://mirror.corp.example/release")
        assert _host_for(source) == "mirror.corp.example"

    @pytest.mark.unit
    def test_mirror_kind_no_url_falls_back_to_oci_host(self):
        source = _make_source(kind="mirror", url=None)
        assert _host_for(source) == OCI_HOST


# ---------------------------------------------------------------------------
# _detect_credential
# ---------------------------------------------------------------------------


class TestDetectCredential:
    @pytest.mark.unit
    def test_sa_key_base64_uses_json_key_base64_username(self):
        raw_key = json.dumps({"type": "service_account", "project_id": "myproj"})
        cred = _b64(raw_key)  # base64 SA key
        username, password = _detect_credential(cred)
        assert username == "_json_key_base64"
        assert password == cred

    @pytest.mark.unit
    def test_dockerconfigjson_extracts_user_password(self):
        auth_str = _b64("myuser:mypassword")
        dockerconfig = json.dumps({
            "auths": {
                "repo.f5.com": {"auth": auth_str}
            }
        })
        cred = _b64(dockerconfig)
        username, password = _detect_credential(cred)
        assert username == "myuser"
        assert password == "mypassword"

    @pytest.mark.unit
    def test_invalid_base64_falls_back_to_sa_key_shape(self):
        cred = "not-valid-base64!!!"
        username, password = _detect_credential(cred)
        assert username == "_json_key_base64"
        assert password == cred


# ---------------------------------------------------------------------------
# registry_session — credential never in argv, temp dir cleaned up
# ---------------------------------------------------------------------------


def _make_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Return a mock CompletedProcess with str stdout/stderr (as text=True produces)."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


class TestRegistrySession:
    @pytest.mark.unit
    def test_credential_never_appears_in_helm_login_argv(self):
        """The decrypted SA key must never be passed in subprocess argv."""
        raw_key = json.dumps({"type": "service_account"})
        cred_b64 = _b64(raw_key)

        # helm login subprocess uses bytes (no text=True), so mock returns bytes.
        completed_bytes = MagicMock(spec=subprocess.CompletedProcess)
        completed_bytes.returncode = 0
        completed_bytes.stdout = b""
        completed_bytes.stderr = b""

        with (
            patch(
                "services.bare_metal.release_source_oci.decrypt_value",
                return_value=cred_b64,
            ),
            patch("subprocess.run", return_value=completed_bytes) as mock_run,
        ):
            source = _make_source(kind="oci")
            with registry_session(source):
                pass

            # Inspect the helm login call
            helm_call = mock_run.call_args
            argv = helm_call[0][0]  # positional: the command list
            for arg in argv:
                assert cred_b64 not in str(arg), "credential found in argv"
            # stdin carries the password (bytes)
            assert helm_call[1].get("input") == cred_b64.encode()

    @pytest.mark.unit
    def test_temp_config_dir_removed_on_success(self):
        """Temp config dir must be removed after a successful session."""
        created_dirs: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = real_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        completed_bytes = MagicMock(spec=subprocess.CompletedProcess)
        completed_bytes.returncode = 0
        completed_bytes.stdout = b""
        completed_bytes.stderr = b""

        with (
            patch(
                "services.bare_metal.release_source_oci.decrypt_value",
                return_value=_b64(json.dumps({"type": "service_account"})),
            ),
            patch("subprocess.run", return_value=completed_bytes),
            patch(
                "services.bare_metal.release_source_oci.tempfile.mkdtemp",
                side_effect=tracking_mkdtemp,
            ),
        ):
            source = _make_source(kind="oci")
            with registry_session(source):
                pass

        for d in created_dirs:
            assert not Path(d).exists(), f"temp dir {d!r} was not cleaned up"

    @pytest.mark.unit
    def test_temp_config_dir_removed_on_failure(self):
        """Temp config dir must be removed even when the body raises."""
        created_dirs: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = real_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        completed_bytes = MagicMock(spec=subprocess.CompletedProcess)
        completed_bytes.returncode = 0
        completed_bytes.stdout = b""
        completed_bytes.stderr = b""

        with (
            patch(
                "services.bare_metal.release_source_oci.decrypt_value",
                return_value=_b64(json.dumps({"type": "service_account"})),
            ),
            patch("subprocess.run", return_value=completed_bytes),
            patch(
                "services.bare_metal.release_source_oci.tempfile.mkdtemp",
                side_effect=tracking_mkdtemp,
            ),
        ):
            source = _make_source(kind="oci")
            with pytest.raises(RuntimeError, match="body error"):
                with registry_session(source):
                    raise RuntimeError("body error")

        for d in created_dirs:
            assert not Path(d).exists(), f"temp dir {d!r} was not cleaned up after failure"

    @pytest.mark.unit
    def test_no_credential_raises(self):
        source = _make_source(kind="oci", credential_encrypted=None)
        with pytest.raises(RuntimeError, match="no stored credential"):
            with registry_session(source):
                pass

    @pytest.mark.unit
    def test_helm_login_failure_raises_and_cleans_up(self):
        failed_bytes = MagicMock(spec=subprocess.CompletedProcess)
        failed_bytes.returncode = 1
        failed_bytes.stdout = b""
        failed_bytes.stderr = b"unauthorized"
        created_dirs: list[str] = []
        real_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = real_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        with (
            patch(
                "services.bare_metal.release_source_oci.decrypt_value",
                return_value=_b64(json.dumps({"type": "service_account"})),
            ),
            patch("subprocess.run", return_value=failed_bytes),
            patch(
                "services.bare_metal.release_source_oci.tempfile.mkdtemp",
                side_effect=tracking_mkdtemp,
            ),
        ):
            with pytest.raises(RuntimeError, match="helm registry login"):
                with registry_session(_make_source(kind="oci")):
                    pass

        for d in created_dirs:
            assert not Path(d).exists()


# ---------------------------------------------------------------------------
# OciRegistrySession.list_tags
# ---------------------------------------------------------------------------


class TestListTags:
    @pytest.mark.unit
    def test_list_tags_parses_stdout_lines(self):
        sess = OciRegistrySession(host="repo.f5.com", config_dir="/tmp/fake")
        with patch(
            "subprocess.run",
            return_value=_make_completed(stdout="2.2.1-3.2226.0-0.0.511\n2.3.1-3.2598.3-0.0.304\n"),
        ):
            tags = sess.list_tags()
        assert tags == ["2.2.1-3.2226.0-0.0.511", "2.3.1-3.2598.3-0.0.304"]

    @pytest.mark.unit
    def test_list_tags_raises_on_nonzero_exit(self):
        sess = OciRegistrySession(host="repo.f5.com", config_dir="/tmp/fake")
        with patch(
            "subprocess.run",
            return_value=_make_completed(returncode=1, stderr="connection refused"),
        ):
            with pytest.raises(RuntimeError, match="oras repo tags failed"):
                sess.list_tags()

    @pytest.mark.unit
    def test_list_tags_uses_registry_config_flag(self):
        sess = OciRegistrySession(host="repo.f5.com", config_dir="/tmp/fake-cfg")
        with patch("subprocess.run", return_value=_make_completed(stdout="2.2.1\n")) as mock_run:
            sess.list_tags()
        argv = mock_run.call_args[0][0]
        assert "--registry-config" in argv
        assert "/tmp/fake-cfg/config.json" in argv


# ---------------------------------------------------------------------------
# OciRegistrySession.pull_manifest_yaml
# ---------------------------------------------------------------------------


class TestPullManifestYaml:
    @pytest.mark.unit
    def test_pull_manifest_yaml_reads_yaml_file(self, tmp_path):
        """Pull should return the contents of the manifest yaml found in workdir."""
        manifest_content = "releases:\n  - version: '2.2.1-test'\n"
        tag = "2.2.1-test"

        def fake_helm_pull(argv, **kwargs):
            # Simulate helm creating an untarred dir with a manifest yaml
            dest = None
            for i, arg in enumerate(argv):
                if arg == "--destination" and i + 1 < len(argv):
                    dest = argv[i + 1]
            if dest:
                chart_dir = Path(dest) / "f5-bigip-k8s-manifest"
                chart_dir.mkdir(parents=True, exist_ok=True)
                (chart_dir / f"f5-bigip-k8s-manifest-{tag}.yaml").write_text(manifest_content)
            return _make_completed()

        sess = OciRegistrySession(host="repo.f5.com", config_dir=str(tmp_path))
        with patch("subprocess.run", side_effect=fake_helm_pull):
            result = sess.pull_manifest_yaml(tag)

        assert result == manifest_content

    @pytest.mark.unit
    def test_pull_manifest_yaml_raises_on_nonzero_exit(self, tmp_path):
        sess = OciRegistrySession(host="repo.f5.com", config_dir=str(tmp_path))
        with patch("subprocess.run", return_value=_make_completed(returncode=1, stderr=b"not found")):
            with pytest.raises(RuntimeError, match="helm pull"):
                sess.pull_manifest_yaml("2.2.1-test")

    @pytest.mark.unit
    def test_pull_manifest_yaml_raises_when_no_yaml_found(self, tmp_path):
        def fake_pull(argv, **kwargs):
            return _make_completed()

        sess = OciRegistrySession(host="repo.f5.com", config_dir=str(tmp_path))
        with patch("subprocess.run", side_effect=fake_pull):
            with pytest.raises(RuntimeError, match="No manifest YAML found"):
                sess.pull_manifest_yaml("2.2.1-test")


# ---------------------------------------------------------------------------
# _is_prerelease / _base_version — F5 real tag grammar (Fix ADR-494 audit)
# ---------------------------------------------------------------------------


class TestIsPrerelease:
    """Unit tests for the F5 OCI tag prerelease heuristic.

    Real F5 tag grammar:
      Stable:     x.y.z-<digit-starting-build-id>[-optional-rest]
      Prerelease: x.y.z-<letter-starting-first-segment>
      Plain:      x.y.z (no suffix — always stable)

    NOTE: tags of the form x.y.z-<digit>-<letter-word>-... (e.g.
    "2.1.0-3.1736.2-ready-prod.15573925") have a digit-starting first
    post-base segment and therefore classify as stable under this rule.
    Such tags are rare; the common prerelease pattern is x.y.z-<word>.
    """

    @pytest.mark.unit
    def test_stable_full_build_tag(self):
        assert _is_prerelease("2.2.1-3.2226.0-0.0.511") is False

    @pytest.mark.unit
    def test_stable_release_version_pipeline_tag(self):
        assert _is_prerelease("2.4.0-3.2981.1-release-version.17861144") is False

    @pytest.mark.unit
    def test_stable_plain_version(self):
        assert _is_prerelease("2.3.0") is False

    @pytest.mark.unit
    def test_prerelease_laiq_label(self):
        assert _is_prerelease("2.4.0-laiq") is True

    @pytest.mark.unit
    def test_prerelease_ready_prod_label(self):
        # "ready-prod" IS the first post-base segment (no build-id prefix).
        assert _is_prerelease("2.1.0-ready-prod.15573925") is True

    @pytest.mark.unit
    def test_prerelease_semver_rc(self):
        assert _is_prerelease("2.4.0-rc.1") is True

    @pytest.mark.unit
    def test_prerelease_semver_alpha(self):
        assert _is_prerelease("3.0.0-alpha.1") is True

    @pytest.mark.unit
    def test_stable_tag_with_digit_first_segment_and_letter_later(self):
        # First post-base segment "3.1736.2" starts with digit → stable
        # even though a later segment contains letters.
        assert _is_prerelease("2.1.0-3.1736.2-ready-prod.15573925") is False

    @pytest.mark.unit
    def test_trailing_hyphen_returns_false_not_raises(self):
        # "2.2.1-" splits to ["2.2.1", ""] → first_post is "" → no IndexError, stable
        assert _is_prerelease("2.2.1-") is False

    @pytest.mark.unit
    def test_base_version_extracts_leading_xyz(self):
        assert _base_version("2.2.1-3.2226.0-0.0.511").major == 2
        assert _base_version("2.2.1-3.2226.0-0.0.511").minor == 2

    @pytest.mark.unit
    def test_base_version_fallback_on_invalid(self):
        from packaging.version import Version
        assert _base_version("not-a-version-at-all") == Version("0.0.0")
