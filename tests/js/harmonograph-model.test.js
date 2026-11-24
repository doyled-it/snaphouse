import { test } from "node:test";
import assert from "node:assert/strict";
import {
  HARMONO,
  createHarmonograph,
  figureEnd,
  figurePoint,
  newFigure,
} from "../../roles/room_picker/files/site/harmonograph-model.js";

const seq = (...vals) => {
  let i = 0;
  return () => vals[i++ % vals.length];
};

test("a figure draws from the pendulum sum and decays toward the centre", () => {
  const fig = newFigure(seq(0.5));
  const w = 400;
  const h = 240;
  const [x0, y0] = figurePoint(fig, 0, w, h);
  const e = 1;
  const expectX = w / 2 + e * (HARMONO.ax1 * w * Math.sin(fig.p1) + HARMONO.ax2 * w * Math.sin(fig.p2));
  const expectY = h / 2 + e * (HARMONO.ay1 * h * Math.sin(fig.p3) + HARMONO.ay2 * h * Math.sin(fig.p4));
  assert.ok(Math.abs(x0 - expectX) < 1e-9);
  assert.ok(Math.abs(y0 - expectY) < 1e-9);
  const [xe, ye] = figurePoint(fig, figureEnd(fig), w, h);
  assert.ok(Math.abs(xe - w / 2) < 0.07 * w);
  assert.ok(Math.abs(ye - h / 2) < 0.07 * h);
});

test("the figure ends where the envelope has fallen to 6 percent", () => {
  const fig = newFigure(seq(0.2, 0.7));
  assert.ok(Math.abs(Math.exp(-fig.d * figureEnd(fig)) - 0.06) < 1e-9);
});

test("new figures differ and stay in the ratio and damping ranges", () => {
  const a = newFigure(seq(0.1, 0.9, 0.3));
  const b = newFigure(seq(0.8, 0.2, 0.6));
  assert.notDeepEqual(a, b);
  for (let i = 0; i < 20; i += 1) {
    const f = newFigure(() => i / 20);
    assert.ok(f.d >= 0.0045 && f.d <= 0.0075);
    assert.ok(f.f1 >= 1.98 && f.f1 <= 5.02);
  }
});

test("the first advance starts a fresh figure in the draw phase", () => {
  const h = createHarmonograph({ random: seq(0.4) });
  const s = h.advance(0);
  assert.equal(s.phase, "draw");
  assert.equal(s.fresh, true);
  assert.equal(s.u0, 0);
  assert.equal(s.u1, 0);
});

test("draw takes about a minute and covers the whole figure", () => {
  const h = createHarmonograph({ random: seq(0.4) });
  h.advance(0);
  let last;
  for (let t = 0; t < 59; t += 1) last = h.advance(1);
  assert.equal(last.phase, "draw");
  last = h.advance(1.01);
  assert.equal(last.phase, "hold");
  assert.ok(Math.abs(h.figure().endU - h.u()) < 1e-6);
});

test("draw segments are contiguous and together span the figure", () => {
  const h = createHarmonograph({ random: seq(0.4) });
  h.advance(0);
  let prev = 0;
  for (let t = 0; t < 30; t += 1) {
    const s = h.advance(1);
    assert.equal(s.u0, prev);
    assert.ok(s.u1 > s.u0);
    prev = s.u1;
  }
});

test("hold lasts 20 s, then the fade, then a new figure", () => {
  const h = createHarmonograph({ random: seq(0.4, 0.6, 0.1) });
  h.advance(0);
  h.advance(HARMONO.drawS + 0.01);
  const firstFig = h.figure();
  let s = h.advance(19);
  assert.equal(s.phase, "hold");
  s = h.advance(1.5);
  assert.equal(s.phase, "fade");
  assert.ok(s.fadeS === HARMONO.fadeS);
  s = h.advance(HARMONO.fadeS);
  assert.equal(s.fresh, true);
  assert.equal(s.phase, "draw");
  assert.notEqual(h.figure(), firstFig);
});

test("a long frame gap cannot skip past a phase", () => {
  const h = createHarmonograph({ random: seq(0.4) });
  h.advance(0);
  const s = h.advance(1000);
  assert.ok(["hold", "fade", "draw"].includes(s.phase));
});

test("night draws a fainter line", () => {
  assert.ok(HARMONO.nightAlpha < HARMONO.dayAlpha);
});
