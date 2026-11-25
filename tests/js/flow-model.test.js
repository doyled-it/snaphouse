import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FLOW,
  activeCount,
  createFlow,
  flowAngle,
  stepFlow,
} from "../../roles/room_picker/files/site/flow-model.js";

const seq = (...vals) => {
  let i = 0;
  return () => vals[i++ % vals.length];
};

test("the flow angle is deterministic and within 0..4 pi", () => {
  for (let i = 0; i < 200; i += 1) {
    const a = flowAngle(i * 1.7, i * 0.9, i * 0.1);
    assert.ok(a >= 0 && a <= Math.PI * 4);
    assert.equal(a, flowAngle(i * 1.7, i * 0.9, i * 0.1));
  }
});

test("particles start inside the canvas, with a color bin each", () => {
  const f = createFlow({ count: 50, width: 320, height: 192, random: seq(0.25, 0.5) });
  assert.equal(f.x.length, 50);
  for (let i = 0; i < 50; i += 1) {
    assert.ok(f.x[i] >= 0 && f.x[i] <= 320);
    assert.ok(f.y[i] >= 0 && f.y[i] <= 192);
    assert.equal(f.bin[i], i % FLOW.bins);
    assert.equal(f.px[i], f.x[i]);
  }
});

test("a step moves a particle speed * dt along the field angle and remembers where it was", () => {
  const f = createFlow({ count: 1, width: 320, height: 192, random: () => 0.5 });
  const [x0, y0] = [f.x[0], f.y[0]];
  const a = flowAngle(x0, y0, 5);
  stepFlow(f, 5, 0.5, 1, () => 0.99, FLOW);
  assert.ok(Math.abs(f.x[0] - (x0 + Math.cos(a) * FLOW.speed * 0.5)) < 1e-3);
  assert.ok(Math.abs(f.y[0] - (y0 + Math.sin(a) * FLOW.speed * 0.5)) < 1e-3);
  assert.equal(f.px[0], x0);
  assert.equal(f.py[0], y0);
});

test("a particle that leaves the canvas respawns inside it with no trail segment", () => {
  const f = createFlow({ count: 1, width: 320, height: 192, random: () => 0.5 });
  f.x[0] = -5;
  stepFlow(f, 0, 0.033, 1, seq(0.9, 0.1, 0.2), FLOW);
  assert.ok(f.x[0] >= 0 && f.x[0] <= 320 && f.y[0] >= 0 && f.y[0] <= 192);
  assert.equal(f.px[0], f.x[0]);
  assert.equal(f.py[0], f.y[0]);
});

test("a low random draw respawns a particle that is still inside", () => {
  const f = createFlow({ count: 1, width: 320, height: 192, random: () => 0.5 });
  stepFlow(f, 0, 0.033, 1, () => 0, FLOW);
  assert.equal(f.px[0], f.x[0]);
});

test("only the active particles move", () => {
  const f = createFlow({ count: 10, width: 320, height: 192, random: seq(0.3, 0.6, 0.8) });
  const before = Array.from(f.x);
  stepFlow(f, 1, 0.033, 4, () => 0.99, FLOW);
  for (let i = 4; i < 10; i += 1) assert.equal(f.x[i], before[i]);
  assert.notEqual(f.x[0], before[0]);
});

test("night has fewer threads, and the count eases between", () => {
  assert.equal(activeCount(0), FLOW.dayCount);
  assert.equal(activeCount(1), FLOW.nightCount);
  assert.ok(FLOW.nightCount < FLOW.dayCount);
  const mid = activeCount(0.5);
  assert.ok(mid < FLOW.dayCount && mid > FLOW.nightCount);
});

import { trailFade } from "../../roles/room_picker/files/site/flow-model.js";

test("trailFade matches the 30 fps look at 1/30 s and is dt based", () => {
  assert.ok(Math.abs(trailFade(0, 1 / 30) - 0.07) < 1e-9);
  assert.ok(Math.abs(trailFade(1, 1 / 30) - 0.12) < 1e-9);
});

test("trailFade at 60 fps twice over leaves the same as one 30 fps frame", () => {
  for (const night of [0, 1]) {
    const keep60 = (1 - trailFade(night, 1 / 60)) ** 2;
    const keep30 = 1 - trailFade(night, 1 / 30);
    assert.ok(Math.abs(keep60 - keep30) < 1e-9);
  }
});

test("trailFade is 0 for no time and below 1 for a long gap", () => {
  assert.equal(trailFade(0, 0), 0);
  assert.ok(trailFade(0, 0.25) < 1);
});

import { trailAlpha, trailLightness } from "../../roles/room_picker/files/site/flow-model.js";

test("trailAlpha is brighter by day than the old 0.65 and stays dim at night", () => {
  assert.equal(trailAlpha(0), 0.9);
  assert.ok(Math.abs(trailAlpha(1) - 0.32) < 1e-9);
  assert.ok(trailAlpha(0.5) < trailAlpha(0) && trailAlpha(0.5) > trailAlpha(1));
});

test("trailLightness is lighter by day than the old 0.78 and unchanged at night", () => {
  assert.equal(trailLightness(0), 0.84);
  assert.equal(trailLightness(1), 0.5);
  assert.ok(trailLightness(0.5) < trailLightness(0) && trailLightness(0.5) > trailLightness(1));
});
