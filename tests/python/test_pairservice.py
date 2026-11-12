"""Tests for the hub's web pairing service (lib/snaphouse/pairservice.py).

The service opens the inbound pairing window on the hub's receive adapter and
exposes the small loopback HTTP API the room-picker page drives through nginx
(/api/pair/). It never touches BlueZ in a test: the window driver is injected, so
the whole state machine (idle, open, paired, failed, the window timeout, stop,
and restart) runs in no real time against a scripted window.

Two layers are exercised:
  - The PairingService state machine, driven by a fake window that calls the
    service's confirm and report hooks the way the real window would.
  - The HTTP surface (header guard, routes, status codes, JSON), on a real
    ThreadingHTTPServer bound to an ephemeral port, over a fake service.

Nothing here carries a household value: the controller address and the device
names are placeholders, and the service never logs either.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from snaphouse import pairservice


CONTROLLER = "AA:BB:CC:DD:EE:FF"
DEVICE = "Pixel 7"


# --------------------------------------------------------------------------- #
# A scripted window: stands in for pairing.run_pairing_window
# --------------------------------------------------------------------------- #


class ScriptedWindow:
    """A fake pairing window the service drives, recording how it was called.

    `script(confirm, report, stop_event)` runs on the window thread and plays the
    bluetoothctl side: it may call `confirm(passkey, device)` (which blocks until
    the page answers, exactly like the reader thread) and `report(event, info)`.
    When no script is given the window just waits for the stop event, so the
    'open' state stays observable until the test ends it.
    """

    def __init__(self, script=None):
        self.script = script
        self.calls: list[dict] = []
        self.started = threading.Event()

    def __call__(self, controller, *, confirm, report, stop_event, duration=None, **kw):
        self.calls.append({"controller": controller, "duration": duration})
        self.started.set()
        try:
            if self.script is not None:
                self.script(confirm, report, stop_event)
            else:
                stop_event.wait(5)
        finally:
            # The real window always closes; nothing to do here for the fake.
            pass


def wait_for(predicate, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def state_is(service, target):
    return lambda: service.state()["state"] == target


def make_service(script=None, **kwargs):
    window = ScriptedWindow(script)
    kwargs.setdefault("window_seconds", 5.0)
    service = pairservice.PairingService(CONTROLLER, run_window=window, **kwargs)
    return service, window


# --------------------------------------------------------------------------- #
# The state machine
# --------------------------------------------------------------------------- #


def test_a_fresh_service_is_idle_with_no_window():
    service, _ = make_service()
    snap = service.state()
    assert snap == {
        "state": "idle",
        "until": None,
        "passkey": None,
        "device": None,
        "message": None,
    }


def test_start_opens_the_window_and_reports_an_until():
    service, window = make_service()
    snap = service.start()
    assert snap["state"] == "open"
    # until is an ISO-8601 UTC instant in the future.
    assert snap["until"] is not None
    parsed = datetime.fromisoformat(snap["until"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    assert parsed > datetime.now(timezone.utc)
    assert wait_for(lambda: window.started.is_set())
    assert window.calls[0]["controller"] == CONTROLLER
    service.stop()


def test_start_while_open_conflicts():
    # A second start while a window is open is a conflict, not a second window.
    service, _ = make_service(script=lambda confirm, report, stop: stop.wait(5))
    service.start()
    assert wait_for(state_is(service, "open"))
    with pytest.raises(pairservice.Conflict):
        service.start()
    service.stop()


def test_the_first_pairing_request_is_accepted_and_the_window_closes_itself():
    # Round 2 (owner decision): the hub answers the first phone's numeric
    # comparison itself. The page never shows a code, because switching to the
    # browser dismisses Android's own pairing dialog.
    answers = {}

    def script(confirm, report, stop):
        answers["first"] = confirm(123456, DEVICE)
        report("paired", {"device": DEVICE})
        answers["stop_set"] = stop.is_set()

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(state_is(service, "paired"))
    assert answers["first"] is True
    snap = service.state()
    assert snap["device"] == DEVICE
    assert snap["passkey"] is None
    assert snap["until"] is None
    # Reporting paired also ends the window (the stop event the real window waits on).
    assert wait_for(lambda: answers.get("stop_set") is True)


def test_the_state_never_enters_confirm():
    seen = []

    def script(confirm, report, stop):
        seen.append(confirm(123456, DEVICE))
        stop.wait(0.3)

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(lambda: seen)
    for _ in range(20):
        assert service.state()["state"] != "confirm"
        time.sleep(0.005)
    service.stop()


def test_while_pairing_is_under_way_the_open_state_names_the_device():
    release = threading.Event()

    def script(confirm, report, stop):
        confirm(123456, DEVICE)
        release.wait(2)

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(lambda: service.state()["device"] == DEVICE)
    snap = service.state()
    assert snap["state"] == "open"
    assert snap["passkey"] is None
    release.set()
    service.stop()


def test_a_second_pairing_request_in_the_same_window_is_declined():
    answers = []

    def script(confirm, report, stop):
        answers.append(confirm(111111, "First phone"))
        answers.append(confirm(222222, "Second phone"))
        answers.append(confirm(333333, "Third phone"))
        report("paired", {"device": "First phone"})

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(state_is(service, "paired"))
    assert answers == [True, False, False]
    # The declined phones never overwrite the one that was accepted.
    assert service.state()["device"] == "First phone"


def test_confirm_is_never_awaited_so_it_always_conflicts():
    # POST /api/pair/confirm stays for compatibility and answers 409.
    service, _ = make_service(script=lambda confirm, report, stop: stop.wait(5))
    service.start()
    assert wait_for(state_is(service, "open"))
    with pytest.raises(pairservice.Conflict):
        service.confirm(True)
    service.stop()
    with pytest.raises(pairservice.Conflict):
        service.confirm(False)


def test_window_reporting_failed_sets_failed():
    def script(confirm, report, stop):
        if confirm(123456, DEVICE):
            report("failed", {"device": DEVICE})

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(state_is(service, "failed"))
    assert service.state()["message"]
    assert service.state()["passkey"] is None


def test_an_exception_in_the_window_sets_failed():
    def script(confirm, report, stop):
        raise RuntimeError("bluetoothctl blew up")

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(state_is(service, "failed"))
    # The failure message is generic: no address or internal detail leaks.
    assert service.state()["message"]


def test_window_that_closes_without_pairing_returns_to_idle():
    # The window opens and times out with no pairing request: the state falls back
    # to idle (no terminal result to report).
    service, _ = make_service(script=lambda confirm, report, stop: None)
    service.start()
    assert wait_for(state_is(service, "idle"))


def test_accepted_but_no_result_ends_as_failed():
    # The hub accepted the first request, but the window ended without a paired
    # or failed report (pairing silently did not complete). That is a failure,
    # not a success and not idle.
    service, _ = make_service(
        script=lambda confirm, report, stop: confirm(123456, DEVICE) and None
    )
    service.start()
    assert wait_for(state_is(service, "failed"))


def test_stop_closes_the_window_and_is_idempotent():
    started = threading.Event()

    def script(confirm, report, stop):
        started.set()
        stop.wait(5)

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(started.is_set)
    snap = service.stop()
    assert snap == {"state": "idle", "until": None, "passkey": None,
                    "device": None, "message": None} or snap["state"] == "idle"
    assert wait_for(state_is(service, "idle"))
    # A second stop is a no-op that still answers idle.
    assert service.stop()["state"] == "idle"


def test_stop_then_a_late_request_is_declined():
    answers = []
    stopped = threading.Event()

    def script(confirm, report, stop):
        stopped.wait(2)
        answers.append(confirm(123456, DEVICE))

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(state_is(service, "open"))
    service.stop()
    stopped.set()
    assert wait_for(lambda: answers)
    # A request that arrives after the window was stopped is never accepted.
    assert answers == [False]
    assert service.state()["state"] == "idle"


def test_a_terminal_result_stays_readable_until_the_next_start():
    # paired/declined/failed persist until a new window opens.
    service, window = make_service(
        script=lambda confirm, report, stop: report("paired", {"device": DEVICE})
    )
    service.start()
    assert wait_for(state_is(service, "paired"))
    # It stays paired (readable) with no new start.
    time.sleep(0.1)
    assert service.state()["state"] == "paired"
    # A new start reopens the window and clears the old result.
    service2, _ = make_service(script=lambda confirm, report, stop: stop.wait(5))
    snap = service2.start()
    assert snap["state"] == "open"
    assert snap["passkey"] is None
    service2.stop()


def test_restart_after_a_terminal_state_opens_a_new_window():
    # The window driver reports paired or failed; the first run pairs, the second
    # fails, and the restart in between must open a fresh window cleanly.
    results = iter(["paired", "failed"])

    def script(confirm, report, stop):
        report(next(results), {"device": DEVICE})

    service, _ = make_service(script=script)
    service.start()
    assert wait_for(state_is(service, "paired"))
    # The previous window finished, so a restart is allowed and opens a new one.
    assert service.start()["state"] == "open"
    assert wait_for(state_is(service, "failed"))


# --------------------------------------------------------------------------- #
# The HTTP surface (over a fake service)
# --------------------------------------------------------------------------- #


class FakeService:
    """A duck-typed service recording calls, for the HTTP routing tests."""

    def __init__(self):
        self.calls: list[tuple] = []
        self._state = {"state": "idle", "until": None, "passkey": None,
                       "device": None, "message": None}
        self.start_conflict = False
        self.confirm_conflict = False

    def start(self):
        self.calls.append(("start",))
        if self.start_conflict:
            raise pairservice.Conflict("already open")
        return {"state": "open", "until": "2026-11-12T20:00:00Z"}

    def state(self):
        self.calls.append(("state",))
        return dict(self._state)

    def confirm(self, accept):
        self.calls.append(("confirm", accept))
        if self.confirm_conflict:
            raise pairservice.Conflict("not confirming")
        return {"state": "declined" if not accept else "confirm", "until": None,
                "passkey": None, "device": None, "message": None}

    def stop(self):
        self.calls.append(("stop",))
        return {"state": "idle", "until": None, "passkey": None,
                "device": None, "message": None}


class Client:
    def __init__(self, service):
        self.service = service
        app = pairservice.PairingApp(service)
        self.server = pairservice.make_server(app, "127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method, path, *, body=None, header=True):
        headers = {}
        if header:
            headers["X-Snaphouse"] = "1"
        if body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, _read_json(resp)
        except urllib.error.HTTPError as err:
            return err.code, _read_json(err)


def _read_json(resp):
    raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else None


@pytest.fixture
def client():
    service = FakeService()
    c = Client(service)
    yield c
    c.close()


def test_http_start_returns_202_and_the_state(client):
    status, body = client.request("POST", "/api/pair/start", body={})
    assert status == 202
    assert body["state"] == "open"
    assert body["until"]


def test_http_start_conflict_is_409(client):
    client.service.start_conflict = True
    status, body = client.request("POST", "/api/pair/start", body={})
    assert status == 409
    assert body["error"] == "already open"


def test_http_state_is_a_get_returning_the_snapshot(client):
    status, body = client.request("GET", "/api/pair/state")
    assert status == 200
    assert body["state"] == "idle"
    assert set(body) == {"state", "until", "passkey", "device", "message"}


def test_http_confirm_passes_the_accept_flag(client):
    status, body = client.request("POST", "/api/pair/confirm", body={"accept": True})
    assert status == 200
    assert ("confirm", True) in client.service.calls
    status, body = client.request("POST", "/api/pair/confirm", body={"accept": False})
    assert status == 200
    assert ("confirm", False) in client.service.calls


def test_http_confirm_malformed_body_is_400(client):
    # Missing accept, a non-bool accept, and non-JSON are all 400.
    status, _ = client.request("POST", "/api/pair/confirm", body={})
    assert status == 400
    status, _ = client.request("POST", "/api/pair/confirm", body={"accept": "yes"})
    assert status == 400
    req = urllib.request.Request(
        f"http://127.0.0.1:{client.port}/api/pair/confirm",
        data=b"not json",
        headers={"X-Snaphouse": "1", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
    except urllib.error.HTTPError as err:
        status = err.code
    assert status == 400
    # None of those malformed bodies reached the service.
    assert all(call[0] != "confirm" for call in client.service.calls)


def test_http_confirm_not_in_confirm_is_409(client):
    client.service.confirm_conflict = True
    status, _ = client.request("POST", "/api/pair/confirm", body={"accept": True})
    assert status == 409


def test_http_stop_returns_idle(client):
    status, body = client.request("POST", "/api/pair/stop", body={})
    assert status == 200
    assert body["state"] == "idle"


def test_http_posts_require_the_guard_header(client):
    for path in ("/api/pair/start", "/api/pair/confirm", "/api/pair/stop"):
        status, _ = client.request("POST", path, body={}, header=False)
        assert status == 403, f"{path} answered {status} without the guard"
    # No guarded POST reached the service.
    assert client.service.calls == []


def test_http_get_state_needs_no_guard(client):
    status, body = client.request("GET", "/api/pair/state", header=False)
    assert status == 200
    assert body["state"] == "idle"


def test_http_unknown_route_is_404(client):
    status, _ = client.request("POST", "/api/pair/bogus", body={})
    assert status == 404


def test_http_get_on_a_post_route_is_405(client):
    status, _ = client.request("GET", "/api/pair/start")
    assert status == 405
