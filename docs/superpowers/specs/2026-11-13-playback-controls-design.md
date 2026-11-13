# Playback controls: design

## Why
The owner listens to podcasts from their phone over Bluetooth into the house. To pause or rewind they have to reach the phone. They want play/pause, back and forward from the house page (phone or wall panel), and from the speakers' own buttons, above all the JBL in the bathroom while showering ("rewind" there means Pocket Casts' skip back).

## Goals
- Back, play/pause and forward on the house page's now-playing card while Bluetooth is the source.
- The JBL's buttons (paired to the nightstand Pi) and the Vizio soundbar's buttons (paired to the hub) control the phone the same way.
- Commands reach the phone as standard AVRCP Play, Pause, Next and Previous, so each app decides what they mean (Pocket Casts: skip forward 30 s and back 10 s by default; music apps: tracks).

## Non-goals
- AirPlay control (a later step once tested on the owner's phone).
- Seeking, volume through AVRCP, stop, shuffle or repeat.
- Changing what the phone's app does with next and previous.

## Anti-goals
- A speaker button or page tap that silently does nothing when it could have told the owner. The page shows a short toast on failure; speaker buttons log on the device.
- Any change to how audio flows or to the rooms' on/off.

## Constraints
- No em dashes. No names, MACs or IPs in the repo (the speaker addresses stay in each device's local.yml, as the bt_speaker role already does).
- jeepney for D-Bus (already a dependency); no new Python dependencies (read input events from /dev/input with the stdlib).
- Page: ES modules, pure logic in a model file with `node --test` tests; 44 px targets, 48 px on the kiosk.

## Approaches considered
1. **Snapcast stream control (chosen).** The Bluetooth stream's existing controlscript (lib/snaphouse/avrcp_meta.py) reports it can be controlled and turns Snapcast's `Plugin.Stream.Player.Control` into BlueZ `MediaPlayer1` calls on the phone. The page and the speaker listeners all send one standard request, `Stream.Control` with `id: "Bluetooth"`, to snapserver's JSON-RPC (the page over its existing socket, the nightstand over the hub's `/jsonrpc` proxy). One control path, already on the wire, testable as pure functions.
2. A separate HTTP control service on the hub. Rejected: a second API and nginx route for what Snapcast already defines.
3. Mimicking the JBL's press counts. Rejected: that is the speaker's own firmware sending AVRCP; the hub can send AVRCP directly.

The request targets the `Bluetooth` stream directly rather than the `House` meta stream, so it does not depend on how the meta stream forwards control.

## Decided
- Buttons show only when now-playing's `source` is `bluetooth`; the play/pause button shows the current state and flips immediately, then follows now-playing.
- Speaker buttons: key presses from the BlueZ "(AVRCP)" input device of the host's configured speaker only (matched by its address from local.yml), never the phone's own AVRCP input device. Key down only; repeats ignored.

## Left to the builder
Exact button glyphs and spacing within the panel-ink visual system; toast wording within the existing plain-language style; how the listener rescans for the speaker's input device as it connects and disconnects.
