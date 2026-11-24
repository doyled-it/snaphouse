import { test } from "node:test";
import assert from "node:assert/strict";
import { createCanvasStyle } from "../../roles/room_picker/files/site/canvas-style.js";
import { createFlowStyle } from "../../roles/room_picker/files/site/flow-style.js";
import { createContoursStyle } from "../../roles/room_picker/files/site/contours-style.js";
import { createLifeStyle } from "../../roles/room_picker/files/site/life-style.js";
import { createHarmonographStyle } from "../../roles/room_picker/files/site/harmonograph-style.js";

function fakeCtx() {
  const calls = [];
  const store = {};
  return new Proxy(store, {
    get(t, k) {
      if (k === "calls") return calls;
      if (k in t) return t[k];
      return (...args) => {
        calls.push(k);
        return { addColorStop() {} };
      };
    },
    set(t, k, v) {
      t[k] = v;
      return true;
    },
  });
}

function fakeEnv() {
  const ctx = fakeCtx();
  const canvas = { width: 0, height: 0, getContext: () => ctx };
  const state = { raf: 0, caf: 0, pending: new Map(), next: 1, now: 0, timers: 0 };
  const win = {
    innerWidth: 800,
    innerHeight: 480,
    performance: { now: () => state.now },
    requestAnimationFrame(fn) {
      state.raf += 1;
      const id = state.next++;
      state.pending.set(id, fn);
      return id;
    },
    cancelAnimationFrame(id) {
      state.caf += 1;
      state.pending.delete(id);
    },
  };
  const env = {
    doc: { createElement: () => canvas },
    win,
    now: () => 0,
    setTimeout() { state.timers += 1; return 1; },
    clearTimeout() {},
    random: () => 0.4,
  };
  // run the next frame at time `at` ms
  const tick = (at) => {
    state.now = at;
    const [id, fn] = [...state.pending][0] || [];
    if (!fn) return false;
    state.pending.delete(id);
    fn(at);
    return true;
  };
  return { env, state, canvas, ctx, tick };
}

const spec = (record) => ({
  width: 40,
  height: 24,
  fps: (night) => (night > 0.5 ? 10 : 30),
  make: () => ({ frame: (dt, view) => record.push({ dt, view: { ...view } }) }),
});

test("the canvas is sized, and nothing runs or draws before start", () => {
  const { env, canvas, state } = fakeEnv();
  const rec = [];
  const s = createCanvasStyle(env, spec(rec));
  assert.equal(s.node, canvas);
  assert.equal(canvas.width, 40);
  assert.equal(canvas.height, 24);
  assert.equal(state.raf, 0);
  assert.equal(rec.length, 0);
});

test("start runs one rAF loop capped at the style's fps; stop ends it completely", () => {
  const { env, state, tick } = fakeEnv();
  const rec = [];
  const s = createCanvasStyle(env, spec(rec));
  s.start();
  s.start();
  assert.equal(state.pending.size, 1);
  tick(0);
  tick(16.7);
  tick(33.4);
  assert.equal(rec.length, 2, "60 Hz ticks are thinned to 30 fps");
  s.stop();
  assert.equal(state.pending.size, 0);
  const rafs = state.raf;
  assert.equal(tick(50), false);
  assert.equal(state.raf, rafs);
  assert.equal(state.timers, 0);
});

test("dt is the real gap since the last drawn frame, capped at 0.25 s", () => {
  const { env, tick } = fakeEnv();
  const rec = [];
  const s = createCanvasStyle(env, spec(rec));
  s.start();
  tick(0);
  tick(40);
  assert.ok(Math.abs(rec[1].dt - 0.04) < 1e-9);
  tick(2000);
  assert.equal(rec[2].dt, 0.25);
});

