import { test } from "node:test";
import assert from "node:assert/strict";
import { AURORA_MAX_FPS, createAurora } from "../../roles/room_picker/files/site/aurora.js";

function fakeCtx() {
  const ctx = {
    gradients: [],
    fills: 0,
    draws: 0,
    fillStyle: null,
    globalAlpha: 1,
    globalCompositeOperation: "source-over",
    createLinearGradient() {
      const g = { stops: [], addColorStop(p, c) { g.stops.push([p, c]); } };
      ctx.gradients.push(g);
      return g;
    },
    createRadialGradient() {
      return { addColorStop() {} };
    },
    transforms: [],
    setTransform(...a) { ctx.transforms.push(a.join(",")); },
    fillRect() { ctx.fills += 1; },
  };
  return ctx;
}

function setup(over = {}) {
  const ctx = fakeCtx();
  const canvas = { width: 0, height: 0, getContext: () => ctx };
  const queue = [];
  const cancelled = [];
  let nextId = 1;
  const clock = { now: 1000 };
  const aurora = createAurora({
    canvas,
    getSize: () => ({ width: 800, height: 480 }),
    raf: (fn) => { const id = nextId++; queue.push({ id, fn }); return id; },
    caf: (id) => { cancelled.push(id); const i = queue.findIndex((q) => q.id === id); if (i >= 0) queue.splice(i, 1); },
    nowMs: () => clock.now,
    ...over,
  });
  return { aurora, ctx, canvas, queue, cancelled, clock };
}

const spread = (rgba) => {
  const [r, g, b] = rgba.match(/\d+/g).slice(0, 3).map(Number);
  return Math.max(r, g, b) - Math.min(r, g, b);
};
const firstStop = (ctx) => ctx.gradients[0].stops[2][1];

test("canvas is 200 wide with the window aspect", () => {
  const { canvas } = setup();
  assert.equal(canvas.width, 200);
  assert.equal(canvas.height, 120);
});

test("the cap is 60 fps: 60 Hz frames all draw, faster ones are thinned", () => {
  assert.equal(AURORA_MAX_FPS, 60);
  const { aurora, ctx } = setup();
  assert.equal(aurora.frame(1000), true);
  const after = ctx.fills;
  assert.equal(aurora.frame(1008), false);
  assert.equal(ctx.fills, after);
  assert.equal(aurora.frame(1016.7), true);
  assert.ok(ctx.fills > after);
});

test("motion is time based: 30 Hz and 60 Hz frames land on the same picture", () => {
  const nums = (s) => s.split(",").map(Number);
  const last = (stepMs) => {
    const { aurora, ctx, clock } = setup();
    aurora.frame(1000);
    for (let t = 1000 + stepMs; t <= 3000; t += stepMs) {
      clock.now = t;
      aurora.frame(t);
    }
    ctx.transforms.length = 0;
    clock.now = 3000 + stepMs;
    aurora.frame(3000 + stepMs);
    return ctx.transforms;
  };
  const a = last(16);
  const b = last(32);
  assert.equal(a.length, b.length);
  for (let i = 0; i < a.length; i += 1) {
    const [x, y] = [nums(a[i]), nums(b[i])];
    for (let k = 0; k < x.length; k += 1) assert.ok(Math.abs(x[k] - y[k]) < 0.05, `${a[i]} vs ${b[i]}`);
  }
});

test("start queues one rAF, twice still one, stop cancels", () => {
  const { aurora, queue, cancelled } = setup();
  aurora.start();
  aurora.start();
  assert.equal(queue.length, 1);
  assert.equal(aurora.running(), true);
  aurora.stop();
  assert.equal(aurora.running(), false);
  assert.equal(cancelled.length, 1);
  assert.equal(queue.length, 0);
  aurora.start();
  assert.equal(queue.length, 1);
});

test("a running frame re-queues itself and a stopped one does not", () => {
  const { aurora, queue } = setup();
  aurora.start();
  queue.shift().fn(2000);
  assert.equal(queue.length, 1);
  aurora.stop();
  assert.equal(queue.length, 0);
});

test("setHues crossfades over 3 s and lands on the target", () => {
  const { aurora, ctx, clock } = setup();
  aurora.frame(1000);
  const start = spread(firstStop(ctx));
  ctx.gradients.length = 0;
  aurora.setHues({ h1: 255, h2: 285, chroma: 1 });
  const seen = [start];
  for (const dt of [1500, 3000, 4000]) {
    ctx.gradients.length = 0;
    clock.now = 1000 + dt;
    aurora.frame(clock.now);
    seen.push(spread(firstStop(ctx)));
  }
  // The default hues already give a visible colour (chroma floor), so the early steps
  // can clip to the same channel spread; the target is clearly richer.
  assert.ok(seen[1] >= seen[0]);
  assert.ok(seen[2] > seen[1]);
  assert.equal(seen[2], seen[3]);
});

test("a long gap between frames advances time by at most 0.1 s", () => {
  const a = setup();
  const b = setup();
  a.aurora.frame(1000);
  b.aurora.frame(1000);
  a.ctx.transforms.length = 0;
  b.ctx.transforms.length = 0;
  a.aurora.frame(6000);
  b.aurora.frame(1100);
  assert.ok(a.ctx.transforms.length > 0);
  assert.deepEqual(a.ctx.transforms, b.ctx.transforms);
});
