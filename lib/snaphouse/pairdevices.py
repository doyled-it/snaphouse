"""The Bluetooth devices list behind Settings > "Bluetooth devices".

Lists the phones paired to the hub's receive adapter (name, connected or not, and
when each last connected), drops a connection while keeping the pairing, and
forgets a pairing. It is the device half of the pairing service
(``snaphouse.pairservice``), which exposes it at ``/api/pair/devices``.

Privacy rules, because the hub's Bluetooth addresses are the household's:

  - A device's ``id`` is opaque: a keyed hash (HMAC-SHA256) of its address with a
    per-host secret held in root-only state, so it is stable for one device on one
    host and says nothing about the address. The address itself is never returned
    and never logged, and neither is a device's name.
  - ``last_connected`` is recorded in root-only state keyed by that id, never by
    address, and entries for devices no longer paired are dropped.

bluetoothctl is injected (``session``) and so is the clock, so every path is
tested without BlueZ. Adapters are addressed the way the rest of the pairing code
does: by address, selected at the start of every session and checked.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from snaphouse import pairing

#: Where the secret and the last-connected times live (root-only).
DEFAULT_STATE_DIR = Path("/var/lib/snaphouse/pairing")

#: How often the background tick samples who is connected.
TICK_SECONDS = 30.0

#: Shown for a device that never advertised a name (the address is never shown).
UNNAMED = "Unnamed device"

_ID = re.compile(r"[0-9a-f]{8,64}")
_INFO_HEADER = re.compile(r"^\s*Device\s+((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})\b")
_CONNECTED = re.compile(r"^\s*Connected:\s*(yes|no)\b")


class BadId(Exception):
    """The id in a request is not shaped like an id (HTTP 400)."""


class UnknownDevice(Exception):
    """No paired device has that id (HTTP 404)."""


class NotConnected(Exception):
    """The device is not connected, so there is nothing to disconnect (HTTP 409)."""


class Unavailable(Exception):
    """bluetoothctl could not be used (HTTP 503)."""


def _iso(instant: datetime) -> str:
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_private(path: Path, text: str) -> None:
    """Write text to path atomically with mode 0600."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class DeviceBook:
    """The paired devices of one adapter, with their connection history."""

    def __init__(
        self,
        controller: str,
        *,
        state_dir: Path = DEFAULT_STATE_DIR,
        session: Callable[..., str] = pairing._run_session,
        wall_now: Callable[[], datetime] = _utc_now,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self._controller = controller
        self._dir = Path(state_dir)
        self._session = session
        self._wall_now = wall_now
        self._log = log or logging.getLogger("snaphouse.pairdevices")
        self._lock = threading.RLock()
        self._secret: Optional[bytes] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- public API -------------------------------------------------------- #

    def devices(self) -> list[dict]:
        """Every paired device, connected first then by name; records who is connected."""
        with self._lock:
            return self._view(self._read())

    def sample(self) -> None:
        """Record who is connected now (the background tick)."""
        with self._lock:
            self._read()

    def disconnect(self, device_id: object) -> dict:
        """Drop the device's current connection and keep the pairing."""
        with self._lock:
            address, device = self._find(device_id)
            if not device["connected"]:
                raise NotConnected("not connected")
            self._run([f"select {self._controller}", f"disconnect {address}"])
            after = self._read()
            for entry in self._view(after):
                if entry["id"] == device["id"]:
                    return entry
            raise UnknownDevice("unknown device")

    def forget(self, device_id: object) -> None:
        """Remove the pairing on the hub and its stored connection time."""
        with self._lock:
            address, device = self._find(device_id)
            self._run([f"select {self._controller}", f"remove {address}"])
            times = self._load_times()
            if times.pop(device["id"], None) is not None:
                self._save_times(times)

    def start_ticker(self, interval: float = TICK_SECONDS) -> None:
        """Sample who is connected every `interval` seconds on a daemon thread."""
        if self._thread is not None:
            return
        self._stop.clear()

        def tick() -> None:
            while not self._stop.wait(interval):
                try:
                    self.sample()
                except Exception:  # noqa: BLE001 - a bad tick must not end the loop
                    self._log.warning("device sampling failed")

        self._thread = threading.Thread(target=tick, name="snaphouse-device-tick", daemon=True)
        self._thread.start()

    def stop_ticker(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    # -- bluetoothctl ------------------------------------------------------ #

    def _run(self, commands: list[str]) -> str:
        try:
            return pairing._check_select(self._session(commands))
        except pairing.AdapterSelectFailed as exc:
            raise Unavailable("bluetooth is not available") from exc
        except OSError as exc:
            raise Unavailable("bluetooth is not available") from exc

    def _read(self) -> list[dict]:
        """Paired devices with a connected flag; also records connection times."""
        out = self._run([f"select {self._controller}", "paired-devices"])
        paired = pairing._paired_devices(out)
        connected: dict[str, bool] = {}
        if paired:
            info = self._run(
                [f"select {self._controller}", *(f"info {a}" for a in paired)]
            )
            connected = self._parse_connected(info)
        entries = []
        for address, name in paired.items():
            entries.append(
                {
                    "address": address,
                    "id": self._id(address),
                    "name": UNNAMED if self._unnamed(address, name) else name,
                    "connected": connected.get(address, False),
                }
            )
        self._record(entries)
        return entries

    @staticmethod
    def _unnamed(address: str, name: str) -> bool:
        return name.replace(":", "").replace("-", "").upper() == address.replace(":", "").upper()

    @staticmethod
    def _parse_connected(output: str) -> dict[str, bool]:
        result: dict[str, bool] = {}
        current: Optional[str] = None
        for raw in output.splitlines():
            line = pairing._strip_ansi(raw)
            header = _INFO_HEADER.match(line)
            if header is not None:
                current = header.group(1)
                continue
            flag = _CONNECTED.match(line)
            if flag is not None and current is not None:
                result[current] = flag.group(1) == "yes"
        return result

    # -- identity and persistence ----------------------------------------- #

    def _ensure_dir(self) -> None:
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)

    def _secret_bytes(self) -> bytes:
        if self._secret is not None:
            return self._secret
        self._ensure_dir()
        path = self._dir / "secret"
        try:
            self._secret = bytes.fromhex(path.read_text().strip())
        except (OSError, ValueError):
            self._secret = b""
        if len(self._secret) < 16:
            self._secret = os.urandom(32)
            _write_private(path, self._secret.hex() + "\n")
        return self._secret

    def _id(self, address: str) -> str:
        digest = hmac.new(self._secret_bytes(), address.upper().encode(), hashlib.sha256)
        return digest.hexdigest()[:32]

    def _times_path(self) -> Path:
        return self._dir / "last-connected.json"

    def _load_times(self) -> dict[str, str]:
        try:
            data = json.loads(self._times_path().read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}

    def _save_times(self, times: dict[str, str]) -> None:
        self._ensure_dir()
        _write_private(self._times_path(), json.dumps(times, sort_keys=True) + "\n")

    def _record(self, entries: list[dict]) -> None:
        """Stamp connected devices with now; drop times of devices not paired."""
        times = self._load_times()
        updated = {k: v for k, v in times.items() if k in {e["id"] for e in entries}}
        now = _iso(self._wall_now())
        for entry in entries:
            if entry["connected"]:
                updated[entry["id"]] = now
        if updated != times or not self._times_path().exists():
            self._save_times(updated)

    def _view(self, entries: list[dict]) -> list[dict]:
        times = self._load_times()
        shown = [
            {
                "id": e["id"],
                "name": e["name"],
                "connected": e["connected"],
                "last_connected": times.get(e["id"]),
            }
            for e in entries
        ]
        shown.sort(key=lambda d: (not d["connected"], d["name"].casefold()))
        return shown

    def _find(self, device_id: object) -> tuple[str, dict]:
        if not isinstance(device_id, str) or _ID.fullmatch(device_id) is None:
            raise BadId("bad id")
        for entry in self._read():
            if entry["id"] == device_id:
                return entry["address"], entry
        raise UnknownDevice("unknown device")
