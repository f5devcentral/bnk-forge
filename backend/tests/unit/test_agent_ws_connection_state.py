"""Unit tests for agent WebSocket connection-state helpers.

These encode two invariants discovered when the managed agent stayed wedged at
status="disconnected" while its heartbeats kept advancing last_heartbeat:

1. A heartbeat is proof of life — it must reset status to "connected" (or
   "running"), so a stale "disconnected" self-heals instead of failing dispatch
   with AGENT_NOT_CONNECTED forever.
2. On an agent pod restart the new connection overwrites the registry before the
   old handler's teardown runs; the old handler must not clobber the new ws.
"""

import routes.benchmarks as bm
from routes.benchmarks import _owns_agent_connection, _status_for_heartbeat


class TestStatusForHeartbeat:
    def test_plain_heartbeat_maps_to_connected(self):
        # No status field → the old code left status untouched (the bug). It must
        # now resolve to "connected".
        assert _status_for_heartbeat(None) == "connected"

    def test_explicit_connected_stays_connected(self):
        assert _status_for_heartbeat("connected") == "connected"

    def test_running_is_preserved(self):
        assert _status_for_heartbeat("running") == "running"

    def test_unknown_status_collapses_to_connected(self):
        # Anything that isn't "running" is still a live agent.
        assert _status_for_heartbeat("idle") == "connected"


class TestOwnsAgentConnection:
    def test_true_when_registered_ws_is_this_ws(self):
        ws = object()
        bm._agent_ws_connections[999] = ws
        try:
            assert _owns_agent_connection(999, ws) is True
        finally:
            bm._agent_ws_connections.pop(999, None)

    def test_false_when_a_newer_ws_replaced_it(self):
        old_ws, new_ws = object(), object()
        bm._agent_ws_connections[999] = new_ws  # restart already re-registered
        try:
            # The old handler's teardown must see it no longer owns the slot.
            assert _owns_agent_connection(999, old_ws) is False
        finally:
            bm._agent_ws_connections.pop(999, None)

    def test_false_when_no_connection_registered(self):
        bm._agent_ws_connections.pop(999, None)
        assert _owns_agent_connection(999, object()) is False
