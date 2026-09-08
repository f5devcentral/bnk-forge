"""Tests for mint_builtin_agent_token_step (#148).

The agent-facing endpoints now require an agent-class token by default, so the
built-in forge-agent needs one it can find without operator setup. The backend
mints it at startup into the keys volume; the agent container mounts exactly
that file. These tests pin the properties that make that safe:

  - the token is NARROW: role=agent and no agent_id, so it can register and
    open a claimless WS and nothing more;
  - it is stable across restarts (a valid file is left alone), so a running
    agent is not invalidated every time the backend restarts;
  - it is reissued when it no longer verifies (secret rotation).
"""

from __future__ import annotations

import pytest


@pytest.fixture
def keys_dir(tmp_path, monkeypatch):
    # The token lives in its own volume/dir (AGENT_TOKEN_DIR), deliberately
    # NOT beside jwt_secret.key -- see mint_builtin_agent_token_step.
    monkeypatch.setenv("AGENT_TOKEN_DIR", str(tmp_path))
    return tmp_path


def _run_step():
    from startup_steps import mint_builtin_agent_token_step
    mint_builtin_agent_token_step()


@pytest.mark.unit
class TestMintBuiltinAgentToken:
    def test_writes_a_narrow_agent_token(self, keys_dir):
        from services.auth_service import decode_token

        _run_step()

        path = keys_dir / "builtin_agent.token"
        assert path.exists()
        payload = decode_token(path.read_text().strip())
        assert payload["role"] == "agent"
        assert payload["sub"] == "forge-builtin-agent"
        # The whole point: it must NOT be bound to any agent, or it could
        # impersonate a provisioned one over the WS.
        assert "agent_id" not in payload

    def test_token_is_accepted_by_the_agent_bearer_gate(self, keys_dir):
        """The minted token must actually pass _require_agent_bearer."""
        from unittest.mock import MagicMock, patch

        from routes.benchmarks import _require_agent_bearer

        _run_step()
        token = (keys_dir / "builtin_agent.token").read_text().strip()
        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {token}"}

        with patch("routes.benchmarks.settings") as s:
            s.BENCHMARK_AGENT_AUTH_REQUIRED = True
            payload = _require_agent_bearer(request)
        assert payload["role"] == "agent"

    def test_valid_existing_token_is_left_alone(self, keys_dir):
        """A running agent holds this token; restarting the backend must not rotate it."""
        _run_step()
        first = (keys_dir / "builtin_agent.token").read_text()

        _run_step()
        assert (keys_dir / "builtin_agent.token").read_text() == first

    def test_stale_token_is_reissued(self, keys_dir):
        """A token that no longer verifies (secret rotated) is replaced."""
        from services.auth_service import decode_token

        (keys_dir / "builtin_agent.token").write_text("not.a.valid.jwt")

        _run_step()

        token = (keys_dir / "builtin_agent.token").read_text().strip()
        assert token != "not.a.valid.jwt"
        assert decode_token(token)["role"] == "agent"

    def test_empty_file_is_reissued(self, keys_dir):
        from services.auth_service import decode_token

        (keys_dir / "builtin_agent.token").write_text("")
        _run_step()
        assert decode_token((keys_dir / "builtin_agent.token").read_text().strip())["role"] == "agent"

    def test_nearly_expired_token_is_reissued_early(self, keys_dir):
        """A token that decodes today but expires soon must be renewed now.

        Otherwise it expires under a running agent, every heartbeat 4401s, and
        nothing reissues until the NEXT backend restart -- a silent lockout.
        """
        from datetime import timedelta

        from services.auth_service import create_access_token, decode_token

        soon = create_access_token(
            {"sub": "forge-builtin-agent", "role": "agent"},
            expires_delta=timedelta(days=5),
        )
        (keys_dir / "builtin_agent.token").write_text(soon)

        _run_step()

        token = (keys_dir / "builtin_agent.token").read_text().strip()
        assert token != soon, "near-expiry token was left alone"
        exp = decode_token(token)["exp"]
        import time
        assert exp - time.time() > 300 * 86400, "reissued token is not long-lived"

    def test_reissue_restores_world_read_on_a_locked_down_file(self, keys_dir):
        """chmod must apply on rewrite, not only on create.

        An opener's mode applies only when the file is created; rewriting an
        existing 0600 file keeps it 0600, and the agent (a different uid) could
        not read the reissued token.
        """
        import stat

        p = keys_dir / "builtin_agent.token"
        p.write_text("not.a.valid.jwt")
        p.chmod(0o600)

        _run_step()

        mode = stat.S_IMODE(p.stat().st_mode)
        assert mode & stat.S_IROTH, f"reissued token file is {oct(mode)}, agent cannot read it"

    def test_file_is_world_readable(self, keys_dir):
        """The agent runs as a different uid and reads it via the compose mount."""
        import stat

        _run_step()
        mode = stat.S_IMODE((keys_dir / "builtin_agent.token").stat().st_mode)
        assert mode & stat.S_IROTH, f"mode {oct(mode)} is not world-readable"
