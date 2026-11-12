// Tests for the pure model behind Settings > "Bluetooth devices". Run with:
//   node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  parseDevices,
  deviceStatus,
  lastConnectedText,
  deviceDisconnectRequest,
  deviceForgetRequest,
  interpretDeviceResponse,
  deviceButtons,
  forgetPrompt,
  forgetReminder,
  shouldPollPair,
} from "../../roles/room_picker/files/site/rooms-model.js";

const NOW = Date.parse("2026-11-12T20:00:00Z");
const PIXEL = { id: "a".repeat(32), name: "Pixel 7", connected: true, last_connected: "2026-11-12T19:59:40Z" };
const IPHONE = { id: "b".repeat(32), name: "Alex's iPhone", connected: false, last_connected: "2026-11-09T08:00:00Z" };

test("parseDevices reads the list in the order the hub sent it", () => {
  const out = parseDevices(JSON.stringify({ devices: [PIXEL, IPHONE] }));
  assert.deepEqual(out.map((d) => d.name), ["Pixel 7", "Alex's iPhone"]);
  assert.deepEqual(out[0], { id: PIXEL.id, name: "Pixel 7", connected: true, lastConnected: PIXEL.last_connected });
});

test("parseDevices returns null for a body that is not a device list", () => {
  for (const bad of ["", "nope", "[]", "{}", '{"devices": 5}', "null"]) {
    assert.equal(parseDevices(bad), null, bad);
  }
});

test("parseDevices drops malformed entries and normalizes odd fields", () => {
  const out = parseDevices(
    JSON.stringify({
      devices: [
        null,
        { name: "No id", connected: true },
        { id: "" , name: "Empty id" },
        { id: "c".repeat(32), name: "  ", connected: "yes", last_connected: 5 },
      ],
    }),
  );
  assert.equal(out.length, 1);
  assert.equal(out[0].name, "Unnamed device");
  assert.equal(out[0].connected, false);
  assert.equal(out[0].lastConnected, null);
});

test("parseDevices ignores an unparseable last_connected", () => {
  const out = parseDevices(JSON.stringify({ devices: [{ ...IPHONE, last_connected: "yesterday-ish" }] }));
  assert.equal(out[0].lastConnected, null);
});

test("deviceStatus says Connected or Not connected", () => {
  assert.equal(deviceStatus({ connected: true }), "Connected");
  assert.equal(deviceStatus({ connected: false }), "Not connected");
});

test("lastConnectedText is blank while connected and plain when not", () => {
  assert.equal(lastConnectedText({ connected: true, lastConnected: PIXEL.last_connected }, NOW), null);
  const t = (iso) => lastConnectedText({ connected: false, lastConnected: iso }, NOW);
  assert.equal(t(null), "Not seen connected yet");
  assert.equal(t("2026-11-12T19:59:40Z"), "Last connected just now");
  assert.equal(t("2026-11-12T19:55:00Z"), "Last connected 5 minutes ago");
  assert.equal(t("2026-11-12T19:59:00Z"), "Last connected 1 minute ago");
  assert.equal(t("2026-11-12T17:00:00Z"), "Last connected 3 hours ago");
  assert.equal(t("2026-11-12T19:00:00Z"), "Last connected 1 hour ago");
  assert.equal(t("2026-11-11T19:00:00Z"), "Last connected yesterday");
  assert.equal(t("2026-11-09T08:00:00Z"), "Last connected 3 days ago");
  assert.equal(t("2026-10-01T12:00:00Z"), "Last connected Oct 1");
  assert.equal(t("2025-10-01T12:00:00Z"), "Last connected Oct 1, 2025");
});

test("a last-connected time in the future reads as just now", () => {
  assert.equal(
    lastConnectedText({ connected: false, lastConnected: "2026-11-12T20:05:00Z" }, NOW),
    "Last connected just now",
  );
});

