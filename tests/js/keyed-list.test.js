import { test } from "node:test";
import assert from "node:assert/strict";
import { planKeyedUpdate } from "../../roles/room_picker/files/site/keyed-list.js";

const it = (key, sig = "s") => ({ key, sig });

test("unchanged items produce an empty plan with the same order", () => {
  const plan = planKeyedUpdate([it("a"), it("b")], [it("a"), it("b")]);
  assert.deepEqual(plan, { remove: [], create: [], replace: [], order: ["a", "b"] });
});

test("a new key is created, a missing key is removed", () => {
  const plan = planKeyedUpdate([it("a"), it("b")], [it("b"), it("c")]);
  assert.deepEqual(plan.remove, ["a"]);
  assert.deepEqual(plan.create, ["c"]);
  assert.deepEqual(plan.replace, []);
  assert.deepEqual(plan.order, ["b", "c"]);
});

test("reordered keys keep all nodes and change only order", () => {
  const plan = planKeyedUpdate([it("a"), it("b"), it("c")], [it("c"), it("a"), it("b")]);
  assert.deepEqual(plan.remove, []);
  assert.deepEqual(plan.create, []);
  assert.deepEqual(plan.replace, []);
  assert.deepEqual(plan.order, ["c", "a", "b"]);
});

test("a changed sig lands in replace, not remove plus create", () => {
  const plan = planKeyedUpdate([it("a", "1"), it("b")], [it("a", "2"), it("b")]);
  assert.deepEqual(plan.replace, ["a"]);
  assert.deepEqual(plan.remove, []);
  assert.deepEqual(plan.create, []);
});

test("duplicate keys in the new list are dropped after the first", () => {
  const plan = planKeyedUpdate([], [it("a"), it("a", "x"), it("b")]);
  assert.deepEqual(plan.create, ["a", "b"]);
  assert.deepEqual(plan.order, ["a", "b"]);
});
