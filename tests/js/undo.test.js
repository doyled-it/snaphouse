import { test } from "node:test";
import assert from "node:assert/strict";
import { TOAST_MS, snapshotRooms, undoPlan, createToaster } from "../../roles/room_picker/files/site/undo.js";

const room = (id, on, reachable = true) => ({ id, on, reachable });

test("snapshotRooms maps id to on", () => {
  const snap = snapshotRooms([room("a", true), room("b", false)]);
  assert.deepEqual([...snap], [["a", true], ["b", false]]);
});

test("undoPlan returns only changed, reachable rooms, with no volumes", () => {
  const snap = snapshotRooms([room("a", true), room("b", false), room("c", true)]);
  const plan = undoPlan(snap, [{ ...room("a", false), volume: 40 }, room("b", false), room("c", false)]);
  assert.deepEqual(plan, [{ id: "a", on: true }, { id: "c", on: true }]);
});

test("undoPlan skips a room that is unreachable now", () => {
  const snap = snapshotRooms([room("a", true)]);
  assert.deepEqual(undoPlan(snap, [room("a", false, false)]), []);
});

test("undoPlan drops a room that vanished", () => {
  const snap = snapshotRooms([room("a", true), room("b", true)]);
  assert.deepEqual(undoPlan(snap, [room("b", false)]), [{ id: "b", on: true }]);
});

function harness() {
  const timers = new Map();
  let next = 1;
  const rendered = [];
  const toaster = createToaster({
    now: () => 0,
    setTimer: (fn, ms) => { const id = next++; timers.set(id, { fn, ms }); return id; },
    clearTimer: (id) => { timers.delete(id); },
    render: (view) => rendered.push(view),
  });
  return { toaster, timers, rendered };
}

test("show arms an 8000 ms timer", () => {
  const { toaster, timers } = harness();
  toaster.show("Hi");
  assert.equal(TOAST_MS, 8000);
  assert.deepEqual([...timers.values()].map((t) => t.ms), [8000]);
});

test("a second show clears the first timer and renders only the second text", () => {
  const { toaster, timers, rendered } = harness();
  toaster.show("one");
  toaster.show("two");
  assert.equal(timers.size, 1);
  assert.equal(rendered.at(-1).text, "two");
  assert.equal(rendered.at(-1).visible, true);
});

test("the timer expiring hides the toast", () => {
  const { toaster, timers, rendered } = harness();
  toaster.show("one");
  [...timers.values()][0].fn();
  assert.equal(rendered.at(-1).visible, false);
});

test("dismiss clears the timer", () => {
  const { toaster, timers, rendered } = harness();
  toaster.show("one");
  toaster.dismiss();
  assert.equal(timers.size, 0);
  assert.equal(rendered.at(-1).visible, false);
});

test("Undo dismisses the toast and runs the callback once", () => {
  const { toaster, timers, rendered } = harness();
  let calls = 0;
  toaster.show("one", () => { calls += 1; });
  const view = rendered.at(-1);
  assert.equal(typeof view.undo, "function");
  view.undo();
  view.undo();
  assert.equal(calls, 1);
  assert.equal(timers.size, 0);
  assert.equal(rendered.at(-1).visible, false);
});

test("a toast without a callback has no undo", () => {
  const { toaster, rendered } = harness();
  toaster.show("one");
  assert.equal(rendered.at(-1).undo, null);
});
