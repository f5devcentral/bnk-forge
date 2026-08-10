"""
Credential Refresh Service
Handles automatic refresh of expiring credentials for AWS, GCP, Azure

ENG-006: This service retains its own db.commit() calls because it runs as a
background loop (started from main.py lifespan) with its own SessionLocal()
instances, and force_refresh_project creates its own session. These are
legitimate self-managed transactions outside of route/task context.
"""
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from core.encryption import decrypt_value, encrypt_value
from database import SessionLocal
from models import CloudCredentialTemplate, Notification, Project
from services.aws_auth_service import AWSAuthService
from services.azure_oauth_service import request_azure_oauth_token

logger = logging.getLogger(__name__)


class CredentialRefreshService:
    """Service for monitoring and refreshing cloud credentials"""

    def __init__(self):
        self.aws_auth = AWSAuthService()
        self.refresh_threshold_minutes = 15  # Refresh if expiring within 15 minutes
        self.warning_threshold_hours_24 = 24  # 24 hour warning
        self.warning_threshold_hours_1 = 1  # 1 hour warning

    def check_and_refresh_all(self):
        """
        Check all projects and credential templates, refresh expiring credentials

        Called periodically by background scheduler
        """
        logger.info("Starting credential refresh check...")

        db = SessionLocal()
        try:
            refreshed_count = 0
            failed_count = 0

            # Check and refresh credential templates (SSO and regular)
            templates = db.query(CloudCredentialTemplate).filter(
                (CloudCredentialTemplate.aws_session_token_encrypted.isnot(None)) |
                (CloudCredentialTemplate.aws_sso_enabled)
            ).all()

            for template in templates:
                try:
                    if self.check_and_refresh_template(template, db):
                        refreshed_count += 1
                except Exception as e:
                    logger.error(f"Failed to refresh credential template {template.id}: {e}")
                    failed_count += 1

            # Check and refresh project credentials (legacy)
            projects = db.query(Project).filter(
                Project.cloud_credentials_encrypted.isnot(None)
            ).all()

            for project in projects:
                try:
                    if self.check_and_refresh_project(project, db):
                        refreshed_count += 1
                except Exception as e:
                    logger.error(f"Failed to refresh credentials for project {project.id}: {e}")
                    failed_count += 1

            logger.info(f"Credential refresh complete: {refreshed_count} refreshed, {failed_count} failed")

        except Exception as e:
            logger.error(f"Credential refresh check failed: {e}")
        finally:
            db.close()

    def check_and_refresh_template(self, template: CloudCredentialTemplate, db: Session) -> bool:
        """
        Check and refresh credentials for a CloudCredentialTemplate

        Args:
            template: Credential template to check
            db: Database session

        Returns:
            bool: True if credentials were refreshed
        """
        try:
            # Check if SSO template with refresh capability
            if template.aws_sso_enabled and template.aws_sso_refresh_token_encrypted:
                return self._refresh_sso_template(template, db)

            # Check if has regular session token (temporary credentials)
            if not template.aws_session_token_encrypted:
                return False

            # Check if has expiration time
            if not template.aws_credentials_expiry:
                logger.debug(f"Template {template.id} '{template.name}' has no expiration time")
                return False

            # Calculate time until expiry
            now_utc = datetime.now(UTC)
            time_until_expiry = template.aws_credentials_expiry - now_utc

            # If expiring within threshold, log warning
            if time_until_expiry.total_seconds() < (self.refresh_threshold_minutes * 60):
                minutes_remaining = int(time_until_expiry.total_seconds() / 60)
                logger.warning(
                    f"⚠️  Credentials for template '{template.name}' (ID {template.id}) expiring in {minutes_remaining} minutes! "
                    f"Manual refresh required - go to Settings → Cloud Credential Templates"
                )
                return False

            return False

        except Exception as e:
            logger.error(f"Failed to check/refresh template {template.id}: {e}")
            return False

    def _refresh_sso_template(self, template: CloudCredentialTemplate, db: Session) -> bool:
        """
        Refresh SSO credentials for a template

        Args:
            template: Template with SSO enabled
            db: Database session

        Returns:
            bool: True if refresh successful
        """
        try:
            # Check if SSO token is expiring
            if not template.aws_sso_token_expiry:
                return False

            now_utc = datetime.now(UTC)
            time_until_expiry = template.aws_sso_token_expiry - now_utc

            # Check for 24-hour warning
            hours_until_expiry = time_until_expiry.total_seconds() / 3600
            if 23 < hours_until_expiry < 25:  # 24 hour window
                self._create_notification(
                    db,
                    title="SSO Credentials Expiring in 24 Hours",
                    message=f"Credentials for '{template.name}' will expire tomorrow. Auto-refresh will attempt to renew them.",
                    resource_type="credential_template",
                    resource_id=template.id
                )

            # Check for 1-hour warning
            if 0.9 < hours_until_expiry < 1.1:  # 1 hour window
                self._create_notification(
                    db,
                    title="SSO Credentials Expiring in 1 Hour",
                    message=f"Credentials for '{template.name}' will expire soon. Auto-refresh is attempting to renew them.",
                    resource_type="credential_template",
                    resource_id=template.id
                )

            # If expiring within threshold, refresh
            if time_until_expiry.total_seconds() < (self.refresh_threshold_minutes * 60):
                logger.info(f"SSO token for template '{template.name}' (ID {template.id}) expiring soon, refreshing...")

                # Decrypt stored credentials
                refresh_token = decrypt_value(template.aws_sso_refresh_token_encrypted)
                client_secret = decrypt_value(template.aws_sso_client_secret_encrypted)

                if not refresh_token or not client_secret:
                    logger.error(f"Failed to decrypt SSO credentials for template {template.id}")
                    return False

                # Refresh the SSO access token
                token_data = self.aws_auth.refresh_credentials(
                    refresh_token=refresh_token,
                    client_id=template.aws_sso_client_id,
                    client_secret=client_secret,
                    region=template.aws_sso_region or template.region
                )

                # Update SSO tokens
                template.aws_sso_access_token_encrypted = encrypt_value(token_data['access_token'])

                # Update refresh token if a new one was returned
                if token_data.get('refresh_token'):
                    template.aws_sso_refresh_token_encrypted = encrypt_value(token_data['refresh_token'])

                # Update expiry
                expires_in = token_data.get('expires_in', 28800)
                template.aws_sso_token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)

                # Get fresh AWS credentials if account/role configured
                if template.aws_sso_account_id and template.aws_sso_role_name:
                    credentials = self.aws_auth.get_account_credentials(
                        access_token=token_data['access_token'],
                        account_id=template.aws_sso_account_id,
                        role_name=template.aws_sso_role_name,
                        region=template.aws_sso_region or template.region
                    )

                    # Update AWS credentials
                    template.aws_access_key_id = credentials['access_key_id']
                    template.aws_secret_access_key_encrypted = encrypt_value(credentials['secret_access_key'])
                    template.aws_session_token_encrypted = encrypt_value(credentials['session_token'])

                    # Parse expiration
                    expiration_str = credentials['expiration']
                    template.aws_credentials_expiry = datetime.fromisoformat(expiration_str)

                # ENG-006: Background refresh loop manages its own transaction
                db.commit()

                logger.info(f"✓ Successfully refreshed SSO credentials for template '{template.name}' (ID {template.id})")

                # Send success notification
                self._create_notification(
                    db,
                    title="SSO Credentials Refreshed",
                    message=f"Successfully refreshed credentials for '{template.name}'. New expiry: {template.aws_credentials_expiry.strftime('%Y-%m-%d %H:%M UTC') if template.aws_credentials_expiry else 'N/A'}",
                    resource_type="credential_template",
                    resource_id=template.id
                )

                return True

            return False

        except Exception as e:
            logger.error(f"Failed to refresh SSO template {template.id}: {e}")

            # Send error notification
            self._create_notification(
                db,
                title="SSO Credential Refresh Failed",
                message=f"Failed to refresh credentials for '{template.name}': {str(e)}. Manual re-authentication may be required.",
                resource_type="credential_template",
                resource_id=template.id
            )

            return False

    def check_and_refresh_project(self, project: Project, db: Session, force: bool = False) -> bool:
        """
        Check and refresh credentials for a single project

        Args:
            project: Project to check
            db: Database session
            force: When True, refresh regardless of the expiry threshold (used by
                the manual/forced refresh endpoint). Static (no-expiration)
                credentials are still skipped — there is nothing to refresh.

        Returns:
            bool: True if credentials were refreshed
        """
        try:
            # Decrypt credentials
            cred_json = decrypt_value(project.cloud_credentials_encrypted)
            if not cred_json:
                return False

            cred_data = json.loads(cred_json)

            # Check expiration
            expiration = cred_data.get('expiration') or cred_data.get('expires_at')
            if not expiration:
                # No expiration means static credentials (don't refresh)
                return False

            # S15-013: Use timezone-aware datetime to avoid TypeError when comparing
            expiration_dt = datetime.fromisoformat(expiration.replace('Z', '+00:00'))
            time_until_expiry = expiration_dt - datetime.now(UTC)

            # If expiring within threshold (or forced), refresh
            if force or time_until_expiry.total_seconds() < (self.refresh_threshold_minutes * 60):
                if force:
                    logger.info(f"Force-refreshing credentials for project {project.id}...")
                else:
                    logger.info(f"Credentials for project {project.id} expiring soon, refreshing...")

                if project.cloud_provider == 'aws':
                    return self.refresh_aws_credentials(project, cred_data, db)
                elif project.cloud_provider == 'gcp':
                    return self.refresh_gcp_credentials(project, cred_data, db)
                elif project.cloud_provider == 'azure':
                    return self.refresh_azure_credentials(project, cred_data, db)

            return False

        except Exception as e:
            logger.error(f"Failed to check/refresh project {project.id}: {e}")
            return False

    def refresh_aws_credentials(self, project: Project, cred_data: dict, db: Session) -> bool:
        """
        Refresh AWS credentials (SSO or STS)

        Args:
            project: Project to refresh
            cred_data: Current credential data
            db: Database session

        Returns:
            bool: True if refresh successful
        """
        try:
            auth_method = cred_data.get('auth_method')

            if auth_method == 'sso':
                # Refresh SSO token using refresh_token
                refresh_token = cred_data.get('refresh_token')
                if not refresh_token:
                    logger.warning(f"No refresh token for project {project.id}, cannot auto-refresh SSO")
                    return False

                # Refresh the access token
                new_token = self.aws_auth.refresh_credentials(
                    refresh_token=refresh_token,
                    client_id=cred_data.get('client_id'),
                    client_secret=cred_data.get('client_secret'),
                    region=cred_data.get('region')
                )

                # Update credentials
                cred_data['access_token'] = new_token['access_token']
                cred_data['expires_at'] = new_token['expires_at']
                if 'refresh_token' in new_token:
                    cred_data['refresh_token'] = new_token['refresh_token']

                # Now refresh account credentials if we have account/role info
                if cred_data.get('account_id') and cred_data.get('role_name'):
                    account_creds = self.aws_auth.get_account_credentials(
                        access_token=new_token['access_token'],
                        account_id=cred_data['account_id'],
                        role_name=cred_data['role_name'],
                        region=cred_data.get('region')
                    )

                    # Update with new temporary credentials
                    cred_data['access_key_id'] = account_creds['access_key_id']
                    cred_data['secret_access_key'] = account_creds['secret_access_key']
                    cred_data['session_token'] = account_creds['session_token']
                    cred_data['expiration'] = account_creds['expiration']

                project.cloud_credentials_encrypted = encrypt_value(json.dumps(cred_data))
                # ENG-006: Background refresh loop manages its own transaction
                db.commit()

                logger.info(f"Successfully refreshed AWS SSO credentials for project {project.id}")
                return True

            elif auth_method == 'assume_role':
                # Re-assume the role to get fresh credentials
                role_arn = cred_data.get('role_arn')
                if not role_arn:
                    logger.warning(f"No role ARN for project {project.id}, cannot refresh")
                    return False

                new_creds = self.aws_auth.assume_role(
                    role_arn=role_arn,
                    session_name=f"BNK-Forge-Refresh-{project.id}",
                    duration_seconds=3600,
                    region=cred_data.get('region')
                )

                # Update credentials
                cred_data['access_key_id'] = new_creds['access_key_id']
                cred_data['secret_access_key'] = new_creds['secret_access_key']
                cred_data['session_token'] = new_creds['session_token']
                cred_data['expiration'] = new_creds['expiration']

                project.cloud_credentials_encrypted = encrypt_value(json.dumps(cred_data))
                # ENG-006: Background refresh loop manages its own transaction
                db.commit()

                logger.info(f"Successfully refreshed AWS AssumeRole credentials for project {project.id}")
                return True

            else:
                logger.warning(f"Unknown AWS auth method '{auth_method}' for project {project.id}")
                return False

        except Exception as e:
            logger.error(f"Failed to refresh AWS credentials for project {project.id}: {e}")
            return False

    def refresh_gcp_credentials(self, project: Project, cred_data: dict, db: Session) -> bool:
        """
        Refresh GCP credentials using service-account key or OAuth2 refresh token.

        Supports two flows:
        1. Service-account JSON key → mint a fresh access token via JWT assertion
        2. OAuth2 refresh token → exchange for a new access token

        Returns True if refresh succeeded.
        """
        try:
            import time as _time

            import requests as http_requests

            auth_method = cred_data.get('auth_method', 'service_account')

            if auth_method == 'service_account':
                # --- JWT assertion flow (RFC 7523) ---
                sa_key = cred_data.get('service_account_key') or cred_data.get('credentials_json')
                if not sa_key:
                    logger.warning(f"No service-account key for project {project.id}, cannot refresh GCP")
                    return False

                sa_data = json.loads(sa_key) if isinstance(sa_key, str) else sa_key

                import jwt as pyjwt  # PyJWT

                now = int(_time.time())
                payload = {
                    "iss": sa_data["client_email"],
                    "scope": "https://www.googleapis.com/auth/cloud-platform",
                    "aud": "https://oauth2.googleapis.com/token",
                    "iat": now,
                    "exp": now + 3600,
                }
                assertion = pyjwt.encode(payload, sa_data["private_key"], algorithm="RS256")

                resp = http_requests.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": assertion,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                token_data = resp.json()

                cred_data['access_token'] = token_data['access_token']
                cred_data['expiration'] = (
                    datetime.now(UTC) + timedelta(seconds=token_data.get('expires_in', 3600))
                ).isoformat()

            elif auth_method == 'refresh_token':
                # --- OAuth2 refresh-token flow ---
                refresh_token = cred_data.get('refresh_token')
                client_id = cred_data.get('client_id')
                client_secret = cred_data.get('client_secret')
                if not refresh_token or not client_id or not client_secret:
                    logger.warning(f"Missing refresh token / client creds for GCP project {project.id}")
                    return False

                resp = http_requests.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                token_data = resp.json()

                cred_data['access_token'] = token_data['access_token']
                if token_data.get('refresh_token'):
                    cred_data['refresh_token'] = token_data['refresh_token']
                cred_data['expiration'] = (
                    datetime.now(UTC) + timedelta(seconds=token_data.get('expires_in', 3600))
                ).isoformat()

            else:
                logger.warning(f"Unknown GCP auth method '{auth_method}' for project {project.id}")
                return False

            project.cloud_credentials_encrypted = encrypt_value(json.dumps(cred_data))
            # ENG-006: Background refresh loop manages its own transaction
            db.commit()

            logger.info(f"Successfully refreshed GCP credentials for project {project.id}")
            return True

        except Exception as e:
            logger.error(f"Failed to refresh GCP credentials for project {project.id}: {e}")
            return False

    def refresh_azure_credentials(self, project: Project, cred_data: dict, db: Session) -> bool:
        """
        Refresh Azure credentials using service-principal client-credentials
        or OAuth2 refresh-token flow.

        Supports two flows:
        1. Service principal (client_id + client_secret + tenant_id) → client credentials grant
        2. OAuth2 refresh token → exchange for a new access token

        Returns True if refresh succeeded.
        """
        try:
            auth_method = cred_data.get('auth_method', 'service_principal')
            tenant_id = cred_data.get('tenant_id')

            if not tenant_id:
                logger.warning(f"No tenant_id for Azure project {project.id}, cannot refresh")
                return False

            if auth_method == 'service_principal':
                client_id = cred_data.get('client_id')
                client_secret = cred_data.get('client_secret')
                if not client_id or not client_secret:
                    logger.warning(f"Missing client_id/client_secret for Azure project {project.id}")
                    return False

                token_data = request_azure_oauth_token(
                    tenant_id=tenant_id,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "scope": "https://management.azure.com/.default",
                    },
                    timeout=30,
                )

                cred_data['access_token'] = token_data['access_token']
                cred_data['expiration'] = (
                    datetime.now(UTC) + timedelta(seconds=token_data.get('expires_in', 3600))
                ).isoformat()

            elif auth_method == 'refresh_token':
                refresh_token = cred_data.get('refresh_token')
                client_id = cred_data.get('client_id')
                if not refresh_token or not client_id:
                    logger.warning(f"Missing refresh token / client_id for Azure project {project.id}")
                    return False

                data = {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "scope": "https://management.azure.com/.default offline_access",
                }
                if cred_data.get('client_secret'):
                    data["client_secret"] = cred_data['client_secret']

                token_data = request_azure_oauth_token(
                    tenant_id=tenant_id,
                    data=data,
                    timeout=30,
                )

                cred_data['access_token'] = token_data['access_token']
                if token_data.get('refresh_token'):
                    cred_data['refresh_token'] = token_data['refresh_token']
                cred_data['expiration'] = (
                    datetime.now(UTC) + timedelta(seconds=token_data.get('expires_in', 3600))
                ).isoformat()

            else:
                logger.warning(f"Unknown Azure auth method '{auth_method}' for project {project.id}")
                return False

            project.cloud_credentials_encrypted = encrypt_value(json.dumps(cred_data))
            # ENG-006: Background refresh loop manages its own transaction
            db.commit()

            logger.info(f"Successfully refreshed Azure credentials for project {project.id}")
            return True

        except Exception as e:
            logger.error(f"Failed to refresh Azure credentials for project {project.id}: {e}")
            return False

    def get_credential_status(self, project: Project) -> dict | None:
        """
        Get credential expiration status for a project

        Args:
            project: Project to check

        Returns:
            dict: Credential status information or None
        """
        try:
            if not project.cloud_credentials_encrypted:
                return None

            cred_json = decrypt_value(project.cloud_credentials_encrypted)
            if not cred_json:
                return None

            cred_data = json.loads(cred_json)

            expiration = cred_data.get('expiration') or cred_data.get('expires_at')
            if not expiration:
                return {
                    'has_expiration': False,
                    'provider': project.cloud_provider,
                    'auth_method': cred_data.get('auth_method')
                }

            # S15-013: Use timezone-aware datetime to avoid TypeError when comparing
            expiration_dt = datetime.fromisoformat(expiration.replace('Z', '+00:00'))
            time_until_expiry = expiration_dt - datetime.now(UTC)

            is_expired = time_until_expiry.total_seconds() <= 0
            is_expiring_soon = 0 < time_until_expiry.total_seconds() < (self.refresh_threshold_minutes * 60)

            return {
                'has_expiration': True,
                'provider': project.cloud_provider,
                'auth_method': cred_data.get('auth_method'),
                'expiration': expiration,
                'time_until_expiry_seconds': time_until_expiry.total_seconds(),
                'is_expired': is_expired,
                'is_expiring_soon': is_expiring_soon,
                'can_auto_refresh': cred_data.get('refresh_token') is not None or cred_data.get('role_arn') is not None
            }

        except Exception as e:
            logger.error(f"Failed to get credential status for project {project.id}: {e}")
            return None

    def force_refresh_project(self, project_id: int) -> bool:
        """
        Force refresh credentials for a specific project

        Args:
            project_id: Project ID to refresh

        Returns:
            bool: True if refresh successful
        """
        db = SessionLocal()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                logger.error(f"Project {project_id} not found")
                return False

            return self.check_and_refresh_project(project, db, force=True)

        except Exception as e:
            logger.error(f"Failed to force refresh project {project_id}: {e}")
            return False
        finally:
            db.close()

    def _create_notification(self, db: Session, title: str, message: str,
                           resource_type: str = None, resource_id: int = None):
        """
        Create a notification for admins

        Args:
            db: Database session
            title: Notification title
            message: Notification message
            resource_type: Type of resource (e.g., 'credential_template')
            resource_id: ID of the resource
        """
        try:
            notification = Notification(
                user="admin",  # Send to admin user
                type="warning",
                title=title,
                message=message,
                resource_type=resource_type,
                resource_id=resource_id,
                is_read=False,
                created_at=datetime.now(UTC)
            )
            db.add(notification)
            # ENG-006: Background refresh loop manages its own transaction
            db.commit()
            logger.info(f"Created notification: {title}")
        except Exception as e:
            logger.error(f"Failed to create notification: {e}")
            # ENG-006: Background refresh loop manages its own transaction
            db.rollback()
