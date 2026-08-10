"""Unit tests for shared SSH/paramiko utilities."""

import paramiko
import pytest

from services.ssh.paramiko_utils import (
    load_private_key_from_content,
    load_private_key_from_file,
)

# Test key content (generated test keys for unit testing)

RSA_KEY_CONTENT = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABFwAAAAdzc2gtcn
NhAAAAAwEAAQAAAQEAtuD6+pjX23xUI2yNf3hbgRkeZ7daRYkmgEP7mjozJnszKaRD9Z+O
io0wqhSShNzCrvyMA6J4vcutzA2ZbqHqvfUWOVaIDPOShCUWvqskwyyYxg/yFU3ocX7ZmE
B2RmmkoqlikCNKy25nAbEvjyeAmMopuK6AhaSJ3LiGRdXus+7+YA2Yt5kFcUA441vPbD2m
3lGzCXcbqVk08pyOBAuvlosDCXTFw3Eg61dkEEEKRLvKhgckUKHt3QPDeGFPZTPusoELkC
BQ2gF9E6emha+CvsLHpkszNdcE1nFcdgSPSiO1spAt9hHzuNdaylywHmkJT9qgGyWe/ffD
bEYFbmy0qQAAA8imgmKfpoJinwAAAAdzc2gtcnNhAAABAQC24Pr6mNfbfFQjbI1/eFuBGR
5nt1pFiSaAQ/uaOjMmezMppEP1n46KjTCqFJKE3MKu/IwDoni9y63MDZluoeq99RY5VogM
85KEJRa+qyTDLJjGD/IVTehxftmYQHZGaaSiqWKQI0rLbmcBsS+PJ4CYyim4roCFpIncuI
ZF1e6z7v5gDZi3mQVxQDjjW89sPabeUbMJdxupWTTynI4EC6+WiwMJdMXDcSDrV2QQQQpE
u8qGByRQoe3dA8N4YU9lM+6ygQuQIFDaAX0Tp6aFr4K+wsemSzM11wTWcVx2BI9KI7WykC
32EfO411rKXLAeaQlP2qAbJZ7998NsRgVubLSpAAAAAwEAAQAAAQAGhiwt+Hnq6JqP0PWT
UJXjGyRMiuv7gxMOjF5TeDQO8WI34BZUkuag5ryPtMAtYTrIx1WvY4JvMu72Up3gpoIbWL
z8OqwL2jyl3jTbhHuBQvwIRNVcETVzpxTYK6SMioRHEUfk4H1wmHWwR8PslXou+TdX0VHg
cqhAaYzQvPsRDCwvFXR/aNjH3MPSv83XqqaaKDIjumtmrdNfapuzsPg+d/7OaSZnzMcEpD
3Is5gbJi5yg8l38N/xIcidbRvhPSPvTVP03hGd7l/kcW+EfyoNu9sDzQVptUJhoFNrpDXI
KC4HI5T7VSWbOrcXTIBZnqPO04vaF0YziCS6SxXTflyJAAAAgGJUpKjrSdrMvkOkCoohBD
Z8vIjkcinOxYgQZOH1fSSLqoQ5aGcAG6AjVgSgdmWTgPUMpxzzcB3Tk5FzHH6FXXD6R8kn
RRtrZ5bebAXTk3rBfunseqUxAPXRF/naIixjRkOmnREMHQ4giy3mjF9tLD8CB+U6PxvWcp
7kDppYzG0bAAAAgQDh+l966ShQUGqMSPs6YpD3rxZR3FMkD7x4dciVS+1qzUKe2tDG1UMn
ywOyGwz8p3rNT9sSCLTJ09rUsr+bUENVOiKTh0P0CHaFzBqCrwm2nkJ1zLraq7Xg3bl327
wl2j8FNOitOEo4RYMssWC7YBSH/N12dXSrVtCaW8+SrDRbqwAAAIEAzyzION3XZK5aMq2w
cu7nWCgjCgbBhEpsHV1Low+kFDtZxV6atXbrT3jhVeDnwYsL0Y5mUyHIzRiE77wGu+1o6n
LBiKgQbeZZDi8ZFEwrwmSeBT2je++q4DTdAbtkEVzYZ9xNC+tQ77+AmZ7VA58gApZKLFCZ
fN8tQ4OsFoAQfPsAAAAQdGVzdEBleGFtcGxlLmNvbQECAw==
-----END OPENSSH PRIVATE KEY-----"""

ED25519_KEY_CONTENT = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACBSZdBYIX8+cgVY1h6sXKgaU9bzhXE1hOSw/lUuV+60GQAAAJis11MWrNdT
FgAAAAtzc2gtZWQyNTUxOQAAACBSZdBYIX8+cgVY1h6sXKgaU9bzhXE1hOSw/lUuV+60GQ
AAAEAuVMYcMiC6bGD6qVmUZCgwBHW5n+u/tRL/xQ05lY8iz1Jl0Fghfz5yBVjWHqxcqBpT
1vOFcTWE5LD+VS5X7rQZAAAAEHRlc3RAZXhhbXBsZS5jb20BAgMEBQ==
-----END OPENSSH PRIVATE KEY-----"""


class TestLoadPrivateKeyFromContent:
    """Tests for load_private_key_from_content()."""

    def test_loads_rsa_key(self):
        """Should load RSA private key from content."""
        key = load_private_key_from_content(RSA_KEY_CONTENT)
        assert isinstance(key, paramiko.RSAKey)

    def test_loads_ed25519_key(self):
        """Should load Ed25519 private key from content."""
        key = load_private_key_from_content(ED25519_KEY_CONTENT)
        assert isinstance(key, paramiko.Ed25519Key)

    def test_raises_for_invalid_key(self):
        """Should raise SSHException for invalid key content."""
        with pytest.raises(paramiko.SSHException, match="Unable to load private key"):
            load_private_key_from_content("not a valid key")

    def test_raises_for_empty_content(self):
        """Should raise SSHException for empty content."""
        with pytest.raises(paramiko.SSHException, match="Unable to load private key"):
            load_private_key_from_content("")


class TestLoadPrivateKeyFromFile:
    """Tests for load_private_key_from_file()."""

    def test_raises_for_nonexistent_file(self, tmp_path):
        """Should raise FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError, match="not found"):
            load_private_key_from_file(tmp_path / "nonexistent.key")

    def test_loads_rsa_key_from_file(self, tmp_path):
        """Should load RSA private key from file."""
        key_path = tmp_path / "test_rsa.key"
        key_path.write_text(RSA_KEY_CONTENT)
        key = load_private_key_from_file(key_path)
        assert isinstance(key, paramiko.RSAKey)

    def test_loads_ed25519_key_from_file(self, tmp_path):
        """Should load Ed25519 private key from file."""
        key_path = tmp_path / "test_ed25519.key"
        key_path.write_text(ED25519_KEY_CONTENT)
        key = load_private_key_from_file(key_path)
        assert isinstance(key, paramiko.Ed25519Key)
