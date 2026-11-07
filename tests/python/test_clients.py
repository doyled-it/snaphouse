"""Tests for apply_client_settings, the hub's push of room names and latencies.

The hub runs this once per apply, after snapserver is up. snapserver is the one
place that knows every room's name and latency offset, so the repo's room labels
(vars/hosts.yml room_label) and tuned offsets (latency_ms) are pushed into it
through the JSON-RPC control API. Two things are pinned here:

1. apply_client_settings sends only the requests it needs: a Client.SetName or
   Client.SetLatency only where the server's current value differs from the
   repo's, and nothing at all for a room snapserver has never seen.
2. It is idempotent. A second run against a server that already carries the
   applied values sends no Set requests and reports nothing changed.

The RPC transport is injected as a plain callable rpc(method, params) -> result,
so these tests drive a fake server with no network.
"""

from __future__ import annotations

from snaphouse import clients


class FakeSnapserver:
    """A minimal stand-in for snapserver's JSON-RPC control API.

    It holds a set of known clients, each with a name and latency, and answers
    Server.GetStatus / Client.SetName / Client.SetLatency the way snapserver
    does. Every call is recorded in `calls` so a test can assert exactly which
    requests were sent.
    """

    def __init__(self, clients_state: dict[str, dict]):
        # clients_state maps client id -> {"name": str, "latency": int}.
        self.clients_state = clients_state
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if method == "Server.GetStatus":
            return self._status()
        if method == "Client.SetName":
            self.clients_state[params["id"]]["name"] = params["name"]
            return {"id": params["id"], "name": params["name"]}
        if method == "Client.SetLatency":
            self.clients_state[params["id"]]["latency"] = params["latency"]
            return {"id": params["id"], "latency": params["latency"]}
        raise AssertionError(f"unexpected method {method}")

    def _status(self) -> dict:
        return {
            "server": {
                "groups": [
                    {
                        "clients": [
                            {
                                "id": client_id,
                                "connected": True,
                                "config": {
                                    "name": state["name"],
                                    "latency": state["latency"],
                                },
                            }
                            for client_id, state in self.clients_state.items()
                        ]
                    }
                ]
            }
        }

    def set_calls(self) -> list[tuple[str, dict]]:
        """Only the mutating calls (everything except the status read)."""
        return [call for call in self.calls if call[0] != "Server.GetStatus"]


def test_sends_only_the_requests_that_change_something():
    # The server knows all three rooms. living already carries its label but the
    # wrong latency; office carries the wrong label but the right latency;
    # bedroom already matches on both.
    server = FakeSnapserver(
        {
            "living": {"name": "Living room", "latency": 10},
            "office": {"name": "office", "latency": 0},
            "bedroom": {"name": "Bedroom", "latency": 0},
        }
    )
    desired = {
        "living": {"label": "Living room", "latency_ms": 0},
        "office": {"label": "Office", "latency_ms": 0},
        "bedroom": {"label": "Bedroom", "latency_ms": 0},
    }

    changed = clients.apply_client_settings(server, desired)

    # living needed a latency fix only; office needed a name fix only; bedroom
    # needed nothing. No SetName on living, no SetLatency on office.
    assert server.set_calls() == [
        ("Client.SetLatency", {"id": "living", "latency": 0}),
        ("Client.SetName", {"id": "office", "name": "Office"}),
    ]
    assert set(changed) == {"living", "office"}


def test_skips_a_room_snapserver_has_never_seen():
    # Only office has ever connected. living and bedroom are in the repo but
    # absent from the server, so they must not be touched.
    server = FakeSnapserver({"office": {"name": "office", "latency": 0}})
    desired = {
        "living": {"label": "Living room", "latency_ms": 0},
        "office": {"label": "Office", "latency_ms": 5},
        "bedroom": {"label": "Bedroom", "latency_ms": 0},
    }

    changed = clients.apply_client_settings(server, desired)

    assert server.set_calls() == [
        ("Client.SetName", {"id": "office", "name": "Office"}),
        ("Client.SetLatency", {"id": "office", "latency": 5}),
    ]
    assert changed == ["office"]


def test_is_idempotent_on_a_second_run():
    server = FakeSnapserver(
        {
            "living": {"name": "the hub", "latency": 20},
            "office": {"name": "pi", "latency": 7},
        }
    )
    desired = {
        "living": {"label": "Living room", "latency_ms": 0},
        "office": {"label": "Office", "latency_ms": 5},
    }

    first = clients.apply_client_settings(server, desired)
    assert set(first) == {"living", "office"}

    # The server now carries the applied values. A second run reads the status
    # and sends nothing.
    server.calls.clear()
    second = clients.apply_client_settings(server, desired)

    assert second == []
    assert server.set_calls() == []
