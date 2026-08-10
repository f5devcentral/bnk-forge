"""Unit tests for the pure functions in api_token_service.

Tests token generation, hashing, prefix derivation. No DB.
"""

from __future__ import annotations

from services.api_token_service import (
    PREFIX_LOOKUP_LEN,
    TOKEN_PREFIX,
    _generate_token_plaintext,
    _hash_token,
    _prefix_of,
    _role_at_most,
)


def test_generated_token_has_correct_prefix_and_length() -> None:
    tok = _generate_token_plaintext()
    assert tok.startswith(TOKEN_PREFIX)
    # bnk_ + 32 base32 chars
    assert len(tok) == len(TOKEN_PREFIX) + 32


def test_generated_tokens_are_unique() -> None:
    seen = {_generate_token_plaintext() for _ in range(100)}
    assert len(seen) == 100


def test_hash_is_stable_and_64_hex() -> None:
    tok = "bnk_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    h1 = _hash_token(tok)
    h2 = _hash_token(tok)
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_hash_changes_on_one_char_difference() -> None:
    a = _hash_token("bnk_abcdef")
    b = _hash_token("bnk_abcdeg")
    assert a != b


def test_prefix_of_is_first_12_chars() -> None:
    tok = "bnk_K3M7QBVZX9A1HPNW2RS4TJEF6L8CGYDU"
    assert _prefix_of(tok) == tok[:PREFIX_LOOKUP_LEN]
    assert _prefix_of(tok).startswith("bnk_")


def test_role_at_most_linear_order() -> None:
    assert _role_at_most("viewer", "operator") is True
    assert _role_at_most("operator", "admin") is True
    assert _role_at_most("admin", "admin") is True
    assert _role_at_most("admin", "operator") is False
    assert _role_at_most("operator", "viewer") is False
