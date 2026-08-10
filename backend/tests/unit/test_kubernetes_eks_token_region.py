"""
Unit tests for region resolution inside ``KubernetesServiceBase._generate_eks_token``.

EKS bearer tokens are signed against a regional STS endpoint and the EKS API
server rejects tokens signed for the wrong region with HTTP 401. The
``_generate_eks_token`` resolver must pick the cluster's own home region over
the backend process's inherited ``AWS_REGION`` / ``AWS_DEFAULT_REGION``.

Background: prior to the fix, the env-var values won over ``cluster.region``,
which silently broke every cluster outside the backend's default region.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from services.kubernetes._base import KubernetesServiceBase


def _cluster(region: str | None, *, name: str = "syd-tracer") -> SimpleNamespace:
    """Build a minimal stand-in for the KubernetesCluster ORM model.

    The token generator reads only `.context`, `.name`, `.meta_data` (optional)
    and `.region` from the cluster argument — a SimpleNamespace satisfies the
    contract without instantiating the ORM model.
    """
    return SimpleNamespace(
        context=f"arn:aws:eks:{region or 'unknown'}:000000000000:cluster/{name}",
        name=name,
        meta_data=None,
        region=region,
    )


def _patch_boto_pipeline():
    """Patch the boto3 / botocore call chain to capture the region used to
    construct the SigV4 signer. Returns the SigV4QueryAuth mock so callers
    can inspect call_args.
    """
    sig_mock = MagicMock()
    request_mock = MagicMock()
    request_mock.url = "https://sts.example.amazonaws.com/?signed=ok"

    return (
        patch("boto3.Session"),
        patch("botocore.auth.SigV4QueryAuth", sig_mock),
        patch("botocore.awsrequest.AWSRequest", return_value=request_mock),
        sig_mock,
    )


class TestGenerateEksTokenRegion:
    def test_cluster_region_wins_over_inherited_aws_region_env(self):
        """The cluster's own region must override AWS_REGION inherited from the
        backend's process environment. This is the bug fix that this test
        guards: prior to it, AWS_REGION (e.g. ap-northeast-1 picked up from
        the deployer's local shell) silently won over cluster.region
        (ap-southeast-2), producing tokens the EKS API server rejected as 401.
        """
        cluster = _cluster(region="ap-southeast-2")
        env = {
            "AWS_REGION": "ap-northeast-1",
            "AWS_DEFAULT_REGION": "us-west-2",
            "AWS_ACCESS_KEY_ID": "AKIA-FAKE",
            "AWS_SECRET_ACCESS_KEY": "fake-secret",
        }

        session_patch, sig_patch, request_patch, sig_mock = _patch_boto_pipeline()
        with session_patch as session_cls, sig_patch, request_patch:
            session_cls.return_value.get_credentials.return_value.get_frozen_credentials.return_value = MagicMock()
            KubernetesServiceBase._generate_eks_token(cluster, env)

        signer_region = sig_mock.call_args[0][2]
        assert signer_region == "ap-southeast-2", (
            f"expected cluster.region (ap-southeast-2) to win, got {signer_region!r}; "
            "regression of the silent-401 bug in _generate_eks_token"
        )
        session_cls.assert_called_once()
        assert session_cls.call_args.kwargs["region_name"] == "ap-southeast-2"

    def test_env_region_used_when_cluster_region_unset(self):
        """When cluster.region is None or empty, fall back to AWS_REGION and
        then AWS_DEFAULT_REGION. Preserves behaviour for legacy cluster rows
        that pre-date the region column.
        """
        cluster = _cluster(region=None)
        env = {
            "AWS_REGION": "ap-northeast-1",
            "AWS_DEFAULT_REGION": "us-west-2",
            "AWS_ACCESS_KEY_ID": "AKIA-FAKE",
            "AWS_SECRET_ACCESS_KEY": "fake-secret",
        }

        session_patch, sig_patch, request_patch, sig_mock = _patch_boto_pipeline()
        with session_patch as session_cls, sig_patch, request_patch:
            session_cls.return_value.get_credentials.return_value.get_frozen_credentials.return_value = MagicMock()
            KubernetesServiceBase._generate_eks_token(cluster, env)

        assert sig_mock.call_args[0][2] == "ap-northeast-1"

    def test_default_region_used_when_no_other_source(self):
        """Last-resort fallback is us-east-1 when neither cluster.region nor
        the AWS_REGION / AWS_DEFAULT_REGION env vars are populated.
        """
        cluster = _cluster(region=None)
        env = {
            "AWS_ACCESS_KEY_ID": "AKIA-FAKE",
            "AWS_SECRET_ACCESS_KEY": "fake-secret",
        }

        session_patch, sig_patch, request_patch, sig_mock = _patch_boto_pipeline()
        with session_patch as session_cls, sig_patch, request_patch:
            session_cls.return_value.get_credentials.return_value.get_frozen_credentials.return_value = MagicMock()
            KubernetesServiceBase._generate_eks_token(cluster, env)

        assert sig_mock.call_args[0][2] == "us-east-1"


class TestGenerateEksTokenClusterName:
    """
    Guard tests for x-k8s-aws-id cluster-name extraction.

    EKS rejects tokens where x-k8s-aws-id is a full ARN — the header must
    equal the bare cluster name. This covers the fix for clusters registered
    with an ARN-shaped context (e.g. via awsbnkctl forge register).
    """

    def _run_and_capture_request_headers(self, cluster, env):
        """Run _generate_eks_token and capture the AWSRequest headers kwarg."""
        captured = {}

        def fake_aws_request(method, url, headers=None, **kw):
            captured["headers"] = headers or {}
            mock_req = MagicMock()
            mock_req.url = "https://sts.example.amazonaws.com/?signed=ok"
            return mock_req

        session_patch, sig_patch, _, sig_mock = _patch_boto_pipeline()
        with session_patch as session_cls, sig_patch, patch(
            "botocore.awsrequest.AWSRequest", side_effect=fake_aws_request
        ):
            session_cls.return_value.get_credentials.return_value.get_frozen_credentials.return_value = MagicMock()
            KubernetesServiceBase._generate_eks_token(cluster, env)

        return captured.get("headers", {})

    def test_arn_shaped_context_yields_bare_name(self):
        """Cluster 24 shape: context = full ARN, meta_data.cluster_arn = None.
        x-k8s-aws-id must be 'syd-tracer', NOT the full ARN.
        """
        cluster = SimpleNamespace(
            context="arn:aws:eks:ap-southeast-2:292785712872:cluster/syd-tracer",
            name="syd-tracer",
            meta_data=None,
            region="ap-southeast-2",
        )
        env = {"AWS_ACCESS_KEY_ID": "AKIA-FAKE", "AWS_SECRET_ACCESS_KEY": "fake"}
        headers = self._run_and_capture_request_headers(cluster, env)
        assert headers.get("x-k8s-aws-id") == "syd-tracer"

    def test_bare_name_context_unchanged(self):
        """Cluster 9 shape: context = bare name. x-k8s-aws-id must stay as-is."""
        cluster = SimpleNamespace(
            context="aws-syd-test-cluster",
            name="aws-syd-test-cluster",
            meta_data={"cluster_arn": "arn:aws:eks:ap-southeast-2:292785712872:cluster/aws-syd-test-cluster"},
            region="ap-southeast-2",
        )
        env = {"AWS_ACCESS_KEY_ID": "AKIA-FAKE", "AWS_SECRET_ACCESS_KEY": "fake"}
        headers = self._run_and_capture_request_headers(cluster, env)
        assert headers.get("x-k8s-aws-id") == "aws-syd-test-cluster"

    def test_meta_data_cluster_arn_wins_over_arn_context(self):
        """When both meta_data.cluster_arn and an ARN-shaped context exist,
        meta_data.cluster_arn is the source of truth (priority path unchanged).
        """
        cluster = SimpleNamespace(
            context="arn:aws:eks:ap-southeast-2:111111111111:cluster/context-name",
            name="context-name",
            meta_data={"cluster_arn": "arn:aws:eks:ap-southeast-2:292785712872:cluster/real-name"},
            region="ap-southeast-2",
        )
        env = {"AWS_ACCESS_KEY_ID": "AKIA-FAKE", "AWS_SECRET_ACCESS_KEY": "fake"}
        headers = self._run_and_capture_request_headers(cluster, env)
        assert headers.get("x-k8s-aws-id") == "real-name"

    def test_region_wins_still_holds_with_arn_context(self):
        """region-wins logic (6f06f09e) must still hold even when context is ARN-shaped."""
        cluster = SimpleNamespace(
            context="arn:aws:eks:ap-southeast-2:292785712872:cluster/syd-tracer",
            name="syd-tracer",
            meta_data=None,
            region="ap-southeast-2",
        )
        env = {
            "AWS_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "AKIA-FAKE",
            "AWS_SECRET_ACCESS_KEY": "fake",
        }

        session_patch, sig_patch, request_patch, sig_mock = _patch_boto_pipeline()
        with session_patch as session_cls, sig_patch, request_patch:
            session_cls.return_value.get_credentials.return_value.get_frozen_credentials.return_value = MagicMock()
            KubernetesServiceBase._generate_eks_token(cluster, env)

        assert sig_mock.call_args[0][2] == "ap-southeast-2"


class TestGenerateEksTokenCredentialAbsence:
    """
    Guard tests for the boto3-chain absence check in _generate_eks_token.

    When session.get_credentials() returns None (no credentials anywhere —
    no template, no env vars, no IMDS, no IRSA), the function must raise
    CredentialUnavailableError rather than producing a None-session 401.

    When get_credentials() returns a non-None object (ambient/IRSA/instance-role
    creds, even with no explicit env keys), the token must mint successfully.
    """

    def _run(self, cluster, env, session_get_credentials_return):
        """Run _generate_eks_token with a mocked boto3 session whose
        get_credentials() returns the given value."""
        sig_mock = MagicMock()
        request_mock = MagicMock()
        request_mock.url = "https://sts.example.amazonaws.com/?signed=ok"

        def fake_aws_request(method, url, headers=None, **kw):
            mock_req = MagicMock()
            mock_req.url = request_mock.url
            return mock_req

        with patch("boto3.Session") as session_cls, \
             patch("botocore.auth.SigV4QueryAuth", sig_mock), \
             patch("botocore.awsrequest.AWSRequest", side_effect=fake_aws_request):
            session_inst = session_cls.return_value
            session_inst.get_credentials.return_value = session_get_credentials_return
            if session_get_credentials_return is not None:
                session_inst.get_credentials.return_value.get_frozen_credentials.return_value = MagicMock()
            return KubernetesServiceBase._generate_eks_token(cluster, env)

    def test_ambient_creds_present_token_mints(self):
        """Ambient/IRSA creds: get_credentials() returns non-None even when
        no explicit AWS_ACCESS_KEY_ID is set in env.  Token must mint successfully.
        """
        cluster = SimpleNamespace(
            context="syd-tracer",
            name="syd-tracer",
            meta_data=None,
            region="ap-southeast-2",
        )
        # No explicit AWS keys in env — simulates IRSA / instance-role
        env = {}

        # get_credentials() returns a non-None frozen-creds mock → ambient creds present
        frozen_creds = MagicMock()
        fake_creds = MagicMock()
        fake_creds.get_frozen_credentials.return_value = frozen_creds

        token = self._run(cluster, env, fake_creds)
        # Token should be a non-None string starting with 'k8s-aws-v1.'
        assert token is not None
        assert token.startswith("k8s-aws-v1.")

    def test_no_credentials_raises_credential_unavailable(self):
        """get_credentials() returns None → CredentialUnavailableError raised,
        NOT a generic Exception.  test_connection should surface success:false
        with the credential message.
        """
        from services.credentials_service import CredentialUnavailableError

        cluster = SimpleNamespace(
            context="orphan-cluster",
            name="orphan-cluster",
            meta_data=None,
            region="us-east-1",
        )
        env = {}  # No explicit keys, and IMDS/IRSA will be None in this mock

        import pytest
        with pytest.raises(CredentialUnavailableError, match="No AWS credentials resolved"):
            self._run(cluster, env, None)  # get_credentials() → None
