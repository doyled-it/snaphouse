import { test } from "node:test";
import assert from "node:assert/strict";
import { pointerValue, keyStepFor } from "../../roles/room_picker/files/site/slider-model.js";

const rect = { left: 100, top: 50, width: 200, height: 300 };

test("horizontal: value follows x across the track", () => {
  assert.equal(pointerValue("horizontal", { x: 100, y: 0 }, rect), 0);
  assert.equal(pointerValue("horizontal", { x: 200, y: 999 }, rect), 50);
  assert.equal(pointerValue("horizontal", { x: 300, y: 0 }, rect), 100);
});

test("vertical: the top is 100 and the bottom is 0, from y only", () => {
  assert.equal(pointerValue("vertical", { x: 999, y: 350 }, rect), 0);
  assert.equal(pointerValue("vertical", { x: 0, y: 200 }, rect), 50);
  assert.equal(pointerValue("vertical", { x: 0, y: 50 }, rect), 100);
});

test("values clamp to 0..100 and round to whole numbers", () => {
  assert.equal(pointerValue("vertical", { x: 0, y: -500 }, rect), 100);
  assert.equal(pointerValue("vertical", { x: 0, y: 900 }, rect), 0);
  assert.equal(pointerValue("horizontal", { x: 0, y: 0 }, rect), 0);
  assert.equal(pointerValue("vertical", { x: 0, y: 150 }, rect), 67);
});

test("a zero-size track reads 0", () => {
  assert.equal(pointerValue("vertical", { x: 0, y: 0 }, { left: 0, top: 0, width: 0, height: 0 }), 0);
});

test("keys step the same in both orientations; unknown keys do nothing", () => {
  for (const o of ["horizontal", "vertical"]) {
    assert.equal(keyStepFor("ArrowUp", o), 2);
    assert.equal(keyStepFor("ArrowDown", o), -2);
    assert.equal(keyStepFor("PageUp", o), 10);
    assert.equal(keyStepFor("PageDown", o), -10);
    assert.equal(keyStepFor("x", o), null);
  }
});
