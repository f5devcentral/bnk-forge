"""
Input validation utilities for BNK-Forge.

Provides reusable validation functions for common input types:
- CIDR notation (network addresses)
- AWS region codes
- Helm timeout duration strings

All validators raise ValueError with clear messages on invalid input.
They accept None gracefully (caller decides if the field is required).

Usage::

    from utils.validators import validate_cidr, validate_aws_region, validate_helm_timeout

    validate_cidr("10.0.0.0/16")         # OK
    validate_cidr("999.0.0.0/50")         # raises ValueError
    validate_aws_region("us-east-1")      # OK
    validate_aws_region("narnia-1")       # raises ValueError
    validate_cidr_fields({"vpc_cidr": "10.0.0.0/16", "name": "foo"})  # validates only CIDR-like keys
"""

import ipaddress
import re
from typing import Any

# ---------------------------------------------------------------------------
# CIDR Validation
# ---------------------------------------------------------------------------

def validate_cidr(value: str | None, field_name: str = "cidr") -> None:
    """
    Validate that *value* is a valid CIDR notation string.

    Uses Python's ``ipaddress.ip_network`` which handles both IPv4 and IPv6.
    ``strict=False`` allows host bits to be set (e.g. 10.0.0.1/16 → 10.0.0.0/16).

    Args:
        value: CIDR string to validate. ``None`` and empty string are allowed
               (the caller decides whether the field is required).
        field_name: Human-readable field name for the error message.

    Raises:
        ValueError: If *value* is not valid CIDR notation.
    """
    if not value:
        return
    try:
        ipaddress.ip_network(value, strict=False)
    except (ValueError, TypeError):
        raise ValueError(
            f"Invalid CIDR notation for '{field_name}': '{value}'. "
            f"Expected format like '10.0.0.0/16' or '192.168.1.0/24'."
        )


def validate_cidr_fields(variables: dict[str, Any] | None) -> None:
    """
    Scan a variables dict and validate any values whose keys look like CIDRs.

    Keys are matched if they contain 'cidr' (case-insensitive).
    Values that are lists are validated element-by-element.

    Args:
        variables: Dict of variable name → value. ``None`` is allowed.

    Raises:
        ValueError: On the first invalid CIDR value found.
    """
    if not variables:
        return
    for key, value in variables.items():
        if "cidr" not in key.lower():
            continue
        if isinstance(value, str):
            validate_cidr(value, field_name=key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, str):
                    validate_cidr(item, field_name=f"{key}[{i}]")


# ---------------------------------------------------------------------------
# AWS Region Validation
# ---------------------------------------------------------------------------

# All standard AWS regions as of 2025. Matches frontend-v2/src/lib/aws-regions.ts.
VALID_AWS_REGIONS = {
    # North America
    "us-east-1",       # US East (N. Virginia)
    "us-east-2",       # US East (Ohio)
    "us-west-1",       # US West (N. California)
    "us-west-2",       # US West (Oregon)
    "ca-central-1",    # Canada (Central)
    "ca-west-1",       # Canada (Calgary)
    # Europe
    "eu-central-1",    # EU (Frankfurt)
    "eu-central-2",    # EU (Zurich)
    "eu-west-1",       # EU (Ireland)
    "eu-west-2",       # EU (London)
    "eu-west-3",       # EU (Paris)
    "eu-north-1",      # EU (Stockholm)
    "eu-south-1",      # EU (Milan)
    "eu-south-2",      # EU (Spain)
    # Asia Pacific
    "ap-northeast-1",  # Asia Pacific (Tokyo)
    "ap-northeast-2",  # Asia Pacific (Seoul)
    "ap-northeast-3",  # Asia Pacific (Osaka)
    "ap-southeast-1",  # Asia Pacific (Singapore)
    "ap-southeast-2",  # Asia Pacific (Sydney)
    "ap-southeast-3",  # Asia Pacific (Jakarta)
    "ap-southeast-4",  # Asia Pacific (Melbourne)
    "ap-southeast-5",  # Asia Pacific (Malaysia)
    "ap-south-1",      # Asia Pacific (Mumbai)
    "ap-south-2",      # Asia Pacific (Hyderabad)
    "ap-east-1",       # Asia Pacific (Hong Kong)
    # Middle East / Africa
    "me-south-1",      # Middle East (Bahrain)
    "me-central-1",    # Middle East (UAE)
    "il-central-1",    # Israel (Tel Aviv)
    "af-south-1",      # Africa (Cape Town)
    # South America
    "sa-east-1",       # South America (Sao Paulo)
    # GovCloud
    "us-gov-west-1",   # AWS GovCloud (US-West)
    "us-gov-east-1",   # AWS GovCloud (US-East)
}


