import { test } from "node:test";
import assert from "node:assert/strict";
import { createAutosave, changedKeys, saveStatus } from "../../roles/room_picker/files/site/autosave-queue.js";

// A manual clock: timers only fire when the test advances time.
function harness({ delayMs, savedMs } = {}) {
  let now = 0;
  let nextId = 1;
  const timers = new Map();
  const states = [];
  const writes = [];
  const pendingWrites = [];
  let failNext = false;
  const write = (obj) => {
    writes.push(JSON.parse(JSON.stringify(obj)));
    return new Promise((resolve, reject) => {
      pendingWrites.push({ resolve, reject });
    });
  };
  const queue = createAutosave({
    write,
    delayMs,
    savedMs,
    setTimer: (fn, ms) => {
      const id = nextId++;
      timers.set(id, { fn, at: now + ms });
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    onState: (s) => states.push(s),
  });
  const advance = (ms) => {
    now += ms;
    for (const [id, t] of [...timers]) {
      if (t.at <= now) {
        timers.delete(id);
        t.fn();
      }
    }
  };
  const tick = () => new Promise((r) => setImmediate(r));
  return { queue, writes, states, advance, pendingWrites, tick, setFail: (v) => (failNext = v) };
}

test("two edits inside the delay produce one write with both patches merged", () => {
  const h = harness();
  h.queue.edit({ a: 1 });
  h.advance(300);
  h.queue.edit({ b: 2 });
  h.advance(599);
  assert.equal(h.writes.length, 0);
  h.advance(1);
  assert.deepEqual(h.writes, [{ a: 1, b: 2 }]);
  assert.equal(h.states.at(-1), "saving");
});

test("an edit during an in-flight write waits, then the next write carries the latest merged object", async () => {
  const h = harness();
  h.queue.edit({ a: 1 });
  h.advance(600);
  assert.equal(h.writes.length, 1);
  h.queue.edit({ a: 2 });
  h.queue.edit({ b: 3 });
  h.advance(600);
  assert.equal(h.writes.length, 1, "never two in flight");
  h.pendingWrites[0].resolve();
  await h.tick();
  assert.deepEqual(h.writes, [{ a: 1 }, { a: 2, b: 3 }]);
  h.pendingWrites[1].resolve();
  await h.tick();
  assert.equal(h.states.at(-1), "saved");
});

test("an edit during a write whose delay has not elapsed still waits for its own delay", async () => {
  const h = harness();
  h.queue.edit({ a: 1 });
  h.advance(600);
  h.queue.edit({ a: 2 });
  h.pendingWrites[0].resolve();
  await h.tick();
  assert.equal(h.writes.length, 1);
  assert.equal(h.states.at(-1), "pending");
  h.advance(600);
  assert.deepEqual(h.writes, [{ a: 1 }, { a: 2 }]);
});

test("flush writes immediately and resolves when the write finishes", async () => {
  const h = harness();
  h.queue.edit({ a: 1 });
  const done = h.queue.flush();
  assert.deepEqual(h.writes, [{ a: 1 }]);
  h.pendingWrites[0].resolve();
  await done;
  assert.equal(h.states.at(-1), "saved");
});

test("flush with nothing pending resolves without writing", async () => {
  const h = harness();
  await h.queue.flush();
  assert.equal(h.writes.length, 0);
});

test("saved only after the write resolves, then idle after 1500 ms", async () => {
  const h = harness();
  h.queue.edit({ a: 1 });
  h.advance(600);
  assert.ok(!h.states.includes("saved"));
  h.pendingWrites[0].resolve();
  await h.tick();
  assert.equal(h.states.at(-1), "saved");
  h.advance(1499);
  assert.equal(h.states.at(-1), "saved");
  h.advance(1);
  assert.equal(h.states.at(-1), "idle");
});

test("a rejected write gives failed, keeps the patch, and retry re-sends it merged with newer edits", async () => {
  const h = harness();
  h.queue.edit({ a: 1 });
  h.advance(600);
  h.pendingWrites[0].reject(new Error("down"));
  await h.tick();
  assert.equal(h.states.at(-1), "failed");
  h.queue.edit({ b: 2 });
  assert.equal(h.states.at(-1), "pending");
  h.queue.retry();
  assert.deepEqual(h.writes.at(-1), { a: 1, b: 2 });
  h.pendingWrites[1].resolve();
  await h.tick();
  assert.equal(h.states.at(-1), "saved");
});

test("retry with nothing to send does nothing", () => {
  const h = harness();
  h.queue.retry();
  assert.equal(h.writes.length, 0);
});

test("seed between edits does not drop unsent patches and does not write", () => {
  const h = harness();
  h.queue.seed({ a: 0, c: 9 });
  h.queue.edit({ a: 1 });
  h.queue.seed({ a: 0, c: 10 });
  h.advance(600);
  assert.deepEqual(h.writes, [{ a: 1, c: 10 }]);
});

test("seed while a write is in flight keeps the value being written", async () => {
  const h = harness();
  h.queue.edit({ a: 1 });
  h.advance(600);
  h.queue.seed({ a: 0, c: 5 });
  h.queue.edit({ b: 2 });
  h.advance(600);
  h.pendingWrites[0].resolve();
  await h.tick();
  assert.deepEqual(h.writes.at(-1), { a: 1, b: 2, c: 5 });
});

test("an edit with delayMs 0 writes immediately", () => {
  const h = harness();
  h.queue.edit({ on: true }, { delayMs: 0 });
  assert.deepEqual(h.writes, [{ on: true }]);
});

test("a new edit after saved cancels the idle timer", async () => {
  const h = harness();
  h.queue.edit({ a: 1 }, { delayMs: 0 });
  h.pendingWrites[0].resolve();
  await h.tick();
  h.queue.edit({ a: 2 });
  h.advance(1500);
  assert.notEqual(h.states.at(-1), "idle");
});

test("changedKeys lists the keys whose values differ", () => {
  assert.deepEqual(changedKeys({ a: 1, b: { x: 1 } }, { a: 1, b: { x: 2 }, c: 3 }).sort(), ["b", "c"]);
  assert.deepEqual(changedKeys(null, { a: 1 }), ["a"]);
});

test("saveStatus words each state", () => {
  assert.equal(saveStatus("saved").text, "Saved");
  assert.equal(saveStatus("failed").retry, true);
  assert.equal(saveStatus("failed").text, "Couldn't save.");
  assert.equal(saveStatus("idle").text, "");
  assert.equal(saveStatus("saving").text, "");
});
