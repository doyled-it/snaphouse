import { test } from "node:test";
import assert from "node:assert/strict";
import { createPageTimers } from "../../roles/room_picker/files/site/page-timers.js";

function setup({ hidden = false } = {}) {
  const live = new Map();
  let next = 1;
  let isHidden = hidden;
  const timers = createPageTimers({
    setInterval: (fn, ms) => {
      live.set(next, { fn, ms });
      return next++;
    },
    clearInterval: (id) => live.delete(id),
    isHidden: () => isHidden,
  });
  return { timers, live, setHidden: (v) => (isHidden = v) };
}

test("start runs every registered interval once", () => {
  const { timers, live } = setup();
  timers.add(() => {}, 100);
  timers.add(() => {}, 200);
  assert.equal(live.size, 0);
  timers.start();
  timers.start();
  assert.deepEqual([...live.values()].map((t) => t.ms), [100, 200]);
});

test("pause clears them all", () => {
  const { timers, live } = setup();
  timers.add(() => {}, 100);
  timers.add(() => {}, 200);
  timers.start();
  timers.pause();
  assert.equal(live.size, 0);
});

test("resume after pause restarts each exactly once", () => {
  const { timers, live } = setup();
  timers.add(() => {}, 100);
  timers.add(() => {}, 200);
  timers.start();
  timers.pause();
  timers.resume();
  assert.equal(live.size, 2);
});

test("resume twice does not double", () => {
  const { timers, live } = setup();
  timers.add(() => {}, 100);
  timers.start();
  timers.pause();
  timers.resume();
  timers.resume();
  assert.equal(live.size, 1);
});

test("resume invokes no callbacks", () => {
  const { timers } = setup();
  let calls = 0;
  timers.add(() => calls++, 100);
  timers.start();
  timers.pause();
  timers.resume();
  assert.equal(calls, 0);
});

test("pause before start does not throw", () => {
  const { timers, live } = setup();
  timers.add(() => {}, 100);
  assert.doesNotThrow(() => timers.pause());
  assert.equal(live.size, 0);
});

test("add after start begins immediately unless paused", () => {
  const { timers, live } = setup();
  timers.start();
  timers.add(() => {}, 100);
  assert.equal(live.size, 1);
  timers.pause();
  timers.add(() => {}, 200);
  assert.equal(live.size, 0);
  timers.resume();
  assert.equal(live.size, 2);
});
