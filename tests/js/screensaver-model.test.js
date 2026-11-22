import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parsePanelParams,
  inQuietHours,
  idleLimitMs,
  shouldShow,
} from "../../roles/room_picker/files/site/screensaver-model.js";

const at = (h, m = 0) => new Date(2026, 10, 22, h, m, 0);
const P = parsePanelParams("?panel=1");

test("no panel parameter means no screensaver", () => {
  assert.equal(parsePanelParams(""), null);
  assert.equal(parsePanelParams("?panel=0"), null);
  assert.equal(parsePanelParams("?x=1"), null);
});

test("panel=1 alone gives the defaults", () => {
  assert.deepEqual(P, { dayIdleMs: 300000, nightIdleMs: 60000, quietStart: "22:00", quietEnd: "06:00" });
});

test("bad values fall back per field", () => {
  const p = parsePanelParams("?panel=1&idle=abc&qs=99:99&nidle=30&qe=07:30");
  assert.equal(p.dayIdleMs, 300000);
  assert.equal(p.quietStart, "22:00");
  assert.equal(p.nightIdleMs, 30000);
  assert.equal(p.quietEnd, "07:30");
});

test("quiet hours wrap midnight, start inclusive, end exclusive", () => {
  assert.equal(inQuietHours(at(21, 59), P), false);
  assert.equal(inQuietHours(at(22, 0), P), true);
  assert.equal(inQuietHours(at(5, 59), P), true);
  assert.equal(inQuietHours(at(6, 0), P), false);
});

test("equal start and end is never quiet", () => {
  const p = parsePanelParams("?panel=1&qs=22:00&qe=22:00");
  assert.equal(inQuietHours(at(22, 0), p), false);
  assert.equal(inQuietHours(at(3, 0), p), false);
});

test("a same-day window works too", () => {
  const p = parsePanelParams("?panel=1&qs=01:00&qe=05:00");
  assert.equal(inQuietHours(at(2), p), true);
  assert.equal(inQuietHours(at(23), p), false);
});

test("idle limit is 5 min by day and 60 s at night", () => {
  assert.equal(idleLimitMs(at(12), P), 300000);
  assert.equal(idleLimitMs(at(23), P), 60000);
});

test("shouldShow by day needs the full idle time", () => {
  assert.equal(shouldShow({ idleMs: 299999, date: at(12), params: P, inputHeld: false }), false);
  assert.equal(shouldShow({ idleMs: 300000, date: at(12), params: P, inputHeld: false }), true);
});

test("a panel already idle shows at once when 22:00 arrives", () => {
  assert.equal(shouldShow({ idleMs: 90000, date: at(21, 59), params: P, inputHeld: false }), false);
  assert.equal(shouldShow({ idleMs: 90000, date: at(22, 0), params: P, inputHeld: false }), true);
});

test("at 06:00 the day limit applies again", () => {
  assert.equal(shouldShow({ idleMs: 90000, date: at(6, 0), params: P, inputHeld: false }), false);
});

test("a held input never shows it", () => {
  assert.equal(shouldShow({ idleMs: 9e9, date: at(23), params: P, inputHeld: true }), false);
});
