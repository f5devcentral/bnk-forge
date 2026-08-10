"""
System Defaults Service

Manages application-wide default values stored in the database.
All configurable defaults are stored in ApplicationSetting table.

Sensible defaults are pre-populated on first run. Users can change them
via System > Defaults in the UI at any time.
"""
import logging
import os
from typing import Any

from sqlalchemy.orm import Session

from core.encryption import decrypt_value_or_none, encrypt_value
from models import ApplicationSetting

logger = logging.getLogger(__name__)

# Canonical upstream repository. Forks and internal mirrors (e.g. a GitLab
# mirror behind a corporate network) override this with BNKFORGE_REPO_URL so
# builds and version checks point at their own remote. The DB setting
# `system.update_repo_url` overrides both at runtime.
DEFAULT_REPO_URL = os.environ.get("BNKFORGE_REPO_URL", "https://github.com/f5devcentral/bnk-forge")

# ============================================================================
# Default Definitions
# ============================================================================
# These define ALL configurable defaults with sensible initial values.
# On first run, these are seeded into the database.
# Users can change them via System > Defaults in the UI.

SYSTEM_DEFAULTS = {
    # Module Library
    "module_library.git_url": {
        "value": "https://github.com/JLCode-tech/bnk-forge-modules.git",
        "value_type": "string",
        "description": "Git repository URL for module library",
        "category": "module_library",
    },
    "module_library.git_ref": {
        "value": "release/2.2",
        "value_type": "string",
        "description": "Git branch or tag for module library",
        "category": "module_library",
    },
    "module_library.git_token": {
        "value": "",  # Optional - only for private repos
        "value_type": "string",
        "description": "Personal Access Token for private repositories",
        "category": "module_library",
        "is_encrypted": True,
        "optional": True,
    },

    # Blueprint Catalog
    "blueprint_library.git_url": {
        "value": "",
        "value_type": "string",
        "description": "Optional Git repository URL for default blueprint catalog source",
        "category": "blueprint_library",
        "optional": True,
    },
    "blueprint_library.git_ref": {
        "value": "main",
        "value_type": "string",
        "description": "Git branch or tag for default blueprint catalog source",
        "category": "blueprint_library",
        "optional": True,
    },

    # BNK Defaults
    "bnk.far_pull_secret_default": {
        "value": "",
        "value_type": "string",
        "description": "Optional global FAR pull secret (base64 Docker config JSON) used when project/stack cne_pull_secret is not set",
        "category": "bnk",
        "is_encrypted": True,
        "optional": True,
    },

    # Container Supply Chain
    "container.registry_host_allowlist": {
        "value": "ghcr.io,quay.io,docker.io,registry.k8s.io",
        "value_type": "string",
        "description": "Comma-separated allowlist of registry hosts permitted in artifact manifests (bnkforge.artifact.json)",
        "category": "container",
        "optional": True,
    },

    # System Update Source
    "system.update_repo_url": {
        "value": DEFAULT_REPO_URL,
        "value_type": "string",
        "description": "GitHub repository URL for version checks (owner/repo format derived automatically)",
        "category": "system",
        "optional": True,
    },

    # Project Defaults
    "project.default_type": {
        "value": "cloud-aws",
        "value_type": "string",
        "description": "Default project type for new projects (cloud-aws, cloud-azure, cloud-gcp, cloud-ibm, kubernetes)",
        "category": "project",
        "optional": True,
    },

    # Cloud Provider Default Regions
    "cloud.aws.default_region": {
        "value": "us-east-1",
        "value_type": "string",
        "description": "Default AWS region for new projects and templates",
        "category": "cloud",
    },
    "cloud.azure.default_region": {
        "value": "eastus",
        "value_type": "string",
        "description": "Default Azure region for new projects and templates",
        "category": "cloud",
    },
    "cloud.gcp.default_region": {
        "value": "us-central1",
        "value_type": "string",
        "description": "Default GCP region for new projects and templates",
        "category": "cloud",
    },
    "cloud.ibm.default_region": {
        "value": "us-south",
        "value_type": "string",
        "description": "Default IBM Cloud region for new projects and templates",
        "category": "cloud",
    },

    # OpenTofu Timeouts (seconds)
    "opentofu.timeout.init": {
        "value": "300",
        "value_type": "int",
        "description": "Timeout for tofu init command (seconds)",
        "category": "opentofu",
    },
    "opentofu.timeout.plan": {
        "value": "600",
        "value_type": "int",
        "description": "Timeout for tofu plan command (seconds)",
        "category": "opentofu",
    },
    "opentofu.timeout.apply": {
        "value": "1800",
        "value_type": "int",
        "description": "Timeout for tofu apply command (seconds)",
        "category": "opentofu",
    },
    "opentofu.timeout.destroy": {
        "value": "1800",
        "value_type": "int",
        "description": "Timeout for tofu destroy command (seconds)",
        "category": "opentofu",
    },

    # Execution Settings
    "execution.max_retries": {
        "value": "3",
        "value_type": "int",
        "description": "Maximum retry attempts for failed operations",
        "category": "execution",
    },
    "execution.retry_delay": {
        "value": "5",
        "value_type": "int",
        "description": "Delay between retry attempts (seconds)",
        "category": "execution",
    },
}


