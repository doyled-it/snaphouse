// Tests for the pure volume send/hold state machine. Run with:
//   node --test tests/js/*.test.*
//
// A deterministic fake clock stands in for time and timers, so no test waits.

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  createVolumeController,
  valueFromPointer,
  keyStep,
} from "../../roles/room_picker/files/site/volume-controller.js";

function harness(opts = {}) {
  let t = 0;
  let nextId = 1;
  let timers = [];
  const sent = [];
  const pending = [];
  const clock = {
    now: () => t,
    setTimer: (fn, ms) => {
      const id = nextId++;
      timers.push({ id, at: t + ms, fn });
      return id;
    },
    clearTimer: (id) => {
      timers = timers.filter((x) => x.id !== id);
    },
  };
  function tick(ms) {
    const target = t + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at || a.id - b.id);
      const next = timers[0];
      if (!next || next.at > target) break;
      timers.shift();
      t = next.at;
      next.fn();
    }
    t = target;
  }
  const c = createVolumeController({
    send: (v) => { sent.push(v); return opts.send ? opts.send(v) : undefined; },
    ...clock,
    ...(opts.config || {}),
  });
  return { c, sent, tick, pending, timers: () => timers, now: clock.now };
}

test("first move sends immediately; second within 100 ms waits for the trailing send", () => {
  const h = harness();
  h.c.begin(30);
  assert.deepEqual(h.sent, [30]);
  h.tick(50);
  h.c.move(40);
  assert.deepEqual(h.sent, [30]);
  h.tick(49);
  assert.deepEqual(h.sent, [30]);
  h.tick(1);
  assert.deepEqual(h.sent, [30, 40]);
});

test("a first move with no begin also sends at once", () => {
  const h = harness();
  h.c.move(12);
  assert.deepEqual(h.sent, [12]);
});

test("a newer value replaces the queued one", () => {
  const h = harness();
  h.c.begin(30);
  h.tick(10);
  h.c.move(40);
  h.tick(10);
  h.c.move(50);
  h.tick(10);
  h.c.move(60);
  h.tick(100);
  assert.deepEqual(h.sent, [30, 60]);
});

test("trailing send of the final value always goes out on end()", () => {
  const h = harness();
  h.c.begin(30);
  h.tick(20);
  h.c.move(70);
  h.c.end();
  assert.deepEqual(h.sent, [30]);
  h.tick(80);
  assert.deepEqual(h.sent, [30, 70]);
});

test("only one request is in flight", async () => {
  const resolvers = [];
  const h = harness({
    send: () => new Promise((resolve) => resolvers.push(resolve)),
  });
  h.c.begin(10);
  assert.deepEqual(h.sent, [10]);
  h.tick(500);
  h.c.move(20);
  h.tick(500);
  h.c.move(30);
  h.tick(500);
  assert.deepEqual(h.sent, [10]);
  resolvers[0]();
  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(h.sent, [10, 30]);
  h.tick(500);
  assert.deepEqual(h.sent, [10, 30]);
});

test("a rejected request frees the slot", async () => {
  const rejecters = [];
  const h = harness({
    send: () => new Promise((_, reject) => rejecters.push(reject)),
  });
  h.c.begin(10);
  h.tick(500);
  h.c.move(20);
  assert.deepEqual(h.sent, [10]);
  rejecters[0](new Error("network"));
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(h.sent, [10, 20]);
  h.tick(500);
  h.c.move(25);
  assert.deepEqual(h.sent, [10, 20]);
  rejecters[1](new Error("network"));
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(h.sent, [10, 20, 25]);
});

test("incoming is ignored while dragging", () => {
  const h = harness();
  h.c.begin(55);
  assert.equal(h.c.dragging(), true);
  assert.deepEqual(h.c.incoming(10), { show: false, value: 55 });
});

