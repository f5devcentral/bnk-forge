"""Naming helpers for cloud/provider-safe derived identifiers."""

from __future__ import annotations

import re


def slugify_aws_name(value: str, *, max_length: int = 48, fallback: str = "bnk-forge") -> str:
    """Convert a free-form display name into an AWS-safe identifier fragment.

    Keeps only characters allowed across common AWS resource naming surfaces used
    by this project (`[A-Za-z0-9+=,.@_-]`), normalizing whitespace and other
    separators to `-` so human-readable project names can still safely drive
    derived IAM role / key-pair names.
    """
    cleaned = re.sub(r"[^A-Za-z0-9+=,.@_-]+", "-", (value or "").strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-_.")
    if not cleaned:
        cleaned = fallback
    return cleaned[:max_length].rstrip("-_.") or fallback


AWS_NAME_PATTERN = re.compile(r"^[A-Za-z0-9+=,.@_-]+$")


def is_aws_safe_name(value: str) -> bool:
    """Return True when a display name is already AWS-safe as-is."""
    return bool(value) and AWS_NAME_PATTERN.fullmatch(value) is not None


def slugify_s3_name(value: str, *, max_length: int = 40, fallback: str = "bnk-forge") -> str:
    """Convert a free-form name into a lowercase S3 bucket-safe fragment."""
    cleaned = re.sub(r"[^a-z0-9-]+", "-", (value or "").strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = fallback
    cleaned = cleaned[:max_length].strip("-")
    return cleaned or fallback


# IBM Cloud resource names (clusters, COS instances, resource groups) accept
# lowercase letters, digits, and hyphens. Length cap of 35 mirrors the IBM
# Container Service cluster-name limit; tighter than AWS but safe for every
# IBM resource type we drive.
IBM_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,34}$")


def is_ibm_safe_name(value: str) -> bool:
    """Return True when a display name is already IBM-Cloud-safe as-is."""
    return bool(value) and IBM_NAME_PATTERN.fullmatch(value) is not None


def slugify_ibm_name(value: str, *, max_length: int = 35, fallback: str = "bnk-forge") -> str:
    """Convert a free-form display name into an IBM Cloud-safe identifier fragment.

    Lower-cases, replaces disallowed characters with ``-``, collapses runs,
    and ensures the first character is alphanumeric. Output length capped at
    35 to match IBM Container Service cluster naming.
    """
    cleaned = re.sub(r"[^a-z0-9-]+", "-", (value or "").strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    # IBM names must start with an alphanumeric — strip any leading hyphens.
    cleaned = cleaned.lstrip("-")
    if not cleaned:
        cleaned = fallback
    cleaned = cleaned[:max_length].strip("-")
    return cleaned or fallback


# GCP resource names (GKE clusters, node pools, GCS-derived identifiers) must be
# lowercase, contain only letters/digits/hyphens, START WITH A LETTER, and not
# end with a hyphen. Length cap of 40 is comfortably within GKE cluster naming.
GCP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,38}[a-z0-9]$|^[a-z]$")


def is_gcp_safe_name(value: str) -> bool:
    """Return True when a display name is already GCP-safe as-is."""
    return bool(value) and GCP_NAME_PATTERN.fullmatch(value) is not None


def slugify_gcp_name(value: str, *, max_length: int = 40, fallback: str = "bnk-forge") -> str:
    """Convert a free-form display name into a GCP-safe identifier fragment.

    Lower-cases, replaces disallowed characters with ``-``, collapses runs,
    strips leading non-letters (GCP names must START WITH A LETTER), drops any
    trailing hyphen, and caps the length at 40. Falls back to ``fallback`` when
    the result is empty.
    """
    cleaned = re.sub(r"[^a-z0-9-]+", "-", (value or "").strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    # GCP names must start with a letter — strip any leading digits/hyphens.
    cleaned = re.sub(r"^[^a-z]+", "", cleaned)
    if not cleaned:
        cleaned = fallback
    cleaned = cleaned[:max_length].rstrip("-")
    return cleaned or fallback