test("restarting after a stop carries on without a huge dt", () => {
  const { env, tick } = fakeEnv();
  const rec = [];
  const s = createCanvasStyle(env, spec(rec));
  s.start();
  tick(0);
  s.stop();
  s.start();
  tick(60000);
  assert.equal(rec[1].dt, 0);
});

test("setNight before the first frame snaps; while running it eases over about 3 s", () => {
  const a = fakeEnv();
  const rec1 = [];
  const early = createCanvasStyle(a.env, spec(rec1));
  early.setNight(true);
  early.start();
  a.tick(0);
  assert.equal(rec1[0].view.night, 1);

  const b = fakeEnv();
  const rec2 = [];
  const live = createCanvasStyle(b.env, spec(rec2));
  live.start();
  b.tick(0);
  live.setNight(true);
  b.tick(1000);
  const mid = rec2[rec2.length - 1].view.night;
  assert.ok(mid > 0 && mid < 1, `eased: ${mid}`);
  for (let at = 1034; at < 4300; at += 34) b.tick(at);
  assert.equal(rec2[rec2.length - 1].view.night, 1);
  live.setNight(false);
  for (let at = 4334; at < 8000; at += 34) b.tick(at);
  assert.equal(rec2[rec2.length - 1].view.night, 0);
});

test("the fps cap follows night", () => {
  const { env, tick } = fakeEnv();
  const rec = [];
  const s = createCanvasStyle(env, spec(rec));
  s.setNight(true);
  s.start();
  tick(0);
  tick(40);
  tick(80);
  assert.equal(rec.length, 1, "10 fps at night: 40 ms apart is too soon");
  tick(105);
  assert.equal(rec.length, 2);
});

test("hues crossfade and bump the version each frame while fading", () => {
  const { env, tick } = fakeEnv();
  const rec = [];
  const s = createCanvasStyle(env, spec(rec));
  s.start();
  tick(0);
  const v0 = rec[0].view.hueVersion;
  s.setHues({ h1: 100, h2: 160, chroma: 1 });
  tick(1000);
  const mid = rec[rec.length - 1].view;
  assert.ok(mid.hues.h1 < 255 && mid.hues.h1 > 100);
  assert.ok(mid.hueVersion > v0);
  tick(5000);
  tick(5100);
  const end = rec[rec.length - 1].view;
  assert.equal(end.hues.h1, 100);
  const settled = end.hueVersion;
  tick(5200);
  assert.equal(rec[rec.length - 1].view.hueVersion, settled);
});

for (const [name, factory, size] of [
  ["flow", createFlowStyle, [320, 192]],
  ["contours", createContoursStyle, [400, 240]],
  ["life", createLifeStyle, [320, 192]],
  ["harmonograph", createHarmonographStyle, [400, 240]],
]) {
  test(`${name}: builds the interface, runs, draws and stops cleanly`, () => {
    const { env, canvas, ctx, state, tick } = fakeEnv();
    const s = factory(env);
    for (const fn of ["start", "stop", "setHues", "setNight"]) assert.equal(typeof s[fn], "function");
    assert.deepEqual([canvas.width, canvas.height], size);
    s.setHues({ h1: 200, h2: 260, chroma: 0.6 });
    s.start();
    for (let t = 0; t < 40; t += 1) tick(t * 34);
    assert.ok(ctx.calls.length > 0, "something was drawn");
    s.setNight(true);
    for (let t = 40; t < 200; t += 1) tick(t * 34);
    s.stop();
    const rafs = state.raf;
    assert.equal(tick(99999), false);
    assert.equal(state.raf, rafs);
    assert.equal(state.timers, 0);
  });
}

test("after a stop, setNight snaps again until the next frame has run", () => {
  const { env, tick } = fakeEnv();
  const rec = [];
  const s = createCanvasStyle(env, spec(rec));
  s.start();
  tick(0);
  s.stop();
  s.setNight(true);
  s.start();
  tick(100);
  assert.equal(rec.at(-1).view.night, 1, "snapped, not eased from 0");
});
