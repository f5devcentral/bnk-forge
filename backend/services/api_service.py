"""
API Service — DB and business logic for general API endpoints.

Extracted from routes/api.py to separate HTTP handling from domain logic.

Covers:
- Application settings CRUD (get, update, batch update)
- AWS authentication method management
- Sync job status/history
- Cloud provider summary
- Database statistics
"""

import logging
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.cache import cache
from core.encryption import decrypt_value_or_none, encrypt_value
from core.errors import BadRequestError, NotFoundError
from models import (
    ApplicationSetting,
    KubernetesCluster,
    Project,
    SyncJob,
)
from services.defaults_service import get_default
from services.kubeconfig_normalizer import NormalizationSource, normalize_kubeconfig

logger = logging.getLogger(__name__)

# Keys that should be encrypted at rest
ENCRYPTED_SETTING_KEYS = {
    'aws.secret_access_key',
    'aws.session_token',
    'module_library.git_token',
}


class ApiService:
    """Service layer for general API operations."""

    def __init__(self, db: Session):
        self.db = db

    # ================================================================
    # Cloud Providers
    # ================================================================

    def get_provider_summary(self) -> dict[str, Any]:
        """Get summary of cloud providers configured in projects."""
        projects = self.db.query(Project).filter(
            Project.cloud_provider.isnot(None),
            Project.cloud_provider != ''
        ).all()

        provider_stats: dict[str, dict] = {}
        for project in projects:
            provider_name = project.cloud_provider.upper()
            if provider_name not in provider_stats:
                provider_stats[provider_name] = {
                    "name": provider_name, "projects": [], "count": 0, "enabled": 0
                }
            provider_stats[provider_name]["projects"].append(project.name)
            provider_stats[provider_name]["count"] += 1
            if project.cloud_sync_enabled:
                provider_stats[provider_name]["enabled"] += 1

        return {
            "providers": list(provider_stats.values()),
            "total_providers": len(provider_stats)
        }

    # ================================================================
    # Sync Status
    # ================================================================

    def get_sync_status(self) -> dict[str, Any]:
        """Get current sync status and recent job history."""
        recent_jobs = self.db.query(SyncJob).order_by(
            SyncJob.started_at.desc()
        ).limit(10).all()

        jobs_list = []
        for job in recent_jobs:
            jobs_list.append({
                "id": job.id,
                "job_type": job.job_type,
                "resource_type": job.resource_type,
                "status": job.status,
                "started_at": job.started_at.isoformat(),
                "completed_at": job.completed_at.isoformat() if job.completed_at else None,
                "duration_seconds": job.duration_seconds,
                "records_synced": job.records_synced,
                "records_created": job.records_created,
                "records_updated": job.records_updated,
                "error_message": job.error_message
            })

        last_syncs = {}
        for job_type in ["module_library", "opentofu", "drift"]:
            last_sync = self.db.query(SyncJob).filter(
                SyncJob.job_type == job_type,
                SyncJob.status == "success"
            ).order_by(SyncJob.completed_at.desc()).first()
            if last_sync:
                last_syncs[job_type] = {
                    "last_synced_at": last_sync.completed_at.isoformat(),
                    "records_synced": last_sync.records_synced,
                    "duration_seconds": last_sync.duration_seconds
                }

        running_jobs = self.db.query(SyncJob).filter(SyncJob.status == "running").all()

        return {
            "recent_jobs": jobs_list,
            "last_syncs": last_syncs,
            "running_jobs_count": len(running_jobs),
            "scheduler_running": False,
            "v2_note": "In v2, use /api/module-library/sync for module catalog updates"
        }

    # ================================================================
    # Application Settings
    # ================================================================

    def get_settings(self) -> dict[str, Any]:
        """Get all application settings grouped by category."""
        settings = self.db.query(ApplicationSetting).order_by(
            ApplicationSetting.category, ApplicationSetting.key
        ).all()

        settings_by_category: dict[str, list] = {}
        for setting in settings:
            if setting.category == "deprecated":
                continue
            if setting.category not in settings_by_category:
                settings_by_category[setting.category] = []
            settings_by_category[setting.category].append({
                "key": setting.key,
                "value": setting.value,
                "value_type": setting.value_type,
                "description": setting.description,
                "is_encrypted": setting.is_encrypted
            })

        return {"settings": settings_by_category}

    def update_setting(self, key: str, value: str) -> dict[str, Any]:
        """Update a single application setting."""
        setting = self.db.query(ApplicationSetting).filter(
            ApplicationSetting.key == key
        ).first()
        if not setting:
            raise NotFoundError("setting", key)

        setting.value = value
        setting.updated_at = datetime.now(UTC)

        if "interval" in key:
            logger.info("Sync interval changed, scheduler restart may be required...")

        return {"message": "Setting updated successfully", "key": key, "value": value}

    def batch_update_settings(self, settings_dict: dict) -> dict[str, Any]:
        """Batch update multiple application settings."""
        updated = []
        errors = []

        for key, value in settings_dict.items():
            try:
                setting = self.db.query(ApplicationSetting).filter(
                    ApplicationSetting.key == key
                ).first()
                if setting:
                    setting.value = value
                    setting.updated_at = datetime.now(UTC)
                    updated.append(key)
                else:
                    category = key.split('.')[0] if '.' in key else "general"
                    new_setting = ApplicationSetting(
                        key=key, value=value, value_type="string",
                        description=f"Setting for {key}", category=category
                    )
                    self.db.add(new_setting)
                    updated.append(key)
            except Exception as e:
                errors.append({"key": key, "error": str(e)})

        self.db.flush()
        return {
            "success": True,
            "message": f"Updated {len(updated)} setting(s)",
            "updated": updated,
            "errors": errors
        }

    # ================================================================
    # AWS Auth Method
    # ================================================================

    def _upsert_setting(self, key: str, value: str, description: str = "",
                        category: str = "aws", should_encrypt: bool = False) -> None:
        """Upsert an ApplicationSetting row."""
        setting = self.db.query(ApplicationSetting).filter(
            ApplicationSetting.key == key
        ).first()
        stored_value = encrypt_value(value) if should_encrypt and value else value
        if setting:
            setting.value = stored_value
            if should_encrypt:
                setting.is_encrypted = True
        else:
            self.db.add(ApplicationSetting(
                key=key, value=stored_value, description=description,
                category=category, is_encrypted=should_encrypt
            ))

    def set_aws_auth_method(self, request: dict) -> dict[str, Any]:
        """Set AWS authentication method and credentials."""
        auth_method = request.get('auth_method')
        credentials = request.get('credentials', {})
        region = request.get('region')

        if not auth_method or auth_method not in ['profile', 'access_keys']:
            raise BadRequestError("Invalid auth_method. Must be 'profile' or 'access_keys'",
                                  code="INVALID_AUTH_METHOD")

        updated_keys = []

        # Set auth method
        self._upsert_setting('aws.auth_method', auth_method,
                             'AWS authentication method: profile or access_keys')
        updated_keys.append('aws.auth_method')

        if auth_method == 'profile':
            profile_name = credentials.get('profile')
            if not profile_name:
                raise BadRequestError("Profile name required for profile auth method",
                                      code="MISSING_PROFILE")
            self._upsert_setting('aws.profile', profile_name, 'AWS CLI profile name')
            updated_keys.append('aws.profile')

            # Clear access keys when using profile
            for key in ['aws.access_key_id', 'aws.secret_access_key', 'aws.session_token']:
                setting = self.db.query(ApplicationSetting).filter(
                    ApplicationSetting.key == key
                ).first()
                if setting:
                    setting.value = None

        elif auth_method == 'access_keys':
            access_key_id = credentials.get('access_key_id')
            secret_access_key = credentials.get('secret_access_key')
            session_token = credentials.get('session_token')

            if not access_key_id or not secret_access_key:
                raise BadRequestError(
                    "access_key_id and secret_access_key required for access_keys auth method")

            cred_settings = [
                ('aws.access_key_id', access_key_id, 'AWS access key ID'),
                ('aws.secret_access_key', secret_access_key, 'AWS secret access key'),
            ]
            if session_token:
                cred_settings.append(
                    ('aws.session_token', session_token, 'AWS session token (temporary credentials)'))

            for key, value, desc in cred_settings:
                should_encrypt = key in ENCRYPTED_SETTING_KEYS
                self._upsert_setting(key, value, desc, should_encrypt=should_encrypt)
                updated_keys.append(key)

            # Clear profile when using access keys
            profile_setting = self.db.query(ApplicationSetting).filter(
                ApplicationSetting.key == 'aws.profile'
            ).first()
            if profile_setting:
                profile_setting.value = None

        # Set region if provided
        if region:
            self._upsert_setting('aws.region', region, 'AWS default region')
            updated_keys.append('aws.region')

        self.db.flush()
        logger.info(f"AWS authentication method changed to: {auth_method}")

        # Auto-refresh EKS kubeconfigs
        refreshed_clusters, failed_clusters = self._refresh_eks_kubeconfigs()

        return {
            "success": True,
            "auth_method": auth_method,
            "updated_keys": updated_keys,
            "message": f"AWS authentication method set to {auth_method}. Changes will take effect immediately.",
            "kubeconfig_refresh": {
                "refreshed": refreshed_clusters,
                "failed": failed_clusters
            } if refreshed_clusters or failed_clusters else None
        }

    def _refresh_eks_kubeconfigs(self) -> tuple:
        """Auto-refresh kubeconfig for all EKS clusters after credential change."""
        refreshed_clusters: list[str] = []
        failed_clusters: list[str] = []

        try:
            aws_env = self._build_aws_env_from_settings()

            eks_clusters = self.db.query(KubernetesCluster).filter(
                KubernetesCluster.cloud_provider.in_(['eks', 'aws'])
            ).all()

            for cluster in eks_clusters:
                if not cluster.name or not cluster.region:
                    continue
                try:
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
                        temp_kubeconfig = f.name
                    try:
                        cmd = [
                            'aws', 'eks', 'update-kubeconfig',
                            '--name', cluster.name,
                            '--region', cluster.region,
                            '--kubeconfig', temp_kubeconfig
                        ]
                        result = subprocess.run(cmd, capture_output=True, text=True,
                                                timeout=30, env=aws_env)
                        if result.returncode == 0:
                            with open(temp_kubeconfig) as f:
                                kubeconfig_yaml = f.read()
                            # Assert invariant — aws eks update-kubeconfig must produce portable output.
                            # Hard-fail on violation (kubeconfig_invariant_violation).
                            try:
                                kubeconfig_yaml = normalize_kubeconfig(
                                    kubeconfig_yaml,
                                    source=NormalizationSource.CLOUD_API_GENERATED,
                                )
                            except Exception as norm_exc:
                                logger.error(
                                    "kubeconfig_invariant_violation: EKS CLI refresh produced "
                                    "non-portable kubeconfig for cluster %s: %s",
                                    cluster.name, norm_exc,
                                )
                                failed_clusters.append(f"{cluster.name}: {norm_exc}")
                                continue
                            cluster.kubeconfig_encrypted = encrypt_value(kubeconfig_yaml)
                            refreshed_clusters.append(cluster.name)
                        else:
                            failed_clusters.append(f"{cluster.name}: {result.stderr}")
                    finally:
                        if os.path.exists(temp_kubeconfig):
                            os.remove(temp_kubeconfig)
                except Exception as e:
                    failed_clusters.append(f"{cluster.name}: {str(e)}")

            if refreshed_clusters:
                self.db.flush()
        except Exception as e:
            logger.error(f"Error during automatic kubeconfig refresh: {e}")

        return refreshed_clusters, failed_clusters

    def _build_aws_env_from_settings(self) -> dict[str, str]:
        """Build AWS env dict from current ApplicationSettings."""
        aws_env = os.environ.copy()

        for key, env_var in [
            ('aws.access_key_id', 'AWS_ACCESS_KEY_ID'),
            ('aws.secret_access_key', 'AWS_SECRET_ACCESS_KEY'),
            ('aws.session_token', 'AWS_SESSION_TOKEN'),
            ('aws.region', 'AWS_DEFAULT_REGION'),
        ]:
            setting = self.db.query(ApplicationSetting).filter(
                ApplicationSetting.key == key
            ).first()
            if setting and setting.value:
                value = setting.value
                if setting.is_encrypted:
                    value = decrypt_value_or_none(value) or value
                aws_env[env_var] = value

        return aws_env

    def get_aws_auth_method(self) -> dict[str, Any]:
        """Get current AWS authentication method and configuration status."""
        auth_method_setting = self.db.query(ApplicationSetting).filter(
            ApplicationSetting.key == 'aws.auth_method'
        ).first()
        auth_method = auth_method_setting.value if auth_method_setting else 'access_keys'

        profile_setting = self.db.query(ApplicationSetting).filter(
            ApplicationSetting.key == 'aws.profile'
        ).first()
        profile = profile_setting.value if profile_setting else None

        access_key_setting = self.db.query(ApplicationSetting).filter(
            ApplicationSetting.key == 'aws.access_key_id'
        ).first()
        access_keys_configured = bool(access_key_setting and access_key_setting.value)

        session_token_setting = self.db.query(ApplicationSetting).filter(
            ApplicationSetting.key == 'aws.session_token'
        ).first()
        has_session_token = bool(session_token_setting and session_token_setting.value)

        region_setting = self.db.query(ApplicationSetting).filter(
            ApplicationSetting.key == 'aws.region'
        ).first()
        region = region_setting.value if region_setting else get_default(self.db, "cloud.aws.default_region")

        return {
            "success": True,
            "auth_method": auth_method,
            "profile": profile,
            "profile_configured": bool(profile),
            "access_keys_configured": access_keys_configured,
            "has_session_token": has_session_token,
            "region": region
        }

    # ================================================================
    # Database Stats
    # ================================================================

    def get_database_stats(self) -> dict[str, Any]:
        """Get database statistics (cached 2 min)."""
        cache_key = "database_stats"
        cached = cache.get(cache_key)
        if cached:
            return cached

        result = self.db.execute(text("""
            SELECT 'projects' as name, COUNT(*) as count FROM projects
            UNION ALL SELECT 'active_projects', COUNT(*) FROM projects WHERE is_active = true
            UNION ALL SELECT 'synced_projects', COUNT(*) FROM projects WHERE sync_status = 'success'
            UNION ALL SELECT 'clusters', COUNT(*) FROM kubernetes_clusters
            UNION ALL SELECT 'gateways', COUNT(*) FROM k8s_gateways
            UNION ALL SELECT 'project_modules', COUNT(*) FROM project_modules
            UNION ALL SELECT 'module_library', COUNT(*) FROM module_library
            UNION ALL SELECT 'sync_jobs', COUNT(*) FROM sync_jobs
            UNION ALL SELECT 'settings', COUNT(*) FROM application_settings
        """)).fetchall()

        stats = {row[0]: row[1] for row in result}

        projects = self.db.query(Project.id, Project.name, Project.is_active).limit(100).all()
        project_info = [
            {"id": p.id, "name": p.name, "is_active": p.is_active}
            for p in projects
        ]

        response = {
            "stats": stats,
            "projects": project_info,
            "cached_at": datetime.now(UTC).isoformat() + 'Z'
        }
        cache.set(cache_key, response, ttl_seconds=120)
        return response


