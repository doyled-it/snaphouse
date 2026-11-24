import { test } from "node:test";
import assert from "node:assert/strict";
import { MOVE_EVERY_MIN, clockText, msToNextMinute, moveDue, pickSpot } from "../../roles/room_picker/files/site/clock-model.js";

test("the time is 12-hour with no AM or PM and no leading zero on the hour", () => {
  assert.equal(clockText(new Date(2026, 10, 24, 0, 5)).time, "12:05");
  assert.equal(clockText(new Date(2026, 10, 24, 7, 9)).time, "7:09");
  assert.equal(clockText(new Date(2026, 10, 24, 12, 0)).time, "12:00");
  assert.equal(clockText(new Date(2026, 10, 24, 13, 30)).time, "1:30");
  assert.equal(clockText(new Date(2026, 10, 24, 23, 59)).time, "11:59");
});

test("the date reads weekday, month and day in English whatever the locale", () => {
  assert.equal(clockText(new Date(2026, 10, 24, 9, 0)).date, "Tuesday, November 24");
  assert.equal(clockText(new Date(2026, 0, 1, 9, 0)).date, "Thursday, January 1");
});

test("the next redraw is at the next whole minute", () => {
  assert.equal(msToNextMinute(new Date(2026, 10, 24, 9, 0, 0, 0)), 60000);
  assert.equal(msToNextMinute(new Date(2026, 10, 24, 9, 0, 59, 500)), 500);
  assert.equal(msToNextMinute(new Date(2026, 10, 24, 9, 0, 30, 0)), 30000);
});

test("it moves every few minutes, not every minute", () => {
  assert.equal(MOVE_EVERY_MIN >= 2 && MOVE_EVERY_MIN <= 6, true);
  assert.equal(moveDue(0), false);
  assert.equal(moveDue(MOVE_EVERY_MIN - 1), false);
  assert.equal(moveDue(MOVE_EVERY_MIN), true);
  assert.equal(moveDue(MOVE_EVERY_MIN + 3), true);
});

test("a new spot is a fraction pair inside the free area and away from the last one", () => {
  const seq = [0.1, 0.1, 0.12, 0.1, 0.9, 0.8];
  let i = 0;
  const rand = () => seq[i++ % seq.length];
  const spot = pickSpot(rand, { x: 0.1, y: 0.1 });
  assert.deepEqual(spot, { x: 0.9, y: 0.8 });
});

test("pickSpot always answers, even when every try lands near the last spot", () => {
  const spot = pickSpot(() => 0.5, { x: 0.5, y: 0.5 });
  assert.equal(spot.x >= 0 && spot.x <= 1 && spot.y >= 0 && spot.y <= 1, true);
});

test("with no previous spot the first draw is used", () => {
  assert.deepEqual(pickSpot(() => 0.25, null), { x: 0.25, y: 0.25 });
});
