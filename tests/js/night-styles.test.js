import { test } from "node:test";
import assert from "node:assert/strict";
import { createSkyStyle } from "../../roles/room_picker/files/site/sky-style.js";
import { createPlanetsStyle } from "../../roles/room_picker/files/site/planets-style.js";
import { createMoonStyle } from "../../roles/room_picker/files/site/moon-style.js";
import { PLANETS } from "../../roles/room_picker/files/site/planets-model.js";
import { textLines, MOON } from "../../roles/room_picker/files/site/moon-model.js";

// A 2d context that records every call with its arguments.
function recordingCtx() {
  const calls = [];
  const store = { calls };
  return new Proxy(store, {
    get(t, k) {
      if (k in t) return t[k];
      return (...args) => {
        calls.push([k, ...args]);
        if (k === "measureText") return { width: 60 };
        if (k === "getImageData") return { data: new Uint8ClampedArray(args[2] * args[3] * 4).fill(200), width: args[2], height: args[3] };
        if (k === "createImageData") return { data: new Uint8ClampedArray(args[0] * args[1] * 4), width: args[0], height: args[1] };
        return { addColorStop() {} };
      };
    },
    set(t, k, v) {
      t[k] = v;
      return true;
    },
  });
}

function fakeEnv({ nowMs = Date.UTC(2026, 10, 24, 12) } = {}) {
  const made = [];
  const state = { raf: 0, caf: 0, pending: new Map(), next: 1, now: 0, timers: 0, clock: nowMs };
  const doc = {
    createElement(tag) {
      const ctx = recordingCtx();
      const el = { tag, width: 0, height: 0, ctx, getContext: () => ctx };
      made.push(el);
      return el;
    },
  };
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
      state.pending.delete(id);
    },
  };
  const env = {
    doc,
    win,
    now: () => state.clock,
    setTimeout() { state.timers += 1; return 1; },
    clearTimeout() {},
    random: () => 0.4,
  };
  const tick = (at) => {
    state.now = at;
    const [id, fn] = [...state.pending][0] || [];
    if (!fn) return false;
    state.pending.delete(id);
    fn(at);
    return true;
  };
  return { env, state, made, tick };
}

const callsOf = (style) => style.node.ctx.calls;
const texts = (style) => callsOf(style).filter((c) => c[0] === "fillText").map((c) => c[1]);

for (const [name, factory] of [["sky", createSkyStyle], ["planets", createPlanetsStyle], ["moon", createMoonStyle]]) {
  test(`${name}: common interface, nothing runs before start, no loop or timer after stop`, () => {
    const { env, state, tick } = fakeEnv();
    const s = factory(env);
    for (const fn of ["start", "stop", "setHues", "setNight"]) assert.equal(typeof s[fn], "function", fn);
    assert.ok(s.node);
    assert.equal(state.raf, 0);
    s.start();
    s.start();
    assert.equal(state.pending.size, 1);
    tick(0);
    tick(1000);
    s.stop();
    assert.equal(state.pending.size, 0);
    const rafs = state.raf;
    assert.equal(tick(2000), false);
    assert.equal(state.raf, rafs);
    assert.equal(state.timers, 0);
    s.setNight(true);
    s.setHues({ h1: 10, h2: 20, chroma: 0.1 });
    s.start();
    tick(3000);
    s.stop();
    assert.equal(state.pending.size, 0);
  });
}

test("sky: draws a sky and fewer stars at night; a shooting star shows within a minute", () => {
  const day = fakeEnv();
  const a = createSkyStyle(day.env);
  a.start();
  day.tick(0);
  const dayStars = callsOf(a).filter((c) => c[0] === "arc").length;
  assert.ok(dayStars > 100, `${dayStars} stars by day`);
  const night = fakeEnv();
  const b = createSkyStyle(night.env);
  b.setNight(true);
  b.start();
  night.tick(0);
  const nightStars = callsOf(b).filter((c) => c[0] === "arc").length;
  assert.ok(nightStars < dayStars * 0.7, `${nightStars} at night`);
  let stroked = false;
  for (let t = 200; t < 60000 && !stroked; t += 200) {
    day.state.clock += 200;
    day.tick(t);
    stroked = callsOf(a).some((c) => c[0] === "stroke");
  }
  assert.ok(stroked, "a shooting star was drawn");
});

test("sky: the stars turn with the clock, not the frame count", () => {
  const { env, state, tick } = fakeEnv();
  const s = createSkyStyle(env);
  s.start();
  tick(0);
  const first = callsOf(s).filter((c) => c[0] === "arc").slice(0, 20).map((c) => [c[1], c[2]]);
  callsOf(s).length = 0;
  state.clock += 3600 * 1000; // an hour later, same frame time
  tick(250);
  const later = callsOf(s).filter((c) => c[0] === "arc").slice(0, 20).map((c) => [c[1], c[2]]);
  assert.notDeepEqual(first, later);
});

