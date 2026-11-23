import { test } from "node:test";
import assert from "node:assert/strict";
import { garageView } from "../../roles/room_picker/files/site/garage-model.js";
import {
  TRAVEL_MS,
  ARM_MS,
  garageFill,
  garageButton,
  armState,
} from "../../roles/room_picker/files/site/panel-garage-model.js";

const doc = (over) => ({
  connected: true,
  lastSeenMs: 1000,
  state: "closed",
  sinceMs: 0,
  sinceApproximate: false,
  position: null,
  lastKnownState: null,
  obstruction: null,
  lockout: null,
  settings: {},
  recent: [],
  ...over,
});
const fill = (d, now = 5000, start = 0) => garageFill(d, garageView(d, now), now, start);

test("the travel time constant is 15 s and the arm window 4 s", () => {
  assert.equal(TRAVEL_MS, 15000);
  assert.equal(ARM_MS, 4000);
});

test("closed is full and open is empty when there is no position", () => {
  assert.deepEqual(fill(doc({ state: "closed" })), { fill: 1, text: null });
  assert.deepEqual(fill(doc({ state: "open" })), { fill: 0, text: null });
});

test("position drives the fill (0 closed, 100 open), also while stopped", () => {
  assert.equal(fill(doc({ state: "stopped", position: 25 })).fill, 0.75);
  assert.equal(fill(doc({ state: "open", position: 100 })).fill, 0);
  assert.equal(fill(doc({ state: "closed", position: 0 })).fill, 1);
});

test("a moving door with a position reads Opening, N% and Closing, N%", () => {
  const o = fill(doc({ state: "opening", position: 40 }));
  assert.deepEqual([o.fill, o.text], [0.6, "Opening, 40%"]);
  const c = fill(doc({ state: "closing", position: 30 }));
  assert.deepEqual([c.fill, c.text], [0.7, "Closing, 70%"]);
});

test("a moving door with no position follows the 15 s clock and clamps", () => {
  assert.equal(fill(doc({ state: "opening" }), 7500, 0).fill, 0.5);
  assert.equal(fill(doc({ state: "closing" }), 3000, 0).fill, 0.2);
  assert.equal(fill(doc({ state: "opening" }), 20000, 0).fill, 0);
  assert.equal(fill(doc({ state: "closing" }), 20000, 0).fill, 1);
  assert.equal(fill(doc({ state: "opening" }), 7500, 0).text, "Opening");
});

test("a stopped door with no position is half full", () => {
  assert.equal(fill(doc({ state: "stopped" })).fill, 0.5);
});

test("link down or unknown state gives no fill and never says closed", () => {
  assert.deepEqual(fill(doc({ connected: false, state: "closed", position: 0 })), { fill: null, text: null });
  assert.deepEqual(fill(doc({ state: "unknown" })), { fill: null, text: null });
});

test("garageButton picks the action from the state and disables when it cannot act", () => {
  const b = (d) => garageButton(d, garageView(d, 5000));
  assert.deepEqual(b(doc({ state: "closed" })), { action: "open", disabled: false });
  assert.deepEqual(b(doc({ state: "open" })), { action: "close", disabled: false });
  assert.deepEqual(b(doc({ state: "stopped" })), { action: "open", disabled: false });
  assert.equal(b(doc({ state: "opening" })).disabled, true);
  assert.equal(b(doc({ state: "closing" })).disabled, true);
  assert.equal(b(doc({ connected: false })).disabled, true);
  assert.equal(b(doc({ state: "open", obstruction: true })).disabled, true);
});

test("armState: the first tap arms for 4 s with the action's label", () => {
  const a = armState(null, { type: "tap", action: "open" }, 1000);
  assert.deepEqual(a, { armed: true, action: "open", label: "Tap to open", expiresMs: 5000, send: false });
  assert.equal(armState(null, { type: "tap", action: "close" }, 0).label, "Tap to close");
});

