"""
Unit tests for services/kubeconfig_normalizer.py.

Covers:
  - Every fixture in backend/tests/unit/fixtures/kubeconfigs/ × every NormalizationSource
  - Precedence rule: *-data wins over path (silent drop)
  - Multi-cluster: ALL clusters must be portable, not just current-context
  - Exec allowlist
  - Malformed YAML
  - scrub_secrets does not leak client-key-data into 422 body
"""
import base64
from pathlib import Path

import pytest
import yaml

from services.kubeconfig_normalizer import (
    SUPPORTED_EXEC_COMMANDS,
    KubeconfigUnportableError,
    NormalizationSource,
    normalize_kubeconfig,
)

FIXTURES = Path(__file__).parent / "fixtures" / "kubeconfigs"

ALL_SOURCES = list(NormalizationSource)


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# ── Flat (already portable) fixtures ────────────────────────────────────────


class TestFlatEks:
    """flat_eks.yaml — inlined CA, aws exec plugin → succeeds for all sources."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_passes_all_sources(self, source):
        result = normalize_kubeconfig(load_fixture("flat_eks.yaml"), source=source)
        parsed = yaml.safe_load(result)
        cluster = parsed["clusters"][0]["cluster"]
        assert "certificate-authority-data" in cluster
        assert "certificate-authority" not in cluster

    def test_exec_command_preserved(self):
        result = normalize_kubeconfig(
            load_fixture("flat_eks.yaml"), source=NormalizationSource.MANUAL_UPLOAD
        )
        parsed = yaml.safe_load(result)
        user = parsed["users"][0]["user"]
        assert user["exec"]["command"] == "aws"


class TestFlatGke:
    """flat_gke.yaml — inlined CA, gke-gcloud-auth-plugin → succeeds for all sources."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_passes_all_sources(self, source):
        result = normalize_kubeconfig(load_fixture("flat_gke.yaml"), source=source)
        parsed = yaml.safe_load(result)
        cluster = parsed["clusters"][0]["cluster"]
        assert "certificate-authority-data" in cluster
        assert "certificate-authority" not in cluster


# ── Unportable fixtures — path refs ─────────────────────────────────────────


class TestPathCa:
    """path_ca.yaml — certificate-authority: /path → KubeconfigUnportableError."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_raises_for_all_sources(self, source):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(load_fixture("path_ca.yaml"), source=source)
        err = exc_info.value
        assert err.details["field"] == "certificate-authority"
        assert err.status_code == 422
        assert err.code == "KUBECONFIG_UNPORTABLE"

    def test_user_message_contains_path(self):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(
                load_fixture("path_ca.yaml"), source=NormalizationSource.MANUAL_UPLOAD
            )
        assert "/etc/pki/ca-trust" in exc_info.value.details["user_message"]

    def test_internal_reread_message_says_reupload(self):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(
                load_fixture("path_ca.yaml"), source=NormalizationSource.INTERNAL_REREAD
            )
        assert "re-upload" in exc_info.value.details["user_message"].lower()


class TestPathClientCert:
    """path_client_cert.yaml — client-certificate: /path, client-key-data present."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_raises_for_all_sources(self, source):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(load_fixture("path_client_cert.yaml"), source=source)
        assert exc_info.value.details["field"] == "client-certificate"


class TestPathClientKey:
    """path_client_key.yaml — client-certificate-data present, client-key: /path."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_raises_for_all_sources(self, source):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(load_fixture("path_client_key.yaml"), source=source)
        assert exc_info.value.details["field"] == "client-key"


class TestPathTokenFile:
    """path_token_file.yaml — tokenFile: /path → KubeconfigUnportableError."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_raises_for_all_sources(self, source):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(load_fixture("path_token_file.yaml"), source=source)
        assert exc_info.value.details["field"] == "tokenFile"


# ── exec allowlist ───────────────────────────────────────────────────────────


class TestExecEks:
    """exec_eks.yaml — aws-iam-authenticator exec → passes."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_passes_all_sources(self, source):
        result = normalize_kubeconfig(load_fixture("exec_eks.yaml"), source=source)
        parsed = yaml.safe_load(result)
        user = parsed["users"][0]["user"]
        assert user["exec"]["command"] == "aws-iam-authenticator"


class TestExecGke:
    """exec_gke.yaml — gke-gcloud-auth-plugin exec → passes."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_passes_all_sources(self, source):
        normalize_kubeconfig(load_fixture("exec_gke.yaml"), source=source)


class TestExecAks:
    """exec_aks.yaml — kubelogin exec → passes (rewritten later in engine router)."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_passes_all_sources(self, source):
        result = normalize_kubeconfig(load_fixture("exec_aks.yaml"), source=source)
        parsed = yaml.safe_load(result)
        user = parsed["users"][0]["user"]
        assert user["exec"]["command"] == "kubelogin"


class TestExecUnknown:
    """exec_unknown.yaml — unknown exec command → KubeconfigUnportableError."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_raises_for_all_sources(self, source):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(load_fixture("exec_unknown.yaml"), source=source)
        err = exc_info.value
        assert err.details["field"] == "exec.command"
        assert "unknown-auth-plugin" in err.details["user_message"]
        for cmd in SUPPORTED_EXEC_COMMANDS:
            assert cmd in err.details["user_message"]


