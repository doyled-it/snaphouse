import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CONTOURS,
  fillField,
  levelView,
  marchLevel,
} from "../../roles/room_picker/files/site/contours-model.js";

// A 2 by 2 grid is one cell: values are [TL, TR, BL, BR] in row order.
const cell = (tl, tr, bl, br) => [tl, tr, bl, br];
const march = (v, level = 0.5, size = 10) => {
  const out = [];
  const n = marchLevel(Float32Array.from(v), 2, 2, level, size, out);
  return { n, out };
};
const near = (a, b) => Math.abs(a - b) < 1e-6;

test("an all-below or all-above cell has no segment", () => {
  assert.equal(march(cell(0, 0, 0, 0)).n, 0);
  assert.equal(march(cell(1, 1, 1, 1)).n, 0);
});

test("one high corner cuts the corner: top-left joins the top and left edges", () => {
  const { n, out } = march(cell(1, 0, 0, 0));
  assert.equal(n, 1);
  // left edge point first (x 0), top edge point second (y 0), halfway along each
  const pts = [[out[0], out[1]], [out[2], out[3]]].sort((a, b) => a[0] - b[0]);
  assert.ok(near(pts[0][0], 0) && near(pts[0][1], 5));
  assert.ok(near(pts[1][0], 5) && near(pts[1][1], 0));
});

test("interpolation puts the point where the level crosses", () => {
  const { out } = march(cell(0.9, 0.3, 0, 0), 0.5);
  const xs = [out[0], out[2]].sort((a, b) => a - b);
  // top edge crosses at (0.5 - 0.9) / (0.3 - 0.9) = 0.667 of the cell
  assert.ok(near(xs[1], 10 * ((0.5 - 0.9) / (0.3 - 0.9))));
});

test("two high corners on one side make one straight segment", () => {
  const { n, out } = march(cell(1, 1, 0, 0));
  assert.equal(n, 1);
  assert.ok(near(out[1], 5) && near(out[3], 5));
});

test("saddle: the centre decides whether the high corners join", () => {
  // TL and BR high, TR and BL low: a low centre keeps them apart (two corner cuts)
  const apart = march(cell(1, 0, 0, 1), 0.5);
  assert.equal(apart.n, 2);
  // a high centre needs the high corners joined: the two segments run between the low corners
  const joinedValues = cell(1, 0.45, 0.45, 1);
  const joined = march(joinedValues, 0.5);
  assert.equal(joined.n, 2);
  const lowCenter = march(cell(0.6, 0.1, 0.1, 0.6), 0.5);
  assert.equal(lowCenter.n, 2);
  // low centre: TL cut touches the top and left edges only
  const [a, b] = [lowCenter.out.slice(0, 4), lowCenter.out.slice(4, 8)];
  const touchesTopLeft = (s) => (s[0] < 5 && s[1] === 0 && s[2] === 0 && s[3] < 5) || (s[2] < 5 && s[3] === 0 && s[0] === 0 && s[1] < 5);
  assert.ok(touchesTopLeft(a) || touchesTopLeft(b));
});

test("a saddle with a high centre joins the high corners through the middle", () => {
  // TL 0.9, BR 0.9, TR 0.4, BL 0.4: centre mean 0.65 is above 0.5
  const { n, out } = march(cell(0.9, 0.4, 0.4, 0.9), 0.5);
  assert.equal(n, 2);
  // each segment cuts off one LOW corner: it spans the top and right edges (TR) or the left and bottom edges (BL)
  const segs = [out.slice(0, 4), out.slice(4, 8)];
  const hasTopRight = segs.some((s) => (s[1] === 0 && near(s[2], 10)) || (s[3] === 0 && near(s[0], 10)));
  assert.ok(hasTopRight);
});

test("segments append to the output array and the count matches", () => {
  const out = [9, 9];
  const n = marchLevel(Float32Array.from(cell(1, 0, 0, 0)), 2, 2, 0.5, 10, out);
  assert.equal(n, 1);
  assert.equal(out.length, 2 + 4);
});

test("fillField is deterministic and in 0..1, and moves with time", () => {
  const a = new Float32Array(CONTOURS.cols * CONTOURS.rows);
  const b = new Float32Array(a.length);
  fillField(a, CONTOURS.cols, CONTOURS.rows, 3);
  fillField(b, CONTOURS.cols, CONTOURS.rows, 3);
  assert.deepEqual(a, b);
  for (const v of a) assert.ok(v >= 0 && v <= 1);
  fillField(b, CONTOURS.cols, CONTOURS.rows, 30);
  assert.notDeepEqual(a, b);
});

test("the grid spans the canvas exactly", () => {
  assert.equal((CONTOURS.cols - 1) * CONTOURS.cell, CONTOURS.width);
  assert.equal((CONTOURS.rows - 1) * CONTOURS.cell, CONTOURS.height);
});

test("every fourth line is heavier", () => {
  const heavy = CONTOURS.levels.map((_, i) => levelView(i, 0).heavy);
  assert.deepEqual(heavy, [true, false, false, false, true, false, false, false]);
});

test("night keeps every other line and fades the rest out", () => {
  for (let i = 0; i < CONTOURS.levels.length; i += 1) {
    assert.equal(levelView(i, 0).weight, 1);
    assert.equal(levelView(i, 1).weight, i % 2 === 0 ? 1 : 0);
  }
  assert.equal(levelView(1, 0.5).weight, 0.5);
});
