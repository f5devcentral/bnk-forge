"""Unit tests for the module fetcher SHA pinning logic."""

from __future__ import annotations

import pytest

from services.module_fetcher import ModuleFetchError, verify_sha256


def test_verify_sha256_matches() -> None:
    data = b"hello world"
    expected = "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"
    verify_sha256(data, expected)  # does not raise


def test_verify_sha256_mismatch_raises() -> None:
    with pytest.raises(ModuleFetchError, match="SHA256 mismatch"):
        verify_sha256(b"some bytes", "a" * 64)