test("planets: names and date by day, only the date at night", () => {
  const { env, tick } = fakeEnv();
  const s = createPlanetsStyle(env);
  s.start();
  tick(0);
  const t = texts(s);
  for (const p of PLANETS) assert.ok(t.includes(p.name), p.name);
  assert.ok(t.includes("November 24, 2026") || t.some((x) => /^\w+ \d+, \d{4}$/.test(x)));
  const night = fakeEnv();
  const n = createPlanetsStyle(night.env);
  n.setNight(true);
  n.start();
  night.tick(0);
  const nt = texts(n);
  assert.ok(PLANETS.every((p) => !nt.includes(p.name)), "names are off at night");
  assert.equal(nt.length, 1);
});

test("planets: one redraw a second, faster only while the night fade runs", () => {
  const { env, state, tick } = fakeEnv();
  const s = createPlanetsStyle(env);
  s.start();
  tick(0);
  const before = callsOf(s).length;
  tick(300);
  assert.equal(callsOf(s).length, before, "no redraw 300 ms later");
  tick(1100);
  assert.ok(callsOf(s).length > before);
  assert.equal(state.timers, 0);
});

test("moon: waits for the photo, loads moon.jpg from the page's own directory", () => {
  const { env, made, tick } = fakeEnv();
  const s = createMoonStyle(env);
  s.start();
  const img = made.find((m) => m.tag === "img");
  assert.ok(img, "an img element is made");
  assert.equal(img.src, "moon.jpg");
  tick(0);
  assert.ok(!callsOf(s).some((c) => c[0] === "drawImage"), "no moon before the photo loads");
  img.width = img.naturalWidth = 600;
  img.height = img.naturalHeight = 600;
  img.onload();
  callsOf(s).length = 0;
  tick(600);
  const draws = callsOf(s).filter((c) => c[0] === "drawImage");
  assert.equal(draws.length, 1);
  const lines = textLines(env.now());
  const t = texts(s);
  assert.deepEqual(t, [lines.name, lines.lit, lines.next]);
});

test("moon: redraws only when the picture changes (drift pixel, phase pixel, text, night)", () => {
  const { env, state, made, tick } = fakeEnv();
  const s = createMoonStyle(env);
  s.start();
  const img = made.find((m) => m.tag === "img");
  img.onload();
  tick(0);
  const count = () => callsOf(s).filter((c) => c[0] === "drawImage").length;
  assert.equal(count(), 1);
  tick(500);
  tick(1000);
  assert.equal(count(), 1, "nothing changed in a second");
  state.clock += 8 * 3600 * 1000;
  tick(1500);
  assert.equal(count(), 2, "eight hours later the drift or phase pixel moved");
  const shaded = made.filter((m) => m.tag === "canvas").some((m) => m.ctx.calls.some((c) => c[0] === "putImageData"));
  assert.ok(shaded, "the phase shading was written to the offscreen canvas");
});

function moonDraw(night) {
  const e = fakeEnv();
  const s = createMoonStyle(e.env);
  if (night) s.setNight(true);
  s.start();
  e.made.find((m) => m.tag === "img").onload();
  const ctx = s.node.ctx;
  let alpha = null;
  const push = ctx.calls.push.bind(ctx.calls);
  ctx.calls.push = (c) => {
    if (c[0] === "drawImage") alpha = ctx.globalAlpha;
    return push(c);
  };
  e.tick(0);
  return { call: ctx.calls.find((c) => c[0] === "drawImage"), alpha, fills: ctx.calls.filter((c) => c[0] === "fillText").length };
}

test("moon: the photo box is inside the screen and the photo is dimmer at night", () => {
  const day = moonDraw(false);
  const [, , x, y, w, h] = day.call;
  assert.ok(x >= 0 && x + w <= MOON.width && y >= 0 && y + h <= MOON.height, "photo box inside the screen");
  const night = moonDraw(true);
  assert.equal(day.alpha, 1);
  assert.ok(night.alpha < 0.5, `night alpha ${night.alpha}`);
  assert.equal(night.fills, 3, "text still shows at night");
});

test("planets: the rings and sun are drawn at a drifted centre that moves with the clock", () => {
  const { env, state, tick } = fakeEnv();
  const s = createPlanetsStyle(env);
  s.start();
  tick(0);
  const centre = () => callsOf(s).find((c) => c[0] === "ellipse").slice(1, 3);
  const first = centre();
  callsOf(s).length = 0;
  state.clock += 3 * 60 * 1000;
  tick(1500);
  const later = centre();
  assert.ok(Math.hypot(later[0] - first[0], later[1] - first[1]) >= 1);
});
