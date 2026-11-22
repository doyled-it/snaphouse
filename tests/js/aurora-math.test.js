import { test } from "node:test";
import assert from "node:assert/strict";
import {
  RIBBONS,
  oklchToRgb,
  shortHueDiff,
  smoothstep,
  mixHues,
  tintColor,
  ribbonColumn,
  ribbonStops,
} from "../../roles/room_picker/files/site/aurora-math.js";

test("oklchToRgb gives equal channels at zero chroma", () => {
  const [r, g, b] = oklchToRgb(0.68, 0, 255);
  assert.equal(r, g);
  assert.equal(g, b);
});

test("shortHueDiff takes the short way round", () => {
  assert.equal(shortHueDiff(350, 10), 20);
  assert.equal(shortHueDiff(10, 350), -20);
});

test("smoothstep clamps and eases", () => {
  assert.equal(smoothstep(-1), 0);
  assert.equal(smoothstep(2), 1);
  assert.equal(smoothstep(0.5), 0.5);
});

test("mixHues crosses 0 the short way and mixes chroma", () => {
  const m = mixHues({ h1: 350, h2: 0, chroma: 0 }, { h1: 10, h2: 20, chroma: 1 }, 0.5);
  assert.ok(Math.abs(shortHueDiff(m.h1, 0)) < 1e-9);
  assert.equal(m.chroma, 0.5);
});

test("mixHues falls back to defaults for non-finite input", () => {
  const m = mixHues({ h1: NaN, h2: undefined, chroma: NaN }, { h1: 10, h2: 20, chroma: 1 }, 0.3);
  for (const v of Object.values(m)) assert.ok(Number.isFinite(v));
});

test("tintColor is finite and not black at chroma 0", () => {
  const c = tintColor({ h1: 255, h2: 285, chroma: 0 }, 0.68, 0);
  assert.equal(c.length, 3);
  assert.ok(c.every(Number.isFinite));
  assert.ok(c.some((v) => v > 0));
});

test("tintColor with chroma 0 is fainter than with chroma 1", () => {
  const spread = (c) => Math.max(...c) - Math.min(...c);
  const faint = tintColor({ h1: 255, h2: 285, chroma: 0 }, 0.68, 0);
  const full = tintColor({ h1: 255, h2: 285, chroma: 1 }, 0.68, 0);
  assert.ok(spread(faint) < spread(full));
});

test("ribbonColumn is deterministic and bounded", () => {
  const H = 120;
  const a = ribbonColumn(RIBBONS[0], 40, 12.5, H);
  assert.deepEqual(a, ribbonColumn(RIBBONS[0], 40, 12.5, H));
  assert.ok(a.alpha >= 0.12 * 0.85 - 1e-9 && a.alpha <= 0.85 + 1e-9);
  assert.ok(a.y >= H * 0.4 - H * 0.14 - 1e-9 && a.y <= H * 0.4 + H * 0.14 + 1e-9);
});

test("ribbonStops pins the four gradient stops", () => {
  const s = ribbonStops([1, 2, 3]);
  assert.deepEqual(s.map((x) => x[0]), [0, 0.55, 0.85, 1]);
  assert.deepEqual(s.map((x) => x[1]), [
    "rgba(1,2,3,0)",
    "rgba(1,2,3,0.22)",
    "rgba(1,2,3,0.7)",
    "rgba(1,2,3,0)",
  ]);
});
