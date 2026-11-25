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
        classList: { add: (...c) => c.forEach((x) => classes.add(x)), remove: (...c) => c.forEach((x) => classes.delete(x)), contains: (c) => classes.has(c) },
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
  const fake = (id) => ({
    id, starts: 0, stops: 0, hues: [], nights: [],
    node: doc.createElement("div"),
    start() { this.starts += 1; },
    stop() { this.stops += 1; },
    setHues(h) { this.hues.push(h); },
    setNight(on) { this.nights.push(on); },
  });
  const styles = { aurora: fake("aurora"), clock: fake("clock") };
  return { clock, doc, aurora: styles.aurora, styles, listeners };
}

function make(env, search = "?panel=1", extra = {}) {
  const win = { innerWidth: 800, innerHeight: 480, requestAnimationFrame() {}, cancelAnimationFrame() {}, performance: { now: () => 0 } };
  return createScreensaver({
    doc: env.doc,
    win,
    params: parsePanelParams(search),
    createStyle: (id) => env.styles[id] || null,
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
  assert.equal(o.children[0].className, "saver-stage");
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

test("setHues reaches the style on screen, and a style made later gets the latest", () => {
  const env = makeEnv();
  const s = make(env);
  s.setHues({ h1: 10, h2: 20, chroma: 0.5 });
  env.clock.advance(5 * MIN);
  assert.deepEqual(env.aurora.hues, [{ h1: 10, h2: 20, chroma: 0.5 }]);
  s.setHues({ h1: 30, h2: 40, chroma: 0.2 });
  assert.deepEqual(env.aurora.hues.at(-1), { h1: 30, h2: 40, chroma: 0.2 });
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

test("the ambient layer is not redrawn while the page is hidden", () => {
  const env = makeEnv(night());
  const s = make(env);
  s.setTrack(track({ position: 10, positionAt: night().toISOString() }));
  env.clock.advance(61 * 1000);
  env.doc.hidden = true;
  env.doc.dispatch("visibilitychange");
  const fill = byClass(env, "p-amb-fill");
  const frozen = fill.style.transform;
  env.clock.advance(20 * 1000);
  assert.equal(fill.style.transform, frozen);
  env.doc.hidden = false;
  env.doc.dispatch("visibilitychange");
  assert.notEqual(fill.style.transform, frozen);
});

const SETTINGS = { screensaver_on: true, screensaver_wait_s: 120, night_start: "22:00", night_end: "06:00", screensaver_style: "aurora" };
const TRACK = { title: "Song", artist: "Band", status: "playing", artUrl: null, controllable: false, held: false, position: 0, duration: 200, updatedAt: 0 };

test("setSettings changes the wait live, for day and night", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(SETTINGS);
  env.clock.advance(2 * MIN - 1000);
  assert.equal(s.isUp(), false);
  env.clock.advance(1000);
  assert.equal(s.isUp(), true);
  s.setSettings({ ...SETTINGS, screensaver_wait_s: 30 });
  assert.equal(s.isUp(), true);
});

test("a shorter wait takes effect for the idle time already spent", () => {
  const env = makeEnv();
  const s = make(env);
  env.clock.advance(60 * 1000);
  s.setSettings({ ...SETTINGS, screensaver_wait_s: 30 });
  env.clock.advance(1000);
  assert.equal(s.isUp(), true);
});

test("setSettings off hides an overlay that is up and it never shows again", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(SETTINGS);
  env.clock.advance(2 * MIN);
  assert.equal(s.isUp(), true);
  let dismissed = 0;
  s.setSettings({ ...SETTINGS, screensaver_on: false });
  assert.equal(s.isUp(), false);
  assert.equal(overlay(env).classList.contains("is-up"), false);
  env.clock.advance(60 * MIN);
  assert.equal(s.isUp(), false);
  assert.equal(dismissed, 0);
});

test("with the screensaver off the ambient view never shows either", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings({ ...SETTINGS, screensaver_on: false });
  s.setTrack(TRACK);
  env.clock.advance(60 * MIN);
  assert.equal(s.isUp(), false);
});

test("turning it off does not count as a dismiss (no jump to Now playing)", () => {
  const env = makeEnv();
  let dismissed = 0;
  const s = make(env, "?panel=1", { onDismiss: () => { dismissed += 1; } });
  s.setSettings(SETTINGS);
  s.setTrack(TRACK);
  env.clock.advance(2 * MIN);
  assert.equal(s.isUp(), true);
  s.setSettings({ ...SETTINGS, screensaver_on: false });
  assert.equal(dismissed, 0);
});

test("turning it back on starts a fresh wait", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings({ ...SETTINGS, screensaver_on: false });
  env.clock.advance(10 * MIN);
  s.setSettings(SETTINGS);
  env.clock.advance(2 * MIN - 1000);
  assert.equal(s.isUp(), false);
  env.clock.advance(2000);
  assert.equal(s.isUp(), true);
});

test("setSettings with the same values changes nothing", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(SETTINGS);
  env.clock.advance(MIN);
  s.setSettings({ ...SETTINGS });
  env.clock.advance(MIN);
  assert.equal(s.isUp(), true);
});