test("armState: a second tap inside the window sends and disarms", () => {
  const a = armState(null, { type: "tap", action: "open" }, 1000);
  const b = armState(a, { type: "tap", action: "open" }, 4999);
  assert.deepEqual(b, { armed: false, action: null, label: null, expiresMs: null, send: true, sendAction: "open" });
});

test("armState: a tick after the window disarms, and a late tap re-arms instead of sending", () => {
  const a = armState(null, { type: "tap", action: "open" }, 1000);
  assert.equal(armState(a, { type: "tick" }, 4999).armed, true);
  assert.equal(armState(a, { type: "tick" }, 5000).armed, false);
  const late = armState(a, { type: "tap", action: "open" }, 5001);
  assert.deepEqual([late.armed, late.send, late.expiresMs], [true, false, 9001]);
});

test("armState: a tap for another action re-arms for that action", () => {
  const a = armState(null, { type: "tap", action: "open" }, 0);
  const b = armState(a, { type: "tap", action: "close" }, 100);
  assert.deepEqual([b.armed, b.action, b.send], [true, "close", false]);
});

test("armState: a disabled button never arms", () => {
  const a = armState(null, { type: "tap", action: "open", disabled: true }, 0);
  assert.deepEqual([a.armed, a.send], [false, false]);
});

// ---- glide: the fill moves between the opener's position reports ----
import { GLIDE_AHEAD, glideRate, glideOpen } from "../../roles/room_picker/files/site/panel-garage-model.js";

test("glideRate uses the observed speed between two reports, else the usual travel time", () => {
  assert.equal(glideRate(null, { open: 0.1, ms: 1000 }), 1 / TRAVEL_MS);
  // 20 points in 2 s: 0.1 per second.
  const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-12, `${a} vs ${b}`);
  near(glideRate({ open: 0.1, ms: 1000 }, { open: 0.3, ms: 3000 }), 0.2 / 2000);
  // Closing gives the same positive speed.
  near(glideRate({ open: 0.6, ms: 0 }, { open: 0.4, ms: 2000 }), 0.2 / 2000);
  // No time between reports, or no movement: fall back.
  assert.equal(glideRate({ open: 0.1, ms: 1000 }, { open: 0.3, ms: 1000 }), 1 / TRAVEL_MS);
  assert.equal(glideRate({ open: 0.3, ms: 0 }, { open: 0.3, ms: 2000 }), 1 / TRAVEL_MS);
  // Absurd speeds are clamped to 5 to 40 s for a full travel.
  assert.equal(glideRate({ open: 0, ms: 0 }, { open: 0.9, ms: 100 }), 1 / 5000);
  assert.equal(glideRate({ open: 0, ms: 0 }, { open: 0.01, ms: 10000 }), 1 / 40000);
});

test("glideOpen moves ahead of the last report at the rate, capped and never backwards", () => {
  const rate = 1 / 10000; // 10 s full travel
  // Opening: 1 s after a report at 40% open, 50%.
  assert.ok(Math.abs(glideOpen({ open: 0.4, ms: 0 }, 1, rate, 1000, 0.4) - 0.5) < 1e-9);
  // Never more than GLIDE_AHEAD past the report.
  assert.equal(glideOpen({ open: 0.4, ms: 0 }, 1, rate, 60000, 0.4), 0.4 + GLIDE_AHEAD);
  // Closing goes down.
  assert.ok(Math.abs(glideOpen({ open: 0.6, ms: 0 }, -1, rate, 1000, 0.6) - 0.5) < 1e-9);
  // Never backwards: a report behind what is already shown holds the shown value.
  assert.equal(glideOpen({ open: 0.5, ms: 0 }, 1, rate, 0, 0.55), 0.55);
  assert.equal(glideOpen({ open: 0.5, ms: 0 }, -1, rate, 0, 0.45), 0.45);
  // Clamped to the ends.
  assert.equal(glideOpen({ open: 0.98, ms: 0 }, 1, rate, 5000, 0.98), 1);
  assert.equal(glideOpen({ open: 0.02, ms: 0 }, -1, rate, 5000, 0.02), 0);
});
