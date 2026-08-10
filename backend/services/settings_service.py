"""
Settings Service
Manages application settings stored in the database.

All configurable settings are managed via the UI (System > Defaults).
No .env file dependency — settings are seeded with sensible defaults on first run.
"""
import logging

logger = logging.getLogger(__name__)

# Keys that should be encrypted at rest
ENCRYPTED_SETTING_KEYS = {
    'aws.secret_access_key',
    'aws.session_token',
    'module_library.git_token',
}