def get_default(db: Session, key: str) -> Any:
    """
    Get a system default value from the database.

    Returns None if the setting is not found or empty.

    Args:
        db: Database session
        key: Setting key (e.g., "cloud.aws.default_region")

    Returns:
        The setting value (converted to appropriate type), or None if not configured
    """
    setting = db.query(ApplicationSetting).filter(
        ApplicationSetting.key == key
    ).first()

    if not setting or setting.value is None or setting.value == "":
        return None

    stored_value = setting.value
    if setting.is_encrypted and stored_value:
        stored_value = decrypt_value_or_none(stored_value) or ""
    if stored_value == "":
        return None

    # Get value type from definition
    value_type = "string"
    if key in SYSTEM_DEFAULTS:
        value_type = SYSTEM_DEFAULTS[key].get("value_type", "string")

    return _convert_value(stored_value, value_type)


def _convert_value(value: str, value_type: str) -> Any:
    """Convert string value to appropriate Python type."""
    if not value:
        return None
    if value_type == "int":
        return int(value)
    elif value_type == "float":
        return float(value)
    elif value_type == "bool":
        return value.lower() in ("true", "1", "yes")
    elif value_type == "json":
        import json
        return json.loads(value)
    return value


def get_all_defaults(db: Session) -> dict[str, Any]:
    """
    Get all system defaults organized by category.

    Returns:
        Dict with category -> settings mapping
    """
    # Get all settings from database
    settings = db.query(ApplicationSetting).filter(
        ApplicationSetting.key.in_(SYSTEM_DEFAULTS.keys())
    ).all()

    settings_map = {s.key: s for s in settings}

    result = {}
    for key, definition in SYSTEM_DEFAULTS.items():
        category = definition["category"]
        if category not in result:
            result[category] = {}

        # Get value from DB (decrypt if needed)
        db_setting = settings_map.get(key)
        value = db_setting.value if db_setting else ""
        if db_setting and db_setting.is_encrypted and value:
            value = decrypt_value_or_none(value) or ""

        # Convert value
        converted_value = _convert_value(value, definition.get("value_type", "string")) if value else None

        # Extract setting name from key
        setting_name = key.split(".", 1)[1].replace(".", "_") if "." in key else key

        # Check if optional
        is_optional = definition.get("optional", False)

        result[category][setting_name] = {
            "key": key,
            "value": converted_value,
            "raw_value": "" if definition.get("is_encrypted", False) else (value or ""),
            "value_type": definition.get("value_type", "string"),
            "description": definition["description"],
            "is_configured": bool(value),
            "is_optional": is_optional,
            "is_encrypted": bool(definition.get("is_encrypted", False)),
            "suggested_value": definition.get("suggested_value"),
        }

    return result


