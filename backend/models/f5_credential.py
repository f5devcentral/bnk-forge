"""
F5 Credential model — encrypted-at-rest BIG-IP management credentials.

Supports two auth flows:
  * token  — bearer token, stored encrypted; used directly in X-F5-Auth-Token header.
  * password — username + password; traded for a session token via iControl REST login.

Secret fields (password_encrypted, token_encrypted) are NEVER exposed in serialize().
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from database import Base


class F5Credential(Base):
    """Reusable BIG-IP management credential (token or username/password)."""

    __tablename__ = "f5_credentials"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)

    # Auth type — drives which encrypted field is populated
    auth_type = Column(String(20), nullable=False, default="password")  # 'token' | 'password'

    # Credentials — one or both may be set depending on auth_type
    username = Column(String(255), nullable=True)
    password_encrypted = Column(Text, nullable=True)
    token_encrypted = Column(Text, nullable=True)

    # Metadata
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255), nullable=True)

    # Last connection-test outcome
    # Values: "ok" | "failed" | "running" | None (never tested)
    last_test_status = Column(String(16), nullable=True)
    last_test_at = Column(DateTime(timezone=True), nullable=True)
    last_test_message = Column(Text, nullable=True)
