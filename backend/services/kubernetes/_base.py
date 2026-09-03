"""
Core KubernetesService: cluster loading, kubeconfig, connection testing.
"""

import logging
import os
import tempfile
import time
from typing import Any

from kubernetes import client
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException
from sqlalchemy.orm import Session

from core.encryption import decrypt_value
from models import KubernetesCluster
from services.cluster_utils import _maybe_open_ssh_tunnel
from services.cluster_utils import get_cluster as get_cluster_util
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig
from services.reachability import with_breaker

logger = logging.getLogger(__name__)


class KubernetesServiceBase:
    """
    Core Kubernetes service methods: cluster access, kubeconfig, connection testing.
    """

    _azure_token_cache: dict[str, tuple[str, float]] = {}
    _gcp_token_cache: dict[str, tuple[str, float]] = {}

    def __init__(self, db: Session):
        self.db = db

    def get_cluster(self, cluster_id: int) -> KubernetesCluster:
        """Get cluster configuration from database."""
        return get_cluster_util(self.db, cluster_id)

    def load_kubeconfig(self, cluster: KubernetesCluster) -> client.ApiClient:
        """
        Load kubeconfig for a cluster.
        Creates temporary kubeconfig file and returns configured API client.

        For EKS clusters, mints a bearer token via boto3 and rewrites the
        kubeconfig user to use it as a static token, so the API container
        does not need the AWS CLI / aws-iam-authenticator binaries.

        For GKE clusters, mints an OAuth access token via google-auth and
        rewrites the kubeconfig user the same way, so the API container
        does not need gcloud / gke-gcloud-auth-plugin.

        For SSH/on-prem clusters, opens an SSH tunnel and rewrites the
        kubeconfig server URL to route through the tunnel.
        """
        if not cluster.kubeconfig_encrypted:
            raise ValueError(f"Cluster {cluster.name} has no kubeconfig configured")

        # Decrypt kubeconfig
        kubeconfig_yaml = decrypt_value(cluster.kubeconfig_encrypted)
        if not kubeconfig_yaml:
            raise ValueError("Failed to decrypt kubeconfig")

        # Defense-in-depth: assert portability before consuming.
        # KubeconfigUnportableError here means a legacy row survived pre-fix;
        # the error propagates to the caller with an actionable re-upload message.
        kubeconfig_yaml = normalize_kubeconfig(
            kubeconfig_yaml, source=NormalizationSource.INTERNAL_REREAD
        )

        # Check if project uses SSH credential template -- open tunnel if so
        import yaml as yaml_lib
        tunnel_port = self._maybe_open_ssh_tunnel(cluster)
        if tunnel_port:
            # Rewrite kubeconfig to route through SSH tunnel
            kubeconfig_dict = yaml_lib.safe_load(kubeconfig_yaml)
            for c in kubeconfig_dict.get('clusters', []):
                # 127.0.0.1 not "localhost" — see cluster_utils. Tunnel
                # listener is IPv4-only and "localhost" resolves to ::1
                # first on most installs, causing connection-refused.
                c['cluster']['server'] = f'https://127.0.0.1:{tunnel_port}'
                c['cluster']['insecure-skip-tls-verify'] = True
                c['cluster'].pop('certificate-authority-data', None)
                c['cluster'].pop('certificate-authority', None)
            kubeconfig_yaml = yaml_lib.dump(kubeconfig_dict, default_flow_style=False)

        # Write to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(kubeconfig_yaml)
            kubeconfig_path = f.name

        try:
            # For EKS/AWS clusters, generate a bearer token using boto3 (Python-native)
            # so the API container does not need the AWS CLI binary.  The worker
            # containers have `aws` and can use the exec-plugin kubeconfig directly,
            # but the slim API image intentionally omits heavy CLI tools.
            if cluster.cloud_provider in ["eks", "aws"]:
                from services.credentials_service import (
                    CredentialUnavailableError,
                    get_cloud_credentials_env,
                )
                project = cluster.project
                # strict=True: CredentialUnavailableError propagates when SSO
                # keys are absent or expired — intentionally NOT caught below
                # so test_connection returns success:false with a credential
                # message rather than masking it as a 401.
                # All other callers (tasks, tofu, drift, etc.) call with the
                # default strict=False and retain graceful-degradation behaviour.
                aws_env = get_cloud_credentials_env(project, self.db, strict=True)

                for key, value in aws_env.items():
                    if key.startswith('AWS_'):
                        os.environ[key] = value

                logger.info(f"Set AWS credentials for EKS cluster {cluster.name}")

                # Generate a bearer token via boto3 STS presigned URL (same mechanism
                # as `aws eks get-token`) and rewrite the kubeconfig to use it as a
                # static token instead of the exec plugin.  This avoids the need for
                # the `aws` binary in the API container.
                try:
                    token = self._generate_eks_token(cluster, aws_env)
                    if token:
                        kubeconfig_dict = yaml_lib.safe_load(
                            open(kubeconfig_path).read()
                        )
                        # Replace exec-based user auth with a static bearer token
                        for user_entry in kubeconfig_dict.get("users", []):
                            user_entry["user"] = {"token": token}
                        with open(kubeconfig_path, "w") as f:
                            yaml_lib.dump(kubeconfig_dict, f, default_flow_style=False)
                        logger.info("Injected boto3-generated bearer token for EKS cluster %s", cluster.name)
                except CredentialUnavailableError:
                    raise  # propagate structured credential error — not a transient mint glitch
                except Exception as e:
                    # Propagate AWS credential-expiry errors as AuthenticationError rather than
                    # silently falling back to the exec plugin (which would fail with a vague error).
                    try:
                        from botocore.exceptions import ClientError as BotoCoreClientError

                        from core.errors import classify_aws_credential_error
                        if isinstance(e, BotoCoreClientError):
                            classified = classify_aws_credential_error(e)
                            if classified is not None:
                                raise classified from e
                    except (ImportError, CredentialUnavailableError):
                        pass
                    logger.warning("Failed to generate boto3 EKS token for %s, falling back to exec plugin: %s", cluster.name, e)

            # For GKE/GCP clusters, mint an OAuth access token from a service-account
            # key via google-auth (Python-native) and rewrite the kubeconfig user to
            # use it as a static token.  Mirrors the EKS path: avoids needing
            # gke-gcloud-auth-plugin or gcloud inside the container.
            elif cluster.cloud_provider in ["gke", "gcp"]:
                from services.credentials_service import get_gcp_service_account_info
                project = cluster.project
                sa_info = get_gcp_service_account_info(project, self.db)

                if not sa_info:
                    logger.warning(
                        "No GCP service-account credentials configured for cluster %s "
                        "(project '%s'); kubeconfig exec plugin will be invoked and "
                        "is expected to fail in slim API container",
                        cluster.name, project.name if project else "<none>",
                    )
                else:
                    try:
                        token = self._generate_gcp_token(sa_info)
                        if token:
                            kubeconfig_dict = yaml_lib.safe_load(
                                open(kubeconfig_path).read()
                            )
                            for user_entry in kubeconfig_dict.get("users", []):
                                user_entry["user"] = {"token": token}
                            with open(kubeconfig_path, "w") as f:
                                yaml_lib.dump(kubeconfig_dict, f, default_flow_style=False)
                            logger.info("Injected google-auth-generated bearer token for GKE cluster %s", cluster.name)
                    except Exception as e:
                        logger.warning("Failed to generate google-auth GCP token for %s, falling back to exec plugin: %s", cluster.name, e)

            # For Azure / AKS clusters, mint an OAuth access token scoped to AKS AAD Server
            # via pure Python OAuth2 token exchange and rewrite the kubeconfig user to
            # use it as a static token. Mirrors the EKS and GKE paths: avoids needing
            # `az` CLI or `kubelogin` inside the container and prevents token expiration.
            elif cluster.cloud_provider in ["azure", "aks"]:
                from services.credentials_service import get_azure_service_principal_info
                project = cluster.project
                azure_info = get_azure_service_principal_info(project, self.db)

                if not azure_info:
                    logger.warning(
                        "No Azure service-principal credentials configured for cluster %s "
                        "(project '%s'); static token in kubeconfig will be used and "
                        "may expire",
                        cluster.name, project.name if project else "<none>",
                    )
                else:
                    try:
                        tenant_id, client_id, client_secret = azure_info
                        token = self._generate_azure_token(tenant_id, client_id, client_secret)
                        if token:
                            kubeconfig_dict = yaml_lib.safe_load(
                                open(kubeconfig_path).read()
                            )
                            for user_entry in kubeconfig_dict.get("users", []):
                                user_entry["user"] = {"token": token}
                            with open(kubeconfig_path, "w") as f:
                                yaml_lib.dump(kubeconfig_dict, f, default_flow_style=False)
                            logger.info("Injected pure-Python OAuth bearer token for Azure AKS cluster %s", cluster.name)
                    except Exception as e:
                        logger.warning("Failed to generate Azure AKS token for %s: %s", cluster.name, e)

            # Load config from file
            k8s_config.load_kube_config(config_file=kubeconfig_path, context=cluster.context)

            # Disable urllib3 retries on the returned ApiClient. The kubernetes-client
            # default is to inherit urllib3's 3-retry policy, which silently amplifies
            # a single broken request into ~75s of blocking. The breaker assumes one
            # call = one wire attempt; per-call _request_timeout enforces the upper
            # bound on the wire attempt itself.
            cfg = client.Configuration.get_default_copy()
            cfg.retries = 0
            return client.ApiClient(cfg)
        finally:
            # Clean up temp file
            try:
                os.unlink(kubeconfig_path)
            except Exception as e:
                logger.warning(f"Failed to delete temp kubeconfig: {e}")

    @staticmethod
    def _generate_eks_token(cluster: KubernetesCluster, aws_env: dict) -> str | None:
        """
        Generate an EKS bearer token using boto3 STS presigned GetCallerIdentity.

        This is the Python-native equivalent of `aws eks get-token` and does not
        require the AWS CLI binary.  The token is valid for ~15 minutes.

        Returns:
            Base64-encoded presigned URL token prefixed with 'k8s-aws-v1.', or None on failure.
        """
        import base64

        try:
            import boto3
            from botocore.auth import SigV4QueryAuth
            from botocore.awsrequest import AWSRequest
        except ImportError:
            logger.warning("boto3/botocore not available — cannot generate EKS token natively")
            return None

        # Extract cluster name — must be the BARE EKS cluster name (not an ARN).
        # Priority: meta_data.cluster_arn > context/name (with ARN-parse for both).
        cluster_name = cluster.context or cluster.name
        if cluster.meta_data and cluster.meta_data.get("cluster_arn"):
            # ARN format: arn:aws:eks:region:account:cluster/name
            arn_parts = cluster.meta_data["cluster_arn"].split("/")
            if len(arn_parts) >= 2:
                cluster_name = arn_parts[-1]
        elif ":cluster/" in cluster_name:
            # context or name is itself an ARN — extract bare name after ":cluster/"
            cluster_name = cluster_name.split(":cluster/", 1)[-1]

        # Build STS credentials from the env we already set.
        #
        # The cluster's own region MUST win over any inherited AWS_REGION /
        # AWS_DEFAULT_REGION from the backend's process env. EKS bearer tokens
        # are signed against the regional STS endpoint and the EKS API server
        # rejects tokens signed for a different region (401 Unauthorized). The
        # backend's global env represents the deployer's environment, not the
        # cluster's home region — picking up a stale env value here silently
        # breaks every cluster outside that region.
        region = cluster.region or aws_env.get("AWS_REGION") or aws_env.get("AWS_DEFAULT_REGION") or "us-east-1"

        session = boto3.Session(
            aws_access_key_id=aws_env.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=aws_env.get("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=aws_env.get("AWS_SESSION_TOKEN"),
            region_name=region,
        )

        # Detect truly absent credentials via boto3's full resolution chain
        # (env vars, ~/.aws/credentials, IMDS, IRSA/web-identity token file).
        # A non-None result means boto3 found SOME credentials — including
        # ambient/IRSA/instance-role paths that have no explicit env keys.
        # None means nothing whatsoever resolved; raise rather than producing
        # a None-session 401 with no diagnostic context.
        if session.get_credentials() is None:
            from services.credentials_service import CredentialUnavailableError
            raise CredentialUnavailableError(
                f"No AWS credentials resolved for cluster '{cluster.name}' — "
                "bind a credential template or configure a default template."
            )

        # Build a presigned STS GetCallerIdentity URL with x-k8s-aws-id as a
        # signed header.  This is exactly what `aws eks get-token` does: it creates
        # a SigV4-signed GET request to the regional STS endpoint with the cluster
        # identity header included in the signature.  The signed URL becomes the
        # bearer token after base64 encoding.
        #
        # Must use regional STS endpoint — EKS rejects tokens signed against
        # the global sts.amazonaws.com endpoint.
        regional_sts_url = f"https://sts.{region}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15"

        credentials = session.get_credentials().get_frozen_credentials()
        signer = SigV4QueryAuth(credentials, "sts", region, expires=60)
        request = AWSRequest(
            method="GET",
            url=regional_sts_url,
            headers={
                "x-k8s-aws-id": cluster_name,
            },
        )
        signer.add_auth(request)

        # The fully signed URL (with auth params in query string) is the token payload
        signed_url = request.url

        # Encode as k8s-aws-v1 token
        token = "k8s-aws-v1." + base64.urlsafe_b64encode(signed_url.encode("utf-8")).rstrip(b"=").decode("utf-8")

        logger.info("Generated EKS bearer token for cluster %s (region=%s)", cluster_name, region)
        return token

    @classmethod
    def _generate_gcp_token(cls, sa_info: dict) -> str | None:
        """
        Generate a GCP access token for a GKE cluster from a service-account
        key dict, using google-auth.  This is the Python-native equivalent of
        ``gke-gcloud-auth-plugin`` and does not require the gcloud CLI.

        The token is a Google OAuth2 access token (~1 hour TTL) with the
        ``cloud-platform`` scope, which GKE accepts as a bearer token.
        Cached in-memory for 45 minutes to prevent redundant network calls.

        Returns the token string, or None on failure.
        """
        client_email = sa_info.get("client_email", "")
        now = time.time()
        if client_email:
            cached = cls._gcp_token_cache.get(client_email)
            if cached and cached[1] > now:
                return cached[0]

        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError:
            logger.warning("google-auth not available — cannot generate GCP token natively")
            return None

        try:
            credentials = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            credentials.refresh(Request())
            token = credentials.token
            if token and client_email:
                cls._gcp_token_cache[client_email] = (token, now + 2700)
            logger.info(
                "Generated GCP access token for service account %s",
                client_email or "<unknown>",
            )
            return token
        except Exception as e:
            logger.warning("Failed to generate GCP access token for %s: %s", client_email or "unknown", e)
            return None

    @classmethod
    def _generate_azure_token(cls, tenant_id: str, client_id: str, client_secret: str) -> str | None:
        """
        Generate an AKS bearer token using Azure OAuth2 client_credentials flow.
        Cached in-memory for 45 minutes (Azure tokens are valid for 60-90 minutes).
        """
        cache_key = f"{tenant_id}:{client_id}"
        now = time.time()
        cached = cls._azure_token_cache.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]

        from services.azure_oauth_service import request_azure_oauth_token

        aks_aad_server_app_id = "6dae42f8-4368-4678-94ff-776099604563"
        try:
            token_data = request_azure_oauth_token(
                tenant_id=tenant_id,
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "scope": f"{aks_aad_server_app_id}/.default",
                },
                timeout=15,
            )
            token = token_data.get("access_token")
            if token:
                expires_in = token_data.get("expires_in", 3600)
                ttl = max(60, expires_in - 300)
                cls._azure_token_cache[cache_key] = (token, now + ttl)
                return token
        except Exception as e:
            logger.error("Failed to fetch AKS OAuth bearer token from Azure: %s", e)
            raise
        return None

    @staticmethod
    def _maybe_open_ssh_tunnel(cluster: KubernetesCluster) -> int | None:
        """Delegate to shared cluster_utils._maybe_open_ssh_tunnel()."""
        return _maybe_open_ssh_tunnel(cluster)

    @with_breaker("cluster", target_id_arg="cluster_id")
    def test_connection(self, cluster_id: int) -> dict[str, Any]:
        """Test connection to cluster."""
        try:
            cluster = self.get_cluster(cluster_id)
            api_client = self.load_kubeconfig(cluster)
            v1 = client.CoreV1Api(api_client)

            # Bound the wire attempt: connect=3s, read=8s. Without _request_timeout
            # a kubernetes-client call inherits the OS TCP timeout (75s+).
            v1.list_namespace(limit=1, _request_timeout=(3, 8))

            # Get server version
            version_api = client.VersionApi(api_client)
            version_info = version_api.get_code(_request_timeout=(3, 8))

            return {
                "success": True,
                "message": "Connection successful",
                "cluster_name": cluster.name,
                "version": f"{version_info.major}.{version_info.minor}",
                "api_server": cluster.api_server,
                "cloud_provider": cluster.cloud_provider,
                "region": cluster.region
            }
        except ApiException as e:
            logger.error(f"Kubernetes API error during connection test: {e}")
            return {
                "success": False,
                "message": f"Kubernetes API error: {e.reason}",
                "status_code": e.status
            }
        except Exception as e:
            logger.error(f"Connection test failed: {e}")
            return {
                "success": False,
                "message": str(e)
            }

    def list_namespaces(self, cluster_id: int) -> list[str]:
        """List all namespaces in a cluster."""
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)
        v1 = client.CoreV1Api(api_client)

        namespaces = v1.list_namespace()
        return [ns.metadata.name for ns in namespaces.items]

    def get_node_count(self, cluster_id: int) -> int:
        """Get the total number of nodes in a cluster."""
        cluster = self.get_cluster(cluster_id)
        api_client = self.load_kubeconfig(cluster)
        v1 = client.CoreV1Api(api_client)

        nodes = v1.list_node()
        return len(nodes.items)
