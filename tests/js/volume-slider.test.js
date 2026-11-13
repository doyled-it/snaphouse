// Tests for the slider element against a tiny fake DOM. Run with:
//   node --test tests/js/*.test.*

import { test } from "node:test";
import assert from "node:assert/strict";
import { createVolumeSlider } from "../../roles/room_picker/files/site/volume-slider.js";

function fakeEl() {
  const classes = new Set();
  const listeners = {};
  const el = {
    attrs: {},
    style: {},
    children: [],
    captured: null,
    className: "",
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
    setAttribute(k, v) { el.attrs[k] = String(v); },
    removeAttribute(k) { delete el.attrs[k]; },
    getAttribute: (k) => (k in el.attrs ? el.attrs[k] : null),
    append(...kids) { el.children.push(...kids); },
    addEventListener(t, fn) { (listeners[t] ||= []).push(fn); },
    fire(t, ev = {}) {
      const e = { preventDefault() { e.prevented = true; }, ...ev };
      for (const fn of listeners[t] || []) fn(e);
      return e;
    },
    setPointerCapture(id) { el.captured = id; },
    focused: 0,
    focus() { el.focused += 1; },
    getBoundingClientRect: () => ({ left: 100, width: 226 }),
    offsetWidth: 226,
  };
  return el;
}

function setup(opts = {}) {
  const sent = [];
  const renders = [];
  const frames = [];
  const buzz = [];
  let t = 0;
  const timers = [];
  const slider = createVolumeSlider({
    label: "Office volume",
    onSend: (p) => { sent.push(p); },
    onRender: (p) => renders.push(p),
    haptics: { volume: (a, b) => buzz.push([a, b]) },
    doc: { createElement: () => fakeEl() },
    raf: (fn) => { frames.push(fn); return frames.length; },
    now: () => t,
    setTimer: (fn, ms) => { timers.push({ at: t + ms, fn }); return timers.length; },
    clearTimer: () => {},
    ...opts,
  });
  const flushFrames = () => { while (frames.length) frames.shift()(); };
  const advance = (ms) => {
    t += ms;
    for (const x of timers.splice(0)) (x.at <= t ? x.fn() : timers.push(x));
  };
  return { slider, sent, renders, frames, buzz, flushFrames, advance };
}

test("has slider semantics and children", () => {
  const { slider } = setup();
  const { el } = slider;
  assert.equal(el.getAttribute("role"), "slider");
  assert.equal(el.getAttribute("tabindex"), "0");
  assert.equal(el.getAttribute("aria-valuemin"), "0");
  assert.equal(el.getAttribute("aria-valuemax"), "100");
  assert.equal(el.getAttribute("aria-label"), "Office volume");
  assert.equal(el.children.length, 2);
  assert.match(el.children[0].className, /volume-fill/);
  assert.match(el.children[1].className, /volume-thumb/);
});

test("render writes aria-valuenow at once and transforms in one frame", () => {
  const { slider, frames, flushFrames, renders } = setup();
  slider.set(40);
  slider.set(50);
  assert.equal(slider.el.getAttribute("aria-valuenow"), "50");
  assert.equal(frames.length, 1);
  flushFrames();
  assert.equal(slider.el.children[0].style.transform, "scaleX(0.5)");
  assert.match(slider.el.children[1].style.translate, /^[\d.]+px 0px$/);
  assert.deepEqual(renders, [50]);
});

test("thumb moves with the translate property so its held scale cannot stretch the travel", () => {
  // CSS applies the individual `scale` property before `transform`, so a
  // translateX in `transform` was multiplied by the 1.2 held scale and the
  // thumb overshot the track near 100. `translate` is applied first, unscaled.
  const { slider, flushFrames } = setup();
  slider.set(100);
  flushFrames();
  assert.equal(slider.el.children[1].style.transform || "", "");
  assert.match(slider.el.children[1].style.translate, /px 0px$/);
});

