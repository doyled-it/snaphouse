import { test } from "node:test";
import assert from "node:assert/strict";

import {
  PANEL_COLORS,
  PREVIEW_FULL,
  PREVIEW_DRAG,
  ditherToPanel,
  previewSize,
  sourceRect,
} from "../../roles/room_picker/files/site/crop-preview.js";

// A tiny generated picture: a horizontal grey ramp with a red tint at the right.
function ramp(w, h) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y += 1) {
    for (let x = 0; x < w; x += 1) {
      const i = (y * w + x) * 4;
      const v = Math.round((x / (w - 1)) * 255);
      data[i] = Math.min(255, v + (x > w / 2 ? 60 : 0));
      data[i + 1] = v;
      data[i + 2] = v;
      data[i + 3] = 255;
    }
  }
  return data;
}

const key = (r, g, b) => `${r},${g},${b}`;
const allowed = new Set(PANEL_COLORS.map(([r, g, b]) => key(r, g, b)));

test("the panel has six colors, the muted ones the frame's palette note lists", () => {
  assert.equal(PANEL_COLORS.length, 6);
  assert.deepEqual(PANEL_COLORS[0], [43, 41, 42]); // black
  assert.deepEqual(PANEL_COLORS[1], [233, 231, 224]); // white
});

test("every output pixel is one of the six panel colors, fully opaque", () => {
  const w = 24;
  const h = 14;
  const out = ditherToPanel(ramp(w, h), w, h);
  assert.equal(out.length, w * h * 4);
  for (let i = 0; i < out.length; i += 4) {
    assert.ok(allowed.has(key(out[i], out[i + 1], out[i + 2])), `pixel ${i / 4}`);
    assert.equal(out[i + 3], 255);
  }
});

test("the dither is deterministic and leaves its input alone", () => {
  const w = 24;
  const h = 14;
  const src = ramp(w, h);
  const copy = src.slice();
  const a = ditherToPanel(src, w, h);
  const b = ditherToPanel(src, w, h);
  assert.deepEqual(a, b);
  assert.deepEqual(src, copy);
});

test("a flat white area stays white and a flat black area stays black", () => {
  const w = 8;
  const h = 6;
  const flat = (v) => new Uint8ClampedArray(w * h * 4).fill(v);
  const white = ditherToPanel(flat(255), w, h);
  const black = ditherToPanel(flat(0), w, h);
  for (let i = 0; i < white.length; i += 4) {
    assert.equal(key(white[i], white[i + 1], white[i + 2]), key(...PANEL_COLORS[1]));
    assert.equal(key(black[i], black[i + 1], black[i + 2]), key(...PANEL_COLORS[0]));
  }
});

test("a mid grey mixes light and dark pixels (it is dithered, not snapped)", () => {
  const w = 16;
  const h = 16;
  const grey = new Uint8ClampedArray(w * h * 4).fill(138);
  for (let i = 3; i < grey.length; i += 4) grey[i] = 255;
  const out = ditherToPanel(grey, w, h);
  const seen = new Set();
  for (let i = 0; i < out.length; i += 4) seen.add(key(out[i], out[i + 1], out[i + 2]));
  assert.ok(seen.size >= 2);
});

test("previewSize is the real 800x480 at rest and a small one while dragging", () => {
  assert.deepEqual(previewSize(false), PREVIEW_FULL);
  assert.deepEqual(previewSize(true), PREVIEW_DRAG);
  assert.deepEqual(PREVIEW_FULL, { w: 800, h: 480 });
  assert.equal(PREVIEW_DRAG.w / PREVIEW_DRAG.h, 800 / 480);
  assert.ok(PREVIEW_DRAG.w < PREVIEW_FULL.w);
});

test("sourceRect is the box in picture pixels", () => {
  assert.deepEqual(sourceRect({ x: 0.25, y: 0.5, w: 0.5, h: 0.3 }, 4000, 3000), {
    sx: 1000,
    sy: 1500,
    sw: 2000,
    sh: 900,
  });
});
