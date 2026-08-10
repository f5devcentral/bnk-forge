"""Infrastructure access output normalization and durable SSH key handling.

This service keeps infrastructure access metadata truthful by ensuring that
module outputs do not rely on ephemeral backend-container home paths such as
``~/.ssh/...`` for private-key access.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Narrow, intentional infrastructure access keys only.
INFRA_PRIVATE_KEY_PATH_FIELD = "infrastructure_private_key_path"
INFRA_JUMPHOST_COMMAND_FIELD = "jumphost_ssh_command"
INFRA_PRIVATE_KEY_FIELDS = (
    "infrastructure_private_key",
    "infrastructure_private_key_pem",
)

INFRA_ACCESS_STATUS_FIELD = "infrastructure_access_status"
INFRA_ACCESS_MESSAGE_FIELD = "infrastructure_access_message"
INFRA_PRIVATE_KEY_AVAILABLE_FIELD = "infrastructure_private_key_available"

INFRA_ACCESS_STATUS_READY = "ready"
INFRA_ACCESS_STATUS_RECOVERY_REQUIRED = "recovery_required"

_PRIVATE_KEY_HEADER_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_SSH_IDENTITY_ARG_RE = re.compile(r"(-i\s+)(\"[^\"]+\"|'[^']+'|[^\s]+)")


@dataclass(frozen=True)
class InfrastructureAccessNormalizationResult:
    """Result envelope for output normalization and repair behavior."""

    outputs: dict[str, Any]
    changed: bool
    key_available: bool
    key_path: str


def get_durable_infra_private_key_path(project_id: int, module_id: int) -> str:
    """Deterministic durable key path for a module."""
    return f"/app/state/{project_id}/{module_id}/infrastructure/infrastructure-access.pem"


def cleanup_durable_infra_private_key(project_id: int, module_id: int) -> bool:
    """Delete durable infrastructure private key file if present."""
    path = get_durable_infra_private_key_path(project_id, module_id)
    if not os.path.exists(path):
        return False

    try:
        os.remove(path)
        logger.info(
            "Removed durable infrastructure private key for project=%s module=%s",
            project_id,
            module_id,
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive logging path
        logger.warning(
            "Failed removing durable infrastructure private key for project=%s module=%s: %s",
            project_id,
            module_id,
            exc,
        )
        return False


def normalize_infrastructure_access_outputs(
    outputs: dict[str, Any] | None,
    project_id: int,
    module_id: int,
) -> InfrastructureAccessNormalizationResult:
    """Normalize infrastructure access outputs and repair stale key paths.

    Behavior is intentionally narrow to infrastructure SSH access keys.
    """
    normalized = dict(outputs or {})
    changed = normalized != (outputs or {})

    durable_path = get_durable_infra_private_key_path(project_id, module_id)
    legacy_path = _string_value(normalized.get(INFRA_PRIVATE_KEY_PATH_FIELD))
    key_material = _extract_private_key_material(normalized)

    key_available = False

    # Prefer output key material when available.
    if key_material:
        _write_private_key(durable_path, key_material)
        key_available = True
    else:
        # Historical repair path: migrate from existing path if file still exists.
        if legacy_path and os.path.exists(legacy_path):
            copied = _copy_private_key_if_valid(legacy_path, durable_path)
            key_available = copied
        else:
            key_available = os.path.exists(durable_path)

    # Never keep raw private key material in module outputs once captured.
    for private_key_field in INFRA_PRIVATE_KEY_FIELDS:
        if private_key_field in normalized:
            normalized.pop(private_key_field, None)
            changed = True

    if key_available:
        if normalized.get(INFRA_PRIVATE_KEY_PATH_FIELD) != durable_path:
            normalized[INFRA_PRIVATE_KEY_PATH_FIELD] = durable_path
            changed = True

        updated_command, command_changed = _normalize_jumphost_command(
            normalized.get(INFRA_JUMPHOST_COMMAND_FIELD),
            durable_path,
        )
        if command_changed:
            normalized[INFRA_JUMPHOST_COMMAND_FIELD] = updated_command
            changed = True

        if normalized.get(INFRA_PRIVATE_KEY_AVAILABLE_FIELD) is not True:
            normalized[INFRA_PRIVATE_KEY_AVAILABLE_FIELD] = True
            changed = True

        if normalized.get(INFRA_ACCESS_STATUS_FIELD) != INFRA_ACCESS_STATUS_READY:
            normalized[INFRA_ACCESS_STATUS_FIELD] = INFRA_ACCESS_STATUS_READY
            changed = True

        if INFRA_ACCESS_MESSAGE_FIELD in normalized:
            normalized.pop(INFRA_ACCESS_MESSAGE_FIELD, None)
            changed = True
    else:
        # Truthful degradation: avoid surfacing dead key paths/commands.
        if normalized.get(INFRA_PRIVATE_KEY_PATH_FIELD) is not None:
            normalized[INFRA_PRIVATE_KEY_PATH_FIELD] = None
            changed = True

        command_value = _string_value(normalized.get(INFRA_JUMPHOST_COMMAND_FIELD))
        if command_value and "-i" in command_value:
            normalized[INFRA_JUMPHOST_COMMAND_FIELD] = None
            changed = True

        if normalized.get(INFRA_PRIVATE_KEY_AVAILABLE_FIELD) is not False:
            normalized[INFRA_PRIVATE_KEY_AVAILABLE_FIELD] = False
            changed = True

        if normalized.get(INFRA_ACCESS_STATUS_FIELD) != INFRA_ACCESS_STATUS_RECOVERY_REQUIRED:
            normalized[INFRA_ACCESS_STATUS_FIELD] = INFRA_ACCESS_STATUS_RECOVERY_REQUIRED
            changed = True

        recovery_msg = (
            "Infrastructure SSH private key is unavailable. "
            "Run state recovery/repair to rematerialize access metadata."
        )
        if normalized.get(INFRA_ACCESS_MESSAGE_FIELD) != recovery_msg:
            normalized[INFRA_ACCESS_MESSAGE_FIELD] = recovery_msg
            changed = True

    return InfrastructureAccessNormalizationResult(
        outputs=normalized,
        changed=changed,
        key_available=key_available,
        key_path=durable_path,
    )


def normalize_module_outputs_in_place(module) -> InfrastructureAccessNormalizationResult:
    """Normalize module.outputs and persist updated metadata in-memory object."""
    result = normalize_infrastructure_access_outputs(
        module.outputs,
        project_id=module.project_id,
        module_id=module.id,
    )
    if result.changed:
        module.outputs = result.outputs
    return result


def _string_value(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _extract_private_key_material(outputs: dict[str, Any]) -> str | None:
    for field in INFRA_PRIVATE_KEY_FIELDS:
        value = outputs.get(field)
        if isinstance(value, str) and _looks_like_private_key(value):
            return value.strip() + "\n"
    return None


def _looks_like_private_key(value: str) -> bool:
    return bool(_PRIVATE_KEY_HEADER_RE.search(value))


def _write_private_key(path: str, key_material: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(key_material)
    os.chmod(path, 0o600)


def _copy_private_key_if_valid(source_path: str, target_path: str) -> bool:
    try:
        source_text = Path(source_path).read_text()
    except Exception:
        return False

    if not _looks_like_private_key(source_text):
        return False

    _write_private_key(target_path, source_text.strip() + "\n")
    return True


def _normalize_jumphost_command(command: Any, key_path: str) -> tuple[Any, bool]:
    """Rewrite command -i argument to the durable key path."""
    if not isinstance(command, str) or not command.strip():
        return command, False

    if "-i" not in command:
        return command, False

    replacement = f'\\1"{key_path}"'
    updated, count = _SSH_IDENTITY_ARG_RE.subn(replacement, command, count=1)
    if count > 0:
        return updated, updated != command
    return command, False