# ── Precedence rule ──────────────────────────────────────────────────────────


class TestMixedDataAndPath:
    """mixed_data_and_path.yaml — both *-data and path present → data wins, path dropped."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_passes_and_drops_paths(self, source):
        result = normalize_kubeconfig(
            load_fixture("mixed_data_and_path.yaml"), source=source
        )
        parsed = yaml.safe_load(result)
        cluster = parsed["clusters"][0]["cluster"]
        assert "certificate-authority-data" in cluster
        assert "certificate-authority" not in cluster

        user = parsed["users"][0]["user"]
        assert "client-certificate-data" in user
        assert "client-certificate" not in user
        assert "client-key-data" in user
        assert "client-key" not in user


# ── Multi-cluster: ALL clusters checked, not just current-context ────────────


class TestMultiClusterPartial:
    """multi_cluster_partial.yaml — good-cluster is current-context; bad-cluster has path CA.

    Normalizer must check ALL clusters, not just current-context → raises 422.
    """

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_raises_on_bad_cluster_even_if_not_current_context(self, source):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(
                load_fixture("multi_cluster_partial.yaml"), source=source
            )
        assert exc_info.value.details["field"] == "certificate-authority"


# ── Malformed YAML ────────────────────────────────────────────────────────────


class TestMalformed:
    """malformed.yaml — invalid YAML → KubeconfigUnportableError (not a crash)."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_raises_for_all_sources(self, source):
        with pytest.raises(KubeconfigUnportableError) as exc_info:
            normalize_kubeconfig(load_fixture("malformed.yaml"), source=source)
        assert exc_info.value.details["field"] == "kubeconfig"


# ── Non-dict YAML ─────────────────────────────────────────────────────────────


class TestNonDict:
    """YAML that parses to a non-dict (e.g. a list) → KubeconfigUnportableError."""

    @pytest.mark.parametrize("source", ALL_SOURCES)
    def test_raises_for_list(self, source):
        with pytest.raises(KubeconfigUnportableError):
            normalize_kubeconfig("- item1\n- item2\n", source=source)


# ── scrub_secrets does not leak client-key-data ──────────────────────────────


class TestScrubSecrets:
    """SEC-GOV-003: client-key-data must not appear in the 422 error body."""

    def test_error_message_does_not_contain_key_material(self):
        """Even if a bug were to surface client-key-data in the error, scrub_secrets
        must catch it before the value reaches the API response body.

        We verify that scrub_secrets() (called by format_error_response) strips the
        40-char base64 pattern that client-key-data values match.
        """
        from core.errors import scrub_secrets

        # A realistic base64-encoded private key fragment (40-char block → matches redaction)
        fake_key_material = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        assert len(fake_key_material) == 40

        scrubbed = scrub_secrets(fake_key_material)
        assert fake_key_material not in scrubbed

    def test_kubeconfig_unportable_error_is_appError_subclass(self):
        """KubeconfigUnportableError must be an AppError so format_error_response
        routes it through the scrub_secrets path.
        """
        from core.errors import AppError

        err = KubeconfigUnportableError(field="client-key", user_message="test")
        assert isinstance(err, AppError)
        assert err.status_code == 422
        assert err.code == "KUBECONFIG_UNPORTABLE"
        assert err.details["field"] == "client-key"
        assert err.details["user_message"] == "test"


# ── token + tokenFile coexistence ────────────────────────────────────────────


class TestTokenPrecedence:
    """When both 'token' and 'tokenFile' present, token wins, tokenFile dropped."""

    def test_token_wins_over_tokenfile(self):
        kubeconfig_yaml = """
apiVersion: v1
kind: Config
current-context: c
clusters:
- name: c
  cluster:
    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCmZha2VjYQo=
    server: https://c:6443
contexts:
- name: c
  context:
    cluster: c
    user: u
users:
- name: u
  user:
    token: real-inline-token
    tokenFile: /var/run/secrets/token
"""
        result = normalize_kubeconfig(
            kubeconfig_yaml, source=NormalizationSource.MANUAL_UPLOAD
        )
        parsed = yaml.safe_load(result)
        user = parsed["users"][0]["user"]
        assert user.get("token") == "real-inline-token"
        assert "tokenFile" not in user


# ── Empty / minimal kubeconfig ────────────────────────────────────────────────


class TestMinimal:
    """A minimal kubeconfig with no users or clusters → passes (nothing to normalize)."""

    def test_empty_clusters_and_users(self):
        kubeconfig_yaml = "apiVersion: v1\nkind: Config\n"
        result = normalize_kubeconfig(
            kubeconfig_yaml, source=NormalizationSource.MANUAL_UPLOAD
        )
        parsed = yaml.safe_load(result)
        assert parsed["apiVersion"] == "v1"