// ---- styles, rotation and night ------------------------------------------ //

const TWO = { ...SETTINGS, screensaver_wait_s: 60, screensaver_styles: ["aurora", "clock"], screensaver_change_min: 15 };
const FADE = 3000;
const running = (st) => st.starts - st.stops > 0;

test("each start shows the next pick after the last one shown", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(TWO);
  env.clock.advance(MIN);
  assert.equal(env.styles.aurora.starts, 1);
  assert.equal(env.styles.clock.starts, 0);
  env.doc.dispatch("pointerdown");
  env.clock.advance(MIN + 500);
  assert.equal(s.isUp(), true);
  assert.equal(env.styles.clock.starts, 1);
  env.doc.dispatch("pointerdown");
  env.clock.advance(MIN + 500);
  assert.equal(env.styles.aurora.starts, 2);
});

test("a page that has just loaded starts at the first pick", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings({ ...TWO, screensaver_styles: ["aurora", "clock"] });
  assert.equal(s.nextStyle(), "aurora");
});

test("after the change time it crossfades to the next pick, the old one stops after 3 s", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(TWO);
  env.clock.advance(MIN);
  env.clock.advance(15 * MIN - 1);
  assert.equal(env.styles.clock.starts, 0);
  env.clock.advance(1);
  assert.equal(env.styles.clock.starts, 1);
  assert.equal(running(env.styles.aurora), true);
  env.clock.advance(FADE - 1);
  assert.equal(running(env.styles.aurora), true);
  env.clock.advance(1);
  assert.equal(running(env.styles.aurora), false);
  assert.equal(running(env.styles.clock), true);
  env.clock.advance(15 * MIN);
  assert.equal(env.styles.aurora.starts, 2);
});

test("the first crossfade of a session fades the outgoing layer, it is not left snapped", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(TWO);
  env.clock.advance(MIN);
  env.clock.advance(15 * MIN);
  const snapped = env.doc.created.filter((n) => n.classList.contains("is-snap"));
  assert.equal(snapped.length, 0);
});

test("with Change every off, or one pick, the style never changes", () => {
  for (const patch of [{ screensaver_change_min: 0 }, { screensaver_styles: ["aurora"] }]) {
    const env = makeEnv();
    const s = make(env);
    s.setSettings({ ...TWO, ...patch });
    env.clock.advance(MIN);
    env.clock.advance(5 * 60 * MIN);
    assert.equal(env.styles.clock.starts, 0);
    assert.equal(env.styles.aurora.starts, 1);
  }
});

test("rotation runs the same by night", () => {
  const env = makeEnv(new Date(2026, 10, 22, 23, 0, 0));
  const s = make(env);
  s.setSettings({ ...TWO, screensaver_change_min: 5 });
  env.clock.advance(MIN);
  env.clock.advance(5 * MIN);
  assert.equal(env.styles.clock.starts, 1);
});