def set_default(db: Session, key: str, value: Any) -> bool:
    """
    Set a system default value.

    Args:
        db: Database session
        key: Setting key
        value: New value (will be converted to string for storage)

    Returns:
        True if successful
    """
    if key not in SYSTEM_DEFAULTS:
        logger.warning(f"Attempted to set unknown default: {key}")
        return False

    definition = SYSTEM_DEFAULTS[key]

    # Convert value to string for storage
    str_value = str(value) if value is not None else ""
    stored_value = str_value
    if definition.get("is_encrypted", False) and str_value:
        encrypted = encrypt_value(str_value)
        stored_value = encrypted or ""

    # Find or create setting
    setting = db.query(ApplicationSetting).filter(
        ApplicationSetting.key == key
    ).first()

    if setting:
        setting.value = stored_value
        setting.is_encrypted = bool(definition.get("is_encrypted", False))
        logger.info(f"Updated system default: {key}")
    else:
        setting = ApplicationSetting(
            key=key,
            value=stored_value,
            value_type=definition.get("value_type", "string"),
            description=definition["description"],
            category=definition["category"],
            is_encrypted=definition.get("is_encrypted", False),
        )
        db.add(setting)
        logger.info(f"Created system default: {key}")

    # ENG-006: No commit here — caller (route or batch) owns the transaction
    db.flush()
    return True


def set_defaults_batch(db: Session, updates: dict[str, Any]) -> dict[str, bool]:
    """
    Set multiple defaults at once.

    Args:
        db: Database session
        updates: Dict of key -> value pairs

    Returns:
        Dict of key -> success status
    """
    results = {}
    for key, value in updates.items():
        results[key] = set_default(db, key, value)
    return results


def seed_defaults(db: Session) -> int:
    """
    Seed default settings into database on first run.

    Creates settings with sensible defaults. Users can change them in the UI.
    Only creates settings that don't already exist.

    Args:
        db: Database session

    Returns:
        Number of settings seeded
    """
    seeded = 0

    for key, definition in SYSTEM_DEFAULTS.items():
        existing = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == key
        ).first()

        if not existing:
            setting = ApplicationSetting(
                key=key,
                value=definition["value"],  # Empty string - user must configure
                value_type=definition.get("value_type", "string"),
                description=definition["description"],
                category=definition["category"],
                is_encrypted=definition.get("is_encrypted", False),
            )
            db.add(setting)
            seeded += 1
            logger.debug(f"Seeded default: {key}")

    if seeded > 0:
        # ENG-006: Startup seed manages its own transaction
        db.commit()
        logger.info(f"Seeded {seeded} system defaults")

    return seeded


def check_required_configured(db: Session) -> dict[str, Any]:
    """
    Check if all required settings are configured.

    All settings are required EXCEPT those marked with optional=True.

    Args:
        db: Database session

    Returns:
        Dict with 'all_configured' bool and 'missing' list
    """
    missing = []

    for key, definition in SYSTEM_DEFAULTS.items():
        # Skip optional settings
        if definition.get("optional", False):
            continue

        setting = db.query(ApplicationSetting).filter(
            ApplicationSetting.key == key
        ).first()

        if not setting or not setting.value:
            missing.append({
                "key": key,
                "description": definition["description"],
                "category": definition["category"],
                "suggested_value": definition.get("suggested_value"),
            })

    return {
        "all_configured": len(missing) == 0,
        "missing": missing,
        "total_required": len([k for k, v in SYSTEM_DEFAULTS.items() if not v.get("optional", False)]),
        "configured_count": len([k for k, v in SYSTEM_DEFAULTS.items() if not v.get("optional", False)]) - len(missing),
    }
