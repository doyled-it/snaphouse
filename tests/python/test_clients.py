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

    def __init__(
        self,
        clients_state: dict[str, dict],
        *,
        groups: list[dict] | None = None,
        stream: str = "House",
    ):
        # clients_state maps client id -> {"name": str, "latency": int}.
        self.clients_state = clients_state
        # groups, when given, is a list of {"id", "stream_id", "clients": [ids]}.
        # The default is one group holding every client, on `stream`, which keeps
        # the name/latency tests unconcerned with groups (they are all on House,
        # so no Group.SetStream fires).
        if groups is None:
            groups = [
                {
                    "id": "group-all",
                    "stream_id": stream,
                    "clients": list(clients_state),
                }
            ]
        self.groups = groups
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
        if method == "Group.SetStream":
            for group in self.groups:
                if group["id"] == params["id"]:
                    group["stream_id"] = params["stream_id"]
            return {"id": params["id"], "stream_id": params["stream_id"]}
        raise AssertionError(f"unexpected method {method}")

    def _client_obj(self, client_id: str) -> dict:
        state = self.clients_state[client_id]
        return {
            "id": client_id,
            "connected": True,
            "config": {"name": state["name"], "latency": state["latency"]},
        }

    def _status(self) -> dict:
        return {
            "server": {
                "groups": [
                    {
                        "id": group["id"],
                        "stream_id": group["stream_id"],
                        "clients": [self._client_obj(c) for c in group["clients"]],
                    }
                    for group in self.groups
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


# --------------------------------------------------------------------------- #
# Group stream assignment: move every room onto the House meta stream
# --------------------------------------------------------------------------- #


def test_moves_each_rooms_group_onto_house_when_on_another_stream():
    # Names and latencies already match, so only the stream moves are sent. living
    # and office sit on the bare AirPlay input; bedroom is already on House.
    server = FakeSnapserver(
        {
            "living": {"name": "Living room", "latency": 0},
            "office": {"name": "Office", "latency": 0},
            "bedroom": {"name": "Bedroom", "latency": 0},
        },
        groups=[
            {"id": "g-living", "stream_id": "AirPlay", "clients": ["living"]},
            {"id": "g-office", "stream_id": "AirPlay", "clients": ["office"]},
            {"id": "g-bedroom", "stream_id": "House", "clients": ["bedroom"]},
        ],
    )
    desired = {
        "living": {"label": "Living room", "latency_ms": 0},
        "office": {"label": "Office", "latency_ms": 0},
        "bedroom": {"label": "Bedroom", "latency_ms": 0},
    }

    changed = clients.apply_client_settings(server, desired)

    assert server.set_calls() == [
        ("Group.SetStream", {"id": "g-living", "stream_id": "House"}),
        ("Group.SetStream", {"id": "g-office", "stream_id": "House"}),
    ]
    assert set(changed) == {"living", "office"}


def test_leaves_a_group_already_on_house_alone():
    server = FakeSnapserver(
        {"living": {"name": "Living room", "latency": 0}},
        groups=[{"id": "g-living", "stream_id": "House", "clients": ["living"]}],
    )

    changed = clients.apply_client_settings(
        server, {"living": {"label": "Living room", "latency_ms": 0}}
    )

    assert server.set_calls() == []
    assert changed == []


def test_group_reassignment_is_idempotent_on_a_second_run():
    server = FakeSnapserver(
        {"living": {"name": "Living room", "latency": 0}},
        groups=[{"id": "g-living", "stream_id": "AirPlay", "clients": ["living"]}],
    )
    desired = {"living": {"label": "Living room", "latency_ms": 0}}

    first = clients.apply_client_settings(server, desired)
    assert first == ["living"]
    assert server.set_calls() == [
        ("Group.SetStream", {"id": "g-living", "stream_id": "House"})
    ]

    # The server now holds the group on House. A second run sends nothing.
    server.calls.clear()
    second = clients.apply_client_settings(server, desired)
    assert second == []
    assert server.set_calls() == []


def test_moves_a_shared_group_once():
    # Two rooms in one group on AirPlay: one Group.SetStream, both reported moved.
    server = FakeSnapserver(
        {
            "living": {"name": "Living room", "latency": 0},
            "office": {"name": "Office", "latency": 0},
        },
        groups=[{"id": "g-both", "stream_id": "AirPlay", "clients": ["living", "office"]}],
    )
    desired = {
        "living": {"label": "Living room", "latency_ms": 0},
        "office": {"label": "Office", "latency_ms": 0},
    }

    changed = clients.apply_client_settings(server, desired)

    assert server.set_calls() == [
        ("Group.SetStream", {"id": "g-both", "stream_id": "House"})
    ]
    assert set(changed) == {"living", "office"}


def test_moves_a_group_on_the_tonetest_sandbox_stream_too():
    # The reassignment has no stream special-cases: a group on the ToneTest
    # diagnostic stream is moved to House like any other non-House stream. The
    # live ToneTest experiment is cleaned up before this ever runs on the hub.
    server = FakeSnapserver(
        {"office": {"name": "Office", "latency": 0}},
        groups=[{"id": "g-office", "stream_id": "ToneTest", "clients": ["office"]}],
    )

    changed = clients.apply_client_settings(
        server, {"office": {"label": "Office", "latency_ms": 0}}
    )

    assert server.set_calls() == [
        ("Group.SetStream", {"id": "g-office", "stream_id": "House"})
    ]
    assert changed == ["office"]


def test_does_not_touch_the_group_of_a_room_snapserver_has_never_seen():
    # bedroom is in the repo but absent from the server. Its group is unknown, so
    # no Group.SetStream is sent for it.
    server = FakeSnapserver(
        {"office": {"name": "Office", "latency": 0}},
        groups=[{"id": "g-office", "stream_id": "AirPlay", "clients": ["office"]}],
    )
    desired = {
        "office": {"label": "Office", "latency_ms": 0},
        "bedroom": {"label": "Bedroom", "latency_ms": 0},
    }

    changed = clients.apply_client_settings(server, desired)

    assert server.set_calls() == [
        ("Group.SetStream", {"id": "g-office", "stream_id": "House"})
    ]
    assert changed == ["office"]


# --------------------------------------------------------------------------- #
# Tolerant reading of a status bloated with inline cover art
# --------------------------------------------------------------------------- #


def test_rpc_result_tolerates_control_characters_in_art_data():
    # snapserver embeds cover art and track metadata in Server.GetStatus, and the
    # artData can carry unescaped control characters that strict JSON (and jq)
    # reject. The hub-side reader must still parse it, or the apply and verify
    # fail and the hub rolls back to its last-good commit.
    art = "iVBORw0KGgo" + "".join(chr(c) for c in range(0, 32)) + "A" * 2048
    body = (
        '{"id":1,"jsonrpc":"2.0","result":{"server":{'
        '"streams":[{"id":"House"}],'
        '"groups":[{"id":"g","stream_id":"House","clients":[]}]},'
        '"properties":{"metadata":{"artData":{"data":"' + art + '"}}}}}'
    )

    result = clients.rpc_result(body, "Server.GetStatus")

    assert result["server"]["streams"][0]["id"] == "House"
    assert result["server"]["groups"][0]["stream_id"] == "House"


def test_rpc_result_raises_on_a_jsonrpc_error():
    body = '{"id":1,"jsonrpc":"2.0","error":{"code":-32601,"message":"nope"}}'
    try:
        clients.rpc_result(body, "Server.GetStatus")
    except RuntimeError as exc:
        assert "nope" in str(exc)
    else:
        raise AssertionError("expected a RuntimeError on a JSON-RPC error")


# --------------------------------------------------------------------------- #
# Stream presence / format check used by the hub's verify scripts
# --------------------------------------------------------------------------- #


def _streams_rpc(streams: list[dict]) -> clients.Rpc:
    def rpc(method: str, params: dict) -> dict:
        assert method == "Server.GetStatus"
        return {"server": {"streams": streams}}

    return rpc


def test_check_streams_passes_when_all_present_and_bt_format_right():
    rpc = _streams_rpc(
        [
            {"id": "AirPlay"},
            {"id": "Bluetooth", "uri": {"query": {"sampleformat": "44100:16:2"}}},
            {"id": "House"},
        ]
    )
    assert clients.check_streams(rpc) == []


def test_check_streams_reports_a_missing_stream():
    rpc = _streams_rpc(
        [
            {"id": "AirPlay"},
            {"id": "Bluetooth", "uri": {"query": {"sampleformat": "44100:16:2"}}},
        ]
    )
    assert clients.check_streams(rpc) == ["snapserver is missing streams: House"]


def test_check_streams_reports_a_wrong_bluetooth_sampleformat():
    rpc = _streams_rpc(
        [
            {"id": "AirPlay"},
            {"id": "Bluetooth", "uri": {"query": {"sampleformat": "48000:16:2"}}},
            {"id": "House"},
        ]
    )
    assert clients.check_streams(rpc) == [
        "Bluetooth stream sample format is '48000:16:2', expected 44100:16:2"
    ]


# --------------------------------------------------------------------------- #
# snapclient verify logic on a Bluetooth-output host
# --------------------------------------------------------------------------- #


def test_snapclient_verify_ok_covers_all_four_combinations():
    # On a Bluetooth host snapclient's lifecycle follows the speaker, so verify
    # passes only when the two agree: speaker connected with snapclient active, or
    # speaker absent with snapclient inactive. The speaker simply being off (the
    # JBL's normal state) must never fail verify, or self-update would roll the
    # machine back whenever the speaker is off.
    assert clients.snapclient_verify_ok(speaker_connected=True, client_active=True) is True
    assert clients.snapclient_verify_ok(speaker_connected=False, client_active=False) is True
    # Disagreement is a real fault: the link is up but the player is down, or the
    # player is up while the speaker is gone.
    assert clients.snapclient_verify_ok(speaker_connected=True, client_active=False) is False
    assert clients.snapclient_verify_ok(speaker_connected=False, client_active=True) is False


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_urlopen(monkeypatch, body: bytes):
    seen = {}

    def fake(request, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse(body)

    monkeypatch.setattr(clients.urllib.request, "urlopen", fake)
    return seen


def test_http_rpc_rejects_an_empty_reply_by_default(monkeypatch):
    _patch_urlopen(monkeypatch, b"")
    import pytest

    with pytest.raises(ValueError):
        clients.HttpRpc("http://x/jsonrpc")("Stream.Control", {})


def test_http_rpc_empty_ok_returns_empty_dict_for_blank_bodies(monkeypatch):
    for body in (b"", b"  \n"):
        _patch_urlopen(monkeypatch, body)
        assert clients.HttpRpc("http://x/jsonrpc", empty_ok=True)("Stream.Control", {}) == {}


def test_http_rpc_empty_ok_still_parses_and_raises_on_a_real_body(monkeypatch):
    import pytest

    _patch_urlopen(monkeypatch, b'{"result": {"a": 1}}')
    assert clients.HttpRpc("http://x/jsonrpc", empty_ok=True)("m", {}) == {"a": 1}
    _patch_urlopen(monkeypatch, b'{"error": {"message": "bad"}}')
    with pytest.raises(RuntimeError):
        clients.HttpRpc("http://x/jsonrpc", empty_ok=True)("m", {})


def test_http_rpc_passes_its_timeout_to_urlopen(monkeypatch):
    seen = _patch_urlopen(monkeypatch, b"{}")
    clients.HttpRpc("http://x/jsonrpc", timeout=2.0)("m", {})
    assert seen["timeout"] == 2.0
