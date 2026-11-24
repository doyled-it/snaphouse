import { test } from "node:test";
import assert from "node:assert/strict";
import { createScreensaver, initScreensaver } from "../../roles/room_picker/files/site/screensaver.js";
import { parsePanelParams } from "../../roles/room_picker/files/site/screensaver-model.js";

function makeEnv(startDate = new Date(2026, 10, 22, 12, 0, 0)) {
  let t = startDate.getTime();
  const timers = [];
  let nextId = 1;
  const clock = {
    now: () => t,
    setTimeout(fn, ms) { const id = nextId++; timers.push({ id, fn, at: t + ms, every: 0 }); return id; },
    setInterval(fn, ms) { const id = nextId++; timers.push({ id, fn, at: t + ms, every: ms }); return id; },
    clear(id) { const i = timers.findIndex((x) => x.id === id); if (i >= 0) timers.splice(i, 1); },
    advance(ms) {
      const end = t + ms;
      for (;;) {
        timers.sort((a, b) => a.at - b.at);
        const next = timers[0];
        if (!next || next.at > end) break;
        t = next.at;
        if (next.every) next.at += next.every; else timers.splice(0, 1);
        next.fn();
      }
      t = end;
    },
  };
  const listeners = [];
  const doc = {
    hidden: false,
    created: [],
    addEventListener(type, fn, cap) { listeners.push({ type, fn, cap }); },
    removeEventListener(type, fn) {
      const i = listeners.findIndex((l) => l.type === type && l.fn === fn);
      if (i >= 0) listeners.splice(i, 1);
    },
    createElement(tag) {
      const classes = new Set();
      const node = {
        tag, children: [], style: {}, attrs: {}, removed: false,
        classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
        setAttribute(k, v) { node.attrs[k] = v; },
        append(...c) { node.children.push(...c); },
        remove() { node.removed = true; },
        getContext: () => ({}),
      };
      doc.created.push(node);
      return node;
    },
    body: { children: [], append(...c) { this.children.push(...c); } },
    dispatch(type, extra = {}) {
      const evt = { type, ...extra, prevented: false, stopped: false,
        preventDefault() { evt.prevented = true; },
        stopPropagation() {},
        stopImmediatePropagation() { evt.stopped = true; } };
      for (const l of [...listeners]) {
        if (l.type !== type) continue;
        l.fn(evt);
        if (evt.stopped) break;
      }
      return evt;
    },
  };
  const aurora = { starts: 0, stops: 0, hues: [], start() { this.starts += 1; }, stop() { this.stops += 1; }, setHues(h) { this.hues.push(h); } };
  return { clock, doc, aurora, listeners };
}

function make(env, search = "?panel=1", extra = {}) {
  const win = { innerWidth: 800, innerHeight: 480, requestAnimationFrame() {}, cancelAnimationFrame() {}, performance: { now: () => 0 } };
  return createScreensaver({
    doc: env.doc,
    win,
    params: parsePanelParams(search),
    createAurora: () => env.aurora,
    now: env.clock.now,
    setInterval: env.clock.setInterval,
    clearInterval: env.clock.clear,
    setTimeout: env.clock.setTimeout,
    clearTimeout: env.clock.clear,
    swapImage: () => {},
    ...extra,
  });
}

const overlay = (env) => env.doc.body.children[0];
const MIN = 60000;

test("the overlay is created hidden with a canvas", () => {
  const env = makeEnv();
  make(env);
  const o = overlay(env);
  assert.equal(o.attrs["aria-hidden"], "true");
  assert.equal(o.classList.contains("is-up"), false);
  assert.equal(o.children[0].tag, "canvas");
  assert.equal(env.aurora.starts, 0);
});

test("by day it shows after 5 minutes idle, not before", () => {
  const env = makeEnv();
  const s = make(env);
  env.clock.advance(5 * MIN - 1000);
  assert.equal(s.isUp(), false);
  env.clock.advance(1000);
  assert.equal(s.isUp(), true);
  assert.equal(overlay(env).classList.contains("is-up"), true);
  assert.equal(env.aurora.starts, 1);
});

test("in quiet hours it shows after 60 seconds", () => {
  const env = makeEnv(new Date(2026, 10, 22, 23, 0, 0));
  const s = make(env);
  env.clock.advance(59000);
  assert.equal(s.isUp(), false);
  env.clock.advance(1000);
  assert.equal(s.isUp(), true);
});