test("the action requests are guarded POSTs carrying only the opaque id", () => {
  const d = deviceDisconnectRequest(PIXEL.id);
  assert.equal(d.url, "/api/pair/devices/disconnect");
  assert.equal(d.init.method, "POST");
  assert.equal(d.init.headers["X-Snaphouse"], "1");
  assert.deepEqual(JSON.parse(d.init.body), { id: PIXEL.id });
  const f = deviceForgetRequest(PIXEL.id);
  assert.equal(f.url, "/api/pair/devices/forget");
  assert.deepEqual(JSON.parse(f.init.body), { id: PIXEL.id });
});

test("interpretDeviceResponse maps disconnect and forget answers", () => {
  const ok = interpretDeviceResponse(200, JSON.stringify({ ...PIXEL, connected: false }), "disconnect");
  assert.equal(ok.ok, true);
  assert.equal(ok.device.connected, false);
  assert.equal(ok.device.name, "Pixel 7");
  assert.deepEqual(interpretDeviceResponse(200, '{"forgotten": true}', "forget"), { ok: true });
  const gone = interpretDeviceResponse(404, '{"error":"x"}', "forget");
  assert.equal(gone.ok, false);
  assert.equal(gone.gone, true);
  assert.match(gone.error, /no longer paired/i);
  const notConnected = interpretDeviceResponse(409, '{"error":"x"}', "disconnect");
  assert.equal(notConnected.stale, true);
  assert.match(notConnected.error, /not connected/i);
  assert.match(interpretDeviceResponse(503, '{"error":"Bluetooth is not available."}', "forget").error, /Bluetooth/);
  assert.match(interpretDeviceResponse(500, "", "forget").error, /500/);
  assert.equal(interpretDeviceResponse(403, "", "forget").forbidden, true);
});

test("a 200 disconnect with an unreadable body still succeeds without a device", () => {
  const r = interpretDeviceResponse(200, "garbage", "disconnect");
  assert.equal(r.ok, true);
  assert.equal(r.device, null);
});

test("deviceButtons: Disconnect only while connected, Forget always, all off while pending", () => {
  const idle = { pending: false, confirmingId: null };
  assert.deepEqual(deviceButtons(PIXEL, idle), {
    disconnect: { visible: true, disabled: false },
    forget: { visible: true, disabled: false },
    confirm: false,
  });
  assert.equal(deviceButtons({ ...PIXEL, connected: false }, idle).disconnect.visible, false);
  const busy = deviceButtons(PIXEL, { pending: true, confirmingId: null });
  assert.equal(busy.disconnect.disabled, true);
  assert.equal(busy.forget.disabled, true);
});

test("deviceButtons: forgetting asks first, and only for the chosen device", () => {
  const confirming = { pending: false, confirmingId: PIXEL.id };
  const chosen = deviceButtons(PIXEL, confirming);
  assert.equal(chosen.confirm, true);
  assert.equal(chosen.disconnect.visible, false);
  assert.equal(chosen.forget.visible, false);
  const other = deviceButtons({ ...IPHONE }, confirming);
  assert.equal(other.confirm, false);
  assert.equal(other.forget.visible, true);
});

test("the forget prompt names the phone and says what is lost; the reminder names the phone-side step", () => {
  const p = forgetPrompt(PIXEL);
  assert.equal(p.question, "Forget Pixel 7?");
  assert.equal(p.confirm, "Forget");
  assert.equal(p.cancel, "Keep");
  assert.match(p.detail, /until you pair it again/i);
  assert.match(forgetReminder("Pixel 7"), /Pixel 7/);
  assert.match(forgetReminder("Pixel 7"), /forget/i);
  assert.match(forgetReminder("Pixel 7"), /phone/i);
});

test("the devices list polls under the pairing rule: visible tab, Settings showing", () => {
  assert.equal(shouldPollPair({ documentVisible: true, settingsVisible: true, windowActive: false }), true);
  assert.equal(shouldPollPair({ documentVisible: false, settingsVisible: true, windowActive: false }), false);
  assert.equal(shouldPollPair({ documentVisible: true, settingsVisible: false, windowActive: false }), false);
});