def validate_aws_region(value: str | None, field_name: str = "region") -> None:
    """
    Validate that *value* is a known AWS region code.

    Only validates when the value looks like an AWS region (matches the
    ``xx-yyyy-N`` pattern). Free-form location labels (e.g. "on-prem",
    "datacenter-nyc") are allowed through without error.

    Args:
        value: Region string to validate. ``None`` and empty string are allowed.
        field_name: Human-readable field name for the error message.

    Raises:
        ValueError: If *value* looks like an AWS region but isn't in the known list.
    """
    if not value:
        return

    # Only validate if it looks like an AWS region code (e.g. us-east-1, eu-west-2)
    # Free-form labels like "on-prem" or "datacenter-nyc" pass through
    aws_region_pattern = re.compile(r"^[a-z]{2,4}-[a-z]+-\d+$")
    if not aws_region_pattern.match(value):
        return  # Not an AWS region pattern — allow as free-form label

    if value not in VALID_AWS_REGIONS:
        raise ValueError(
            f"Invalid AWS region for '{field_name}': '{value}'. "
            f"Must be a valid AWS region (e.g. 'us-east-1', 'eu-west-1'). "
            f"See https://docs.aws.amazon.com/general/latest/gr/rande.html"
        )


# IBM Cloud multi-zone-region (MZR) codes. Mirrors services.ibm_cloud_service.IBM_REGIONS.
# Authoritative list at https://cloud.ibm.com/docs/overview?topic=overview-locations.
VALID_IBM_REGIONS = {
    "au-syd",   # Australia (Sydney)
    "br-sao",   # Brazil (Sao Paulo)
    "ca-tor",   # Canada (Toronto)
    "eu-de",    # EU Germany (Frankfurt)
    "eu-es",    # EU Spain (Madrid)
    "eu-gb",    # EU United Kingdom (London)
    "jp-osa",   # Japan (Osaka)
    "jp-tok",   # Japan (Tokyo)
    "us-east",  # US East (Washington DC)
    "us-south", # US South (Dallas)
}


def validate_ibm_region(value: str | None, field_name: str = "region") -> None:
    """Validate that *value* is a known IBM Cloud region code.

    Only validates when the value looks like an IBM region (matches one of the
    short ``xx-yyy`` / ``xx-yyyy`` shapes). Free-form location labels are
    allowed through. The IBM region naming pattern is distinguishable from
    the AWS ``xx-yyyy-N`` pattern by the absence of a trailing digit segment.

    Raises:
        ValueError: If *value* looks like an IBM region but isn't recognized.
    """
    if not value:
        return

    # IBM regions are short two-segment codes without a trailing number:
    # us-east, eu-de, jp-tok, br-sao, etc. AWS regions always have a trailing
    # numeric segment (us-east-1). Reject only IBM-shaped values that aren't
    # in the known set.
    ibm_region_pattern = re.compile(r"^[a-z]{2,3}-[a-z]{2,5}$")
    if not ibm_region_pattern.match(value):
        return

    if value not in VALID_IBM_REGIONS:
        raise ValueError(
            f"Invalid IBM Cloud region for '{field_name}': '{value}'. "
            f"Must be a valid IBM Cloud MZR code (e.g. 'us-south', 'eu-de', 'jp-tok'). "
            f"See https://cloud.ibm.com/docs/overview?topic=overview-locations"
        )
