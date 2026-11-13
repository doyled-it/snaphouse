// Tests for the haptics helper. Run with: node --test tests/js/*.test.*

import { test } from "node:test";
import assert from "node:assert/strict";
import { createHaptics } from "../../roles/room_picker/files/site/haptics.js";

function fake() {
  const calls = [];
  return { calls, vibrate: (ms) => calls.push(ms) };
}

test("volume vibrates 5 across a ten boundary", () => {
  const nav = fake();
  const h = createHaptics(nav);
  h.volume(49, 51);
  h.volume(51, 49);
  assert.deepEqual(nav.calls, [5, 5]);
});

test("volume vibrates 14 at 0 and at 100", () => {
  const nav = fake();
  const h = createHaptics(nav);
  h.volume(3, 0);
  h.volume(97, 100);
  h.volume(0, 0);
  h.volume(100, 100);
  assert.deepEqual(nav.calls, [14, 14]);
});

test("volume does nothing inside a decade", () => {
  const nav = fake();
  const h = createHaptics(nav);
  h.volume(41, 48);
  h.volume(40, 49);
  assert.deepEqual(nav.calls, []);
});

test("roomOn 10, roomOff 6, playPause 8", () => {
  const nav = fake();
  const h = createHaptics(nav);
  h.roomOn();
  h.roomOff();
  h.playPause();
  assert.deepEqual(nav.calls, [10, 6, 8]);
});

test("missing navigator.vibrate is a no-op", () => {
  const h = createHaptics({});
  assert.doesNotThrow(() => {
    h.volume(0, 100);
    h.roomOn();
    h.roomOff();
    h.playPause();
  });
  assert.doesNotThrow(() => createHaptics(undefined).roomOn());
});

test("a throwing vibrate is swallowed", () => {
  const h = createHaptics({
    vibrate: () => {
      throw new Error("blocked");
    },
  });
  assert.doesNotThrow(() => {
    h.volume(0, 100);
    h.roomOn();
  });
});