test("after end the user's value is held until the hub is within 1", () => {
  const h = harness();
  h.c.begin(30);
  h.c.end();
  assert.equal(h.c.dragging(), false);
  assert.equal(h.c.held(), true);
  assert.deepEqual(h.c.incoming(10), { show: false, value: 30 });
  assert.deepEqual(h.c.incoming(31), { show: true, value: 31 });
  assert.equal(h.c.held(), false);
  assert.deepEqual(h.c.incoming(10), { show: true, value: 10 });
});

test("hold expires after 2 s even if the hub never agrees", () => {
  const h = harness();
  h.c.begin(30);
  h.c.end();
  h.tick(1999);
  assert.deepEqual(h.c.incoming(10), { show: false, value: 30 });
  h.tick(1);
  assert.deepEqual(h.c.incoming(10), { show: true, value: 10 });
  assert.equal(h.c.held(), false);
});

test("end() twice is harmless", () => {
  const h = harness();
  h.c.begin(30);
  h.tick(1000);
  h.c.end();
  h.tick(1000);
  h.c.end();
  // The second end() did not restart the hold: it began at t=1000 and ends at t=3000.
  h.tick(999);
  assert.equal(h.c.held(), true);
  h.tick(1);
  assert.equal(h.c.held(), false);
  assert.deepEqual(h.c.incoming(10), { show: true, value: 10 });
  assert.deepEqual(h.sent, [30]);
});

test("a new begin cancels a hold", () => {
  const h = harness();
  h.c.begin(30);
  h.c.end();
  h.c.begin(60);
  assert.equal(h.c.held(), false);
  assert.deepEqual(h.c.incoming(10), { show: false, value: 60 });
});

test("dispose clears timers", () => {
  const h = harness();
  h.c.begin(30);
  h.tick(10);
  h.c.move(40);
  h.c.dispose();
  assert.equal(h.timers().length, 0);
  h.tick(1000);
  assert.deepEqual(h.sent, [30]);
});

test("end() with a request in flight at release still sends the final value once", async () => {
  const resolvers = [];
  const h = harness({
    send: () => new Promise((resolve) => resolvers.push(resolve)),
  });
  h.c.begin(10);
  h.tick(500);
  h.c.move(80);
  h.c.end();
  h.tick(500);
  assert.deepEqual(h.sent, [10]);
  resolvers[0]();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(h.sent, [10, 80]);
  resolvers[1]();
  await new Promise((r) => setImmediate(r));
  h.tick(500);
  assert.deepEqual(h.sent, [10, 80]);
});

test("begin() while a prior request is in flight sends the begun value when it settles", async () => {
  const resolvers = [];
  const h = harness({
    send: () => new Promise((resolve) => resolvers.push(resolve)),
  });
  h.c.begin(10);
  h.c.end();
  h.tick(500);
  h.c.begin(65);
  assert.deepEqual(h.sent, [10]);
  resolvers[0]();
  await new Promise((r) => setImmediate(r));
  assert.deepEqual(h.sent, [10, 65]);
});

test("valueFromPointer clamps and rounds", () => {
  assert.equal(valueFromPointer(50, 0, 200), 25);
  assert.equal(valueFromPointer(-5, 0, 200), 0);
  assert.equal(valueFromPointer(500, 0, 200), 100);
  assert.equal(valueFromPointer(1, 0, 0), 0);
  assert.equal(valueFromPointer(110, 10, 300), 33);
});

test("keyStep table", () => {
  assert.equal(keyStep("ArrowRight"), 2);
  assert.equal(keyStep("ArrowUp"), 2);
  assert.equal(keyStep("ArrowLeft"), -2);
  assert.equal(keyStep("ArrowDown"), -2);
  assert.equal(keyStep("PageUp"), 10);
  assert.equal(keyStep("PageDown"), -10);
  assert.equal(keyStep("Home"), null);
  assert.equal(keyStep("End"), null);
  assert.equal(keyStep("a"), null);
});
