"""The hub's web pairing service: a loopback HTTP API over the pairing window.

The hub receives audio from a phone over Bluetooth, and a phone must pair once
before it can stream. This service opens the inbound pairing window on the hub's
receive adapter and exposes the small POST/GET API the room-picker page drives
through nginx (``/api/pair/``), so the window can be opened from the web app
instead of a terminal.

The person opening a window is the person holding the phone, and Android drops
its own pairing dialog the moment they switch to the browser to read a code. So
the web flow does not ask for a confirmation: a window opened here accepts the
FIRST pairing request (the numeric comparison is answered yes by the hub), declines
any further request in the same window, trusts the new device, and closes itself.
The CLI (``snaphouse pair phone``) shares the same window driver
(``snaphouse.pairing``) but keeps its interactive confirm.

It is a separate service from the art admin one on purpose: pairing needs BlueZ
rights (register a pairing agent, set adapter properties) that the art service,
which runs as the unprivileged ``art`` user, must not have. This service runs as
root, because the stock Debian BlueZ D-Bus policy grants agent registration and
adapter property writes to root only (the default policy denies non-root clients
``send_destination=org.bluez`` and there is no ``bluetooth`` group allow for it),
and widening that policy would add attack surface. It is LAN-only, bound to
127.0.0.1, reachable only through nginx; there is no inbound access from the
internet (AGENTS.md).

The state machine (idle, open, paired, failed, with the window timeout, a stop, and
a restart) is driven entirely by the injected window driver and clocks, so it is
fully tested without BlueZ. The phone's and the
adapter's addresses are never logged or returned; the phone's advertised name is
returned only on the LAN page and never logged.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from snaphouse import pairing


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Default loopback port; nginx proxies /api/pair/ here. Never bound off
#: 127.0.0.1. 8781 is the art admin service; pairing gets the next port.
DEFAULT_PORT = 8782

#: How long the pairing window stays open. It lives in snaphouse.pairing so the
#: CLI and the web service share one source of truth.
WINDOW_SECONDS = pairing.PAIRING_WINDOW_SECONDS

#: The custom header every write must carry. A cross-site form cannot set it
#: without a CORS preflight the server never answers, so it blocks CSRF.
GUARD_HEADER = "X-Snaphouse"
GUARD_VALUE = "1"

_API_PREFIX = "/api/pair/"

#: The terminal states: once set they stay readable until the next start.
_TERMINAL = ("paired", "failed")

#: The states in which a window is still live, so a second start is a conflict.
_LIVE = ("open",)


class Conflict(Exception):
    """A request that does not fit the current state (maps to HTTP 409)."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(instant: datetime) -> str:
    """ISO-8601 UTC to the second, with a trailing Z (never a household value)."""
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# The state machine
# --------------------------------------------------------------------------- #