test("pointer drag sends, buzzes and ends with a hold", () => {
  const { slider, sent, buzz, advance } = setup();
  const { el } = slider;
  el.fire("pointerdown", { pointerId: 7, clientX: 100 + 113 });
  assert.equal(el.captured, 7);
  assert.ok(el.classList.contains("drag"));
  assert.deepEqual(sent, [50]);
  el.fire("pointermove", { pointerId: 7, clientX: 100 + 226 });
  advance(100);
  assert.deepEqual(sent, [50, 100]);
  assert.deepEqual(buzz.at(-1), [50, 100]);
  el.fire("pointerup", { pointerId: 7 });
  el.fire("lostpointercapture", { pointerId: 7 });
  assert.ok(!el.classList.contains("drag"));
  slider.applyStatus(20);
  assert.equal(slider.el.getAttribute("aria-valuenow"), "100");
  slider.applyStatus(100);
  assert.equal(slider.el.getAttribute("aria-valuenow"), "100");
});

test("a move without a captured pointer does nothing", () => {
  const { slider, sent } = setup();
  slider.el.fire("pointermove", { pointerId: 1, clientX: 150 });
  assert.deepEqual(sent, []);
});

test("applyStatus is ignored while dragging", () => {
  const { slider } = setup();
  slider.el.fire("pointerdown", { pointerId: 1, clientX: 100 + 113 });
  slider.applyStatus(5);
  assert.equal(slider.el.getAttribute("aria-valuenow"), "50");
});

test("applyStatus renders when idle and calls onRender", () => {
  const { slider, flushFrames, renders } = setup();
  slider.applyStatus(33);
  flushFrames();
  assert.equal(slider.el.getAttribute("aria-valuenow"), "33");
  assert.deepEqual(renders, [33]);
});

test("pointercancel is a release", () => {
  const { slider } = setup();
  slider.el.fire("pointerdown", { pointerId: 1, clientX: 150 });
  slider.el.fire("pointercancel", { pointerId: 1 });
  assert.ok(!slider.el.classList.contains("drag"));
});

test("arrow keys step by 2, Page keys by 10, Home and End jump", () => {
  const { slider, sent, advance } = setup();
  slider.set(50);
  slider.el.fire("keydown", { key: "ArrowRight" });
  assert.equal(slider.el.getAttribute("aria-valuenow"), "52");
  advance(100);
  slider.el.fire("keydown", { key: "PageDown" });
  assert.equal(slider.el.getAttribute("aria-valuenow"), "42");
  advance(100);
  slider.el.fire("keydown", { key: "Home" });
  assert.equal(slider.el.getAttribute("aria-valuenow"), "0");
  advance(100);
  slider.el.fire("keydown", { key: "End" });
  assert.equal(slider.el.getAttribute("aria-valuenow"), "100");
  assert.deepEqual(sent, [52, 42, 0, 100]);
});

test("keys clamp and unrelated keys are left alone", () => {
  const { slider, sent } = setup();
  slider.set(99);
  slider.el.fire("keydown", { key: "ArrowUp" });
  assert.equal(slider.el.getAttribute("aria-valuenow"), "100");
  const e = slider.el.fire("keydown", { key: "a" });
  assert.ok(!e.prevented);
  assert.deepEqual(sent, [100]);
});

test("a disabled slider ignores all input and sets aria-disabled", () => {
  const { slider, sent } = setup();
  slider.setEnabled(false);
  assert.equal(slider.el.getAttribute("aria-disabled"), "true");
  slider.el.fire("pointerdown", { pointerId: 1, clientX: 150 });
  slider.el.fire("keydown", { key: "ArrowUp" });
  assert.deepEqual(sent, []);
  slider.setEnabled(true);
  assert.equal(slider.el.getAttribute("aria-disabled"), null);
  slider.el.fire("keydown", { key: "ArrowUp" });
  assert.deepEqual(sent, [2]);
});

test("pointerdown focuses the slider so keys work after a tap", () => {
  const { slider } = setup();
  slider.el.fire("pointerdown", { pointerId: 1, clientX: 150 });
  assert.equal(slider.el.focused, 1);
});

test("disabling mid-drag ends the drag and stops sending", () => {
  const { slider, sent, advance } = setup();
  slider.el.fire("pointerdown", { pointerId: 1, clientX: 100 + 50 });
  assert.deepEqual(sent, [22]);
  slider.setEnabled(false);
  assert.ok(!slider.el.classList.contains("drag"));
  slider.el.fire("pointermove", { pointerId: 1, clientX: 100 + 200 });
  advance(200);
  assert.deepEqual(sent, [22]);
});
