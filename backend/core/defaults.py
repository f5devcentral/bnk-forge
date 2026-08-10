"""
Application Defaults

NO HARDCODED DEFAULTS for configurable values.
All configurable settings MUST come from the database (configured via GUI).

This file only contains:
1. Internal constants that are NOT user-configurable
2. Module-specific timeouts that are too granular for GUI

For ALL configurable settings, use `services.defaults_service.get_default()`
which reads from the database. If settings are not configured, the system
will show a warning banner and block operations until configured.
"""

# ============================================================================
# Module-specific Destroy Timeouts (internal - not GUI configurable)
# ============================================================================
# These are too granular for the GUI. They're multipliers of the base
# destroy timeout that the user configures in System > Defaults.
EKS_DESTROY_TIMEOUT = 2700        # 45 minutes (EKS takes longer)
RDS_DESTROY_TIMEOUT = 1800        # 30 minutes

# ============================================================================
# Git Defaults (internal - not GUI configurable)
# ============================================================================
DEFAULT_GIT_REF = "main"
DEFAULT_GIT_CLONE_DEPTH = 1

# ============================================================================
# Internal Constants (not user-configurable)
# ============================================================================
DEFAULT_POLL_INTERVAL = 5         # seconds
DEFAULT_PAGE_SIZE = 50
DEFAULT_LOG_TAIL_LINES = 100
DEFAULT_ENVIRONMENT = "dev"