test("ambient Now Playing takes over, then the same style returns and the timer restarts", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(TWO);
  env.clock.advance(MIN);
  env.clock.advance(10 * MIN);
  s.setTrack(TRACK);
  env.clock.advance(1000);
  assert.equal(running(env.styles.aurora), false);
  env.clock.advance(60 * MIN);
  assert.equal(env.styles.clock.starts, 0);
  s.setTrack(null);
  env.clock.advance(1000);
  assert.equal(running(env.styles.aurora), true);
  assert.equal(env.styles.clock.starts, 0);
  env.clock.advance(15 * MIN - 2000);
  assert.equal(env.styles.clock.starts, 0);
  env.clock.advance(2000);
  assert.equal(env.styles.clock.starts, 1);
  assert.equal(s.nextStyle(), "aurora");
});

test("nextStyle peeks without moving the rotation", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(TWO);
  assert.equal(s.nextStyle(), "aurora");
  assert.equal(s.nextStyle(), "aurora");
  env.clock.advance(MIN);
  assert.equal(s.nextStyle(), "clock");
  assert.equal(s.nextStyle(), "clock");
});

test("a pick with no module is skipped, and with none shipped Aurora shows", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings({ ...TWO, screensaver_styles: ["nope", "sky"] });
  env.clock.advance(MIN);
  assert.equal(env.styles.aurora.starts, 1);
  const env2 = makeEnv();
  const s2 = make(env2);
  s2.setSettings({ ...TWO, screensaver_styles: ["aurora", "nope", "clock"] });
  env2.clock.advance(MIN);
  env2.clock.advance(15 * MIN);
  assert.equal(env2.styles.clock.starts, 1);
});

test("unpicking the style on screen moves on to the next pick", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(TWO);
  env.clock.advance(MIN);
  s.setSettings({ ...TWO, screensaver_styles: ["clock"] });
  assert.equal(env.styles.clock.starts, 1);
  env.clock.advance(FADE);
  assert.equal(running(env.styles.aurora), false);
});

test("a style is told night during quiet hours and day otherwise", () => {
  const env = makeEnv(new Date(2026, 10, 22, 23, 0, 0));
  const s = make(env);
  env.clock.advance(MIN);
  assert.equal(s.isUp(), true);
  assert.deepEqual(env.aurora.nights, [true]);
  const day = makeEnv();
  make(day);
  day.clock.advance(5 * MIN);
  assert.deepEqual(day.aurora.nights, [false]);
});

test("when the hour passes while it runs the style is told night, once", () => {
  const env = makeEnv(new Date(2026, 10, 22, 21, 57, 0));
  const s = make(env);
  s.setSettings({ ...SETTINGS, screensaver_wait_s: 30 });
  env.clock.advance(31 * 1000);
  assert.equal(s.isUp(), true);
  assert.deepEqual(env.aurora.nights, [false]);
  env.clock.advance(3 * MIN);
  assert.deepEqual(env.aurora.nights, [false, true]);
  env.clock.advance(10 * MIN);
  assert.deepEqual(env.aurora.nights, [false, true]);
});

test("hiding the page stops the running style and a crossfade timer does not restart it", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(TWO);
  env.clock.advance(MIN);
  env.doc.hidden = true;
  env.doc.dispatch("visibilitychange");
  assert.equal(running(env.aurora), false);
  env.clock.advance(60 * MIN);
  assert.equal(running(env.aurora), false);
  assert.equal(running(env.styles.clock), false);
  env.doc.hidden = false;
  env.doc.dispatch("visibilitychange");
  assert.equal(running(env.aurora), true);
});

test("dismiss stops every style after the fade", () => {
  const env = makeEnv();
  const s = make(env);
  s.setSettings(TWO);
  env.clock.advance(MIN + 15 * MIN);
  env.doc.dispatch("pointerdown");
  env.clock.advance(300);
  assert.equal(running(env.aurora), false);
  assert.equal(running(env.styles.clock), false);
});
