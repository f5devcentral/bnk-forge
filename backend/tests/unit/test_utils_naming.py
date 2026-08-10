"""Unit tests for cloud-provider-safe name slugification helpers.

Focus: the GCP helpers added for WO-2 (#301) plus light sanity coverage of the
existing AWS/S3/IBM helpers they sit alongside.
"""

import pytest

from utils.naming import (
    is_aws_safe_name,
    is_gcp_safe_name,
    is_ibm_safe_name,
    slugify_aws_name,
    slugify_gcp_name,
    slugify_ibm_name,
    slugify_s3_name,
)


class TestSlugifyGcpName:
    def test_spaces_become_hyphens(self):
        assert slugify_gcp_name("My GKE Project") == "my-gke-project"

    def test_uppercase_lowered(self):
        assert slugify_gcp_name("PRODUCTION") == "production"

    def test_collapses_runs_of_separators(self):
        assert slugify_gcp_name("a -- b   c") == "a-b-c"

    def test_leading_digit_stripped_to_start_with_letter(self):
        # GCP names MUST start with a letter — leading digits/hyphens are dropped.
        assert slugify_gcp_name("123abc") == "abc"

    def test_leading_hyphen_stripped(self):
        assert slugify_gcp_name("-cluster") == "cluster"

    def test_trailing_hyphen_stripped(self):
        assert slugify_gcp_name("cluster-") == "cluster"

    def test_empty_falls_back(self):
        assert slugify_gcp_name("") == "bnk-forge"
        assert slugify_gcp_name("   ") == "bnk-forge"

    def test_all_invalid_falls_back(self):
        # Only digits/symbols -> nothing starts with a letter -> fallback.
        assert slugify_gcp_name("123") == "bnk-forge"
        assert slugify_gcp_name("@#$%") == "bnk-forge"

    def test_length_cap_default_40(self):
        result = slugify_gcp_name("a" * 60)
        assert len(result) == 40
        assert result == "a" * 40

    def test_length_cap_strips_trailing_hyphen_after_truncation(self):
        # Truncation lands on a hyphen; it must be stripped.
        name = "ab" + "-c" * 30  # 'ab-c-c-c...' — char 40 will be a hyphen
        result = slugify_gcp_name(name)
        assert not result.endswith("-")
        assert len(result) <= 40

    def test_custom_fallback(self):
        assert slugify_gcp_name("", fallback="default-cluster") == "default-cluster"

    def test_already_safe_is_unchanged(self):
        assert slugify_gcp_name("my-cluster-1") == "my-cluster-1"


class TestIsGcpSafeName:
    @pytest.mark.parametrize(
        "name",
        ["a", "abc", "my-cluster", "my-cluster-1", "ab" + "c" * 38],
    )
    def test_safe_names(self, name):
        assert is_gcp_safe_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "My Cluster",        # space + uppercase
            "MyCluster",         # uppercase
            "1cluster",          # starts with digit
            "-cluster",          # starts with hyphen
            "cluster-",          # ends with hyphen
            "clus_ter",          # underscore not allowed
            "a" * 41,            # too long
        ],
    )
    def test_unsafe_names(self, name):
        assert is_gcp_safe_name(name) is False


class TestExistingHelpersSanity:
    def test_aws_helpers(self):
        assert slugify_aws_name("My Project!") == "My-Project"
        assert is_aws_safe_name("My_Project-1") is True
        assert is_aws_safe_name("My Project") is False

    def test_s3_helper_lowercases(self):
        assert slugify_s3_name("My Bucket") == "my-bucket"

    def test_ibm_helpers(self):
        assert slugify_ibm_name("My Cluster") == "my-cluster"
        assert is_ibm_safe_name("my-cluster") is True
        assert is_ibm_safe_name("My Cluster") is False
