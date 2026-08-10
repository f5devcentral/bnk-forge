"""
AWS Authentication Service
Handles AWS SSO device code flow, IAM roles, and credential management
"""
import logging
import time
from datetime import UTC, datetime, timedelta

from core.aws_config import adaptive_retry_config

logger = logging.getLogger(__name__)


def _import_boto3():
    """Lazy-load boto3 to avoid ~80MB import at startup when AWS isn't used."""
    import boto3
    return boto3


def _import_botocore_exceptions():
    """Lazy-load botocore exceptions."""
    from botocore.exceptions import BotoCoreError, ClientError
    return BotoCoreError, ClientError


class AWSAuthService:
    """Service for managing AWS authentication via SSO and IAM roles"""

    def __init__(self):
        self.sso_oidc_client = None
        self.sso_client = None

    def initiate_device_authorization(
        self,
        start_url: str,
        region: str = None
    ) -> dict:
        """
        Initiate AWS SSO device authorization flow

        Args:
            start_url: AWS SSO start URL (e.g., https://my-sso-portal.awsapps.com/start)
            region: AWS region for SSO (required — caller must provide from template/project)

        Returns:
            dict: Device authorization details including:
                - device_code: Code to poll for authorization
                - user_code: Code user enters in browser
                - verification_uri: URL user visits
                - verification_uri_complete: URL with code pre-filled
                - expires_in: Seconds until code expires
                - interval: Polling interval in seconds
        """
        boto3 = _import_boto3()
        BotoCoreError, ClientError = _import_botocore_exceptions()
        try:
            # Create SSO OIDC client
            self.sso_oidc_client = boto3.client('sso-oidc', region_name=region, config=adaptive_retry_config())

            # Register client (get client credentials)
            register_response = self.sso_oidc_client.register_client(
                clientName='BNK-Forge',
                clientType='public'
            )

            client_id = register_response['clientId']
            client_secret = register_response['clientSecret']

            # Start device authorization
            device_response = self.sso_oidc_client.start_device_authorization(
                clientId=client_id,
                clientSecret=client_secret,
                startUrl=start_url
            )

            logger.info(f"Device authorization initiated for {start_url}")

            return {
                'device_code': device_response['deviceCode'],
                'user_code': device_response['userCode'],
                'verification_uri': device_response['verificationUri'],
                'verification_uri_complete': device_response.get('verificationUriComplete', ''),
                'expires_in': device_response['expiresIn'],
                'interval': device_response['interval'],
                'client_id': client_id,
                'client_secret': client_secret,
                'start_url': start_url,
                'region': region
            }

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to initiate device authorization: {e}")
            raise Exception(f"AWS SSO device authorization failed: {str(e)}")

    def poll_for_token(
        self,
        client_id: str,
        client_secret: str,
        device_code: str,
        region: str = None,
        max_attempts: int = 60
    ) -> dict:
        """
        Poll for access token after user completes device authorization

        Args:
            client_id: Client ID from registration
            client_secret: Client secret from registration
            device_code: Device code from authorization
            region: AWS region
            max_attempts: Maximum polling attempts (default: 60)

        Returns:
            dict: Token information including:
                - access_token: AWS SSO access token
                - expires_in: Token expiration in seconds
                - token_type: Token type (Bearer)
                - refresh_token: Refresh token (if available)
        """
        boto3 = _import_boto3()
        BotoCoreError, ClientError = _import_botocore_exceptions()
        try:
            if not self.sso_oidc_client:
                self.sso_oidc_client = boto3.client('sso-oidc', region_name=region, config=adaptive_retry_config())

            interval = 5  # Default polling interval
            attempts = 0

            while attempts < max_attempts:
                try:
                    token_response = self.sso_oidc_client.create_token(
                        clientId=client_id,
                        clientSecret=client_secret,
                        grantType='urn:ietf:params:oauth:grant-type:device_code',
                        deviceCode=device_code
                    )

                    logger.info("Successfully obtained AWS SSO access token")

                    return {
                        'access_token': token_response['accessToken'],
                        'expires_in': token_response['expiresIn'],
                        'token_type': token_response.get('tokenType', 'Bearer'),
                        'refresh_token': token_response.get('refreshToken'),
                        'expires_at': (datetime.now(UTC) + timedelta(seconds=token_response['expiresIn'])).isoformat()
                    }

                except ClientError as e:
                    error_code = e.response['Error']['Code']

                    if error_code == 'AuthorizationPendingException':
                        # User hasn't completed authorization yet
                        logger.debug(f"Authorization pending, attempt {attempts + 1}/{max_attempts}")
                        attempts += 1

                        # If this was the last attempt, raise the exception so caller knows it's still pending
                        if attempts >= max_attempts:
                            raise Exception("Authorization pending")

                        # Otherwise, sleep and continue polling
                        time.sleep(interval)

                    elif error_code == 'SlowDownException':
                        # Polling too fast, increase interval
                        interval += 5
                        logger.debug(f"Slowing down polling interval to {interval}s")
                        attempts += 1

                        if attempts >= max_attempts:
                            raise Exception("Authorization pending")

                        time.sleep(interval)

                    elif error_code == 'ExpiredTokenException':
                        raise Exception("Device code has expired. Please restart authorization.")

                    else:
                        raise Exception(f"Token polling failed: {str(e)}")

            raise Exception("Authorization timed out. User did not complete device authorization in time.")

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to poll for token: {e}")
            raise Exception(f"AWS SSO token polling failed: {str(e)}")

    def get_account_credentials(
        self,
        access_token: str,
        account_id: str,
        role_name: str,
        region: str = None
    ) -> dict:
        """
        Get temporary AWS credentials for a specific account and role

        Args:
            access_token: AWS SSO access token
            account_id: AWS account ID
            role_name: IAM role name to assume
            region: AWS region

        Returns:
            dict: Temporary AWS credentials including:
                - access_key_id: AWS access key
                - secret_access_key: AWS secret key
                - session_token: Session token
                - expiration: Credential expiration timestamp
        """
        boto3 = _import_boto3()
        BotoCoreError, ClientError = _import_botocore_exceptions()
        try:
            self.sso_client = boto3.client('sso', region_name=region, config=adaptive_retry_config())

            response = self.sso_client.get_role_credentials(
                roleName=role_name,
                accountId=account_id,
                accessToken=access_token
            )

            credentials = response['roleCredentials']

            logger.info(f"Successfully obtained credentials for account {account_id}, role {role_name}")

            return {
                'access_key_id': credentials['accessKeyId'],
                'secret_access_key': credentials['secretAccessKey'],
                'session_token': credentials['sessionToken'],
                'expiration': datetime.fromtimestamp(credentials['expiration'] / 1000).isoformat()
            }

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to get account credentials: {e}")
            raise Exception(f"Failed to retrieve AWS credentials: {str(e)}")

    def list_accounts(self, access_token: str, region: str = None) -> list:
        """
        List AWS accounts available to the user

        Args:
            access_token: AWS SSO access token
            region: AWS region

        Returns:
            list: Available AWS accounts with account_id, account_name, email
        """
        boto3 = _import_boto3()
        BotoCoreError, ClientError = _import_botocore_exceptions()
        try:
            self.sso_client = boto3.client('sso', region_name=region, config=adaptive_retry_config())

            accounts = []
            paginator = self.sso_client.get_paginator('list_accounts')

            for page in paginator.paginate(accessToken=access_token):
                for account in page['accountList']:
                    accounts.append({
                        'account_id': account['accountId'],
                        'account_name': account['accountName'],
                        'email': account['emailAddress']
                    })

            logger.info(f"Found {len(accounts)} AWS accounts")
            return accounts

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to list accounts: {e}")
            raise Exception(f"Failed to list AWS accounts: {str(e)}")

    def list_account_roles(
        self,
        access_token: str,
        account_id: str,
        region: str = None
    ) -> list:
        """
        List available IAM roles for a specific account

        Args:
            access_token: AWS SSO access token
            account_id: AWS account ID
            region: AWS region (required — caller must provide)

        Returns:
            list: Available roles with role_name and account_id
        """
        boto3 = _import_boto3()
        BotoCoreError, ClientError = _import_botocore_exceptions()
        try:
            self.sso_client = boto3.client('sso', region_name=region, config=adaptive_retry_config())

            roles = []
            paginator = self.sso_client.get_paginator('list_account_roles')

            for page in paginator.paginate(
                accessToken=access_token,
                accountId=account_id
            ):
                for role in page['roleList']:
                    role_account_id = role['accountId']
                    roles.append({
                        'role_name': role['roleName'],
                        'account_id': role_account_id,
                        # Construct the full role ARN (FEAT-0099 / ERR-0004) so
                        # callers can assume_role without re-deriving it.
                        'role_arn': f"arn:aws:iam::{role_account_id}:role/{role['roleName']}",
                    })

            logger.info(f"Found {len(roles)} roles for account {account_id}")
            return roles

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to list account roles: {e}")
            raise Exception(f"Failed to list AWS account roles: {str(e)}")

    def assume_role(
        self,
        role_arn: str,
        session_name: str = "BNK-Forge-Session",
        duration_seconds: int = 3600,
        region: str = None
    ) -> dict:
        """
        Assume an IAM role using STS

        Args:
            role_arn: ARN of the role to assume
            session_name: Name for the session
            duration_seconds: Session duration (900-43200 seconds)
            region: AWS region

        Returns:
            dict: Temporary credentials from assumed role
        """
        boto3 = _import_boto3()
        BotoCoreError, ClientError = _import_botocore_exceptions()
        try:
            sts_client = boto3.client('sts', region_name=region, config=adaptive_retry_config())

            response = sts_client.assume_role(
                RoleArn=role_arn,
                RoleSessionName=session_name,
                DurationSeconds=duration_seconds
            )

            credentials = response['Credentials']

            logger.info(f"Successfully assumed role {role_arn}")

            return {
                'access_key_id': credentials['AccessKeyId'],
                'secret_access_key': credentials['SecretAccessKey'],
                'session_token': credentials['SessionToken'],
                'expiration': credentials['Expiration'].isoformat()
            }

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to assume role: {e}")
            raise Exception(f"Failed to assume IAM role: {str(e)}")

    def refresh_credentials(
        self,
        refresh_token: str,
        client_id: str,
        client_secret: str,
        region: str = None
    ) -> dict:
        """
        Refresh AWS SSO access token using refresh token

        Args:
            refresh_token: Refresh token from previous authentication
            client_id: SSO client ID
            client_secret: SSO client secret
            region: AWS region

        Returns:
            dict: New access token and expiration
        """
        boto3 = _import_boto3()
        BotoCoreError, ClientError = _import_botocore_exceptions()
        try:
            if not self.sso_oidc_client:
                self.sso_oidc_client = boto3.client('sso-oidc', region_name=region, config=adaptive_retry_config())

            response = self.sso_oidc_client.create_token(
                clientId=client_id,
                clientSecret=client_secret,
                grantType='refresh_token',
                refreshToken=refresh_token
            )

            logger.info("Successfully refreshed AWS SSO access token")

            return {
                'access_token': response['accessToken'],
                'expires_in': response['expiresIn'],
                'token_type': response.get('tokenType', 'Bearer'),
                'refresh_token': response.get('refreshToken', refresh_token),
                'expires_at': (datetime.now(UTC) + timedelta(seconds=response['expiresIn'])).isoformat()
            }

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Failed to refresh credentials: {e}")
            raise Exception(f"Failed to refresh AWS credentials: {str(e)}")

    def validate_credentials(self, credentials: dict) -> tuple[bool, str | None]:
        """
        Validate AWS credentials by making a test API call

        Args:
            credentials: Dict with access_key_id, secret_access_key, session_token

        Returns:
            tuple: (is_valid, error_message)
        """
        boto3 = _import_boto3()
        BotoCoreError, ClientError = _import_botocore_exceptions()
        try:
            sts_client = boto3.client(
                'sts',
                aws_access_key_id=credentials.get('access_key_id'),
                aws_secret_access_key=credentials.get('secret_access_key'),
                aws_session_token=credentials.get('session_token'),
                config=adaptive_retry_config(),
            )

            # Call get_caller_identity to validate credentials
            response = sts_client.get_caller_identity()

            logger.info(f"Credentials validated for account: {response['Account']}")
            return True, None

        except (ClientError, BotoCoreError) as e:
            logger.error(f"Credential validation failed: {e}")
            return False, str(e)
