# Playback Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play/pause, back and forward for the phone's Bluetooth playback, from the house page and from the JBL and Vizio buttons.

**Architecture:** avrcp_meta.py (the Bluetooth stream's Snapcast controlscript) accepts `Plugin.Stream.Player.Control` and calls BlueZ `MediaPlayer1` on the phone. The page and a new speaker-key listener send `Stream.Control` with `id: "Bluetooth"` to snapserver.

**Tech Stack:** Python 3 (stdlib plus jeepney) with pytest; Ansible; ES modules with `node --test`.

**Spec:** `docs/superpowers/specs/2026-11-13-playback-controls-design.md`

## Global Constraints
- No em dashes anywhere. No names, MACs or IPs in tracked files.
- The control contract every task uses: JSON-RPC `{"method": "Stream.Control", "params": {"id": "Bluetooth", "command": C}}` with C one of `play`, `pause`, `playPause`, `next`, `previous`.
- No new Python dependencies; jeepney for D-Bus; stdlib for /dev/input.
- Page targets 44 px, 48 px in the kiosk media query.

## Review Focus
1. The phone disconnects (no MediaPlayer1): the stream reports it cannot be controlled, a control request gets a JSON-RPC error, nothing crashes.
2. The phone's own "(AVRCP)" input device on the hub never triggers commands (feedback loop risk).
3. AirPlay is the source: no buttons on the page; a speaker press sends the request and the error is logged, not retried.
4. A held or bouncing speaker button sends one command (key repeats ignored).
5. The speaker reconnects (new /dev/input/eventN): the listener finds it again without a restart.

---

### Task 1: The Bluetooth stream accepts control (hub)

**Files:** Modify `lib/snaphouse/avrcp_meta.py`, `tests/python/test_avrcp_meta.py`.

**Interfaces:** Produces the control contract above on the hub.

- [ ] Failing tests: `build_properties(track, status, position=None, controllable=True)` sets `canControl`, `canPlay`, `canPause`, `canGoNext`, `canGoPrevious` true (canSeek stays false) and all false when `controllable=False`; `Player.properties()` is controllable exactly when `Player.path` is set; `control_method("playPause", "playing") == "Pause"`, `("playPause", "paused") == "Play"`, `("play", x) == "Play"`, `("pause", x) == "Pause"`, `("next", x) == "Next"`, `("previous", x) == "Previous"`, `("seek", x) is None`; `handle_request(req, player, control)` for `Plugin.Stream.Player.Control` calls `control(method_name)` and answers `{"result": "ok"}`, answers error -32602 for an unknown command, -32603 when no player path or `control` raises; a ("player", path) event sets `Player.path` and ("player", None) clears it and emits a properties notification; Review Focus 1.
- [ ] Implement: `control_method(command: str, status: Any) -> str | None`; `Player.path: str | None`; `handle_request(request, player, control: Callable[[str], None])`; in the D-Bus half, emit ("player", path) from GetManagedObjects and from the signal's object path, ("player", None) on `InterfacesRemoved` of `org.bluez.MediaPlayer1`; a `_BluezControl` with its own jeepney connection that calls the method on `org.bluez.MediaPlayer1` at `player.path` with a 2 s timeout. Update the module docstring (the stream is now controllable; volume set requests are still declined).
- [ ] `uv run pytest tests/python/test_avrcp_meta.py` then the full suite; commit.
- [ ] Controller step on the hub after merge: while the phone plays, `curl -s -d '{"id":1,"jsonrpc":"2.0","method":"Stream.Control","params":{"id":"Bluetooth","command":"playPause"}}' http://127.0.0.1:1780/jsonrpc` pauses the phone.

### Task 2: Buttons on the now-playing card (page)

**Files:** Modify `roles/room_picker/files/site/rooms-model.js` (keep `source`), `listen-model.js`, `index.html` (the `#np` card), `rooms.js`, `style.css`; tests in `tests/js/`.

**Interfaces:** Consumes the control contract.

- [ ] Failing tests: `parseNowPlayingDoc` keeps `source` (string or null); `playbackControls(track)` in listen-model.js returns `{visible: false}` for null or a non-`bluetooth` source and `{visible: true, playing: track.status !== "paused"}` for bluetooth; `playbackRequest(command)` returns `{method: "Stream.Control", params: {id: "Bluetooth", command}}` for the five commands and throws for others; Review Focus 3.
- [ ] Implement: `<div id="np-controls" class="np-controls" hidden>` in the card with three buttons (`np-prev` aria-label "Back", `np-playpause` aria-label "Pause" or "Play" by state, `np-next` aria-label "Forward"); taps send through the existing `sendControl`; play/pause flips its icon immediately and follows the next now-playing refresh; on a JSON-RPC error show the toast "Couldn't reach your phone".
- [ ] Browser check with stub data at 412x915 and 800x480 (bluetooth source shows the controls; airplay hides them); full `node --test tests/js/*.test.*`; commit.

### Task 3: Speaker buttons (hub and nightstand)

**Files:** Create `lib/snaphouse/avrcp_keys.py`, `tests/python/test_avrcp_keys.py`, `roles/bt_speaker/templates/snaphouse-avrcp-keys.service.j2`; modify `roles/bt_speaker/tasks/main.yml`, role tests.

**Interfaces:** Consumes the control contract. The hub posts to `http://127.0.0.1:1780/jsonrpc`; other hosts post to `http://{{ hub hostname }}.local/jsonrpc` (the nginx proxy). The speaker's address comes from local.yml the same way the keepalive gets it.

- [ ] Failing tests: `parse_input_devices(text) -> list[InputDevice(name, uniq, event)]` over a /proc/bus/input/devices sample; `speaker_device(devices, address)` picks the "(AVRCP)" device whose `uniq` matches the address (case-insensitive) and never another (Review Focus 2); `decode_events(buf: bytes)` unpacks 24-byte `struct input_event` records; `command_for(code, value)` maps key-down (value 1) of KEY_PLAYPAUSE (164), KEY_PLAYCD (200), KEY_PAUSECD (201), KEY_PLAY (207), KEY_PAUSE (119), KEY_NEXTSONG (163), KEY_PREVIOUSSONG (165) to `playPause`, `play`, `pause`, `play`, `pause`, `next`, `previous`, and returns None for value 0 or 2 (Review Focus 4); `control_body(command)` builds the contract body; the run loop rescans every 5 s when the device is gone (Review Focus 5) and logs one line per failed post (Review Focus 3).
- [ ] Implement the module and a `snaphouse-avrcp-keys` unit (Restart=always, a user in the `input` group, sandboxed), installed and started by bt_speaker on both Bluetooth-output hosts and gated on common_systemd_init like the keepalive.
- [ ] Full Python suite; container apply idempotent; commit.
- [ ] Controller step after merge (owner present): `evtest`-style capture confirms which keys the JBL and the Vizio send, then a press pauses the phone.
