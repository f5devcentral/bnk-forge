"""
BU-007: Unit tests for utils.validators module.

Tests CIDR validation, AWS region validation, and the bulk
validate_cidr_fields helper.
"""

import pytest

from utils.validators import (
    VALID_AWS_REGIONS,
    validate_aws_region,
    validate_cidr,
    validate_cidr_fields,
)

# ── CIDR Validation ───────────────────────────────────────────────────


class TestValidateCidr:
    """Tests for validate_cidr()."""

    def test_valid_ipv4_cidr(self):
        validate_cidr("10.0.0.0/16")  # should not raise

    def test_valid_ipv4_cidr_24(self):
        validate_cidr("192.168.1.0/24")

    def test_valid_ipv4_cidr_32(self):
        validate_cidr("10.0.0.1/32")

    def test_valid_ipv6_cidr(self):
        validate_cidr("fd00::/64")

    def test_host_bits_set_ok_with_strict_false(self):
        """strict=False means 10.0.0.1/16 is accepted (normalized to 10.0.0.0/16)."""
        validate_cidr("10.0.0.1/16")

    def test_none_allowed(self):
        validate_cidr(None)  # should not raise

    def test_empty_string_allowed(self):
        validate_cidr("")  # should not raise

    def test_invalid_cidr_raises(self):
        with pytest.raises(ValueError, match="Invalid CIDR"):
            validate_cidr("999.0.0.0/50")

    def test_plain_ip_no_prefix_raises(self):
        with pytest.raises(ValueError, match="Invalid CIDR"):
            validate_cidr("not-a-cidr")

    def test_custom_field_name_in_error(self):
        with pytest.raises(ValueError, match="vpc_cidr"):
            validate_cidr("garbage", field_name="vpc_cidr")


# ── CIDR Fields Bulk Validation ───────────────────────────────────────


class TestValidateCidrFields:
    """Tests for validate_cidr_fields()."""

    def test_validates_cidr_keys(self):
        validate_cidr_fields({"vpc_cidr": "10.0.0.0/16", "name": "foo"})

    def test_ignores_non_cidr_keys(self):
        validate_cidr_fields({"name": "anything", "description": "stuff"})

    def test_validates_list_of_cidrs(self):
        validate_cidr_fields({"subnet_cidrs": ["10.0.1.0/24", "10.0.2.0/24"]})

    def test_invalid_cidr_in_list_raises(self):
        with pytest.raises(ValueError, match="subnet_cidrs"):
            validate_cidr_fields({"subnet_cidrs": ["10.0.1.0/24", "bad"]})

    def test_none_variables_ok(self):
        validate_cidr_fields(None)

    def test_empty_dict_ok(self):
        validate_cidr_fields({})

    def test_case_insensitive_key_matching(self):
        """Keys with CIDR in any case should be validated."""
        with pytest.raises(ValueError):
            validate_cidr_fields({"VPC_CIDR": "invalid"})


# ── AWS Region Validation ─────────────────────────────────────────────


class TestValidateAwsRegion:
    """Tests for validate_aws_region()."""

    def test_valid_region(self):
        validate_aws_region("us-east-1")

    def test_all_standard_regions_accepted(self):
        """Spot-check several regions from the set."""
        for region in ["eu-west-1", "ap-southeast-2", "sa-east-1", "ca-central-1"]:
            validate_aws_region(region)

    def test_none_allowed(self):
        validate_aws_region(None)

    def test_empty_string_allowed(self):
        validate_aws_region("")

    def test_invalid_aws_region_raises(self):
        with pytest.raises(ValueError, match="Invalid AWS region"):
            validate_aws_region("us-narnia-1")

    def test_freeform_label_passes_through(self):
        """Non-AWS-pattern strings (e.g., 'on-prem') should not raise."""
        validate_aws_region("on-prem")
        validate_aws_region("datacenter-nyc")

    def test_custom_field_name_in_error(self):
        with pytest.raises(ValueError, match="cloud_region"):
            validate_aws_region("xx-fake-1", field_name="cloud_region")

    def test_valid_regions_set_is_not_empty(self):
        """Sanity check that the region set is populated."""
        assert len(VALID_AWS_REGIONS) > 25

    def test_govcloud_regions_included(self):
        assert "us-gov-west-1" in VALID_AWS_REGIONS
        assert "us-gov-east-1" in VALID_AWS_REGIONS
