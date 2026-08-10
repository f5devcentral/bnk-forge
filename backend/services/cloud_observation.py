"""Passive observation hooks for cloud-credential health.

Phase 2 of the unified connectivity RFC. Cloud APIs don't get their own
breaker (botocore's adaptive retry mode does that). Instead, real call
sites stamp observation columns on ``cloud_credential_templates`` so the
UI can show *"AWS access OK as of 4 min ago"* or *"AWS access failed:
ExpiredToken"* without forcing the user to click a Test button.

Stays best-effort: failures here never propagate (a bad observation
write must not break the actual cloud call).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


def record_aws_observation(
    template_id: int | None,
    *,
    success: bool,
    error: BaseException | None = None,
) -> None:
    """Stamp a single boto3 outcome onto the credential template row.

    Opens its own short-lived DB session. The caller does NOT have to —
    and often shouldn't — share the request DB session, since
    observation must persist even when the surrounding call later fails
    in the request handler.
    """
    if not template_id:
        return
    try:
        from database import SessionLocal
        from models import CloudCredentialTemplate
    except Exception as exc:
        logger.debug("cloud_observation skipped (import error): %s", exc)
        return

    db = SessionLocal()
    try:
        row = (
            db.query(CloudCredentialTemplate)
            .filter(CloudCredentialTemplate.id == template_id)
            .first()
        )
        if row is None:
            return
        now = datetime.now(UTC)
        if success:
            row.last_successful_call_at = now
            # Clear any stale error so the UI isn't lying after recovery.
            row.last_error_at = None
            row.last_error_code = None
            row.last_error_message = None
        else:
            row.last_error_at = now
            row.last_error_code = _extract_error_code(error)
            row.last_error_message = _extract_error_message(error)
        db.commit()
    except Exception as exc:
        logger.debug("cloud_observation write failed: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        try:
            db.close()
        except Exception:
            pass


def _extract_error_code(error: BaseException | None) -> str | None:
    """Pull the AWS error code out of botocore's ClientError shape if present."""
    if error is None:
        return None
    response: Any = getattr(error, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str):
            return code[:64]
    return type(error).__name__[:64]


def _extract_error_message(error: BaseException | None) -> str | None:
    if error is None:
        return None
    response: Any = getattr(error, "response", None)
    if isinstance(response, dict):
        msg = response.get("Error", {}).get("Message")
        if isinstance(msg, str):
            return msg[:1000]
    return str(error)[:1000]
