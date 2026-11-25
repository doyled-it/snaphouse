import { test } from "node:test";
import assert from "node:assert/strict";
import { createClockStyle } from "../../roles/room_picker/files/site/clock-style.js";
import { MOVE_EVERY_MIN } from "../../roles/room_picker/files/site/clock-model.js";

function env(start = new Date(2026, 10, 24, 9, 5, 20)) {
  let t = start.getTime();
  const timers = [];
  let id = 1;
  const doc = {
    createElement(tag) {
      const classes = new Set();
      const props = {};
      const node = {
        tag, children: [], textContent: "", className: "",
        style: { setProperty(k, v) { props[k] = v; }, props },
        classList: {
          add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
          toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
        },
        append(...c) { node.children.push(...c); },
      };
      return node;
    },
  };
  return {
    doc,
    timers,
    now: () => t,
    setTimeout: (fn, ms) => { const n = id++; timers.push({ n, fn, at: t + ms }); return n; },
    clearTimeout: (n) => { const i = timers.findIndex((x) => x.n === n); if (i >= 0) timers.splice(i, 1); },
    advance(ms) {
      const end = t + ms;
      for (;;) {
        timers.sort((a, b) => a.at - b.at);
        if (!timers[0] || timers[0].at > end) break;
        const next = timers.shift();
        t = next.at;
        next.fn();
      }
      t = end;
    },
    random: () => 0.5,
  };
}

const make = (e) => createClockStyle({ doc: e.doc, now: e.now, setTimeout: e.setTimeout, clearTimeout: e.clearTimeout, random: e.random });

test("the node carries a time line and a date line in the panel's local time", () => {
  const e = env();
  const s = make(e);
  assert.equal(s.node.classList.contains("saver-clock"), true);
  s.start();
  assert.equal(s.node.children[0].textContent, "9:05");
  assert.equal(s.node.children[1].textContent, "Tuesday, November 24");
});

test("it redraws once at each whole minute and not in between", () => {
  const e = env();
  const s = make(e);
  s.start();
  assert.equal(e.timers.length, 1);
  e.advance(39000);
  assert.equal(s.node.children[0].textContent, "9:05");
  e.advance(1000);
  assert.equal(s.node.children[0].textContent, "9:06");
  assert.equal(e.timers.length, 1);
});

test("stop leaves no timer and start twice keeps a single one", () => {
  const e = env();
  const s = make(e);
  s.start();
  s.start();
  assert.equal(e.timers.length, 1);
  s.stop();
  assert.equal(e.timers.length, 0);
  e.advance(10 * 60000);
  assert.equal(s.node.children[0].textContent, "9:05");
});

test("it moves to a new spot every few minutes", () => {
  let n = 0;
  const e = env();
  e.random = () => [0.1, 0.1, 0.9, 0.9, 0.2, 0.1, 0.8, 0.9][n++ % 8];
  const s = make(e);
  s.start();
  const first = { ...s.node.style.props };
  assert.equal("--clock-x" in first && "--clock-y" in first, true);
  e.advance((MOVE_EVERY_MIN - 1) * 60000);
  assert.deepEqual(s.node.style.props, first);
  e.advance(2 * 60000);
  assert.notDeepEqual(s.node.style.props, first);
});

test("setNight toggles the night look and a restart keeps it", () => {
  const e = env();
  const s = make(e);
  s.setNight(true);
  assert.equal(s.node.classList.contains("is-night"), true);
  s.start();
  s.stop();
  s.start();
  assert.equal(s.node.classList.contains("is-night"), true);
  s.setNight(false);
  assert.equal(s.node.classList.contains("is-night"), false);
});

test("setHues is accepted and ignored", () => {
  const s = make(env());
  s.setHues({ h1: 1, h2: 2, chroma: 0.1 });
});

test("the Clock has no box behind it", async () => {
  const { readFileSync } = await import("node:fs");
  const css = readFileSync(new URL("../../roles/room_picker/files/site/style.css", import.meta.url), "utf8");
  const rule = css.match(/\n\.saver-clock\s*\{[^}]*\}/)[0];
  assert.doesNotMatch(rule, /background/);
  assert.match(rule, /--ss-move:\s*6s/);
});