test("input just before the limit pushes the show back", () => {
  const env = makeEnv();
  const s = make(env);
  env.clock.advance(5 * MIN - 1000);
  env.doc.dispatch("pointermove");
  env.clock.advance(5000);
  assert.equal(s.isUp(), false);
  env.clock.advance(5 * MIN);
  assert.equal(s.isUp(), true);
});

test("a finger held down keeps it away until released", () => {
  const env = makeEnv();
  const s = make(env);
  env.doc.dispatch("pointerdown");
  env.clock.advance(20 * MIN);
  assert.equal(s.isUp(), false);
  env.doc.dispatch("pointerup");
  env.clock.advance(5 * MIN + 1000);
  assert.equal(s.isUp(), true);
});

test("the first tap is swallowed with its pointerup and click", () => {
  const env = makeEnv();
  const s = make(env);
  const below = [];
  for (const type of ["pointerdown", "pointerup", "click"]) env.doc.addEventListener(type, (e) => below.push(e.type), true);
  env.clock.advance(5 * MIN);
  assert.equal(s.isUp(), true);
  const down = env.doc.dispatch("pointerdown");
  assert.equal(down.stopped, true);
  assert.equal(down.prevented, true);
  assert.equal(s.isUp(), false);
  assert.equal(overlay(env).classList.contains("is-up"), false);
  env.clock.advance(100);
  assert.equal(env.doc.dispatch("pointerup").stopped, true);
  assert.equal(env.doc.dispatch("click").stopped, true);
  assert.deepEqual(below, []);
});

test("aurora stops after the 300 ms fade out", () => {
  const env = makeEnv();
  make(env);
  env.clock.advance(5 * MIN);
  env.doc.dispatch("pointerdown");
  assert.equal(env.aurora.stops, 0);
  env.clock.advance(300);
  assert.equal(env.aurora.stops, 1);
});

test("a second tap during the fade is swallowed and does not re-show", () => {
  const env = makeEnv();
  const s = make(env);
  env.clock.advance(5 * MIN);
  env.doc.dispatch("pointerdown");
  env.clock.advance(150);
  assert.equal(env.doc.dispatch("pointerdown").stopped, true);
  assert.equal(s.isUp(), false);
  assert.equal(env.aurora.starts, 1);
});

test("after the swallow window a tap reaches the page and resets idle", () => {
  const env = makeEnv();
  const s = make(env);
  const below = [];
  env.doc.addEventListener("pointerdown", (e) => below.push(e.type), true);
  env.clock.advance(5 * MIN);
  env.doc.dispatch("pointerdown");
  env.doc.dispatch("pointerup");
  env.clock.advance(500);
  const down = env.doc.dispatch("pointerdown");
  assert.equal(down.stopped, false);
  assert.deepEqual(below, ["pointerdown"]);
  env.doc.dispatch("pointerup");
  env.clock.advance(5 * MIN - 1000);
  assert.equal(s.isUp(), false);
});

test("hiding the page stops aurora, showing it restarts only when up", () => {
  const env = makeEnv();
  const s = make(env);
  env.doc.hidden = true;
  env.doc.dispatch("visibilitychange");
  assert.equal(env.aurora.stops, 0);
  env.doc.hidden = false;
  env.doc.dispatch("visibilitychange");
  assert.equal(env.aurora.starts, 0);
  env.clock.advance(5 * MIN);
  assert.equal(s.isUp(), true);
  env.doc.hidden = true;
  env.doc.dispatch("visibilitychange");
  assert.equal(env.aurora.stops, 1);
  env.doc.hidden = false;
  env.doc.dispatch("visibilitychange");
  assert.equal(env.aurora.starts, 2);
});

test("a panel already idle 3 minutes shows at 22:00 on the minute tick", () => {
  const env = makeEnv(new Date(2026, 10, 22, 21, 57, 0));
  const s = make(env);
  env.clock.advance(2 * MIN + 59000);
  assert.equal(s.isUp(), false);
  env.clock.advance(1000);
  assert.equal(s.isUp(), true);
});

test("an overlay that is up stays up when quiet hours end", () => {
  const env = makeEnv(new Date(2026, 10, 22, 5, 58, 0));
  const s = make(env);
  env.clock.advance(MIN);
  assert.equal(s.isUp(), true);
  env.clock.advance(5 * MIN);
  assert.equal(s.isUp(), true);
});

test("setHues forwards to the aurora", () => {
  const env = makeEnv();
  const s = make(env);
  s.setHues({ h1: 10, h2: 20, chroma: 0.5 });
  assert.deepEqual(env.aurora.hues, [{ h1: 10, h2: 20, chroma: 0.5 }]);
});

