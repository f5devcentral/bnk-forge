"""
Cloud authentication tools — AWS, IBM, SSH credentials, credential templates.

Lets MCP consumers manage the cloud-account credentials forge uses to talk
to external providers (assume IAM roles, complete SSO flows, store SSH
key material). Maps to: routes/cloud_auth.py, routes/credential_templates.py,
routes/ssh_credentials.py.

Auth: all routes under /api/cloud-auth require `operator`. The first-class
SSH credential CRUD under /api/ssh-credentials uses `viewer` for reads
and `operator` for writes. Credential templates follow the same pattern.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..client import BNKForgeClient


def register(mcp: FastMCP, client: BNKForgeClient) -> None:
    """Register cloud authentication tools with the MCP server."""

    # ------------------------------------------------------------------
    # AWS regions + credentials
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_aws_regions() -> str:
        """List the AWS regions BNK-Forge will accept for cloud_provider="aws".

        Use this before calling `create_cluster` / `create_project` with
        `cloud_provider="aws"` to confirm the region string will validate.
        """
        result = await client.get("/api/cloud-auth/aws/regions")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def aws_sso_initiate(
        start_url: str,
        region: str = "",
        project_id: int = 0,
    ) -> str:
        """Begin AWS IAM Identity Center (SSO) device-authorization flow.

        Returns a `verification_uri` and a `device_code`; the user opens
        the URI in a browser and approves the device. Then call
        `aws_sso_poll` with the returned `client_id`/`client_secret`/
        `device_code` to obtain an access token.

        Args:
            start_url: SSO start URL (e.g. https://my-org.awsapps.com/start).
            region: SSO region (e.g. "us-east-1"). Empty leaves backend default.
            project_id: Optional project ID to associate the SSO session with
                (pass 0 to leave unset).
        """
        body: dict[str, Any] = {"start_url": start_url}
        if region:
            body["region"] = region
        if project_id:
            body["project_id"] = project_id
        result = await client.post("/api/cloud-auth/aws/sso/initiate", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def aws_sso_poll(
        client_id: str,
        client_secret: str,
        device_code: str,
        region: str = "",
        project_id: int = 0,
    ) -> str:
        """Poll an in-flight SSO device-authorization session for a token.

        Returns the access token once the user has approved the device in
        their browser. Until then returns `authorization_pending` and the
        caller should retry (typical SSO interval: 5 seconds).

        Args:
            client_id: From the `aws_sso_initiate` response.
            client_secret: From the `aws_sso_initiate` response.
            device_code: From the `aws_sso_initiate` response.
            region: SSO region (same as initiate). Empty leaves backend default.
            project_id: Optional project ID (pass 0 to leave unset).
        """
        body: dict[str, Any] = {
            "client_id": client_id,
            "client_secret": client_secret,
            "device_code": device_code,
        }
        if region:
            body["region"] = region
        if project_id:
            body["project_id"] = project_id
        result = await client.post("/api/cloud-auth/aws/sso/poll", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def aws_set_project_credentials(
        access_token: str,
        account_id: str,
        role_name: str,
        project_id: int,
        region: str = "",
    ) -> str:
        """Resolve an AWS account/role into a project credential record.

        Uses an SSO access token (from `aws_sso_poll`) to exchange for
        short-lived AWS credentials in a specific account+role, then
        stores them under the given project_id.

        Args:
            access_token: SSO access token obtained from `aws_sso_poll`.
            account_id: 12-digit AWS account ID.
            role_name: IAM role name to assume in that account.
            project_id: Project to store the resolved credentials under.
            region: AWS region (empty for default).
        """
        body: dict[str, Any] = {
            "access_token": access_token,
            "account_id": account_id,
            "role_name": role_name,
            "project_id": project_id,
        }
        if region:
            body["region"] = region
        result = await client.post("/api/cloud-auth/aws/credentials", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def aws_assume_role(
        role_arn: str,
        project_id: int,
        session_name: str = "BNK-Forge-Session",
        duration_seconds: int = 3600,
        region: str = "",
    ) -> str:
        """Call STS AssumeRole using the project's stored credentials.

        Refreshes a short-lived credential pair for the project by assuming
        the given role ARN. Result is stored under the project; subsequent
        forge operations (TF apply, EKS describe-cluster) use the refreshed
        credentials transparently.

        Args:
            role_arn: Full IAM role ARN to assume.
            project_id: Project whose stored creds will be used to make
                the STS call.
            session_name: STS session name (default "BNK-Forge-Session";
                max 128 chars).
            duration_seconds: Session lifetime in seconds (900–43200;
                default 3600 = 1h).
            region: AWS region for the STS call (empty for default).
        """
        body: dict[str, Any] = {
            "role_arn": role_arn,
            "project_id": project_id,
            "session_name": session_name,
            "duration_seconds": duration_seconds,
        }
        if region:
            body["region"] = region
        result = await client.post("/api/cloud-auth/aws/assume-role", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_project_aws_credentials(project_id: int) -> str:
        """Return metadata (NOT the secret material) about the project's AWS credentials.

        Useful for: confirming creds are configured, checking expiry,
        seeing which account/role they correspond to. The access key /
        secret access key are never returned.

        Args:
            project_id: Project to query.
        """
        result = await client.get(f"/api/cloud-auth/aws/credentials/{project_id}")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def delete_project_aws_credentials(project_id: int) -> str:
        """Remove the AWS credentials currently associated with a project.

        Does NOT revoke the underlying IAM role or SSO grant — only deletes
        the locally-stored credential record. The project will need fresh
        credentials before subsequent AWS operations.

        Args:
            project_id: Project whose AWS creds to remove.
        """
        result = await client.delete(f"/api/cloud-auth/aws/credentials/{project_id}")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Credential templates (reusable across projects)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_credential_templates() -> str:
        """List all credential templates available in BNK-Forge.

        Templates are reusable credential bundles (AWS access keys, SSO
        configs, Azure service principals, GCP service-account JSON,
        IBM Cloud API keys, SSH key material) that can be bound to projects
        via the `credential_template_id` field on a project.
        """
        result = await client.get("/api/credential-templates")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_credential_template(
        name: str,
        provider: str,
        description: str = "",
        region: str = "",
        aws_auth_method: str = "",
        aws_access_key_id: str = "",
        aws_secret_access_key: str = "",
        aws_session_token: str = "",
        aws_sso_enabled: bool = False,
        aws_sso_start_url: str = "",
        aws_sso_region: str = "",
        aws_sso_account_id: str = "",
        aws_sso_role_name: str = "",
        ibmcloud_api_key: str = "",
        is_default: bool = False,
    ) -> str:
        """Create a new credential template.

        Only pass fields relevant to the chosen `provider`. Empty strings
        are dropped before the request, so unused fields stay unset rather
        than empty.

        Args:
            name: Display name for the template.
            provider: One of "aws", "azure", "gcp", "ibm", "ssh".
            description: Optional free-text description.
            region: Default region (validated for AWS).
            aws_auth_method: e.g. "static", "sso", "instance-profile" — provider-specific.
            aws_access_key_id: AWS_ACCESS_KEY_ID (for static auth).
            aws_secret_access_key: AWS_SECRET_ACCESS_KEY (for static auth).
            aws_session_token: AWS_SESSION_TOKEN (for STS-derived creds).
            aws_sso_enabled: True to mark the template as SSO-driven.
            aws_sso_start_url / aws_sso_region / aws_sso_account_id / aws_sso_role_name: SSO config.
            ibmcloud_api_key: IBM Cloud API key (required when provider="ibm").
            is_default: Mark as the default template for this provider.
        """
        body: dict[str, Any] = {"name": name, "provider": provider, "is_default": is_default}
        for k, v in (
            ("description", description),
            ("region", region),
            ("aws_auth_method", aws_auth_method),
            ("aws_access_key_id", aws_access_key_id),
            ("aws_secret_access_key", aws_secret_access_key),
            ("aws_session_token", aws_session_token),
            ("aws_sso_start_url", aws_sso_start_url),
            ("aws_sso_region", aws_sso_region),
            ("aws_sso_account_id", aws_sso_account_id),
            ("aws_sso_role_name", aws_sso_role_name),
            ("ibmcloud_api_key", ibmcloud_api_key),
        ):
            if v:
                body[k] = v
        if aws_sso_enabled:
            body["aws_sso_enabled"] = True
        result = await client.post("/api/credential-templates", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def test_credential_template(template_id: int) -> str:
        """Validate a credential template by attempting to authenticate with the provider.

        For AWS static: calls STS GetCallerIdentity. For SSO: verifies the
        cached SSO token. For IBM: pings IAM. For SSH: opens a test
        connection. Returns a structured result with any error detail.

        Args:
            template_id: The credential template ID to test.
        """
        result = await client.post(f"/api/credential-templates/{template_id}/test")
        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # SSH credentials (first-class — used for jumphost / on-prem access)
    # ------------------------------------------------------------------

    @mcp.tool()
    async def list_ssh_credentials() -> str:
        """List SSH credentials known to BNK-Forge.

        Returns metadata only (host, port, username, auth_type, fingerprint) —
        the private key / password is never returned. SSH credentials are
        referenced by ID from clusters (via `ssh_credential_id` on
        `create_cluster`) and operators.
        """
        result = await client.get("/api/ssh-credentials")
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def create_ssh_credential(
        name: str,
        host: str,
        username: str,
        auth_type: str,
        port: int = 22,
        password: str = "",
        private_key: str = "",
        passphrase: str = "",
        description: str = "",
    ) -> str:
        """Register a new SSH credential.

        Stored encrypted server-side. Use `test_ssh_credential` after
        creating to verify connectivity.

        Args:
            name: Display name (unique per user).
            host: Target hostname or IP.
            username: SSH username.
            auth_type: Either "password" or "key".
            port: SSH port (default 22).
            password: Required when auth_type="password" (max 500 chars).
            private_key: Required when auth_type="key" (PEM-encoded; max 16KB).
            passphrase: Optional passphrase for the private key.
            description: Optional free-text description.
        """
        body: dict[str, Any] = {
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "auth_type": auth_type,
        }
        if password:
            body["password"] = password
        if private_key:
            body["private_key"] = private_key
        if passphrase:
            body["passphrase"] = passphrase
        if description:
            body["description"] = description
        result = await client.post("/api/ssh-credentials", json=body)
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def test_ssh_credential(credential_id: int) -> str:
        """Verify an SSH credential by opening a test connection.

        Returns a structured result with success/failure detail. Does not
        execute commands — just probes auth.

        Args:
            credential_id: The SSH credential ID to test.
        """
        result = await client.post(f"/api/ssh-credentials/{credential_id}/test")
        return json.dumps(result, indent=2)