class PairingService:
    """The pairing window's state, driven by one background window at a time.

    ``start`` opens a window (a background thread running the injected window
    driver). The driver calls back into this service when a phone asks to pair
    (``_request_cb``: the first request is accepted, later ones declined) and to
    report the terminal result (``_report_cb``). ``state`` is a snapshot the page
    polls; ``stop`` closes the window now. One condition guards every field, so
    the threaded HTTP server and the window thread never race on the state.
    """

    def __init__(
        self,
        controller: str,
        *,
        run_window: Callable[..., None] = pairing.run_pairing_window,
        window_seconds: float = WINDOW_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall_now: Callable[[], datetime] = _utc_now,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self._controller = controller
        self._run_window = run_window
        self._window_seconds = window_seconds
        self._monotonic = monotonic
        self._wall_now = wall_now
        self._log = log or logging.getLogger("snaphouse.pairservice")

        self._cond = threading.Condition()
        self._state = "idle"
        self._until: Optional[str] = None
        self._passkey: Optional[str] = None
        self._device: Optional[str] = None
        self._message: Optional[str] = None
        self._accepted = False  # the one request this window has accepted
        self._stopping = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        # Bumped on every start. A window's callbacks and its finally carry the
        # generation they belong to and act only while it is current, so a just
        # finished or stopped window can never clobber the state of the next one.
        self._generation = 0

    # -- public API -------------------------------------------------------- #

    def start(self) -> dict:
        """Open a pairing window. 409 (Conflict) if one is already live."""
        with self._cond:
            if self._state in _LIVE:
                raise Conflict("already open")
            # A finished window's thread is joined so the previous run's finally
            # has fully unwound before the next window opens.
            thread = self._thread
            self._generation += 1
            generation = self._generation
            self._state = "open"
            self._passkey = None
            self._device = None
            self._message = None
            self._accepted = False
            self._stopping = False
            self._stop_event = threading.Event()
            self._until = _iso(self._wall_now() + timedelta(seconds=self._window_seconds))
            self._thread = threading.Thread(
                target=self._run,
                args=(generation,),
                name="snaphouse-pairing-window",
                daemon=True,
            )
            self._thread.start()
            snapshot = self._snapshot_locked()
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        return snapshot

    def state(self) -> dict:
        with self._cond:
            return self._snapshot_locked()

    def confirm(self, accept: bool) -> dict:
        """Kept for API compatibility: nothing is ever awaiting a confirmation.

        The window answers the first phone itself, so the state is never
        ``confirm`` and POST /api/pair/confirm always conflicts (HTTP 409).
        """
        raise Conflict("not awaiting confirmation")

    def stop(self) -> dict:
        """Close the window now. Idempotent; always answers idle."""
        with self._cond:
            self._stopping = True
            self._state = "idle"
            self._until = None
            self._passkey = None
            self._device = None
            self._cond.notify_all()
            snapshot = self._snapshot_locked()
        self._stop_event.set()
        return snapshot

    # -- window callbacks (called from the window/reader threads) ----------- #

    def _request_cb(self, generation: int, passkey: int, device: Optional[str]) -> bool:
        """Answer a phone's pairing request: accept the first, decline the rest.

        The window's reader calls this when a phone's passkey prompt arrives. The
        first request in a live window is accepted at once (the person who opened
        the window is the one pairing) and the phone's name is recorded so the page
        can say who is pairing; every later request, a request after a stop, and a
        request from a superseded window are declined. The passkey is not kept.
        """
        with self._cond:
            if self._stopping or generation != self._generation:
                return False
            if self._accepted or self._state != "open":
                return False
            self._accepted = True
            self._device = device
            self._cond.notify_all()
            return True

    def _report_cb(self, generation: int, event: str, info: dict) -> None:
        """Record the window's terminal result (paired or failed) and close early."""
        with self._cond:
            if self._stopping or generation != self._generation:
                return
            if event == "paired":
                self._device = info.get("device") or self._device
                self._enter_terminal_locked("paired", None)
            else:
                self._enter_terminal_locked("failed", "Pairing did not complete.")
            self._cond.notify_all()
        # Once there is a result, the adapter need not stay discoverable.
        self._stop_event.set()

    # -- the window thread body -------------------------------------------- #

    def _run(self, generation: int) -> None:
        try:
            self._run_window(
                self._controller,
                confirm=lambda passkey, device: self._request_cb(
                    generation, passkey, device
                ),
                report=lambda event, info: self._report_cb(generation, event, info),
                stop_event=self._stop_event,
                duration=self._window_seconds,
            )
        except Exception:  # noqa: BLE001 - the window must never crash the service
            self._log.exception("pairing window failed")
            with self._cond:
                if not self._stopping and generation == self._generation:
                    self._enter_terminal_locked("failed", "Pairing failed.")
        finally:
            with self._cond:
                # The window closed. Settle only if this is still the current
                # window: a superseded run must never touch the next one's state.
                # If it never reached a terminal result, an accepted-but-incomplete
                # pairing is a failure, and an idle open window that timed out
                # returns to idle.
                if generation == self._generation and not self._stopping:
                    if self._state == "open" and self._accepted:
                        self._enter_terminal_locked(
                            "failed", "Pairing did not complete."
                        )
                    elif self._state == "open":
                        self._state = "idle"
                        self._until = None
                        self._passkey = None
                self._cond.notify_all()

    # -- helpers (callers hold the condition) ------------------------------ #

    def _enter_terminal_locked(self, state: str, message: Optional[str]) -> None:
        self._state = state
        self._message = message
        self._passkey = None
        self._until = None

    def _snapshot_locked(self) -> dict:
        return {
            "state": self._state,
            "until": self._until,
            "passkey": self._passkey,
            "device": self._device,
            "message": self._message,
        }


# --------------------------------------------------------------------------- #
# The HTTP surface
# --------------------------------------------------------------------------- #


class PairingApp:
    """Routes the four endpoints to the service; enforces the header guard."""

    def __init__(self, service: PairingService, *, log: Optional[logging.Logger] = None) -> None:
        self.service = service
        self.log = log or logging.getLogger("snaphouse.pairservice")

    def handle_post(self, handler: "_Handler") -> None:
        path = urllib.parse.urlsplit(handler.path).path
        if path not in ("/api/pair/start", "/api/pair/confirm", "/api/pair/stop"):
            if path == "/api/pair/state":
                handler.send_api_error(
                    HTTPStatus.METHOD_NOT_ALLOWED, "Use GET for this endpoint."
                )
            else:
                handler.send_api_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")
            return
        if handler.headers.get(GUARD_HEADER) != GUARD_VALUE:
            handler.send_api_error(HTTPStatus.FORBIDDEN, "That request is not allowed.")
            return

        if path == "/api/pair/start":
            self._start(handler)
        elif path == "/api/pair/confirm":
            self._confirm(handler)
        else:
            self._stop(handler)

    def handle_get(self, handler: "_Handler") -> None:
        path = urllib.parse.urlsplit(handler.path).path
        if path == "/api/pair/state":
            handler.send_api_json(HTTPStatus.OK, self.service.state())
        elif path in ("/api/pair/start", "/api/pair/confirm", "/api/pair/stop"):
            handler.send_api_error(
                HTTPStatus.METHOD_NOT_ALLOWED, "Use POST for this endpoint."
            )
        else:
            handler.send_api_error(HTTPStatus.NOT_FOUND, "Unknown endpoint.")

    def _start(self, handler: "_Handler") -> None:
        _read_body(handler)  # drain any body; start takes none
        try:
            result = self.service.start()
        except Conflict:
            # A window is already open; report it without leaking any state.
            handler.send_api_error(HTTPStatus.CONFLICT, "already open")
            return
        handler.send_api_json(HTTPStatus.ACCEPTED, result)

    def _confirm(self, handler: "_Handler") -> None:
        body = _read_body(handler)
        try:
            data = json.loads(body.decode("utf-8")) if body else None
        except (ValueError, UnicodeDecodeError):
            handler.send_api_error(HTTPStatus.BAD_REQUEST, "That request body was not valid JSON.")
            return
        if not isinstance(data, dict) or not isinstance(data.get("accept"), bool):
            handler.send_api_error(
                HTTPStatus.BAD_REQUEST, "The body must be {\"accept\": true|false}."
            )
            return
        try:
            result = self.service.confirm(data["accept"])
        except Conflict:
            handler.send_api_error(HTTPStatus.CONFLICT, "not awaiting confirmation")
            return
        handler.send_api_json(HTTPStatus.OK, result)

    def _stop(self, handler: "_Handler") -> None:
        _read_body(handler)
        handler.send_api_json(HTTPStatus.OK, self.service.stop())


# A hard cap on a request body: the pairing bodies are tiny JSON. nginx caps it at
# the edge too; this is the service's own enforcement, so a lying Content-Length
# allocates nothing.
MAX_REQUEST_BYTES = 64 * 1024


def _read_body(handler: "_Handler") -> bytes:
    raw = handler.headers.get("Content-Length")
    try:
        length = max(int(raw), 0) if raw else 0
    except ValueError:
        return b""
    if length > MAX_REQUEST_BYTES:
        length = MAX_REQUEST_BYTES
    return handler.rfile.read(length) if length else b""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "snaphouse-pairing"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self.server.app.handle_post(self)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler naming
        self.server.app.handle_get(self)

    def send_api_json(self, status: HTTPStatus, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if int(status) == int(HTTPStatus.METHOD_NOT_ALLOWED):
            self.send_header("Allow", "GET, POST")
        self.end_headers()
        self.wfile.write(body)

    def send_api_error(self, status: HTTPStatus, message: str) -> None:
        self.send_api_json(status, {"error": message})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature
        # Route request logging through the service logger (quiet by default); it
        # never carries a path with a household value anyway.
        logging.getLogger("snaphouse.pairservice").debug(
            "%s - %s", self.address_string(), format % args
        )


def make_server(app: PairingApp, host: str, port: int) -> ThreadingHTTPServer:
    """Build a threaded HTTP server bound to host:port serving app.

    Pass 0 as the port for an ephemeral port (the tests do this); production binds
    127.0.0.1:8782.
    """
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    server.app = app  # type: ignore[attr-defined]
    return server


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _resolve_controller(adapter: Optional[str], prog: str) -> Optional[str]:
    """Return the receive adapter address if it is present, else explain and None."""
    if not adapter:
        print(
            f"{prog}: bt_receive_adapter is not set in {pairing.LOCAL_SETTINGS}",
            file=sys.stderr,
        )
        return None
    try:
        pairing.resolve_adapter(adapter)
    except pairing.AdapterNotFound as exc:
        print(f"{prog}: {exc}", file=sys.stderr)
        return None
    return adapter


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="The hub's web pairing service (loopback HTTP API)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("snaphouse.pairservice")

    prog = "snaphouse-pairing"
    settings = pairing._load_local(pairing.LOCAL_SETTINGS)
    controller = _resolve_controller(settings.get("bt_receive_adapter"), prog)
    if controller is None:
        return 1

    service = PairingService(controller, log=log)
    app = PairingApp(service, log=log)
    server = make_server(app, args.host, args.port)
    log.info("pairing service listening on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - signal path
        pass
    finally:
        service.stop()
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