test("destroy removes the node, listeners and timers", () => {
  const env = makeEnv();
  const s = make(env);
  s.destroy();
  assert.equal(overlay(env).removed, true);
  assert.equal(env.listeners.length, 0);
  env.clock.advance(20 * MIN);
  assert.equal(env.aurora.starts, 0);
});

test("without panel=1 init returns null and touches nothing", () => {
  const touched = [];
  const doc = new Proxy({}, { get(_t, k) { touched.push(k); return () => {}; } });
  assert.equal(initScreensaver("", { doc }), null);
  assert.equal(initScreensaver("?x=1", { doc }), null);
  assert.deepEqual(touched, []);
});


// ---- ambient Now Playing ------------------------------------------------- //

const byClass = (env, cls) => env.doc.created.find((n) => n.className === cls);
const track = (over = {}) => ({ title: "Song", artist: "Band", album: "LP", status: "playing", source: "bluetooth", artUrl: "/cover/a.jpg", duration: 100, position: 50, positionAt: "2026-11-22T00:00:00Z", startedAt: null, ...over });
const night = () => new Date(2026, 10, 22, 3, 0, 0);

test("a playing track shows the ambient view at any hour, with no Aurora loop", () => {
  const env = makeEnv(night());
  const s = make(env);
  s.setTrack(track());
  env.clock.advance(61 * 1000);
  assert.equal(s.isUp(), true);
  assert.equal(byClass(env, "p-amb").hidden, false);
  assert.equal(env.aurora.starts, 0);
  assert.equal(byClass(env, "p-amb-title").textContent, "Song");
  assert.equal(byClass(env, "p-amb-artist").textContent, "Band");
});

test("with nothing playing the Aurora shows and the ambient layer stays hidden", () => {
  const env = makeEnv(night());
  const s = make(env);
  env.clock.advance(61 * 1000);
  assert.equal(s.isUp(), true);
  assert.equal(byClass(env, "p-amb").hidden, true);
  assert.equal(env.aurora.starts, 1);
});

test("a track paused under 5 minutes stays ambient, then the Aurora takes over", () => {
  const env = makeEnv(night());
  const s = make(env);
  s.setTrack(track({ status: "paused" }));
  env.clock.advance(61 * 1000);
  assert.equal(byClass(env, "p-amb").hidden, false);
  env.clock.advance(3 * MIN);
  assert.equal(byClass(env, "p-amb").hidden, false);
  env.clock.advance(2 * MIN);
  assert.equal(byClass(env, "p-amb").hidden, true);
  assert.equal(env.aurora.starts, 1);
});

test("a track arriving while the Aurora is up switches to ambient", () => {
  const env = makeEnv(night());
  const s = make(env);
  env.clock.advance(61 * 1000);
  assert.equal(env.aurora.starts, 1);
  s.setTrack(track());
  assert.equal(byClass(env, "p-amb").hidden, false);
  assert.equal(env.aurora.stops >= 1, true);
});

test("the ambient progress line follows the paused position", () => {
  const env = makeEnv(night());
  const s = make(env);
  s.setTrack(track({ status: "paused" }));
  env.clock.advance(61 * 1000);
  assert.equal(byClass(env, "p-amb-fill").style.transform, "scaleX(0.5000)");
});

test("the first touch dismisses and reports the track so the page lands on Now playing", () => {
  const env = makeEnv(night());
  const seen = [];
  const s = make(env, "?panel=1", { onDismiss: (t) => seen.push(t) });
  s.setTrack(track());
  env.clock.advance(61 * 1000);
  const evt = env.doc.dispatch("pointerdown");
  assert.equal(evt.prevented, true);
  assert.equal(s.isUp(), false);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].title, "Song");
});

test("dismissing the Aurora with no track reports nothing", () => {
  const env = makeEnv(night());
  const seen = [];
  make(env, "?panel=1", { onDismiss: (t) => seen.push(t) });
  env.clock.advance(61 * 1000);
  env.doc.dispatch("pointerdown");
  assert.deepEqual(seen, []);
});

test("ambient text follows setTrack after a dismiss", () => {
  const env = makeEnv(night());
  const s = make(env);
  s.setTrack(track());
  env.clock.advance(61 * 1000);
  env.doc.dispatch("pointerdown");
  const before = byClass(env, "p-amb-title").textContent;
  s.setTrack(track({ title: "Later" }));
  env.clock.advance(5000);
  assert.equal(byClass(env, "p-amb-title").textContent, "Later");
  assert.equal(before, "Song");
});
